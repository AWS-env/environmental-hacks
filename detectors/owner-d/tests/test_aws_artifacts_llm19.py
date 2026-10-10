"""Artifact parser routing for LLM-19 (`llm-19.json`), following tests/test_aws_artifacts.py (TST-12)."""

import json

from tests.aws_fakes import NOW, SHA, AwsTestCase, FakeTable, mock_env
from tests.test_aws_artifacts import BUCKET, PREFIX, FakeS3, s3_event
from findings_hub import writer
from owner_d.aws import artifact_handler
from shared.contracts.validation import validate

SATURATED = {"server_id": "llama-8b-prod", "engine": "vllm", "window_seconds": 3600, "requests": 12000,
             "preemptions": 840, "kv_cache_usage_max": 0.99, "prefix_cache_hit_rate": 0.4}
HEALTHY = {"server_id": "qwen-7b-prod", "engine": "vllm", "window_seconds": 3600, "requests": 5000,
           "preemptions": 0, "kv_cache_usage_max": 0.7}


def metrics(*servers, **extra):
    return json.dumps({"servers": list(servers), **extra}).encode()


class Llm19ArtifactRoutingTests(AwsTestCase):
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

    def assert_refused(self, out, reason):
        self.assertEqual(out["outcome"], "refused")
        self.assertIn(reason, out["reason"])
        self.assertEqual(self.fakes["events"].entries, [])

    def test_llm19_artifact_is_evaluated_validated_and_published(self):
        out = self.run_key("llm-19.json", metrics(SATURATED, HEALTHY))
        self.assertEqual(out["outcome"], "evaluated")
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-19", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 2}])
        self.assertEqual((out["repository_id"], out["commit_sha"], out["scan_id"], out["artifact"]),
                         ("github:AWS-env/example", SHA, "gha-4242-1", "llm-19.json"))
        entry = self.fakes["events"].entries[0]
        self.assertEqual((entry["Source"], entry["DetailType"]), ("owner-d.artifact-parser", "detector.result.v1"))
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual({(f["scope_id"], f["identity"]) for f in result["findings"]},
                         {("inference:llama-8b-prod", "preemptions"),
                          ("inference:llama-8b-prod", "kv-cache-saturation")})
        self.assertNotIn("123456789012", entry["Detail"])
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-llm19"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_llm19_settings_override_and_limitations(self):
        out = self.run_key("llm-19.json", metrics(SATURATED, SATURATED, {"engine": "vllm"},
                                                  settings={"max_preemption_ratio": 0.5, "max_kv_cache_usage": 1}),
                           dry_run=True)
        self.assertEqual((out["published"], out["results"][0]["findings"], out["results"][0]["scope"]), (0, 0, 1))
        limitations = " ".join(out["result_payloads"][0]["coverage"]["limitations"])
        self.assertIn("duplicate server_id", limitations)
        self.assertIn("servers[2] has no usable server_id", limitations)

    def test_llm19_incomplete_server_data_is_not_reported_clean(self):
        out = self.run_key("llm-19.json", metrics({"server_id": "a", "engine": "vllm"}), dry_run=True)
        self.assertEqual(out["refused"], [])
        self.assertEqual(out["results"][0]["status"], "unavailable")
        self.assertEqual(out["results"][0]["evaluated"], 0)

    def test_llm19_bad_artifacts_are_refused(self):
        for body, reason in [(b"[]", "needs"), (metrics(), "needs"), (b"{oops", "not UTF-8 JSON"),
                             (metrics(SATURATED, extra=1), "unknown top-level fields"),
                             (metrics(SATURATED, settings={"max_tests": 1}), "settings may only contain"),
                             (metrics({"engine": "vllm"}), "no usable servers")]:
            with self.subTest(reason=reason):
                self.fakes["events"].entries.clear()
                self.assert_refused(self.run_key("llm-19.json", body), reason)

    def test_llm19_huge_integers_never_raise_or_lose_the_upload(self):
        # A ~400-digit JSON integer overflowed float math, failed the payload and sent the event to the DLQ.
        huge = 10 ** 400
        body = metrics(SATURATED | {"window_seconds": huge}, HEALTHY | {"preemptions": huge},
                       HEALTHY | {"server_id": "ctx", "max_model_len": huge, "max_request_tokens": 6000},
                       SATURATED | {"server_id": "ok"})
        out = self.run_key("llm-19.json", body)
        self.assertEqual(out["outcome"], "evaluated")
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-19", "status": "partial", "scope": 4, "evaluated": 1,
                                           "findings": 2}])
        result = json.loads(self.fakes["events"].entries[0]["Detail"])
        validate(result)
        limitations = " ".join(result["coverage"]["limitations"])
        for reason in ("window_seconds must be a positive number of at most 1e15",
                       "preemptions must be a nonnegative integer of at most 1e15",
                       "max_model_len must be a positive integer of at most 1e15"):
            self.assertIn(reason, limitations)
        for settings in ({"max_context_headroom_ratio": huge}, {"min_requests": huge}):
            with self.subTest(settings=settings):
                self.fakes["events"].entries.clear()
                self.assert_refused(self.run_key("llm-19.json", metrics(SATURATED, settings=settings)),
                                    "invalid settings")

    def test_tst12_route_is_unchanged(self):
        self.assertIs(artifact_handler.ROUTES["tst-12.json"], artifact_handler.tst12_inputs)
        self.assertIs(artifact_handler.ROUTES["llm-19.json"], artifact_handler.llm19_inputs)

