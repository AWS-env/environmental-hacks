"""Behavioral tests for the LLM-17 detector (issue #210): fixed agent capacity with bursty utilization.

Series here are SYNTHETIC CloudWatch GetMetricData Average/Maximum values shaped on real EC2/ECS CPUUtilization,
Lambda ProvisionedConcurrencyUtilization and custom percent metrics; they are not production telemetry.
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.aws_fakes import ROLE, AwsTestCase, FakeCloudWatch, hourly

from owner_d import cli, inf01, llm17
from owner_d.aws import common, metrics, registry, telemetry_handler
from owner_d.llm17 import CHECK_ID, DETECTOR_VERSION, GENERAL_LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, validate_pair
from shared.contracts.validation import fingerprint as shared_fingerprint

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm17"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = dict(llm17.REFERENCE_SETTINGS)
WINDOW = {"start": "2026-09-30T12:00:00Z", "end": "2026-10-10T12:00:00Z"}
BURST_HOUR = 14


def _bursty_avg(i):
    return 72.0 if i % 24 == BURST_HOUR else 3.0 + i % 4


def _bursty_max(i):
    return 92.0 if i % 24 == BURST_HOUR else 7.0 + i % 4


def series(avg, peak, days=10, complete=True):
    (stamps, averages), (_, maxima) = hourly(days, avg), hourly(days, peak)
    return {"average": {"timestamps": [common.iso(t) for t in stamps], "values": averages},
            "maximum": {"timestamps": [common.iso(t) for t in stamps], "values": maxima},
            "complete": complete, "messages": []}


BURSTY = series(_bursty_avg, _bursty_max)  # 4-7% most hours, one 72% (max 92%) burst a day
STEADY = series(lambda i: 55.0 + (i % 5) * 2, lambda i: 70.0 + (i % 5) * 2)  # a busy, well-used pool
IDLE_FLAT = series(4.0, lambda i: 20.0 + i % 5)  # idle without bursts: INF-01's pattern
BUSY_STRETCHES = series(lambda i: 70.0 if i % 10 < 4 else 5.0, lambda i: 90.0 if i % 10 < 4 else 9.0)

ECS = {"type": "ecs", "cluster": "agents", "service": "support-agent-worker", "provisioned_capacity": 6,
       "capacity_unit": "task", "autoscaling": False}
POOL = {"type": "custom", "name": "triage-agent-pool", "namespace": "Example/Agents",
        "metric_name": "AgentWorkerUtilization", "dimensions": {"pool": "triage"}, "provisioned_capacity": 8,
        "capacity_unit": "worker"}
ON_DEMAND = {"type": "lambda", "name": "planner-agent"}


def raw(*entries):
    """A capacity_metrics raw dict like collect_capacity_metrics returns; entries are (spec, series or None)."""
    resources = [metrics.parse_capacity(spec) for spec, _ in entries]
    return {"window": dict(WINDOW), "period_seconds": 3600, "region": "ap-south-1", "resources": resources,
            "series": {r["id"]: s for r, (_, s) in zip(resources, entries) if s is not None},
            "truncated": False, "collection": {"source": "cloudwatch-getmetricdata", "period_seconds": 3600,
                                               "lookback_days": 10.0},
            "counts": {}, "limitations": []}


def make_input(*entries, context=None, scope=None, sources=None):
    if sources is None:
        normalized = metrics.normalize_capacity_metrics(raw(*entries))
        scope = normalized["scope"] if scope is None else scope
        sources = normalized["sources"]
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-llm17-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope,
        "sources": sources,
    }


def positive_input():
    return make_input((ECS, BURSTY), (POOL, STEADY), (ON_DEMAND, None))


def run(payload):
    result = evaluate(payload)
    validate_pair(payload, result)
    return result


def data_for(spec, values, **overrides):
    """Normalized LLM-17 data for one declared resource, with optional field overrides."""
    source = metrics.normalize_capacity_metrics(raw((spec, values)))["sources"][0]
    source["data"].update(overrides)
    return source


def notes(result):
    return " ".join(result["coverage"]["limitations"])


class SummaryTests(unittest.TestCase):
    def test_mean_median_peak_window_and_samples(self):
        out = metrics.summarize_utilization(BURSTY["average"], BURSTY["maximum"], 3600, 100.0)
        self.assertEqual(out, {"mean_utilization": 0.0729, "median_utilization": 0.045, "peak_utilization": 0.92,
                               "window_days": 10.0, "sample_count": 240, "capped": False})

    def test_odd_median_fraction_scale_nan_and_cap(self):
        stamps, _ = hourly(1, 0)
        average = {"timestamps": stamps[:3], "values": [0.2, float("nan"), 0.1]}
        out = metrics.summarize_utilization(average, {"values": [1.4]}, 3600, 1.0)
        self.assertEqual((out["median_utilization"], out["mean_utilization"], out["sample_count"]), (0.15, 0.15, 2))
        self.assertEqual((out["peak_utilization"], out["capped"]), (1.0, True))
        average = {"timestamps": stamps[:3], "values": [0.3, 0.1, 0.2]}
        self.assertEqual(metrics.summarize_utilization(average, {}, 3600, 1.0)["median_utilization"], 0.2)
        self.assertIsNone(metrics.summarize_utilization({"timestamps": [], "values": []}, {}, 3600, 100.0))


class CapacitySpecTests(unittest.TestCase):
    def test_metric_per_type(self):
        ecs = metrics.parse_capacity(ECS)
        self.assertEqual((ecs["id"], ecs["metric"]["Namespace"], ecs["metric"]["MetricName"], ecs["scale"]),
                         ("ecs/agents/support-agent-worker", "AWS/ECS", "CPUUtilization", 100.0))
        pc = metrics.parse_capacity({"type": "lambda", "name": "agent-fn", "qualifier": "live"})
        self.assertEqual((pc["id"], pc["scale"], pc["capacity_unit"]),
                         ("lambda/agent-fn:live", 1.0, "provisioned_concurrency"))
        self.assertEqual(pc["metric"], {"Namespace": "AWS/Lambda", "MetricName": "ProvisionedConcurrencyUtilization",
                                        "Dimensions": [{"Name": "FunctionName", "Value": "agent-fn"},
                                                       {"Name": "Resource", "Value": "agent-fn:live"}]})
        self.assertIsNone(metrics.parse_capacity(ON_DEMAND)["metric"])
        pool = metrics.parse_capacity({**POOL, "unit": "fraction", "workload": "inference"})
        self.assertEqual((pool["id"], pool["scale"], pool["workload"], pool["autoscaling"]),
                         ("metric/triage-agent-pool", 1.0, "inference", None))
        ec2 = metrics.parse_capacity({"type": "ec2", "id": "i-0abc12345678def00"})
        self.assertEqual((ec2["provisioned_capacity"], ec2["capacity_unit"], ec2["workload"]), (1, "instance", "agent"))

    def test_invalid_specs_are_refused(self):
        for bad in ({"type": "sagemaker"}, {"type": "ec2", "id": "web-1"}, {"type": "lambda"},
                    {"type": "lambda", "name": "fn", "qualifier": "$LATEST"},
                    {**ECS, "autoscaling": "no"}, {**ECS, "workload": "batch"}, {**ECS, "provisioned_capacity": 0},
                    {**ECS, "capacity_unit": " "}, {**POOL, "unit": "percentage"},
                    {**POOL, "dimensions": {"pool": 3}}, {**POOL, "dimensions": {f"d{i}": "x" for i in range(31)}},
                    {k: v for k, v in POOL.items() if k != "namespace"}):
            with self.assertRaises(ValueError, msg=bad):
                metrics.parse_capacity(bad)


class NormalizerTests(unittest.TestCase):
    def test_sources_carry_the_declared_capacity_and_the_distribution(self):
        out = metrics.normalize_capacity_metrics(raw((ECS, BURSTY)))
        (source,) = out["sources"]
        self.assertEqual(out["scope"], ["resource:ecs/agents/support-agent-worker"])
        self.assertEqual(source["source_id"], "cloudwatch:ecs/agents/support-agent-worker")
        self.assertEqual(source["locator"], "cloudwatch://ap-south-1/AWS/ECS/CPUUtilization?ClusterName=agents&"
                                            "ServiceName=support-agent-worker&period=3600&stat=Average,Maximum")
        self.assertEqual(source["data"], {
            "resource_id": "ecs/agents/support-agent-worker", "resource_type": "aws_ecs_service", "workload": "agent",
            "metric": "cpu_utilization", "provisioned_capacity": 6, "capacity_unit": "task", "autoscaling": False,
            "mean_utilization": 0.0729, "median_utilization": 0.045, "peak_utilization": 0.92, "window_days": 10.0,
            "sample_count": 240, "period_seconds": 3600, "window_start": WINDOW["start"], "window_end": WINDOW["end"]})

    def test_unmeasurable_capacity_stays_in_scope_without_a_source(self):
        empty = {"average": {"timestamps": [], "values": []}, "maximum": {"timestamps": [], "values": []},
                 "complete": True, "messages": []}
        out = metrics.normalize_capacity_metrics(raw(
            (ON_DEMAND, None), ({**ECS, "service": "cut-off"}, {**BURSTY, "complete": False}), (POOL, empty)))
        self.assertEqual(out["scope"], ["resource:lambda/planner-agent", "resource:ecs/agents/cut-off",
                                        "resource:metric/triage-agent-pool"])
        self.assertEqual(out["sources"], [])
        text = " ".join(out["limitations"])
        self.assertIn("resource:lambda/planner-agent: on-demand Lambda", text)
        self.assertIn("resource:ecs/agents/cut-off: CloudWatch returned incomplete AWS/ECS CPUUtilization", text)
        self.assertIn("resource:metric/triage-agent-pool: no Example/Agents AgentWorkerUtilization datapoints", text)

    def test_values_over_capacity_are_capped_with_a_limitation(self):
        over = series(lambda i: 2.0, lambda i: 180.0)
        out = metrics.normalize_capacity_metrics(raw((ECS, over)))
        self.assertEqual(out["sources"][0]["data"]["peak_utilization"], 1.0)
        self.assertIn("values were capped at 1.0", out["limitations"][0])


class Llm17DetectorTests(unittest.TestCase):
    def test_bursty_fixed_capacity_is_flagged_with_exact_evidence(self):
        payload = positive_input()
        result = run(payload)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"],
                         ["resource:ecs/agents/support-agent-worker", "resource:metric/triage-agent-pool"])
        (finding,) = result["findings"]
        self.assertEqual((finding["scope_id"], finding["identity"], finding["confidence"]),
                         ("resource:ecs/agents/support-agent-worker", "bursty-fixed-capacity", "high"))
        self.assertEqual({e["field"]: e["value"] for e in finding["evidence"]},
                         {"median_utilization": 0.045, "mean_utilization": 0.0729, "peak_utilization": 0.92,
                          "provisioned_capacity": 6, "window_days": 10.0, "sample_count": 240})
        self.assertEqual(finding["summary"], (
            "agent aws_ecs_service ecs/agents/support-agent-worker holds 6 task of fixed capacity (no autoscaling) "
            "whose median utilization is 4.5% over 10 days, while bursts peak at 92.0% (12.6x the 7.3% mean); "
            "capacity is sized for bursts it serves only briefly"))
        self.assertIn("resource:lambda/planner-agent: no telemetry source supplied", notes(result))
        self.assertEqual(result["coverage"]["limitations"][-1], GENERAL_LIMITATION)
        self.assertEqual(result["measurements"], [])

    def test_unknown_autoscaling_is_medium_and_saturated_bursts_are_called_out(self):
        saturating = series(_bursty_avg, lambda i: 100.0 if i % 24 == BURST_HOUR else 8.0)
        result = run(make_input(({**ECS, "autoscaling": None}, saturating)))
        (finding,) = result["findings"]
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("(autoscaling not declared)", finding["summary"])
        self.assertTrue(finding["summary"].endswith("and the bursts saturate it, so it is also short at the peak"))

    def test_declared_autoscaling_is_not_flagged(self):
        result = run(make_input(({**ECS, "autoscaling": True}, BURSTY)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("declared autoscaled", notes(result))

    def test_steady_and_busy_stretch_loads_are_clean(self):
        result = run(make_input((POOL, STEADY)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        result = run(make_input((ECS, BUSY_STRETCHES)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("busy for long stretches rather than bursty", notes(result))

    def test_idle_without_bursts_is_left_to_inf01_and_the_checks_never_overlap(self):
        for values, llm17_flags, inf01_flags in ((IDLE_FLAT, False, True), (BURSTY, True, False),
                                                 (STEADY, False, False)):
            source = data_for(ECS, values)
            result = run(make_input(scope=[source["scope_id"]], sources=[source]))
            self.assertEqual(bool(result["findings"]), llm17_flags)
            data = source["data"]
            inf_source = {**source, "data": {
                "resource_id": data["resource_id"], "resource_type": data["resource_type"],
                "metric": "cpu_utilization", "provisioned_capacity": data["provisioned_capacity"],
                "capacity_unit": data["capacity_unit"], "average_utilization": data["mean_utilization"],
                "peak_utilization": data["peak_utilization"], "window_days": data["window_days"],
                "sample_count": data["sample_count"]}}
            inf_payload = {**make_input(scope=[source["scope_id"]], sources=[inf_source]), "check_id": "INF-01",
                           "detector_version": inf01.DETECTOR_VERSION, "context": {  # INF-01 values, 10-day window
                               "min_window_days": 7, "min_sample_count": 100,
                               "average_utilization_threshold": 0.1, "peak_utilization_threshold": 0.5}}
            self.assertEqual(bool(inf01.evaluate(inf_payload)["findings"]), inf01_flags)
        result = run(make_input((ECS, IDLE_FLAT)))
        self.assertIn("sustained over-provisioning is INF-01's pattern", notes(result))

    def test_thresholds(self):
        def flagged(**overrides):
            source = data_for(ECS, BURSTY, **overrides)
            return bool(run(make_input(scope=[source["scope_id"]], sources=[source]))["findings"])

        self.assertFalse(flagged(median_utilization=0.10))  # strict <
        self.assertTrue(flagged(median_utilization=0.0999))
        self.assertTrue(flagged(peak_utilization=0.50, mean_utilization=0.125))  # peak >=, ratio == 4 is flagged
        self.assertFalse(flagged(peak_utilization=0.4999))
        self.assertFalse(flagged(peak_utilization=0.6, mean_utilization=0.1501))  # ratio just under 4
        self.assertTrue(flagged(mean_utilization=0, median_utilization=0))  # unbounded ratio
        source = data_for(ECS, BURSTY, mean_utilization=0, median_utilization=0)
        summary = run(make_input(scope=[source["scope_id"]], sources=[source]))["findings"][0]["summary"]
        self.assertIn("(with a 0% mean)", summary)

    def test_settings_change_the_verdict(self):
        context = {**CONTEXT, "min_peak_to_mean_ratio": 20}
        self.assertEqual(run(make_input((ECS, BURSTY), context=context))["findings"], [])

    def test_window_and_sample_minimums(self):
        result = run(make_input((ECS, series(_bursty_avg, _bursty_max, days=5))))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("5 days is below the required 7 days", notes(result))
        result = run(make_input((ECS, BURSTY), context={**CONTEXT, "min_sample_count": 241}))
        self.assertIn("240 samples is below the required 241", notes(result))

    def test_on_demand_lambda_only_is_unavailable_not_clean(self):
        result = run(make_input((ON_DEMAND, None)))
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]), ("unavailable", []))
        self.assertIn("resource:lambda/planner-agent: no telemetry source supplied", notes(result))

    def test_missing_or_invalid_settings_are_unavailable(self):
        for context in ({}, {**CONTEXT, "median_utilization_threshold": 0}, {**CONTEXT, "min_peak_to_mean_ratio": 1},
                        {**CONTEXT, "min_window_days": "7"}, {**CONTEXT, "peak_utilization_threshold": 1.5},
                        {**CONTEXT, "min_sample_count": 0}, {**CONTEXT, "min_window_days": 0}):
            result = run(make_input((ECS, BURSTY), context=context))
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []), context)
            self.assertIn("Missing or invalid context settings", result["coverage"]["limitations"][0])

    def test_malformed_or_mismatched_data_is_omitted_and_reported(self):
        cases = {
            "workload must be one of agent, inference": {"workload": "batch"},
            "unsupported metric 'gpu_utilization'": {"metric": "gpu_utilization"},
            "peak_utilization must not be below median_utilization": {"peak_utilization": 0.01},
            "autoscaling must be true, false or null": {"autoscaling": "yes"},
            "mean_utilization must be within [0, 1]": {"mean_utilization": 1.2},
            "provisioned_capacity must be positive": {"provisioned_capacity": 0},
            "sample_count must be a number": {"sample_count": "240"},
            "does not match resource_id": {"resource_id": "ecs/agents/other"},
        }
        good = data_for(POOL, STEADY)
        for expected, overrides in cases.items():
            bad = data_for(ECS, BURSTY, **overrides)
            result = run(make_input(scope=[bad["scope_id"], good["scope_id"]], sources=[bad, good]))
            self.assertEqual((result["status"], result["findings"]), ("partial", []), expected)
            self.assertIn(expected, notes(result))
        missing = data_for(ECS, BURSTY)
        del missing["data"]["autoscaling"]
        result = run(make_input(scope=[missing["scope_id"]], sources=[missing]))
        self.assertIn("missing fields: autoscaling", notes(result))

    def test_duplicate_sources_are_not_evaluated(self):
        source = data_for(ECS, BURSTY)
        twin = {**copy.deepcopy(source), "source_id": "cloudwatch:twin"}
        result = run(make_input(scope=[source["scope_id"]], sources=[source, twin]))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("multiple telemetry sources supplied", notes(result))

    def test_fingerprint_ignores_observed_values_and_matches_the_shared_contract(self):
        scope_id = "resource:ecs/agents/support-agent-worker"
        first = run(positive_input())["findings"][0]["fingerprint"]
        other = make_input((ECS, series(lambda i: 60.0 if i % 24 == 3 else 1.0, lambda i: 99.0)))
        self.assertEqual(run(other)["findings"][0]["fingerprint"], first)
        self.assertEqual(first, fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, "bursty-fixed-capacity"))
        self.assertEqual(first, shared_fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, "bursty-fixed-capacity"))

    def test_invented_evidence_is_rejected(self):
        payload = positive_input()
        result = evaluate(payload)
        result["findings"][0]["evidence"][0]["value"] = 0.001
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_bad_payloads_raise(self):
        for change in ({"detector_version": "9.9.9"}, {"check_id": "INF-01"}, {"kind": "result"}, {"scope": []},
                       {"schema_version": "2.0"}):
            with self.assertRaises(EvaluationError, msg=change):
                evaluate({**positive_input(), **change})

    def test_deterministic(self):
        payload = positive_input()
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_series(self):
        committed = json.loads((FIXTURES / "llm17-01-positive-input.json").read_text())
        self.assertEqual(committed, positive_input())


class Llm17CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm17_result(self):
        input_path = FIXTURES / "llm17-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual([f["identity"] for f in result["findings"]], ["bursty-fixed-capacity"])
        self.assertIs(cli.DETECTORS[CHECK_ID], llm17)


def fake_series(spec):
    (stamps, avg), (_, peak) = hourly(10, spec[0]), hourly(10, spec[1])
    return (stamps, avg), (stamps, peak)


class Llm17AnalyzerTests(AwsTestCase):
    """owner-d-telemetry-analyzer: agent_capacity -> GetMetricData -> LLM-17 -> findings-hub (stubbed boto3)."""

    DATA = {
        ("agents", "support-agent-worker"): fake_series((_bursty_avg, _bursty_max)),
        ("agent-fn", "agent-fn:live"): fake_series((lambda i: 0.72 if i % 24 == 9 else 0.02, lambda i: 1.0)),
        ("triage",): fake_series((lambda i: 55.0, lambda i: 75.0)),
    }

    def setUp(self):
        super().setUp()
        self.fakes["cloudwatch"] = FakeCloudWatch(self.DATA)

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(self.base_event(checks=["LLM-17"], **extra))

    def test_declared_capacity_is_read_evaluated_and_published(self):
        out = self.run_event(agent_capacity=[ECS, {"type": "lambda", "name": "agent-fn", "qualifier": "live"},
                                             POOL, ON_DEMAND], role_arn=ROLE)
        self.assertEqual(out["results"], [{"check_id": "LLM-17", "status": "partial", "scope": 4, "evaluated": 3,
                                           "findings": 2}])
        self.assertEqual((out["published"], out["refused"], out["errors"], out["assumed_role"]), (1, [], [], True))
        entry = self.fakes["events"].entries[0]
        result = json.loads(entry["Detail"])
        self.assertEqual(sorted(f["scope_id"] for f in result["findings"]),
                         ["resource:ecs/agents/support-agent-worker", "resource:lambda/agent-fn:live"])
        self.assertEqual(result["context"]["collection"],
                         {"source": "cloudwatch-getmetricdata", "period_seconds": 3600, "lookback_days": 15.0})
        self.assertNotRegex(entry["Detail"], r"\d{12}")
        (call,) = [kw for name, kw in self.fakes["cloudwatch"].calls if name == "get_metric_data"]
        queried = {(q["MetricStat"]["Metric"]["Namespace"], q["MetricStat"]["Metric"]["MetricName"],
                    q["MetricStat"]["Stat"]) for q in call["MetricDataQueries"]}
        self.assertEqual(queried, {(ns, name, stat) for ns, name in (
            ("AWS/ECS", "CPUUtilization"), ("AWS/Lambda", "ProvisionedConcurrencyUtilization"),
            ("Example/Agents", "AgentWorkerUtilization")) for stat in ("Average", "Maximum")})
        self.assertEqual(len(call["MetricDataQueries"]), 6)  # the on-demand Lambda is not queried
        self.assertIn(("cloudwatch", "ap-south-1", "ASIA1"), self.created)

    def test_on_demand_lambda_alone_is_unavailable_and_reads_nothing(self):
        out = self.run_event(agent_capacity=[ON_DEMAND], dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("on-demand Lambda", notes(result))
        self.assertEqual(self.fakes["cloudwatch"].calls, [])

    def test_without_agent_capacity_llm17_is_skipped_without_reading(self):
        out = self.run_event()
        self.assertEqual((out["results"], out["published"]), ([], 0))
        self.assertEqual(out["skipped"][0]["check_id"], "LLM-17")
        self.assertEqual((self.fakes["cloudwatch"].calls, self.fakes["sts"].calls), ([], []))

    def test_event_validation(self):
        for agent_capacity in ([], {"type": "ecs"}, [ECS, ECS], [{"type": "ecs", "cluster": "a"}],
                               [{**ECS, "service": f"s{i}"} for i in range(51)]):
            with self.assertRaises(ValueError, msg=agent_capacity):
                self.run_event(agent_capacity=agent_capacity)

    def test_settings_override_and_probe(self):
        out = self.run_event(agent_capacity=[ECS], dry_run=True, settings={"LLM-17": {"min_window_days": 11}})
        self.assertIn("10 days is below the required 11 days", notes(out["result_payloads"][0]))
        out = telemetry_handler.lambda_handler({"probe": ["capacity_metrics"], "agent_capacity": [ECS, ON_DEMAND]})
        self.assertEqual(out["probe"]["capacity_metrics"]["resources"], 2)
        self.assertEqual(out["probe"]["capacity_metrics"]["with_series"], 1)
        self.assertEqual(self.fakes["events"].entries, [])

    def test_registry_defaults_are_the_reference_settings(self):
        check = next(c for c in registry.CHECKS if c.check_id == CHECK_ID)
        self.assertEqual(check.defaults, llm17.REFERENCE_SETTINGS)


if __name__ == "__main__":
    unittest.main()
