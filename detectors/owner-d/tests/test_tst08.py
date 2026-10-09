"""Behavioral tests for the TST-08 Sensitive Equality detector (issue #263)."""

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
from owner_d.tst08 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst08"
REPOSITORY_ID = "github:AWS-env/example"
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_cart.py",
    "negative.py": "tests/test_orders.py",
    "exceptions.py": "tests/test_money.py",
    "boundary.py": "tests/test_boundary.py",
    "helpers.py": "app/helpers.py",
    "broken.py": "tests/test_broken.py",
    "cart.test.js": "web/cart.test.js",
}
LITERAL = "with literal expected text, so a change to its string formatting fails the test"
BOTH = "text instead of comparing the values"


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
        "scan_id": "scan-tst08-001",
        "commit_sha": "8888888888888888888888888888888888888888",
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


class Tst08PositiveTests(unittest.TestCase):
    """TST08-01: equality assertions on str()/repr() text are flagged with exact evidence."""

    def test_text_equality_assertions_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_cart.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "CartTest.test_add_item:self.assertEqual": (9, 1, "low", "the str() text of `cart` " + LITERAL),
            "CartTest.test_owner:self.assertEqual": (13, 1, "medium", "the repr() text of `order.owner` " + LITERAL),
            "CartTest.test_copy:self.assertEqual": (17, 1, "medium", "their repr() " + BOTH),
            "CartTest.test_not_empty:self.assertNotEqual": (
                20, 1, "low", "the __str__() text of `Cart(['pear'])` " + LITERAL),
            "test_total_label:assert": (26, 1, "low", "the str() text of `total` " + LITERAL),
            "test_receipt:assert": (31, 1, "low", "the str() text of `receipt` " + LITERAL),
            "test_reload:assert": (35, 1, "medium", "their str() " + BOTH),
            "test_build:assert": (39, 3, "medium", "the repr() text of `build()` " + LITERAL),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, span, confidence, detail) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(detail, finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_cart.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], "\n".join(lines[line - 1:line - 1 + span]))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Tst08NegativeTests(unittest.TestCase):
    """TST08-02: structured equality, conversions, tolerant checks and message slots are not flagged."""

    def test_similar_non_text_equality_assertions_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_orders.py"])
        self.assertEqual(result["findings"], [])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-08 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst08ExceptionTests(unittest.TestCase):
    """TST08-03: tests about the text, exception/warning messages, noqa and helpers."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("  # noqa: TST-08", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_noqa:assert"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 50)

    def test_module_or_directory_about_the_text_is_not_flagged(self):
        for locator in ("tests/test_repr.py", "tests/str/test_cart.py"):
            with self.subTest(locator=locator):
                _, result = run(sources=[static_source("positive.py", locator=locator)])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])

    def test_renaming_a_repr_test_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("def test_repr(", "def test_amount(")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["MoneyTest.test_amount:self.assertEqual"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 10)
        self.assertEqual(result["findings"][0]["confidence"], "medium")


class Tst08IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST08-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_cart.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST08-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_cart.py"])
        self.assertTrue(all(f["scope_id"] == "file:tests/test_cart.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst08BoundaryTests(unittest.TestCase):
    """TST08-06: repeats, chained/reassigned operands, concatenated text and whole-word name matching."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("test_two_in_one_test:assert", 5, "low"),
            ("test_two_in_one_test:assert#2", 6, "low"),
            ("test_concatenated_expected:assert", 20, "low"),
            ("test_stream_prefix_is_not_matched:assert", 24, "medium"),
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


class Tst08ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_cart.py", "test_reload:assert")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "assert str(invented) == 'x'"
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
        committed = json.loads((FIXTURES / "tst08-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst08CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst08_result(self):
        input_path = FIXTURES / "tst08-01-positive-input.json"
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
