import io
import json
import unittest
import urllib.parse

from tests.aws_fakes import NOW, SHA, AwsTestCase, ClientError, FakeTable, mock_env
from findings_hub import writer
from owner_d.aws import artifact_handler
from shared.contracts.validation import validate

BUCKET = "owner-d-artifacts-123456789012-ap-south-1"
PREFIX = f"uploads/{urllib.parse.quote('github:AWS-env/example', safe='')}/{SHA}/4242-1/"

HEAVY = {"test_id": "tests/test_api.py::test_fetch_users", "duration_seconds": 14.2, "sleep_seconds": 2.5,
         "network_call_count": 3, "fixture_bytes": 52428800, "setup_seconds": 4.1}
LIGHT = {"test_id": "tests/test_api.py::test_parse", "duration_seconds": 0.01, "sleep_seconds": 0,
         "network_call_count": 0, "fixture_bytes": 1024, "setup_seconds": 0.001}


class FakeS3:
    def __init__(self, objects=None):
        self.objects, self.calls = dict(objects or {}), []

    def get_object(self, Bucket, Key, Range=None):
        self.calls.append((Bucket, Key, Range))
        if Key not in self.objects:
            raise ClientError("NoSuchKey")
        body = self.objects[Key]
        if not body:
            raise ClientError("InvalidRange")
        end = int(Range.rsplit("-", 1)[1]) if Range else len(body) - 1
        return {"Body": io.BytesIO(body[:end + 1])}


def s3_event(key, bucket=BUCKET):
    return {"source": "aws.s3", "detail-type": "Object Created",
            "detail": {"bucket": {"name": bucket}, "object": {"key": key, "size": 1}, "reason": "PostObject"}}


def artifact(*tests, **extra):
    return json.dumps({"framework": "pytest", "tests": list(tests), **extra}).encode()


class ArtifactParserTests(AwsTestCase):
    def setUp(self):
        super().setUp()
        self._artifact_env = mock_env({"ARTIFACT_BUCKET": BUCKET})
        self.fakes["s3"] = FakeS3()

    def tearDown(self):
        self._artifact_env()
        super().tearDown()

    def run_key(self, name, body, **event):
        self.fakes["s3"].objects[PREFIX + name] = body
        return artifact_handler.lambda_handler(s3_event(PREFIX + name) | event)

    # ---- accepted ------------------------------------------------------------------------------

    def test_tst12_artifact_is_evaluated_validated_and_published(self):
        out = self.run_key("tst-12.json", artifact(HEAVY, LIGHT))
        self.assertEqual(out["outcome"], "evaluated")
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "TST-12", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 1}])
        self.assertEqual((out["repository_id"], out["commit_sha"], out["scan_id"]),
                         ("github:AWS-env/example", SHA, "gha-4242-1"))
        entry = self.fakes["events"].entries[0]
        self.assertEqual((entry["Source"], entry["DetailType"], entry["EventBusName"]),
                         ("owner-d.artifact-parser", "detector.result.v1", "findings-hub"))
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["findings"][0]["scope_id"], "test:tests/test_api.py::test_fetch_users")
        self.assertNotIn("123456789012", entry["Detail"])  # the bucket name (account) never leaks into results
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-1"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_reads_are_range_capped(self):
        self.run_key("tst-12.json", artifact(LIGHT))
        self.assertEqual(self.fakes["s3"].calls, [(BUCKET, PREFIX + "tst-12.json",
                                                   f"bytes=0-{artifact_handler.MAX_OBJECT_BYTES}")])

    def test_settings_override_reference_maxima(self):
        out = self.run_key("tst-12.json", artifact(HEAVY, settings={
            "max_duration_seconds": 60, "max_sleep_seconds": 5, "max_network_calls": 5,
            "max_fixture_bytes": 10 ** 9, "max_setup_seconds": 10}), dry_run=True)
        self.assertEqual(out["results"][0]["findings"], 0)
        self.assertEqual(out["published"], 0)

    def test_per_test_framework_wins(self):
        out = self.run_key("tst-12.json", artifact(dict(HEAVY, framework="jest")), dry_run=True)
        self.assertIn("jest test", out["result_payloads"][0]["findings"][0]["summary"])

    def test_duplicate_and_unusable_tests_become_limitations(self):
        out = self.run_key("tst-12.json", artifact(HEAVY, HEAVY, {"duration_seconds": 1}, "x"), dry_run=True)
        self.assertEqual(out["results"][0]["scope"], 1)
        limitations = " ".join(out["result_payloads"][0]["coverage"]["limitations"])
        self.assertIn("duplicate test_id", limitations)
        self.assertIn("tests[2] has no usable test_id", limitations)
        self.assertIn("tests[3] has no usable test_id", limitations)

    def test_incomplete_test_data_is_not_reported_clean(self):
        out = self.run_key("tst-12.json", artifact({"test_id": "t::a", "duration_seconds": 1}), dry_run=True)
        self.assertEqual(out["refused"], [])
        self.assertEqual(out["results"][0]["evaluated"], 0)

    def test_scope_is_chunked_into_several_results(self):
        tests = [dict(LIGHT, test_id=f"t::case_{i}") for i in range(120)]
        out = self.run_key("tst-12.json", artifact(*tests))
        self.assertEqual(out["published"], 3)
        self.assertEqual([r["scope"] for r in out["results"]], [50, 50, 20])

    def test_direct_invoke_replay(self):
        self.fakes["s3"].objects[PREFIX + "tst-12.json"] = artifact(HEAVY)
        out = artifact_handler.lambda_handler({"bucket": BUCKET, "key": PREFIX + "tst-12.json", "dry_run": True})
        self.assertEqual((out["outcome"], out["published"]), ("evaluated", 0))

    # ---- ignored and refused -------------------------------------------------------------------

    def assert_refused(self, out, reason):
        self.assertEqual(out["outcome"], "refused")
        self.assertIn(reason, out["reason"])
        self.assertEqual(self.fakes["events"].entries, [])

    def test_unrouted_names_are_ignored_without_reading(self):
        out = self.run_key("junit.xml", b"<testsuite/>")
        self.assertEqual((out["outcome"], out["published"]), ("ignored", 0))
        self.assertEqual(self.fakes["s3"].calls, [])

    def test_bad_artifacts_are_refused(self):
        for body, reason in [(b"\xff\xfe", "not UTF-8 JSON"), (b"{not json", "not UTF-8 JSON"),
                             (b"[]", "needs"), (json.dumps({"tests": []}).encode(), "needs"),
                             (artifact(HEAVY, settings={"max_cpu": 1}), "settings may only contain"),
                             (artifact({"duration_seconds": 1}), "no usable tests"), (b"", "empty")]:
            with self.subTest(reason=reason, body=body[:20]):
                self.fakes["events"].entries.clear()
                self.assert_refused(self.run_key("tst-12.json", body), reason)

    def test_oversized_artifact_is_refused(self):
        body = b" " * artifact_handler.MAX_OBJECT_BYTES + artifact(HEAVY)
        self.assert_refused(self.run_key("tst-12.json", body), "larger than")

    def test_deleted_object_is_refused(self):
        out = artifact_handler.lambda_handler(s3_event(PREFIX + "tst-12.json"))
        self.assert_refused(out, "no longer exists")

    def test_keys_outside_the_upload_layout_are_refused(self):
        for key in ["other/tst-12.json", f"uploads/github%3Ao%2Fr/{'A' * 40}/1-1/tst-12.json",
                    f"uploads/github%3Ao%2Fr/{SHA}/run-1/tst-12.json", f"uploads/github%3Ao%2Fr/{SHA}/1-1/a/tst-12.json",
                    f"uploads/gitlab%3Ao%2Fr/{SHA}/1-1/tst-12.json", f"uploads/github%3A..%2F..%2Fx/{SHA}/1-1/tst-12.json"]:
            with self.subTest(key=key):
                self.assert_refused(artifact_handler.lambda_handler(s3_event(key)), "key")
        self.assertEqual(self.fakes["s3"].calls, [])

    # ---- AWS failures and guards ---------------------------------------------------------------

    def test_other_buckets_raise(self):
        with self.assertRaises(ValueError):
            artifact_handler.lambda_handler(s3_event(PREFIX + "tst-12.json", bucket="someone-elses-bucket"))

    def test_s3_access_errors_raise_for_retry(self):
        class Denied(FakeS3):
            def get_object(self, **kw):
                raise ClientError("AccessDenied")
        self.fakes["s3"] = Denied()
        with self.assertRaises(ClientError):
            artifact_handler.lambda_handler(s3_event(PREFIX + "tst-12.json"))

    def test_missing_bus_raises(self):
        self.fakes["events"].bus_exists = False
        self.fakes["s3"].objects[PREFIX + "tst-12.json"] = artifact(HEAVY)
        with self.assertRaises(RuntimeError):
            artifact_handler.lambda_handler(s3_event(PREFIX + "tst-12.json"))

    def test_refuses_other_regions(self):
        restore = mock_env({"AWS_REGION": "us-east-1"})
        self.addCleanup(restore)
        with self.assertRaises(RuntimeError):
            artifact_handler.lambda_handler(s3_event(PREFIX + "tst-12.json"))


if __name__ == "__main__":
    unittest.main()
