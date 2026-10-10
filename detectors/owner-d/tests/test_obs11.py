"""Behavioral tests for the OBS-11 detector (issue #235).

`fixtures/obs11/recorded-logs-insights.json` is a real GetQueryResults response for `LOGS_INSIGHTS_QUERY` over
the deployed demo log group `/aws/lambda/owner-d-telemetry-demo` (account ID replaced with 123456789012, `@ptr`
removed). The demo's log lines are synthetic by design. It is also the regression case for issue #455: a 20-line
retry burst inside one invocation of a group with 23 invocations, which detector 1.0.0 exempted as a per-request
line. Every other case below is synthetic.
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.aws_fakes import AwsTestCase, FakeLogs, FakeTable, NOW, SHA

from findings_hub import writer
from owner_d import cli, obs11
from owner_d.aws import log_handler, registry
from shared.contracts.validation import validate, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs11"
RECORDED = json.loads((FIXTURES / "recorded-logs-insights.json").read_text())
DEMO = "/aws/lambda/owner-d-telemetry-demo"
REPOSITORY_ID = "aws:ap-south-1:project"
WINDOW = {"start": "2026-10-09T12:00:00Z", "end": "2026-10-10T12:00:00Z"}
RECORDED_WINDOW = {"start": "2026-10-09T03:03:42Z", "end": "2026-10-10T03:03:42Z"}  # startTime/endTime of the query
# The recorded window holds one "all" invocation, ten "llm10" and twelve "llm05" invocations (77 application events),
# below the reference min_events/min_repeats; the demo context lowers both so the real data can be evaluated.
DEMO_CONTEXT = {"min_events": 50, "min_repeats": 10, "min_share": 0.2, "exempt_message_markers": ["heartbeat"]}
CONTEXT = dict(obs11.REFERENCE_SETTINGS)
RETRY = "upstream payments-api unavailable, retrying"


def recorded_rows():
    """The recorded response as log_handler.run_insights_query returns its rows."""
    return [{c["field"]: c.get("value") for c in row if c.get("field") != "@ptr"}
            for row in RECORDED["response"]["results"]]


def row(group, message, occurrences, first="2026-10-10 01:00:00.000", last="2026-10-10 11:00:00.000", slots=None):
    """One query row; `slots` (distinct log stream + second) defaults to one slot per line (fully spread)."""
    return {"@log": f"123456789012:{group}", "message": message, "occurrences": str(occurrences),
            "slots": str(occurrences if slots is None else slots), "first_seen": first, "last_seen": last}


def platform_rows(group, invocations, slots=None):
    return [row(group, "START RequestId: <uuid> Version: $LATEST", invocations, slots=slots),
            row(group, "END RequestId: <uuid>", invocations),
            row(group, "REPORT RequestId: <uuid> Duration: <n>.<n> ms Billed Duration: <n> ms", invocations)]


def raw(rows, groups, truncated=False, window=WINDOW):
    return {"rows": rows, "statistics": {"bytesScanned": 1.0}, "log_groups": groups, "query": obs11.LOGS_INSIGHTS_QUERY,
            "window": window, "region": "ap-south-1", "truncated": truncated, "limitations": []}


def make_input(normalized, context=None):
    return {"schema_version": "1.0", "kind": "input", "repository_id": REPOSITORY_ID, "scan_id": "scan-obs11",
            "commit_sha": SHA, "check_id": obs11.CHECK_ID, "detector_version": obs11.DETECTOR_VERSION,
            "context": dict(CONTEXT if context is None else context), "scope": normalized["scope"],
            "sources": normalized["sources"]}


def run(rows, groups, context=None, truncated=False, window=WINDOW):
    normalized = obs11.normalize_logs_insights(raw(rows, groups, truncated, window), settings=context)
    payload = make_input(normalized, context)
    result = obs11.evaluate(payload)
    validate_pair(payload, result)
    return payload, result, normalized


def scope(group):
    return obs11.scope_id_for(group)


def source_data(payload, group):
    return next(s["data"] for s in payload["sources"] if s["scope_id"] == scope(group))


def retry_group(group, *, retries=120, invocations=40, other=140, message=RETRY):
    """A Lambda group with a retry loop: `retries` identical lines over `invocations` invocations, each
    invocation's attempts sharing one slot."""
    return platform_rows(group, invocations) + [row(group, message, retries, slots=min(retries, invocations)),
                                                row(group, "order <n> accepted", 40), row(group, "<other>", other)]


class RecordedDemoTests(unittest.TestCase):
    """OBS11-01/02 on the real Logs Insights response from the deployed demo log group (regression for #455)."""

    def setUp(self):
        self.payload, self.result, self.normalized = run(recorded_rows(), [DEMO], DEMO_CONTEXT, window=RECORDED_WINDOW)
        self.data = source_data(self.payload, DEMO)

    def test_recorded_query_is_the_module_query(self):
        self.assertEqual(RECORDED["query"], obs11.LOGS_INSIGHTS_QUERY)
        self.assertEqual(RECORDED["response"]["status"], "Complete")

    def test_normalized_counts_match_the_recorded_rows(self):
        self.assertEqual((self.data["events"], self.data["platform_events"], self.data["application_events"]),
                         (148, 71, 77))
        self.assertEqual((self.data["invocations"], self.data["invocation_slots"], self.data["folded_events"]),
                         (23, 23, 11))
        self.assertEqual([(m["occurrences"], m["slots"]) for m in self.data["messages"]],
                         [(20, 1), (12, 12), (12, 12), (11, 11), (11, 11)])
        self.assertNotIn("123456789012", json.dumps(self.normalized))
        self.assertEqual(self.payload["sources"][0]["locator"], f"logs-insights://ap-south-1/log-group/{DEMO}")

    def test_single_invocation_retry_burst_is_flagged(self):
        # Issue #455: 20 lines <= 23 invocations, which 1.0.0 exempted; all 20 share one slot, so it is a burst.
        waste = self.data["messages"][0]
        self.assertLessEqual(waste["occurrences"], self.data["invocations"])
        self.assertEqual(self.result["status"], "completed")
        self.assertEqual(self.result["coverage"]["evaluated_scope"], [scope(DEMO)])
        [finding] = self.result["findings"]
        self.assertIn("upstream inventory-svc unavailable, retrying", waste["message"])
        self.assertIn('"path":"waste"', waste["message"])
        self.assertEqual(finding["identity"], "repeated-log-line:" + waste["message_sha256"][:16])
        self.assertIn("logged the same normalised message 20 times", finding["summary"])
        self.assertIn("26% of its 77 application events, 0.9 per invocation over 23 invocations, 20.0 per slot "
                      "(log stream and second) against 1.0 invocations per slot", finding["summary"])
        self.assertEqual(finding["confidence"], "medium")  # "OBS-11" became "OBS-<n>"
        self.assertEqual({e["field"]: e["value"] for e in finding["evidence"]},
                         {f: self.data[f] for f in ("messages", "application_events", "invocations",
                                                    "invocation_slots", "window")})

    def test_once_per_invocation_llm_lines_are_not_flagged(self):
        # At min_share 0.2 the LLM lines (11-12 of 77) stay below the share threshold; at 0.1 they pass both
        # thresholds and must be exempt as per-request lines (one line per slot, like the START lines).
        for context in (DEMO_CONTEXT, {**DEMO_CONTEXT, "min_share": 0.1}):
            payload = make_input(self.normalized, context)
            result = obs11.evaluate(payload)
            validate_pair(payload, result)
            [finding] = result["findings"]
            self.assertIn("upstream inventory-svc unavailable, retrying", finding["summary"])
            flagged = " ".join(f["summary"] + f["identity"] for f in result["findings"])
            self.assertNotIn("after retries", flagged)  # the control summary line is logged once, folded into <other>
            self.assertNotIn("agent run finished", flagged)
            self.assertNotIn("pipeline run finished", flagged)
        note = next(n for n in result["coverage"]["limitations"] if "at most once per invocation" in n)
        for agent in ("demo_bounded_agent", "demo_unbounded_agent", "demo_chained_pipeline", "demo_redundant_pipeline"):
            self.assertIn(agent, note)
        self.assertIn("(11 lines in 11 slots; 23 invocations in 23 slots)", note)
        self.assertIn("(12 lines in 12 slots; 23 invocations in 23 slots)", note)
        self.assertNotIn("inventory-svc", note)

    def test_reference_settings_need_more_demo_traffic(self):
        payload = make_input(self.normalized, CONTEXT)
        result = obs11.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn(f"{scope(DEMO)}: only 77 application events in the window, below min_events 100; not evaluated",
                      result["coverage"]["limitations"])

    def test_cli_fixture_is_in_sync_and_evaluates(self):
        committed = json.loads((FIXTURES / "obs11-01-positive-input.json").read_text())
        self.assertEqual(committed, {**self.payload, "repository_id": committed["repository_id"]})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(FIXTURES / "obs11-01-positive-input.json"), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out.read_text())["findings"]), 1)


class SyntheticDetectionTests(unittest.TestCase):
    def test_retry_loop_is_flagged_and_a_varied_group_is_not(self):  # OBS11-01 / OBS11-02
        varied = "/aws/lambda/owner-d-varied"
        rows = retry_group("/aws/lambda/owner-d-retry") + platform_rows(varied, 50) + [
            row(varied, f"handled route /{name}", 60) for name in "abcde"] + [row(varied, "<other>", 100)]
        payload, result, _ = run(rows, ["/aws/lambda/owner-d-retry", varied])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope("/aws/lambda/owner-d-retry"), scope(varied)])
        [finding] = result["findings"]
        self.assertEqual(finding["scope_id"], scope("/aws/lambda/owner-d-retry"))
        self.assertEqual(finding["confidence"], "high")
        self.assertIn(f'Message: "{RETRY}"', finding["summary"])
        self.assertIn("120 times", finding["summary"])
        self.assertIn("40% of its 300 application events, 3.0 per invocation over 40 invocations", finding["summary"])
        data = source_data(payload, "/aws/lambda/owner-d-retry")
        self.assertEqual((data["application_events"], data["platform_events"], data["invocations"]), (300, 120, 40))

    def test_non_lambda_group_without_invocations_is_medium(self):
        group = "/aws/lambda/owner-d-ecs-like"
        _, result, _ = run([row(group, RETRY, 90), row(group, "<other>", 110)], [group])
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("invocation count unknown", finding["summary"])

    def test_fingerprint_is_stable_across_counts_and_differs_per_message(self):
        group = "/aws/lambda/owner-d-retry"
        _, first, _ = run(retry_group(group, retries=120), [group])
        _, second, _ = run(retry_group(group, retries=400), [group])
        _, other, _ = run(retry_group(group, message="health probe to db flapped, retrying"), [group])
        self.assertEqual(first["findings"][0]["fingerprint"], second["findings"][0]["fingerprint"])
        self.assertNotEqual(first["findings"][0]["fingerprint"], other["findings"][0]["fingerprint"])

    def test_reported_text_is_redacted_and_truncated(self):
        group = "/aws/lambda/owner-d-secret"
        secret = ("login failed for alice@example.com from <n>.<n>.<n>.<n> password=hunter2 "
                  "token: AKIA<n>EXAMPLEKEYABCDEF<n>ZZ Authorization: Bearer abc.def.ghi " + "x" * 300)
        payload, result, _ = run([row(group, secret, 90), row(group, "<other>", 110)], [group])
        text = json.dumps([payload["sources"], result])
        for leaked in ("alice@example.com", "hunter2", "AKIA", "abc.def.ghi", "<n>.<n>.<n>.<n>"):
            self.assertNotIn(leaked, text)
        message = source_data(payload, group)["messages"][0]["message"]
        self.assertTrue(message.startswith("login failed for <email> from <ip> password=<redacted> token: <redacted>"))
        self.assertEqual(len(message), obs11.MAX_MESSAGE_CHARS)
        self.assertTrue(message.endswith("…"))


class ExceptionTests(unittest.TestCase):  # OBS11-03
    def test_platform_lines_dominating_are_not_application_events(self):
        group = "/aws/lambda/owner-d-quiet"
        rows = platform_rows(group, 400) + [row(group, "INIT_START Runtime Version: python:<n>.<n>", 30),
                                            row(group, "cache warm", 12), row(group, "<other>", 100)]
        payload, result, _ = run(rows, [group])
        data = source_data(payload, group)
        self.assertEqual((data["platform_events"], data["application_events"]), (1230, 112))
        self.assertEqual([m["message"] for m in data["messages"]], ["cache warm"])
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_platform_message_in_supplied_data_is_not_flagged(self):
        group = "/aws/lambda/owner-d-quiet"
        payload = make_input(obs11.normalize_logs_insights(raw(retry_group(group), [group])))
        data = payload["sources"][0]["data"]
        data["messages"][0]["message"] = "REPORT RequestId: <uuid> Duration: <n> ms"
        result = obs11.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["findings"], [])
        self.assertIn(f"{scope(group)}: 1 repeated Lambda platform line not flagged", result["coverage"]["limitations"])

    def test_heartbeat_line_is_exempt(self):
        group = "/aws/lambda/owner-d-heartbeat"
        _, result, _ = run(retry_group(group, message="Heartbeat: worker alive"), [group])
        self.assertEqual(result["findings"], [])
        self.assertIn(f"{scope(group)}: 1 repeated heartbeat line exempt by exempt_message_markers",
                      result["coverage"]["limitations"])
        _, flagged, _ = run(retry_group(group, message="Heartbeat: worker alive"), [group],
                            {**CONTEXT, "exempt_message_markers": []})
        self.assertEqual(len(flagged["findings"]), 1)

    def test_once_per_invocation_summary_line_is_a_per_request_line(self):
        group = "/aws/lambda/owner-d-summary"
        _, result, _ = run(retry_group(group, retries=120, invocations=120), [group])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("at most once per invocation" in n and
                            "(120 lines in 120 slots; 120 invocations in 120 slots)" in n
                            for n in result["coverage"]["limitations"]))

    def test_burst_in_a_few_invocations_is_flagged_despite_many_invocations(self):  # issue #455
        group = "/aws/lambda/owner-d-burst"
        rows = platform_rows(group, 200) + [row(group, RETRY, 60, slots=2), row(group, "<other>", 140)]
        payload, result, _ = run(rows, [group])
        entry = source_data(payload, group)["messages"][0]
        self.assertLessEqual(entry["occurrences"], 200)  # 1.0.0 exempted this: 60 lines <= 200 invocations
        self.assertFalse(obs11.is_per_request(entry, source_data(payload, group)))
        [finding] = result["findings"]
        self.assertIn("0.3 per invocation over 200 invocations, 30.0 per slot (log stream and second) against "
                      "1.0 invocations per slot", finding["summary"])

    def test_busy_function_per_request_line_is_exempt_and_its_bursts_are_not(self):
        # 600 invocations packed 6 per slot (busy execution environments): a summary line logged once per request
        # is as clustered as the START lines; a retry burst at 30 lines per slot is not.
        group = "/aws/lambda/owner-d-busy"
        summary, burst = "request handled", "upstream cache-svc unavailable, retrying"
        rows = platform_rows(group, 600, slots=100) + [row(group, summary, 600, slots=100),
                                                        row(group, burst, 300, slots=10), row(group, "<other>", 300)]
        payload, result, _ = run(rows, [group], {**CONTEXT, "min_share": 0.2})
        [finding] = result["findings"]
        self.assertIn(burst, finding["summary"])
        self.assertTrue(any("request handled\" (600 lines in 100 slots; 600 invocations in 100 slots)" in n
                            for n in result["coverage"]["limitations"]))


class MissingEvidenceTests(unittest.TestCase):  # OBS11-04
    def test_scope_without_source_is_unavailable(self):
        group = "/aws/lambda/owner-d-retry"
        payload = make_input({"scope": [scope(group)], "sources": []})
        result = obs11.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("unavailable", []))
        self.assertIn("no telemetry source supplied", result["coverage"]["limitations"][0])

    def test_missing_or_invalid_settings_are_unavailable(self):
        group = "/aws/lambda/owner-d-retry"
        normalized = obs11.normalize_logs_insights(raw(retry_group(group), [group]))
        cases = {"missing required context settings: min_share": {k: v for k, v in CONTEXT.items() if k != "min_share"},
                 "min_repeats must be an integer of at least 9": {**CONTEXT, "min_repeats": 8},
                 "min_share must be a number in [0, 1)": {**CONTEXT, "min_share": 1},
                 "min_events must be a positive integer": {**CONTEXT, "min_events": True},
                 "exempt_message_markers must be a list": {**CONTEXT, "exempt_message_markers": "heartbeat"}}
        for reason, context in cases.items():
            payload = make_input(normalized, context)
            result = obs11.evaluate(payload)
            validate_pair(payload, result)
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []), reason)
            self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_group_without_rows_has_zero_events_and_is_not_evaluated(self):
        empty, busy = "/aws/lambda/owner-d-empty", "/aws/lambda/owner-d-retry"
        payload, result, _ = run(retry_group(busy), [busy, empty])
        self.assertEqual(source_data(payload, empty)["application_events"], 0)
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(busy)]))
        self.assertEqual(len(result["findings"]), 1)

    def test_row_limit_leaves_the_cut_group_and_later_groups_without_source(self):
        a, b, c = "/aws/lambda/owner-d-a", "/aws/lambda/owner-d-b", "/aws/lambda/owner-d-c"
        rows = retry_group(a) + retry_group(b)[:2]  # b was cut by the row limit; c never appeared
        payload, result, normalized = run(rows, [a, b, c], truncated=True)
        self.assertEqual([s["scope_id"] for s in payload["sources"]], [scope(a)])
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(a)]))
        self.assertIn(f"2 log group(s) have incomplete counts and were not evaluated: {b}, {c}",
                      normalized["limitations"][0])


class MalformedInputTests(unittest.TestCase):  # OBS11-05
    def test_non_numeric_occurrences_drop_only_that_group(self):
        good, bad = "/aws/lambda/owner-d-good", "/aws/lambda/owner-d-bad"
        rows = retry_group(good) + [row(bad, RETRY, "lots"), row(bad, "<other>", 300)]
        payload, result, normalized = run(rows, [good, bad])
        self.assertEqual([s["scope_id"] for s in payload["sources"]], [scope(good)])
        self.assertEqual(result["status"], "partial")
        self.assertIn(f"{scope(bad)}: Logs Insights rows with a missing message or a non-integer occurrences value",
                      normalized["limitations"][0])

    def test_rows_without_a_slot_count_drop_that_group(self):
        good, bad = "/aws/lambda/owner-d-good", "/aws/lambda/owner-d-bad"
        rows = retry_group(good) + [{k: v for k, v in r.items() if k != "slots"} for r in retry_group(bad)]
        payload, result, normalized = run(rows, [good, bad])
        self.assertEqual([s["scope_id"] for s in payload["sources"]], [scope(good)])
        self.assertIn(f"{scope(bad)}: Logs Insights rows with a missing message or a non-integer occurrences value or "
                      "slots count; not evaluated", normalized["limitations"])

    def test_slot_counts_are_capped_by_line_counts(self):
        group = "/aws/lambda/owner-d-approx"  # count_distinct is approximate at high cardinality
        payload, _, _ = run(platform_rows(group, 40, slots=45) + [row(group, RETRY, 120, slots=130),
                                                                  row(group, "<other>", 140)], [group])
        data = source_data(payload, group)
        self.assertEqual((data["invocation_slots"], data["messages"][0]["slots"]), (40, 120))

    def test_rows_without_log_group_make_every_group_unevaluable(self):
        group = "/aws/lambda/owner-d-retry"
        rows = retry_group(group) + [{"message": RETRY, "occurrences": "5"}]
        payload, result, normalized = run(rows, [group])
        self.assertEqual(payload["sources"], [])
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("had no @log field", normalized["limitations"][0])

    def test_rows_for_unqueried_groups_are_ignored(self):
        group = "/aws/lambda/owner-d-retry"
        _, result, normalized = run(retry_group(group) + [row("/aws/lambda/elsewhere", RETRY, 999)], [group])
        self.assertEqual(len(result["findings"]), 1)
        self.assertIn("named a log group that was not queried", normalized["limitations"][0])

    def test_malformed_source_data_is_omitted(self):
        good, bad = "/aws/lambda/owner-d-good", "/aws/lambda/owner-d-bad"
        payload = make_input(obs11.normalize_logs_insights(raw(retry_group(good) + retry_group(bad), [good, bad])))
        broken = next(s for s in payload["sources"] if s["scope_id"] == scope(bad))["data"]
        broken["application_events"] = 7
        result = obs11.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(good)]))
        self.assertTrue(any(n.startswith(f"{scope(bad)}: application_events must equal events - platform_events")
                            for n in result["coverage"]["limitations"]))
        for mutate, problem in ((lambda d: d.pop("window"), "missing fields: window"),
                                (lambda d: d["messages"][0].update(occurrences=0), "occurrences must be a positive"),
                                (lambda d: d.update(log_group="/aws/lambda/other"), "does not match log_group"),
                                (lambda d: d.update(invocations=0), "invocations must be a positive integer or null"),
                                (lambda d: d["messages"][0].update(slots=d["messages"][0]["occurrences"] + 1),
                                 "slots must be a positive integer no greater than occurrences"),
                                (lambda d: d["messages"][0].pop("slots"), "slots must be a positive integer"),
                                (lambda d: d.update(invocation_slots=None), "invocation_slots must be a positive"),
                                (lambda d: d.update(invocation_slots=d["invocations"] + 1),
                                 "invocation_slots must be a positive integer no greater than invocations"),
                                (lambda d: d.update(invocations=None), "invocation_slots must be null"),
                                (lambda d: d.pop("invocation_slots"), "missing fields: invocation_slots")):
            payload2 = copy.deepcopy(payload)
            mutate(next(s for s in payload2["sources"] if s["scope_id"] == scope(bad))["data"])
            result2 = obs11.evaluate(payload2)
            validate_pair(payload2, result2)
            self.assertNotIn(scope(bad), result2["coverage"]["evaluated_scope"])
            self.assertTrue(any(problem in n for n in result2["coverage"]["limitations"]), problem)

    def test_unusable_raw_data_raises(self):
        for bad in ({"rows": "x", "log_groups": []}, {"rows": [], "log_groups": "g"}, []):
            with self.assertRaises(obs11.NormalizationError):
                obs11.normalize_logs_insights(bad)

    def test_wrong_check_or_version_raises(self):
        group = "/aws/lambda/owner-d-retry"
        payload = make_input(obs11.normalize_logs_insights(raw(retry_group(group), [group])))
        for field, value in (("check_id", "OBS-07"), ("detector_version", "0.9.0"), ("kind", "result")):
            with self.assertRaises(obs11.EvaluationError):
                obs11.evaluate({**payload, field: value})


class BoundaryTests(unittest.TestCase):  # OBS11-06
    GROUP = "/aws/lambda/owner-d-edge"

    def findings(self, rows, context=None):
        return run(rows, [self.GROUP], context)[1]

    def test_repeats_must_exceed_min_repeats(self):
        base = platform_rows(self.GROUP, 10)
        at = self.findings(base + [row(self.GROUP, RETRY, 50), row(self.GROUP, "<other>", 50)])
        over = self.findings(base + [row(self.GROUP, RETRY, 51), row(self.GROUP, "<other>", 49)])
        self.assertEqual((len(at["findings"]), len(over["findings"])), (0, 1))

    def test_share_must_exceed_min_share(self):
        context = {**CONTEXT, "min_repeats": 9}
        base = platform_rows(self.GROUP, 5)
        at = self.findings(base + [row(self.GROUP, RETRY, 20), row(self.GROUP, "<other>", 80)], context)
        over = self.findings(base + [row(self.GROUP, RETRY, 21), row(self.GROUP, "<other>", 79)], context)
        self.assertEqual((at["status"], len(at["findings"]), len(over["findings"])), ("completed", 0, 1))

    def test_min_events_is_inclusive(self):
        context = {**CONTEXT, "min_repeats": 9}
        below = self.findings([row(self.GROUP, RETRY, 60), row(self.GROUP, "<other>", 39)], context)
        at = self.findings([row(self.GROUP, RETRY, 60), row(self.GROUP, "<other>", 40)], context)
        self.assertEqual((below["status"], at["status"], len(at["findings"])), ("unavailable", "completed", 1))

    def test_more_than_once_per_invocation_is_required(self):
        same = self.findings(retry_group(self.GROUP, retries=120, invocations=120))
        one_more = self.findings(retry_group(self.GROUP, retries=121, invocations=120))
        self.assertEqual((len(same["findings"]), len(one_more["findings"])), (0, 1))

    def test_clustering_must_exceed_the_cluster_factor(self):
        # 100 invocations in 100 slots: 60 lines in 30 slots is exactly CLUSTER_FACTOR (2.0) lines per slot.
        self.assertEqual(obs11.CLUSTER_FACTOR, 2)
        base = platform_rows(self.GROUP, 100) + [row(self.GROUP, "<other>", 140)]
        at = self.findings(base + [row(self.GROUP, RETRY, 60, slots=30)])
        over = self.findings(base + [row(self.GROUP, RETRY, 60, slots=29)])
        self.assertEqual((len(at["findings"]), len(over["findings"])), (0, 1))

    def test_messages_beyond_the_cap_are_counted_but_not_supplied(self):
        rows = [row(self.GROUP, f"message {chr(97 + i)}", 10 + i) for i in range(obs11.MAX_MESSAGES + 2)]
        payload, result, _ = run(rows, [self.GROUP], {**CONTEXT, "min_repeats": 9, "min_share": 0})
        data = source_data(payload, self.GROUP)
        self.assertEqual((len(data["messages"]), data["omitted_messages"]), (obs11.MAX_MESSAGES, 2))
        self.assertEqual(data["application_events"], sum(10 + i for i in range(obs11.MAX_MESSAGES + 2)))
        self.assertIn(f"{scope(self.GROUP)}: 2 less frequent messages were not supplied",
                      result["coverage"]["limitations"])


class QueryAndRegistryTests(unittest.TestCase):
    def test_query_shape(self):
        query = obs11.LOGS_INSIGHTS_QUERY
        self.assertIn("regexReplace(", query)
        self.assertIn("by @log, normalized", query)
        self.assertIn(f'concat(@logStream, " ", toMillis(datefloor(@timestamp, {obs11.SLOT_PERIOD}))) as slot', query)
        self.assertIn("count_distinct(slot) as n_slots", query)
        self.assertIn("sum(n_slots) as slots", query)
        self.assertIn(f"if(n >= {obs11.FOLD_THRESHOLD} or normalized like /^(START|END|REPORT) RequestId: ", query)
        self.assertIn("| sort @log asc, occurrences desc", query)
        self.assertNotIn("\\d", query)  # regexReplace patterns avoid backslash escapes
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        self.assertIn(f"```text\n{query}\n```", readme)  # the documented query is the one that runs

    def test_registry_defaults_are_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == "OBS-11")
        self.assertEqual((check.module, check.source, check.adapter), ("owner_d.obs11", "logs_insights", None))
        self.assertEqual(check.defaults, obs11.REFERENCE_SETTINGS)
        module, normalize = registry.load(check)
        self.assertIs(normalize, obs11.normalize_logs_insights)


class RecordedLogs(FakeLogs):
    """StartQuery/GetQueryResults that replay the recorded demo response."""

    def get_query_results(self, queryId):
        self.calls.append(("get_query_results", queryId))
        response = copy.deepcopy(RECORDED["response"])
        response["results"] = [r + [{"field": "@ptr", "value": "p"}] for r in response["results"]]
        return response


class LogAnalyzerEndToEndTests(AwsTestCase):
    """Collector -> registry normalizer -> OBS-11 -> validate_pair -> PutEvents -> findings-hub writer."""

    def test_recorded_demo_response_is_validated_and_published(self):
        logs = self.fakes["logs"] = RecordedLogs([{"logGroupName": DEMO}])
        out = log_handler.lambda_handler(self.base_event(checks=["OBS-11"], logs={"log_groups": [DEMO]},
                                                         settings={"OBS-11": DEMO_CONTEXT}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "OBS-11", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        self.assertEqual(out["collection"]["OBS-11"]["bytes_scanned"],
                         RECORDED["response"]["statistics"]["bytesScanned"])
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual((start["queryString"], start["logGroupNames"]), (obs11.LOGS_INSIGHTS_QUERY, [DEMO]))
        entry = self.fakes["events"].entries[0]
        self.assertNotIn("123456789012", entry["Detail"])
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["context"]["min_repeats"], 10)
        self.assertEqual(result["context"]["collection"]["log_groups"], [DEMO])
        self.assertIn("upstream inventory-svc unavailable, retrying", result["findings"][0]["summary"])
        summary = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                 "id": "evt-obs11"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(summary["outcome"], "stored")

    def test_reference_defaults_apply_without_overrides(self):
        self.fakes["logs"] = RecordedLogs([{"logGroupName": DEMO}])
        out = log_handler.lambda_handler(self.base_event(checks=["OBS-11"], logs={"log_groups": [DEMO]}, dry_run=True))
        self.assertEqual(out["results"][0]["status"], "unavailable")
        self.assertEqual(out["result_payloads"][0]["context"]["min_events"], 100)


if __name__ == "__main__":
    unittest.main()
