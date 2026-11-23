"""Exercise the packaged Linux stack in a fresh, disposable Compose project."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote
import uuid


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://localhost:8879"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def literal(value):
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fixture_sql(identity):
    schema = json.loads((ROOT / "data/schema-v1.json").read_text())
    snapshot = "synthetic-linux-v1"
    scope = [identity, identity, snapshot]
    entities, units, statistics = [], [], []
    for index in range(3):
        unit_id = "fixture-unit-" + str(index)
        entities.append({"entity_id": unit_id, "kind": "unit", "source_key": unit_id,
            "name": "Synthetic unit " + str(index), "description": "Deployment verification fixture",
            "description_raw": "Deployment verification fixture", "lifecycle_status": "active",
            "source_id": "synthetic", "source_path": "fixture/" + str(index), "raw_json": "{}",
            "variant_key": "base", "evidence_origin": "synthetic", "corroboration_status": "unverified"})
        units.append({"entity_id": unit_id, "cost": index + 1, "shop_unit": 1, "shop_tier": index + 1,
            "pool_count": None, "pool_cost": None, "role": None, "unit_kind": "champion", "locked": 0, "board_slots": 1})
        statistics.append({"entity_id": unit_id, "stat": "attack_damage", "star_level": 1,
            "value": [0.0, None, 50.0][index], "unit": "points", "source_path": "fixture/stats/" + str(index)})
    catalog = [{"id": row["entity_id"], "kind": row["kind"], "name": row["name"], "description": row["description"],
                "source_id": row["source_id"], "locator": row["source_path"]} for row in entities]
    rules = {"dataset_id": identity, "snapshot_id": snapshot, "publication_id": identity, "version": "fixture-rules-v1",
        "assumptions": ["Synthetic deployment test, not game observations"],
        "units": [{"form_id": row["entity_id"], "unit_id": row["entity_id"], "champion_id": row["entity_id"],
            "name": row["name"], "cost": index + 1, "slots": 1, "locked": False, "traits": ["fixture-trait"], "evidence": []}
            for index, row in enumerate(entities)],
        "traits": [{"trait_id": "fixture-trait", "name": "Synthetic trait", "breakpoints": [
            {"breakpoint_id": "two", "minimum": 2, "status": "supported", "evidence": []}]}]}
    statements = ["BEGIN;", "INSERT INTO research.datasets(id,name,description,adapter,capabilities) VALUES (" +
        ",".join(map(literal, [identity, "Synthetic Linux rehearsal", "Disposable deployment fixture", "tft_set19", encoded(["catalog", "tables", "team_queries"])])) + ");",
        "INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint,published_at) VALUES (" +
        ",".join(map(literal, [identity, identity, snapshot, "published", encoded({"snapshot_id": snapshot, "completeness": "synthetic_test_only", "channel": "synthetic"}),
            encoded({"kind": "synthetic_deployment_fixture", "description": "Synthetic fixture, not research evidence"}), "fixture", "fixture", "fixture"])) + ",now());"]
    for name, rows in (("entities", entities), ("units", units), ("unit_stats", statistics), ("research_catalog", catalog)):
        definition = schema[name]
        columns = [column["name"] for column in definition["columns"]]
        logical_rows = sorted(encoded([row[column] for column in columns]) for row in rows)
        digest = hashlib.sha256(("\n".join(logical_rows) + "\n").encode()).hexdigest()
        statements.append("INSERT INTO research.table_definitions(publication_id,name,kind,columns,primary_key,row_count,logical_sha256) VALUES (" +
            ",".join(map(literal, [identity, name, definition["kind"], encoded(definition["columns"]), encoded(definition["primary_key"]), len(rows), digest])) + ");")
        if definition["kind"] == "table":
            for row in rows:
                identifiers = ["publication_id", "dataset_id", "snapshot_id", *columns]
                statements.append("INSERT INTO research_data." + name + "(" + ",".join('"' + column + '"' for column in identifiers) + ") VALUES (" +
                    ",".join(map(literal, [*scope, *[row[column] for column in columns]])) + ");")
    statements += ["INSERT INTO research.rulesets(publication_id,version,rules) VALUES (" + ",".join(map(literal, [identity, rules["version"], encoded(rules)])) + ");",
        "UPDATE research.datasets SET latest_publication_id=" + literal(identity) + " WHERE id=" + literal(identity) + ";", "COMMIT;"]
    return "\n".join(statements), snapshot, rules["version"]


class Rehearsal:
    def __init__(self, directory, report, output):
        self.directory, self.report, self.output = directory, report, output
        self.config = directory / "config.env"
        self.project = "research-rehearsal-" + uuid.uuid4().hex[:12]
        self.command_count = 0

    def sanitized_tail(self, output):
        secrets = set()
        if self.config.exists():
            settings = dict(line.split("=", 1) for line in self.config.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
            secret_root = Path(settings.get("SECRET_ROOT", str(self.directory / "secrets")))
            require(secret_root.resolve().is_relative_to(self.directory.resolve()), "Rehearsal secret directory left its private work directory")
            if secret_root.exists():
                for path in secret_root.iterdir():
                    if not path.is_file() or path.is_symlink():
                        continue
                    raw = path.read_text().strip()
                    if raw:
                        secrets.add(raw)
                    if path.name == "r2_config":
                        try:
                            values = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(values, dict):
                            secrets.update(value for value in values.values() if isinstance(value, str) and value)
        for value in sorted(secrets, key=len, reverse=True):
            output = output.replace(value, "[REDACTED]").replace(quote(value, safe=""), "[REDACTED]")
        output = re.sub(r"(?i)(postgres(?:ql)?://)[^\s\"']+", r"\1[REDACTED]", output)
        output = re.sub(r"(?i)((?:password|secret|access_key|session_token)[a-z_]*\s*[=:]\s*)[^\s,}\n]+", r"\1[REDACTED]", output)
        return output.encode("utf-8")[-16384:].decode("utf-8", errors="ignore")

    def command(self, label, command, data=None, timeout=1800):
        self.command_count += 1
        began = time.monotonic()
        process = subprocess.run(command, input=data, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        log = self.directory / ("command-" + str(self.command_count) + ".log")
        log.write_text(process.stdout + "\n" + process.stderr, encoding="utf-8")
        os.chmod(log, 0o600)
        step = {"name": label, "passed": process.returncode == 0, "seconds": round(time.monotonic() - began, 3), "exit_code": process.returncode}
        if process.returncode:
            # Never publish inspect output: it can contain container environments.
            diagnostic = process.stderr if "inspect" in command else process.stdout + "\n" + process.stderr
            step["diagnostic_tail"] = self.sanitized_tail(diagnostic)
        self.report["steps"].append(step)
        save(self.output, self.report)
        require(process.returncode == 0, label + " failed; private command log " + log.name)
        return process.stdout.strip()

    def manage(self, action, *extra):
        return self.command("manage " + action, [sys.executable, str(ROOT / "deploy/manage.py"), "--config", str(self.config), "--local", action, *extra])

    def compose(self, label, *args, data=None):
        return self.command(label, ["docker", "compose", "--env-file", str(self.config), "--project-name", self.project,
            "-f", str(ROOT / "deploy/compose.yaml"), "-f", str(ROOT / "deploy/compose.local.yaml"), *args], data=data)

    def request(self, path, method="GET", body=None, expected=200):
        request = urllib.request.Request(BASE_URL + path, data=encoded(body).encode() if body is not None else None,
            method=method, headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, headers, raw = response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            status, headers, raw = error.code, error.headers, error.read()
        require(status == expected, method + " " + path + " returned " + str(status) + ", expected " + str(expected))
        if expected == 410:
            require(not headers.get("Location") and not headers.get("Content-Disposition"), "Retired route exposed file delivery headers")
            return None
        return json.loads(raw)

    def check(self, name, callback):
        began = time.monotonic()
        result = callback()
        self.report["steps"].append({"name": name, "passed": True, "seconds": round(time.monotonic() - began, 3)})
        save(self.output, self.report)
        return result

    def wait_query(self, identity):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = self.request("/v1/queries/" + identity)
            if state["state"] == "complete":
                return state
            require(state["state"] in ("queued", "running"), "Worker query ended in " + state["state"])
            time.sleep(2)
        raise TimeoutError("Worker did not complete the small fixture within 180 seconds")

    def inspect_query(self, identity, publication, snapshot, rules):
        state = self.request("/v1/queries/" + identity)
        require(state["state"] == "complete" and state["complete"] and state["result_count"] == 3 and state["champion_set_count"] == 3, "Saved query counts/completion changed")
        definition = state["query"] if "query" in state else state["definition"]
        require(definition["publication_id"] == publication and definition["snapshot_id"] == snapshot and definition["ruleset_version"] == rules, "Saved query provenance changed")
        first = self.request("/v1/queries/" + identity + "/results?page_size=2")
        second = self.request("/v1/queries/" + identity + "/results?page_size=2&page=2")
        rows = first["rows"] + second["rows"]
        require(first["total"] == 3 and len(first["rows"]) == 2 and len(second["rows"]) == 1, "Paginated counts changed")
        require(len({row["configuration_key"] for row in rows}) == 3, "Results repeat across pages")
        require(sorted(row["total_cost"] for row in rows) == [3, 4, 5] and all(row["active_trait_count"] == 1 for row in rows), "Derived fixture counts are incorrect")
        return {"query_id": identity, "result_count": 3, "champion_set_count": 3, "snapshot_id": snapshot, "ruleset_version": rules}

    def inspect_record(self, publication):
        record = self.request("/v1/publications/" + publication + "/records/fixture-unit-0")
        require(record["record"]["name"] == "Synthetic unit 0", "Saved record changed")
        rows = self.request("/v1/publications/" + publication + "/tables/unit_stats?sort=entity_id")["rows"]
        require([row["value"] for row in rows] == [0, None, 50], "Zero, unknown and numeric statistics changed")
        require(all(row["unit"] == "points" and row["star_level"] == 1 for row in rows), "Statistic units or coordinates changed")
        return {"record_id": "fixture-unit-0", "statistic_values": [0, None, 50]}

    def run(self):
        self.report["docker"] = json.loads(self.command("Docker versions", ["docker", "version", "--format", "{{json .}}"], timeout=60))
        self.report["compose_version"] = self.command("Compose version", ["docker", "compose", "version", "--short"], timeout=60)
        self.manage("init", "--state", str(self.directory / "state"), "--hostname", "http://localhost", "--origin", "http://localhost:8879")
        text = self.config.read_text().replace("COMPOSE_PROJECT_NAME=research-workspace", "COMPOSE_PROJECT_NAME=" + self.project)
        self.config.write_text(text)
        self.manage("check")
        self.manage("up")
        self.check("HTTP health through proxy", lambda: require(self.request("/health")["status"] == "ok", "API unhealthy"))
        ledger = self.compose("Capture migration ledger", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "research", "-At", "-v", "ON_ERROR_STOP=1", "-c", "SELECT name||':'||sha256 FROM public.research_schema_migrations ORDER BY name")
        self.manage("up")
        repeated = self.compose("Repeat migration ledger", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "research", "-At", "-v", "ON_ERROR_STOP=1", "-c", "SELECT name||':'||sha256 FROM public.research_schema_migrations ORDER BY name")
        require(ledger == repeated and ledger, "Repeated setup changed migration history")
        self.report["migration_ledger"] = ledger.splitlines()
        identity = "linux-fixture-" + uuid.uuid4().hex[:12]
        sql, snapshot, rules = fixture_sql(identity)
        self.compose("Seed explicit synthetic fixture", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "research", "-v", "ON_ERROR_STOP=1", data=sql)
        datasets = self.request("/v1/datasets")["datasets"]
        require(any(row["id"] == identity and row["latest_publication_id"] == identity for row in datasets), "Published snapshot not visible")
        self.report["fixture"] = {"publication_id": identity, "snapshot_id": snapshot, "synthetic": True, "source_import_verified": False}
        self.report["record_before_restart"] = self.check("Read record and statistics", lambda: self.inspect_record(identity))
        submitted = self.request("/v1/queries", method="POST", body={"publication_id": identity, "query": {"team_size": {"min": 2, "max": 2}, "min_active_traits": 1}}, expected=202)
        query = submitted["query_id"]
        self.check("Actual worker completes uncached fixture", lambda: self.wait_query(query))
        self.report["query_before_restart"] = self.check("Check complete counts and result pages", lambda: self.inspect_query(query, identity, snapshot, rules))
        paths = ["/v1/publications/" + identity + "/downloads", "/v1/publications/" + identity + "/downloads/database.sqlite.gz",
            "/v1/publications/" + identity + "/artifacts/private.dump", "/v1/queries/" + query + "/export?format=json", "/v1/queries/" + query + "/export?format=csv"]
        self.check("GET and HEAD public exports denied at proxy", lambda: [self.request(path, method=method, expected=410) for path in paths for method in ("GET", "HEAD")])
        self.manage("restart")
        self.report["record_after_restart"] = self.check("Record and statistics survive restart", lambda: self.inspect_record(identity))
        self.report["query_after_restart"] = self.check("Saved query and frozen rules survive restart", lambda: self.inspect_query(query, identity, snapshot, rules))
        ids = self.compose("Inspect running containers", "ps", "--all", "--quiet").splitlines()
        require(len(ids) == 4, "Expected only db, api, worker and proxy persistent services")
        containers = json.loads(self.command("Inspect port bindings and image identity", ["docker", "inspect", *ids], timeout=60))
        services = {}
        for container in containers:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            bindings = container["HostConfig"].get("PortBindings") or {}
            if service != "proxy":
                require(not bindings, service + " published an unexpected host port")
            else:
                require(set(bindings) == {"80/tcp"} and bindings["80/tcp"][0]["HostIp"] == "127.0.0.1" and bindings["80/tcp"][0]["HostPort"] == "8879", "Rehearsal proxy is not loopback-only")
            services[service] = {"image_id": container["Image"], "configured_image": container["Config"]["Image"],
                "running": container["State"]["Running"], "health": container["State"].get("Health", {}).get("Status"), "port_bindings": bindings}
        self.report["containers"] = services
        self.report["postgres_version"] = self.compose("PostgreSQL version", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "research", "-At", "-c", "SHOW server_version")
        self.report["container_runtime"] = json.loads(self.compose("Python runtime and architecture", "exec", "-T", "api", "python", "-c", "import json,platform; print(json.dumps({'python':platform.python_version(),'architecture':platform.machine()}))"))
        require(self.report["postgres_version"].split(".")[0] == "17", "Expected PostgreSQL 17")
        require(self.report["container_runtime"]["python"].startswith("3.12."), "Expected Python 3.12")
        self.report["passed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Sanitized report to retain even if verification fails")
    parser.add_argument("--work-root", type=Path, help="New, nonexistent disposable directory; default creates one under /tmp")
    parser.add_argument("--keep", action="store_true", help="Leave the isolated services running for inspection")
    args = parser.parse_args()
    os.umask(0o077)
    report = {"started_at": timestamp(), "passed": False, "steps": [], "host_architecture": platform.machine(),
        "scope": "Synthetic local Linux deployment and persistence; not a source import, R2 drill, hosted HTTPS or ARM performance test"}
    began = time.monotonic()
    rehearsal = None
    try:
        require(os.name == "posix" and os.geteuid() == 0, "Run on disposable Linux as root")
        # CI checks out as the runner user, then invokes this host setup as root.
        # Scope Git's ownership exception to this checkout and this process tree.
        config_index = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        os.environ["GIT_CONFIG_COUNT"] = str(config_index + 1)
        os.environ["GIT_CONFIG_KEY_" + str(config_index)] = "safe.directory"
        os.environ["GIT_CONFIG_VALUE_" + str(config_index)] = str(ROOT.parents[1])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8879))
        if args.work_root:
            directory = args.work_root.absolute()
            require(directory.is_absolute() and len(directory.parts) >= 3 and not directory.exists(), "--work-root must be a new dedicated directory")
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        else:
            directory = Path(tempfile.mkdtemp(prefix="research-linux-rehearsal-"))
        report["work_directory"] = str(directory)
        rehearsal = Rehearsal(directory, report, args.output.absolute())
        report["compose_project"] = rehearsal.project
        report["commit"] = rehearsal.command("Record source commit", ["git", "rev-parse", "HEAD"], timeout=60)
        report["source_dirty"] = bool(rehearsal.command("Record source changes", ["git", "status", "--porcelain", "--", str(ROOT)], timeout=60))
        rehearsal.run()
    except Exception as error:
        report["passed"] = False
        report["error_type"] = type(error).__name__
        report["error"] = str(error) if isinstance(error, RuntimeError) else "Verification failed; inspect private command logs"
    finally:
        report["finished_at"] = timestamp()
        report["duration_seconds"] = round(time.monotonic() - began, 3)
        report["services_kept"] = bool(args.keep)
        save(args.output, report)
        if rehearsal and rehearsal.config.exists() and not args.keep:
            try:
                rehearsal.compose("Stop only the disposable Compose project", "down", "--remove-orphans")
                report["cleanup_passed"] = True
            except Exception:
                report["cleanup_passed"] = False
                report["passed"] = False
            save(args.output, report)
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "steps": len(report["steps"]), "duration_seconds": report["duration_seconds"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
