"""Apply the packaged plain-PostgreSQL schema atomically and exactly once."""
import hashlib
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "migrations/001_publications.sql",
    "migrations/002_source_tables.sql",
    "migrations/003_source_views.sql",
    "service/migrations/002_queries.sql",
    "migrations/005_service_roles.sql",
    "migrations/006_private_artifacts.sql",
    "migrations/007_serverless.sql",
    "migrations/008_managed_capacity.sql",
    "migrations/009_result_objects.sql",
    "migrations/010_budget.sql",
    "migrations/011_neon_capacity.sql",
    "migrations/012_launch_tokens.sql",
)
LOCK = "research-schema-migrations"
LOGIN_ROLES = {
    "api": ("research_api_login", "research_api", False),
    "worker": ("research_worker_login", "research_worker", False),
    "importer": ("research_importer_login", "service_role", True),
    "backup": ("research_backup_login", None, True),
}


def migration_body(text):
    lines = text.splitlines()
    meaningful = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("--")]
    if meaningful and lines[meaningful[0]].strip().upper() == "BEGIN;":
        lines[meaningful[0]] = ""
        if lines[meaningful[-1]].strip().upper() != "COMMIT;":
            raise ValueError("Migration transaction wrapper is incomplete")
        lines[meaningful[-1]] = ""
    return "\n".join(lines)


def apply(connection, passwords, root=ROOT, names=MIGRATIONS):
    files = [(name, (root / name).read_text(encoding="utf-8-sig").encode("utf-8")) for name in names]
    if set(passwords) != set(LOGIN_ROLES) or any(len(value) < 24 for value in passwords.values()):
        raise ValueError("Four independent service passwords of at least 24 characters are required")
    if len(set(passwords.values())) != 4:
        raise ValueError("Service passwords must differ")
    applied = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK,))
        connection.execute("SET LOCAL lock_timeout='30s'")
        connection.execute("CREATE TABLE IF NOT EXISTS public.research_schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        connection.execute("REVOKE ALL ON public.research_schema_migrations FROM PUBLIC")
        known = dict(connection.execute("SELECT name,sha256 FROM public.research_schema_migrations").fetchall())
        if set(known) - set(names):
            raise ValueError("Database has migrations missing from this release; rollback is incompatible")
        for name, content in files:
            if name in known and hashlib.sha256(content).hexdigest() != known[name]:
                raise ValueError("Previously applied migration changed: " + name)
        for role in ("anon", "authenticated", "service_role"):
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        for name, content in files:
            if name in known:
                continue
            connection.execute(migration_body(content.decode("utf-8-sig")))
            connection.execute("INSERT INTO public.research_schema_migrations(name,sha256) VALUES(%s,%s)", (name, hashlib.sha256(content).hexdigest()))
            applied.append(name)
        for key, (role, group, bypass) in LOGIN_ROLES.items():
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            connection.execute(sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION {} PASSWORD {}").format(
                sql.Identifier(role), sql.SQL("BYPASSRLS" if bypass else "NOBYPASSRLS"), sql.Literal(passwords[key])))
            if group:
                connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(role)))
        connection.execute("GRANT USAGE ON SCHEMA research,research_data TO research_backup_login")
        connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA research,research_data TO research_backup_login")
        connection.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA research,research_data TO research_backup_login")
        connection.execute("GRANT SELECT ON public.research_schema_migrations TO research_backup_login")
        connection.execute("GRANT research_budget_operator TO research_backup_login")
        connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA research TO service_role")
    return {"applied": applied, "already_applied": len(known)}


def main():
    import json
    from .runtime import database_url, secret
    passwords = {key: secret(key + "_password") for key in LOGIN_ROLES}
    with psycopg.connect(database_url("postgres", "postgres_password")) as connection:
        print(json.dumps(apply(connection, passwords)))


if __name__ == "__main__":
    main()
