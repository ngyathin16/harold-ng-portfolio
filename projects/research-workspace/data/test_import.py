import gzip
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

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

    def test_interrupted_upload_has_no_final_object_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_bytes(b'{"complete":"payload"}')
            store = importer.LocalStorage(root / "objects")
            target = root / "objects/publication/data.json"
            def interrupted(reader, writer):
                writer.write(reader.read(5))
                writer.flush()
                self.assertFalse(target.exists())
                raise OSError("interrupted copy")
            with mock.patch.object(importer.shutil, "copyfileobj", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    store.put_verified("publication/data.json", source, importer.sha(source), "application/json")
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])
            store.put_verified("publication/data.json", source, importer.sha(source), "application/json")
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_wrong_upload_checksum_is_not_published_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_bytes(b'{"verified":true}')
            store = importer.LocalStorage(root / "objects")
            target = root / "objects/publication/data.json"
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                store.put_verified("publication/data.json", source, "0" * 64, "application/json")
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])
            store.put_verified("publication/data.json", source, importer.sha(source), "application/json")
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_concurrent_different_uploads_never_overwrite_immutable_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "first.json", root / "second.json"]
            for index, source in enumerate(sources):
                source.write_bytes(bytes([index]) * 10000)
            store = importer.LocalStorage(root / "objects")
            target = root / "objects/publication/data.json"
            ready = threading.Barrier(2)
            link = importer.os.link
            def publish(temporary, destination):
                ready.wait(timeout=5)
                return link(temporary, destination)
            with mock.patch.object(importer.os, "link", side_effect=publish), ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(store.put_verified, "publication/data.json", source,
                                          importer.sha(source), "application/json") for source in sources]
                outcomes = [future.exception(timeout=10) for future in futures]
            winners = [index for index, error in enumerate(outcomes) if error is None]
            self.assertEqual(len(winners), 1)
            self.assertIsInstance(outcomes[1 - winners[0]], ValueError)
            self.assertIn("Immutable", str(outcomes[1 - winners[0]]))
            self.assertEqual(target.read_bytes(), sources[winners[0]].read_bytes())
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_every_table_has_scoped_identity_and_rls(self):
        sql = schema.table_migration(schema.load())
        tables = [name for name, value in schema.load().items() if value["kind"] == "table"]
        self.assertEqual(sql.count("ENABLE ROW LEVEL SECURITY"), len(tables))
        self.assertEqual(sql.count('PRIMARY KEY ("publication_id","dataset_id","snapshot_id"'), len(tables))
        self.assertNotIn('FOREIGN KEY ("entity_id")', sql)

    def test_real_bundle_checksums_and_compression(self):
        root = Path(os.environ.get("RESEARCH_BUNDLE_ROOT", Path(__file__).resolve().parents[1] / ".local/bundle"))
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
