import gzip
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import import_snapshot as importer
import schema


class ImportTests(unittest.TestCase):
    def test_paths_cannot_escape_artifact_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                importer.child(Path(directory), "../private.env")

    def test_logical_hash_preserves_values_and_ignores_row_order(self):
        rows = [[None, 0, "", "0", 'false', 1.25], ["中文", 2, "\\N", "x", "true", None]]
        self.assertEqual(importer.logical_rows(rows), importer.logical_rows(reversed(rows)))
        changed = [list(row) for row in rows]
        changed[0][0] = ""
        self.assertNotEqual(importer.logical_rows(rows), importer.logical_rows(changed))

    def test_unknown_schema_is_rejected(self):
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE unexpected(id TEXT PRIMARY KEY)")
            with self.assertRaisesRegex(ValueError, "Incompatible source schema"):
                schema.check(connection)

    def test_immutable_local_storage_checks_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text('{"a":1}', encoding="utf-8")
            store = importer.LocalStorage(root / "objects")
            store.put_verified("publication/data.json", source, importer.sha(source), "application/json")
            store.put_verified("publication/data.json", source, importer.sha(source), "application/json")
            source.write_text('{"a":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Immutable"):
                store.put_verified("publication/data.json", source, importer.sha(source), "application/json")

    def test_every_table_has_scoped_identity_and_rls(self):
        sql = schema.table_migration(schema.load())
        tables = [name for name, value in schema.load().items() if value["kind"] == "table"]
        self.assertEqual(sql.count("ENABLE ROW LEVEL SECURITY"), len(tables))
        self.assertEqual(sql.count('PRIMARY KEY ("publication_id","dataset_id","snapshot_id"'), len(tables))
        self.assertNotIn('FOREIGN KEY ("entity_id")', sql)

    def test_real_bundle_checksums_and_compression(self):
        root = Path(__file__).resolve().parents[1] / ".local/bundle"
        if not root.exists():
            self.skipTest("Prepare the real snapshot bundle first")
        bundle = importer.verify_bundle(root)
        with importer.restored_database(root, bundle) as database, closing(sqlite3.connect(database)) as connection:
            actual = schema.check(connection)
            for name, definition in bundle["tables"].items():
                self.assertEqual(importer.logical_rows(importer.sqlite_rows(connection, name, definition)), definition["logical_sha256"], name)
            self.assertEqual(len(actual), len(bundle["tables"]))


if __name__ == "__main__":
    unittest.main()
