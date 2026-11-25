"""Managed permission and operator failure cases; hosted qualification is separate."""
from contextlib import contextmanager
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from deploy import managed
from ops import backup, serverless
from ops.test_backup import Store


class OperatorBoundaryTests(unittest.TestCase):
    def test_managed_endpoint_refuses_unverified_tls_and_transaction_pooling(self):
        for value in ("postgresql://u:p@db.example/x", "postgresql://u:p@db.example/x?sslmode=require"):
            with self.assertRaises(ValueError):
                managed.require_tls(value)
        with patch.dict(os.environ, {"RESEARCH_OPERATOR_ENDPOINT_MODE": "direct"}):
            with self.assertRaises(ValueError):
                managed.require_tls("postgresql://u:p@ep-pooler.example/x?sslmode=verify-full", session=True)
            managed.require_tls("postgresql://u:p@db.example/x?sslmode=verify-full", session=True)

    def test_provider_role_mapping_does_not_touch_provider_storage(self):
        content = (managed.migrate.ROOT / "migrations/006_private_artifacts.sql").read_text()
        body = managed.managed_body(content)
        self.assertNotIn("storage.objects", body)
        self.assertIn("research_visitor", body)
        self.assertNotIn("BYPASSRLS", body)

    def test_object_stream_stops_before_writing_excess_bytes(self):
        store = Store()
        store.objects["oversize"] = b"x" * (2 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "object"
            with self.assertRaisesRegex(ValueError, "declared size"):
                backup.check_stream(store, "oversize", {"size": 5, "sha256": "0" * 64}, path)
            self.assertEqual(path.stat().st_size, 0)

    def test_namespace_boundaries_include_parent_prefixes(self):
        class Namespace:
            bucket = "private"
            def __init__(self, prefix):
                self.prefix = prefix
        for prefix in ("objects", "objects/restore"):
            with self.assertRaises(ValueError):
                serverless.separate_namespaces(Namespace("objects"), Namespace(prefix))
        serverless.separate_namespaces(Namespace("live/objects"), Namespace("drill/objects"))

    def test_import_refuses_staging_overflow_before_download(self):
        store = Store()
        store.objects["bundle/bundle.json"] = b'{"artifacts":[{"size":1000}]}'
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RESEARCH_IMPORT_BUNDLE_KEY": "bundle", "RESEARCH_IMPORT_RESTORED_BYTES": "100", "RESEARCH_IMPORT_VERIFY_BYTES": "200"}):
            path = Path(temporary) / "import"
            with self.assertRaisesRegex(ValueError, "before writing"):
                serverless.import_bundle(store, "unused", path, 500)
            self.assertFalse(path.exists())

    def test_bounded_logical_hash_preserves_order_and_unknown_values(self):
        from data.import_snapshot import logical_rows, logical_rows_bounded
        from decimal import Decimal
        for rows in ([], [[None, 0, False, "", "中", Decimal("1.25")], ["z", 0, None, "a", "x", 2]]):
            self.assertEqual(logical_rows(rows), logical_rows_bounded(reversed(rows), 1024 * 1024))

    def test_bounded_logical_hash_rejects_exhausted_scratch_space(self):
        from data.import_snapshot import logical_rows_bounded
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            logical_rows_bounded(([str(i) * 1000] for i in range(1000)), 16384)


@unittest.skipUnless(os.environ.get("RESEARCH_MANAGED_TEST_DATABASE_URL"), "Set an isolated migrated managed *_test database")
class ManagedDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["RESEARCH_MANAGED_TEST_DATABASE_URL"]
        self.connection = psycopg.connect(self.url, row_factory=dict_row)
        self.addCleanup(self.connection.close)
        database = self.connection.execute("SELECT current_database() AS name").fetchone()["name"]
        if not database.endswith("_test"):
            self.fail("Refusing a non-test database")

    def test_runtime_logins_are_not_owners_or_administrators(self):
        self.assertEqual(managed.audit(self.connection)["permission_violations"], [])

    def test_backup_reads_private_rows_and_cannot_write(self):
        conn = self.connection
        conn.execute("INSERT INTO research.datasets VALUES('managed-private-fixture','Fixture','', 'fixture','[]',NULL)")
        conn.execute("""INSERT INTO research.publications(id,dataset_id,snapshot_id,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint)
            VALUES('managed-private-fixture','managed-private-fixture','snapshot','{}','{}','source','bundle','schema')""")
        conn.execute("SET LOCAL ROLE research_backup_login")
        self.assertEqual(conn.execute("SELECT count(*) AS count FROM research.publications WHERE id='managed-private-fixture'").fetchone()["count"], 1)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE research.publications SET source_sha256='corrupt' WHERE id='managed-private-fixture'")
        conn.execute("RESET ROLE")
        conn.execute("SET LOCAL ROLE research_api_login")
        self.assertEqual(conn.execute("SELECT count(*) AS count FROM research.publications WHERE id='managed-private-fixture'").fetchone()["count"], 0)
        conn.rollback()

    def test_cross_connection_operator_exclusion_and_release(self):
        with backup.database_operation_lock(self.url):
            with self.assertRaises(backup.OperatorBusy):
                with backup.database_operation_lock(self.url):
                    self.fail("Concurrent operator admitted")
        with backup.database_operation_lock(self.url):
            pass
        dying = psycopg.connect(self.url, autocommit=True)
        dying.execute("SELECT pg_advisory_lock(hashtext(%s))", (backup.LOCK_NAME,))
        dying.close()
        with backup.database_operation_lock(self.url):
            pass

    def test_private_rows_change_full_logical_hash(self):
        conn = self.connection
        before = backup.table_checks(conn)
        conn.execute("INSERT INTO research.datasets VALUES('private-hash-fixture','Fixture','', 'fixture','[]',NULL)")
        after = backup.table_checks(conn)
        self.assertNotEqual(before, after)
        conn.rollback()

    def test_restore_refuses_live_database_by_namespace_even_with_alias(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            serverless.assert_managed_target(self.connection, {"database": "different-live-name"}, {})

    def test_restore_fences_cloud_bindings_preserving_queued_identity_and_budgets(self):
        conn = self.connection
        conn.execute("INSERT INTO research.datasets VALUES('restore-fence-fixture','Fixture','', 'fixture','[]',NULL)")
        conn.execute("""INSERT INTO research.publications(id,dataset_id,snapshot_id,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint)
            VALUES('restore-fence-fixture','restore-fence-fixture','snapshot','{}','{}','source','bundle','schema')""")
        conn.execute("INSERT INTO research.rulesets(publication_id,version,rules) VALUES('restore-fence-fixture','v1','{}')")
        for name, state, attempts in (("running", "running", 1), ("queued", "queued", 0), ("exhausted", "queued", 3), ("complete", "complete", 1)):
            identity = "restore-fence-" + name
            conn.execute("""INSERT INTO research.query_runs(id,publication_id,ruleset_version,solver_version,query_hash,definition,state,attempts,dispatch_generation,lease_owner)
                VALUES(%s,'restore-fence-fixture','v1','v1',%s,'{}',%s,%s,1,'old-worker')""", (identity, identity, state, attempts))
            conn.execute("INSERT INTO research.dispatch_outbox(run_id,generation,state,launch_attempts) VALUES(%s,1,'reserved',%s)", (identity, attempts))
            conn.execute("""INSERT INTO research.execution_reservations(id,run_id,generation,state,operation_name,execution_name)
                VALUES(%s,%s,1,'running','live-operation','live-execution')""", (identity, identity))
        serverless.fence_restored_work(conn)
        rows = {row['id'].removeprefix('restore-fence-'): row for row in conn.execute("SELECT * FROM research.query_runs WHERE publication_id='restore-fence-fixture'")}
        self.assertEqual((rows['queued']['state'], rows['queued']['dispatch_generation'], rows['queued']['attempts']), ('queued', 2, 0))
        self.assertEqual((rows['running']['state'], rows['running']['attempts'], rows['running']['lease_owner']), ('failed', 1, None))
        self.assertEqual(rows['exhausted']['state'], 'failed')
        self.assertEqual((rows['complete']['state'], rows['complete']['dispatch_generation']), ('complete', 1))
        self.assertTrue(conn.execute("SELECT paused FROM research.dispatch_control").fetchone()['paused'])
        self.assertEqual(conn.execute("SELECT count(*) AS n FROM research.execution_reservations WHERE id LIKE 'restore-fence-%' AND state='terminal' AND operation_name IS NULL AND execution_name IS NULL").fetchone()['n'], 4)
        self.assertEqual(conn.execute("SELECT state FROM research.dispatch_outbox WHERE run_id='restore-fence-queued'").fetchone()['state'], 'pending')
        conn.rollback()


if __name__ == "__main__":
    unittest.main()
