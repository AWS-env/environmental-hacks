"""CI history checks against REAL GitHub Actions run history (fixtures/real_ci, captured 2026-10-10).

Two kinds of ground truth:
- `lab__lab.yml.json`: a private scratch repository (Medhansh-741/owner-c-ci-lab) whose workflow was built to fail in
  known ways and was re-run through the API (re-run failed jobs, a single job, all jobs, cancellation, pull requests).
  The expected numbers below follow from how the scenarios were built, not from the detector.
- public repositories (psf/black, numpy/numpy, tiangolo/sqlmodel, fastapi/fastapi), hand-verified against the raw
  JSON. psf/black's first attempts have conclusion `action_required` (the first-time-contributor approval gate), so
  they are not rechecks: its commit 2286fd0 shows 3 attempts but only ONE re-run after a failure.
"""
import json
import unittest

from helpers_ci import FIXTURES, run_ci_check
from owner_c.ci.normalize.github_actions import PROFILER, normalize
from owner_c.ci.registry import CHECKS

REAL = FIXTURES / "real_ci"


def _raw(name):
    return json.loads((REAL / f"{name}.json").read_text(encoding="utf-8"))


class LabGroundTruth(unittest.TestCase):
    """Runs A-I of the lab (9 runs): A recover + failed-jobs re-run, B fail + two failed-jobs re-runs, C single-job
    re-run, D re-run all (manual dispatch), E cancelled, F clean, G pull request that recovers, H pull request that
    keeps failing, I pull request re-run with ALL jobs."""

    def setUp(self):
        self.data = normalize(_raw("lab__lab.yml"))

    def test_counts_follow_from_how_the_scenarios_were_built(self):
        d = self.data
        self.assertEqual(d["runs_total"], 9)
        self.assertEqual(d["max_rechecks_per_sha"], 2)     # PR H: attempts 2 and 3 each follow a failure (PR G: 1)
        self.assertEqual(d["reran_passing_jobs"], 2)       # pull request I re-ran ALL jobs: `ok` and `matrix (a)` passed and ran again
        self.assertEqual(d["repeat_failed_reruns"], 2)     # B and H re-ran `always-fail` and it failed again; C does not count
        self.assertEqual(d["fail_then_pass_runs"], 4)      # A, D, G and I failed first and passed later
        self.assertEqual(d["fail_then_pass:flaky"], 7)     # A B C D G H I: `flaky` fails on attempt 1, passes on the re-run
        self.assertEqual(d["fail_then_pass:matrix (b)"], 6)  # not C: `matrix (b)` stayed failed (never re-executed)
        self.assertNotIn("fail_then_pass:always-fail", d)

    def test_a_failure_that_was_only_carried_over_is_not_a_repeated_failure(self):
        """Run C re-ran only the `flaky` job: `matrix (b)` is still failed in attempt 2 but was not re-executed."""
        run = next(r for r in _raw("lab__lab.yml")["runs"] if r["conclusion"] == "failure" and r["event"] == "workflow_dispatch"
                   and len(r["attempts"]) == 2)
        attempt2 = {j["name"]: j for j in run["attempts"][1]["jobs"]}
        self.assertEqual(attempt2["matrix (b)"]["conclusion"], "failure")
        self.assertLess(attempt2["matrix (b)"]["started_at"], attempt2["matrix (b)"]["created_at"])  # carried over
        self.assertGreaterEqual(attempt2["flaky"]["started_at"], attempt2["flaky"]["created_at"])    # re-executed
        one_run = dict(_raw("lab__lab.yml"), runs=[run])
        self.assertEqual(normalize(one_run)["repeat_failed_reruns"], 0)

    def test_a_cancelled_run_counts_nothing(self):
        cancelled = [r for r in _raw("lab__lab.yml")["runs"] if r["conclusion"] == "cancelled"]
        self.assertEqual(len(cancelled), 1)
        data = normalize(dict(_raw("lab__lab.yml"), runs=cancelled))
        self.assertEqual((data["max_rechecks_per_sha"], data["fail_then_pass_runs"], data["repeat_failed_reruns"]), (0, 0, 0))

    def test_stage_chains_come_from_executed_jobs(self):
        self.assertLess(self.data["chain_seconds:flaky->after-flaky"], 60)


class RealPublicRepos(unittest.TestCase):
    def test_black_approval_gated_attempts_are_not_rechecks(self):
        data = normalize(_raw("psf__black__test.yml"))
        self.assertEqual(data["runs_total"], 100)
        self.assertEqual(data["max_rechecks_per_sha"], 1)
        self.assertEqual(data["repeat_failed_reruns"], 0)
        self.assertEqual(data["fail_then_pass_runs"], 2)  # commits 9dcbe69 and 2286fd0
        self.assertEqual(data["fail_then_pass:test (3.15, windows-11-arm)"], 2)

    def test_black_attempt_conclusions_were_collected(self):
        gated = next(r for r in _raw("psf__black__test.yml")["runs"]
                     if r["head_sha"].startswith("2286fd0") and len(r["attempts"]) == 3)
        self.assertEqual([a["conclusion"] for a in gated["attempts"]], ["action_required", "failure", "success"])

    def test_numpy_stage_chain_collapses_matrix_legs(self):
        data = normalize(_raw("numpy__numpy__linux.yml"))
        self.assertEqual(data["max_rechecks_per_sha"], 1)
        self.assertEqual(data["fail_then_pass:full"], 1)
        self.assertGreater(data["chain_seconds:smoke_test->debug"], 1000)
        self.assertFalse(any("(" in k for k in data if k.startswith("chain_seconds:")))

    def test_scheduled_runs(self):
        sql = normalize(_raw("tiangolo__sqlmodel__test.yml"))
        self.assertEqual((sql["scheduled_runs_total"], sql["scheduled_runs_same_sha"]), (7, 2))
        self.assertEqual(sql["chain_seconds:Test->coverage-combine"], 122.0)
        issue = normalize(_raw("fastapi__fastapi__issue-manager.yml"))
        self.assertEqual((issue["scheduled_runs_total"], issue["scheduled_runs_same_sha"]), (8, 2))

    def test_typescript_nightly_is_schedule_heavy(self):
        """59 of 60 runs are scheduled, on only 21 distinct commits: 38 scheduled runs repeat an earlier commit (hand-counted)."""
        ts = normalize(_raw("microsoft__TypeScript__nightly.yaml"))
        self.assertEqual((ts["scheduled_runs_total"], ts["scheduled_runs_same_sha"]), (59, 38))
        self.assertEqual((ts["max_rechecks_per_sha"], ts["fail_then_pass_runs"]), (0, 0))

    def test_vite_reruns_hand_checked(self):
        """Three re-run runs. Pull request run 37897223199 re-ran 4 jobs: `windows` failed again, `ubuntu` passed, the
        result-gate job `Build & Test Failed` passed before and ran again (1 passing job re-run). Pull request run
        37889886287 recovered; its gate job ended `skipped` (no work), so it is not a re-run passing job (before the
        fix both gate jobs were counted)."""
        vite = normalize(_raw("vitejs__vite__ci.yml"))
        self.assertEqual(vite["max_rechecks_per_sha"], 1)
        self.assertEqual(vite["repeat_failed_reruns"], 1)
        self.assertEqual(vite["reran_passing_jobs"], 1)
        self.assertEqual(vite["fail_then_pass_runs"], 2)

    def test_pytest_large_matrix_and_attempt_without_detail(self):
        """One pull request run has attempts [failure, no detail, failure]; the collector got no jobs for attempt 2, so
        the recheck count is a lower bound (1), never an over-count. 32 jobs per attempt."""
        data = normalize(_raw("pytest-dev__pytest__test.yml"))
        self.assertEqual((data["runs_total"], data["max_rechecks_per_sha"], data["repeat_failed_reruns"]), (60, 1, 0))


@unittest.skipUnless("CI-05" in CHECKS, "needs the CI-01/02/03/05 history checks")
class RealChecks(unittest.TestCase):
    def _run(self, check_id, name, path, context=None):
        data = normalize(_raw(name))
        return run_ci_check(check_id, {}, artifacts={PROFILER: {path: data}}, context=context)[1]

    def test_black_is_not_flagged_by_ci_01_but_its_flaky_job_is_flagged_by_ci_03(self):
        path = ".github/workflows/test.yml"
        ci01 = self._run("CI-01", "psf__black__test.yml", path)
        self.assertEqual((ci01["status"], ci01["findings"]), ("completed", []))  # was a false positive before CD-4
        ci03 = self._run("CI-03", "psf__black__test.yml", path)
        self.assertEqual([f["identity"] for f in ci03["findings"]], ["job:test (3.15, windows-11-arm):fail-then-pass"])
        for check in ("CI-02", "CI-05"):
            result = self._run(check, "psf__black__test.yml", path)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["findings"], [], check)

    def test_lab_is_flagged_where_the_scenarios_were_built_to_fail(self):
        path = ".github/workflows/lab.yml"
        found = {c: [f["identity"] for f in self._run(c, "lab__lab.yml", path, {"min_runs": 5})["findings"]]
                 for c in ("CI-01", "CI-02", "CI-03", "CI-05")}
        self.assertEqual(found["CI-01"], ["workflow:repeated-reruns"])          # PR H: 2 rechecks (threshold 2)
        self.assertEqual(found["CI-02"], ["workflow:blind-reruns"])             # 2 runs (threshold 2)
        self.assertEqual(found["CI-03"], ["job:flaky:fail-then-pass", "job:matrix (b):fail-then-pass"])
        self.assertEqual(found["CI-05"], ["workflow:brown-builds"])             # 4 runs (threshold 3)

    def test_threshold_is_configurable(self):
        result = self._run("CI-01", "numpy__numpy__linux.yml", ".github/workflows/linux.yml", context={"min_rechecks": 1})
        self.assertEqual([f["identity"] for f in result["findings"]], ["workflow:repeated-reruns"])


if __name__ == "__main__":
    unittest.main()
