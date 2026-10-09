"""Behavioral tests for the OBS-06 high-cardinality metric dimension detector (issue #230).

`listmetrics-real-pages.json` is a real ListMetrics recording (two pages) from the
project's CloudWatch in ap-south-1, with account IDs replaced by 123456789012 and
the NextToken redacted. The other `*-pages.json` fixtures are synthetic pages in
the same shape (marked with `_synthetic`).
"""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli
from owner_d.obs06 import (
    CHECK_ID,
    DETECTOR_VERSION,
    EVIDENCE_FIELDS,
    EvaluationError,
    evaluate,
    fingerprint,
    normalize_list_metrics,
    scope_id_for,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs06"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"provider": "aws-cloudwatch", "max_dimension_values": 100, "min_identifier_values": 10}


def pages(name):
    return json.loads((FIXTURES / f"{name}-pages.json").read_text())


def telemetry_sources(normalized):
    return [
        {
            "source_id": f"cw:{index}",
            "scope_id": scope_id,
            "kind": "telemetry",
            "locator": f"cloudwatch:ListMetrics/{data['namespace']}/{data['metric_name']}",
            "data": data,
        }
        for index, (scope_id, data) in enumerate(normalized["metrics"].items())
    ]


def make_input(*names, sources=None, scope=None, context=None):
    if sources is None:
        sources = []
        for name in names:
            sources.extend(telemetry_sources(normalize_list_metrics(pages(name))))
        for index, source in enumerate(sources):
            source["source_id"] = f"cw:{index}"
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-obs06-001",
        "commit_sha": "6666666666666666666666666666666666666666",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope if scope is not None else [source["scope_id"] for source in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def flagged(result):
    return [(f["scope_id"], f["identity"], f["confidence"]) for f in result["findings"]]


DEMO = "resource:metric/OwnerD/Demo/"
EDGE = "resource:metric/OwnerD/Edge/"


class Obs06NormalizerTests(unittest.TestCase):
    def test_real_recording_is_normalized_per_metric(self):
        """The real recording: 630 listed series across 74 metrics, all AWS namespaces."""
        normalized = normalize_list_metrics(pages("listmetrics-real"))
        self.assertEqual(normalized["window_days"], 14)
        self.assertTrue(normalized["listing_complete"])
        self.assertEqual(normalized["page_count"], 2)
        metrics = normalized["metrics"]
        self.assertEqual(len(metrics), 74)
        self.assertEqual(sum(d["series_count"] for d in metrics.values()), 630)
        self.assertTrue(all(d["namespace"].startswith("AWS/") for d in metrics.values()))
        usage = metrics["resource:metric/AWS/Usage/CallCount"]
        self.assertEqual(usage["series_count"], 145)
        self.assertEqual(usage["dimension_value_counts"], {"Class": 1, "Resource": 143, "Service": 29, "Type": 1})
        self.assertEqual(usage["dimension_value_samples"]["Class"], ["None"])
        self.assertEqual(len(usage["dimension_value_samples"]["Resource"]), 5)
        invocations = metrics["resource:metric/AWS/Lambda/Invocations"]
        self.assertEqual(invocations["dimension_value_counts"], {"FunctionName": 18, "Resource": 18})
        self.assertEqual(invocations["series_count"], 37)  # FunctionName, FunctionName+Resource, dimensionless

    def test_series_are_distinct_dimension_sets_and_samples_are_bounded(self):
        page = {"Metrics": [
            {"Namespace": "App", "MetricName": "Hits", "Dimensions": [{"Name": "A", "Value": "x"}, {"Name": "B", "Value": "1"}]},
            {"Namespace": "App", "MetricName": "Hits", "Dimensions": [{"Name": "B", "Value": "1"}, {"Name": "A", "Value": "x"}]},
            {"Namespace": "App", "MetricName": "Hits", "Dimensions": [{"Name": "A", "Value": "y"}]},
            {"Namespace": "App", "MetricName": "Hits", "Dimensions": []},
        ] + [
            {"Namespace": "App", "MetricName": "Ids", "Dimensions": [{"Name": "Id", "Value": f"v{i:02d}"}]} for i in range(9)
        ]}
        metrics = normalize_list_metrics([page])["metrics"]
        hits = metrics[scope_id_for("App", "Hits")]
        self.assertEqual(hits["series_count"], 3)  # the reordered duplicate is the same metric
        self.assertEqual(hits["dimension_value_counts"], {"A": 2, "B": 1})
        self.assertEqual(hits["dimension_value_samples"], {"A": ["x", "y"], "B": ["1"]})
        ids = metrics[scope_id_for("App", "Ids")]
        self.assertEqual(ids["dimension_value_counts"], {"Id": 9})
        self.assertEqual(ids["dimension_value_samples"], {"Id": ["v00", "v01", "v02", "v03", "v04"]})

    def test_trailing_next_token_marks_listing_incomplete(self):
        first, second = pages("positive")
        self.assertTrue(normalize_list_metrics([first, second])["listing_complete"])
        self.assertFalse(normalize_list_metrics([first])["listing_complete"])

    def test_malformed_pages_raise(self):
        """OBS06-05: pages that are not ListMetrics responses are rejected, not guessed."""
        for bad in (
            [],
            {"Metrics": []},
            [{"metrics": []}],
            [{"Metrics": [{"MetricName": "Hits"}]}],
            [{"Metrics": [{"Namespace": "App", "MetricName": "Hits", "Dimensions": {"A": "x"}}]}],
            [{"Metrics": [{"Namespace": "App", "MetricName": "Hits", "Dimensions": [{"Name": "A"}]}]}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_list_metrics(bad)


class Obs06PositiveTests(unittest.TestCase):
    """OBS06-01: unbounded identifiers and over-threshold dimensions are flagged with exact evidence."""

    def test_high_cardinality_dimensions_are_flagged(self):
        payload, result = run("positive")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 5)
        self.assertEqual(flagged(result), [
            (DEMO + "CartEvents", "dimension:CartToken", "medium"),
            (DEMO + "Latency", "dimension:RequestId", "high"),
            (DEMO + "Logins", "dimension:UserId", "low"),
            (DEMO + "PageViews", "dimension:Url", "high"),
            (DEMO + "ShardLag", "dimension:Shard", "medium"),
        ])
        findings = {f["identity"]: f for f in result["findings"]}
        latency = findings["dimension:RequestId"]
        self.assertIn("has 150 distinct values for dimension 'RequestId'", latency["summary"])
        self.assertIn("more than the configured 100 (context.max_dimension_values)", latency["summary"])
        self.assertIn("identifier-like by key name and sampled values", latency["summary"])
        self.assertIn("across 150 dimension combinations listed in the past 14 days", latency["summary"])
        self.assertIn("identifier-like by key name", findings["dimension:UserId"]["summary"])
        self.assertNotIn("max_dimension_values", findings["dimension:UserId"]["summary"])
        self.assertNotIn("identifier-like", findings["dimension:Shard"]["summary"])
        sources = {s["scope_id"]: s for s in payload["sources"]}
        for finding in result["findings"]:
            source = sources[finding["scope_id"]]
            with self.subTest(identity=finding["identity"]):
                self.assertEqual(
                    finding["fingerprint"],
                    fingerprint(REPOSITORY_ID, CHECK_ID, finding["scope_id"], finding["identity"]),
                )
                self.assertEqual([e["field"] for e in finding["evidence"]], list(EVIDENCE_FIELDS))
                for evidence in finding["evidence"]:
                    self.assertEqual(evidence["kind"], "telemetry")
                    self.assertEqual(evidence["source_id"], source["source_id"])
                    self.assertEqual(evidence["value"], source["data"][evidence["field"]])
                self.assertTrue(finding["references"])
        latency_evidence = {e["field"]: e["value"] for e in latency["evidence"]}
        self.assertEqual(latency_evidence["series_count"], 150)
        self.assertEqual(latency_evidence["dimension_value_counts"], {"RequestId": 150, "Route": 5})
        self.assertEqual(latency_evidence["window_days"], 14)
        self.assertEqual(len(latency_evidence["dimension_value_samples"]["RequestId"]), 5)
        self.assertEqual(result["measurements"], [])

    def test_lower_thresholds_flag_more(self):
        _, result = run("negative", context={**CONTEXT, "max_dimension_values": 11, "min_identifier_values": 2})
        self.assertEqual(flagged(result), [
            ("resource:metric/OwnerD/Shop/RequestCount", "dimension:Route", "medium"),
            ("resource:metric/OwnerD/Shop/Revenue", "dimension:TenantId", "medium"),
        ])


class Obs06NegativeTests(unittest.TestCase):
    """OBS06-02: bounded custom dimensions, bounded identifiers and dimensionless metrics are clean."""

    def test_bounded_custom_metrics_are_clean(self):
        _, result = run("negative")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [
            "resource:metric/OwnerD/Shop/Heartbeat",
            "resource:metric/OwnerD/Shop/RequestCount",
            "resource:metric/OwnerD/Shop/Revenue",
        ])
        self.assertEqual(result["findings"], [])
        self.assertFalse(any("AWS/*" in item for item in result["coverage"]["limitations"]))


class Obs06ExceptionTests(unittest.TestCase):
    """OBS06-03: AWS-vended namespaces are evaluated as exceptions, using the real recording."""

    def test_real_recording_of_aws_namespaces_is_not_flagged(self):
        payload, result = run("listmetrics-real")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 74)
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "74 metric(s) in AWS/* namespaces were evaluated as exceptions",
            " ".join(result["coverage"]["limitations"]),
        )
        # AWS/Usage CallCount has 143 Resource values: only the exception keeps it clean.
        usage = next(s for s in payload["sources"] if s["scope_id"] == "resource:metric/AWS/Usage/CallCount")
        self.assertGreater(usage["data"]["dimension_value_counts"]["Resource"], CONTEXT["max_dimension_values"])

    def test_same_dimensions_in_a_custom_namespace_are_flagged(self):
        normalized = normalize_list_metrics(pages("listmetrics-real"))
        data = copy.deepcopy(normalized["metrics"]["resource:metric/AWS/Usage/CallCount"])
        data["namespace"] = "MyApp/Usage"
        source = {
            "source_id": "cw:custom",
            "scope_id": scope_id_for("MyApp/Usage", "CallCount"),
            "kind": "telemetry",
            "locator": "cloudwatch:ListMetrics/MyApp/Usage/CallCount",
            "data": data,
        }
        _, result = run(sources=[source])
        self.assertEqual(flagged(result), [("resource:metric/MyApp/Usage/CallCount", "dimension:Resource", "medium")])


class Obs06IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS06-04: requested scope without telemetry is not evaluated."""
        _, result = run(sources=[], scope=[DEMO + "Latency"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertIn(
            f"{DEMO}Latency: no telemetry source supplied; OBS-06 requires normalized ListMetrics telemetry",
            result["coverage"]["limitations"],
        )

    def test_missing_scope_item_makes_result_partial(self):
        payload = make_input("negative")
        payload["scope"].append(DEMO + "Latency")
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn(DEMO + "Latency", result["coverage"]["evaluated_scope"])

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """OBS06-04: both thresholds are required judgment calls."""
        for context, reason in (
            ({"provider": "aws-cloudwatch"}, "missing required context settings: max_dimension_values, min_identifier_values"),
            ({k: v for k, v in CONTEXT.items() if k != "min_identifier_values"},
             "missing required context settings: min_identifier_values"),
            ({**CONTEXT, "max_dimension_values": 0}, "context.max_dimension_values must be a positive integer"),
            ({**CONTEXT, "max_dimension_values": "100"}, "context.max_dimension_values must be a positive integer"),
            ({**CONTEXT, "min_identifier_values": True}, "context.min_identifier_values must be a positive integer"),
            ({**CONTEXT, "min_identifier_values": 10.0}, "context.min_identifier_values must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_telemetry_is_omitted_with_reason(self):
        """OBS06-05: malformed data is left out of evaluated scope, never reported clean."""
        base = make_input("positive")
        target = DEMO + "Latency"
        cases = (
            (lambda d: d.pop("series_count"), "missing fields: series_count"),
            (lambda d: d.update(series_count=100), "dimension_value_counts['RequestId'] = 150 exceeds series_count 100"),
            (lambda d: d.update(dimension_value_samples={"RequestId": ["a"]}), "must have the same keys"),
            (lambda d: d["dimension_value_samples"].update(Route=[]), "dimension_value_samples['Route'] must be a nonempty list"),
            (lambda d: d.update(listing_complete="yes"), "listing_complete must be a boolean"),
            (lambda d: d.update(window_days=0), "window_days must be a positive number"),
            (lambda d: d.update(metric_name="Other"), "does not match namespace/metric_name"),
        )
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                payload = copy.deepcopy(base)
                source = next(s for s in payload["sources"] if s["scope_id"] == target)
                mutate(source["data"])
                result = evaluate(payload)
                validate_pair(payload, result)
                self.assertEqual(result["status"], "partial")
                self.assertNotIn(target, result["coverage"]["evaluated_scope"])
                self.assertNotIn(target, [f["scope_id"] for f in result["findings"]])
                self.assertEqual(len(result["findings"]), 4)
                self.assertTrue(any(item.startswith(f"{target}: ") and reason in item
                                    for item in result["coverage"]["limitations"]))

    def test_duplicate_sources_are_not_evaluated(self):
        payload = make_input("negative")
        extra = copy.deepcopy(payload["sources"][0])
        extra["source_id"] = "cw:dup"
        payload["sources"].append(extra)
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("multiple telemetry sources" in item for item in result["coverage"]["limitations"]))

    def test_incomplete_listing_keeps_findings_but_not_clean_claims(self):
        """OBS06-05: counts from a truncated listing are lower bounds."""
        truncated = pages("positive") + pages("negative")
        truncated[-1]["NextToken"] = "more"
        sources = telemetry_sources(normalize_list_metrics(truncated))
        _, result = run(sources=sources)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["findings"]), 5)
        self.assertEqual(sorted(result["coverage"]["evaluated_scope"]), sorted({f["scope_id"] for f in result["findings"]}))
        limitations = " ".join(result["coverage"]["limitations"])
        for metric in ("Heartbeat", "RequestCount", "Revenue"):
            self.assertIn(f"resource:metric/OwnerD/Shop/{metric}: the ListMetrics listing was incomplete", limitations)


class Obs06BoundaryTests(unittest.TestCase):
    """OBS06-06: both thresholds are strict; mixed samples are not identifier-like by shape."""

    def test_boundaries(self):
        _, result = run("boundary")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 6)
        self.assertEqual(flagged(result), [
            (EDGE + "ElevenSessions", "dimension:SessionId", "medium"),
            (EDGE + "HundredOneShards", "dimension:Shard", "medium"),
            (EDGE + "UuidBuilds", "dimension:Build", "medium"),
        ])
        summaries = {f["scope_id"]: f["summary"] for f in result["findings"]}
        self.assertIn("identifier-like by sampled values", summaries[EDGE + "UuidBuilds"])
        self.assertIn("101 distinct values", summaries[EDGE + "HundredOneShards"])

    def test_threshold_equal_to_count_is_not_flagged(self):
        _, result = run("boundary", context={**CONTEXT, "max_dimension_values": 101, "min_identifier_values": 11})
        self.assertEqual(flagged(result), [])

    def test_fingerprints_ignore_observed_counts(self):
        _, before = run("positive")
        payload = make_input("positive")
        for source in payload["sources"]:
            source["data"]["series_count"] += 7
        after = evaluate(payload)
        validate_pair(payload, after)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])


class Obs06ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, DEMO + "Latency", "dimension:RequestId")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_telemetry_evidence_is_rejected(self):
        payload, result = run("positive")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = 999999
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive", "negative", "boundary")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs06-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive"))


class Obs06CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs06_result(self):
        input_path = FIXTURES / "obs06-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 5)


if __name__ == "__main__":
    unittest.main()
