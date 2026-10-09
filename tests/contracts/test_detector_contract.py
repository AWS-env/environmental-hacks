import copy
import json
import unittest
from pathlib import Path

from shared.contracts.validation import ContractError, compare, fingerprint, validate, validate_pair


EXAMPLES = Path(__file__).resolve().parents[2] / "shared/contracts/examples"


def example(name):
    return json.loads((EXAMPLES / f"obs01-{name}.json").read_text())


class DetectorContractTests(unittest.TestCase):
    def setUp(self):
        self.input = example("input")
        self.detected = example("detected")
        self.clean = example("clean")

    def test_examples_validate_against_supplied_inputs(self):
        for source, result in [("input", "detected"), ("after-input", "clean"), ("unavailable-input", "unavailable")]:
            with self.subTest(result=result):
                validate_pair(example(source), example(result))

    def test_missing_required_fields_unknown_fields_and_versions_rejected(self):
        for mutation in (lambda p: p.pop("check_id"), lambda p: p.update(extra="unexpected"),
                         lambda p: p.update(schema_version="2.0"), lambda p: p.update(check_id="FAKE-01")):
            with self.subTest(mutation=mutation):
                result = copy.deepcopy(self.detected)
                mutation(result)
                with self.assertRaises(ContractError):
                    validate(result)

    def test_findings_without_evidence_rejected(self):
        self.detected["findings"][0]["evidence"] = []
        with self.assertRaises(ContractError):
            validate(self.detected)

    def test_invented_file_evidence_rejected(self):
        for field, value in [("value", "log_level: TRACE"), ("line_start", 1), ("source_id", "invented"),
                             ("locator", "wrong.yaml"), ("line_start", 100)]:
            with self.subTest(field=field):
                result = copy.deepcopy(self.detected)
                result["findings"][0]["evidence"][0][field] = value
                with self.assertRaises(ContractError):
                    validate_pair(self.input, result)

    def test_input_result_context_and_revision_must_match(self):
        for field, value in [("context", {}), ("commit_sha", "d" * 40), ("scan_id", "wrong")]:
            with self.subTest(field=field):
                result = copy.deepcopy(self.detected)
                result[field] = value
                with self.assertRaises(ContractError):
                    validate_pair(self.input, result)

    def test_duplicate_finding_and_invalid_identity_rejected(self):
        for duplicate in (True, False):
            result = copy.deepcopy(self.detected)
            if duplicate:
                result["findings"].append(copy.deepcopy(result["findings"][0]))
            else:
                result["findings"][0]["identity"] = "different"
            with self.assertRaises(ContractError):
                validate(result)

    def test_duplicate_source_and_outside_scope_rejected(self):
        for duplicate in (True, False):
            payload = copy.deepcopy(self.input)
            if duplicate:
                payload["sources"].append(copy.deepcopy(payload["sources"][0]))
            else:
                payload["sources"][0]["scope_id"] = "file:other.yaml"
            with self.assertRaises(ContractError):
                validate(payload)

    def test_incomplete_scan_cannot_claim_completed(self):
        self.clean["coverage"]["evaluated_scope"] = []
        with self.assertRaises(ContractError):
            validate(self.clean)

    def test_clean_scan_without_any_supplied_evidence_rejected(self):
        source = example("after-input")
        source["sources"] = []
        with self.assertRaises(ContractError):
            validate_pair(source, self.clean)

    def test_context_boolean_and_number_are_not_interchangeable(self):
        self.input["context"]["threshold"] = True
        self.detected["context"]["threshold"] = 1
        with self.assertRaises(ContractError):
            validate_pair(self.input, self.detected)

    def test_unavailable_and_error_cannot_emit_findings(self):
        for status in ("unavailable", "error"):
            result = copy.deepcopy(self.detected)
            result["status"] = status
            result["coverage"]["evaluated_scope"] = []
            with self.assertRaises(ContractError):
                validate(result)

    def test_partial_scan_requires_coverage_and_explanation(self):
        result = copy.deepcopy(self.detected)
        result["scope"].append("file:missing.yaml")
        result["status"] = "partial"
        validate(result)
        result["coverage"]["limitations"] = []
        with self.assertRaises(ContractError):
            validate(result)

    def test_finding_outside_evaluated_scope_rejected(self):
        self.detected["findings"][0]["scope_id"] = "file:other.yaml"
        with self.assertRaises(ContractError):
            validate(self.detected)

    def test_line_moves_preserve_identity_and_still_verify(self):
        before = self.detected["findings"][0]["fingerprint"]
        self.input["sources"][0]["content"] = "# moved\n" + self.input["sources"][0]["content"]
        self.detected["findings"][0]["evidence"][0]["line_start"] = 3
        validate_pair(self.input, self.detected)
        self.assertEqual(before, self.detected["findings"][0]["fingerprint"])
        self.assertEqual(before, fingerprint("github:AWS-env/example", "OBS-01", "file:config/production.yaml", "production-log-level"))

    def test_compatible_clean_scan_marks_no_longer_detected(self):
        result = compare(self.detected, self.clean)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["no_longer_detected"], [self.detected["findings"][0]["fingerprint"]])
        self.assertEqual(result["unknown"], [])

    def test_detected_after_clean_is_new_and_repeated_finding_persists(self):
        self.assertEqual(len(compare(self.clean, self.detected)["new"]), 1)
        self.assertEqual(len(compare(self.detected, self.detected)["persisting"]), 1)

    def test_missing_failed_partial_and_changed_evaluations_cannot_resolve(self):
        cases = []
        for status in ("unavailable", "error"):
            result = copy.deepcopy(self.clean)
            result["status"] = status
            result["coverage"]["evaluated_scope"] = []
            cases.append(result)
        for field, value in [("detector_version", "2.0.0"), ("context", {"environment": "development"})]:
            result = copy.deepcopy(self.clean)
            result[field] = value
            cases.append(result)
        partial = copy.deepcopy(self.clean)
        partial["scope"].append("file:missing.yaml")
        partial["status"] = "partial"
        cases.append(partial)
        changed_scope = copy.deepcopy(self.clean)
        changed_scope["scope"] = ["file:other.yaml"]
        changed_scope["coverage"]["evaluated_scope"] = changed_scope["scope"][:]
        cases.append(changed_scope)
        for result in cases:
            with self.subTest(status=result["status"], context=result["context"]):
                comparison = compare(self.detected, result)
                self.assertFalse(comparison["comparable"])
                self.assertEqual(comparison["no_longer_detected"], [])
                self.assertEqual(len(comparison["unknown"]), 1)

    def test_cross_repository_or_check_comparison_rejected(self):
        for field, value in [("repository_id", "github:other/repository"), ("check_id", "OBS-02")]:
            result = copy.deepcopy(self.clean)
            result[field] = value
            with self.assertRaises(ContractError):
                compare(self.detected, result)

    def measurement_pair(self):
        source = copy.deepcopy(self.input)
        source["sources"] = [{
            "source_id": "logs", "scope_id": source["scope"][0], "kind": "telemetry",
            "locator": "metrics:api/logs", "data": {"debug_bytes": 1024}
        }]
        result = copy.deepcopy(self.detected)
        result["findings"][0]["evidence"] = [{
            "source_id": "logs", "kind": "telemetry", "locator": "metrics:api/logs",
            "field": "debug_bytes", "value": 1024
        }]
        result["measurements"] = [{
            "metric": "log_volume", "value": 1024, "unit": "bytes", "basis": "measured",
            "boundary": "service:api", "allocation_key": "api:debug-log-volume",
            "window": {"start": "2026-10-09T00:00:00Z", "end": "2026-10-09T01:00:00Z"},
            "provenance": {"source_ids": ["logs"], "method": "Sum debug_bytes over the stated hour", "assumptions": []}
        }]
        return source, result

    def test_telemetry_and_artifact_values_verified(self):
        for kind in ("telemetry", "artifact"):
            source, result = self.measurement_pair()
            source["sources"][0]["kind"] = kind
            result["findings"][0]["evidence"][0]["kind"] = kind
            validate_pair(source, result)
            result["findings"][0]["evidence"][0]["value"] = 9999
            with self.assertRaises(ContractError):
                validate_pair(source, result)

    def test_measurement_units_windows_provenance_and_duplicates(self):
        mutations = [
            lambda m: m.update(unit="kWh"),
            lambda m: m.update(value=-1),
            lambda m: m.update(value=float("nan")),
            lambda m: m.update(value=float("inf")),
            lambda m: m["window"].update(start="not-a-date"),
            lambda m: m["window"].update(start="2026-10-09T00:00:00"),
            lambda m: m["window"].update(end=m["window"]["start"]),
            lambda m: m["provenance"].update(source_ids=["invented"]),
        ]
        for mutation in mutations:
            source, result = self.measurement_pair()
            mutation(result["measurements"][0])
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_pair(source, result)
        source, result = self.measurement_pair()
        result["measurements"].append(copy.deepcopy(result["measurements"][0]))
        with self.assertRaises(ContractError):
            validate_pair(source, result)


if __name__ == "__main__":
    unittest.main()
