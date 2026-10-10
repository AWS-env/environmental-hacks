"""Run every committed CI verification case (fixtures/ci_NN/ci_cases.json) through the detector.

Each case id maps to the Verification plan comment on the check's issue. Every result is validated with the
shared contract (including evidence quotes against the supplied sources) and compared with the expected status,
finding identities, evidence lines or fields, confidence and coverage. History cases use synthetic normalized
data; test_ci_real.py checks the same checks against real GitHub Actions captures.
"""
import json
import unittest

from helpers_ci import FIXTURES, run_ci_check
from owner_c.ci.registry import CHECKS


def _cases():
    for cases_file in sorted(FIXTURES.glob("ci_[0-9][0-9]/ci_cases.json")):
        check_id = cases_file.parent.name.upper().replace("_", "-")
        for case in json.loads(cases_file.read_text(encoding="utf-8")):
            yield check_id, case


class CiVerificationCases(unittest.TestCase):
    def test_all_cases(self):
        count = 0
        for check_id, case in _cases():
            with self.subTest(case=case["id"]):
                count += 1
                _payload, result = run_ci_check(
                    check_id, case["files"], artifacts=case.get("artifacts"), context=case.get("context"))
                expect = case["expect"]
                self.assertEqual(result["status"], expect["status"], case["title"])
                got = sorted(f["identity"] for f in result["findings"])
                want = sorted(f["identity"] for f in expect["findings"])
                self.assertEqual(got, want, case["title"])
                by_identity = {f["identity"]: f for f in result["findings"]}
                for wanted in expect["findings"]:
                    finding = by_identity[wanted["identity"]]
                    if "line" in wanted:
                        self.assertEqual(finding["evidence"][0]["line_start"], wanted["line"], case["title"])
                    if "confidence" in wanted:
                        self.assertEqual(finding["confidence"], wanted["confidence"], case["title"])
                    if "field" in wanted:
                        cited = [e for e in finding["evidence"] if e["kind"] == "artifact"]
                        self.assertEqual([(e["field"], e["value"]) for e in cited],
                                         [(wanted["field"], wanted["value"])], case["title"])
                if "evaluated" in expect:
                    self.assertEqual(sorted(result["coverage"]["evaluated_scope"]),
                                     sorted(f"file:{p}" for p in expect["evaluated"]), case["title"])
                limitations = " | ".join(result["coverage"]["limitations"])
                for text in expect.get("limitations_contain", []):
                    self.assertIn(text, limitations, case["title"])
                self.assertEqual(result["measurements"], [])
        self.assertGreater(count, 0, "no CI verification cases discovered")

    def test_ci_case_files_never_use_the_name_another_runner_globs(self):
        """tests/test_cases.py runs every `*/cases.json` through the Python runner; CI cases must not be named that."""
        stray = [p.as_posix() for p in FIXTURES.glob("ci_[0-9][0-9]/cases.json")]
        self.assertEqual(stray, [], "rename to ci_cases.json: the shared runner would run these through the wrong detector")

    def test_every_check_has_unique_ids_a_positive_and_a_clean_negative(self):
        by_check = {}
        for check_id, case in _cases():
            by_check.setdefault(check_id, []).append(case)
        self.assertEqual(set(by_check), set(CHECKS), "every registered check needs cases, and every case set a check")
        for check_id, cases in by_check.items():
            ids = [c["id"] for c in cases]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate case ids in {check_id}")
            self.assertTrue(all(i.startswith(check_id + "-") for i in ids), check_id)
            self.assertTrue(any(c["expect"]["findings"] for c in cases), f"{check_id} lacks a positive case")
            self.assertTrue(any(c["expect"]["status"] == "completed" and not c["expect"]["findings"]
                                for c in cases), f"{check_id} lacks a clean negative case")


if __name__ == "__main__":
    unittest.main()
