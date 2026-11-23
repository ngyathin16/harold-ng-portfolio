"""Offline behavior checks: no OCI dependency, network calls or notifications."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, call, patch, sentinel


SPEC = importlib.util.spec_from_file_location(
    "oracle_capacity_under_test", Path(__file__).with_name("oracle_capacity.py")
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def capacity(*statuses):
    return {"region": "ap-singapore-1", "results": [
        {"availability_domain": 1, "shape": "VM.Standard.A1.Flex",
         "ocpus": 2, "memory_gb": 12, "status": status}
        for status in statuses
    ]}


class ApiFailure(Exception):
    pass


class CapacityMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = self.directory / "never-read-config"
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.deadline = self.now + timedelta(days=3)

    def latest(self):
        return json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))

    def history(self):
        return [json.loads(line) for line in
                (self.directory / "history.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_expired_window_calls_neither_query_nor_notification(self):
        query, notify = Mock(), Mock()
        with patch.object(checker, "utc_now", return_value=self.deadline):
            result = checker.check_once(self.config, self.directory, self.deadline, query, notify)
        self.assertEqual(result, 0)
        self.assertEqual(self.latest()["status"], "EXPIRED")
        self.assertEqual(self.history()[0]["status"], "EXPIRED")
        self.assertFalse(self.latest()["resources_created"])
        query.assert_not_called()
        notify.assert_not_called()

    def test_response_arriving_after_deadline_cannot_notify(self):
        clock = [self.now]

        def delayed_response(*_):
            clock[0] = self.deadline
            return capacity("AVAILABLE")

        notify = Mock()
        with patch.object(checker, "utc_now", side_effect=lambda: clock[0]):
            checker.check_once(self.config, self.directory, self.deadline, delayed_response, notify)
        self.assertEqual(self.latest()["status"], "EXPIRED")
        self.assertFalse((self.directory / "last-available.json").exists())
        notify.assert_not_called()

    def test_deadline_crossed_while_writing_report_prevents_notification(self):
        clock = [self.now]
        write_status = checker.write_status

        def delayed_write(directory, report):
            write_status(directory, report)
            clock[0] = self.deadline

        notify = Mock()
        with patch.object(checker, "utc_now", side_effect=lambda: clock[0]), \
                patch.object(checker, "write_status", side_effect=delayed_write):
            checker.check_once(self.config, self.directory, self.deadline,
                               Mock(return_value=capacity("AVAILABLE")), notify)
        self.assertEqual(self.latest()["status"], "AVAILABLE")
        notify.assert_not_called()

    def test_errors_redact_messages_and_credentials_from_every_report(self):
        secret = "never-publish-this-bearer-secret"
        failure = ApiFailure("Authorization: Bearer " + secret + "; private_key=private-material")
        failure.status = 403
        failure.code = "NotAuthorizedOrNotFound"
        query, notify = Mock(side_effect=failure), Mock()
        with patch.object(checker, "utc_now", return_value=self.now):
            result = checker.check_once(self.config, self.directory, self.deadline, query, notify)
        self.assertEqual(result, 1)
        self.assertEqual(self.latest()["error"], {
            "type": "ApiFailure", "http_status": 403, "code": "NotAuthorizedOrNotFound"
        })
        for name in ("latest.json", "history.jsonl", "STATUS.txt"):
            with self.subTest(report=name):
                text = (self.directory / name).read_text(encoding="utf-8")
                self.assertNotIn(secret, text)
                self.assertNotIn("private-material", text)
                self.assertNotIn("Authorization", text)
                self.assertNotIn(str(failure), text)
        notify.assert_not_called()

    def test_untrusted_error_code_and_status_are_omitted(self):
        failure = ApiFailure("private exception message")
        failure.code = "Authorization: Bearer private-token"
        failure.status = "403 private-token"
        with patch.object(checker, "utc_now", return_value=self.now):
            checker.check_once(self.config, self.directory, self.deadline,
                               Mock(side_effect=failure), Mock())
        self.assertEqual(self.latest()["error"], {"type": "ApiFailure"})
        for name in ("latest.json", "history.jsonl", "STATUS.txt"):
            self.assertNotIn("private-token", (self.directory / name).read_text(encoding="utf-8"))

    def test_notification_transitions_and_retained_last_available(self):
        notify = Mock()
        statuses = ["OUT_OF_HOST_CAPACITY", "AVAILABLE", "AVAILABLE",
                    "OUT_OF_HOST_CAPACITY", "AVAILABLE"]
        expected_calls = [0, 1, 1, 1, 2]
        last_available = None
        for index, status in enumerate(statuses):
            moment = self.now + timedelta(hours=3 * index)
            with patch.object(checker, "utc_now", return_value=moment):
                checker.check_once(self.config, self.directory, self.deadline,
                                   Mock(return_value=capacity(status)), notify)
            self.assertEqual(notify.call_count, expected_calls[index])
            if status == "AVAILABLE":
                last_available = (self.directory / "last-available.json").read_bytes()
                self.assertEqual(self.latest()["last_available_at"], moment.isoformat())
            elif last_available is not None:
                self.assertEqual((self.directory / "last-available.json").read_bytes(), last_available)
                retained = json.loads(last_available)
                self.assertEqual(self.latest()["last_available_at"], retained["checked_at"])
                self.assertIn(retained["checked_at"],
                              (self.directory / "STATUS.txt").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in self.history()],
                         ["UNAVAILABLE", "AVAILABLE", "AVAILABLE", "UNAVAILABLE", "AVAILABLE"])

    def test_notification_failure_preserves_already_written_reports(self):
        observed = []

        def failed_notification():
            observed.append((self.latest()["status"], self.history()[-1]["status"]))
            raise RuntimeError("notification-private-secret")

        with patch.object(checker, "utc_now", return_value=self.now):
            result = checker.check_once(self.config, self.directory, self.deadline,
                                        Mock(return_value=capacity("AVAILABLE")), failed_notification)
        self.assertEqual(result, 0)
        self.assertEqual(observed, [("AVAILABLE", "AVAILABLE")])
        self.assertEqual(self.latest()["status"], "AVAILABLE")
        self.assertTrue((self.directory / "last-available.json").exists())
        for name in ("latest.json", "history.jsonl", "STATUS.txt", "last-available.json"):
            self.assertNotIn("notification-private-secret",
                             (self.directory / name).read_text(encoding="utf-8"))

    def test_unknown_is_distinct_from_confirmed_unavailable(self):
        cases = [
            ((), "UNKNOWN", 1),
            (("OUT_OF_HOST_CAPACITY", "OUT_OF_HOST_CAPACITY"), "UNAVAILABLE", 0),
            (("OUT_OF_HOST_CAPACITY", "UNKNOWN"), "UNKNOWN", 1),
            (("FUTURE_API_STATUS",), "UNKNOWN", 1),
            (("UNKNOWN", "AVAILABLE"), "AVAILABLE", 0),
        ]
        for index, (statuses, expected, exit_code) in enumerate(cases):
            with self.subTest(statuses=statuses), patch.object(checker, "utc_now", return_value=self.now):
                destination = self.directory / str(index)
                notify = Mock()
                result = checker.check_once(self.config, destination, self.deadline,
                                            Mock(return_value=capacity(*statuses)), notify)
                report = json.loads((destination / "latest.json").read_text(encoding="utf-8"))
                self.assertEqual((report["status"], result), (expected, exit_code))
                self.assertEqual(notify.call_count, int(expected == "AVAILABLE"))

    def test_concurrent_run_skips_query_and_releases_lock_for_next_run(self):
        query = Mock(return_value=capacity("OUT_OF_HOST_CAPACITY"))
        notify = Mock()
        with checker.single_run(self.directory) as acquired:
            self.assertTrue(acquired)
            with ThreadPoolExecutor(max_workers=1) as executor:
                attempt = executor.submit(checker.check_once, self.config, self.directory,
                                          self.deadline, query, notify)
                self.assertEqual(attempt.result(timeout=5), 0)
            query.assert_not_called()
            notify.assert_not_called()
            self.assertFalse((self.directory / "latest.json").exists())
            self.assertFalse((self.directory / "history.jsonl").exists())
        with patch.object(checker, "utc_now", return_value=self.now):
            checker.check_once(self.config, self.directory, self.deadline, query, notify)
        query.assert_called_once_with(self.config, self.deadline)
        self.assertEqual(self.latest()["status"], "UNAVAILABLE")

    def test_deadline_requires_timezone_and_normalizes_to_utc(self):
        self.assertEqual(checker.parse_deadline("2030-01-01T08:00:00+08:00"), self.now)
        self.assertEqual(checker.parse_deadline("2030-01-01T00:00:00Z"), self.now)
        with self.assertRaises(ValueError):
            checker.parse_deadline("2030-01-01T00:00:00")


class CapacityApiTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.deadline = self.now + timedelta(days=3)
        self.config_path = Path("never-read-config")
        self.config = {"tenancy": "private-tenancy-id", "user": "private-user-id",
                       "key_file": "private-key-path", "region": "ap-singapore-1"}
        self.identity = Mock(spec=["list_region_subscriptions", "list_availability_domains"])
        self.identity.list_region_subscriptions.return_value = SimpleNamespace(data=[
            SimpleNamespace(region_name="ap-singapore-1", is_home_region=True),
            SimpleNamespace(region_name="us-ashburn-1", is_home_region=False),
        ])
        self.domains = [SimpleNamespace(name="private-ad-one"), SimpleNamespace(name="private-ad-two")]
        self.identity.list_availability_domains.return_value = SimpleNamespace(data=self.domains)
        self.compute = Mock(spec=["create_compute_capacity_report"])
        self.compute.create_compute_capacity_report.side_effect = self.report
        models = SimpleNamespace(**{name: Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
            for name in ("CreateComputeCapacityReportDetails",
                         "CreateCapacityReportShapeAvailabilityDetails", "CapacityReportInstanceShapeConfig")})
        self.oci = SimpleNamespace(
            config=SimpleNamespace(from_file=Mock(return_value=self.config)),
            retry=SimpleNamespace(NoneRetryStrategy=Mock(return_value=sentinel.no_retry)),
            identity=SimpleNamespace(IdentityClient=Mock(return_value=self.identity)),
            core=SimpleNamespace(ComputeClient=Mock(return_value=self.compute), models=models),
        )
        self.addCleanup(patch.stopall)
        patch.dict("sys.modules", {"oci": self.oci}).start()
        self.clock = patch.object(checker, "utc_now", return_value=self.now).start()

    @staticmethod
    def report(details):
        return SimpleNamespace(data=SimpleNamespace(shape_availabilities=[
            SimpleNamespace(instance_shape_config=item.instance_shape_config,
                            availability_status="OUT_OF_HOST_CAPACITY")
            for item in details.shape_availabilities
        ]))

    def test_only_home_region_fixed_a1_sizes_and_report_apis_are_used(self):
        result = checker.query_capacity(self.config_path, self.deadline)
        self.oci.config.from_file.assert_called_once_with(str(self.config_path))
        options = {"timeout": (10, 25), "retry_strategy": sentinel.no_retry}
        self.oci.identity.IdentityClient.assert_called_once_with(self.config, **options)
        self.oci.core.ComputeClient.assert_called_once_with(self.config, **options)
        self.assertEqual(self.identity.method_calls, [
            call.list_region_subscriptions("private-tenancy-id"),
            call.list_availability_domains("private-tenancy-id"),
        ])
        self.assertEqual(len(self.compute.method_calls), 2)
        for index, invocation in enumerate(self.compute.method_calls):
            self.assertEqual(invocation[0], "create_compute_capacity_report")
            details = invocation.args[0]
            self.assertEqual(details.compartment_id, "private-tenancy-id")
            self.assertEqual(details.availability_domain, self.domains[index].name)
            self.assertEqual([(item.instance_shape, item.instance_shape_config.ocpus,
                               item.instance_shape_config.memory_in_gbs)
                              for item in details.shape_availabilities],
                             [("VM.Standard.A1.Flex", 2, 12), ("VM.Standard.A1.Flex", 1, 6)])
        self.assertEqual(result["region"], "ap-singapore-1")
        self.assertEqual([row["availability_domain"] for row in result["results"]], [1, 1, 2, 2])
        self.assertNotIn("private-", json.dumps(result))

    def test_non_home_region_is_rejected_before_capacity_query(self):
        self.config["region"] = "us-ashburn-1"
        with self.assertRaises(ValueError):
            checker.query_capacity(self.config_path, self.deadline)
        self.identity.list_availability_domains.assert_not_called()
        self.oci.core.ComputeClient.assert_not_called()
        self.compute.create_compute_capacity_report.assert_not_called()

    def test_expired_query_does_not_read_credentials_or_call_api(self):
        self.clock.return_value = self.deadline
        with self.assertRaises(TimeoutError):
            checker.query_capacity(self.config_path, self.deadline)
        self.oci.config.from_file.assert_not_called()
        self.oci.identity.IdentityClient.assert_not_called()
        self.oci.core.ComputeClient.assert_not_called()

    def test_deadline_between_identity_calls_stops_remaining_requests(self):
        response = self.identity.list_region_subscriptions.return_value

        def regions(*_):
            self.clock.return_value = self.deadline
            return response

        self.identity.list_region_subscriptions.side_effect = regions
        with self.assertRaises(TimeoutError):
            checker.query_capacity(self.config_path, self.deadline)
        self.identity.list_availability_domains.assert_not_called()
        self.compute.create_compute_capacity_report.assert_not_called()

    def test_deadline_between_domains_stops_remaining_capacity_requests(self):
        def first_report(details):
            self.clock.return_value = self.deadline
            return self.report(details)

        self.compute.create_compute_capacity_report.side_effect = first_report
        with self.assertRaises(TimeoutError):
            checker.query_capacity(self.config_path, self.deadline)
        self.compute.create_compute_capacity_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
