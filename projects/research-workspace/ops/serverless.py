"""One bounded managed-database operation per Cloud Run Job execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from deploy.managed import APP_ROLES, SCHEMAS, provider_baseline, require_tls
from . import backup


def positive(name):
    value = int(backup.require_env(name))
    if value < 1:
        raise ValueError(name + " must be positive")
    return value


def separate_namespaces(first, second):
    if first.bucket == second.bucket and (first.prefix == second.prefix or first.prefix.startswith(second.prefix + "/") or second.prefix.startswith(first.prefix + "/")):
        raise ValueError("Object, backup and restore namespaces must not overlap")


class ImportStorage:
    def __init__(self, store):
        self.store = store

    def put_verified(self, key, path, expected, media_type):
        item = {"size": Path(path).stat().st_size, "sha256": expected}
        if backup.digest_file(path) != expected:
            raise ValueError("Import source changed")
        try:
            self.store.put(key, path)
        except Exception as error:
            # An immutable retry is accepted only when the existing bytes match.
            if getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
        backup.check_stream(self.store, key, item)


class LockedStore:
    """Stop issuing object operations as soon as the lock session is unavailable."""
    def __init__(self, connection, store):
        self.connection, self.store = connection, store

    def check(self):
        self.connection.execute("SELECT 1")

    def read(self, key):
        self.check()
        return self.store.read(key)

    def put(self, key, path):
        self.check()
        return self.store.put(key, path)

    def delete(self, key):
        self.check()
        return self.store.delete(key)

    def keys(self):
        for key in self.store.keys():
            self.check()
            yield key


def import_bundle(store, url, directory, staging_bytes):
    from data.import_snapshot import publish
    prefix = backup.safe_relative(backup.require_env("RESEARCH_IMPORT_BUNDLE_KEY"))
    raw = backup.read_bytes(store, prefix + "/bundle.json")
    bundle = json.loads(raw)
    reserved = positive("RESEARCH_IMPORT_RESTORED_BYTES")
    verification_bytes = positive("RESEARCH_IMPORT_VERIFY_BYTES")
    if len(raw) + sum(item["size"] for item in bundle["artifacts"]) + reserved + verification_bytes > staging_bytes:
        raise ValueError("Import exceeds staging capacity before writing")
    directory.mkdir()
    (directory / "bundle.json").write_bytes(raw)
    for item in bundle["artifacts"]:
        relative = backup.safe_relative(item["path"])
        backup.check_stream(store, prefix + "/artifacts/" + relative, item, backup.child(directory, "artifacts/" + relative))
    return publish(directory, url, ImportStorage(store), managed=True, max_restored_bytes=reserved, max_verification_bytes=verification_bytes)


def cleanup(connection, store):
    """Shared lock is held by caller; source objects are never cleanup candidates."""
    with connection.transaction():
        rows = connection.execute("""SELECT a.storage_key FROM research.query_artifacts a JOIN research.query_runs r ON r.id=a.run_id
            WHERE r.expires_at<=now() UNION SELECT g.storage_key FROM research.query_artifact_gc g WHERE g.delete_after<=now()
            AND NOT EXISTS(SELECT 1 FROM research.query_artifacts a JOIN research.query_runs r ON r.id=a.run_id
                WHERE a.storage_key=g.storage_key AND (r.expires_at IS NULL OR r.expires_at>now()))""").fetchall()
        for item in rows:
            key = backup.safe_relative(item["storage_key"])
            if not key.startswith("queries/"):
                raise ValueError("Cleanup refuses a source object")
            store.delete(key)
            connection.execute("DELETE FROM research.query_artifacts a USING research.query_runs r WHERE a.run_id=r.id AND a.storage_key=%s AND r.expires_at<=now()", (key,))
            connection.execute("DELETE FROM research.query_artifact_gc WHERE storage_key=%s", (key,))
        connection.execute("DELETE FROM research.query_results WHERE run_id IN (SELECT id FROM research.query_runs WHERE expires_at<now())")
        connection.execute("DELETE FROM research.request_limits WHERE window_start<now()-interval '1 day'")
    return {"deleted_query_objects": len(rows)}


def assert_managed_target(connection, manifest, baseline):
    target = connection.execute("SELECT current_database() AS name,current_user AS identity").fetchone()
    database = target["name"]
    if target["identity"] == manifest.get("backup_identity"):
        raise ValueError("Restore requires a distinct operator identity")
    if database == manifest["database"] or not database.endswith("_restore_test"):
        raise ValueError("Restore requires a distinct *_restore_test database")
    if connection.execute("SELECT 1 FROM pg_namespace WHERE nspname=ANY(%s)", (list(SCHEMAS),)).fetchone():
        raise ValueError("Restore requires an empty application namespace; aliases of the live database are rejected")
    if connection.execute("SELECT to_regclass('public.research_schema_migrations') AS name").fetchone()["name"]:
        raise ValueError("Restore target already has application migration state")
    if provider_baseline(connection) != baseline:
        raise ValueError("Restore target differs from its explicitly reviewed provider baseline")
    sessions = connection.execute("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()["count"]
    if sessions:
        raise ValueError("Restore target has other active clients")


def fence_restored_work(connection, max_attempts=3, max_launch_attempts=3, retention_days=30):
    """Only for a hash-verified isolated restore, never the live source database."""
    if min(max_attempts, max_launch_attempts, retention_days) < 1:
        raise ValueError("Restored execution limits must be positive")
    missing = connection.execute("""SELECT 1 FROM research.query_runs r WHERE r.state IN ('queued','running')
        AND NOT EXISTS(SELECT 1 FROM research.dispatch_outbox o WHERE o.run_id=r.id AND o.generation=r.dispatch_generation) LIMIT 1""").fetchone()
    if missing:
        raise ValueError("Restored accepted work is missing its matching dispatch intent")
    connection.execute("UPDATE research.dispatch_control SET paused=true")
    connection.execute("""UPDATE research.execution_reservations SET operation_name=NULL,execution_name=NULL,
        outcome=CASE WHEN state IN ('launching','uncertain','running') THEN 'restore_fenced' ELSE outcome END,
        state=CASE WHEN state IN ('launching','uncertain','running') THEN 'terminal' ELSE state END""")
    connection.execute("""UPDATE research.query_runs SET dispatch_generation=dispatch_generation+1,lease_owner=NULL,lease_until=NULL
        WHERE state IN ('queued','running')""")
    connection.execute("""UPDATE research.query_runs r SET state='failed',finished_at=now(),expires_at=now()+(%s*interval '1 day'),
        error='Execution was interrupted or exhausted before isolated recovery. Recompute this saved query after reviewing recovery capacity.'
        WHERE r.state='running' OR (r.state='queued' AND (r.attempts>=%s OR EXISTS(
            SELECT 1 FROM research.dispatch_outbox o WHERE o.run_id=r.id AND o.launch_attempts>=%s)))""",
        (retention_days, max_attempts, max_launch_attempts))
    connection.execute("""UPDATE research.dispatch_outbox o SET generation=r.dispatch_generation,
        state=CASE WHEN r.state='queued' THEN 'pending' ELSE 'done' END,
        eligible_at=now(),created_at=now() FROM research.query_runs r WHERE r.id=o.run_id""")
    connection.execute("DELETE FROM research.capacity_sample")


def restore(store, objects, identity, url, directory, staging_bytes):
    manifest = backup.manifest_for(store, identity)
    if not manifest.get("managed"):
        raise ValueError("VM backup requires explicit reviewed provider-role conversion before managed restore")
    if sum(item["size"] for item in manifest["members"]) + 16 * 1024 * 1024 > staging_bytes:
        raise ValueError("Restore exceeds staging capacity before writing")
    baseline = json.loads(backup.require_env("RESEARCH_RESTORE_BASELINE_JSON"))
    with psycopg.connect(url, row_factory=dict_row) as connection:
        assert_managed_target(connection, manifest, baseline)
    backup.verify_set(store, identity, directory)
    roles = json.loads((directory / "roles.json").read_bytes())
    if set(roles["roles"]) - set(APP_ROLES):
        raise ValueError("Backup contains unapproved provider roles")
    if any(item["role"] not in roles["roles"] or item["member"] not in roles["roles"] for item in roles["memberships"]):
        raise ValueError("Backup contains unapproved role membership")
    with psycopg.connect(url, row_factory=dict_row) as connection:
        assert_managed_target(connection, manifest, baseline)
        created = set()
        for role in roles["roles"]:
            existing = connection.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if existing:
                if any(existing.values()):
                    raise ValueError("Restore application role has administrative attributes")
            else:
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
                created.add(role)
        for item in roles["memberships"]:
            if item["member"] in created:
                connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(item["role"]), sql.Identifier(item["member"])))
            elif not connection.execute("SELECT pg_has_role(%s,%s,'MEMBER') AS member", (item["member"], item["role"])).fetchone()["member"]:
                raise ValueError("Existing restore role memberships are incompatible; use an isolated role namespace")
    backup.pg_command("pg_restore", url, ["--exit-on-error", "--single-transaction", "--no-owner", "--dbname=" + psycopg.conninfo.conninfo_to_dict(url)["dbname"], str(directory / "database.dump")])
    with psycopg.connect(url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if backup.table_checks(connection) != manifest["table_checks"]:
            raise ValueError("Restored application rows differ, including private rows")
        if backup.query_checks(connection) != manifest["query_checks"]:
            raise ValueError("Restored result hashes differ from the snapshot")
        if provider_baseline(connection) != baseline:
            raise ValueError("Restore altered provider baseline")
        connection.execute("CREATE TABLE public.research_schema_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL)")
        connection.execute("REVOKE ALL ON public.research_schema_migrations FROM PUBLIC")
        for item in manifest["applied_migrations"]:
            connection.execute("INSERT INTO public.research_schema_migrations VALUES(%s,%s,%s)", (item["name"], item["sha256"], item["applied_at"]))
        connection.execute("GRANT SELECT ON public.research_schema_migrations TO research_backup_login")
        fence_restored_work(connection, int(os.environ.get("RESEARCH_MAX_ATTEMPTS", "3")),
            int(os.environ.get("RESEARCH_MAX_LAUNCH_ATTEMPTS", "3")), int(os.environ.get("RESEARCH_RESULT_RETENTION_DAYS", "30")))
        connection.execute("UPDATE research.environment SET identity=gen_random_uuid()::text")
    # Full streaming table hashes above include all definitions, logical-source
    # hashes and saved identities. Avoid the VM verifier's fetchall/sort copies.
    with psycopg.connect(url, row_factory=dict_row) as connection:
        artifacts = connection.execute("SELECT storage_key,size,sha256,restored_sha256 FROM research.artifacts UNION SELECT storage_key,size,sha256,restored_sha256 FROM research.query_artifacts").fetchall()
        for item in artifacts:
            path = backup.child(directory / "objects", item["storage_key"])
            if path.stat().st_size != item["size"] or backup.digest_file(path) != item["sha256"]:
                raise ValueError("Restored registered object differs")
            if item["restored_sha256"]:
                with gzip.open(path, "rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != item["restored_sha256"]:
                        raise ValueError("Restored decompressed object differs")
    report = {"passed": True, "objects_verified": len(artifacts), "application_tables_verified": len(manifest["table_checks"]),
              "restoration_performed_by_script": True, "logical_verification": "streamed snapshot hashes of every application row"}
    for item in manifest["members"]:
        if item["path"].startswith("objects/"):
            key = item["path"][8:]
            ImportStorage(objects).put_verified(key, backup.child(directory, item["path"]), item["sha256"], "application/octet-stream")
    return {**report, "backup_id": identity, "query_result_hashes_verified": len(manifest["query_checks"]),
            "dispatch_paused": True, "api_and_worker_verified": False}


def operate(command, work, identity=None):
    url = backup.require_env("RESEARCH_OPERATOR_DATABASE_URL")
    require_tls(url, session=True)
    staging = positive("RESEARCH_OPERATOR_MAX_STAGING_BYTES")
    store = backup.R2Store()
    if command == "verify":
        with backup.database_operation_lock(url):
            if identity is None:
                candidates = backup.completed(store)
                if not candidates:
                    raise ValueError("No completed backup is available")
                identity = max(candidates, key=lambda item: item["completed_at"])["backup_id"]
            manifest = backup.verify_set(store, backup.backup_id(identity))
        return {"verified_members": len(manifest["members"])}
    if command == "prune":
        with backup.database_operation_lock(url) as connection:
            return backup.prune(LockedStore(connection, store), execute=True)
    objects = backup.R2Store("OBJECT_", backup.require_env("RESEARCH_OBJECT_PREFIX"))
    separate_namespaces(store, objects)
    if command == "restore":
        restore_prefix = backup.require_env("RESEARCH_RESTORE_OBJECT_PREFIX")
        restored_objects = backup.R2Store("OBJECT_", restore_prefix)
        separate_namespaces(objects, restored_objects)
        separate_namespaces(store, restored_objects)
        source_url = backup.require_env("RESEARCH_BACKUP_DATABASE_URL")
        require_tls(source_url, session=True)
        with backup.database_operation_lock(source_url) as connection:
            return restore(LockedStore(connection, store), LockedStore(connection, restored_objects), backup.backup_id(identity), url, work / "restore", staging)
    with backup.database_operation_lock(url) as connection:
        store, objects = LockedStore(connection, store), LockedStore(connection, objects)
        if command == "backup":
            identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
            manifest = backup.prepare_set(url, None, work / identity, identity, object_store=objects, managed=True,
                max_staging_bytes=staging, max_dump_bytes=positive("RESEARCH_OPERATOR_MAX_DUMP_BYTES"), connection=connection)
            manifest = backup.publish_set(store, work / identity, manifest)
            return {"backup_id": identity, "verified_members": len(manifest["members"]), "bytes": sum(item["size"] for item in manifest["members"])}
        if command == "cleanup":
            return cleanup(connection, objects)
        return import_bundle(objects, url, work / "import", staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "verify", "prune", "cleanup", "import", "restore"))
    args = parser.parse_args()
    began = time.monotonic()
    # A hard process limit bounds downloads, compilation and database work together.
    if hasattr(signal, "SIGALRM"):
        def expired(signum, frame):
            raise TimeoutError("Operator duration exhausted")
        signal.signal(signal.SIGALRM, expired)
        timeout = int(os.environ.get("RESEARCH_OPERATOR_TIMEOUT_SECONDS", "1500"))
        if timeout < 1:
            raise ValueError("Operator timeout must be positive")
        signal.alarm(timeout)
    result = {"operation": args.command, "operation_id": uuid.uuid4().hex, "reported_at": backup.now()}
    with tempfile.TemporaryDirectory(prefix="research-operator-") as directory:
        work = Path(directory)
        try:
            result.update(operate(args.command, work, os.environ.get("RESEARCH_BACKUP_ID")), passed=True)
        except Exception as error:
            result.update(passed=False, error_type=type(error).__name__)
        result["duration_seconds"] = round(time.monotonic() - began, 3)
        try:
            report = work / "report.json"
            backup.write_json(report, result)
            store = backup.R2Store(namespace=backup.require_env("RESEARCH_REPORT_PREFIX"))
            separate_namespaces(store, backup.R2Store())
            object_prefix = os.environ.get("RESEARCH_OBJECT_PREFIX")
            if object_prefix and os.environ.get("OBJECT_R2_BUCKET") == store.bucket:
                if store.prefix == object_prefix or store.prefix.startswith(object_prefix + "/") or object_prefix.startswith(store.prefix + "/"):
                    raise ValueError("Report and object namespaces overlap")
            store.put(result["operation_id"] + ".json", report)
        except Exception as error:
            result.update(passed=False, durable_report_failed=type(error).__name__)
        print(backup.packed(result).decode(), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
