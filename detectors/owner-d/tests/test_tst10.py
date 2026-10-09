"""Behavioral tests for the TST-10 Redundant Assertion detector (issue #265)."""

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
from owner_d.tst10 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst10"
REPOSITORY_ID = "github:AWS-env/example"
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_jobs.py",
    "negative.py": "tests/test_orders.py",
    "exceptions.py": "tests/test_money.py",
    "boundary.py": "tests/test_boundary.py",
    "helpers.py": "app/helpers.py",
    "broken.py": "tests/test_broken.py",
    "cart.test.js": "web/cart.test.js",
}


def static_source(name, content=None, locator=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    locator = locator or LOCATORS[name]
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{locator}",
        "kind": "static",
        "locator": locator,
        "content": content,
    }


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-tst10-001",
        "commit_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"language": "python"},
        "scope": scope if scope is not None else [source["scope_id"] for source in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


class Tst10PositiveTests(unittest.TestCase):
    """TST10-01: always-true assertions are flagged with exact evidence."""

    def test_always_true_assertions_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_jobs.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "PlaceholderTest.test_placeholder:self.assertTrue": (9, "is a literal"),
            "PlaceholderTest.test_same_literal:self.assertEqual": (12, "compares a literal with itself"),
            "PlaceholderTest.test_literal_none:self.assertIsNone": (15, "is a literal"),
            "PlaceholderTest.test_not_empty_literal:self.assertFalse": (18, "is a literal"),
            "test_bare_true:assert": (23, "is a literal"),
            "test_tuple_mistake:assert": (28, "is a non-empty tuple"),
            "test_same_string:assert": (32, "compares a literal with itself"),
            "test_numpy_same_literal:numpy.testing.assert_equal": (36, "compares a literal with itself"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, reason) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], "medium")
                self.assertIn(f"always passes: its condition {reason}", finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_jobs.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Tst10NegativeTests(unittest.TestCase):
    """TST10-02: assertions on produced values and differently spelled literals are not flagged."""

    def test_value_dependent_assertions_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_orders.py"])
        self.assertEqual(result["findings"], [])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-10 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst10ExceptionTests(unittest.TestCase):
    """TST10-03: reflexivity checks, explicit failures, non-singleton identity, noqa and helpers."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("  # noqa: TST-10", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_noqa:assert"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 29)


class Tst10IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST10-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_jobs.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST10-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_jobs.py"])
        self.assertTrue(all(f["scope_id"] == "file:tests/test_jobs.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst10BoundaryTests(unittest.TestCase):
    """TST10-06: whitespace-only differences still match; always-false literals are not reported."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("test_two_in_one_test:assert", 5),
            ("test_two_in_one_test:assert#2", 6),
            ("test_spacing_is_ignored:assert", 10),
            ("test_none_is_none:assert", 18),
        ])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        _, after = run(sources=[static_source("boundary.py", shifted)])
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Tst10ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_jobs.py", "test_bare_true:assert")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "assert invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py", "helpers.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "tst10-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst10CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst10_result(self):
        input_path = FIXTURES / "tst10-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 8)


if __name__ == "__main__":
    unittest.main()
