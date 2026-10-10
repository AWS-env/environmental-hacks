"""OBS-17 verbose log fields: the Logs Insights query, the normalizer, the detector and the log-analyzer route.

Case IDs follow the verification plan on issue #241. `fixtures/obs17/demo.insights.json` holds real, sanitized
GetQueryResults responses from the synthetic owner-d-telemetry-demo log group; every other input is synthetic."""

import copy
import json
import re
import unittest
from pathlib import Path

from tests.aws_fakes import NOW, AwsTestCase, FakeLogs, FakeTable

from findings_hub import writer
from owner_d import obs17
from owner_d.aws import log_handler, registry
from shared.contracts.validation import validate, validate_pair

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "obs17" / "demo.insights.json"
DEMO = "/aws/lambda/owner-d-telemetry-demo"
ACCOUNT = "123456789012"  # AWS documentation placeholder
REPO = "github:AWS-env/example"
SETTINGS = dict(obs17.REFERENCE_SETTINGS)


def recorded(name):
    """Rows of one recorded response, as log_handler.run_insights_query returns them (@ptr dropped)."""
    response = json.loads(FIXTURE.read_text())["responses"][name]
    return [{c["field"]: c["value"] for c in row} for row in response["results"]], response


def row(group, level, bucket, events, chars, max_chars, traces=0, bodies=0):
    """One Logs Insights stats row; values are strings, zero marker counts are left out as Logs Insights does."""
    out = {"@log": f"{ACCOUNT}:{group}", "o17_bucket": str(bucket), "events": str(events), "chars": str(chars),
           "max_chars": str(max_chars)}
    if level:
        out["o17_level"] = level
    if traces:
        out["trace_events"] = str(traces)
    if bodies:
        out["body_events"] = str(bodies)
    return out


def lean(group, events=30, level="INFO"):
    """`events` lean lines of 200 characters (bucket 1)."""
    return row(group, level, 1, events, 200 * events, 200)


def raw(rows, groups, **extra):
    return {"rows": rows, "log_groups": groups, "window": {"start": "2026-10-09T12:00:00Z", "end": "2026-10-10T12:00:00Z"},
            "truncated": False, "region": "ap-south-1", **extra}


def run(raw_data, settings=None, scope=None):
    settings = dict(SETTINGS if settings is None else settings)
    normalized = registry.normalize(obs17.normalize_logs_insights, raw_data, settings)
    payload = {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "scan-1",
               "commit_sha": "0123456789abcdef0123456789abcdef01234567", "check_id": "OBS-17",
               "detector_version": obs17.DETECTOR_VERSION, "context": settings,
               "scope": scope or normalized["scope"] or ["resource:log-group/none"], "sources": normalized["sources"]}
    result = obs17.evaluate(payload)
    validate_pair(payload, result)
    return result, normalized


def by_identity(result):
    return {(f["scope_id"], f["identity"]): f for f in result["findings"]}


def scope(group):
    return obs17.scope_id_for(group)


class Obs17RealDemoTests(unittest.TestCase):
    """OBS17-01 on real Logs Insights output from the deployed demo (synthetic workload)."""

    def test_demo_log_group_is_flagged_for_oversized_events(self):
        rows, response = recorded("all")
        result, normalized = run(raw(rows, [DEMO], window=response["window"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope(DEMO)])
        self.assertEqual(list(by_identity(result)), [(scope(DEMO), "oversized-log-events")])
        finding = result["findings"][0]
        data = normalized["sources"][0]["data"]
        large = [b for b in data["size_histogram"] if b["bucket"] > SETTINGS["max_event_bytes"] // obs17.BUCKET_CHARS]
        # exactly one large event: the waste line (chained traceback + echoed request_body)
        self.assertEqual([(b["events"], b["trace_events"], b["body_events"]) for b in large], [(1, 1, 1)])
        self.assertEqual((data["events"], data["max_event_chars"]), (53, 5587))
        self.assertIn("1 of 53 application log events are larger than 4096 characters", finding["summary"])
        self.assertIn("carry 35.8% of", finding["summary"])
        self.assertEqual(finding["confidence"], "medium")
        self.assertEqual([e["field"] for e in finding["evidence"]], ["size_histogram", "event_chars", "events"])
        # one ERROR trace per failure is the legitimate case for the trace rule
        self.assertIn("1 stack trace at ERROR level, within max_error_trace_repeats 10; not flagged",
                      " ".join(result["coverage"]["limitations"]))

    def test_demo_waste_path_alone_is_flagged(self):
        rows, response = recorded("waste")
        result, _ = run(raw(rows, [DEMO], window=response["window"]), {**SETTINGS, "min_events": 1})
        self.assertEqual(list(by_identity(result)), [(scope(DEMO), "oversized-log-events")])
        self.assertIn("1 carries a full stack trace and 1 carries a JSON body/payload field",
                      result["findings"][0]["summary"])

    def test_demo_control_path_alone_is_clean(self):
        rows, response = recorded("control")
        result, normalized = run(raw(rows, [DEMO], window=response["window"]), {**SETTINGS, "min_events": 1})
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        data = normalized["sources"][0]["data"]
        self.assertEqual((data["max_event_chars"], data["body_field_events"], data["trace_events_by_level"]["error"]),
                         (260, 0, 0))

    def test_no_account_ids_or_log_content_reach_the_result(self):
        rows, _ = recorded("all")
        text = json.dumps(run(raw(rows, [DEMO]))[0])
        for forbidden in (ACCOUNT, "Traceback", "SKU-", "request_body", "order rejected"):
            self.assertNotIn(forbidden, text)

    def test_cli_fixture_matches_the_recorded_response(self):
        rows, response = recorded("all")
        normalized = registry.normalize(obs17.normalize_logs_insights, raw(rows, [DEMO], window=response["window"]),
                                        SETTINGS)
        payload = json.loads((FIXTURE.parent / "obs17-01-demo-input.json").read_text())
        self.assertEqual((payload["scope"], payload["sources"], payload["context"]),
                         (normalized["scope"], normalized["sources"], SETTINGS))
        result = obs17.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual([f["identity"] for f in result["findings"]], ["oversized-log-events"])

    def test_recorded_responses_came_from_this_query(self):
        for name in ("all", "waste", "control"):
            rows, response = recorded(name)
            self.assertEqual(response["status"], "Complete")
            self.assertTrue(all(r["@log"] == f"{ACCOUNT}:{DEMO}" for r in rows))
            self.assertLessEqual(set().union(*rows), {"@log", "o17_level", "o17_bucket", "events", "chars", "max_chars",
                                                      "trace_events", "body_events"})


class Obs17PositiveTests(unittest.TestCase):
    """OBS17-01 synthetic: repeated INFO traces, repeated ERROR traces, oversized bodies."""

    def test_info_and_warn_traces_that_repeat_are_flagged(self):
        group = "/aws/lambda/owner-d-orders"
        result, _ = run(raw([lean(group), row(group, "WARN", 3, 2, 2400, 1300, traces=2),
                             row(group, "INFO", 2, 1, 800, 800, traces=1)], [group]))
        finding = by_identity(result)[(scope(group), "repeated-stack-traces")]
        self.assertIn("3 events below ERROR level carry a full stack trace (limit 1)", finding["summary"])
        self.assertEqual(finding["evidence"][0]["value"], {"error": 0, "below_error": 3, "unknown": 0})
        self.assertNotIn((scope(group), "oversized-log-events"), by_identity(result))

    def test_error_traces_beyond_the_repeat_limit_are_flagged(self):
        group = "/aws/lambda/owner-d-crashloop"
        result, _ = run(raw([lean(group), row(group, "ERROR", 4, 11, 19000, 2000, traces=11)], [group]))
        self.assertIn("11 ERROR-level events carry a full stack trace (limit 10)",
                      by_identity(result)[(scope(group), "repeated-stack-traces")]["summary"])

    def test_large_bodies_over_the_share_are_flagged_with_both_rules_per_group(self):
        group, other = "/aws/lambda/owner-d-api", "/aws/lambda/owner-d-lean"
        rows = [lean(group, 40), row(group, "INFO", 16, 5, 40000, 8100, bodies=5), row(group, "INFO", 2, 2, 1200, 700, traces=2),
                lean(other, 40)]
        result, _ = run(raw(rows, [group, other]))
        self.assertEqual(sorted(by_identity(result)), [(scope(group), "oversized-log-events"),
                                                       (scope(group), "repeated-stack-traces")])
        size = by_identity(result)[(scope(group), "oversized-log-events")]
        self.assertIn("5 of 47 application log events are larger than 4096 characters", size["summary"])
        self.assertIn("5 carry a JSON body/payload field", size["summary"])
        self.assertEqual(size["fingerprint"], obs17.fingerprint(REPO, "OBS-17", scope(group), "oversized-log-events"))
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope(group), scope(other)])

    def test_large_events_without_markers_are_low_confidence(self):
        group = "/aws/lambda/owner-d-blob"
        result, _ = run(raw([lean(group), row(group, None, 20, 3, 30000, 10200)], [group]))
        finding = by_identity(result)[(scope(group), "oversized-log-events")]
        self.assertEqual(finding["confidence"], "low")
        self.assertIn("no recognized stack-trace or body marker", finding["summary"])


class Obs17NegativeTests(unittest.TestCase):
    """OBS17-02 and OBS17-03."""

    def test_lean_log_group_is_clean(self):
        group = "/aws/lambda/owner-d-lean"
        result, _ = run(raw([lean(group, 60), row(group, "WARN", 2, 4, 2400, 900)], [group]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_error_traces_within_the_limit_and_unknown_levels_are_exceptions(self):
        group = "/aws/lambda/owner-d-errors"
        result, _ = run(raw([lean(group), row(group, "ERROR", 3, 10, 13000, 1500, traces=10),
                             row(group, None, 2, 4, 3000, 900, traces=4)], [group]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        notes = " ".join(result["coverage"]["limitations"])
        self.assertIn("10 stack traces at ERROR level, within max_error_trace_repeats 10", notes)
        self.assertIn("4 stack traces without a recognized level", notes)

    def test_one_large_event_below_the_share_is_noted_not_flagged(self):
        group = "/aws/lambda/owner-d-busy"
        result, _ = run(raw([lean(group, 200), row(group, "ERROR", 11, 1, 5500, 5500, traces=1, bodies=1)], [group]))
        self.assertEqual(result["findings"], [])
        self.assertIn("1 events larger than 4096 characters carry 12.1%", " ".join(result["coverage"]["limitations"]))

    def test_levels_are_classified(self):
        self.assertEqual([obs17.level_class(v) for v in ("ERROR", "fatal", "50", "WARNING", "info", "30", "", None, "X")],
                         ["error", "error", "error", "below_error", "below_error", "below_error", "unknown", "unknown",
                          "unknown"])


class Obs17IncompleteTests(unittest.TestCase):
    """OBS17-04 missing evidence and OBS17-05 malformed input: never a clean claim."""

    def test_group_without_events_is_not_evaluated(self):
        result, normalized = run(raw([], [DEMO]))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("unavailable", []))
        self.assertEqual(normalized["sources"][0]["data"]["events"], 0)
        self.assertIn("only 0 application log events in the window, fewer than min_events 20",
                      result["coverage"]["limitations"][0])

    def test_too_few_events_gives_partial(self):
        quiet, busy = "/aws/lambda/owner-d-quiet", "/aws/lambda/owner-d-busy"
        result, _ = run(raw([lean(quiet, 19), lean(busy, 20)], [busy, quiet]))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(busy)]))

    def test_missing_or_invalid_settings_are_unavailable(self):
        rows, _ = recorded("all")
        cases = {"max_event_bytes": None, "min_bytes_share": 1.5, "max_trace_repeats": -1,
                 "max_error_trace_repeats": True, "min_events": 0}
        for key, value in cases.items():
            settings = dict(SETTINGS)
            if value is None:
                del settings[key]
            else:
                settings[key] = value
            with self.subTest(key=key):
                result, _ = run(raw(rows, [DEMO]), settings)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(key, result["coverage"]["limitations"][0])

    def test_no_source_for_a_requested_scope(self):
        result, _ = run(raw([lean(DEMO)], [DEMO]), scope=[scope(DEMO), scope("/aws/lambda/owner-d-missing")])
        self.assertEqual(result["status"], "partial")
        self.assertIn("no telemetry source supplied", " ".join(result["coverage"]["limitations"]))

    def test_rows_that_are_not_a_list(self):
        normalized = obs17.normalize_logs_insights(raw(None, [DEMO]))
        self.assertEqual((normalized["scope"], normalized["sources"]), ([scope(DEMO)], []))
        result, _ = run(raw(None, [DEMO]))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(obs17.normalize_logs_insights("nope")["sources"], [])

    def test_bad_values_make_only_that_group_unevaluated(self):
        good, bad = "/aws/lambda/owner-d-good", "/aws/lambda/owner-d-bad"
        broken = [lean(bad), {**lean(bad), "events": "many"}, row(bad, "INFO", 1, 2, 300, 900),
                  {**lean(bad), "o17_bucket": "99"}, row(bad, "INFO", 1, 2, 300, 200, traces=3)]
        result, _ = run(raw([lean(good)] + broken, [good, bad]))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(good)]))
        note = next(n for n in result["coverage"]["limitations"] if n.startswith(scope(bad)))
        for reason in ("events='many' is not a nonnegative whole number", "sizes in bucket 1 do not match",
                       "bucket 99", "marker counts exceed"):
            self.assertIn(reason, note)

    def test_unattributed_rows_and_row_limit_leave_every_group_unevaluated(self):
        for extra, reason in (({"truncated": True}, "row limit"), ({}, "could not be attributed")):
            rows = [lean(DEMO)] + ([] if extra else [{k: v for k, v in lean(DEMO).items() if k != "@log"}])
            with self.subTest(reason=reason):
                result, _ = run(raw(rows, [DEMO], **extra))
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(reason, " ".join(result["coverage"]["limitations"]))

    def test_tampered_source_data_is_refused_by_the_detector(self):
        _, normalized = run(raw([lean(DEMO)], [DEMO]))
        source = copy.deepcopy(normalized["sources"][0])
        source["data"]["event_chars"] += 1
        payload = {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "s",
                   "commit_sha": "0" * 40, "check_id": "OBS-17", "detector_version": "1.0.0", "context": SETTINGS,
                   "scope": [scope(DEMO)], "sources": [source]}
        result = obs17.evaluate(payload)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("does not add up", result["coverage"]["limitations"][0])
        with self.assertRaises(obs17.EvaluationError):
            obs17.evaluate({**payload, "detector_version": "0.9"})


class Obs17BoundaryTests(unittest.TestCase):
    """OBS17-06: strict thresholds and the bucket alignment of max_event_bytes."""

    def test_share_exactly_at_the_limit_is_not_flagged_and_just_above_is(self):
        group = "/aws/lambda/owner-d-edge"
        # 30 lean lines = 6000 characters; one 2000-character event at 1024 limit -> 2000 / 8000 = 25%
        at_limit = [lean(group), row(group, "INFO", 4, 1, 2000, 2000)]
        above = [lean(group), row(group, "INFO", 4, 1, 2001, 2001)]
        settings = {**SETTINGS, "max_event_bytes": 1024}
        self.assertEqual(run(raw(at_limit, [group]), settings)[0]["findings"], [])
        self.assertEqual(list(by_identity(run(raw(above, [group]), settings)[0])), [(scope(group), "oversized-log-events")])

    def test_event_size_boundary_follows_the_buckets(self):
        group = "/aws/lambda/owner-d-size"
        exactly = [lean(group, 20), row(group, "INFO", 8, 10, 40960, 4096)]  # 4096 characters: not larger
        larger = [lean(group, 20), row(group, "INFO", 9, 10, 40970, 4097)]
        self.assertEqual(run(raw(exactly, [group]))[0]["findings"], [])
        self.assertEqual(len(run(raw(larger, [group]))[0]["findings"]), 1)

    def test_trace_repeat_limits_are_strict(self):
        group = "/aws/lambda/owner-d-traces"
        for below, error, flagged in ((1, 10, False), (2, 10, True), (1, 11, True), (0, 0, False)):
            rows = [lean(group)]
            if below:
                rows.append(row(group, "WARN", 2, below, 700 * below, 700, traces=below))
            if error:
                rows.append(row(group, "ERROR", 2, error, 700 * error, 700, traces=error))
            with self.subTest(below=below, error=error):
                found = by_identity(run(raw(rows, [group]))[0])
                self.assertEqual((scope(group), "repeated-stack-traces") in found, flagged)

    def test_unaligned_threshold_is_unavailable(self):
        for value in (4000, 256, 32768 + 512):
            with self.subTest(value=value):
                result, _ = run(raw([lean(DEMO)], [DEMO]), {**SETTINGS, "max_event_bytes": value})
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("multiple of 512", result["coverage"]["limitations"][0])

    def test_fingerprints_ignore_values(self):
        group = "/aws/lambda/owner-d-api"
        small = run(raw([lean(group), row(group, "INFO", 16, 5, 40000, 8100)], [group]))[0]["findings"][0]
        large = run(raw([lean(group, 90), row(group, "INFO", 30, 9, 130000, 15300)], [group]))[0]["findings"][0]
        self.assertEqual(small["fingerprint"], large["fingerprint"])
        self.assertNotEqual(small["summary"], large["summary"])


class Obs17QueryTests(unittest.TestCase):
    """The query measures size per log group and recognizes markers in raw and JSON-escaped events."""

    def regex(self, field):
        match = re.search(r"/(.*\(\?<" + field + r">.*)/", obs17.LOGS_INSIGHTS_QUERY)
        return re.compile(match.group(1).replace("(?<", "(?P<"))

    def test_query_shape(self):
        query = obs17.LOGS_INSIGHTS_QUERY
        for part in ("strlen(@message)", "least(ceil(strlen(@message) / 512), 65)", "by @log, o17_level, o17_bucket",
                     "count(o17_trace)", "count(o17_body)", "not like /^(START|END|REPORT) RequestId: "):
            self.assertIn(part, query)
        self.assertEqual(obs17.BUCKET_CHARS * (obs17.BUCKET_CAP - 1), obs17.MAX_THRESHOLD)
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        self.assertIn(f"```text\n{query}\n```", readme)  # the documented query is the one that runs

    def test_trace_markers(self):
        trace = self.regex("o17_trace")
        positives = [
            'Traceback (most recent call last):\n  File "x.py", line 1',
            "java.lang.IllegalStateException: boom\n\tat com.acme.Orders.place(Orders.java:42)",
            '{"stack":"java.lang.X: y\\n\\tat com.acme.A.b(A.java:1)"}',
            "Error: boom\n    at placeOrder (/var/task/index.js:10:5)",
            '{"err":"Error: boom\\n    at Object.<anonymous> (/var/task/a.js:1:1)"}',
            "System.Exception: boom\r   at Acme.Orders.Place() in Orders.cs:line 9",
            "panic: boom\n\ngoroutine 1 [running]:",
        ]
        negatives = ['{"message":"look at this","level":"INFO"}', "\tat the start of a single line",
                     "meet at noon (UTC)", "Traceback requested by user"]
        for text in positives:
            self.assertIsNotNone(trace.search(text), text)
        for text in negatives:
            self.assertIsNone(trace.search(text), text)

    def test_body_markers(self):
        body = self.regex("o17_body")
        for text in ('{"body": "{}"}', '{"request_body":{"a":1}}', '{"payload":[1]}', '{"requestBody":"x"}'):
            self.assertIsNotNone(body.search(text), text)
        for text in ('{"body_bytes":12,"body_sha256":"ab"}', '{"message":"payload too large"}', 'body: 12'):
            self.assertIsNone(body.search(text), text)


class Obs17RegistryTests(unittest.TestCase):
    def test_registered_on_the_log_analyzer_with_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == "OBS-17")
        self.assertEqual((check.source, check.adapter, check.normalizer), ("logs_insights", None, None))
        self.assertEqual(check.defaults, obs17.REFERENCE_SETTINGS)
        module, normalize = registry.load(check)
        self.assertIs(normalize, obs17.normalize_logs_insights)
        self.assertEqual(set(obs17.SETTING_KEYS), set(check.defaults))


class Obs17ThroughTheLogAnalyzerTests(AwsTestCase):
    """End to end: stubbed StartQuery/GetQueryResults with the recorded demo rows -> normalizer -> detector ->
    validate_pair -> PutEvents, and the findings-hub writer stores the published event."""

    def test_demo_rows_are_validated_and_published(self):
        rows, _ = recorded("all")
        logs = self.fakes["logs"] = FakeLogs(rows=rows)
        out = log_handler.lambda_handler(self.base_event(checks=["OBS-17"], logs={"log_groups": [DEMO]}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "OBS-17", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual((start["queryString"], start["logGroupNames"]), (obs17.LOGS_INSIGHTS_QUERY, [DEMO]))
        self.assertEqual(out["collection"]["OBS-17"]["bytes_scanned"], 2048.0)
        entry = self.fakes["events"].entries[0]
        self.assertNotIn(ACCOUNT, entry["Detail"])
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["findings"][0]["identity"], "oversized-log-events")
        self.assertEqual(result["context"]["collection"]["source"], "cloudwatch-logs-insights")
        self.assertEqual({k: result["context"][k] for k in obs17.SETTING_KEYS}, obs17.REFERENCE_SETTINGS)
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-obs17"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_row_limit_reached_publishes_an_unavailable_result_not_a_clean_one(self):
        rows, _ = recorded("all")
        self.fakes["logs"] = FakeLogs(rows=rows)
        out = log_handler.lambda_handler(self.base_event(checks=["OBS-17"], logs={"log_groups": [DEMO], "limit": 5},
                                                         dry_run=True))
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("Logs Insights returned the 5-row limit; more matching rows may exist",
                      result["coverage"]["limitations"])


if __name__ == "__main__":
    unittest.main()
