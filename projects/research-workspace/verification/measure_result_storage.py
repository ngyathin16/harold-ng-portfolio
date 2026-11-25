"""Measure lossless result compression in a disposable complete database clone.

The source is read-only. Create the target with CREATE DATABASE ... TEMPLATE ...
before running; the target name must end in _storage_test. The target's result
table is replaced only after full row-hash and index-shape verification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import zlib

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


FIELDS = (
    "run_id", "configuration_key", "champion_set_key", "active_trait_count",
    "total_cost", "champion_names", "discovery_index", "result",
)
ORDER = " ORDER BY run_id,configuration_key"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def relation_size(connection, relation):
    row = connection.execute("""SELECT pg_table_size(%s::regclass),
        pg_indexes_size(%s::regclass),pg_total_relation_size(%s::regclass)""",
        (relation, relation, relation)).fetchone()
    return dict(zip(("heap_toast_bytes", "index_bytes", "total_bytes"), row))


def index_shapes(connection, relation):
    return connection.execute("""SELECT i.indisunique,i.indisprimary,
        ARRAY(SELECT pg_get_indexdef(i.indexrelid,k,false)
          FROM generate_series(1,i.indnkeyatts) k),
        i.indoption::text,i.indclass::text,i.indcollation::text,
        pg_get_expr(i.indpred,i.indrelid)
        FROM pg_index i WHERE i.indrelid=%s::regclass ORDER BY 1,2,3""",
        (relation,)).fetchall()


def identity(connection):
    return connection.execute(
        "SELECT current_database(),system_identifier FROM pg_control_system()"
    ).fetchone()


def hash_rows(connection, relation, *, compressed=False):
    checksum, count = hashlib.sha256(), 0
    with connection.cursor(name="storage_hash") as cursor:
        cursor.itersize = 1000
        cursor.execute(sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(",").join(map(sql.Identifier, FIELDS)),
            sql.Identifier("research", relation)) + sql.SQL(ORDER))
        for row in cursor:
            if compressed:
                row = (*row[:-1], json.loads(zlib.decompress(row[-1])))
            checksum.update(canonical(row) + b"\n")
            count += 1
    return {"rows": count, "canonical_row_sha256": checksum.hexdigest()}


def check_pages(source, target, relation):
    """Compare actual result payloads at first, middle and last API pages."""
    orders = (
        "active_trait_count DESC,configuration_key",
        "total_cost NULLS LAST,configuration_key",
        "champion_names,configuration_key",
        "discovery_index,configuration_key",
    )
    checks, rows_checked = 0, 0
    for run_id, count in source.execute(
            "SELECT run_id,count(*) FROM research.query_results GROUP BY run_id ORDER BY run_id"):
        for order in orders:
            for offset in sorted({0, (count // 400) * 200, max(0, count - 200)}):
                expected = source.execute(
                    "SELECT result FROM research.query_results WHERE run_id=%s ORDER BY "
                    + order + " LIMIT 200 OFFSET %s", (run_id, offset)).fetchall()
                query = sql.SQL("SELECT result FROM {} WHERE run_id=%s ORDER BY ").format(
                    sql.Identifier("research", relation)) + sql.SQL(order + " LIMIT 200 OFFSET %s")
                actual = target.execute(query, (run_id, offset)).fetchall()
                decoded = [(json.loads(zlib.decompress(row[0])),) for row in actual]
                if canonical(decoded) != canonical(expected):
                    raise ValueError("Sorted/paginated response payload differs")
                checks += 1
                rows_checked += len(actual)
    return {"page_cases": checks, "payload_rows_compared": rows_checked, "page_size": 200}


def measure(source_url, target_url, output):
    for value in (source_url, target_url):
        endpoint = conninfo_to_dict(value)
        if endpoint.get('host') not in ('127.0.0.1', 'localhost', '::1') or endpoint.get('hostaddr'):
            raise ValueError('This measurement accepts local loopback databases only')
    began = time.monotonic()
    with psycopg.connect(source_url, connect_timeout=10) as source, psycopg.connect(target_url, connect_timeout=10) as target:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source_id, target_id = identity(source), identity(target)
        if source_id == target_id or not target_id[0].endswith("_storage_test"):
            raise ValueError("Use a distinct disposable *_storage_test clone")
        if target.execute("""SELECT count(*) FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid()""").fetchone()[0]:
            raise ValueError("Disposable target has other clients")
        if target.execute("SELECT to_regclass('research.query_results_compact')").fetchone()[0]:
            raise ValueError("Use a new clone; existing experiment is not overwritten")
        report = {
            "observed_on": datetime.now(timezone.utc).date().isoformat(),
            "environment": platform.platform(),
            "postgres_version": source.execute("SHOW server_version").fetchone()[0],
            "python_version": platform.python_version(),
            "zlib_version": zlib.ZLIB_VERSION,
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "codec": "canonical UTF-8 JSON + zlib level 6, independently per row",
            "source_database_modified": False,
            "provider_billed_storage_bytes": None,
            "scope": "Complete clean-import clone: one retained publication and both benchmark query results",
            "source_database_bytes": source.execute(
                "SELECT pg_database_size(current_database())").fetchone()[0],
            "original_results": relation_size(source, "research.query_results"),
            "publication_ids": [row[0] for row in source.execute(
                "SELECT id FROM research.publications ORDER BY id")],
            "queries": [dict(zip(("query_id", "state", "configurations", "champion_sets"), row))
                for row in source.execute("""SELECT id,state,result_count,champion_set_count
                    FROM research.query_runs ORDER BY id""")],
        }
        source_hash = hash_rows(source, "query_results")
        clone_hash = hash_rows(target, "query_results")
        if source_hash != clone_hash:
            raise ValueError("Disposable clone does not match the source results")
        report["source_rows"] = source_hash
        report["clone_rows_before"] = clone_hash
        shapes = index_shapes(source, "research.query_results")
        target.execute("CREATE TABLE research.query_results_compact (LIKE research.query_results INCLUDING ALL)")
        target.execute("ALTER TABLE research.query_results_compact ALTER COLUMN result TYPE bytea USING NULL::bytea")
        # Application-compressed values should not be compressed a second time.
        target.execute("ALTER TABLE research.query_results_compact ALTER COLUMN result SET STORAGE EXTERNAL")
        started = time.monotonic()
        canonical_bytes, compressed_bytes = 0, 0
        with source.cursor(name="storage_copy") as rows, target.cursor() as writer:
            rows.itersize = 1000
            rows.execute("SELECT " + ",".join(FIELDS) + " FROM research.query_results" + ORDER)
            with writer.copy("COPY research.query_results_compact (" + ",".join(FIELDS) + ") FROM STDIN") as copy:
                for row in rows:
                    payload = canonical(row[-1])
                    encoded = zlib.compress(payload, 6)
                    canonical_bytes += len(payload)
                    compressed_bytes += len(encoded)
                    copy.write_row((*row[:-1], encoded))
        target.commit()
        report["encode_copy_seconds"] = round(time.monotonic() - started, 3)
        report["canonical_result_payload_bytes"] = canonical_bytes
        report["compressed_result_payload_bytes"] = compressed_bytes
        report["compact_results"] = relation_size(target, "research.query_results_compact")
        report["migration_high_water_database_bytes"] = target.execute(
            "SELECT pg_database_size(current_database())").fetchone()[0]
        if index_shapes(target, "research.query_results_compact") != shapes:
            raise ValueError("Sorting/primary index shapes differ")
        report["same_index_shapes"] = True
        report["index_shapes"] = shapes
        started = time.monotonic()
        compact_hash = hash_rows(target, "query_results_compact", compressed=True)
        report["decode_hash_seconds"] = round(time.monotonic() - started, 3)
        if compact_hash != source_hash:
            raise ValueError("Compressed rows differ from the complete source")
        report["compact_rows"] = compact_hash
        report["sorted_pagination"] = check_pages(source, target, "query_results_compact")
        target.execute("DROP TABLE research.query_results")
        target.execute("ALTER TABLE research.query_results_compact RENAME TO query_results")
        target.commit()
        report["replacement_database_bytes"] = target.execute(
            "SELECT pg_database_size(current_database())").fetchone()[0]
        report["database_bytes_saved"] = report["source_database_bytes"] - report["replacement_database_bytes"]
        report["remaining_to_decimal_1gb_bytes"] = 1_000_000_000 - report["replacement_database_bytes"]
        report["all_result_rows_match"] = True
        report["production_fit_established"] = False
        report["limitations"] = [
            "This experiment does not change production schema, worker, API or backups.",
            "The disposable replacement copies columns and indexes, not foreign keys, RLS or grants; it is not a deployable schema.",
            "The clone contains one publication and two queries, not every historical publication/result.",
            "Stored-row and sampled pagination parity do not prove deployed API/restore compatibility.",
            "Logical/provider-billed storage, WAL/history, new-publication growth and temporary work are not measured.",
            "Migration high-water storage requires room for both representations.",
            "Default 2 GiB per-search reservation and 1 GiB headroom still cannot fit a 1 GB free plan.",
            "The full solver-write and read-latency targets need separate tests before adopting this codec.",
            "Compute, transfer, backup and all-provider monthly cost remain unqualified.",
        ]
        report["total_seconds"] = round(time.monotonic() - began, 3)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_database_bytes", "replacement_database_bytes",
        "remaining_to_decimal_1gb_bytes", "all_result_rows_match", "total_seconds")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measure(os.environ["RESEARCH_STORAGE_SOURCE_URL"], os.environ["RESEARCH_STORAGE_TARGET_URL"], args.output)


if __name__ == "__main__":
    main()
