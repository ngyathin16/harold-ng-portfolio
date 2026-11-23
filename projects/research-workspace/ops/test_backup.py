"""Failure and retention behavior for private recovery sets."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops import backup


class Store:
    def __init__(self):
        self.objects = {}
        self.fail_put = None
        self.deleted = []

    def put(self, name, path):
        if name == self.fail_put:
            raise OSError("injected transport failure")
        if name in self.objects:
            raise FileExistsError("immutable object exists")
        self.objects[name] = Path(path).read_bytes()

    def read(self, name):
        return io.BytesIO(self.objects[name])

    def keys(self):
        return iter(self.objects)

    def delete(self, name):
        self.deleted.append(name)
        self.objects.pop(name, None)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store()

    def publish(self, identity="set1", age_days=0):
        directory = self.root / identity
        directory.mkdir()
        (directory / "database.dump").write_bytes(b"test dump")
        backup.write_json(directory / "roles.json", {"roles": [], "memberships": []})
        members = [{"path": path.name, "size": path.stat().st_size, "sha256": backup.digest_file(path)} for path in directory.iterdir()]
        stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
        manifest = {"format_version": 1, "backup_id": identity, "database": "research", "members": members, "snapshot_at": stamp}
        with patch.object(backup, "now", return_value=stamp):
            return backup.publish_set(self.store, directory, manifest)

    def test_verified_round_trip_uses_new_directory(self):
        expected = self.publish()
        restored = self.root / "retrieved"
        actual = backup.verify_set(self.store, "set1", restored)
        self.assertEqual(expected, actual)
        self.assertEqual((restored / "database.dump").read_bytes(), b"test dump")
        with self.assertRaises(FileExistsError):
            backup.verify_set(self.store, "set1", restored)

    def test_interrupted_upload_cannot_be_listed_complete(self):
        self.publish("previous")
        self.store.fail_put = "broken/roles.json"
        with self.assertRaises(OSError):
            self.publish("broken")
        self.assertEqual([item["backup_id"] for item in backup.completed(self.store)], ["previous"])
        backup.verify_set(self.store, "previous")

    def test_missing_member_fails_verification(self):
        self.publish()
        del self.store.objects["set1/database.dump"]
        with self.assertRaises(KeyError):
            backup.verify_set(self.store, "set1")

    def test_corrupt_member_fails_even_when_length_matches(self):
        self.publish()
        self.store.objects["set1/database.dump"] = b"evil dump"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            backup.verify_set(self.store, "set1")

    def test_corrupt_manifest_rejected(self):
        self.publish()
        self.store.objects["set1/manifest.json"] += b" "
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            backup.manifest_for(self.store, "set1")

    def test_completed_member_cannot_be_overwritten(self):
        self.publish()
        with self.assertRaises(FileExistsError):
            self.store.put("set1/database.dump", self.root / "set1/database.dump")

    def test_path_traversal_and_windows_paths_rejected(self):
        for name in ("../x", "/x", "a/../x", "C:/x", "a\\x", "a//x", "./x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                backup.child(self.root, name)

    def test_backup_id_cannot_address_other_namespace(self):
        for value in ("../live", "a/b", "", "a\\b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                backup.backup_id(value)

    def test_retention_dry_run_and_execute_preserve_verified_newest(self):
        self.publish("old", 10)
        self.publish("new")
        plan = backup.prune(self.store, daily=1, weekly=0)
        self.assertEqual(plan["delete"], ["old"])
        self.assertFalse(self.store.deleted)
        backup.prune(self.store, execute=True, daily=1, weekly=0)
        self.assertEqual(self.store.deleted[0], "old/complete.json")
        self.assertTrue(all(name.startswith("old/") for name in self.store.deleted))
        backup.verify_set(self.store, "new")

    def test_corrupt_newest_blocks_all_retention_deletions(self):
        self.publish("old", 10)
        self.publish("new")
        self.store.objects["new/database.dump"] = b"bad"
        with self.assertRaises(ValueError):
            backup.prune(self.store, execute=True, daily=1, weekly=0)
        self.assertFalse(self.store.deleted)
        backup.verify_set(self.store, "old")

    def test_retention_keeps_daily_and_weekly_categories(self):
        manifests = [{"backup_id": f"day{i}", "completed_at": (datetime(2026, 11, 23, tzinfo=timezone.utc) - timedelta(days=i)).isoformat()} for i in range(40)]
        plan = backup.retention_plan(manifests)
        self.assertTrue(set(f"day{i}" for i in range(7)).issubset(plan["keep"]))
        self.assertGreaterEqual(len(plan["keep"]), 9)
        with self.assertRaises(ValueError):
            backup.retention_plan(manifests, daily=0)

    def test_operation_lock_rejects_overlap(self):
        with backup.local_operation_lock(self.root):
            with self.assertRaises(FileExistsError):
                with backup.local_operation_lock(self.root):
                    self.fail("Concurrent operation was admitted")
        self.assertFalse((self.root / ".backup-operation.lock").exists())

    def test_missing_credential_has_no_value_in_exception(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "R2_BUCKET"):
            backup.require_env("R2_BUCKET")

    def test_staging_cleanup_removes_only_completed_payload(self):
        manifest = self.publish()
        restored = self.root / "set1-restore-123"
        restored.mkdir()
        (restored / "source.dump").write_bytes(b"keep")
        expected = sum(item["size"] for item in manifest["members"])
        self.assertEqual(backup.discard_staging(self.store, self.root, "set1"), expected)
        self.assertEqual({path.name for path in (self.root / "set1").iterdir()}, {"manifest.json", "complete.json"})
        self.assertEqual((restored / "source.dump").read_bytes(), b"keep")
        backup.verify_set(self.store, "set1")

    def test_staging_cleanup_refuses_corrupt_remote_before_deleting(self):
        self.publish()
        self.store.objects["set1/database.dump"] = b"bad"
        with self.assertRaises(ValueError):
            backup.discard_staging(self.store, self.root, "set1")
        self.assertEqual((self.root / "set1/database.dump").read_bytes(), b"test dump")

    def test_staging_cleanup_refuses_unexpected_or_corrupt_local_files(self):
        self.publish()
        foreign = self.root / "set1/unrelated.txt"
        foreign.write_text("do not delete")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            backup.discard_staging(self.store, self.root, "set1")
        self.assertTrue(foreign.exists())
        foreign.unlink()
        (self.root / "set1/database.dump").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Local staging member"):
            backup.discard_staging(self.store, self.root, "set1")
        self.assertTrue((self.root / "set1/database.dump").exists())

    def test_staging_cleanup_refuses_traversal(self):
        self.publish()
        with self.assertRaises(ValueError):
            backup.discard_staging(self.store, self.root, "../set1")
        self.assertTrue((self.root / "set1/database.dump").exists())

    def test_staging_cleanup_refuses_a_linked_set(self):
        self.publish()
        linked = self.root / "linked"
        linked.mkdir()
        try:
            (linked / "set1").symlink_to(self.root / "set1", target_is_directory=True)
        except OSError:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(self.root / "set1"), str(linked / "set1"))
            else:
                self.skipTest("Creating filesystem symlinks is unavailable")
        with self.assertRaisesRegex(ValueError, "links"):
            backup.discard_staging(self.store, linked, "set1")
        self.assertTrue((self.root / "set1/database.dump").exists())

    def test_reports_are_written_before_releasing_operation_lock(self):
        original = backup.write_json
        writes = []
        def write(path, value):
            self.assertTrue((self.root / ".backup-operation.lock").exists())
            writes.append(path.name)
            original(path, value)
        argv = ["backup", "list", "--work-dir", str(self.root)]
        with patch("sys.argv", argv), patch.object(backup, "run", return_value={"passed": True}), patch.object(backup, "write_json", side_effect=write), redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(), 0)
        self.assertEqual(writes, ["last-list-attempt.json", "last-list-success.json"])

    def test_lock_contention_does_not_replace_previous_success_report(self):
        report = self.root / "last-list-success.json"
        report.write_text("original")
        with backup.local_operation_lock(self.root), patch("sys.argv", ["backup", "list", "--work-dir", str(self.root)]), redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(), 1)
        self.assertEqual(report.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
