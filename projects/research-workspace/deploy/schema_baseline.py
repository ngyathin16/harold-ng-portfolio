"""Recognize reviewed legacy schemas without guessing which migrations ran."""
import hashlib
import json
from pathlib import Path

from psycopg.rows import dict_row


QUERIES = {
    "relations": """SELECT n.nspname,r.relname,r.relkind,r.relrowsecurity,r.relforcerowsecurity,r.reloptions,
        CASE WHEN r.relkind IN ('v','m') THEN pg_get_viewdef(r.oid,true) END AS definition
        FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname IN ('research','research_data') AND r.relkind IN ('r','p','v','m','S','f') ORDER BY 1,2""",
    "columns": """SELECT n.nspname,r.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod) AS type,
        a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid) AS default_expression
        FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum
        WHERE n.nspname IN ('research','research_data') AND r.relkind IN ('r','v') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3""",
    "constraints": """SELECT n.nspname,r.relname,c.conname,c.contype,pg_get_constraintdef(c.oid,true) AS definition
        FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname IN ('research','research_data') ORDER BY 1,2,3""",
    "indexes": """SELECT n.nspname,r.relname,i.relname AS index_name,pg_get_indexdef(x.indexrelid) AS definition
        FROM pg_index x JOIN pg_class r ON r.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
        JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname IN ('research','research_data') ORDER BY 1,2,3""",
    "policies": """SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies
        WHERE schemaname IN ('research','research_data') ORDER BY 1,2,3""",
    "table_grants": """SELECT n.nspname,r.relname,CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,
        a.privilege_type,a.is_grantable FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
        CROSS JOIN LATERAL aclexplode(r.relacl) a LEFT JOIN pg_roles g ON g.oid=a.grantee
        WHERE n.nspname IN ('research','research_data') AND a.grantee<>r.relowner ORDER BY 1,2,3,4""",
    "schema_grants": """SELECT n.nspname,CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,
        a.privilege_type,a.is_grantable FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
        LEFT JOIN pg_roles g ON g.oid=a.grantee WHERE n.nspname IN ('research','research_data')
        AND a.grantee<>n.nspowner ORDER BY 1,2,3""",
    "functions": """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) AS arguments,
        p.prosecdef,p.provolatile,pg_get_functiondef(p.oid) AS definition FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('research','research_data') ORDER BY 1,2,3""",
    "sequences": """SELECT n.nspname,r.relname,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle
        FROM pg_sequence s JOIN pg_class r ON r.oid=s.seqrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname IN ('research','research_data') ORDER BY 1,2""",
    "triggers": """SELECT n.nspname,r.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true) AS definition
        FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        WHERE n.nspname IN ('research','research_data') AND NOT t.tgisinternal ORDER BY 1,2,3""",
    "types": """SELECT n.nspname,t.typname,t.typtype,t.typnotnull,t.typdefault,format_type(t.typbasetype,t.typtypmod) AS base,
        ARRAY(SELECT e.enumlabel FROM pg_enum e WHERE e.enumtypid=t.oid ORDER BY e.enumsortorder) AS labels
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname IN ('research','research_data') AND t.typtype IN ('d','e') ORDER BY 1,2""",
}


def catalog_fingerprint(connection):
    connection.execute("SET LOCAL search_path=pg_catalog")
    result = {}
    for name, query in QUERIES.items():
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(query).fetchall()
        if name == "constraints":
            redundant = {"nspname": "research", "relname": "datasets", "conname": "datasets_latest_publication_id_fkey",
                "contype": "f", "definition": "FOREIGN KEY (latest_publication_id) REFERENCES research.publications(id)"}
            rows = [row for row in rows if row != redundant]
        value = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
        result[name] = {"count": len(rows), "sha256": hashlib.sha256(value).hexdigest()}
    return result


def migration_hash(root, name):
    return hashlib.sha256((root / name).read_text(encoding="utf-8-sig").encode()).hexdigest()


def adopt(connection, root, baselines=None):
    from .migrate import LOCK
    baseline_path = Path(baselines) if baselines else Path(__file__).with_name("schema-baselines.json")
    records = json.loads(baseline_path.read_text())
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK,))
        if connection.execute("SELECT to_regclass('public.research_schema_migrations')").fetchone()[0]:
            raise ValueError("A migration ledger already exists; use ordinary migrate")
        major = connection.execute("SHOW server_version_num").fetchone()[0]
        if int(major) // 10000 != 17:
            raise ValueError("Legacy schema adoption is verified for PostgreSQL 17 only")
        actual = catalog_fingerprint(connection)
        matching = [record for record in records if record["catalog"] == actual]
        if len(matching) != 1:
            raise ValueError("Restored schema does not exactly match a reviewed legacy baseline")
        baseline = matching[0]
        for name, digest in baseline["migrations"].items():
            if migration_hash(root, name) != digest:
                raise ValueError("Packaged migration differs from the reviewed baseline")
        connection.execute("CREATE TABLE public.research_schema_migrations (name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
        connection.execute("REVOKE ALL ON public.research_schema_migrations FROM PUBLIC")
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO public.research_schema_migrations(name,sha256) VALUES(%s,%s)", list(baseline["migrations"].items()))
    return {"adopted_baseline": baseline["name"], "verified_migrations": len(baseline["migrations"])}
