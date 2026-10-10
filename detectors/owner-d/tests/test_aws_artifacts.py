import io
import json
import unittest
import urllib.parse
from pathlib import Path

from tests.aws_fakes import NOW, SHA, AwsTestCase, ClientError, FakeTable, mock_env
from findings_hub import writer
from owner_d.aws import artifact_handler, common
from shared.contracts.validation import validate

BUCKET = "owner-d-artifacts-123456789012-ap-south-1"
PREFIX = f"uploads/{urllib.parse.quote('github:AWS-env/example', safe='')}/{SHA}/4242-1/"

HEAVY = {"test_id": "tests/test_api.py::test_fetch_users", "duration_seconds": 14.2, "sleep_seconds": 2.5,
         "network_call_count": 3, "fixture_bytes": 52428800, "setup_seconds": 4.1}
LIGHT = {"test_id": "tests/test_api.py::test_parse", "duration_seconds": 0.01, "sleep_seconds": 0,
         "network_call_count": 0, "fixture_bytes": 1024, "setup_seconds": 0.001}
MEMRAY_STATS = (Path(__file__).resolve().parent / "fixtures" / "llm16" / "memray-stats.json").read_bytes()


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

    def test_llm16_memray_stats_are_routed_evaluated_and_published(self):
        out = self.run_key("llm-16.json", MEMRAY_STATS)
        self.assertEqual((out["outcome"], out["artifact"]), ("evaluated", "llm-16.json"))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-16", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 2}])
        entry = self.fakes["events"].entries[0]
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["context"], {"max_buffered_response_bytes": 1048576})
        self.assertEqual([(f["scope_id"], f["identity"]) for f in result["findings"]], [
            ("artifact:llm-16.json", "response-read:anthropic/_response.py:_parse"),
            ("artifact:llm-16.json", "response-read:httpx/_models.py:read")])
        self.assertEqual(result["findings"][0]["evidence"][0]["locator"],
                         "llm-16.json from GitHub Actions run 4242-1: _parse:/home/runner/work/example/example/.venv/"
                         "lib/python3.12/site-packages/anthropic/_response.py:293")
        self.assertNotIn("123456789012", entry["Detail"])
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-llm16"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_llm16_settings_override_the_reference_threshold(self):
        body = json.dumps(json.loads(MEMRAY_STATS) | {"settings": {"max_buffered_response_bytes": 10 ** 9}}).encode()
        out = self.run_key("llm-16.json", body, dry_run=True)
        self.assertEqual((out["results"][0]["findings"], out["published"]), (0, 0))

    def test_llm16_unusable_memray_exports_are_refused(self):
        stats = json.loads(MEMRAY_STATS)
        for body, reason in [(b"[]", "needs a `memray stats --json` object"),
                             (artifact(HEAVY), "not a `memray stats --json` export"),
                             (json.dumps(stats | {"settings": {"max_cpu": 1}}).encode(), "settings may only contain"),
                             (json.dumps({"top_allocations_by_size": [{"location": "x", "size": 1}]}).encode(),
                              "no usable allocation sites")]:
            with self.subTest(reason=reason):
                self.fakes["events"].entries.clear()
                self.assert_refused(self.run_key("llm-16.json", body), reason)

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

    # ---- oversized results (#499) --------------------------------------------------------------

    def published_details(self):
        return [entry["Detail"] for entry in self.fakes["events"].entries]

    def test_long_flagged_test_ids_are_split_into_smaller_results_not_raised(self):
        tests = [dict(HEAVY, test_id=f"tests/test_{i:03d}.py::".ljust(500, "x")) for i in range(50)]
        self.assertTrue(all(len(t["test_id"]) == 500 for t in tests))
        out = self.run_key("tst-12.json", artifact(*tests))
        self.assertEqual((out["outcome"], out["refused"], out["errors"]), ("evaluated", [], []))
        self.assertGreater(out["published"], 1)
        self.assertEqual(sum(r["scope"] for r in out["results"]), 50)
        self.assertEqual(sum(r["findings"] for r in out["results"]), 50)
        details = self.published_details()
        self.assertEqual(len(details), out["published"])
        for detail in details:
            self.assertLessEqual(len(detail.encode("utf-8")), common.MAX_DETAIL_BYTES)
            validate(json.loads(detail))
        scoped = [s for d in details for s in json.loads(d)["scope"]]
        self.assertEqual(scoped, [f"test:{t['test_id']}" for t in tests])  # every test once, in upload order

    def test_a_single_item_whose_result_is_still_too_large_is_refused_not_raised(self):
        # One `artifact:llm-16.json` scope item cannot be split; many flagged long frames make it too large.
        frames = [{"location": f"_parse:/runner/.venv/lib/python3.12/site-packages/anthropic/{'d' * 200}/m{i}.py:7",
                   "size": 10 ** 8} for i in range(600)]
        out = self.run_key("llm-16.json", json.dumps({"top_allocations_by_size": frames}).encode())
        self.assertEqual((out["outcome"], out["published"], out["results"], out["errors"]), ("evaluated", 0, [], []))
        self.assertEqual(len(out["refused"]), 1)
        refused = out["refused"][0]
        self.assertEqual((refused["check_id"], refused["scope"]), ("LLM-16", 1))
        self.assertIn("too large for one event", refused["error"])
        self.assertEqual(self.published_details(), [])

    def test_good_items_are_published_next_to_one_that_is_too_large(self):
        route = artifact_handler.ROUTES["tst-12.json"]

        def padded(data, identity):  # one test whose result alone cannot fit an event
            module, context, scope, sources, notes = route(data, identity)
            sources[1]["data"] = dict(sources[1]["data"], framework="f" * common.MAX_DETAIL_BYTES)
            return module, context, scope, sources, notes

        self.addCleanup(artifact_handler.ROUTES.__setitem__, "tst-12.json", route)
        artifact_handler.ROUTES["tst-12.json"] = padded
        tests = [dict(HEAVY, test_id=f"t::case_{i}") for i in range(3)]
        out = self.run_key("tst-12.json", artifact(*tests))
        self.assertEqual((out["outcome"], out["errors"]), ("evaluated", []))
        self.assertEqual([r["scope"] for r in out["results"]], [1, 1])
        self.assertEqual([(r["scope"], r["check_id"]) for r in out["refused"]], [(1, "TST-12")])
        self.assertEqual(out["published"], 2)
        self.assertEqual([json.loads(d)["scope"] for d in self.published_details()], [["test:t::case_0"],
                                                                                      ["test:t::case_2"]])

    def test_many_long_artifact_notes_are_bounded_so_results_still_fit(self):
        long_id = "tests/test_dup.py::" + "y" * 480
        tests = [dict(HEAVY, test_id=long_id)] * 1000 + [dict(LIGHT, test_id=f"t::case_{i}") for i in range(60)]
        out = self.run_key("tst-12.json", artifact(*tests), dry_run=True)
        self.assertEqual((out["refused"], out["errors"]), ([], []))
        self.assertEqual([r["scope"] for r in out["results"]], [50, 11])
        for result in out["result_payloads"]:
            detail = json.dumps(result, ensure_ascii=False)
            self.assertLessEqual(len(detail.encode("utf-8")), common.MAX_DETAIL_BYTES)
            limitations = result["coverage"]["limitations"]
            self.assertTrue(any("duplicate test_id" in item for item in limitations))
            self.assertIn("more artifact note(s) omitted to keep each result within the 240000-byte event limit",
                          limitations[-1])

    def test_results_that_already_fit_are_unchanged(self):
        tests = [dict(HEAVY, test_id=f"tests/test_{i:03d}.py::" + "x" * 100) for i in range(50)]
        out = self.run_key("tst-12.json", artifact(*tests))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "TST-12", "status": "completed", "scope": 50, "evaluated": 50,
                                           "findings": 50}])

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
