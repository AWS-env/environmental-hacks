"""Run every committed verification case (fixtures/<check>/cases.json) through the detector.

Each case id maps to the Verification plan comment on the check's issue. Every result is validated
with the shared contract (including evidence quotes against the supplied sources) and compared with
the expected status, finding identities, evidence lines and coverage.
"""
import json
import unittest

from helpers import FIXTURES, run_check


def _cases():
    for cases_file in sorted(FIXTURES.glob("*/cases.json")):
        data = json.loads(cases_file.read_text())
        if isinstance(data, dict):  # {"check_id": "CODE-RT.6", "cases": [...]}
            check_id, cases = data["check_id"], data["cases"]
        else:  # py_09 -> PY-09
            check_id, cases = cases_file.parent.name.upper().replace("_", "-"), data
        for case in cases:
            yield check_id, case


class VerificationCases(unittest.TestCase):
    def test_all_cases(self):
        count = 0
        for check_id, case in _cases():
            with self.subTest(case=case["id"]):
                count += 1
                _payload, result = run_check(
                    check_id, case["files"], artifacts=case.get("artifacts"), context=case.get("context"),
                    include_tests=case.get("include_tests", False))
                expect = case["expect"]
                self.assertEqual(result["status"], expect["status"], case["title"])
                got = sorted((f["identity"], f["evidence"][0].get("line_start", 0)) for f in result["findings"])
                want = sorted((f["identity"], f["line"]) for f in expect["findings"])
                self.assertEqual(got, want, case["title"])
                for finding, wanted in zip(sorted(result["findings"], key=lambda f: f["identity"]),
                                           sorted(expect["findings"], key=lambda f: f["identity"])):
                    if "confidence" in wanted:
                        self.assertEqual(finding["confidence"], wanted["confidence"], case["title"])
                if "evaluated" in expect:
                    self.assertEqual(sorted(result["coverage"]["evaluated_scope"]),
                                     sorted(f"file:{p}" for p in expect["evaluated"]), case["title"])
                limitations = " | ".join(result["coverage"]["limitations"])
                for text in expect.get("limitations_contain", []):
                    self.assertIn(text, limitations, case["title"])
                self.assertEqual(result["measurements"], [])
        self.assertGreater(count, 0, "no verification cases discovered")

    def test_every_case_has_unique_id_and_positive_and_negative(self):
        by_check = {}
        for check_id, case in _cases():
            by_check.setdefault(check_id, []).append(case)
        for check_id, cases in by_check.items():
            ids = [c["id"] for c in cases]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate case ids in {check_id}")
            self.assertTrue(all(i.startswith(check_id.replace(".", "-") + "-") for i in ids), check_id)
            self.assertTrue(any(c["expect"]["findings"] for c in cases), f"{check_id} lacks a positive case")
            self.assertTrue(any(c["expect"]["status"] == "completed" and not c["expect"]["findings"]
                                for c in cases), f"{check_id} lacks a clean negative case")


if __name__ == "__main__":
    unittest.main()
