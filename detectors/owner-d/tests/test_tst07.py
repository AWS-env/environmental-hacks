"""Behavioral tests for the TST-07 Verbose Test detector (issue #262)."""

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
from owner_d.tst07 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst07"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_test_statements": 30}
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_checkout.py",
    "negative.py": "tests/test_inventory.py",
    "exceptions.py": "tests/test_suppressed.py",
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


def make_input(*names, sources=None, scope=None, context=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-tst07-001",
        "commit_sha": "7777777777777777777777777777777777777777",
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


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


class Tst07PositiveTests(unittest.TestCase):
    """TST07-01: tests with more statements than the limit are flagged with exact evidence."""

    def test_verbose_tests_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_checkout.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "CheckoutTest.test_full_checkout": (
                6, "low", "32 statements over 32 code lines", "after 26 statements of setup"),
            "test_report_pipeline": (41, "low", "32 statements over 32 code lines", "after 18 statements of setup"),
            "test_everything": (76, "medium", "66 statements over 10 code lines", "after 64 statements of setup"),
            "test_smoke_all_formats": (89, "low", "31 statements over 31 code lines", "none of them is a recognised assertion"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, confidence, size, setup) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(size, finding["summary"])
                self.assertIn(setup, finding["summary"])
                self.assertIn("more than the configured 30 (context.max_test_statements)", finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_checkout.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"].splitlines()[0], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Tst07NegativeTests(unittest.TestCase):
    """TST07-02: long lines, literals, docstrings and non-test code do not make a verbose test."""

    def test_long_looking_but_short_tests_and_non_tests_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_inventory.py"])
        self.assertEqual(result["findings"], [])

    def test_lower_limit_flags_the_same_file(self):
        """The limit is the only judgment: at 3 statements the 4-statement test is reported."""
        _, result = run("negative.py", context={**CONTEXT, "max_test_statements": 3})
        self.assertEqual(identities(result), ["InventoryTest.test_documented"])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-07 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst07ExceptionTests(unittest.TestCase):
    """TST07-03: noqa on the def line, skipped tests and __test__ = False are not flagged."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("  # noqa: TST-07", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_end_to_end_scenario"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 5)

    def test_other_noqa_codes_do_not_suppress(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("# noqa: TST-07", "# noqa: E501")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_end_to_end_scenario"])


class Tst07IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST07-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_checkout.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_setting_is_unavailable(self):
        """TST07-04: the limit is a required judgment call; without it nothing is evaluated."""
        for context, reason in (
            ({"language": "python"}, "missing required context setting: max_test_statements"),
            ({**CONTEXT, "max_test_statements": 0}, "context.max_test_statements must be a positive integer"),
            ({**CONTEXT, "max_test_statements": "30"}, "context.max_test_statements must be a positive integer"),
            ({**CONTEXT, "max_test_statements": True}, "context.max_test_statements must be a positive integer"),
            ({**CONTEXT, "max_test_statements": 30.0}, "context.max_test_statements must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST07-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_checkout.py"])
        self.assertTrue(all(f["scope_id"] == "file:tests/test_checkout.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst07BoundaryTests(unittest.TestCase):
    """TST07-06: the limit is strict, nested statements count, the docstring does not, names repeat."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("test_thirty_one", 12, "low"),
            ("test_nested_blocks_count", 29, "low"),
            ("test_exactly_sixty", 48, "low"),
            ("test_sixty_one", 60, "medium"),
            ("test_thirty_one#2", 72, "low"),
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


class Tst07ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_checkout.py", "CheckoutTest.test_full_checkout")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "def test_invented(self):"
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
        committed = json.loads((FIXTURES / "tst07-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst07CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst07_result(self):
        input_path = FIXTURES / "tst07-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 4)


if __name__ == "__main__":
    unittest.main()
