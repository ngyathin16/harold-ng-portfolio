"""Versioned SQLite-to-Postgres mapping; identifiers never come from API input."""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
TYPES = {"TEXT": "text", "INTEGER": "bigint", "REAL": "double precision", "BLOB": "bytea"}
SCOPE = ("publication_id", "dataset_id", "snapshot_id")


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError("Unsupported SQL identifier")
    return '"' + value + '"'


def inspect(connection):
    result = {}
    for name, kind, sql in connection.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'catalog_search%' ORDER BY name"
    ):
        identifier(name)
        columns = [{"name": row[1], "type": row[2], "nullable": not bool(row[3] or row[5]),
                    "primary_key_order": row[5]} for row in connection.execute(f"PRAGMA table_info({identifier(name)})")]
        entry = {"kind": kind, "columns": columns,
                 "primary_key": [r["name"] for r in sorted(columns, key=lambda r: r["primary_key_order"]) if r["primary_key_order"]]}
        if kind == "table":
            entry["foreign_keys"] = [list(row) for row in connection.execute(f"PRAGMA foreign_key_list({identifier(name)})")]
        else:
            entry["sql"] = sql
        result[name] = entry
    return result


def fingerprint(schema):
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load():
    return json.loads((HERE / "schema-v1.json").read_text(encoding="utf-8"))


def check(connection):
    actual, expected = inspect(connection), load()
    if actual != expected:
        differences = sorted(name for name in set(actual) | set(expected) if actual.get(name) != expected.get(name))
        raise ValueError("Incompatible source schema; add a reviewed migration for: " + ", ".join(differences))
    return actual


def table_migration(schema):
    statements = ["-- Generated from data/schema-v1.json. Scope is part of every identity and relationship."]
    foreign_keys = []
    for name, table in schema.items():
        if table["kind"] != "table":
            continue
        columns = {column["name"]: column for column in table["columns"]}
        definitions = [identifier(scope) + " text NOT NULL" for scope in SCOPE if scope not in columns]
        for column in columns.values():
            definitions.append(identifier(column["name"]) + " " + TYPES[column["type"]] + ("" if column["nullable"] else " NOT NULL"))
        primary = list(dict.fromkeys([*SCOPE, *table["primary_key"]]))
        definitions.append("PRIMARY KEY (" + ",".join(map(identifier, primary)) + ")")
        definitions.append("FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE")
        statements.append(f"CREATE TABLE research_data.{identifier(name)} (\n  " + ",\n  ".join(definitions) + "\n);")
        groups = {}
        for row in table["foreign_keys"]:
            groups.setdefault(row[0], []).append(row)
        for rows in groups.values():
            rows.sort(key=lambda row: row[1])
            source = list(dict.fromkeys([*SCOPE, *(row[3] for row in rows)]))
            target = list(dict.fromkeys([*SCOPE, *(row[4] or schema[row[2]]["primary_key"][row[1]] for row in rows)]))
            foreign_keys.append(f"ALTER TABLE research_data.{identifier(name)} ADD FOREIGN KEY (" + ",".join(map(identifier, source)) +
                                f") REFERENCES research_data.{identifier(rows[0][2])} (" + ",".join(map(identifier, target)) + ") DEFERRABLE INITIALLY DEFERRED;")
        statements.extend([
            f"ALTER TABLE research_data.{identifier(name)} ENABLE ROW LEVEL SECURITY;",
            f"CREATE POLICY published_read ON research_data.{identifier(name)} FOR SELECT TO anon,authenticated USING "
            "(EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));",
        ])
    return "\n\n".join(statements + foreign_keys) + "\n"
