"""Behavioral tests for the TST-06 Unknown Test detector (issue #261)."""

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
from owner_d.tst06 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst06"
REPOSITORY_ID = "github:AWS-env/example"
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_cart.py",
    "negative.py": "tests/test_orders.py",
    "exceptions.py": "tests/test_exceptions.py",
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
        "scan_id": "scan-tst06-001",
        "commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
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


class Tst06PositiveTests(unittest.TestCase):
    """TST06-01: unittest and pytest tests without any assertion are flagged with exact evidence."""

    def test_tests_without_assertions_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_cart.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "CartTest.test_add_item": (9, "unittest", "contains no assertion"),
            "CartTest.test_empty": (13, "unittest", "has an empty body"),
            "test_checkout_runs": (20, "pytest", "contains no assertion"),
            "TestInvoice.test_render": (26, "pytest", "contains no assertion"),
            "TestInvoice.test_send": (30, "pytest", "contains no assertion"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, framework, what) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], "medium")
                self.assertTrue(finding["summary"].startswith(f"{framework} test {identity} {what}"))
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_cart.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertTrue(evidence["value"].lstrip().startswith(("def ", "async def ")))
                self.assertEqual(evidence["value"].splitlines()[0], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_star_test_module_naming_is_recognised(self):
        source = static_source("positive.py", locator="tests/cart_test.py")
        _, result = run(sources=[source])
        self.assertEqual(len(result["findings"]), 5)


class Tst06NegativeTests(unittest.TestCase):
    """TST06-02: every recognised assertion form keeps a test clean; non-tests are ignored."""

    def test_all_assertion_forms_are_recognised(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_orders.py"])
        self.assertEqual(result["findings"], [])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-06 has nothing to flag",
            result["coverage"]["limitations"],
        )

    def test_pytest_functions_outside_test_modules_are_not_tests(self):
        source = static_source("positive.py", locator="tests/cart_checks.py")
        _, result = run(sources=[source])
        self.assertEqual(identities(result), ["CartTest.test_add_item", "CartTest.test_empty"])


class Tst06ExceptionTests(unittest.TestCase):
    """TST06-03: skips, benchmarks, checker decorators/helpers, doctests, noqa and opt-outs."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        # @pytest.mark.skipif is conditional, so that test still counts.
        self.assertEqual(identities(result), ["test_conditionally_skipped"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 63)


class Tst06IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST06-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_cart.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST06-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_cart.py"])
        self.assertTrue(all(f["scope_id"] == "file:tests/test_cart.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)
        self.assertNotIn("nothing to flag", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst06BoundaryTests(unittest.TestCase):
    """TST06-06: one assertion is enough; identities are qualified and survive line movement."""

    def test_identities_are_qualified_and_disambiguated(self):
        _, result = run("boundary.py")
        self.assertEqual(identities(result), [
            "TestA.test_load",
            "TestB.test_load",
            "TestOuter.TestInner.test_deep",
            "test_retry",
            "test_retry#2",
            "ServiceTest.test_start",
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


class Tst06ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_cart.py", "CartTest.test_add_item")
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
        committed = json.loads((FIXTURES / "tst06-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst06CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst06_result(self):
        input_path = FIXTURES / "tst06-01-positive-input.json"
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
