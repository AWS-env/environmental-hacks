"""Behavioral tests for the INF-01 detector (verification plan on issue #169)."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli, inf01
from owner_d.inf01 import CHECK_ID, DETECTOR_VERSION, IDENTITY, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf01"

CASES = (
    "inf01-01-positive",
    "inf01-02-negative",
    "inf01-03-exception",
    "inf01-04-missing-evidence",
    "inf01-05-malformed",
    "inf01-06-boundary",
    "inf01-07-partial",
)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def run_case(case):
    payload = load(f"{case}-input.json")
    return payload, evaluate(payload)


class Inf01PositiveTests(unittest.TestCase):
    def test_positive_fixture_flags_over_provisioned_resource(self):
        payload, result = run_case("inf01-01-positive")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["scope_id"], payload["scope"][0])
        self.assertEqual(finding["identity"], IDENTITY)
        self.assertEqual(
            finding["fingerprint"],
            fingerprint(payload["repository_id"], CHECK_ID, payload["scope"][0], IDENTITY),
        )
        self.assertEqual(finding["confidence"], "high")
        self.assertIn("5.7%", finding["summary"])
        self.assertIn("21.4%", finding["summary"])
        self.assertIn("capacity is sized for peaks that did not occur", finding["summary"])
        self.assertTrue(finding["references"] and all(r.startswith("https://") for r in finding["references"]))
        self.assertEqual(
            {evidence["field"]: evidence["value"] for evidence in finding["evidence"]},
            {
                "average_utilization": 0.057,
                "peak_utilization": 0.214,
                "provisioned_capacity": 8,
                "window_days": 30,
                "sample_count": 1440,
            },
        )
        self.assertTrue(all(evidence["source_id"] == "cw-i-0a1b2c3d4e5f67890-cpu" for evidence in finding["evidence"]))
        self.assertEqual(result["measurements"], [])


class Inf01NegativeTests(unittest.TestCase):
    def test_negative_fixture_is_clean(self):
        payload, result = run_case("inf01-02-negative")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_bursty_peak_exception_is_not_flagged(self):
        payload, result = run_case("inf01-03-exception")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertTrue(
            any("capacity appears driven by real peaks" in limitation for limitation in result["coverage"]["limitations"]),
            result["coverage"]["limitations"],
        )

    def test_threshold_boundary_is_not_flagged(self):
        payload, result = run_case("inf01-06-boundary")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])


class Inf01IncompleteTests(unittest.TestCase):
    def test_missing_evidence_is_unavailable_with_reason(self):
        payload, result = run_case("inf01-04-missing-evidence")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])
        self.assertTrue(any("no telemetry source supplied" in limitation for limitation in result["coverage"]["limitations"]))

    def test_malformed_telemetry_is_unavailable_with_explicit_reason(self):
        payload, result = run_case("inf01-05-malformed")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("peak_utilization", limitations)
        self.assertIn("unsupported metric", limitations)

    def test_partial_coverage_flags_only_evaluated_scope(self):
        payload, result = run_case("inf01-07-partial")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], [payload["scope"][0]])
        self.assertEqual([finding["scope_id"] for finding in result["findings"]], [payload["scope"][0]])
        self.assertTrue(any("below the required 14 days" in limitation for limitation in result["coverage"]["limitations"]))

    def test_missing_context_settings_make_result_unavailable(self):
        payload = load("inf01-01-positive-input.json")
        del payload["context"]["peak_utilization_threshold"]
        result = evaluate(payload)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(
            any("peak_utilization_threshold" in limitation for limitation in result["coverage"]["limitations"])
        )


class Inf01ContractTests(unittest.TestCase):
    def test_all_fixtures_match_recorded_results(self):
        for case in CASES:
            with self.subTest(case=case):
                payload, result = run_case(case)
                validate_pair(payload, result)
                self.assertEqual(result, load(f"{case}-expected.json"))

    def test_fingerprint_matches_shared_contract_implementation(self):
        self.assertEqual(
            fingerprint("github:AWS-env/example", CHECK_ID, "resource:i-1", IDENTITY),
            shared_fingerprint("github:AWS-env/example", CHECK_ID, "resource:i-1", IDENTITY),
        )

    def test_evaluation_is_deterministic(self):
        payload, first = run_case("inf01-01-positive")
        self.assertEqual(first, evaluate(copy.deepcopy(payload)))

    def test_invented_evidence_is_rejected(self):
        payload, result = run_case("inf01-01-positive")
        result["findings"][0]["evidence"][0]["value"] = 0.99
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_unsupported_detector_version_is_rejected(self):
        payload = load("inf01-01-positive-input.json")
        payload["detector_version"] = "2.0.0"
        with self.assertRaises(EvaluationError):
            evaluate(payload)


class Inf01CliTests(unittest.TestCase):
    def test_cli_writes_valid_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            code = cli.main([str(FIXTURES / "inf01-01-positive-input.json"), "-o", str(output)])
            self.assertEqual(code, 0)
            validate_pair(load("inf01-01-positive-input.json"), json.loads(output.read_text()))

    def test_cli_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            self.assertEqual(cli.main([str(bad)]), 2)

    def test_cli_rejects_non_contract_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"kind": "input"}))
            self.assertEqual(cli.main([str(bad)]), 2)


if __name__ == "__main__":
    unittest.main()
