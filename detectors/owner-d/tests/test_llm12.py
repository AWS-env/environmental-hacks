"""LLM-12 per-agent isolated caches: the Logs Insights query, the normalizer, the detector and the log-analyzer route.

Every input is synthetic. `insights()` below is a Python model of `llm12.LOGS_INSIGHTS_QUERY` (coalesce, filter,
two-level stats and the missed-by buckets); it turns cache-lookup log records, including the telemetry demo's
LLM-12 lines, into the rows log_handler.run_insights_query returns. The query itself has not been run against
CloudWatch Logs for these tests."""

import copy
import importlib.util
import json
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

from tests.aws_fakes import NOW, AwsTestCase, FakeLogs, FakeTable

from findings_hub import writer
from owner_d import cli, llm12
from owner_d.aws import log_handler, registry
from shared.contracts.validation import validate, validate_pair

DETECTOR_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm12"
DEMO = "/aws/lambda/owner-d-telemetry-demo"
ACCOUNT = "123456789012"  # AWS documentation placeholder
REPO = "github:AWS-env/example"
WINDOW = {"start": "2026-10-09T12:00:00Z", "end": "2026-10-10T12:00:00Z"}
SETTINGS = copy.deepcopy(llm12.REFERENCE_SETTINGS)

_spec = importlib.util.spec_from_file_location("owner_d_telemetry_demo_llm12",
                                               DETECTOR_DIR / "examples" / "telemetry-demo" / "handler.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def _coalesce(record, *fields, default=None):
    return next((record[f] for f in fields if record.get(f) is not None), default)


def insights(events):
    """Rows of llm12.LOGS_INSIGHTS_QUERY over (log group, log stream, JSON record) events. Values are strings and
    an empty backend is left out of the row, as Logs Insights leaves out empty fields."""
    per_key = defaultdict(lambda: {"lookups": 0, "hits": 0, "holders": set(), "marks": set(), "agents": set()})
    for group, stream, record in events:
        result = str(_coalesce(record, "cache_result", "cache_status", default="")).lower()
        key = _coalesce(record, "cache_key_hash", "prompt_hash", "cache_key", default="")
        cache = str(_coalesce(record, "cache_name", default="default"))[:llm12.MAX_CACHE_NAME]
        backend = str(_coalesce(record, "cache_backend", default="")).lower()[:llm12.MAX_BACKEND]
        agent = _coalesce(record, "agent_id", "agent_name", default="")
        if result not in ("hit", "miss") or key == "":
            continue
        holder = f"{ACCOUNT}:{group} {stream} {agent}"
        entry = per_key[(cache, backend, key)]
        entry["lookups"] += 1
        entry["hits"] += result == "hit"
        entry["holders"].add(holder)
        entry["marks"].add("-" if result == "hit" else holder)
        entry["agents"].add(agent)
    buckets = defaultdict(lambda: {"keys": 0, "lookups": 0, "hits": 0, "missing_holders": 0, "max_holders": 0,
                                   "max_agents": 0})
    for (cache, backend, _), entry in per_key.items():
        missed_by = len(entry["marks"]) - (1 if entry["hits"] > 0 else 0)
        row = buckets[(cache, backend, min(missed_by, llm12.BUCKET_CAP))]
        row["keys"] += 1
        row["lookups"] += entry["lookups"]
        row["hits"] += entry["hits"]
        row["missing_holders"] += missed_by
        row["max_holders"] = max(row["max_holders"], len(entry["holders"]))
        row["max_agents"] = max(row["max_agents"], len(entry["agents"]))
    rows = []
    for (cache, backend, bucket), counts in sorted(buckets.items()):
        row = {"o12_cache": cache, "o12_bucket": str(bucket), **{k: str(v) for k, v in counts.items()}}
        if backend:
            row["o12_backend"] = backend
        rows.append(row)
    return rows


def demo_events(stream="2026/10/10/[$LATEST]0123456789abcdef", **event):
    lines = []
    result = demo.run({"scenario": "LLM-12", **event}, write=lines.append)
    return result, [(DEMO, stream, json.loads(line)) for line in lines]


def lookups(cache, holders, keys, *, backend="memory", hits_after=0, stream="s1", group=DEMO, prefix="k"):
    """Each of `holders` agents misses each of `keys` keys once and then hits it `hits_after` times."""
    events = []
    for k in range(keys):
        for agent in holders:
            for i in range(1 + hits_after):
                record = {"cache_name": cache, "agent_id": agent, "cache_key_hash": f"{prefix}{k:04x}",
                          "cache_result": "miss" if i == 0 else "hit"}
                if backend:
                    record["cache_backend"] = backend
                events.append((group, stream, record))
    return events


def raw(rows, groups=(DEMO,), **extra):
    return {"rows": rows, "log_groups": list(groups), "window": dict(WINDOW), "truncated": False,
            "region": "ap-south-1", **extra}


def payload_for(normalized, settings=None, scope=None):
    return {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "scan-1",
            "commit_sha": "0123456789abcdef0123456789abcdef01234567", "check_id": "LLM-12",
            "detector_version": llm12.DETECTOR_VERSION,
            "context": copy.deepcopy(SETTINGS if settings is None else settings),
            "scope": scope or normalized["scope"] or [llm12.scope_id_for("none")], "sources": normalized["sources"]}


def run(raw_data, settings=None, scope=None):
    settings = copy.deepcopy(SETTINGS if settings is None else settings)
    normalized = registry.normalize(llm12.normalize_logs_insights, raw_data, settings)
    payload = payload_for(normalized, settings, scope)
    result = llm12.evaluate(payload)
    validate_pair(payload, result)
    return result, normalized


def scope(cache):
    return llm12.scope_id_for(cache)


def notes(result):
    return " ".join(result["coverage"]["limitations"])


class Llm12DemoTests(unittest.TestCase):
    """LLM12-01: the telemetry demo's opt-in LLM-12 scenario, through the query model."""

    def test_isolated_caches_are_flagged_and_the_shared_cache_is_clean(self):
        emitted, events = demo_events()
        self.assertEqual(emitted["emitted"]["LLM-12"], {"waste_lookups": 120, "waste_misses": 40,
                                                       "control_lookups": 120, "control_misses": 8})
        result, normalized = run(raw(insights(events)))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope("demo-agent-local"), scope("demo-fleet-shared")])
        self.assertEqual([(f["scope_id"], f["identity"], f["confidence"]) for f in result["findings"]],
                         [(scope("demo-agent-local"), "isolated-agent-caches", "high")])
        summary = result["findings"][0]["summary"]
        self.assertIn("8 of 8 keys looked up between 2026-10-09T12:00:00Z and 2026-10-10T12:00:00Z were missed by two "
                      "or more separate agent caches", summary)
        self.assertIn("up to 5 caches for one key", summary)
        self.assertIn("32 of the 40 misses (26.7% of 120 lookups)", summary)
        self.assertIn("Observed hit rate 66.7%; one cache shared by these agents could reach about 93.3%", summary)
        self.assertEqual([e["field"] for e in result["findings"][0]["evidence"]],
                         ["missed_by_histogram", "lookups", "hits", "backends"])
        control = normalized["sources"][1]["data"]
        self.assertEqual((control["lookups"], control["hits"], control["backends"]),
                         (120, 112, {"demo-shared": {"lookups": 120, "hits": 112, "keys": 8}}))
        self.assertEqual(llm12.redundant_misses(control), (0, 0))

    def test_paths_alone(self):
        waste, _ = run(raw(insights(demo_events(path="waste")[1])))
        self.assertEqual([f["scope_id"] for f in waste["findings"]], [scope("demo-agent-local")])
        control, _ = run(raw(insights(demo_events(path="control")[1])))
        self.assertEqual((control["status"], control["findings"]), ("completed", []))
        self.assertNotIn("misses on keys another agent's cache had already missed", notes(control))

    def test_two_agents_one_round_stay_below_min_lookups(self):
        result, _ = run(raw(insights(demo_events(agents=2, rounds=1, path="waste")[1])))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("only 16 cache lookups in the window, fewer than min_lookups 100", notes(result))

    def test_no_keys_agents_or_account_ids_reach_the_result(self):
        _, events = demo_events()
        text = json.dumps(run(raw(insights(events)))[0])
        keys = {record["cache_key_hash"] for _, _, record in events}
        for forbidden in (ACCOUNT, "planner", "researcher", "synthetic answer", *keys):
            self.assertNotIn(forbidden, text)

    def test_demo_is_opt_in_and_clamps_its_knobs(self):
        self.assertNotIn("LLM-12", demo.SCENARIOS)
        self.assertEqual(demo.run({"scenario": "llm12"}, write=lambda _: None)["scenarios"], ["LLM-12"])
        result, events = demo_events(agents=99, rounds=99, path="waste")
        self.assertEqual(result["emitted"]["LLM-12"], {"waste_lookups": 8 * 8 * 5, "waste_misses": 8 * 8})
        records = [r for _, _, r in events]
        self.assertTrue(all(r["synthetic"] is True and r["check"] == "LLM-12" for r in records))
        self.assertTrue(all(set(r) >= {"agent_id", "cache_name", "cache_backend", "cache_result", "cache_key_hash"}
                            for r in records))
        self.assertNotIn("refund", json.dumps(records))  # question text is never logged
        with self.assertRaises(ValueError):
            demo.run({"scenario": "LLM-12", "path": "sometimes"}, write=lambda _: None)

    def test_cli_fixture_matches_the_demo_rows(self):
        _, events = demo_events()
        normalized = registry.normalize(llm12.normalize_logs_insights, raw(insights(events)), SETTINGS)
        payload = json.loads((FIXTURES / "llm12-01-demo-input.json").read_text())
        self.assertEqual((payload["scope"], payload["sources"], payload["context"]),
                         (normalized["scope"], normalized["sources"], SETTINGS))
        result = llm12.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual([f["identity"] for f in result["findings"]], ["isolated-agent-caches"])
        self.assertIs(cli.DETECTORS["LLM-12"], llm12)


class Llm12PositiveTests(unittest.TestCase):
    """LLM12-01 synthetic fleets."""

    def test_replicas_of_one_agent_in_separate_log_streams_are_separate_caches(self):
        events = []
        for stream in ("env-a", "env-b", "env-c", "env-d"):
            events += lookups("answers", ["support"], 10, backend="", hits_after=2, stream=stream)
        result, normalized = run(raw(insights(events)))
        finding = result["findings"][0]
        self.assertEqual(finding["confidence"], "medium")  # backend not logged
        self.assertIn("30 of the 40 misses (25.0% of 120 lookups)", finding["summary"])
        self.assertEqual(normalized["sources"][0]["data"]["max_agents_per_key"], 1)

    def test_agents_in_separate_log_groups_share_one_cache_name(self):
        group_b = "/aws/lambda/owner-d-agent-b"
        events = (lookups("answers", ["a1"], 30, hits_after=1) +
                  lookups("answers", ["b1"], 30, hits_after=1, group=group_b))
        result, normalized = run(raw(insights(events), groups=[DEMO, group_b]))
        self.assertEqual([f["scope_id"] for f in result["findings"]], [scope("answers")])
        self.assertEqual(normalized["sources"][0]["data"]["log_groups"], sorted([DEMO, group_b]))

    def test_keys_missed_by_more_holders_than_the_bucket_cap_are_counted_exactly(self):
        agents = [f"agent-{i}" for i in range(25)]
        result, normalized = run(raw(insights(lookups("answers", agents, 4))))
        histogram = normalized["sources"][0]["data"]["missed_by_histogram"]
        self.assertEqual(histogram, [{"missed_by": 20, "keys": 4, "lookups": 100, "hits": 0, "missing_holders": 100}])
        self.assertIn("96 of the 100 misses", result["findings"][0]["summary"])

    def test_aliases_are_read(self):
        events = [(DEMO, "s", {"cache_status": "MISS" if i < 4 else "HIT", "prompt_hash": f"p{k}",
                               "agent_name": f"n{i % 4}", "cache_name": "aliases"})
                  for k in range(10) for i in range(12)]
        result, _ = run(raw(insights(events)))
        self.assertIn("30 of the 40 misses", result["findings"][0]["summary"])

    def test_fingerprints_ignore_values(self):
        small = run(raw(insights(lookups("answers", ["a", "b", "c"], 20, hits_after=1))))[0]["findings"][0]
        large = run(raw(insights(lookups("answers", ["a", "b", "c", "d"], 60))))[0]["findings"][0]
        self.assertEqual(small["fingerprint"], large["fingerprint"])
        self.assertNotEqual(small["summary"], large["summary"])


class Llm12NegativeTests(unittest.TestCase):
    """LLM12-02/03: shared caches, per-holder churn, shared backends and lines that are not cache lookups."""

    def test_one_holder_missing_the_same_key_again_is_not_cross_agent(self):
        events = []
        for _ in range(4):  # TTL churn inside one process: miss, then hits, then miss again
            events += lookups("answers", ["solo"], 10, hits_after=2)
        result, normalized = run(raw(insights(events)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertEqual(llm12.redundant_misses(normalized["sources"][0]["data"]), (0, 0))
        self.assertIn("every key was looked up by a single agent cache", notes(result))

    def test_shared_backend_is_treated_as_already_shared(self):
        events = lookups("answers", ["a", "b", "c", "d"], 30) + lookups("answers", ["a"], 1, backend="Redis")
        result, _ = run(raw(insights(events)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("lookups are logged against a shared backend (redis)", notes(result))
        flagged, _ = run(raw(insights(events)), {**SETTINGS, "shared_backends": []})
        self.assertEqual(len(flagged["findings"]), 1)

    def test_partly_shared_fleet_under_the_share_is_noted(self):
        # 2 holders miss 10 of 200 keys: 10 redundant misses, at most 2.4% of the lookups
        events = lookups("answers", ["a", "b"], 10) + lookups("answers", ["a"], 190, hits_after=1, prefix="u")
        result, _ = run(raw(insights(events)))
        self.assertEqual(result["findings"], [])
        self.assertIn("10 misses on keys another agent's cache had already missed", notes(result))

    def test_unrelated_lines_and_lines_without_a_key_are_ignored(self):
        events = [(DEMO, "s", {"message": "hello", "level": "INFO"}),
                  (DEMO, "s", {"cache_result": "miss", "agent_id": "a"}),
                  (DEMO, "s", {"cache_result": "stale", "cache_key": "k", "agent_id": "a"})]
        self.assertEqual(insights(events), [])
        out = registry.normalize(llm12.normalize_logs_insights, raw([]), SETTINGS)
        self.assertEqual((out["scope"], out["sources"]), ([], []))
        self.assertIn("no cache-lookup lines", out["limitations"][0])


class Llm12IncompleteTests(unittest.TestCase):
    """LLM12-04/05: missing evidence, invalid settings, truncated and malformed rows."""

    def rows(self):
        return insights(lookups("answers", ["a", "b", "c"], 20, hits_after=1))

    def test_missing_or_invalid_settings_are_unavailable(self):
        cases = [{k: v for k, v in SETTINGS.items() if k != "min_lookups"}, {**SETTINGS, "min_lookups": 0},
                 {**SETTINGS, "min_redundant_misses": -1}, {**SETTINGS, "min_redundant_share": 1},
                 {**SETTINGS, "min_redundant_share": True}, {**SETTINGS, "shared_backends": "redis"},
                 {**SETTINGS, "shared_backends": [""]}]
        for settings in cases:
            with self.subTest(settings=settings):
                result, _ = run(raw(self.rows()), settings)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertTrue(result["coverage"]["limitations"][0].startswith("Missing or invalid context settings"))

    def test_row_limit_leaves_every_cache_unevaluated(self):
        result, _ = run(raw(self.rows(), truncated=True))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("row limit", notes(result))

    def test_malformed_rows_leave_only_their_cache_unevaluated(self):
        other = insights(lookups("other", ["a", "b", "c"], 20, hits_after=1))
        broken = [dict(r, missing_holders=str(int(r["missing_holders"]) + 1)) for r in self.rows()]
        for name, rows in (("inconsistent", broken), ("not a number", [dict(r, lookups="x") for r in self.rows()]),
                           ("bucket out of range", [dict(r, o12_bucket="21") for r in self.rows()])):
            with self.subTest(name):
                result, _ = run(raw(rows + other))
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], [scope("other")])
                self.assertIn(f"{scope('answers')}: not evaluated: row", notes(result))

    def test_unattributed_rows_and_non_list_rows(self):
        rows = self.rows() + [{"o12_bucket": "1", "keys": "1", "lookups": "1", "hits": "0", "missing_holders": "1"}]
        result, normalized = run(raw(rows))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("1 Logs Insights rows had no o12_cache value", " ".join(normalized["limitations"]))
        out = llm12.normalize_logs_insights({"rows": None, "log_groups": [DEMO]})
        self.assertEqual(out["sources"], [])
        self.assertIn("no rows list", out["limitations"][0])
        self.assertIn("not an object", llm12.normalize_logs_insights([])["limitations"][0])

    def test_missing_or_duplicate_sources_and_tampered_data(self):
        _, normalized = run(raw(self.rows()))
        source = normalized["sources"][0]
        cases = {
            "no source": ([], "no telemetry source supplied"),
            "two sources": ([source, dict(source, source_id="x")], "multiple telemetry sources"),
            "hits above lookups": ([{**source, "data": {**source["data"], "hits": 999}}], "must add up to hits"),
            "wrong scope": ([{**source, "scope_id": scope("answers"), "data": {**source["data"], "resource_id": "x"}}],
                            "scope id does not match"),
            "missing field": ([{**source, "data": {k: v for k, v in source["data"].items() if k != "backends"}}],
                              "missing fields: backends"),
            "bucket mismatch": ([{**source, "data": {**source["data"], "missed_by_histogram": [
                dict(b, missing_holders=b["missing_holders"] + 1) for b in source["data"]["missed_by_histogram"]]}}],
                "does not match its keys and misses"),
        }
        for name, (sources, message) in cases.items():
            with self.subTest(name):
                payload = payload_for({"scope": [scope("answers")], "sources": sources})
                result = llm12.evaluate(payload)
                validate_pair(payload, result)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(message, notes(result))

    def test_bad_payloads_raise(self):
        _, normalized = run(raw(self.rows()))
        good = payload_for(normalized)
        for change in ({"check_id": "OBS-17"}, {"kind": "result"}, {"detector_version": "0.9.0"}, {"scope": []},
                       {"sources": None}, {"schema_version": "2.0"}):
            with self.subTest(change=change), self.assertRaises(llm12.EvaluationError):
                llm12.evaluate({**good, **change})


class Llm12BoundaryTests(unittest.TestCase):
    """LLM12-06: thresholds are strict; min_lookups is inclusive."""

    def evaluate_counts(self, redundant_keys, holders, unique_keys, settings=None):
        events = (lookups("answers", [f"h{i}" for i in range(holders)], redundant_keys) +
                  lookups("answers", ["h0"], unique_keys, prefix="u"))
        return run(raw(insights(events)), settings)[0]

    def test_redundant_misses_must_exceed_the_minimum(self):
        # 10 keys x 3 holders: 20 redundant misses of 30 + 70 lookups -> 20% share
        self.assertEqual(self.evaluate_counts(10, 3, 70)["findings"], [])
        self.assertEqual(len(self.evaluate_counts(10, 3, 70, {**SETTINGS, "min_redundant_misses": 19})["findings"]), 1)

    def test_share_must_exceed_the_minimum(self):
        # 21 keys x 2 holders: 21 redundant of 42 + 168 = 210 lookups -> exactly 10%
        self.assertEqual(self.evaluate_counts(21, 2, 168)["findings"], [])
        self.assertEqual(len(self.evaluate_counts(21, 2, 167)["findings"]), 1)

    def test_min_lookups_is_inclusive(self):
        self.assertEqual(self.evaluate_counts(25, 2, 50)["status"], "completed")  # exactly 100 lookups
        self.assertEqual(self.evaluate_counts(25, 2, 49)["status"], "unavailable")


class Llm12QueryTests(unittest.TestCase):
    def test_query_shape_and_readme(self):
        query = llm12.LOGS_INSIGHTS_QUERY
        for part in ('coalesce(cache_result, cache_status, "")', 'coalesce(cache_key_hash, prompt_hash, cache_key, "")',
                     "concat(@log, \" \", @logStream, \" \", o12_agent) as o12_holder",
                     "count_distinct(o12_miss_mark) as o12_marks", "by o12_cache, o12_backend, o12_key",
                     f"least(o12_marks - if(o12_hits > 0, 1, 0), {llm12.BUCKET_CAP}) as o12_bucket",
                     "by o12_cache, o12_backend, o12_bucket"):
            self.assertIn(part, query)
        self.assertEqual(query.count("| stats "), 2)  # Logs Insights allows at most two stats commands
        readme = (DETECTOR_DIR / "README.md").read_text()
        self.assertIn(f"```text\n{query}\n```", readme)  # the documented query is the one that runs

    def test_rows_carry_counts_only(self):
        rows = insights(demo_events()[1])
        self.assertLessEqual(set().union(*rows), {"o12_cache", "o12_backend", "o12_bucket", "keys", "lookups", "hits",
                                                  "missing_holders", "max_holders", "max_agents"})


class Llm12RegistryTests(unittest.TestCase):
    def test_registered_on_the_log_analyzer_with_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == "LLM-12")
        self.assertEqual((check.source, check.adapter, check.normalizer), ("logs_insights", None, None))
        self.assertEqual(check.defaults, llm12.REFERENCE_SETTINGS)
        module, normalize = registry.load(check)
        self.assertIs(normalize, llm12.normalize_logs_insights)
        self.assertEqual(set(llm12.SETTING_KEYS), set(check.defaults))
        self.assertEqual(llm12.SUPPORTED_KIND, "telemetry")


class Llm12ThroughTheLogAnalyzerTests(AwsTestCase):
    """End to end: stubbed StartQuery/GetQueryResults with the modeled demo rows -> normalizer -> detector ->
    validate_pair -> PutEvents, and the findings-hub writer stores the published event."""

    def test_demo_rows_are_validated_and_published(self):
        rows = insights(demo_events()[1])
        logs = self.fakes["logs"] = FakeLogs(rows=rows)
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-12"], logs={"log_groups": [DEMO]}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-12", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 1}])
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual((start["queryString"], start["logGroupNames"]), (llm12.LOGS_INSIGHTS_QUERY, [DEMO]))
        entry = self.fakes["events"].entries[0]
        self.assertNotIn(ACCOUNT, entry["Detail"])
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["findings"][0]["identity"], "isolated-agent-caches")
        self.assertEqual(result["context"]["collection"]["source"], "cloudwatch-logs-insights")
        self.assertEqual({k: result["context"][k] for k in llm12.SETTING_KEYS}, llm12.REFERENCE_SETTINGS)
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-llm12"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_row_limit_reached_publishes_an_unavailable_result_not_a_clean_one(self):
        self.fakes["logs"] = FakeLogs(rows=insights(demo_events()[1]))
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-12"], logs={"log_groups": [DEMO], "limit": 2},
                                                         dry_run=True))
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("Logs Insights returned the 2-row limit; more matching rows may exist",
                      result["coverage"]["limitations"])

    def test_no_cache_lines_skips_the_check_with_a_reason(self):
        self.fakes["logs"] = FakeLogs(rows=[])
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-12"], logs={"log_groups": [DEMO]}))
        self.assertEqual((out["published"], out["results"]), (0, []))
        self.assertIn("no cache-lookup lines", json.dumps(out["skipped"]))


if __name__ == "__main__":
    unittest.main()
