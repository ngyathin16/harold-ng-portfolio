"""Private, self-contained PostgreSQL recovery sets in R2; never a public API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


LOCK_NAME = "research-backup-objects"
FORMAT_VERSION = 1
MAX_MEMBER_BYTES = 5_000_000_000
APP_ROOT = Path(__file__).resolve().parents[1]


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Invalid relative object path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Object path leaves its namespace")
    return value


def child(root, name):
    root = Path(root).resolve()
    path = (root / safe_relative(name)).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Object path leaves its namespace")
    return path


def backup_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", value):
        raise ValueError("Invalid backup ID")
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(packed(value) + b"\n")
    temporary.replace(path)


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError("Missing required configuration: " + name)
    return value


class R2Store:
    def __init__(self, env_prefix="", namespace=None):
        import boto3
        from botocore.config import Config
        endpoint = require_env(env_prefix + "R2_ENDPOINT")
        parsed = urlparse(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or parsed.port not in (None, 443)):
            raise ValueError("R2_ENDPOINT must be an HTTPS R2 S3 endpoint")
        self.bucket = require_env(env_prefix + "R2_BUCKET")
        self.prefix = safe_relative((namespace or os.environ.get("RESEARCH_BACKUP_PREFIX", "research-backups")).strip("/"))
        self.client = boto3.client("s3", endpoint_url=endpoint, region_name="auto",
            aws_access_key_id=require_env(env_prefix + "R2_ACCESS_KEY_ID"),
            aws_secret_access_key=require_env(env_prefix + "R2_SECRET_ACCESS_KEY"),
            config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60,
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))
        self.requests = 0

    def key(self, name):
        return self.prefix + "/" + safe_relative(name)

    def read(self, name):
        self.requests += 1
        return self.client.get_object(Bucket=self.bucket, Key=self.key(name))["Body"]

    def put(self, name, path):
        if Path(path).stat().st_size > MAX_MEMBER_BYTES:
            raise ValueError("Backup member exceeds the supported single-upload size")
        self.requests += 1
        with Path(path).open("rb") as stream:
            self.client.put_object(Bucket=self.bucket, Key=self.key(name), Body=stream, IfNoneMatch="*")

    def keys(self):
        paginator = self.client.get_paginator("list_objects_v2")
        prefix = self.prefix + "/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            self.requests += 1
            for item in page.get("Contents", []):
                if not item["Key"].startswith(prefix):
                    raise ValueError("Listing left the configured namespace")
                yield item["Key"][len(prefix):]

    def delete(self, name):
        self.requests += 1
        self.client.delete_object(Bucket=self.bucket, Key=self.key(name))


def read_bytes(store, key, maximum=16 * 1024 * 1024):
    with store.read(key) as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("Backup metadata exceeds the supported size")
    return value


def check_stream(store, key, item, destination=None):
    digest, size = hashlib.sha256(), 0
    output = None
    try:
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output = destination.open("xb")
            os.chmod(destination, 0o600)
        with store.read(key) as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > item["size"]:
                    raise ValueError("Backup member exceeds its declared size")
                digest.update(block)
                if output:
                    output.write(block)
        if size != item["size"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("Backup member size or SHA-256 mismatch")
    finally:
        if output:
            output.close()


def manifest_for(store, identity):
    identity = backup_id(identity)
    marker = json.loads(read_bytes(store, identity + "/complete.json"))
    raw = read_bytes(store, identity + "/manifest.json")
    if marker.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("Backup manifest hash mismatch")
    manifest = json.loads(raw)
    if manifest.get("format_version") != FORMAT_VERSION or manifest.get("backup_id") != identity:
        raise ValueError("Incompatible backup format or identity")
    members = manifest["members"]
    names = [safe_relative(item["path"]) for item in members]
    if len(names) != len(set(names)) or "database.dump" not in names or "roles.json" not in names:
        raise ValueError("Backup member inventory is invalid")
    for item in members:
        if item["path"] not in ("database.dump", "roles.json") and not item["path"].startswith("objects/"):
            raise ValueError("Unexpected backup member")
        if not isinstance(item["size"], int) or item["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Invalid member checksum or size")
    return manifest


def completed(store):
    identities = sorted({key.split("/")[0] for key in store.keys() if key.count("/") == 1 and key.endswith("/complete.json")})
    return [manifest_for(store, identity) for identity in identities]


def verify_set(store, identity, directory=None):
    manifest = manifest_for(store, identity)
    if directory:
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for item in manifest["members"]:
        destination = child(directory, item["path"]) if directory else None
        check_stream(store, identity + "/" + item["path"], item, destination)
    if directory:
        write_json(directory / "manifest.json", manifest)
    return manifest


def pg_command(tool, url, arguments, *, max_file_bytes=None, timeout=1500):
    executable = str(Path(os.environ["PG_BIN"]) / tool) if os.environ.get("PG_BIN") else tool
    connection = conninfo_to_dict(url)
    environment = dict(os.environ)
    # libpq reads the connection string from the child environment, never argv.
    supported = {"host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT", "dbname": "PGDATABASE",
                 "user": "PGUSER", "password": "PGPASSWORD", "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
                 "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY", "passfile": "PGPASSFILE", "options": "PGOPTIONS",
                 "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME"}
    if set(connection) - supported.keys():
        raise ValueError("Unsupported libpq connection option for backup subprocess")
    for key in supported.values():
        environment.pop(key, None)
    environment.update({supported[key]: value for key, value in connection.items()})
    environment["PGCONNECT_TIMEOUT"] = "15"
    limit = None
    if max_file_bytes is not None:
        if os.name != "posix":
            raise ValueError("Bounded managed dumps require the Linux operator container")
        def limit():
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
    process = subprocess.run([executable, *arguments], env=environment, capture_output=True, text=True,
                             timeout=timeout, preexec_fn=limit)
    if process.returncode:
        raise RuntimeError(tool + " failed; inspect the isolated database and server logs")
    return process.stdout.strip()


def query_checks(connection):
    checks = []
    runs = connection.execute("SELECT id,state,result_count,champion_set_count FROM research.query_runs ORDER BY id").fetchall()
    for run in runs:
        checksum = hashlib.sha256()
        count = 0
        with connection.cursor(name="backup_results_" + uuid.uuid4().hex) as cursor:
            cursor.itersize = 1000
            cursor.execute("SELECT run_id,configuration_key,champion_set_key,active_trait_count,total_cost,champion_names,discovery_index,result FROM research.query_results WHERE run_id=%s ORDER BY configuration_key", (run["id"],))
            for row in cursor:
                checksum.update(packed(row) + b"\n")
                count += 1
        champion_sets = connection.execute("SELECT count(DISTINCT champion_set_key) AS count FROM research.query_results WHERE run_id=%s", (run["id"],)).fetchone()["count"]
        checks.append({**run, "stored_rows": count, "stored_champion_sets": champion_sets, "rows_sha256": checksum.hexdigest()})
    return checks


def role_definitions(connection):
    names = [row["rolname"] for row in connection.execute("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname")]
    members = connection.execute("""SELECT r.rolname AS role,m.rolname AS member FROM pg_auth_members a
        JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member
        WHERE r.rolname !~ '^pg_' AND m.rolname !~ '^pg_' ORDER BY r.rolname,m.rolname""").fetchall()
    return {"roles": names, "memberships": [row for row in members if row["role"] in names and row["member"] in names]}


def table_checks(connection):
    """Hash every application row, including private/unpublished rows and state."""
    connection.execute("SET LOCAL timezone='UTC'")
    tables = connection.execute("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('research','research_data') AND c.relkind='r' ORDER BY 1,2""").fetchall()
    checks = []
    for table in tables:
        digest, count = hashlib.sha256(), 0
        with connection.cursor(name="all_rows_" + uuid.uuid4().hex) as cursor:
            cursor.itersize = 1000
            cursor.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"').format(sql.Identifier(table["nspname"], table["relname"])))
            for row in cursor:
                digest.update(packed(row["row"]) + b"\n")
                count += 1
        checks.append({"schema": table["nspname"], "table": table["relname"], "rows": count, "sha256": digest.hexdigest()})
    return checks


def prepare_set(url, objects, directory, identity, *, object_store=None, managed=False,
                max_staging_bytes=None, max_dump_bytes=None, connection=None):
    commit = require_env("RESEARCH_APP_COMMIT")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("RESEARCH_APP_COMMIT must be a full Git commit ID")
    started = now()
    context = nullcontext(connection) if connection is not None else psycopg.connect(url, autocommit=True, row_factory=dict_row)
    with context as connection:
        connection.execute("SET statement_timeout='120s'")
        connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        try:
            with connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                snapshot = connection.execute("SELECT pg_export_snapshot() AS id,current_database() AS database,transaction_timestamp() AS snapshot_at").fetchone()
                server_version = connection.execute("SHOW server_version").fetchone()["server_version"]
                inventory = connection.execute("SELECT storage_key,size,sha256 FROM research.artifacts UNION SELECT storage_key,size,sha256 FROM research.query_artifacts ORDER BY storage_key").fetchall()
                if len({item["storage_key"] for item in inventory}) != len(inventory):
                    raise ValueError("An object key has conflicting registered checksums")
                if managed:
                    from deploy.managed import APP_ROLES, ROLE_MAP, SCHEMAS
                    reader = connection.execute("SELECT current_user AS name,r.rolsuper,r.rolbypassrls FROM pg_roles r WHERE r.rolname=current_user").fetchone()
                    if reader["name"] != "research_backup_login" or reader["rolsuper"] or reader["rolbypassrls"]:
                        raise ValueError("Managed backup requires the dedicated non-administrative backup reader")
                    if not max_staging_bytes or not max_dump_bytes or max_dump_bytes <= 0:
                        raise ValueError("Managed backup requires explicit staging and dump byte budgets")
                    if sum(item["size"] for item in inventory) + max_dump_bytes + 16 * 1024 * 1024 > max_staging_bytes:
                        raise ValueError("Backup exceeds the operator staging budget before writing")
                    if any(item["size"] > MAX_MEMBER_BYTES for item in inventory):
                        raise ValueError("Source object exceeds the supported backup member size")
                    restrictive = connection.execute("""SELECT 1 FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s)
                        AND NOT p.polpermissive AND p.polcmd IN ('*','r') LIMIT 1""", (list(SCHEMAS),)).fetchone()
                    if restrictive:
                        raise ValueError("Restrictive RLS requires explicit private-row qualification before backup")
                    missing_policy = connection.execute("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p') AND c.relrowsecurity
                        AND NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname='managed_backup_read'
                            AND p.polcmd='r' AND p.polpermissive AND pg_get_expr(p.polqual,p.polrelid)='true'
                            AND 'research_backup_login'::regrole=ANY(p.polroles)) LIMIT 1""", (list(SCHEMAS),)).fetchone()
                    if missing_policy:
                        raise ValueError("Backup requires a verified all-row SELECT policy on every RLS table")
                directory.mkdir(mode=0o700, parents=True, exist_ok=False)
                publications = connection.execute("SELECT id,dataset_id,snapshot_id,status FROM research.publications ORDER BY id").fetchall()
                ledger = connection.execute("SELECT to_regclass('public.research_schema_migrations') AS name").fetchone()["name"]
                applied = connection.execute("SELECT name,sha256,applied_at FROM public.research_schema_migrations ORDER BY name").fetchall() if ledger else None
                checks = query_checks(connection)
                all_tables = table_checks(connection) if managed else None
                roles = role_definitions(connection)
                if managed:
                    roles["roles"] = [name for name in roles["roles"] if name in APP_ROLES]
                    roles["memberships"] = [item for item in roles["memberships"] if item["role"] in APP_ROLES and item["member"] in APP_ROLES]
                    roles["provider_role_mapping"] = ROLE_MAP
                write_json(directory / "roles.json", roles)
                for item in inventory:
                    destination = child(directory, "objects/" + item["storage_key"])
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if object_store is not None:
                        check_stream(object_store, item["storage_key"], item, destination)
                    else:
                        source = child(objects, item["storage_key"])
                        shutil.copyfile(source, destination)
                    os.chmod(destination, 0o600)
                    if destination.stat().st_size != item["size"] or digest_file(destination) != item["sha256"]:
                        raise ValueError("Registered source object is missing or corrupt")
                options = ["--schema=research", "--schema=research_data", "--enable-row-security"] if managed else []
                pg_command("pg_dump", url, ["--format=custom", "--no-owner", *options,
                    "--snapshot=" + snapshot["id"], "--file=" + str(directory / "database.dump")], max_file_bytes=max_dump_bytes if managed else None)
                os.chmod(directory / "database.dump", 0o600)
                pg_command("pg_restore", url, ["--list", str(directory / "database.dump")])
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
    members = [{"path": path.relative_to(directory).as_posix(), "size": path.stat().st_size, "sha256": digest_file(path)}
               for path in sorted(directory.rglob("*")) if path.is_file()]
    migrations = {path.relative_to(APP_ROOT).as_posix(): digest_file(path)
                  for folder in (APP_ROOT / "migrations", APP_ROOT / "service/migrations") for path in sorted(folder.glob("*.sql"))}
    return {"format_version": FORMAT_VERSION, "backup_id": identity, "started_at": started, "snapshot_at": str(snapshot["snapshot_at"]),
            "prepared_at": now(), "database": snapshot["database"], "server_version": server_version,
            "client_version": pg_command("pg_dump", url, ["--version"]), "application_commit": commit,
            "migration_files": migrations, "applied_migrations": applied,
            "publications": publications, "query_checks": checks, "members": members, "managed": managed,
            "table_checks": all_tables, "backup_identity": reader["name"] if managed else None}


class OperatorBusy(RuntimeError):
    pass


@contextmanager
def database_operation_lock(url):
    """A session lock is released by PostgreSQL when an operator is terminated."""
    with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=10) as connection:
        acquired = connection.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired", (LOCK_NAME,)).fetchone()["acquired"]
        if not acquired:
            raise OperatorBusy("Another backup, retention, import or cleanup is running")
        try:
            yield connection
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def publish_set(store, directory, manifest):
    identity = manifest["backup_id"]
    if any(item["size"] > MAX_MEMBER_BYTES for item in manifest["members"]):
        raise ValueError("Backup member exceeds the supported single-upload size")
    for item in manifest["members"]:
        key = identity + "/" + item["path"]
        store.put(key, child(directory, item["path"]))
        check_stream(store, key, item)
    manifest["completed_at"] = now()
    write_json(directory / "manifest.json", manifest)
    store.put(identity + "/manifest.json", directory / "manifest.json")
    check_stream(store, identity + "/manifest.json", {"size": (directory / "manifest.json").stat().st_size, "sha256": digest_file(directory / "manifest.json")})
    write_json(directory / "complete.json", {"sha256": digest_file(directory / "manifest.json")})
    store.put(identity + "/complete.json", directory / "complete.json")
    manifest_for(store, identity)
    return manifest


def reject_links(path):
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("Backup staging cleanup refuses links and junctions")


def discard_staging(store, work_root, identity):
    """Delete only verified payload files from one completed local backup set."""
    identity = backup_id(identity)
    reject_links(work_root)
    root = Path(work_root).resolve(strict=True)
    directory = root / identity
    reject_links(directory)
    directory = directory.resolve(strict=True)
    if directory.parent != root:
        raise ValueError("Backup staging directory leaves its work root")
    manifest = manifest_for(store, identity)
    reject_links(directory / "manifest.json")
    reject_links(directory / "complete.json")
    if json.loads((directory / "manifest.json").read_bytes()) != manifest:
        raise ValueError("Local staging manifest differs from the completed remote set")
    marker = json.loads((directory / "complete.json").read_bytes())
    if marker.get("sha256") != digest_file(directory / "manifest.json"):
        raise ValueError("Local staging completion marker is invalid")
    expected = {item["path"] for item in manifest["members"]} | {"manifest.json", "complete.json"}
    expected_directories = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    present = set()
    for path in directory.rglob("*"):
        reject_links(path)
        if path.is_file():
            present.add(path.relative_to(directory).as_posix())
        elif path.is_dir() and path.relative_to(directory).as_posix() not in expected_directories:
            raise ValueError("Backup staging has an unexpected directory")
    if present != expected:
        raise ValueError("Backup staging has missing or unexpected files")
    for item in manifest["members"]:
        path = child(directory, item["path"])
        if path.stat().st_size != item["size"] or digest_file(path) != item["sha256"]:
            raise ValueError("Local staging member does not match its backup manifest")
    verify_set(store, identity)
    removed_bytes = 0
    for item in manifest["members"]:
        path = child(directory, item["path"])
        reject_links(path)
        path.unlink()
        removed_bytes += item["size"]
    for path in sorted(directory.rglob("*"), key=lambda entry: len(entry.parts), reverse=True):
        reject_links(path)
        if path.is_dir():
            path.rmdir()
    return removed_bytes


def assert_restore_target(connection, manifest):
    database = connection.execute("SELECT current_database() AS name").fetchone()["name"]
    if database == manifest["database"] or not database.endswith("_restore_test"):
        raise ValueError("Restore requires a different database ending in _restore_test")
    schemas = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')").fetchall()
    objects = connection.execute("""SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace
        UNION ALL SELECT 1 FROM pg_proc WHERE pronamespace='public'::regnamespace
        UNION ALL SELECT 1 FROM pg_type WHERE typnamespace='public'::regnamespace""").fetchall()
    sessions = connection.execute("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()["count"]
    if schemas or objects or sessions:
        raise ValueError("Restore target must be empty and have no other connected clients")


def restore_set(store, identity, target_url, directory):
    began = time.monotonic()
    manifest = manifest_for(store, identity)
    with psycopg.connect(target_url, row_factory=dict_row) as connection:
        assert_restore_target(connection, manifest)
    manifest = verify_set(store, identity, directory)
    roles = json.loads((directory / "roles.json").read_bytes())
    with psycopg.connect(target_url, row_factory=dict_row) as connection:
        assert_restore_target(connection, manifest)
        created = set()
        for role in roles["roles"]:
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
                created.add(role)
        for membership in roles["memberships"]:
            if membership["member"] in created:
                connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(membership["role"]), sql.Identifier(membership["member"])))
        # Existing cluster roles are not changed by a recovery drill.
        # Login passwords and privileged attributes need deliberate provisioning.
    pg_command("pg_restore", target_url, ["--exit-on-error", "--single-transaction", "--no-owner", "--dbname=" + conninfo_to_dict(target_url)["dbname"], str(directory / "database.dump")])
    from data.verify_restore import verify
    report = verify(target_url, directory / "objects")
    with psycopg.connect(target_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actual = query_checks(connection)
        if actual != manifest["query_checks"]:
            raise ValueError("Restored query rows or counts differ from the backup snapshot")
        connection.execute("UPDATE research.query_runs SET lease_owner=NULL,lease_until=now()-interval '1 day' WHERE state='running'")
    report.update(backup_id=identity, restoration_performed_by_script=True, query_result_hashes_verified=len(actual),
                  restore_seconds=round(time.monotonic() - began, 3), completed_at=now(), snapshot_at=manifest["snapshot_at"],
                  snapshot_age_seconds=max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(manifest["snapshot_at"])).total_seconds()),
                  api_and_worker_verified=False)
    write_json(directory / "restore-report.json", report)
    return report


def retention_plan(manifests, daily=7, weekly=4):
    if daily < 1 or weekly < 0:
        raise ValueError("Retention must keep at least one daily backup")
    ordered = sorted(manifests, key=lambda item: item["completed_at"], reverse=True)
    keep, days, weeks = set(), set(), set()
    for item in ordered:
        stamp = datetime.fromisoformat(item["completed_at"])
        day, week = stamp.date(), stamp.isocalendar()[:2]
        if (day not in days and len(days) < daily) or (week not in weeks and len(weeks) < weekly):
            keep.add(item["backup_id"])
        days.add(day)
        weeks.add(week)
    return {"keep": sorted(keep), "delete": [item["backup_id"] for item in ordered if item["backup_id"] not in keep]}


def prune(store, execute=False, daily=7, weekly=4):
    manifests = completed(store)
    plan = retention_plan(manifests, daily, weekly)
    if manifests:
        newest = max(manifests, key=lambda item: item["completed_at"])
        verify_set(store, newest["backup_id"])
    if execute:
        for identity in plan["delete"]:
            manifest = next(item for item in manifests if item["backup_id"] == identity)
            store.delete(identity + "/complete.json")
            for item in manifest["members"]:
                store.delete(identity + "/" + item["path"])
            store.delete(identity + "/manifest.json")
    return {**plan, "executed": execute, "newest_verified": bool(manifests)}


@contextmanager
def local_operation_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / ".backup-operation.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def run(args):
    store = R2Store()
    if args.command == "list":
        result = {"backups": [{"backup_id": item["backup_id"], "snapshot_at": item["snapshot_at"], "completed_at": item["completed_at"],
                              "age_seconds": max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(item["snapshot_at"])).total_seconds()),
                              "bytes": sum(member["size"] for member in item["members"])} for item in completed(store)],
                  "member_verification": "not run; use verify"}
    elif args.command == "verify":
        manifest = verify_set(store, args.backup_id)
        result = {"backup_id": args.backup_id, "verified_members": len(manifest["members"])}
    elif args.command == "prune":
        result = prune(store, args.execute, args.daily, args.weekly)
    elif args.command == "restore":
        result = restore_set(store, args.backup_id, require_env("RESEARCH_RESTORE_DATABASE_URL"), args.work_dir / (args.backup_id + "-restore-" + uuid.uuid4().hex[:8]))
    else:
        identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        directory = args.work_dir / identity
        manifest = prepare_set(require_env("RESEARCH_BACKUP_DATABASE_URL"), Path(require_env("RESEARCH_ARTIFACT_ROOT")), directory, identity)
        manifest = publish_set(store, directory, manifest)
        result = {"backup_id": identity, "snapshot_at": manifest["snapshot_at"], "completed_at": manifest["completed_at"],
                  "verified_members": len(manifest["members"]), "bytes": sum(item["size"] for item in manifest["members"])}
        if args.discard_staging:
            result["staging_bytes_removed"] = discard_staging(store, args.work_dir, identity)
    result.update(passed=True, command=args.command, r2_requests=store.requests)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "list", "verify", "restore", "prune"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--backup-id")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--execute", action="store_true", help="Actually prune old complete sets; default is a dry run")
    parser.add_argument("--discard-staging", action="store_true", help="After remote verification, remove this backup's local payload; retain its manifest and reports")
    parser.add_argument("--daily", type=int, default=7)
    parser.add_argument("--weekly", type=int, default=4)
    args = parser.parse_args()
    if args.command in ("verify", "restore") and not args.backup_id:
        parser.error("--backup-id is required")
    if args.discard_staging and args.command != "backup":
        parser.error("--discard-staging applies only to backup")
    os.umask(0o077)
    started = time.monotonic()
    def failure(error):
        return {"passed": False, "command": args.command, "error_type": type(error).__name__,
                "message": "Operation failed. Previous completed backups remain available; inspect configuration and retry with a fresh work directory.",
                "duration_seconds": round(time.monotonic() - started, 3), "reported_at": now()}

    try:
        with local_operation_lock(args.work_dir):
            try:
                result = run(args)
            except Exception as error:
                result = failure(error)
            result["duration_seconds"] = round(time.monotonic() - started, 3)
            result["reported_at"] = now()
            if args.report:
                write_json(args.report, result)
            write_json(args.work_dir / ("last-" + args.command + "-attempt.json"), result)
            if result["passed"]:
                write_json(args.work_dir / ("last-" + args.command + "-success.json"), result)
    except Exception as error:
        # A competing process must not overwrite the active operation's reports.
        result = failure(error)
    print(packed(result).decode())
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
