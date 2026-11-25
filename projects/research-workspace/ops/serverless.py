"""One bounded managed-database operation per Cloud Run Job execution."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from deploy.managed import APP_ROLES, SCHEMAS, provider_baseline, require_tls
from . import backup, result_recovery


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

    def read_for_copy(self, key):
        self.check()
        return self.store.read_for_copy(key)

    def copy(self, key, source):
        self.check()
        return self.store.copy(key, source)

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


def cleanup(connection, store, result_objects=None, *, budget=None):
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
    result = {"deleted_query_objects": len(rows)}
    if result_objects is not None:
        result.update(result_recovery.cleanup(connection, result_objects, budget=budget))
    return result


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
    if connection.execute("SELECT to_regclass('research.budget_windows') AS name").fetchone()['name']:
        connection.execute("UPDATE research.budget_windows SET armed=false")


def restore(store, objects, identity, url, directory, staging_bytes, *, result_objects=None, result_copy_store=None):
    manifest = backup.manifest_for(store, identity)
    if not manifest.get("managed"):
        raise ValueError("VM backup requires explicit reviewed provider-role conversion before managed restore")
    digest_scratch = result_recovery.DIGEST_SCRATCH_BYTES if manifest.get('query_check_version', 1) == 2 else 0
    if sum(item["size"] for item in manifest["members"]) + 16 * 1024 * 1024 + digest_scratch > staging_bytes:
        raise ValueError("Restore exceeds staging capacity before writing")
    baseline = json.loads(backup.require_env("RESEARCH_RESTORE_BASELINE_JSON"))
    with psycopg.connect(url, row_factory=dict_row) as connection:
        assert_managed_target(connection, manifest, baseline)
    backup.verify_set(store, identity, directory)
    staged_results = result_recovery.staged_objects(directory) if manifest['format_version'] >= 2 else None
    if any(item['path'].startswith('results/') for item in manifest['members']) and result_objects is None:
        raise ValueError('Result recovery requires an isolated result namespace')
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
        if backup.query_checks(connection, staged_results, version=manifest.get('query_check_version', 1), scratch=directory) != manifest["query_checks"]:
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
    restored_results = result_recovery.restore_members(result_objects, manifest, directory,
        archive_store=store, copy_store=result_copy_store) if result_objects is not None else 0
    return {**report, "backup_id": identity, "query_result_hashes_verified": len(manifest["query_checks"]),
            "result_objects_restored": restored_results, "query_check_version": manifest.get('query_check_version', 1),
            "dispatch_paused": True, "api_and_worker_verified": False}


@contextmanager
def operator_budget(command):
    """One independent writable source session; never the dump or restore snapshot."""
    from service.budget import Budget, BudgetUnavailable
    window = os.environ.get('RESEARCH_BUDGET_WINDOW_ID', '')
    if not window and os.environ.get('RESEARCH_RESULT_STORAGE_MODE') != 'objects':
        yield None
        return
    if not window:
        raise BudgetUnavailable('Cloud object operators require a reviewed resource allowance window')
    source = backup.require_env('RESEARCH_BACKUP_DATABASE_URL' if command == 'restore' else 'RESEARCH_OPERATOR_DATABASE_URL')
    require_tls(source, session=True)
    with ConnectionPool(source, min_size=0, max_size=1, timeout=5,
                        kwargs={'row_factory': dict_row, 'connect_timeout': 5,
                                'options': '-c statement_timeout=5000 -c lock_timeout=5000'}) as pool:
        budget = Budget(pool, window)
        budget.invocation(operator=True)
        yield budget


def operator_database_url(command):
    url = backup.require_env("RESEARCH_OPERATOR_DATABASE_URL")
    override = os.environ.get('RESEARCH_RESTORE_DATABASE_NAME')
    if override is None:
        return url
    if command != 'restore':
        raise ValueError('A restore database override is only valid for the restore command')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', override) or not override.endswith('_restore_test'):
        raise ValueError('Restore database name must be a lowercase SQL identifier ending in _restore_test, at most 63 characters')
    source = psycopg.conninfo.conninfo_to_dict(backup.require_env('RESEARCH_BACKUP_DATABASE_URL'))
    if override == source.get('dbname'):
        raise ValueError('Restore database must differ from the live source database')
    return psycopg.conninfo.make_conninfo(url, dbname=override)


def operate(command, work, identity=None, *, budget=None):
    url = operator_database_url(command)
    require_tls(url, session=True)
    if os.environ.get('RESEARCH_RESULT_STORAGE_MODE') == 'objects' and budget is None:
        raise ValueError('Cloud object operators require a reviewed resource allowance')
    charge = budget.result_charge if budget is not None else None
    staging = positive("RESEARCH_OPERATOR_MAX_STAGING_BYTES")
    store = backup.R2Store(budget=budget)
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
    objects = backup.R2Store("OBJECT_", backup.require_env("RESEARCH_OBJECT_PREFIX"), budget=budget)
    separate_namespaces(store, objects)
    results = result_recovery.configured_objects(charge=charge) if os.environ.get('RESEARCH_RESULT_STORAGE_MODE') == 'objects' else None
    if results is not None:
        separate_namespaces(store, results)
        separate_namespaces(objects, results)
    if command == "restore":
        restore_prefix = backup.require_env("RESEARCH_RESTORE_OBJECT_PREFIX")
        restored_objects = backup.R2Store("OBJECT_", restore_prefix, budget=budget)
        separate_namespaces(objects, restored_objects)
        separate_namespaces(store, restored_objects)
        restored_results = None
        if results is not None:
            from service.config import Settings
            from service.result_objects import ResultObjects
            restored_results = ResultObjects.from_settings(replace(Settings.from_env(),
                result_prefix=backup.require_env('RESEARCH_RESTORE_RESULT_PREFIX')), charge=charge)
            for other in (results, store, objects, restored_objects):
                separate_namespaces(other, restored_results)
        source_url = backup.require_env("RESEARCH_BACKUP_DATABASE_URL")
        require_tls(source_url, session=True)
        with backup.database_operation_lock(source_url) as connection:
            result_copy_store = LockedStore(connection, backup.R2Store.from_result_objects(restored_results, budget=budget)) if restored_results is not None else None
            return restore(LockedStore(connection, store), LockedStore(connection, restored_objects), backup.backup_id(identity), url, work / "restore", staging,
                           result_objects=restored_results, result_copy_store=result_copy_store)
    with backup.database_operation_lock(url) as connection:
        store, objects = LockedStore(connection, store), LockedStore(connection, objects)
        if command == "backup":
            identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
            copy_sources = {}
            manifest = backup.prepare_set(url, None, work / identity, identity, object_store=objects, managed=True,
                max_staging_bytes=staging, max_dump_bytes=positive("RESEARCH_OPERATOR_MAX_DUMP_BYTES"), connection=connection,
                copy_sources=copy_sources, result_objects=results)
            manifest = backup.publish_set(store, work / identity, manifest, copy_sources=copy_sources)
            return {"backup_id": identity, "verified_members": len(manifest["members"]),
                    "bytes": sum(item["size"] for item in manifest["members"]),
                    "server_side_copied_objects": len(copy_sources),
                    "server_side_copied_bytes": sum(item["size"] for item in manifest["members"] if item["path"] in copy_sources)}
        if command == "cleanup":
            return cleanup(connection, objects, results, budget=budget)
        return import_bundle(objects, url, work / "import", staging)


def run(command, budget=None):
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
    charge = budget.result_charge if budget is not None else None
    result = {"operation": command, "operation_id": uuid.uuid4().hex, "reported_at": backup.now()}
    with tempfile.TemporaryDirectory(prefix="research-operator-") as directory:
        work = Path(directory)
        try:
            result.update(operate(command, work, os.environ.get("RESEARCH_BACKUP_ID"), budget=budget), passed=True)
        except Exception as error:
            result.update(passed=False, error_type=type(error).__name__)
        result["duration_seconds"] = round(time.monotonic() - began, 3)
        try:
            report = work / "report.json"
            backup.write_json(report, result)
            store = backup.R2Store(namespace=backup.require_env("RESEARCH_REPORT_PREFIX"), budget=budget)
            separate_namespaces(store, backup.R2Store(budget=budget))
            object_prefix = os.environ.get("RESEARCH_OBJECT_PREFIX")
            if object_prefix:
                separate_namespaces(store, backup.R2Store('OBJECT_', object_prefix, budget=budget))
            if os.environ.get('RESEARCH_RESULT_STORAGE_MODE') == 'objects':
                separate_namespaces(store, result_recovery.configured_objects(charge=charge))
            store.put(result["operation_id"] + ".json", report)
        except Exception as error:
            result.update(passed=False, durable_report_failed=type(error).__name__)
        print(backup.packed(result).decode(), flush=True)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('backup', 'verify', 'prune', 'cleanup', 'import', 'restore'))
    args = parser.parse_args()
    try:
        with operator_budget(args.command) as budget:
            return run(args.command, budget)
    except Exception as error:
        print(backup.packed({'operation': args.command, 'passed': False, 'error_type': type(error).__name__}).decode(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
