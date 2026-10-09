"""Behavioral tests for the TST-04 Magic Number Test detector (issue #259)."""

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
from owner_d.tst04 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst04"
REPOSITORY_ID = "github:AWS-env/example"
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_cart.py",
    "negative.py": "tests/test_clean.py",
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


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-tst04-001",
        "commit_sha": "1111111111111111111111111111111111111111",
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


class Tst04PositiveTests(unittest.TestCase):
    """TST04-01: bare numeric literals in assertions are flagged once per test with exact evidence."""

    def test_magic_numbers_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_cart.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "CartTest.test_total": (9, "medium", "1 bare numeric literal with", "42 (line 11);"),
            "CartTest.test_item_count": (13, "low", "2 bare numeric literals", "3 (line 15), 3 (line 16);"),
            "CartTest.test_rounding": (18, "medium", "2 bare numeric literals", "4.67 (line 19), 2.5 (line 20);"),
            "test_shipping_fee": (23, "medium", "1 bare numeric literal with", "7.25 (line 24);"),
            "test_status": (27, "low", "1 bare numeric literal with", "200 (line 29);"),
            "test_ratio_approx": (32, "medium", "1 bare numeric literal with", "-0.25 (line 33);"),
            "test_scores": (36, "medium", "2 bare numeric literals", "0.75 (line 37), 100 (line 38);"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, confidence, count, where) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(count, finding["summary"])
                self.assertIn(where, finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_cart.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"].splitlines()[0], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertIn("1 of 1 is not a len() count", findings["CartTest.test_total"]["summary"])
        self.assertIn("each is a len() count", findings["test_status"]["summary"])
        self.assertEqual(result["measurements"], [])


class Tst04NegativeTests(unittest.TestCase):
    """TST04-02: named, derived, ordinary, tolerance-only and explained values are not flagged."""

    def test_named_and_explained_values_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_clean.py"])
        self.assertEqual(result["findings"], [])

    def test_removing_the_explanations_restores_findings(self):
        content = (FIXTURES / "negative.py").read_text()
        content = content.replace(', "3 items at 14 each"', "").replace("    # 3 items at 14 each\n", "")
        content = content.replace("  # kg, from the catalogue fixture", "")
        _, result = run(sources=[static_source("negative.py", content)])
        self.assertEqual(identities(result), ["CartTest.test_message_explains", "test_comment_explains"])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-04 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst04ExceptionTests(unittest.TestCase):
    """TST04-03: noqa on the def or assertion line, __test__ = False and helpers are not flagged."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_findings(self):
        content = (FIXTURES / "exceptions.py").read_text()
        content = content.replace("  # noqa: TST-04", "").replace("  # noqa: PLR2004", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_suppressed", "test_line_suppressed"])
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [4, 8])


class Tst04IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST04-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_cart.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST04-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Tst04BoundaryTests(unittest.TestCase):
    """TST04-06: 2 and -2 flag, 1 and -1 do not; directives and distant comments do not explain."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("test_two_is_magic", 4, "medium"),
            ("test_minus_two_is_magic", 13, "medium"),
            ("test_directive_comment_is_not_an_explanation", 17, "medium"),
            ("test_comment_two_lines_above_does_not_explain", 21, "medium"),
            ("test_setup_echo_is_low", 27, "low"),
            ("test_two_is_magic#2", 32, "medium"),
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


class Tst04ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_cart.py", "CartTest.test_total")
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
        committed = json.loads((FIXTURES / "tst04-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst04CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst04_result(self):
        input_path = FIXTURES / "tst04-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 7)


if __name__ == "__main__":
    unittest.main()
