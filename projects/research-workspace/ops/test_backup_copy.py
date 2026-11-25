"""Conditional R2 copying preserves independently recoverable backup contents."""
from contextlib import nullcontext
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError, ReadTimeoutError

from ops import backup, serverless
from ops.test_backup import Store


ENDPOINT = "https://test-account.r2.cloudflarestorage.com"
ETAG = '"0123456789abcdef0123456789abcdef"'


class CopyStore(Store):
    def __init__(self, origin=None):
        super().__init__()
        self.origin = origin
        self.etag = ETAG
        self.copied = []
        self.uploaded = []
        self.copy_error = None
        self.corrupt_copy = False

    def read_for_copy(self, name):
        return self.read(name), backup.CopySource(ENDPOINT, "private", "source/" + name, self.etag)

    def copy(self, name, source):
        self.copied.append(name)
        if self.copy_error:
            raise self.copy_error
        if source.etag != self.origin.etag or name in self.objects:
            raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 412},
                               "Error": {"Code": "PreconditionFailed"}}, "CopyObject")
        body = self.origin.objects[source.key.removeprefix("source/")]
        self.objects[name] = b"x" * len(body) if self.corrupt_copy else body

    def put(self, name, path):
        self.uploaded.append(name)
        super().put(name, path)


class Connection:
    def __init__(self, item):
        self.item = item

    def transaction(self):
        return nullcontext(self)

    def execute(self, query, *args):
        if "pg_export_snapshot" in query:
            row = {"id": "snapshot", "database": "research", "snapshot_at": "2026-11-25T00:00:00+00:00"}
        elif "SHOW server_version" in query:
            row = {"server_version": "17.11"}
        elif "to_regclass" in query:
            row = {"name": None}
        else:
            row = None
        rows = [self.item] if "FROM research.artifacts" in query else []
        return Mock(fetchone=lambda: row, fetchall=lambda: rows)


class CopyBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "set1"
        self.source = CopyStore()
        self.source.objects["bundle/item.json"] = b"verified source evidence"
        self.item = {"storage_key": "bundle/item.json", "size": 24,
                     "sha256": hashlib.sha256(b"verified source evidence").hexdigest()}
        self.receipts = {}
        self.store = CopyStore(self.source)

    def prepare(self):
        def pg(tool, url, arguments, **kwargs):
            if tool == "pg_dump" and "--version" not in arguments:
                path = next(value.removeprefix("--file=") for value in arguments if value.startswith("--file="))
                Path(path).write_bytes(b"database snapshot")
            return ""
        with patch.dict(os.environ, {"RESEARCH_APP_COMMIT": "a" * 40}), \
             patch.object(backup, "query_checks", return_value=[]), \
             patch.object(backup, "role_definitions", return_value={"roles": [], "memberships": []}), \
             patch.object(backup, "pg_command", side_effect=pg):
            return backup.prepare_set("unused", None, self.directory, "set1", object_store=self.source,
                                      connection=Connection(self.item), copy_sources=self.receipts)

    def publish(self, manifest):
        return backup.publish_set(self.store, self.directory, manifest, copy_sources=self.receipts)

    def test_copy_creates_self_contained_set_without_uploading_source_body(self):
        manifest = self.prepare()
        self.assertEqual(set(self.receipts), {"objects/bundle/item.json"})
        self.assertEqual(self.receipts["objects/bundle/item.json"].etag, ETAG)
        manifest = self.publish(manifest)
        self.assertEqual(self.store.copied, ["set1/objects/bundle/item.json"])
        self.assertFalse(any("/objects/" in name for name in self.store.uploaded))
        self.assertEqual(self.store.uploaded[-1], "set1/complete.json")
        self.assertNotIn("etag", backup.packed(manifest).decode())
        self.source.objects.clear()
        recovered = self.directory.parent / "recovered"
        self.assertEqual(backup.verify_set(self.store, "set1", recovered), manifest)
        self.assertEqual((recovered / "objects/bundle/item.json").read_bytes(), b"verified source evidence")

    def test_corrupt_source_never_yields_a_copy_receipt(self):
        self.source.objects["bundle/item.json"] = b"x" * self.item["size"]
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare()
        self.assertEqual(self.receipts, {})
        self.assertFalse((self.directory / "database.dump").exists())

    def test_source_changed_after_verification_rejects_copy(self):
        manifest = self.prepare()
        self.source.etag = '"changed"'
        with self.assertRaises(ClientError):
            self.publish(manifest)
        self.assertNotIn("set1/complete.json", self.store.objects)
        self.assertFalse(any("/objects/" in name for name in self.store.uploaded))

    def test_existing_destination_is_never_overwritten(self):
        manifest = self.prepare()
        self.store.objects["set1/objects/bundle/item.json"] = b"existing destination"
        with self.assertRaises(ClientError):
            self.publish(manifest)
        self.assertEqual(self.store.objects["set1/objects/bundle/item.json"], b"existing destination")
        self.assertNotIn("set1/complete.json", self.store.objects)

    def test_copy_failure_has_no_upload_fallback_or_completion(self):
        for error in (OSError("connection lost"), ClientError({"Error": {"Code": "NotImplemented"},
                       "ResponseMetadata": {"HTTPStatusCode": 501}}, "CopyObject")):
            with self.subTest(error=type(error).__name__):
                self.store.copy_error = error
                path = self.directory / "objects/bundle/item.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"verified source evidence")
                manifest = {"format_version": 2, "query_check_version": 2, "backup_id": "set1",
                            "members": [{"path": "objects/bundle/item.json", **self.item}]}
                for name, body in [('database.dump', b'dump'), ('roles.json', b'{}')]:
                    (self.directory / name).write_bytes(body)
                    manifest['members'].append({'path': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
                self.receipts["objects/bundle/item.json"] = backup.check_stream(self.source, "bundle/item.json", self.item, for_copy=True)
                with self.assertRaises(type(error)):
                    self.publish(manifest)
                self.assertEqual(self.store.uploaded, [])
                self.assertNotIn("set1/complete.json", self.store.objects)

    def test_corrupt_destination_readback_blocks_completion(self):
        manifest = self.prepare()
        self.store.corrupt_copy = True
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.publish(manifest)
        self.assertNotIn("set1/manifest.json", self.store.objects)
        self.assertNotIn("set1/complete.json", self.store.objects)

    def test_incomplete_or_extra_receipts_fail_before_writes(self):
        manifest = self.prepare()
        for receipts in ({}, {**self.receipts, "objects/extra": next(iter(self.receipts.values()))}):
            with self.subTest(receipts=list(receipts)), self.assertRaisesRegex(ValueError, "exactly"):
                backup.publish_set(self.store, self.directory, manifest, copy_sources=receipts)
        self.assertEqual(self.store.uploaded, [])
        self.assertEqual(self.store.copied, [])

    def test_lost_database_lock_blocks_source_get_and_destination_copy(self):
        connection = Mock()
        connection.execute.side_effect = OSError("lock session lost")
        store = Mock()
        locked = serverless.LockedStore(connection, store)
        with self.assertRaises(OSError):
            locked.read_for_copy("source")
        with self.assertRaises(OSError):
            locked.copy("destination", backup.CopySource(ENDPOINT, "private", "source/item", ETAG))
        store.read_for_copy.assert_not_called()
        store.copy.assert_not_called()


class RawBody(io.BytesIO):
    def stream(self, amt=None, decode_content=False):
        while chunk := self.read(amt):
            yield chunk


class SignedCopyTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {"R2_ENDPOINT": ENDPOINT, "R2_BUCKET": "private",
                                    "R2_ACCESS_KEY_ID": "test-access", "R2_SECRET_ACCESS_KEY": "test-secret"}):
            self.store = backup.R2Store(namespace="backups")
        self.addCleanup(self.store.client.close)
        self.source = backup.CopySource(ENDPOINT, "private", "source/item.json", ETAG)

    def test_actual_sigv4_request_signs_both_conditions_without_upload_body(self):
        xml = b'<CopyObjectResult><ETag>"copied"</ETag><LastModified>2026-11-25T00:00:00Z</LastModified></CopyObjectResult>'
        requests = []
        def send(request):
            requests.append(request)
            return AWSResponse(request.url, 200, {"content-type": "application/xml"}, RawBody(xml))
        with patch.object(self.store.client._endpoint.http_session, "send", side_effect=send):
            self.store.copy("set1/objects/item.json", self.source)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        headers = {key.lower(): value.decode() if isinstance(value, bytes) else value for key, value in request.headers.items()}
        self.assertEqual(headers["cf-copy-destination-if-none-match"], "*")
        self.assertEqual(headers["x-amz-copy-source-if-match"], ETAG)
        self.assertEqual(headers["x-amz-copy-source"], "private/source/item.json")
        signed = headers["authorization"].split("SignedHeaders=", 1)[1].split(",", 1)[0]
        self.assertIn("cf-copy-destination-if-none-match", signed.split(";"))
        self.assertIn("x-amz-copy-source-if-match", signed.split(";"))
        self.assertFalse(request.body)

    def test_precondition_failure_cannot_fall_back_to_an_unconditional_request(self):
        xml = b"<Error><Code>PreconditionFailed</Code><Message>condition rejected</Message></Error>"
        with patch.object(self.store.client._endpoint.http_session, "send",
                          return_value=AWSResponse(ENDPOINT, 412, {"content-type": "application/xml"}, RawBody(xml))) as send:
            with self.assertRaises(ClientError):
                self.store.copy("set1/objects/item.json", self.source)
        self.assertEqual(send.call_count, 1)

    def test_result_archive_copy_signs_the_separate_source_bucket(self):
        xml = b'<CopyObjectResult><ETag>"copied"</ETag><LastModified>2026-11-25T00:00:00Z</LastModified></CopyObjectResult>'
        requests = []
        def send(request):
            requests.append(request)
            return AWSResponse(request.url, 200, {'content-type': 'application/xml'}, RawBody(xml))
        source = backup.CopySource(ENDPOINT, 'private-results', 'results/generation/active_traits.bin', ETAG)
        with patch.object(self.store.client._endpoint.http_session, 'send', side_effect=send):
            self.store.copy('set1/results/generation/active_traits.bin', source)
        headers = {key.lower(): value.decode() if isinstance(value, bytes) else value for key, value in requests[0].headers.items()}
        self.assertEqual(headers['x-amz-copy-source'], 'private-results/results/generation/active_traits.bin')
        self.assertEqual(headers['cf-copy-destination-if-none-match'], '*')
        self.assertEqual(headers['x-amz-copy-source-if-match'], ETAG)
        self.assertFalse(requests[0].body)

    def test_lost_copy_response_retries_with_both_conditions_and_leaves_failure_visible(self):
        requests = []
        def send(request):
            requests.append(request)
            if len(requests) == 1:
                raise ReadTimeoutError(endpoint_url=ENDPOINT)
            xml = b"<Error><Code>PreconditionFailed</Code><Message>destination exists</Message></Error>"
            return AWSResponse(request.url, 412, {"content-type": "application/xml"}, RawBody(xml))
        with patch.object(self.store.client._endpoint.http_session, "send", side_effect=send), \
             patch("botocore.endpoint.time.sleep", return_value=None):
            with self.assertRaises(ClientError):
                self.store.copy("set1/objects/item.json", self.source)
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request.headers["cf-copy-destination-if-none-match"], b"*")
            self.assertEqual(request.headers["x-amz-copy-source-if-match"], ETAG.encode())
            self.assertFalse(request.body)

    def test_copy_source_etag_comes_from_same_get_as_verified_bytes(self):
        body = io.BytesIO(b"data")
        with patch.object(self.store.client, "get_object", return_value={"Body": body, "ETag": ETAG}) as get:
            receipt = backup.check_stream(self.store, "source", {"size": 4, "sha256": hashlib.sha256(b"data").hexdigest()}, for_copy=True)
        self.assertEqual(receipt, backup.CopySource(ENDPOINT, "private", "backups/source", ETAG,
                                                   4, hashlib.sha256(b'data').hexdigest()))
        self.assertEqual(get.call_count, 1)
        self.assertTrue(body.closed)

    def test_missing_weak_or_malformed_etag_closes_source_without_copy(self):
        for etag in (None, "", "unquoted", 'W/"weak"', '"x"\r\nInjected: yes'):
            body = io.BytesIO(b"data")
            with self.subTest(etag=etag), patch.object(self.store.client, "get_object", return_value={"Body": body, "ETag": etag}):
                with self.assertRaisesRegex(ValueError, "ETag"):
                    self.store.read_for_copy("source")
                self.assertTrue(body.closed)

    def test_cross_account_self_copy_and_namespace_escape_rejected_before_http(self):
        sources = [backup.CopySource("https://other.r2.cloudflarestorage.com", "private", "source/item", ETAG),
                   backup.CopySource(ENDPOINT, "private", "backups/destination", ETAG),
                   backup.CopySource(ENDPOINT, "private", "../outside", ETAG)]
        with patch.object(self.store.client, "copy_object") as copy:
            for source in sources:
                with self.subTest(source=source), self.assertRaises(ValueError):
                    self.store.copy("destination", source)
            with self.assertRaises(ValueError):
                self.store.copy("../outside", self.source)
        copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
