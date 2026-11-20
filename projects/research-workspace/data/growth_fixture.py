"""Plan or run tenfold relational growth in an empty, dedicated test database."""
import argparse
import copy
import json
import os
from pathlib import Path
import time
import uuid

import import_snapshot as importer
import schema


def run(bundle_root, database_url, output, factor):
    import psycopg
    from psycopg.types.json import Jsonb
    app_root = Path(__file__).resolve().parents[1]
    bundle = importer.verify_bundle(bundle_root)
    with psycopg.connect(database_url, autocommit=True) as conn:
        if not conn.info.dbname.endswith(("_growth", "_test")):
            raise ValueError("Growth execution requires a dedicated database ending in _growth or _test")
        if conn.execute("SELECT to_regnamespace('research')").fetchone()[0] is not None:
            raise ValueError("Growth target must have no research schema; use a fresh dedicated database")
        for file in ["migrations/001_publications.sql", "migrations/002_source_tables.sql", "migrations/003_source_views.sql",
                     "service/migrations/002_queries.sql", "migrations/005_service_roles.sql"]:
            conn.execute((app_root / file).read_text(encoding="utf-8"))
    started = time.monotonic()
    source = importer.publish(bundle_root, database_url, importer.LocalStorage(output / "objects"))["publication_id"]
    scopes = [source]
    with psycopg.connect(database_url) as conn:
        conn.execute("SET LOCAL statement_timeout='180s'")
        for index in range(1, factor):
            fixture_id = "growth-" + str(index) + "-" + uuid.uuid4().hex[:12]
            snapshot = bundle["snapshot_id"] + "--" + fixture_id
            publication = bundle["dataset"]["id"] + "--" + snapshot
            scopes.append(publication)
            manifest = {**copy.deepcopy(bundle["manifest"]), "snapshot_id": snapshot,
                        "fixture_only": True, "source_snapshot_id": bundle["snapshot_id"]}
            scope = {"kind": "relational_growth_fixture", "description": "Synthetic identity scope for isolated read-volume testing; source facts copied unchanged. Downloads omitted.", "source_publication_id": source}
            conn.execute("INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint,published_at) VALUES(%s,%s,%s,'published',%s,%s,%s,%s,%s,now())",
                         (publication, bundle["dataset"]["id"], snapshot, Jsonb(manifest), Jsonb(scope), bundle["source_sha256"], fixture_id, bundle["schema_fingerprint"]))
            for name, definition in bundle["tables"].items():
                if definition["kind"] != "table":
                    continue
                columns = [c["name"] for c in definition["columns"] if c["name"] != "snapshot_id"]
                selected = ",".join(map(schema.identifier, columns))
                conn.execute(f"INSERT INTO research_data.{schema.identifier(name)} (publication_id,dataset_id,snapshot_id,{selected}) SELECT %s,dataset_id,%s,{selected} FROM research_data.{schema.identifier(name)} WHERE publication_id=%s", (publication, snapshot, source))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            conn.execute("SET CONSTRAINTS ALL DEFERRED")
            for name, definition in bundle["tables"].items():
                columns = ",".join(schema.identifier(c["name"]) for c in definition["columns"])
                rows = conn.execute(f"SELECT {columns} FROM research_data.{schema.identifier(name)} WHERE publication_id=%s", (publication,)).fetchall()
                checksum = importer.logical_rows(rows)
                if len(rows) != definition["row_count"] or (name != "snapshots" and checksum != definition["logical_sha256"]):
                    raise ValueError("Growth fixture parity failed: " + name)
                conn.execute("INSERT INTO research.table_definitions(publication_id,name,kind,columns,primary_key,row_count,logical_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                             (publication, name, definition["kind"], Jsonb(definition["columns"]), Jsonb(definition["primary_key"]), len(rows), checksum))
    load_seconds = time.monotonic() - started
    counts, plans = {}, {}
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("ANALYZE")
        for name, definition in bundle["tables"].items():
            if definition["kind"] == "table":
                actual = conn.execute(f"SELECT count(*) FROM research_data.{schema.identifier(name)}").fetchone()[0]
                expected = definition["row_count"] * factor
                if actual != expected:
                    raise ValueError("Growth count mismatch: " + name)
                counts[name] = {"expected": expected, "actual": actual}
        samples = {
            "catalog_name": ("SELECT id,name FROM research_data.research_catalog WHERE publication_id=%s AND name ILIKE %s ORDER BY name,id LIMIT 50", (source, "%Ahri%")),
            "units_cost": ("SELECT entity_id,name,cost FROM research_data.v_units WHERE publication_id=%s AND cost<=4 AND shop_unit=1 ORDER BY cost,entity_id LIMIT 50", (source,)),
            "evidence_page": ("SELECT * FROM research_data.v_evidence WHERE publication_id=%s AND entity_id=%s ORDER BY field_path,source_id,source_path LIMIT 50", (source, "tft19:unit:DA_Jhin19")),
            "large_table_page": ("SELECT assertion_id,entity_id,field_path FROM research_data.assertions WHERE publication_id=%s ORDER BY assertion_id LIMIT 200 OFFSET 1000", (source,)),
        }
        for label, (query, parameters) in samples.items():
            plans[label] = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query, parameters).fetchone()[0]
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        relation_sizes = conn.execute("SELECT schemaname,relname,pg_total_relation_size(relid) FROM pg_catalog.pg_statio_user_tables WHERE schemaname IN ('research','research_data') ORDER BY pg_total_relation_size(relid) DESC").fetchall()
    report = {"passed": True, "factor": factor, "publication_scopes": scopes, "counts": counts,
              "load_seconds": load_seconds, "database_bytes": size, "relation_bytes": relation_sizes,
              "plans": plans, "limitations": ["Isolated relational-volume fixture; synthetic scopes omit duplicate downloads and query workloads.",
              "Single EXPLAIN ANALYZE per read, after ANALYZE; not a cold-cache, concurrent or hosted latency measurement.",
              "No production publication or existing database is modified; dedicated test database is retained for inspection."]}
    importer.write(output / "growth-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parents[1] / ".local/bundle")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".local/growth")
    parser.add_argument("--factor", type=int, default=10)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.factor < 2 or args.factor > 20:
        parser.error("--factor must be between 2 and 20")
    bundle = importer.verify_bundle(args.bundle)
    source_rows = sum(d["row_count"] for d in bundle["tables"].values() if d["kind"] == "table")
    plan = {"mode": "plan", "source_snapshot": bundle["snapshot_id"], "factor": args.factor,
            "source_rows": source_rows, "expected_source_table_rows": source_rows * args.factor,
            "required_database_env": "RESEARCH_GROWTH_DATABASE_URL", "requires_empty_database_suffix": "_growth or _test"}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    database_url = os.environ.get("RESEARCH_GROWTH_DATABASE_URL")
    if not database_url:
        parser.error("Set RESEARCH_GROWTH_DATABASE_URL to an empty dedicated database before --execute")
    result = run(args.bundle, database_url, args.output, args.factor)
    print(json.dumps({"passed": result["passed"], "factor": result["factor"], "report": str(args.output / "growth-report.json")}))


if __name__ == "__main__":
    main()
