"""scan-api -> findings-hub publishing (scan_api/hub.py and the worker's best-effort call). No AWS, no network."""
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from scan_api import hub, store, worker
from shared.contracts.validation import validate
from test_scan_api import SERVICE, SHA, FakeS3, make_tarball


class FakeEvents:
    def __init__(self, fail_with=None, failed_per_call=0):
        self.calls, self.fail_with, self.failed_per_call = [], fail_with, failed_per_call

    def put_events(self, Entries):
        if self.fail_with:
            raise self.fail_with
        self.calls.append(Entries)
        return {"FailedEntryCount": self.failed_per_call, "Entries": [{} for _ in Entries]}

    @property
    def entries(self):
        return [e for call in self.calls for e in call]


class WorkerHubTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.events = FakeS3(), FakeEvents()
        patches = [mock.patch.object(store, "client", lambda name: {"s3": self.s3, "events": self.events}[name]),
                   mock.patch.dict(os.environ, {"SCAN_BUCKET": "b", "FINDINGS_BUS_NAME": "findings-hub"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def scan(self):
        fixture = self.tmp / "fixture.tar.gz"
        top = "owner-repo-aaaaaaa"
        make_tarball(fixture, [(top, None), (f"{top}/app", None), (f"{top}/app/service.py", SERVICE)])
        scan_id = str(uuid.uuid4())
        with mock.patch.object(worker, "resolve_sha", return_value=SHA), \
                mock.patch.object(worker, "download", side_effect=lambda url, path: shutil.copy(fixture, path)):
            worker.handler({"scan_id": scan_id, "repo_url": "https://github.com/owner/repo"})
        return scan_id, self.s3.json(scan_id, "status.json"), self.s3.json(scan_id, "report.json")

    def test_validated_results_are_published_once_each(self):
        scan_id, status, report = self.scan()
        self.assertEqual(status["status"], "done")
        entries = self.events.entries
        validated = [c for c in report["checks"] if c["status_source"] == "detector"]
        self.assertEqual(len(entries), len(validated))
        self.assertEqual(status["hub"], {"bus": "findings-hub", "published": len(entries), "failed": 0,
                                         "skipped_too_large": []})
        self.assertEqual({e["Source"] for e in entries}, {"owner-c.scan-api", "owner-d.scan-api"})
        for entry in entries:
            self.assertEqual((entry["DetailType"], entry["EventBusName"]), ("detector.result.v1", "findings-hub"))
            result = json.loads(entry["Detail"])
            validate(result)
            self.assertEqual((result["scan_id"], result["repository_id"], result["commit_sha"]),
                             (scan_id, "github:owner/repo", SHA))
        py09 = [json.loads(e["Detail"]) for e in entries if json.loads(e["Detail"])["check_id"] == "PY-09"]
        self.assertEqual(len(py09[0]["findings"]), 1)

    def test_hub_failure_keeps_the_scan_done(self):
        self.events.fail_with = RuntimeError("AccessDenied")
        _, status, report = self.scan()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["hub"], {"bus": "findings-hub", "error": "publish failed (RuntimeError)"})
        self.assertTrue(report["findings"])

    def test_failed_entries_are_counted(self):
        self.events.failed_per_call = 1
        _, status, _ = self.scan()
        self.assertEqual(status["hub"]["failed"], len(self.events.calls))

    def test_no_bus_means_no_publish(self):
        with mock.patch.dict(os.environ, {"FINDINGS_BUS_NAME": ""}):
            _, status, _ = self.scan()
        self.assertEqual(self.events.calls, [])
        self.assertIsNone(status["hub"])


class HubBatchingTest(unittest.TestCase):
    def result(self, check_id, pad=0):
        return {"check_id": check_id, "pad": "x" * pad}

    def test_batches_respect_entry_count_and_request_size(self):
        sized, skipped = hub.entries([("C", self.result(f"C-{i}", 50_000)) for i in range(7)]
                                     + [("D", self.result(f"D-{i}")) for i in range(12)], "findings-hub")
        self.assertEqual(skipped, [])
        batches = list(hub.batches(sized))
        self.assertEqual(sum(len(b) for b in batches), 19)
        for batch in batches:
            self.assertLessEqual(len(batch), hub.MAX_BATCH_ENTRIES)
            self.assertLessEqual(sum(len(e["Detail"]) + 100 for e in batch), hub.MAX_BATCH_BYTES)

    def test_oversized_results_are_skipped(self):
        sized, skipped = hub.entries([("D", self.result("OBS-01", 300_000)), ("D", self.result("OBS-02"))], "bus")
        self.assertEqual(skipped, ["OBS-01"])
        self.assertEqual([json.loads(e["Detail"])["check_id"] for e, _ in sized], ["OBS-02"])


if __name__ == "__main__":
    unittest.main()
