"""owner-d-log-analyzer / owner-d-trace-analyzer plumbing and the normalizer registry (stubbed boto3)."""

import importlib.util
import json
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

from tests.aws_fakes import ROLE, AwsTestCase, Context, FakeLogs, FakeXray

from owner_d.aws import common, log_handler, registry, trace_handler
from shared.contracts.validation import validate

GROUPS = [{"logGroupName": f"/aws/lambda/owner-d-{n}", "retentionInDays": 7, "storedBytes": 10,
           "logGroupClass": "STANDARD", "creationTime": 1700000000000,
           "arn": f"arn:aws:logs:ap-south-1:123456789012:log-group:/aws/lambda/owner-d-{n}:*",
           "logGroupArn": f"arn:aws:logs:ap-south-1:123456789012:log-group:/aws/lambda/owner-d-{n}"}
          for n in ("a", "b", "c")] + [{"logGroupName": "/aws/lambda/other", "storedBytes": 5}]


def fake_detector(check_id, *, query=None):
    """A minimal contract detector: evaluates every scope item that has a source, no findings."""
    module = types.ModuleType(f"fake_{check_id.replace('-', '_').lower()}")
    module.CHECK_ID, module.DETECTOR_VERSION = check_id, "0.0.1"
    if query:
        module.LOGS_INSIGHTS_QUERY = query

    def evaluate(payload):
        covered = [s for s in payload["scope"] if any(src["scope_id"] == s for src in payload["sources"])]
        status = "completed" if len(covered) == len(payload["scope"]) else "partial" if covered else "unavailable"
        return {**{k: payload[k] for k in ("schema_version", "repository_id", "scan_id", "commit_sha", "check_id",
                                           "detector_version", "context", "scope")},
                "kind": "result", "status": status, "findings": [], "measurements": [],
                "coverage": {"evaluated_scope": covered, "limitations": ["fake detector"]}}

    module.evaluate = evaluate
    return module


def describe_log_groups_like(pages, tags=None):
    """Same interface as owner_d.obs07.normalize_describe_log_groups."""
    out = {}
    for page in pages:
        for group in page["logGroups"]:
            out[group["logGroupName"]] = {"resource_id": group["logGroupName"], "log_group_arn": group.get("logGroupArn"),
                                          "retention_in_days": group.get("retentionInDays"),
                                          "tags": None if tags is None else tags.get(group["logGroupName"])}
    return out


class Registered:
    """Temporarily register a fake module + check in the registry."""

    def __init__(self, module, check):
        self.module, self.check = module, check

    def __enter__(self):
        sys.modules[self.module.__name__] = self.module
        self.patch = mock.patch.object(registry, "CHECKS", registry.CHECKS + (self.check,))
        self.patch.start()

    def __exit__(self, *exc):
        self.patch.stop()
        sys.modules.pop(self.module.__name__, None)


class LogHandlerTests(AwsTestCase):
    def test_log_groups_through_the_obs07_style_adapter(self):
        self.fakes["logs"] = FakeLogs(GROUPS, tags={GROUPS[0]["logGroupArn"]: {"team": "d"}})
        module = fake_detector("OBS-07")
        module.normalize_describe_log_groups = describe_log_groups_like
        check = registry.TelemetryCheck("OBS-07", module.__name__, "log_groups",
                                        normalizer=f"{module.__name__}:normalize_describe_log_groups",
                                        adapter="describe_log_groups")
        with Registered(module, check):
            out = log_handler.lambda_handler(self.base_event(log_groups={"prefix": "/aws/lambda/", "include_tags": True}))
        self.assertEqual(out["results"], [{"check_id": "OBS-07", "status": "completed", "scope": 4, "evaluated": 4,
                                           "findings": 0}])
        entry = self.fakes["events"].entries[0]
        self.assertEqual(entry["Source"], "owner-d.log-analyzer")
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertIn("ListTagsForResource failed for /aws/lambda/owner-d-b (AccessDeniedException); tags not collected",
                      result["coverage"]["limitations"])
        self.assertNotIn("123456789012", entry["Detail"])
        self.assertEqual(result["context"]["collection"]["source"], "cloudwatch-logs-describeloggroups")

    def test_adapter_builds_account_free_sources(self):
        raw = {"pages": [{"logGroups": GROUPS[:1]}], "tags": None, "region": "ap-south-1"}
        out = registry.normalize(describe_log_groups_like, raw, {}, "describe_log_groups")
        source = out["sources"][0]
        self.assertEqual((source["scope_id"], source["locator"]),
                         ("resource:/aws/lambda/owner-d-a", "logs://ap-south-1/log-group//aws/lambda/owner-d-a"))
        self.assertNotIn("log_group_arn", source["data"])

    def test_list_metrics_adapter_matches_the_obs06_interface(self):
        def normalize_list_metrics(pages):
            return {"window_days": 14, "listing_complete": "NextToken" not in pages[-1], "page_count": len(pages),
                    "metrics": {"resource:metric/OwnerD/Demo/Latency": {"namespace": "OwnerD/Demo",
                                                                          "metric_name": "Latency", "series_count": 3}}}

        out = registry.normalize(normalize_list_metrics, {"pages": [{"Metrics": [], "NextToken": "x"}]}, {}, "list_metrics")
        self.assertEqual(out["sources"][0]["locator"], "cloudwatch:ListMetrics/OwnerD/Demo/Latency")
        self.assertEqual(out["scope"], ["resource:metric/OwnerD/Demo/Latency"])
        self.assertIn("lower bounds", out["limitations"][0])

    def test_describe_log_groups_pages_are_bounded(self):
        logs = FakeLogs(GROUPS, page_size=1)
        pages, truncated = log_handler.describe_log_groups(logs, max_pages=2)
        self.assertEqual((len(pages), truncated), (2, True))
        self.assertEqual(sum(1 for c in logs.calls if c[0] == "describe_log_groups"), 2)

    def test_insights_query_over_allowlisted_groups(self):
        logs = self.fakes["logs"] = FakeLogs(GROUPS, statuses=["Scheduled", "Running", "Complete"],
                                             rows=[{"msg": "retry", "count": "12"}])
        module = fake_detector("OBS-11", query="stats count(*) as count by msg")
        module.normalize_logs_insights = lambda raw, *, settings: [
            {"source_id": "q", "scope_id": "query:retries", "kind": "telemetry", "locator": "logs-insights:retries",
             "data": {"rows": raw["rows"], "bytes": raw["statistics"]["bytesScanned"]}}]
        with Registered(module, registry.TelemetryCheck("OBS-11", module.__name__, "logs_insights")):
            out = log_handler.lambda_handler(self.base_event(logs={"lookback_hours": 2}, dry_run=True), Context())
        self.assertEqual(out["results"][0]["status"], "completed")
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual(start["logGroupNames"], [g["logGroupName"] for g in GROUPS[:3]])  # /aws/lambda/other excluded
        self.assertEqual(start["endTime"] - start["startTime"], 7200)
        self.assertEqual((start["queryString"], start["limit"]), ("stats count(*) as count by msg", 1000))
        self.assertEqual(out["collection"]["OBS-11"]["bytes_scanned"], 2048.0)

    def test_query_refuses_unlisted_groups_and_long_windows(self):
        logs = FakeLogs(GROUPS)
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            log_handler.resolve_query_groups(logs, {"log_groups": ["/aws/lambda/other"]}, log_handler.allowlist())
        with self.assertRaisesRegex(ValueError, "exact log group names"):
            log_handler.resolve_query_groups(logs, {"log_groups": ["/aws/lambda/owner-d-*"]}, log_handler.allowlist())
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            log_handler.resolve_query_groups(logs, {}, [])
        with self.assertRaises(ValueError):
            log_handler.collect_logs_insights(self.base_event(logs={"lookback_hours": 169}), common.Readers(),
                                              module=fake_detector("OBS-11", query="fields @message"))

    def test_query_timeout_stops_the_query(self):
        logs = FakeLogs(GROUPS, statuses=["Running"])
        clock = iter(range(0, 1000, 10))
        common._monotonic = lambda: next(clock)
        with self.assertRaises(TimeoutError):
            log_handler.run_insights_query(logs, log_groups=["/aws/lambda/owner-d-a"], query="fields @message",
                                           start=common._now(), end=common._now(), limit=10, timeout_seconds=30)
        self.assertEqual(logs.calls[-1], ("stop_query", "q-1"))
        failed = FakeLogs(GROUPS, statuses=["Failed"])
        with self.assertRaisesRegex(RuntimeError, "Failed"):
            log_handler.run_insights_query(failed, log_groups=["/aws/lambda/owner-d-a"], query="fields @message",
                                           start=common._now(), end=common._now(), limit=10, timeout_seconds=30)

    def test_query_timeout_respects_remaining_lambda_time(self):
        self.fakes["logs"] = FakeLogs(GROUPS)
        with self.assertRaises(TimeoutError):
            log_handler.collect_logs_insights(self.base_event(), common.Readers(), deadline=common.Deadline(Context(10_000)),
                                              module=fake_detector("OBS-11", query="fields @message"))

    def test_probe_through_the_read_only_role(self):
        self.fakes["logs"] = FakeLogs(GROUPS)
        out = log_handler.lambda_handler({"probe": ["log_groups"], "role_arn": ROLE})
        self.assertEqual(out["probe"]["log_groups"]["log_groups"], 4)
        self.assertIn(("logs", "ap-south-1", "ASIA1"), self.created)
        self.assertEqual(self.fakes["events"].entries, [])

    def test_no_registered_checks_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "no checks are registered"):
            log_handler.lambda_handler(self.base_event())


class TraceHandlerTests(AwsTestCase):
    def test_traces_are_fetched_in_batches_of_five_and_bounded(self):
        xray = FakeXray([f"1-{i:08x}-abc" for i in range(30)], page_size=5)
        summaries, traces, unprocessed, truncated, notes = trace_handler.fetch_traces(
            xray, start=common._now(), end=common._now(), filter_expression='service("agent")', max_traces=12, max_pages=10)
        self.assertEqual((len(summaries), len(traces), truncated), (12, 12, True))
        batches = [ids for name, ids in xray.calls if name == "batch_get_traces"]
        self.assertEqual([len(b) for b in batches], [5, 5, 2])
        self.assertEqual(xray.calls[0][1]["FilterExpression"], 'service("agent")')
        self.assertFalse(xray.calls[0][1]["Sampling"])
        xray = FakeXray([f"1-{i:08x}-abc" for i in range(30)], page_size=5)
        _, _, _, truncated, notes = trace_handler.fetch_traces(xray, start=common._now(), end=common._now(),
                                                               max_traces=100, max_pages=2)
        self.assertTrue(truncated)
        self.assertEqual(sum(1 for name, _ in xray.calls if name == "get_trace_summaries"), 2)

    def test_window_over_24_hours_is_refused(self):
        with self.assertRaises(ValueError):
            trace_handler.collect_traces(self.base_event(xray={"lookback_minutes": 24 * 60 + 1}), common.Readers())

    def test_trace_check_end_to_end(self):
        self.fakes["xray"] = FakeXray(["1-a", "1-b"])
        module = fake_detector("LLM-10")
        module.normalize_traces = lambda raw: [{"source_id": f"trace:{t['Id']}", "scope_id": "service:agent",
                                                "kind": "telemetry", "locator": f"xray:{t['Id']}",
                                                "data": {"segments": len(t["Segments"])}} for t in raw["Traces"][:1]]
        with Registered(module, registry.TelemetryCheck("LLM-10", module.__name__, "traces")):
            out = trace_handler.lambda_handler(self.base_event(role_arn=ROLE, xray={"lookback_minutes": 30}))
        self.assertEqual(out["published"], 1)
        self.assertEqual(self.fakes["events"].entries[0]["Source"], "owner-d.trace-analyzer")
        self.assertIn(("xray", "ap-south-1", "ASIA1"), self.created)


class RegistryTests(unittest.TestCase):
    def test_select_and_load(self):
        self.assertEqual([c.check_id for c in registry.select({"cpu_metrics": None})], ["INF-01"])
        self.assertEqual(registry.select({"traces": None}), [])
        with self.assertRaises(ValueError):
            registry.select({"cpu_metrics": None}, ["LLM-10"])
        module, normalize = registry.load(registry.CHECKS[0])
        self.assertEqual((module.CHECK_ID, normalize.__name__), ("INF-01", "normalize_cpu_metrics"))
        with self.assertRaises(ValueError):
            registry.load(registry.TelemetryCheck("INF-01", "owner_d.inf01", "cpu_metrics"))  # no normalize_cpu_metrics

    def test_settings_precedence(self):
        check = registry.CHECKS[0]
        module = types.SimpleNamespace(DEFAULT_SETTINGS={"min_window_days": 7})
        merged = registry.settings(check, module, {"settings": {"INF-01": {"min_sample_count": 10}}})
        self.assertEqual((merged["min_window_days"], merged["min_sample_count"]), (7, 10))
        with self.assertRaises(ValueError):
            registry.settings(check, module, {"settings": {"INF-01": 3}})

    def test_every_registry_comment_names_a_known_source_and_adapter(self):
        text = Path(registry.__file__).read_text(encoding="utf-8")
        for check_id, source in (("OBS-06", "metrics"), ("OBS-07", "log_groups"), ("LLM-10", "traces")):
            line = next(l for l in text.splitlines() if f'TelemetryCheck("{check_id}"' in l)
            self.assertIn(f'"{source}"', line)
            self.assertIn(source, registry.SOURCES)
        self.assertEqual(set(registry.ADAPTERS), {"describe_log_groups", "list_metrics", "xray_traces"})

    def test_xray_adapter_uses_the_module_telemetry_sources_and_flags_too_few_traces(self):
        module = types.ModuleType("fake_llm10_adapter")

        def normalize_xray_traces(traces):
            return {"traces_received": len(traces), "skipped_traces": [{"trace_id": "1-x", "reason": "bad"}],
                    "entrypoints": {"agent": {"entrypoint": "agent"}}}

        def telemetry_sources(normalized, locator="aws-xray:BatchGetTraces"):
            return ["entrypoint:agent"], [{"source_id": "xray:agent", "scope_id": "entrypoint:agent", "kind": "telemetry",
                                           "locator": f"{locator}/agent", "data": normalized["entrypoints"]["agent"]}]

        normalize_xray_traces.__module__ = module.__name__
        module.telemetry_sources = telemetry_sources
        sys.modules[module.__name__] = module
        try:
            out = registry.normalize(normalize_xray_traces, {"Traces": [{}] * 4}, registry.LLM10_DEFAULTS, "xray_traces")
        finally:
            sys.modules.pop(module.__name__)
        self.assertEqual((out["scope"], out["sources"][0]["locator"]), (["entrypoint:agent"], "aws-xray:BatchGetTraces/agent"))
        self.assertIn("only 4 traces were read but min_traces is 10", out["limitations"][0])
        self.assertIn("1 traces could not be parsed", out["limitations"][1])

    @unittest.skipUnless(importlib.util.find_spec("owner_d.llm10"), "LLM-10 detector not merged yet")
    def test_real_llm10_normalizer_through_the_adapter(self):  # pragma: no cover - runs once #396 lands
        from owner_d import llm10
        out = registry.normalize(llm10.normalize_xray_traces, {"Traces": []}, registry.LLM10_DEFAULTS, "xray_traces")
        self.assertEqual(out["scope"], [])

    @unittest.skipUnless(importlib.util.find_spec("owner_d.obs07"), "OBS-07 detector not merged yet")
    def test_real_obs07_normalizer_through_the_adapter(self):  # pragma: no cover - runs once #379 lands
        from owner_d import obs07
        raw = {"pages": [{"logGroups": GROUPS}], "tags": None, "region": "ap-south-1"}
        out = registry.normalize(obs07.normalize_describe_log_groups, raw, {}, "describe_log_groups")
        self.assertEqual(len(out["sources"]), 4)

    @unittest.skipUnless(importlib.util.find_spec("owner_d.obs06"), "OBS-06 detector not merged yet")
    def test_real_obs06_normalizer_through_the_adapter(self):  # pragma: no cover - runs once #380 lands
        from owner_d import obs06
        page = {"Metrics": [{"Namespace": "OwnerD/Demo", "MetricName": "Latency",
                             "Dimensions": [{"Name": "request_id", "Value": "r1"}]}]}
        out = registry.normalize(obs06.normalize_list_metrics, {"pages": [page]}, {}, "list_metrics")
        self.assertEqual(out["scope"], [obs06.scope_id_for("OwnerD/Demo", "Latency")])


if __name__ == "__main__":
    unittest.main()
