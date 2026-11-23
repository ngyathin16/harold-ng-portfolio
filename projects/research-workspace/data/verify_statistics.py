"""Compare public statistics with read-only SQL and independently derived totals."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import time

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row


FIELDS = {
    "unit_stats": ("entity_id", "stat", "star_level", "value", "unit", "source_path"),
    "observation_samples": ("sample_id", "record_kind", "sample_count", "population_unit", "source_id",
                            "requested_window_days", "exact_window_start", "exact_window_end", "region", "client_build"),
    "observation_metrics": ("sample_id", "metric", "value", "unit", "numerator", "denominator", "formula", "source_id"),
    "observation_placements": ("sample_id", "placement", "board_count"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(row, fields):
    return json.dumps([row[field] for field in fields], separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def observation_checks(tables):
    samples = {row["sample_id"]: row for row in tables["observation_samples"]}
    placements = defaultdict(dict)
    for row in tables["observation_placements"]:
        placements[row["sample_id"]][row["placement"]] = row["board_count"]
    populations = {row["source_id"]: row["sample_count"] for row in samples.values() if row["record_kind"] == "population"}
    checked = 0
    for metric in tables["observation_metrics"]:
        sample = samples[metric["sample_id"]]
        counts = placements[metric["sample_id"]]
        denominator = sample["sample_count"]
        name = metric["metric"]
        if name == "population_share":
            numerator, denominator = sample["sample_count"], populations[sample["source_id"]]
        elif name == "average_placement":
            numerator = sum(place * count for place, count in counts.items())
        elif name == "win_rate":
            numerator = counts.get(1, 0)
        elif name == "top_four_rate":
            numerator = sum(count for place, count in counts.items() if 1 <= place <= 4)
        else:
            raise ValueError("Unsupported metric needs an independent verification rule: " + name)
        if name != "population_share":
            require(sum(counts.values()) == sample["sample_count"], "Placement counts differ from the declared sample")
        require(metric["numerator"] == numerator and metric["denominator"] == denominator,
                "Metric numerator or denominator differs from independent board counts")
        expected = numerator / denominator if denominator else None
        require(metric["value"] is None if expected is None else
                metric["value"] is not None and math.isclose(metric["value"], expected, rel_tol=1e-12, abs_tol=1e-12),
                "Metric value differs from independent board counts")
        checked += 1
    return {"metrics_recomputed": checked, "sample_kinds": dict(Counter(row["record_kind"] for row in samples.values()))}


def verify(database_url, api_url):
    started = time.perf_counter()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "platform": platform.platform(), "architecture": platform.machine(), "hosted": False,
              "publications": [], "queries": []}
    with psycopg.connect(database_url, row_factory=dict_row) as conn, httpx.Client(base_url=api_url, timeout=90) as client:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='180s'")
        report["postgres"] = conn.execute("SHOW server_version").fetchone()["server_version"]
        publications = conn.execute("SELECT id,snapshot_id FROM research.publications WHERE status='published' ORDER BY id").fetchall()
        require(bool(publications), "No published statistics were verified")

        def get(path, **params):
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

        for publication in publications:
            publication_id = publication["id"]
            base = "/v1/publications/" + publication_id
            public = get(base)
            require(public["snapshot_id"] == publication["snapshot_id"], "Public snapshot differs from database scope")
            tables, counts = {}, {}
            for name, fields in FIELDS.items():
                statement = sql.SQL("SELECT {} FROM research_data.{} WHERE publication_id=%s").format(
                    sql.SQL(",").join(map(sql.Identifier, fields)), sql.Identifier(name))
                expected = conn.execute(statement, (publication_id,)).fetchall()
                tables[name] = expected
                actual, page = [], 1
                while True:
                    result = get(base + "/tables/" + name, page=page, page_size=200)
                    require(result["total"] == len(expected), "Table count differs: " + name)
                    require(len(result["rows"]) <= 200, "Unbounded public table page: " + name)
                    actual.extend(result["rows"])
                    if len(actual) >= result["total"]:
                        break
                    require(bool(result["rows"]), "Pagination ended before its declared total")
                    page += 1
                require(Counter(canonical(row, fields) for row in actual) == Counter(canonical(row, fields) for row in expected),
                        "Public table lost or changed a scoped statistic: " + name)
                counts[name] = {"rows": len(expected), "pages": page}
            observation = observation_checks(tables)
            coverage = get(base + "/coverage")
            for name in ("coverage_entries", "field_gaps", "discrepancies", "source_documents"):
                total = conn.execute(sql.SQL("SELECT count(*) AS n FROM research_data.{} WHERE publication_id=%s").format(
                    sql.Identifier(name)), (publication_id,)).fetchone()["n"]
                require(coverage["issue_counts"][name] == total, "Coverage count differs: " + name)
            report["publications"].append({**publication, "tables": counts, **observation, "coverage_counts_match": True})

        runs = conn.execute("SELECT id,state,result_count,champion_set_count,publication_id FROM research.query_runs ORDER BY id").fetchall()
        for run in runs:
            run_id = run["id"]
            count = conn.execute("SELECT count(*) AS n,count(DISTINCT champion_set_key) AS champions FROM research.query_results WHERE run_id=%s", (run_id,)).fetchone()
            require(run["result_count"] == count["n"], "Stored configuration total differs from result rows")
            if run["state"] == "complete":
                require(run["champion_set_count"] == count["champions"], "Champion-set total differs from distinct result identities")
            invalid = conn.execute("""
                SELECT count(*) AS n FROM research.query_results q
                WHERE run_id=%s AND (
                    active_trait_count IS DISTINCT FROM jsonb_array_length(result->'traits') OR
                    active_trait_count IS DISTINCT FROM (result->>'active_trait_count')::integer OR
                    total_cost IS DISTINCT FROM (result->>'total_cost')::integer OR
                    (result->>'champion_count')::integer IS DISTINCT FROM
                        (SELECT count(DISTINCT member->>'champion_id') FROM jsonb_array_elements(result->'members') member) OR
                    (result->>'board_slots')::integer IS DISTINCT FROM
                        (SELECT sum((member->>'slots')::integer) FROM jsonb_array_elements(result->'members') member) OR
                    total_cost IS DISTINCT FROM
                        (SELECT CASE WHEN count(*)=count(member->>'cost') THEN sum((member->>'cost')::integer) END
                         FROM jsonb_array_elements(result->'members') member))
                """, (run_id,)).fetchone()["n"]
            require(invalid == 0, "Team totals differ from member and trait data")
            status = get("/v1/queries/" + run_id)
            require(status["result_count"] == count["n"] and status["state"] == run["state"], "Public query state/count differs")
            if run["state"] == "complete":
                require(status["champion_set_count"] == count["champions"], "Public champion-set total differs")
            if not status["expired"]:
                last_page = max(1, (count["n"] + 199) // 200)
                for page in sorted({1, last_page}):
                    result = get("/v1/queries/" + run_id + "/results", page=page, page_size=200)
                    require(result["total"] == count["n"], "Result page count is not the entire run count")
                    require(len(result["rows"]) == max(0, min(200, count["n"] - (page - 1) * 200)), "Result page is incomplete")
                    require(result["complete"] == (run["state"] == "complete"), "Partial results described as complete")
                    ordering = "active_trait_count DESC,configuration_key" if run["state"] == "complete" else "discovery_index,configuration_key"
                    expected_rows = conn.execute("SELECT result FROM research.query_results WHERE run_id=%s ORDER BY " + ordering + " LIMIT 200 OFFSET %s", (run_id, (page - 1) * 200)).fetchall()
                    require(result["rows"] == [row["result"] for row in expected_rows], "Public result page identities or values differ from SQL")
            report["queries"].append({"query_id": run_id, "configurations_recounted": count["n"],
                                      "champion_sets_recounted": count["champions"], "state": run["state"], "expired": status["expired"]})
    report.update(passed=True, elapsed_seconds=round(time.perf_counter() - started, 3),
                  scope="Read-only API/SQL parity, observation formulas, coverage, all stored team aggregate fields, first/last result pages. Solver semantics and browser presentation are checked separately.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    database_url = os.environ.get("RESEARCH_VERIFICATION_DATABASE_URL")
    if not database_url:
        parser.error("Set RESEARCH_VERIFICATION_DATABASE_URL to the database served by --api")
    report = verify(database_url, args.api)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "publications": len(report["publications"]),
                      "queries": len(report["queries"]), "elapsed_seconds": report["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
