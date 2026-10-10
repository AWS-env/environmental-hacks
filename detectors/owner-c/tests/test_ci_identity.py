"""A finding keeps its identity and fingerprint when unrelated lines move, and the budget for the largest legal file.

The contract says the fingerprint is SHA-256 of [repository_id, check_id, scope_id, identity] and must not change
when lines move; these tests check that for the CI identities (job id based, no line numbers).
"""
import time
import unittest

from helpers_ci import run_ci_check
from owner_c.ci import yamlio
from owner_c.ci.registry import CHECKS, kind

WF = ".github/workflows/ci.yml"
STATIC = [k for k, m in CHECKS.items() if kind(m) == "static"]
BASE = "name: CI\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n"
OTHER_JOB = "  lint:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo lint\n"


def findings(check, text):
    result = run_ci_check(check, {WF: text})[1]
    return {f["identity"]: (f["fingerprint"], f["evidence"][0]["line_start"]) for f in result["findings"]}


class IdentityStability(unittest.TestCase):
    def test_inserted_comment_lines_move_the_line_but_not_the_fingerprint(self):
        before = findings("CI-11", BASE)
        after = findings("CI-11", "# a\n# b\n# c\n" + BASE)
        self.assertEqual(list(before), list(after))
        for identity in before:
            self.assertEqual(before[identity][0], after[identity][0], "fingerprint must not depend on the line")
            self.assertNotEqual(before[identity][1], after[identity][1], "the evidence line must move")

    def test_adding_or_reordering_unrelated_jobs_keeps_the_fingerprint(self):
        before = findings("CI-11", BASE)
        added_before = findings("CI-11", BASE.replace("jobs:\n", "jobs:\n" + OTHER_JOB))
        added_after = findings("CI-11", BASE + OTHER_JOB)
        for variant in (added_before, added_after):
            self.assertEqual({k: v[0] for k, v in before.items()}, {k: v[0] for k, v in variant.items()})

    @unittest.skipUnless("CI-06" in CHECKS, "needs the CI-06 check")
    def test_a_job_level_finding_keeps_its_fingerprint_when_other_jobs_are_added_around_it(self):
        text = BASE.replace("npm test", "npm ci")
        before = findings("CI-06", text)
        self.assertEqual(list(before), ["job:test:deps:npm"])
        around = findings("CI-06", text.replace("jobs:\n", "jobs:\n" + OTHER_JOB) + OTHER_JOB.replace("lint", "docs"))
        self.assertEqual(before["job:test:deps:npm"][0], around["job:test:deps:npm"][0])

    @unittest.skipUnless("CI-07" in CHECKS, "needs the CI-07 check")
    def test_repeated_identities_get_a_stable_numeric_suffix(self):
        build = "      - run: docker build -t a .\n"
        text = "name: CI\non:\n  push:\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n" + build + build
        before = findings("CI-07", text)
        self.assertEqual(sorted(before), ["job:b:docker-build:docker build", "job:b:docker-build:docker build#2"])
        after = findings("CI-07", "# moved\n\n" + text)
        self.assertEqual({k: v[0] for k, v in before.items()}, {k: v[0] for k, v in after.items()})

    def test_the_same_workflow_under_another_repository_gets_another_fingerprint(self):
        from helpers_ci import REPO
        from owner_c.contract import fingerprint

        identity = next(iter(findings("CI-11", BASE)))
        self.assertNotEqual(fingerprint(REPO, "CI-11", f"file:{WF}", identity),
                            fingerprint("github:other/app", "CI-11", f"file:{WF}", identity))


JOB = """  job{i}:
    name: Build and test {i} (${{{{ matrix.os }}}})
    runs-on: ${{{{ matrix.os }}}}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.npm
          key: npm-${{{{ hashFiles('package-lock.json') }}}}
      - run: npm ci
      - run: npm test
      - uses: actions/upload-artifact@v4
        with:
          name: out-{i}
          path: out/
"""


class LargestLegalFile(unittest.TestCase):
    def test_a_workflow_at_the_line_bound_is_evaluated_within_budget(self):
        n = (yamlio.MAX_LINES - 100) // len(JOB.splitlines())
        text = "name: Big\non:\n  pull_request:\njobs:\n" + "".join(JOB.format(i=i) for i in range(n))
        self.assertLessEqual(len(text.encode()), 1_000_000, "stay under the connector's 1 MB file cap")
        started = time.time()
        results = [run_ci_check(key, {WF: text})[1] for key in STATIC]
        self.assertLess(time.time() - started, 15, "measured ~3 s on a developer laptop for 9 checks")
        self.assertTrue(all(r["status"] == "completed" for r in results))


if __name__ == "__main__":
    unittest.main()
