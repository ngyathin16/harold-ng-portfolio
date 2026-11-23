"""Prepare and publish immutable research snapshots; never reads collector secrets."""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
from decimal import Decimal
import gzip
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

if __package__:
    from . import schema
else:
    import schema


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(packed(value) + "\n", encoding="utf-8")


def child(root, relative):
    path = (root / relative.replace("\\", "/")).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path leaves its declared root")
    return path


def logical_rows(rows):
    normalized = ([float(value) if isinstance(value, Decimal) else value for value in row] for row in rows)
    ordered = sorted(packed(row) for row in normalized)
    return hashlib.sha256(("\n".join(ordered) + "\n").encode()).hexdigest()


def sqlite_rows(connection, name, definition):
    columns = ",".join(schema.identifier(c["name"]) for c in definition["columns"])
    return connection.execute(f"SELECT {columns} FROM {schema.identifier(name)}")


def prepare(project, snapshot, output, dataset_id="tft-set-19", name="TFT Set 19", adapter="tft_set19", scope=None):
    project = Path(project).resolve()
    if snapshot == "latest":
        snapshot = json.loads((project / "data/latest.json").read_text(encoding="utf-8"))["snapshot_id"]
    source_dir = child(project, f"data/snapshots/{snapshot}")
    database = source_dir / "tft-set-19.sqlite"
    manifest_path = source_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["snapshot_id"] != snapshot or manifest["schema_version"] != 1:
        raise ValueError("Snapshot identity or schema version is incompatible")
    if manifest["completeness"] not in ("validated_with_gaps", "complete_for_declared_scope"):
        raise ValueError("Only validated source snapshots can be prepared")
    scope = scope or {"kind": "full", "description": "Full validated source snapshot with its recorded attribution and reuse conditions.", "excluded_tables": []}
    if scope.get("kind") != "full" or scope.get("excluded_tables"):
        raise ValueError("This adapter supports full snapshots; scoped derivatives require their own verified mapping and artifacts")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Source database integrity or relationships failed")
        definitions = schema.check(connection)
        checks = {}
        for table, definition in definitions.items():
            count = connection.execute(f"SELECT count(*) FROM {schema.identifier(table)}").fetchone()[0]
            if definition["kind"] == "table" and manifest["counts"].get(table) != count:
                raise ValueError("Source manifest row count mismatch: " + table)
            checks[table] = {**definition, "row_count": count,
                             "logical_sha256": logical_rows(sqlite_rows(connection, table, definition))}
    export_root = child(project, f"exports/{snapshot}")
    for relative, expected in manifest["export_checksums"].items():
        if sha(child(export_root, relative)) != expected:
            raise ValueError("Source artifact checksum mismatch: " + relative)
    report_root = child(project, f"reports/{snapshot}")
    validation = json.loads((report_root / "validation.json").read_text(encoding="utf-8"))
    if not validation.get("passed"):
        raise ValueError("Published source validation did not pass")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_root = output / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    compressed = artifact_root / (database.name + ".gz")
    with database.open("rb") as source, compressed.open("wb") as target:
        with gzip.GzipFile(filename=database.name, mode="wb", fileobj=target, mtime=0) as archive:
            shutil.copyfileobj(source, archive)
    artifacts = []

    def add(path, relative, restored=None):
        destination = child(artifact_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != destination:
            shutil.copyfile(path, destination)
        artifacts.append({"id": relative, "path": relative, "sha256": sha(destination),
                          "size": destination.stat().st_size,
                          "media_type": "application/gzip" if relative.endswith(".gz") else mimetypes.guess_type(relative)[0] or "application/octet-stream",
                          "restored_sha256": sha(restored) if restored else None,
                          "restored_filename": restored.name if restored else None})

    add(compressed, compressed.name, database)
    add(manifest_path, "manifest.json")
    for filename in ("coverage.md", "validation.json", "discrepancies.csv", "changes.json", "changes.md"):
        add(report_root / filename, filename)
    for relative in sorted(manifest["export_checksums"]):
        normalized = relative.replace("\\", "/")
        add(child(export_root, relative), normalized)
    bundle = {"version": 1, "dataset": {"id": dataset_id, "name": name, "adapter": adapter,
              "description": "Versioned research records, evidence, and known coverage gaps.",
              "capabilities": ["catalog", "tables", "evidence"] + (["team_queries"] if adapter == "tft_set19" else [])},
              "snapshot_id": snapshot, "manifest": {**manifest, "validation": validation}, "scope": scope,
              "source_sha256": sha(database), "schema_fingerprint": schema.fingerprint(definitions),
              "tables": checks, "artifacts": artifacts}
    bundle["bundle_sha256"] = hashlib.sha256(packed(bundle).encode()).hexdigest()
    bundle["publication_id"] = dataset_id + "--" + snapshot + "--" + bundle["bundle_sha256"][:16]
    write(output / "bundle.json", bundle)
    return bundle


def verify_bundle(directory):
    directory = Path(directory).resolve()
    bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    fingerprint = {k: v for k, v in bundle.items() if k not in ("bundle_sha256", "publication_id")}
    if hashlib.sha256(packed(fingerprint).encode()).hexdigest() != bundle["bundle_sha256"]:
        raise ValueError("Import bundle metadata checksum mismatch")
    expected_id = bundle["dataset"]["id"] + "--" + bundle["snapshot_id"] + "--" + bundle["bundle_sha256"][:16]
    if bundle["publication_id"] != expected_id:
        raise ValueError("Import publication identity mismatch")
    if bundle["schema_fingerprint"] != schema.fingerprint(schema.load()):
        raise ValueError("Bundle needs a different source-schema migration")
    for artifact in bundle["artifacts"]:
        path = child(directory / "artifacts", artifact["path"])
        if path.stat().st_size != artifact["size"] or sha(path) != artifact["sha256"]:
            raise ValueError("Import artifact checksum mismatch: " + artifact["id"])
    return bundle


class LocalStorage:
    """Private filesystem objects published atomically without replacing existing bytes."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def put_verified(self, key, path, expected, media_type):
        target = child(self.directory, key)
        if target.exists():
            if sha(target) != expected:
                raise ValueError("Immutable object already exists with a different checksum")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination, path.open("rb") as source:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            if sha(temporary) != expected:
                raise ValueError("Stored artifact checksum mismatch")
            try:
                # An exclusive hard link publishes verified bytes without overwriting a concurrent writer.
                os.link(temporary, target)
            except FileExistsError:
                if sha(target) != expected:
                    raise ValueError("Immutable object already exists with a different checksum") from None
        finally:
            temporary.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Storage redirects are disabled to keep credentials on the configured host")


class SupabaseStorage:
    def __init__(self, url, key):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Supabase storage requires a clean HTTPS project URL")
        self.base = url.rstrip("/") + "/storage/v1/object/"
        self.key = key
        self.opener = urllib.request.build_opener(NoRedirect())

    def put_verified(self, key, path, expected, media_type):
        url = self.base + "research-artifacts/" + urllib.parse.quote(key, safe="/")
        headers = {"Authorization": "Bearer " + self.key, "apikey": self.key, "Content-Type": media_type, "x-upsert": "false"}
        request = urllib.request.Request(url, data=path.read_bytes(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=120) as response:
                response.read()
        except urllib.error.HTTPError as error:
            # Existing immutable objects are accepted only after full content verification.
            if error.code not in (400, 409):
                raise ValueError(f"Storage upload failed (HTTP {error.code})") from None
        request = urllib.request.Request(url, headers={"Authorization": headers["Authorization"], "apikey": self.key})
        try:
            with self.opener.open(request, timeout=120) as response:
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
        except urllib.error.HTTPError as error:
            raise ValueError(f"Storage verification failed (HTTP {error.code})") from None
        if digest.hexdigest() != expected:
            raise ValueError("Remote artifact checksum mismatch")


@contextmanager
def restored_database(directory, bundle):
    artifact = next(a for a in bundle["artifacts"] if a["restored_filename"])
    with tempfile.TemporaryDirectory(prefix="research-import-") as temporary:
        path = Path(temporary) / "source.sqlite"
        with gzip.open(child(Path(directory) / "artifacts", artifact["path"]), "rb") as source, path.open("wb") as target:
            shutil.copyfileobj(source, target)
        if sha(path) != bundle["source_sha256"] or sha(path) != artifact["restored_sha256"]:
            raise ValueError("Restored SQLite checksum mismatch")
        yield path


def publish(directory, database_url, storage):
    import psycopg
    from psycopg.types.json import Jsonb
    directory = Path(directory).resolve()
    bundle = verify_bundle(directory)
    publication = bundle["publication_id"]
    dataset = bundle["dataset"]
    for artifact in bundle["artifacts"]:
        storage.put_verified(publication + "/" + artifact["path"], child(directory / "artifacts", artifact["path"]), artifact["sha256"], artifact["media_type"])
    with restored_database(directory, bundle) as database, closing(sqlite3.connect(database)) as source, psycopg.connect(database_url) as target:
        target.execute("SET LOCAL statement_timeout='120s'")
        target.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (dataset["id"],))
        previous = target.execute("SELECT bundle_sha256,status FROM research.publications WHERE id=%s", (publication,)).fetchone()
        if previous:
            if previous != (bundle["bundle_sha256"], "published"):
                raise ValueError("Conflicting publication already exists")
            return {"publication_id": publication, "status": "already_published", "parity": "verified_on_original_import"}
        target.execute("INSERT INTO research.datasets(id,name,description,adapter,capabilities) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                       (dataset["id"], dataset["name"], dataset["description"], dataset["adapter"], Jsonb(dataset["capabilities"])))
        target.execute("INSERT INTO research.publications(id,dataset_id,snapshot_id,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                       (publication, dataset["id"], bundle["snapshot_id"], Jsonb(bundle["manifest"]), Jsonb(bundle["scope"]), bundle["source_sha256"], bundle["bundle_sha256"], bundle["schema_fingerprint"]))
        for name, definition in bundle["tables"].items():
            if definition["kind"] != "table":
                continue
            names = [c["name"] for c in definition["columns"]]
            prefix = [column for column in schema.SCOPE if column not in names]
            scope_values = {"publication_id": publication, "dataset_id": dataset["id"], "snapshot_id": bundle["snapshot_id"]}
            columns = ",".join(schema.identifier(c) for c in prefix + names)
            with target.cursor().copy(f"COPY research_data.{schema.identifier(name)} ({columns}) FROM STDIN") as copy:
                for row in sqlite_rows(source, name, definition):
                    copy.write_row(tuple(scope_values[c] for c in prefix) + tuple(row))
        target.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for name, definition in bundle["tables"].items():
            columns = ",".join(schema.identifier(c["name"]) for c in definition["columns"])
            rows = target.execute(f"SELECT {columns} FROM research_data.{schema.identifier(name)} WHERE publication_id=%s", (publication,)).fetchall()
            if len(rows) != definition["row_count"] or logical_rows(rows) != definition["logical_sha256"]:
                raise ValueError("Postgres parity failed: " + name)
            if definition["kind"] == "table" and name != "source_documents" and any(c["name"] == "source_id" for c in definition["columns"]):
                missing = target.execute(f"SELECT count(*) FROM research_data.{schema.identifier(name)} r LEFT JOIN research_data.source_documents s ON s.publication_id=r.publication_id AND s.source_id=r.source_id WHERE r.publication_id=%s AND r.source_id IS NOT NULL AND s.source_id IS NULL", (publication,)).fetchone()[0]
                if missing:
                    raise ValueError("Missing evidence source: " + name)
            target.execute("INSERT INTO research.table_definitions(publication_id,name,kind,columns,primary_key,row_count,logical_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                           (publication, name, definition["kind"], Jsonb(definition["columns"]), Jsonb(definition["primary_key"]), definition["row_count"], definition["logical_sha256"]))
        for artifact in bundle["artifacts"]:
            target.execute("INSERT INTO research.artifacts(publication_id,id,path,storage_key,sha256,size,media_type,restored_sha256,restored_filename) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           (publication, artifact["id"], artifact["path"], publication + "/" + artifact["path"], artifact["sha256"], artifact["size"], artifact["media_type"], artifact["restored_sha256"], artifact["restored_filename"]))
        if dataset["adapter"] == "tft_set19":
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solver"))
            from rules import build_rules
            rules = build_rules(database, dataset_id=dataset["id"], publication_id=publication)
            target.execute("INSERT INTO research.rulesets(publication_id,version,rules) VALUES(%s,%s,%s)", (publication, rules["version"], Jsonb(rules)))
        target.execute("UPDATE research.publications SET status='published',published_at=now() WHERE id=%s", (publication,))
        target.execute("UPDATE research.datasets SET latest_publication_id=%s WHERE id=%s", (publication, dataset["id"]))
    return {"publication_id": publication, "status": "published", "parity": "all_tables_and_views_verified", "tables_and_views": len(bundle["tables"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2] / "tft-set-19")
    prepare_command.add_argument("--snapshot", default="latest")
    prepare_command.add_argument("--output", type=Path, required=True)
    prepare_command.add_argument("--dataset", default="tft-set-19")
    prepare_command.add_argument("--name", default="TFT Set 19")
    prepare_command.add_argument("--adapter", default="tft_set19")
    publish_command = commands.add_parser("publish")
    publish_command.add_argument("bundle", type=Path)
    publish_command.add_argument("--local-storage", type=Path, help="Private local integration-test object store")
    args = parser.parse_args()
    if args.command == "prepare":
        bundle = prepare(args.project, args.snapshot, args.output, args.dataset, args.name, args.adapter)
        print(packed({"publication_id": bundle["publication_id"], "tables_and_views": len(bundle["tables"]), "artifacts": len(bundle["artifacts"]), "bundle": str(args.output)}))
    else:
        storage = LocalStorage(args.local_storage) if args.local_storage else SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
        print(packed(publish(args.bundle, os.environ["DATABASE_URL"], storage)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Connection errors can contain privileged connection strings; keep them off stdout.
        detail = str(error) if isinstance(error, (ValueError, FileNotFoundError)) else type(error).__name__
        print("Import failed: " + detail + ". Existing publication remains available.", file=sys.stderr)
        raise SystemExit(1)
