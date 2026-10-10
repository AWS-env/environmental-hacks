"""owner-d-log-analyzer / owner-d-trace-analyzer plumbing and the normalizer registry (stubbed boto3)."""

import json
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

from tests.aws_fakes import NOW, ROLE, AwsTestCase, Context, FakeCloudWatch, FakeLogs, FakeTable, FakeXray

from findings_hub import writer
from owner_d import obs06
from owner_d.aws import common, log_handler, registry, telemetry_handler, trace_handler
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
    """Temporarily register a fake module + check in the registry, replacing a real check with the same id."""

    def __init__(self, module, check):
        self.module, self.check = module, check

    def __enter__(self):
        sys.modules[self.module.__name__] = self.module
        kept = tuple(c for c in registry.CHECKS if c.check_id != self.check.check_id)
        self.patch = mock.patch.object(registry, "CHECKS", kept + (self.check,))
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
            out = log_handler.lambda_handler(self.base_event(checks=["OBS-07"],
                                                             log_groups={"prefix": "/aws/lambda/", "include_tags": True}))
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
            out = log_handler.lambda_handler(self.base_event(checks=["OBS-11"], logs={"lookback_hours": 2}, dry_run=True),
                                             Context())
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
        with mock.patch.object(registry, "CHECKS", tuple(c for c in registry.CHECKS
                                                         if c.source not in ("log_groups", "logs_insights"))):
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
        self.assertEqual([c.check_id for c in registry.select({"traces": None})], ["LLM-10", "LLM-05"])
        self.assertEqual([c.check_id for c in registry.select({"cpu_metrics": None, "metrics": None})],
                         ["INF-01", "OBS-06"])
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

    def test_every_registered_check_loads_with_a_known_source_and_adapter(self):
        wired = {c.check_id: (c.source, c.adapter) for c in registry.CHECKS}
        self.assertEqual(wired, {"INF-01": ("cpu_metrics", None), "OBS-06": ("metrics", "list_metrics"),
                                 "OBS-07": ("log_groups", "describe_log_groups"), "OBS-11": ("logs_insights", None),
                                 "LLM-10": ("traces", "xray_traces"), "OBS-17": ("logs_insights", None),
                                 "LLM-05": ("traces", "xray_traces"), "INF-04": ("invocation_metrics", None)})
        for check in registry.CHECKS:
            module, normalize = registry.load(check)
            self.assertEqual(module.CHECK_ID, check.check_id)
            self.assertIn(check.source, registry.SOURCES)
            self.assertTrue(check.adapter is None or check.adapter in registry.ADAPTERS)
            # registry defaults supply every setting the detector requires in its context
            self.assertTrue(set(getattr(module, "SETTING_KEYS", ())) <= set(check.defaults), check.check_id)
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

    def test_real_llm10_normalizer_through_the_adapter(self):
        from owner_d import llm10
        out = registry.normalize(llm10.normalize_xray_traces, {"Traces": []}, registry.LLM10_DEFAULTS, "xray_traces")
        self.assertEqual(out["scope"], [])

    def test_real_obs07_normalizer_through_the_adapter(self):
        from owner_d import obs07
        raw = {"pages": [{"logGroups": GROUPS}], "tags": None, "region": "ap-south-1"}
        out = registry.normalize(obs07.normalize_describe_log_groups, raw, {}, "describe_log_groups")
        self.assertEqual(len(out["sources"]), 4)

    def test_real_obs06_normalizer_through_the_adapter(self):
        from owner_d import obs06
        page = {"Metrics": [{"Namespace": "OwnerD/Demo", "MetricName": "Latency",
                             "Dimensions": [{"Name": "request_id", "Value": "r1"}]}]}
        out = registry.normalize(obs06.normalize_list_metrics, {"pages": [page]}, {}, "list_metrics")
        self.assertEqual(out["scope"], [obs06.scope_id_for("OwnerD/Demo", "Latency")])


LLM10_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm10"
LLM05_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm05"


class FixtureXray(FakeXray):
    """GetTraceSummaries/BatchGetTraces over a synthetic BatchGetTraces fixture (LLM-10 by default)."""

    def __init__(self, name, directory=LLM10_FIXTURES):
        self.by_id = {t["Id"]: t for t in json.loads((directory / name).read_text())["Traces"]}
        super().__init__(list(self.by_id))

    def batch_get_traces(self, TraceIds, **kw):
        assert len(TraceIds) <= 5, "BatchGetTraces accepts at most 5 ids"
        self.calls.append(("batch_get_traces", tuple(TraceIds)))
        return {"Traces": [self.by_id[i] for i in TraceIds], "UnprocessedTraceIds": []}


class RealChecksThroughTheAnalyzersTests(AwsTestCase):
    """OBS-06, OBS-07 and LLM-10 as registered: real collector -> real normalizer -> real detector ->
    validate_pair -> PutEvents, and the published event is accepted by the findings-hub writer."""

    def stored(self, index=0):
        entry = self.fakes["events"].entries[index]
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertNotIn("123456789012", entry["Detail"])
        summary = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                 "id": f"evt-{index}"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(summary["outcome"], "stored")
        return entry, result

    def test_obs06_high_cardinality_metric_is_published(self):
        metrics_page = [{"Namespace": "OwnerD/Demo", "MetricName": "Latency", "OwningAccounts": ["123456789012"],
                         "Dimensions": [{"Name": "request_id", "Value": f"req-{i:04d}"}]} for i in range(12)]
        self.fakes["cloudwatch"] = FakeCloudWatch(list_pages=[metrics_page])
        out = telemetry_handler.lambda_handler(self.base_event(checks=["OBS-06"],
                                                               list_metrics={"namespace": "OwnerD/Demo"}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "OBS-06", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        entry, result = self.stored()
        self.assertEqual((entry["Source"], result["check_id"]), ("owner-d.telemetry-analyzer", "OBS-06"))
        self.assertEqual(result["findings"][0]["scope_id"], obs06.scope_id_for("OwnerD/Demo", "Latency"))

    def test_telemetry_analyzer_runs_inf01_obs06_and_inf04_by_default(self):
        self.fakes["cloudwatch"] = FakeCloudWatch(list_pages=[[]])
        out = telemetry_handler.lambda_handler(self.base_event(discover={}, dry_run=True))
        self.assertEqual(sorted(s["check_id"] for s in out["skipped"]), ["INF-01", "INF-04", "OBS-06"])
        # INF-04 reads only listed Lambda functions: with discovery alone it makes no GetMetricData call
        self.assertFalse(any(name == "get_metric_data" for name, _ in self.fakes["cloudwatch"].calls))

    def test_obs07_never_expiring_large_log_group_is_published(self):
        big = {"logGroupName": "/aws/lambda/owner-d-big", "storedBytes": 5 * 1024 ** 3, "logGroupClass": "STANDARD",
               "creationTime": 1700000000000,
               "logGroupArn": "arn:aws:logs:ap-south-1:123456789012:log-group:/aws/lambda/owner-d-big"}
        self.fakes["logs"] = FakeLogs([big] + GROUPS[:1], tags={big["logGroupArn"]: {"team": "d"},
                                                                GROUPS[0]["logGroupArn"]: {}})
        out = log_handler.lambda_handler(self.base_event(checks=["OBS-07"],
                                                         log_groups={"prefix": "/aws/lambda/", "include_tags": True}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"][0]["check_id"], "OBS-07")
        self.assertEqual(out["results"][0]["findings"], 1)
        entry, result = self.stored()
        self.assertEqual(entry["Source"], "owner-d.log-analyzer")
        self.assertEqual(result["findings"][0]["scope_id"], "resource:/aws/lambda/owner-d-big")

    def test_llm10_agent_loop_traces_are_published(self):
        self.fakes["xray"] = FixtureXray("positive.traces.json")
        out = trace_handler.lambda_handler(self.base_event(checks=["LLM-10"], xray={"lookback_minutes": 60},
                                                           settings={"LLM-10": {"min_traces": 1}}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"][0]["check_id"], "LLM-10")
        self.assertGreaterEqual(out["results"][0]["findings"], 1)
        entry, result = self.stored()
        self.assertEqual((entry["Source"], result["context"]["min_traces"]), ("owner-d.trace-analyzer", 1))

    def test_llm05_redundant_chained_calls_are_published(self):
        self.fakes["xray"] = FixtureXray("positive.traces.json", LLM05_FIXTURES)
        out = trace_handler.lambda_handler(self.base_event(checks=["LLM-05"], xray={"lookback_minutes": 60},
                                                           settings={"LLM-05": {"min_traces": 3}}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-05", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 2}])
        entry, result = self.stored()
        self.assertEqual(entry["Source"], "owner-d.trace-analyzer")
        self.assertEqual({f["identity"] for f in result["findings"]},
                         {"consecutive-identical-calls", "repeated-chain-requests"})
        self.assertEqual((result["context"]["min_traces"], result["context"]["min_repeat_share"]), (3, 0.5))
        self.assertEqual(result["context"]["collection"]["source"], "xray-batchgettraces")
        self.assertNotIn("Summarise the open incidents", entry["Detail"])  # hashes only, never prompt content

    def test_trace_analyzer_runs_llm10_and_llm05_on_one_collection(self):
        xray = FixtureXray("positive.traces.json", LLM05_FIXTURES)
        self.fakes["xray"] = xray
        out = trace_handler.lambda_handler(self.base_event(xray={"lookback_minutes": 60}, dry_run=True,
                                                           settings={"LLM-10": {"min_traces": 3},
                                                                     "LLM-05": {"min_traces": 3}}))
        self.assertEqual(sum(1 for name, _ in xray.calls if name == "get_trace_summaries"), 1)
        statuses = {r["check_id"]: (r["status"], r["findings"]) for r in out["results"]}
        self.assertEqual(statuses, {"LLM-10": ("completed", 0), "LLM-05": ("completed", 2)})
        self.assertEqual((out["published"], out["refused"], out["errors"]), (0, [], []))


if __name__ == "__main__":
    unittest.main()
