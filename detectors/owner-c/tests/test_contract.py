"""Contract-level behaviour that is not specific to a single check."""
import unittest

from helpers import REPO, SHA, run_check
from owner_c.connector import build_inputs
from owner_c.contract import fingerprint
from owner_c.runner import EvaluationError, evaluate
from shared.contracts.validation import compare, fingerprint as shared_fingerprint


class FingerprintTests(unittest.TestCase):
    def test_matches_shared_implementation(self):
        for parts in [(REPO, "PY-09", "file:a.py", "f(a)"), ("github:o/r", "PY-04", "file:é/ü.py", "build:s#2")]:
            self.assertEqual(fingerprint(*parts), shared_fingerprint(*parts))


class EvidenceTests(unittest.TestCase):
    def test_multiline_statement_is_quoted_in_full(self):
        src = "def f(a=[\n    1,\n]):\n    pass\n"
        _payload, result = run_check("PY-09", {"app.py": src})
        evidence = result["findings"][0]["evidence"][0]
        self.assertEqual((evidence["line_start"], evidence["value"]), (1, "def f(a=[\n    1,\n]):"))

    def test_finding_carries_recommendation_references_and_limitation(self):
        _payload, result = run_check("PY-09", {"app.py": "def f(a=[]):\n    pass\n"})
        finding = result["findings"][0]
        self.assertTrue(finding["recommendation"] and finding["references"])
        self.assertTrue(result["coverage"]["limitations"])  # static-only caveat is always stated
        self.assertEqual(result["measurements"], [])  # no environmental values are invented


class IdentityTests(unittest.TestCase):
    def test_fingerprint_survives_line_movement_and_changes_when_fixed(self):
        bad = "def f(a=[]):\n    pass\n"
        moved = "# a comment\n\n\n" + bad
        fixed = "def f(a=None):\n    pass\n"
        before = run_check("PY-09", {"app.py": bad})[1]
        after_move = run_check("PY-09", {"app.py": moved})[1]
        after_fix = run_check("PY-09", {"app.py": fixed})[1]
        self.assertEqual(before["findings"][0]["fingerprint"], after_move["findings"][0]["fingerprint"])
        self.assertNotEqual(before["findings"][0]["evidence"][0]["line_start"],
                            after_move["findings"][0]["evidence"][0]["line_start"])
        same = compare(before, after_move)
        self.assertEqual((same["persisting"], same["no_longer_detected"]), ([before["findings"][0]["fingerprint"]], []))
        fixed_cmp = compare(before, after_fix)
        self.assertEqual(fixed_cmp["no_longer_detected"], [before["findings"][0]["fingerprint"]])

    def test_incomplete_coverage_never_proves_a_fix(self):
        before = run_check("PY-09", {"app.py": "def f(a=[]):\n    pass\n"})[1]
        broken = run_check("PY-09", {"app.py": "def (\n"})[1]  # unparsable -> unavailable
        self.assertEqual(broken["status"], "unavailable")
        cmp = compare(before, broken)
        self.assertFalse(cmp["comparable"])
        self.assertEqual(cmp["no_longer_detected"], [])


class RejectionTests(unittest.TestCase):
    def test_unsupported_check_and_version_are_rejected(self):
        payload = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", checks=["PY-09"],
                               files=[("a.py", "x = 1\n")])[0]
        for field, value in [("check_id", "PY-99"), ("detector_version", "9.9.9")]:
            with self.subTest(field=field):
                with self.assertRaises(EvaluationError):
                    evaluate({**payload, field: value})
        with self.assertRaises(EvaluationError):
            evaluate({**payload, "kind": "result"})

    def test_scope_item_without_source_is_not_evaluated(self):
        payload = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", checks=["PY-09"],
                               files=[("a.py", "x = 1\n")])[0]
        payload["scope"] = payload["scope"] + ["file:ghost.py"]
        result = evaluate(payload)
        self.assertEqual(result["status"], "partial")
        self.assertIn("ghost.py", " ".join(result["coverage"]["limitations"]))


if __name__ == "__main__":
    unittest.main()
