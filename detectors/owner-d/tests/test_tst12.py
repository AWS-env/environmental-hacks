"""Behavioral tests for the TST-12 detector (issue #267)."""

import copy
import contextlib
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

from owner_d import cli, tst12
from owner_d.tst12 import CHECK_ID, DETECTOR_VERSION, IDENTITY, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst12"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def run_case(case):
    payload = load(f"{case}-input.json")
    return payload, evaluate(payload)


class Tst12PositiveTests(unittest.TestCase):
    def test_positive_fixture_flags_heavy_test_work(self):
        payload, result = run_case("tst12-01-positive")
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
        self.assertIn("duration 14.2 exceeds 10", finding["summary"])
        self.assertIn("sleep 2.5 exceeds 0", finding["summary"])
        self.assertIn("network calls 3 exceeds 0", finding["summary"])
        self.assertTrue(finding["references"] and all(r.startswith("https://") for r in finding["references"]))
        self.assertEqual(
            {evidence["field"]: evidence["value"] for evidence in finding["evidence"]},
            {
                "duration_seconds": 14.2,
                "sleep_seconds": 2.5,
                "network_call_count": 3,
                "fixture_bytes": 52428800,
                "setup_seconds": 4.1,
            },
        )
        self.assertTrue(all(evidence["kind"] == "artifact" for evidence in finding["evidence"]))
        self.assertEqual(result["measurements"], [])


class Tst12NegativeTests(unittest.TestCase):
    def test_negative_fixture_is_clean(self):
        payload, result = run_case("tst12-02-negative")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_threshold_boundary_is_not_flagged(self):
        payload, result = run_case("tst12-03-boundary")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])


class Tst12IncompleteTests(unittest.TestCase):
    def test_missing_artifact_is_unavailable_with_reason(self):
        payload, result = run_case("tst12-04-missing-evidence")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no test artifact supplied" in limitation for limitation in result["coverage"]["limitations"]))

    def test_malformed_artifact_is_unavailable_with_explicit_reason(self):
        payload, result = run_case("tst12-05-malformed")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("missing fields: sleep_seconds", limitations)
        self.assertIn("duration_seconds must be nonnegative", limitations)
        self.assertIn("network_call_count must be an integer", limitations)

    def test_partial_coverage_flags_only_evaluated_scope(self):
        payload, result = run_case("tst12-06-partial")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], [payload["scope"][0]])
        self.assertEqual([finding["scope_id"] for finding in result["findings"]], [payload["scope"][0]])
        self.assertTrue(any("no test artifact supplied" in limitation for limitation in result["coverage"]["limitations"]))

    def test_missing_context_settings_make_result_unavailable(self):
        payload = load("tst12-01-positive-input.json")
        del payload["context"]["max_network_calls"]
        result = evaluate(payload)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("max_network_calls" in limitation for limitation in result["coverage"]["limitations"]))


class Tst12ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        self.assertEqual(
            fingerprint("github:AWS-env/example", CHECK_ID, "test:tests/test_api.py::test_fetch_users", IDENTITY),
            shared_fingerprint("github:AWS-env/example", CHECK_ID, "test:tests/test_api.py::test_fetch_users", IDENTITY),
        )

    def test_evaluation_is_deterministic(self):
        payload, first = run_case("tst12-01-positive")
        self.assertEqual(first, evaluate(copy.deepcopy(payload)))

    def test_invented_artifact_evidence_is_rejected(self):
        payload, result = run_case("tst12-01-positive")
        result["findings"][0]["evidence"][0]["value"] = 99
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_unsupported_detector_version_is_rejected(self):
        payload = load("tst12-01-positive-input.json")
        payload["detector_version"] = "2.0.0"
        with self.assertRaises(EvaluationError):
            evaluate(payload)


class Tst12CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst12_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            code = cli.main([str(FIXTURES / "tst12-01-positive-input.json"), "-o", str(output)])
            self.assertEqual(code, 0)
            validate_pair(load("tst12-01-positive-input.json"), json.loads(output.read_text()))

    def test_cli_rejects_unsupported_owner_d_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            payload = load("tst12-01-positive-input.json")
            payload["check_id"] = "TST-11"
            bad.write_text(json.dumps(payload))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([str(bad)]), 1)


if __name__ == "__main__":
    unittest.main()
