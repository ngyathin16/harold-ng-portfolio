"""Read-only verification of a restored database and separately restored objects."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

if __package__:
    from . import import_snapshot as importer
    from . import schema
else:
    import import_snapshot as importer
    import schema


def verify(database_url, object_root, publication_id=None):
    object_root = Path(object_root).resolve()
    verified_publications, verified_objects, query_definitions = [], 0, 0
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout='120s'")
        publications = connection.execute("SELECT * FROM research.publications WHERE status='published' AND (%s::text IS NULL OR id=%s) ORDER BY id", (publication_id, publication_id)).fetchall()
        if not publications:
            raise ValueError("No matching published snapshot exists in the restored database")

        def verify_object(item):
            nonlocal verified_objects
            path = importer.child(object_root, item["storage_key"])
            if path.stat().st_size != item["size"] or importer.sha(path) != item["sha256"]:
                raise ValueError("Restored object does not match declared bytes: " + item["storage_key"])
            if item.get("restored_sha256"):
                with gzip.open(path, "rb") as restored:
                    digest = hashlib.file_digest(restored, "sha256").hexdigest()
                if digest != item["restored_sha256"]:
                    raise ValueError("Restored SQLite decompression hash mismatch")
            verified_objects += 1

        for publication in publications:
            definitions = connection.execute("SELECT * FROM research.table_definitions WHERE publication_id=%s ORDER BY name", (publication["id"],)).fetchall()
            if not definitions:
                raise ValueError("Restored publication has no table definitions")
            for definition in definitions:
                columns = [column["name"] for column in definition["columns"]]
                rows = connection.execute("SELECT " + ",".join(map(schema.identifier, columns)) +
                    " FROM research_data." + schema.identifier(definition["name"]) + " WHERE publication_id=%s", (publication["id"],)).fetchall()
                if len(rows) != definition["row_count"] or importer.logical_rows([row[column] for column in columns] for row in rows) != definition["logical_sha256"]:
                    raise ValueError("Restored logical rows differ: " + definition["name"])
            artifacts = connection.execute("SELECT * FROM research.artifacts WHERE publication_id=%s ORDER BY id", (publication["id"],)).fetchall()
            if not artifacts and publication["scope"].get("kind") != "relational_growth_fixture":
                raise ValueError("Restored publication is missing artifact metadata")
            for artifact in artifacts:
                verify_object(artifact)
            runs = connection.execute("SELECT r.id,r.definition,r.ruleset_version,r.state,r.result_count,r.champion_set_count,r.expires_at>now() AS retained,s.rules FROM research.query_runs r LEFT JOIN research.rulesets s ON s.publication_id=r.publication_id AND s.version=r.ruleset_version WHERE r.publication_id=%s", (publication["id"],)).fetchall()
            for run in runs:
                definition = run["definition"]
                if run["rules"] is None or definition.get("publication_id") != publication["id"] or definition.get("snapshot_id") != publication["snapshot_id"] or definition.get("ruleset_version") != run["ruleset_version"]:
                    raise ValueError("Restored query lost its original version context: " + run["id"])
                if run["state"] == "complete" and run["retained"]:
                    counts = connection.execute("SELECT count(*) AS configurations,count(DISTINCT champion_set_key) AS champion_sets FROM research.query_results WHERE run_id=%s", (run["id"],)).fetchone()
                    if counts["configurations"] != run["result_count"] or counts["champion_sets"] != run["champion_set_count"]:
                        raise ValueError("Restored complete query counts differ: " + run["id"])
                query_definitions += 1
            objects = connection.execute("SELECT a.* FROM research.query_artifacts a JOIN research.query_runs r ON r.id=a.run_id WHERE r.publication_id=%s", (publication["id"],)).fetchall()
            for item in objects:
                verify_object(item)
            verified_publications.append({"publication_id": publication["id"], "tables_and_views": len(definitions), "snapshot_id": publication["snapshot_id"]})
    return {"passed": True, "publications": verified_publications, "objects_verified": verified_objects,
            "query_definitions_verified": query_definitions, "restoration_performed_by_script": False,
            "scope": "Logical rows, compressed artifact bytes, frozen query identity, and retained complete-result counts; restoration, full result hashes, HTTP routes and recovery timings are separate operator steps."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--publication")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get("RESEARCH_RESTORE_DATABASE_URL")
    if not url:
        parser.error("Set RESEARCH_RESTORE_DATABASE_URL to the restored test database")
    result = verify(url, args.objects, args.publication)
    importer.write(args.output, result)
    print(json.dumps({"passed": True, "publications": len(result["publications"]), "objects_verified": result["objects_verified"], "query_definitions_verified": result["query_definitions_verified"]}))


if __name__ == "__main__":
    main()
