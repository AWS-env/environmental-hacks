"""LLM-17 static provisioning: the Logs Insights query, the normalizer, the detector and the log-analyzer route.

Inputs are synthetic Logs Insights stats rows shaped like GetQueryResults output (values as strings, @log as
`<account>:<log group>`); Lambda memory values are in bytes of "MB" (10^6), as in the AWS sample query."""

import copy
import json
import unittest
from pathlib import Path

from tests.aws_fakes import NOW, AwsTestCase, FakeLogs, FakeTable

from findings_hub import writer
from owner_d import cli, llm17
from owner_d.aws import log_handler, registry
from shared.contracts.validation import validate, validate_pair

ACCOUNT = "123456789012"  # AWS documentation placeholder
REPO = "github:AWS-env/example"
FN = "/aws/lambda/owner-d-agent-router"
SETTINGS = dict(llm17.REFERENCE_SETTINGS)
WINDOW = {"start": "2026-10-09T12:00:00Z", "end": "2026-10-10T12:00:00Z"}
MB = 1_000_000


def lambda_row(group, memory_mb, invocations, peak_mb, p99_mb=None, avg_mb=None, last_seen="2026-10-10 11:00:00.000"):
    p99_mb = peak_mb if p99_mb is None else p99_mb
    avg_mb = p99_mb if avg_mb is None else avg_mb
    return {"@log": f"{ACCOUNT}:{group}", "l17_provisioned": str(memory_mb * MB), "invocations": str(invocations),
            "peak_used": str(peak_mb * MB), "p99_used": str(p99_mb * MB), "avg_used": str(avg_mb * MB),
            "first_seen": "2026-10-09 12:05:00.000", "last_seen": last_seen}


def agent_row(group, agent, kind, provisioned, invocations, peak, p99=None, avg=None):
    p99 = peak if p99 is None else p99
    avg = p99 if avg is None else avg
    return {"@log": f"{ACCOUNT}:{group}", "l17_agent": agent, "l17_kind": kind, "l17_provisioned": str(provisioned),
            "invocations": str(invocations), "peak_used": str(peak), "p99_used": str(p99), "avg_used": str(avg),
            "first_seen": "2026-10-09 12:05:00.000", "last_seen": "2026-10-10 11:00:00.000"}


def raw(rows, groups, **extra):
    return {"rows": rows, "log_groups": groups, "window": dict(WINDOW), "truncated": False, "region": "ap-south-1",
            **extra}


def payload_for(normalized, settings=None, scope=None):
    settings = dict(SETTINGS if settings is None else settings)
    return {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "scan-1",
            "commit_sha": "0123456789abcdef0123456789abcdef01234567", "check_id": "LLM-17",
            "detector_version": llm17.DETECTOR_VERSION, "context": settings,
            "scope": scope or normalized["scope"], "sources": normalized["sources"]}


def run(raw_data, settings=None, scope=None):
    normalized = registry.normalize(llm17.normalize_logs_insights, raw_data, dict(SETTINGS if settings is None
                                                                                  else settings))
    payload = payload_for(normalized, settings, scope)
    result = llm17.evaluate(payload)
    validate_pair(payload, result)
    return result, normalized


def lam(group):
    return llm17.scope_id_for(group)


def agent(group, name, kind):
    return llm17.scope_id_for(group, name, kind)


def notes(result):
    return " | ".join(result["coverage"]["limitations"])


class Llm17PositiveTests(unittest.TestCase):
    def test_oversized_lambda_memory_is_flagged_with_its_numbers(self):
        result, normalized = run(raw([lambda_row(FN, 1024, 340, 180, 150, 120)], [FN]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [lam(FN)])
        [finding] = result["findings"]
        self.assertEqual((finding["scope_id"], finding["identity"], finding["confidence"]),
                         (lam(FN), "lambda-memory", "medium"))
        self.assertIn(f"Lambda log group {FN} provisions 1024 MB of memory, but its peak use over 340 invocations",
                      finding["summary"])
        self.assertIn("was 180 MB (17.6% of provisioned; p99 150, average 120), below the 30.0% limit",
                      finding["summary"])
        self.assertIn("844 MB stayed unused even at the peak", finding["summary"])
        self.assertEqual({e["field"]: e["value"] for e in finding["evidence"]},
                         {"provisioned": 1024, "peak_used": 180, "p99_used": 150, "invocations": 340,
                          "window_hours": 24.0})
        data = normalized["sources"][0]["data"]
        self.assertEqual((data["unit"], data["capacity_kind"], data["agent"]), ("MB", "memory", None))
        self.assertIn("Power Tuning", finding["recommendation"])

    def test_agent_pool_sized_for_the_theoretical_max_is_flagged(self):
        rows = [agent_row(FN, "support_agent", "workers", 64, 120, 6, 6, 2.1)]
        result, _ = run(raw(rows, [FN]))
        [finding] = result["findings"]
        self.assertEqual((finding["scope_id"], finding["identity"]),
                         (agent(FN, "support_agent", "workers"), "agent-capacity"))
        self.assertIn(f"Agent support_agent in log group {FN} provisions 64 workers, but its peak use over 120 "
                      "logged runs", finding["summary"])
        self.assertIn("was 6 workers (9.4% of provisioned; p99 6, average 2.1)", finding["summary"])
        self.assertIn("autoscaling", finding["recommendation"])

    def test_each_workload_is_its_own_scope_item(self):
        rows = [lambda_row(FN, 2048, 200, 300), agent_row(FN, "planner", "concurrency", 50, 200, 40),
                agent_row(FN, "planner", "max_tokens", 32000, 200, 2000)]
        result, normalized = run(raw(rows, [FN]))
        self.assertEqual(normalized["scope"], [lam(FN), agent(FN, "planner", "concurrency"),
                                               agent(FN, "planner", "max_tokens")])
        self.assertEqual(sorted(f["scope_id"] for f in result["findings"]),
                         sorted([lam(FN), agent(FN, "planner", "max_tokens")]))
        self.assertEqual(result["status"], "completed")


class Llm17NegativeTests(unittest.TestCase):
    def test_well_sized_workloads_are_clean(self):
        rows = [lambda_row(FN, 512, 500, 300, 280, 200), agent_row(FN, "support_agent", "workers", 8, 120, 6)]
        result, _ = run(raw(rows, [FN]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertEqual(result["coverage"]["evaluated_scope"], [lam(FN), agent(FN, "support_agent", "workers")])

    def test_low_average_but_high_peak_is_not_flagged(self):
        # bursty: mostly idle, but the burst uses most of the pool, so the size is justified
        result, _ = run(raw([agent_row(FN, "a", "workers", 64, 500, 60, 20, 2)], [FN]))
        self.assertEqual(result["findings"], [])

    def test_smallest_size_is_an_exception(self):
        rows = [lambda_row(FN, 128, 400, 30), agent_row(FN, "a", "workers", 1, 400, 0)]
        result, _ = run(raw(rows, [FN]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("already the smallest size (128 MB)", notes(result))
        self.assertIn("already the smallest size (1 workers)", notes(result))

    def test_peak_above_provisioned_is_noted_not_flagged(self):
        result, _ = run(raw([agent_row(FN, "a", "workers", 4, 200, 9)], [FN]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("exceeds the provisioned 4 workers", notes(result))


class Llm17BelowMinimumTests(unittest.TestCase):
    def test_too_few_invocations_is_unavailable_not_clean(self):
        result, _ = run(raw([lambda_row(FN, 1024, 99, 100)], [FN]))
        self.assertEqual((result["status"], result["findings"], result["coverage"]["evaluated_scope"]),
                         ("unavailable", [], []))
        self.assertIn("only 99 invocations at the current size in the window, fewer than min_invocations 100",
                      notes(result))

    def test_short_window_is_unavailable(self):
        short = {"start": "2026-10-10T06:00:00Z", "end": "2026-10-10T12:00:00Z"}
        result, _ = run(raw([lambda_row(FN, 1024, 500, 100)], [FN], window=short))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("covers 6 hours, shorter than min_window_hours 24", notes(result))

    def test_one_workload_below_minimum_gives_partial(self):
        rows = [lambda_row(FN, 1024, 500, 100), agent_row(FN, "a", "workers", 64, 10, 2)]
        result, _ = run(raw(rows, [FN]))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], [lam(FN)])
        self.assertEqual([f["scope_id"] for f in result["findings"]], [lam(FN)])

    def test_boundaries_are_inclusive_for_minimums_and_strict_for_the_limit(self):
        at_limit = run(raw([lambda_row(FN, 1000, 100, 300)], [FN]))[0]
        self.assertEqual((at_limit["status"], at_limit["findings"]), ("completed", []))  # 30.0% is not below 30%
        below = run(raw([lambda_row(FN, 1000, 100, 299)], [FN]))[0]
        self.assertEqual(len(below["findings"]), 1)

    def test_queried_group_without_capacity_records_is_never_clean(self):
        other = "/aws/lambda/idle"
        result, normalized = run(raw([lambda_row(FN, 1024, 500, 100)], [FN, other]))
        self.assertIn(llm17.group_scope_id(other), normalized["scope"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("no Lambda REPORT lines or agent capacity lines", notes(result))
        empty, _ = run(raw([], [other]))
        self.assertEqual((empty["status"], empty["findings"]), ("unavailable", []))


class Llm17MissingFieldsTests(unittest.TestCase):
    def test_missing_or_invalid_settings_are_unavailable(self):
        normalized = llm17.normalize_logs_insights(raw([lambda_row(FN, 1024, 500, 100)], [FN]))
        for context in ({}, {**SETTINGS, "min_invocations": 0}, {**SETTINGS, "min_window_hours": -1},
                        {**SETTINGS, "max_peak_utilization": 1}, {**SETTINGS, "max_peak_utilization": True}):
            with self.subTest(context=context):
                payload = payload_for(normalized)
                payload["context"] = context
                result = llm17.evaluate(payload)
                validate_pair(payload, result)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn("Missing or invalid context settings", notes(result))

    def test_missing_row_fields_leave_that_workload_unevaluated(self):
        good = lambda_row(FN, 1024, 500, 100)
        broken = agent_row(FN, "a", "workers", 64, 500, 2)
        del broken["peak_used"]
        result, _ = run(raw([good, broken], [FN]))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], [lam(FN)])
        self.assertIn("peak_used not a nonnegative number", notes(result))

    def test_unknown_window_leaves_everything_unevaluated(self):
        result, _ = run(raw([lambda_row(FN, 1024, 500, 100)], [FN], window=None))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("the query window is unknown", notes(result))

    def test_tampered_source_data_is_refused_by_the_detector(self):
        normalized = llm17.normalize_logs_insights(raw([lambda_row(FN, 1024, 500, 100)], [FN]))
        for field, value in (("window_hours", 48.0), ("peak_used", "100"), ("workload", "gpu"),
                             ("log_group", "/aws/lambda/other"), ("p99_used", 500)):
            with self.subTest(field=field):
                tampered = copy.deepcopy(normalized)
                tampered["sources"][0]["data"][field] = value
                result = llm17.evaluate(payload_for(tampered))
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        missing = copy.deepcopy(normalized)
        del missing["sources"][0]["data"]["other_sizes"]
        self.assertIn("missing fields: other_sizes", notes(llm17.evaluate(payload_for(missing))))


class Llm17MalformedRowsTests(unittest.TestCase):
    def test_non_numeric_and_inconsistent_values(self):
        cases = {"invocations": "many", "l17_provisioned": "-5", "p99_used": str(2000 * MB)}
        for field, value in cases.items():
            with self.subTest(field=field):
                row = lambda_row(FN, 1024, 500, 100)
                row[field] = value
                result, _ = run(raw([row], [FN]))
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))

    def test_unattributed_rows_and_row_limit_leave_every_workload_unevaluated(self):
        rows = [lambda_row(FN, 1024, 500, 100), {"invocations": "3"}, "not a row"]
        result, normalized = run(raw(rows, [FN]))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("2 Logs Insights rows had no @log value", " ".join(normalized["limitations"]))
        truncated, _ = run(raw([lambda_row(FN, 1024, 500, 100)], [FN], truncated=True))
        self.assertEqual((truncated["status"], truncated["findings"]), ("unavailable", []))
        self.assertIn("row limit", notes(truncated))

    def test_unqueried_group_and_odd_agent_names_are_skipped_with_a_note(self):
        rows = [lambda_row(FN, 1024, 500, 100), lambda_row("/aws/lambda/elsewhere", 1024, 500, 100),
                agent_row(FN, "bad name;drop", "workers", 64, 500, 2), agent_row(FN, "", "workers", 64, 500, 2)]
        result, normalized = run(raw(rows, [FN]))
        self.assertEqual(normalized["scope"], [lam(FN)])
        self.assertEqual(len(result["findings"]), 1)
        text = " ".join(normalized["limitations"])
        self.assertIn("named a log group that was not queried", text)
        self.assertIn("2 agent capacity row(s) without a simple agent name", text)
        self.assertNotIn("bad name;drop", json.dumps(result))

    def test_raw_shape_errors_raise(self):
        for bad in (None, {"rows": "x", "log_groups": [FN]}, {"rows": [], "log_groups": "x"}):
            with self.assertRaises(llm17.NormalizationError):
                llm17.normalize_logs_insights(bad)

    def test_resized_function_judges_the_latest_size(self):
        rows = [lambda_row(FN, 3008, 300, 200, last_seen="2026-10-09 20:00:00.000"),
                lambda_row(FN, 512, 300, 200, last_seen="2026-10-10 11:00:00.000")]
        result, normalized = run(raw(rows, [FN]))
        data = normalized["sources"][0]["data"]
        self.assertEqual((data["provisioned"], [o["provisioned"] for o in data["other_sizes"]]), (512, [3008]))
        self.assertEqual(result["findings"], [])  # 200/512 = 39%
        self.assertIn("only the most recent size, 512 MB, was judged", notes(result))

    def test_no_account_ids_reach_the_result(self):
        result, _ = run(raw([lambda_row(FN, 1024, 500, 100)], [FN]))
        self.assertNotIn(ACCOUNT, json.dumps(result))

    def test_fingerprints_ignore_values(self):
        first = run(raw([lambda_row(FN, 1024, 500, 100)], [FN]))[0]["findings"][0]
        second = run(raw([lambda_row(FN, 2048, 900, 150)], [FN]))[0]["findings"][0]
        self.assertEqual(first["fingerprint"], second["fingerprint"])


class Llm17QueryAndWiringTests(unittest.TestCase):
    def test_query_shape_and_readme(self):
        query = llm17.LOGS_INSIGHTS_QUERY
        for part in ('@type = "REPORT"', "@memorySize", "@maxMemoryUsed", "pct(l17_used, 99)", "max(l17_used)",
                     "by @log, l17_agent, l17_kind, l17_provisioned", "capacity_provisioned", "capacity_used"):
            self.assertIn(part, query)
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        self.assertIn(f"```text\n{query}\n```", readme)  # the documented query is the one that runs

    def test_registered_on_the_log_analyzer_with_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == "LLM-17")
        self.assertEqual((check.source, check.adapter, check.normalizer), ("logs_insights", None, None))
        self.assertEqual(check.defaults, llm17.REFERENCE_SETTINGS)
        module, normalize = registry.load(check)
        self.assertIs(normalize, llm17.normalize_logs_insights)
        self.assertEqual(set(llm17.SETTING_KEYS), set(check.defaults))
        self.assertIs(cli.DETECTORS["LLM-17"], llm17)
        self.assertEqual(llm17.SUPPORTED_KIND, "telemetry")

    def test_own_query_so_other_logs_insights_checks_are_unchanged(self):
        keys = {registry.collector_key(registry.load(c)[0]) for c in registry.CHECKS if c.source == "logs_insights"}
        self.assertEqual(len(keys), len([c for c in registry.CHECKS if c.source == "logs_insights"]))


class Llm17ThroughTheLogAnalyzerTests(AwsTestCase):
    """End to end: stubbed StartQuery/GetQueryResults -> normalizer -> detector -> validate_pair -> PutEvents, and
    the findings-hub writer stores the published event."""

    def test_rows_are_validated_and_published(self):
        rows = [lambda_row(FN, 1024, 340, 180), agent_row(FN, "support_agent", "workers", 64, 120, 6)]
        logs = self.fakes["logs"] = FakeLogs(rows=rows)
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-17"], logs={"log_groups": [FN]}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-17", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 2}])
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual((start["queryString"], start["logGroupNames"]), (llm17.LOGS_INSIGHTS_QUERY, [FN]))
        self.assertEqual(start["endTime"] - start["startTime"], 24 * 3600)
        entry = self.fakes["events"].entries[0]
        self.assertNotIn(ACCOUNT, entry["Detail"])
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual({f["identity"] for f in result["findings"]}, {"lambda-memory", "agent-capacity"})
        self.assertEqual({k: result["context"][k] for k in llm17.SETTING_KEYS}, llm17.REFERENCE_SETTINGS)
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-llm17"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_short_lookback_publishes_unavailable_not_clean(self):
        self.fakes["logs"] = FakeLogs(rows=[lambda_row(FN, 1024, 340, 180)])
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-17"], dry_run=True,
                                                         logs={"log_groups": [FN], "lookback_hours": 2}))
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))


if __name__ == "__main__":
    unittest.main()
