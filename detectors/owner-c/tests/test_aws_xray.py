"""X-Ray reader Lambda (JS-01 route) with a fake X-Ray client."""
import json
import sys
import types
import unittest
from unittest import mock

from helpers import SHA
from owner_c.aws import common, xray_handler
from test_aws import AwsTestCase
from test_normalize_xray import SERIAL_SRC, sub, trace


class FakeXray:
    def __init__(self, serial=True):
        self.calls = []
        children = ([sub("Inventory", 1.0 + i * 0.2, 1.2 + i * 0.2) for i in range(6)] if serial
                    else [sub("Inventory", 1.0, 1.4) for _ in range(6)])
        self.traces = {"1-a": trace(children, trace_id="1-a"), "1-b": trace(children, trace_id="1-b")}

    def get_trace_summaries(self, **kw):
        self.calls.append(("summaries", kw["FilterExpression"]))
        return {"TraceSummaries": [{"Id": i} for i in self.traces]}

    def batch_get_traces(self, TraceIds, **kw):
        self.calls.append(("batch", tuple(TraceIds)))
        return {"Traces": [self.traces[i] for i in TraceIds]}


class XrayHandlerTests(AwsTestCase):
    def event(self, **extra):
        return {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": [{"path": "orders.js", "content": SERIAL_SRC}]},
                "xray": {"function_files": {"orders-fn": "orders.js"}}, **extra}

    def test_serial_traces_confirm_js01_and_are_published(self):
        fake = common._clients["xray"] = FakeXray()
        out = xray_handler.lambda_handler(self.event())
        self.assertEqual([(r["check_id"], r["status"], r["findings"]) for r in out["results"]], [("JS-01", "completed", 1)])
        self.assertEqual(fake.calls[0], ("summaries", 'service("orders-fn")'))  # read-only: summaries + batch gets only
        self.assertTrue(all(kind in ("summaries", "batch") for kind, _ in fake.calls))
        detail = json.loads(self.events.entries[0]["Detail"])
        self.assertEqual((detail["check_id"], detail["findings"][0]["evidence"][1]["kind"]), ("JS-01", "telemetry"))

    def test_parallel_traces_give_a_clean_completed_result(self):
        common._clients["xray"] = FakeXray(serial=False)
        out = xray_handler.lambda_handler(self.event(dry_run=True))
        self.assertEqual([(r["status"], r["findings"]) for r in out["results"]], [("completed", 0)])

    def test_no_traces_for_the_function_means_unavailable_not_clean(self):
        class Empty(FakeXray):
            def get_trace_summaries(self, **kw):
                return {"TraceSummaries": []}

        common._clients["xray"] = Empty()
        out = xray_handler.lambda_handler(self.event(dry_run=True))
        self.assertEqual([r["status"] for r in out["results"]], ["unavailable"])

    def test_requires_function_mapping(self):
        with self.assertRaises(ValueError):
            xray_handler.lambda_handler({**self.event(), "xray": {}})

    def test_role_credentials_are_requested_once_per_role(self):
        assumed, built = [], []

        class FakeSts:
            def assume_role(self, **kw):
                assumed.append((kw["RoleArn"], kw["DurationSeconds"]))
                return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c"}}

        def fake_client(name, **kw):
            built.append((name, kw.get("aws_session_token")))
            return FakeXray()

        common._clients["sts"] = FakeSts()
        role = "arn:aws:iam::111111111111:role/owner-c-xray-reader"
        with mock.patch.dict(sys.modules, {"boto3": types.SimpleNamespace(client=fake_client)}):
            first, second = xray_handler._xray_client(role), xray_handler._xray_client(role)
        self.assertIs(first, second)
        self.assertEqual(assumed, [(role, 900)])  # short-lived, once per role
        self.assertEqual(built, [("xray", "c")])  # the X-Ray client uses the assumed (client-account) credentials


if __name__ == "__main__":
    unittest.main()
