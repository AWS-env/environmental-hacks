"""LLM-12 per-agent isolated caches: the Logs Insights query, the normalizer, the detector, the log-analyzer route
and the telemetry-demo scenario.

Case IDs follow the verification plan on issue #205. Every input is synthetic. `simulate()` re-implements the
query stage by stage in Python (with the module's own parse patterns) so demo log lines can be pushed through
query -> normalizer -> detector offline; it is not evidence of how the live service evaluates the query."""

import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.aws_fakes import NOW, AwsTestCase, FakeLogs, FakeTable

from findings_hub import writer
from owner_d import cli, llm12
from owner_d.aws import log_handler, registry
from shared.contracts.validation import validate, validate_pair

DEMO = "/aws/lambda/owner-d-telemetry-demo"
ACCOUNT = "123456789012"  # AWS documentation placeholder
REPO = "github:AWS-env/example"
SETTINGS = dict(llm12.REFERENCE_SETTINGS)
HANDLER = Path(__file__).resolve().parents[1] / "examples" / "telemetry-demo" / "handler.py"
_spec = importlib.util.spec_from_file_location("owner_d_telemetry_demo_llm12", HANDLER)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def row(group, kind, keys, lookups, misses, duplicated=0, named=None, max_agents=3, max_missed=None,
        examples=("key-a", "key-z")):
    """One final-stage Logs Insights row; values are strings as GetQueryResults returns them."""
    out = {"@log": f"{ACCOUNT}:{group}", "o12_kind": kind, "keys": str(keys), "lookups": str(lookups),
           "misses": str(misses), "duplicated_misses": str(duplicated),
           "named_lookups": str(lookups if named is None else named), "max_agents": str(max_agents),
           "max_missed_agents": str(max_agents if max_missed is None else max_missed)}
    if examples:
        out["example_first"], out["example_last"] = examples
    return out


def fleet(group, duplicated_keys=10, agents=3, other_keys=20, named=True):
    """`agents` agents each miss `duplicated_keys` shared keys once and hit them once; `other_keys` keys are
    missed once by one agent and then hit by the others."""
    dup_lookups = duplicated_keys * agents * 2
    other_lookups = other_keys * agents * 2
    rows = [row(group, "other", other_keys, other_lookups, other_keys, named=None if named else 0, max_missed=1)]
    if duplicated_keys:
        rows.append(row(group, "duplicated", duplicated_keys, dup_lookups, duplicated_keys * agents,
                        duplicated_keys * (agents - 1), named=None if named else 0, max_agents=agents))
    return rows


def raw(rows, groups, **extra):
    window = {"start": "2026-10-09T12:00:00Z", "end": "2026-10-10T12:00:00Z"}
    return {"rows": rows, "log_groups": groups, "window": window, "truncated": False, "region": "ap-south-1", **extra}


def run(raw_data, settings=None, scope=None):
    settings = dict(SETTINGS if settings is None else settings)
    normalized = registry.normalize(llm12.normalize_logs_insights, raw_data, settings)
    payload = {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "scan-1",
               "commit_sha": "0123456789abcdef0123456789abcdef01234567", "check_id": "LLM-12",
               "detector_version": llm12.DETECTOR_VERSION, "context": settings,
               "scope": scope or normalized["scope"] or ["resource:log-group/none"], "sources": normalized["sources"]}
    result = llm12.evaluate(payload)
    validate_pair(payload, result)
    return result, normalized


def scope(group):
    return llm12.scope_id_for(group)


def notes(result):
    return " ".join(result["coverage"]["limitations"])


# ---- a Python model of LOGS_INSIGHTS_QUERY -----------------------------------------------------------------


def _python(pattern):
    return re.compile(pattern.replace("(?<", "(?P<"))


PREFILTER = re.compile(r'"(cache_hit|cacheHit|cache_status|cacheStatus)"\s*:')
PATTERNS = {name: _python(p) for name, p in (("o12_hit", llm12.HIT_PATTERN), ("o12_status", llm12.STATUS_PATTERN),
                                              ("o12_key", llm12.KEY_PATTERN), ("o12_agent", llm12.AGENT_PATTERN))}
HIT = re.compile(llm12.HIT_VALUES)


def parsed(message):
    out = {}
    for name, pattern in PATTERNS.items():
        match = pattern.search(message)
        if match:
            out[name] = match.group(name)
    return out


def simulate(events):
    """events: (log group, log stream, epoch ms, message) -> the query's final rows (string values)."""
    per_agent = {}
    for group, stream, ms, message in events:
        if not PREFILTER.search(message):
            continue
        fields = parsed(message)
        if "o12_key" not in fields or not ("o12_hit" in fields or "o12_status" in fields):
            continue
        hit = bool(HIT.match(fields.get("o12_hit") or fields["o12_status"]))
        key = (group, fields["o12_key"], fields.get("o12_agent") or stream)
        lookups, misses, named, first = per_agent.get(key, (0, 0, 0, llm12.NO_MISS))
        per_agent[key] = (lookups + 1, misses + (not hit), named + ("o12_agent" in fields),
                          first if hit else min(first, ms))
    per_key = {}
    for (group, cache_key, _), (lookups, misses, named, first) in per_agent.items():
        entry = per_key.setdefault((group, cache_key), [0, 0, 0, 0, 0, llm12.NO_MISS, 0])
        entry[0] += lookups
        entry[1] += misses
        entry[2] += named
        entry[3] += 1
        entry[4] += misses > 0
        entry[5] = min(entry[5], first)
        entry[6] = max(entry[6], first if misses else 0)
    final = {}
    for (group, cache_key), (lookups, misses, named, agents, missed, first, last) in per_key.items():
        duplicated = missed >= 2 and last - first >= llm12.CONCURRENT_MISS_SECONDS * 1000
        kind = "duplicated" if duplicated else "other"
        out = final.setdefault((group, kind), {"keys": 0, "lookups": 0, "misses": 0, "duplicated_misses": 0,
                                               "named_lookups": 0, "max_agents": 0, "max_missed_agents": 0,
                                               "example_first": cache_key, "example_last": cache_key})
        out["keys"] += 1
        out["lookups"] += lookups
        out["misses"] += misses
        out["duplicated_misses"] += missed - 1 if duplicated else 0
        out["named_lookups"] += named
        out["max_agents"] = max(out["max_agents"], agents)
        out["max_missed_agents"] = max(out["max_missed_agents"], missed)
        out["example_first"] = min(out["example_first"], cache_key)
        out["example_last"] = max(out["example_last"], cache_key)
    return [{"@log": f"{ACCOUNT}:{group}", "o12_kind": kind,
             **{k: (v if k.startswith("example") else str(v)) for k, v in values.items()}}
            for (group, kind), values in sorted(final.items())]


def demo_events(agents=(1, 2, 3), path="both", gap_ms=15_000, stream="2026/10/10/[$LATEST]0123abcd"):
    """Log lines from one demo invocation per agent, `gap_ms` apart, all in one log stream (a warm Lambda)."""
    events = []
    for index, agent in enumerate(agents):
        lines = []
        demo.run({"scenario": "LLM-12", "agent": agent, "path": path}, write=lines.append)
        events += [(DEMO, stream, 1_791_633_600_000 + index * gap_ms + i, line) for i, line in enumerate(lines)]
    return events


class Llm12PositiveTests(unittest.TestCase):
    """LLM12-01: the same keys miss on several agents."""

    def test_duplicated_misses_are_flagged_with_share_agents_and_hashed_examples(self):
        group = "/aws/lambda/owner-d-agents"
        result, normalized = run(raw(fleet(group), [group]))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("completed", [scope(group)]))
        (finding,) = result["findings"]
        self.assertEqual((finding["scope_id"], finding["identity"]), (scope(group), "cross-agent-duplicate-misses"))
        self.assertIn("20 of 50 cache misses (40.0%) were for keys another agent had already missed",
                      finding["summary"])
        self.assertIn("10 keys missed on several agents, up to 3 agents per key", finding["summary"])
        self.assertIn("Hit rate 72.2% over 180 lookups", finding["summary"])
        data = normalized["sources"][0]["data"]
        self.assertEqual(data["example_key_hashes"], [llm12.key_hash("key-a"), llm12.key_hash("key-z")])
        self.assertEqual(data["example_key_hashes"][0], hashlib.sha256(b"key-a").hexdigest()[:16])
        self.assertEqual(finding["confidence"], "medium")
        self.assertEqual([e["field"] for e in finding["evidence"]],
                         ["duplicated_misses", "misses", "duplicated_keys", "max_agents_per_key",
                          "example_key_hashes", "lookups"])
        self.assertEqual(finding["fingerprint"], llm12.fingerprint(REPO, "LLM-12", scope(group), llm12.IDENTITY))
        text = json.dumps(result)
        for forbidden in ("key-a", "key-z", ACCOUNT):
            self.assertNotIn(forbidden, text)

    def test_agents_inferred_from_log_streams_are_low_confidence(self):
        group = "/aws/lambda/owner-d-agents"
        result, _ = run(raw(fleet(group, named=False), [group]))
        self.assertEqual(result["findings"][0]["confidence"], "low")
        self.assertIn("their agent is the log stream", result["findings"][0]["summary"])

    def test_each_log_group_is_judged_alone(self):
        flagged, clean = "/aws/lambda/owner-d-fleet", "/aws/lambda/owner-d-shared"
        result, _ = run(raw(fleet(flagged) + fleet(clean, duplicated_keys=0), [flagged, clean]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["scope_id"] for f in result["findings"]], [scope(flagged)])


class Llm12NegativeTests(unittest.TestCase):
    """LLM12-02 / LLM12-03: shared cache, few duplicates, concurrent misses."""

    def test_fleet_with_a_shared_cache_is_clean(self):
        group = "/aws/lambda/owner-d-shared"
        result, _ = run(raw(fleet(group, duplicated_keys=0), [group]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_few_duplicates_are_noted_not_flagged(self):
        group = "/aws/lambda/owner-d-mostly-shared"
        rows = [row(group, "other", 40, 240, 40, max_missed=1), row(group, "duplicated", 4, 24, 8, 4, max_agents=3,
                                                                      max_missed=2)]
        result, _ = run(raw(rows, [group]))
        self.assertEqual(result["findings"], [])
        self.assertIn("4 of 48 misses (8.3%) were cross-agent duplicates", notes(result))

    def test_concurrent_misses_on_several_agents_are_not_duplicates(self):
        # demo agents 1-3 invoked 2 s apart: every key misses on 3 agents, but within the concurrency window
        result, normalized = run(raw(simulate(demo_events(gap_ms=2_000)), [DEMO]))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertEqual(normalized["sources"][0]["data"]["max_missed_agents_per_key"], 3)


class Llm12MinimumTests(unittest.TestCase):
    """LLM12-06: minimum lookups and agents, strict share."""

    def test_below_min_lookups_is_not_evaluated(self):
        group = "/aws/lambda/owner-d-quiet"
        rows = [row(group, "duplicated", 8, 48, 24, 16, max_agents=3)]
        result, _ = run(raw(rows, [group]), {**SETTINGS, "min_lookups": 48})
        self.assertEqual(len(result["findings"]), 1)
        result, _ = run(raw(rows, [group]), {**SETTINGS, "min_lookups": 49})
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("only 48 cache lookups in the window, fewer than min_lookups 49", notes(result))

    def test_single_agent_or_disjoint_keys_is_not_evaluated(self):
        group = "/aws/lambda/owner-d-solo"
        result, _ = run(raw([row(group, "other", 60, 120, 60, max_agents=1)], [group]))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("no key was looked up by min_agents 2 or more agents (at most 1)", notes(result))

    def test_share_and_count_limits_are_strict(self):
        group = "/aws/lambda/owner-d-edge"
        # 10 duplicated of 50 misses = exactly 20%: not flagged; 11 of 51 is above
        at = [row(group, "other", 40, 100, 40, max_missed=1), row(group, "duplicated", 5, 20, 10, 10, max_agents=3)]
        above = [row(group, "other", 40, 100, 40, max_missed=1), row(group, "duplicated", 5, 21, 11, 11, max_agents=3)]
        self.assertEqual(run(raw(at, [group]))[0]["findings"], [])
        self.assertEqual(len(run(raw(above, [group]))[0]["findings"]), 1)
        few = [row(group, "other", 1, 50, 1, max_missed=1), row(group, "duplicated", 4, 8, 8, 4, max_agents=2)]
        self.assertEqual(run(raw(few, [group]))[0]["findings"], [])  # 4 < min_duplicated_misses 5
        self.assertEqual(len(run(raw(few, [group]), {**SETTINGS, "min_duplicated_misses": 4})[0]["findings"]), 1)

    def test_fingerprint_ignores_counts(self):
        group = "/aws/lambda/owner-d-agents"
        small = run(raw(fleet(group), [group]))[0]["findings"][0]
        large = run(raw(fleet(group, duplicated_keys=90, agents=4), [group]))[0]["findings"][0]
        self.assertEqual(small["fingerprint"], large["fingerprint"])
        self.assertNotEqual(small["summary"], large["summary"])


class Llm12MissingEvidenceTests(unittest.TestCase):
    """LLM12-04: no cache fields in the logs, no source, bad settings: unavailable, never clean."""

    def test_log_group_without_cache_lookup_lines_is_unavailable(self):
        result, normalized = run(raw([], [DEMO]))
        self.assertEqual((result["status"], result["findings"], result["coverage"]["evaluated_scope"]),
                         ("unavailable", [], []))
        self.assertEqual(normalized["sources"][0]["data"]["lookups"], 0)
        self.assertIn("no cache lookup lines with a key (cache_key/prompt_hash/request_hash) and a hit/miss field",
                      notes(result))

    def test_lines_without_a_key_or_outcome_never_reach_the_rows(self):
        lines = ['{"agent_id":"a","cache_hit":false,"message":"no key"}',
                 '{"agent_id":"a","cache_key":"k1","message":"no outcome"}',
                 '{"agent_id":"a","prompt":"hello","latency_ms":12}']
        self.assertEqual(simulate([(DEMO, "s", i, line) for i, line in enumerate(lines)]), [])

    def test_no_source_for_a_requested_scope_is_partial(self):
        group = "/aws/lambda/owner-d-agents"
        result, _ = run(raw(fleet(group), [group]), scope=[scope(group), scope("/aws/lambda/owner-d-missing")])
        self.assertEqual(result["status"], "partial")
        self.assertIn("no telemetry source supplied", notes(result))

    def test_missing_or_invalid_settings_are_unavailable(self):
        group = "/aws/lambda/owner-d-agents"
        cases = {"min_lookups": 0, "min_agents": 1, "min_duplicated_misses": True, "min_duplicated_miss_share": 1.0}
        for key, value in [*cases.items(), ("min_agents", None)]:
            settings = dict(SETTINGS)
            if value is None:
                del settings[key]
            else:
                settings[key] = value
            with self.subTest(key=key, value=value):
                result, _ = run(raw(fleet(group), [group]), settings)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(key, result["coverage"]["limitations"][0])


class Llm12MalformedTests(unittest.TestCase):
    """LLM12-05: malformed rows and tampered sources leave the group unevaluated."""

    def test_bad_rows_make_only_that_group_unevaluated(self):
        good, bad = "/aws/lambda/owner-d-good", "/aws/lambda/owner-d-bad"
        broken = [{**row(bad, "other", 10, 60, 10), "lookups": "many"},
                  row(bad, "sideways", 1, 2, 1),
                  row(bad, "duplicated", 5, 30, 40, 4),
                  row(bad, "other", 5, 30, 5, 2),
                  row(bad, "duplicated", 5, 30, 10, 6, max_agents=1, max_missed=2),
                  row(bad, "duplicated", 5, 30, 10, 3)]
        result, _ = run(raw(fleet(good) + broken, [good, bad]))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("partial", [scope(good)]))
        note = next(n for n in result["coverage"]["limitations"] if n.startswith(scope(bad)))
        for reason in ("lookups='many' is not a nonnegative whole number", "o12_kind='sideways'",
                       "misses or named lookups exceed lookups", "must not carry duplicated misses",
                       "max_missed_agents exceeds max_agents", "must each add at least one duplicated miss"):
            self.assertIn(reason, note)

    def test_a_second_row_of_one_kind_is_refused(self):
        group = "/aws/lambda/owner-d-agents"
        result, _ = run(raw(fleet(group) + fleet(group)[:1], [group]))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("a second other row", notes(result))

    def test_rows_that_are_not_a_list_or_not_objects(self):
        normalized = llm12.normalize_logs_insights(raw(None, [DEMO]))
        self.assertEqual((normalized["scope"], normalized["sources"]), ([scope(DEMO)], []))
        self.assertEqual(run(raw(None, [DEMO]))[0]["status"], "unavailable")
        self.assertEqual(llm12.normalize_logs_insights("nope")["sources"], [])
        result, _ = run(raw(["not a row"] + fleet(DEMO), [DEMO]))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("could not be attributed", notes(result))

    def test_row_limit_leaves_every_group_unevaluated(self):
        result, _ = run(raw(fleet(DEMO), [DEMO], truncated=True))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("row limit", notes(result))

    def test_tampered_source_data_is_refused_by_the_detector(self):
        _, normalized = run(raw(fleet(DEMO), [DEMO]))
        payload = {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "s",
                   "commit_sha": "0" * 40, "check_id": "LLM-12", "detector_version": "1.0.0", "context": SETTINGS,
                   "scope": [scope(DEMO)], "sources": [normalized["sources"][0]]}
        for field, value, reason in (("duplicated_misses", 10_000, "counts are inconsistent"),
                                     ("example_key_hashes", ["key-a"], "16-hex SHA-256 prefixes"),
                                     ("concurrent_miss_seconds", 1, "another query shape"),
                                     ("lookups", -1, "not nonnegative integers: lookups")):
            source = copy.deepcopy(normalized["sources"][0])
            source["data"][field] = value
            with self.subTest(field=field):
                result = llm12.evaluate({**payload, "sources": [source]})
                self.assertEqual(result["status"], "unavailable")
                self.assertIn(reason, result["coverage"]["limitations"][0])
        source = copy.deepcopy(normalized["sources"][0])
        del source["data"]["misses"]
        missing = llm12.evaluate({**payload, "sources": [source]})
        self.assertIn("missing fields: misses", missing["coverage"]["limitations"][0])
        with self.assertRaises(llm12.EvaluationError):
            llm12.evaluate({**payload, "detector_version": "0.9"})
        with self.assertRaises(llm12.EvaluationError):
            llm12.evaluate({**payload, "check_id": "LLM-13"})


class Llm12QueryTests(unittest.TestCase):
    """The parse patterns and the query shape."""

    def test_query_shape_and_readme(self):
        query = llm12.LOGS_INSIGHTS_QUERY
        for part in ("by @log, o12_key, o12_who", "by @log, o12_key\n", "by @log, o12_kind",
                     "coalesce(o12_agent, @logStream) as o12_who", "toMillis(@timestamp)",
                     f"o12_k_last - o12_k_first >= {llm12.CONCURRENT_MISS_SECONDS * 1000}",
                     "sortsFirst(o12_key) as example_first", "sortsLast(o12_key) as example_last"):
            self.assertIn(part, query)
        self.assertEqual(query.count("| stats "), 3)
        self.assertTrue(query.rstrip().endswith("| sort @log asc, o12_kind asc"))  # sort only after the last stats
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        self.assertIn(f"```text\n{query}\n```", readme)  # the documented query is the one that runs

    def test_field_patterns(self):
        cases = {
            '{"agent_id":"agent-7","cache_key":"9f2c1a","cache_hit":false}':
                {"o12_agent": "agent-7", "o12_key": "9f2c1a", "o12_hit": "false"},
            '{"instanceId": 3, "promptHash": "abc", "cacheHit": true }':
                {"o12_agent": "3", "o12_key": "abc", "o12_hit": "true"},
            '{"request_hash":"r1","cache_status":"MISS","instance_id":"i-0abc"}':
                {"o12_key": "r1", "o12_status": "MISS", "o12_agent": "i-0abc"},
            '{"cache_key":"k","cache_hit":"1"}': {"o12_key": "k", "o12_hit": "1"},
        }
        for message, expected in cases.items():
            self.assertEqual(parsed(message), expected, message)
        for message in ('{"cache_hit":"yes","cache_key":"k"}', '{"cache_hit":10,"cache_key":"k"}',
                        '{"cache_status":"stale","cache_key":"k"}'):
            self.assertNotIn("o12_hit", parsed(message))
            self.assertNotIn("o12_status", parsed(message))
        self.assertNotIn("o12_key", parsed('{"cache_key":"' + "x" * (llm12.MAX_KEY_CHARS + 1) + '","cache_hit":true}'))
        self.assertTrue(HIT.match("hit") and HIT.match("True") and not HIT.match("false") and not HIT.match("miss"))


class Llm12RegistryTests(unittest.TestCase):
    def test_registered_on_the_log_analyzer_and_cli_with_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == "LLM-12")
        self.assertEqual((check.source, check.adapter, check.normalizer), ("logs_insights", None, None))
        self.assertEqual(check.defaults, llm12.REFERENCE_SETTINGS)
        self.assertEqual(registry.LLM12_DEFAULTS, llm12.REFERENCE_SETTINGS)
        _module, normalize = registry.load(check)
        self.assertIs(normalize, llm12.normalize_logs_insights)
        self.assertEqual(set(llm12.SETTING_KEYS), set(check.defaults))
        self.assertIs(cli.DETECTORS["LLM-12"], llm12)
        self.assertEqual(llm12.SUPPORTED_KIND, "telemetry")  # repository scans report it unavailable
        self.assertIn("LLM-12", [c.check_id for c in registry.select({"logs_insights": None})])

    def test_existing_logs_insights_checks_are_unchanged(self):
        wired = [c.check_id for c in registry.CHECKS]
        for check_id in ("OBS-07", "OBS-11", "OBS-17"):
            self.assertLess(wired.index(check_id), wired.index("LLM-12"))
        self.assertNotEqual(registry.collector_key(llm12), registry.collector_key(registry.load(
            next(c for c in registry.CHECKS if c.check_id == "OBS-17"))[0]))


class Llm12ThroughTheLogAnalyzerTests(AwsTestCase):
    """End to end: stubbed StartQuery/GetQueryResults -> normalizer -> detector -> validate_pair -> PutEvents, and
    the findings-hub writer stores the published event."""

    def test_rows_are_validated_and_published(self):
        logs = self.fakes["logs"] = FakeLogs(rows=simulate(demo_events()))
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-12"], logs={"log_groups": [DEMO]}))
        self.assertEqual((out["published"], out["refused"], out["errors"]), (1, [], []))
        self.assertEqual(out["results"], [{"check_id": "LLM-12", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        start = next(kw for name, kw in logs.calls if name == "start_query")
        self.assertEqual((start["queryString"], start["logGroupNames"]), (llm12.LOGS_INSIGHTS_QUERY, [DEMO]))
        entry = self.fakes["events"].entries[0]
        self.assertEqual(entry["Source"], "owner-d.log-analyzer")
        self.assertNotIn(ACCOUNT, entry["Detail"])
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["findings"][0]["identity"], "cross-agent-duplicate-misses")
        self.assertEqual(result["context"]["collection"]["source"], "cloudwatch-logs-insights")
        self.assertEqual({k: result["context"][k] for k in llm12.SETTING_KEYS}, llm12.REFERENCE_SETTINGS)
        summary = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                 "id": "evt-1"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(summary["outcome"], "stored")

    def test_log_group_without_cache_lines_is_published_as_unavailable(self):
        self.fakes["logs"] = FakeLogs(rows=[])
        out = log_handler.lambda_handler(self.base_event(checks=["LLM-12"], logs={"log_groups": [DEMO]}))
        self.assertEqual(out["results"], [{"check_id": "LLM-12", "status": "unavailable", "scope": 1, "evaluated": 0,
                                           "findings": 0}])


class Llm12DemoScenarioTests(unittest.TestCase):
    """The opt-in LLM-12 telemetry-demo scenario, and its lines through the query model and the detector."""

    def test_scenario_is_opt_in_and_lines_are_labeled(self):
        self.assertNotIn("LLM-12", demo.SCENARIOS)
        self.assertIn("LLM-12", demo.OPT_IN_SCENARIOS)
        lines = []
        result = demo.run({"scenario": "llm12", "agent": 2}, write=lines.append)
        self.assertEqual(result["scenarios"], ["LLM-12"])
        self.assertEqual(result["emitted"]["LLM-12"], {"agent_id": "demo-agent-b", "waste_lookups": 20,
                                                       "waste_misses": 10, "control_lookups": 20,
                                                       "control_misses": 0})
        records = [json.loads(line) for line in lines]
        self.assertEqual(len(records), 40)
        for record in records:
            self.assertIs(record["synthetic"], True)
            self.assertEqual((record["check"], record["agent_id"]), ("LLM-12", "demo-agent-b"))
            self.assertRegex(record["cache_key"], r"^[0-9a-f]{16}$")
        self.assertNotIn("Summarise", "".join(lines))  # prompt hashes only
        first = demo.run({"scenario": "LLM-12", "agent": 1, "path": "control"}, write=lambda line: None)
        self.assertEqual(first["emitted"]["LLM-12"]["control_misses"], 10)  # agent 1 warms the shared cache
        clamped = demo.run({"scenario": "LLM-12", "agent": 99, "path": "waste"}, write=lambda line: None)
        self.assertEqual(clamped["emitted"]["LLM-12"]["agent_id"], "demo-agent-d")
        with self.assertRaises(ValueError):
            demo.run({"scenario": "LLM-12", "path": "sometimes"}, write=lambda line: None)
        with self.assertRaises(ValueError):
            demo.run({"scenario": "LLM-12", "agent": "2"}, write=lambda line: None)

    def test_three_agents_waste_is_flagged(self):
        result, normalized = run(raw(simulate(demo_events()), [DEMO]))
        (finding,) = result["findings"]
        data = normalized["sources"][0]["data"]
        self.assertEqual((data["lookups"], data["misses"], data["duplicated_misses"], data["duplicated_keys"],
                          data["max_agents_per_key"]), (120, 40, 20, 10, 3))
        self.assertIn("20 of 40 cache misses (50.0%)", finding["summary"])
        self.assertEqual(finding["confidence"], "medium")
        waste_keys = sorted(demo._prompt_hash(p) for p in demo.WASTE_PROMPTS)
        self.assertEqual(data["example_key_hashes"], [llm12.key_hash(waste_keys[0]), llm12.key_hash(waste_keys[-1])])

    def test_cli_fixture_matches_the_demo_through_the_query_model(self):
        normalized = registry.normalize(llm12.normalize_logs_insights, raw(simulate(demo_events()), [DEMO]), SETTINGS)
        payload = json.loads((Path(__file__).resolve().parent / "fixtures" / "llm12" /
                              "llm12-01-demo-input.json").read_text())
        self.assertEqual((payload["scope"], payload["sources"], payload["context"]),
                         (normalized["scope"], normalized["sources"], SETTINGS))
        result = llm12.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual([f["identity"] for f in result["findings"]], ["cross-agent-duplicate-misses"])

    def test_waste_path_alone_is_flagged_and_control_path_alone_is_clean(self):
        waste, _ = run(raw(simulate(demo_events(path="waste")), [DEMO]))
        self.assertEqual([f["identity"] for f in waste["findings"]], ["cross-agent-duplicate-misses"])
        control, normalized = run(raw(simulate(demo_events(path="control")), [DEMO]))
        self.assertEqual((control["status"], control["findings"]), ("completed", []))
        self.assertEqual((normalized["sources"][0]["data"]["misses"], normalized["sources"][0]["data"]["lookups"]),
                         (10, 60))

    def test_one_agent_is_not_evaluated(self):
        result, _ = run(raw(simulate(demo_events(agents=(1,))), [DEMO]))
        self.assertEqual(result["status"], "unavailable")

    def test_demo_lines_do_not_change_other_scenarios(self):
        with mock.patch.object(demo, "SPAN_SECONDS", 0):
            result = demo.run({"scenario": "all"}, write=lambda line: None, cloudwatch=mock.Mock(), xray=mock.Mock())
        self.assertEqual(result["scenarios"], list(demo.SCENARIOS))
        self.assertNotIn("LLM-12", result["emitted"])


if __name__ == "__main__":
    unittest.main()
