import io
import json
import unittest
import zipfile
from unittest import mock

from helpers import SHA
from owner_c.aws import common, handler
from owner_c.checks import STATIC_CHECKS
from owner_c.langs import accepts
from shared.contracts.validation import validate

BAD = "def f(a=[]):\n    pass\n"


class MissingBus(Exception):
    response = {"Error": {"Code": "ResourceNotFoundException"}}


class FakeEvents:
    def __init__(self, fail=0, bus_exists=True):
        self.calls, self.fail, self.bus_exists = [], fail, bus_exists

    def describe_event_bus(self, Name):
        if not self.bus_exists:
            raise MissingBus()
        return {"Name": Name}

    def put_events(self, Entries):
        self.calls.append(Entries)
        return {"FailedEntryCount": self.fail}

    @property
    def entries(self):
        return [e for call in self.calls for e in call]


class AwsTestCase(unittest.TestCase):
    def setUp(self):
        common._clients.clear()
        common._verified_buses.clear()
        self.events = FakeEvents()
        common._clients["events"] = self.events
        patcher = mock.patch.dict("os.environ", {"FINDINGS_BUS_NAME": "findings-hub"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, **extra):
        return {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": [{"path": "app.py", "content": BAD}]},
                **extra}


class StaticHandlerTests(AwsTestCase):
    def test_publishes_one_valid_contract_result_per_check(self):
        out = handler.lambda_handler(self.event())
        self.assertTrue(out["published"])
        expected = [k for k, m in STATIC_CHECKS.items() if accepts(m, "app.py")]
        self.assertEqual(len(self.events.entries), len(expected))
        for entry in self.events.entries:
            self.assertEqual((entry["Source"], entry["DetailType"], entry["EventBusName"]),
                             ("owner-c.detectors", "detector.result.v1", "findings-hub"))
            result = json.loads(entry["Detail"])
            validate(result)  # a complete, valid contract v1 result
            self.assertEqual((result["repository_id"], result["commit_sha"], result["scan_id"]),
                             ("github:o/r", SHA, out["scan_id"]))
        by_check = {r["check_id"]: r for r in out["results"]}
        self.assertEqual(by_check["PY-09"]["findings"], 1)
        self.assertTrue(all(r["status"] == "completed" for r in out["results"]))

    def test_clean_results_are_published_too(self):
        handler.lambda_handler(self.event(source={"files": [{"path": "app.py", "content": "x = 1\n"}]}))
        statuses = {json.loads(e["Detail"])["status"] for e in self.events.entries}
        self.assertEqual(statuses, {"completed"})
        self.assertTrue(all(not json.loads(e["Detail"])["findings"] for e in self.events.entries))

    def test_dry_run_and_missing_bus_env_do_not_publish(self):
        self.assertFalse(handler.lambda_handler(self.event(dry_run=True))["published"])
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(handler.lambda_handler(self.event())["published"])
        self.assertFalse(self.events.calls)

    def test_missing_bus_fails_instead_of_silently_dropping_events(self):
        common._clients["events"] = FakeEvents(bus_exists=False)
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            handler.lambda_handler(self.event())

    def test_failed_entries_raise(self):
        common._clients["events"] = FakeEvents(fail=1)
        with self.assertRaises(RuntimeError):
            handler.lambda_handler(self.event())

    def test_test_files_are_excluded_unless_asked(self):
        files = [{"path": "tests/test_a.py", "content": BAD}, {"path": "app.py", "content": BAD}]
        out = handler.lambda_handler(self.event(source={"files": files}, dry_run=True))
        self.assertEqual({r["check_id"]: r for r in out["results"]}["PY-09"]["scope"], 1)
        out = handler.lambda_handler(self.event(source={"files": files}, dry_run=True, include_tests=True))
        self.assertEqual({r["check_id"]: r for r in out["results"]}["PY-09"]["scope"], 2)

    def test_files_are_batched_into_separate_valid_payloads(self):
        files = [{"path": f"m{i}.py", "content": BAD} for i in range(5)]
        with mock.patch.object(common, "FILES_PER_PAYLOAD", 2):
            out = handler.lambda_handler(self.event(source={"files": files}))
        py09 = [r for r in out["results"] if r["check_id"] == "PY-09"]
        self.assertEqual([r["scope"] for r in py09], [2, 2, 1])
        self.assertEqual(sum(r["findings"] for r in py09), 5)

    def test_s3_zip_source(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("repo-abc/pkg/a.py", BAD)
            zf.writestr("repo-abc/node_modules/x.py", BAD)
            zf.writestr("repo-abc/readme.md", "x")

        class FakeS3:
            def get_object(self, Bucket, Key):
                return {"Body": io.BytesIO(buf.getvalue())}

        common._clients["s3"] = FakeS3()
        out = handler.lambda_handler(self.event(source={"s3": {"bucket": "b", "key": "k.zip"}}, dry_run=True))
        self.assertEqual({r["check_id"]: r for r in out["results"]}["PY-09"]["scope"], 1)

    def test_invalid_events_are_rejected(self):
        for bad in ({"commit_sha": SHA, "source": {"files": []}},
                    {"repository_id": "r", "commit_sha": "abc", "source": {"files": []}},
                    {"repository_id": "r", "commit_sha": SHA}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                handler.lambda_handler(bad)


if __name__ == "__main__":
    unittest.main()
