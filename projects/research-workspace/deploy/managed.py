"""Managed PostgreSQL qualification and application-only permission setup.

Run qualification on the actual candidate endpoint; a local pass does not qualify
a provider. No command creates a provider project or selects a paid plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import migrate

SCHEMAS = ("research", "research_data")
ROLE_MAP = {"anon": "research_visitor", "authenticated": "research_authenticated", "service_role": "research_importer"}
LOGIN_GROUPS = {"api": "research_api", "worker": "research_worker", "dispatcher": "research_dispatcher", "importer": "research_importer", "backup": None}
CONNECTION_LIMITS = {"api": 8, "worker": 4, "dispatcher": 4, "importer": 4, "backup": 4}
APP_ROLES = tuple(ROLE_MAP.values()) + ("research_api", "research_worker", "research_dispatcher") + tuple("research_" + name + "_login" for name in LOGIN_GROUPS)


def require_tls(url, *, session=False):
    options = conninfo_to_dict(url)
    if options.get("sslmode") != "verify-full" or not options.get("host") or options.get("hostaddr"):
        raise ValueError("Managed database connections require a hostname and sslmode=verify-full")
    if session and ("-pooler." in options["host"] or os.environ.get("RESEARCH_OPERATOR_ENDPOINT_MODE") != "direct"):
        raise ValueError("Operator endpoints must explicitly declare direct session-affine connections")
    return options


def provider_baseline(connection):
    """Record exact non-application objects; never infer permission to delete them."""
    schemas = [row["nspname"] for row in connection.execute("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema' AND nspname<>ALL(%s) ORDER BY nspname", (list(SCHEMAS),))]
    relations = connection.execute("""SELECT n.nspname AS schema,c.relname AS name,c.relkind AS kind,
        pg_get_userbyid(c.relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relname NOT IN ('research_schema_migrations','research_schema_migrations_pkey') ORDER BY 1,2""", (schemas,)).fetchall()
    routines = connection.execute("""SELECT n.nspname AS schema,p.proname AS name,pg_get_function_identity_arguments(p.oid) AS arguments
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY(%s) ORDER BY 1,2,3""", (schemas,)).fetchall()
    extensions = connection.execute("SELECT extname,extversion FROM pg_extension ORDER BY extname").fetchall()
    roles = connection.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname !~ '^pg_' AND rolname<>ALL(%s) ORDER BY rolname", (list(APP_ROLES),)).fetchall()
    return {"schemas": schemas, "relations": relations, "routines": routines, "extensions": extensions, "roles": roles}


def managed_body(content):
    body = migrate.migration_body(content)
    for source, target in ROLE_MAP.items():
        body = re.sub(r"\b" + source + r"\b", target, body)
    # The VM migration removes an old Supabase policy. That provider-owned
    # namespace is deliberately outside a managed application deployment.
    body = re.sub(r"DO \$\$ BEGIN\s+IF to_regclass\('storage.objects'\).*?END \$\$;", "", body, flags=re.S)
    return body


def grant_backup_reader(connection):
    connection.execute("GRANT ALL ON ALL TABLES IN SCHEMA research,research_data TO research_importer")
    connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA research,research_data TO research_importer")
    connection.execute("GRANT USAGE ON SCHEMA research,research_data TO research_backup_login")
    connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA research,research_data TO research_backup_login")
    connection.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA research,research_data TO research_backup_login")
    connection.execute("GRANT SELECT ON public.research_schema_migrations TO research_backup_login")
    tables = connection.execute("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p') ORDER BY 1,2""", (list(SCHEMAS),)).fetchall()
    for table in tables:
        name = sql.Identifier(table["nspname"], table["relname"])
        connection.execute(sql.SQL("DROP POLICY IF EXISTS managed_backup_read ON {}").format(name))
        connection.execute(sql.SQL("CREATE POLICY managed_backup_read ON {} FOR SELECT TO research_backup_login USING(true)").format(name))
        connection.execute(sql.SQL("DROP POLICY IF EXISTS managed_import ON {}").format(name))
        connection.execute(sql.SQL("CREATE POLICY managed_import ON {} TO research_importer USING(true) WITH CHECK(true)").format(name))


def apply(connection, passwords, baseline):
    """Separate migration path: never run VM BYPASSRLS provisioning on a provider."""
    if set(passwords) != set(LOGIN_GROUPS) or len(set(passwords.values())) != len(passwords) or any(len(value) < 24 for value in passwords.values()):
        raise ValueError("Five distinct passwords of at least 24 characters are required")
    applied = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (migrate.LOCK,))
        if provider_baseline(connection) != baseline:
            raise ValueError("Provider baseline changed; review it before migration")
        ledger = connection.execute("SELECT to_regclass('public.research_schema_migrations') AS name").fetchone()["name"]
        if not ledger:
            occupied = connection.execute("SELECT 1 FROM pg_namespace WHERE nspname=ANY(%s)", (list(SCHEMAS),)).fetchone()
            roles = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(APP_ROLES),)).fetchone()
            if occupied or roles:
                raise ValueError("Initial managed migration requires an empty application namespace and unclaimed application roles")
        connection.execute("CREATE TABLE IF NOT EXISTS public.research_schema_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
        connection.execute("REVOKE ALL ON public.research_schema_migrations FROM PUBLIC")
        known = {row["name"]: row["sha256"] for row in connection.execute("SELECT name,sha256 FROM public.research_schema_migrations")}
        if set(known) - set(migrate.MIGRATIONS):
            raise ValueError("Unknown migrations make this release incompatible")
        for role in ROLE_MAP.values():
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
        for name in migrate.MIGRATIONS:
            source = (migrate.ROOT / name).read_text(encoding="utf-8-sig")
            body = managed_body(source)
            checksum = hashlib.sha256(body.encode()).hexdigest()
            if name in known:
                if known[name] != checksum:
                    raise ValueError("Managed migration checksum differs: " + name)
                continue
            connection.execute(body)
            connection.execute("INSERT INTO public.research_schema_migrations(name,sha256) VALUES(%s,%s)", (name, checksum))
            applied.append(name)
        for name, group in LOGIN_GROUPS.items():
            role = "research_" + name + "_login"
            existing = connection.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if existing is None:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(passwords[name])))
            elif not existing["rolcanlogin"]:
                # An isolated restore creates disabled logins; this explicit
                # secret-backed setup step makes them usable on a fresh cluster.
                connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(passwords[name])))
            # Repeated deploys leave existing credentials intact.
            connection.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(sql.Identifier(role), sql.Literal(CONNECTION_LIMITS[name])))
            if group:
                connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(role)))
        grant_backup_reader(connection)
        connection.execute("CREATE TABLE IF NOT EXISTS research.environment(identity text PRIMARY KEY)")
        connection.execute("INSERT INTO research.environment(identity) SELECT gen_random_uuid()::text WHERE NOT EXISTS(SELECT 1 FROM research.environment)")
        connection.execute("GRANT SELECT ON research.environment TO research_backup_login")
        if provider_baseline(connection) != baseline:
            raise ValueError("Migration unexpectedly changed provider objects")
    return {"applied": applied, "already_applied": len(known), "role_mapping": ROLE_MAP}


def audit(connection):
    version = int(connection.execute("SHOW server_version_num").fetchone()["server_version_num"])
    if not 170000 <= version < 180000:
        raise ValueError("Qualification requires PostgreSQL 17")
    connection.execute("SET LOCAL statement_timeout='30s'")
    tables = connection.execute("""SELECT n.nspname AS schema,c.relname AS name,c.relrowsecurity AS rls,
        pg_get_userbyid(c.relowner) AS owner,pg_table_size(c.oid) AS table_bytes,
        pg_indexes_size(c.oid) AS index_bytes,pg_total_relation_size(c.oid) AS total_bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','m','p') ORDER BY 1,2""", (list(SCHEMAS),)).fetchall()
    roles = connection.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname", (list(APP_ROLES),)).fetchall()
    violations = []
    for role in roles:
        if any(role[key] for key in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")):
            violations.append(role["rolname"] + ": administrative privilege")
        key = role["rolname"].removeprefix("research_").removesuffix("_login")
        if role["rolname"].endswith("_login") and key in CONNECTION_LIMITS and role["rolconnlimit"] != CONNECTION_LIMITS[key]:
            violations.append(role["rolname"] + ": connection budget differs")
    for name in ("research_api_login", "research_worker_login", "research_backup_login"):
        if not any(role["rolname"] == name for role in roles):
            violations.append(name + ": missing")
            continue
        for table in tables:
            if connection.execute("SELECT pg_has_role(%s,%s,'MEMBER') AS owns", (name, table["owner"])).fetchone()["owns"]:
                violations.append(name + ": inherits table owner")
            if name == "research_backup_login":
                relation = sql.Identifier(table["schema"], table["name"]).as_string(connection)
                if connection.execute("SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS writes", (name, relation)).fetchone()["writes"]:
                    violations.append(name + ": application write privilege")
        for schema in SCHEMAS:
            if connection.execute("SELECT has_schema_privilege(%s,%s,'CREATE') AS creates", (name, schema)).fetchone()["creates"]:
                violations.append(name + ": can create application objects")
        if connection.execute("SELECT pg_has_role(%s,datdba,'MEMBER') AS owns FROM pg_database WHERE datname=current_database()", (name,)).fetchone()["owns"]:
            violations.append(name + ": inherits database owner")
    categories = {key: 0 for key in ("source", "evidence", "saved_definitions", "result_cache", "other")}
    for table in tables:
        name = table["name"]
        category = "result_cache" if name == "query_results" else "saved_definitions" if name in ("query_runs", "query_subscriptions", "rulesets") else "evidence" if name in ("assertions", "observations", "sources", "evidence") else "source" if table["schema"] == "research_data" else "other"
        categories[category] += table["total_bytes"]
    return {"postgres_version_num": version, "tables": tables, "categories_bytes": categories,
            "database_allocated_bytes": connection.execute("SELECT pg_database_size(current_database()) AS bytes").fetchone()["bytes"],
            "provider_billed_storage_bytes": None, "provider_storage_note": "Obtain billed history, branches and storage from the exact provider plan separately.",
            "roles": roles, "permission_violations": violations, "provider_baseline": provider_baseline(connection),
            "qualified": False, "pending": ["clean import", "backup private-row comparison", "direct/pooled concurrency and exhaustion", "hosted latency and sleep", "provider quote", "R2 recovery drill"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "baseline", "migrate"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    url = os.environ["RESEARCH_OPERATOR_DATABASE_URL"]
    require_tls(url, session=True)
    started = time.monotonic()
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as connection:
        if args.command == "migrate":
            if args.baseline is None:
                parser.error("--baseline is required")
            result = apply(connection, {name: os.environ["RESEARCH_" + name.upper() + "_PASSWORD"] for name in LOGIN_GROUPS}, json.loads(args.baseline.read_text()))
        else:
            result = provider_baseline(connection) if args.command == "baseline" else audit(connection)
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({"command": args.command, "report_written": True, "seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"passed": False, "error_type": type(error).__name__}))
        raise SystemExit(1)
