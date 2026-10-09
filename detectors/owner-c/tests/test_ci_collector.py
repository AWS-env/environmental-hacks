"""The client-side collector and the normalizer's GitHub semantics, with fake data (no network).

The semantics asserted here were measured on real GitHub in a controlled lab (tests/fixtures/real_ci/lab__*.json,
docs/research/category-3-ci.md): every attempt lists all jobs, a job that was not re-executed keeps the earlier
attempt's timestamps (`started_at < created_at`), and each attempt has its own conclusion.
"""
import io
import pathlib
import re
import unittest
import urllib.error

from owner_c.ci import collector
from owner_c.ci.normalize import github_actions
from owner_c.ci.normalize.github_actions import SCHEMA, RawHistoryError, normalize

WF = ".github/workflows/ci.yml"
T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-01T00:05:00Z"


def _job(name, conclusion, start="2026-01-01T00:00:10Z", end="2026-01-01T00:01:00Z", created="2026-01-01T00:00:00Z"):
    return {"name": name, "conclusion": conclusion, "started_at": start, "completed_at": end, "created_at": created}


def _carried(name, conclusion):
    """A job listed in a later attempt that was NOT re-executed: its timestamps come from the earlier attempt."""
    return _job(name, conclusion, start="2026-01-01T00:00:10Z", end="2026-01-01T00:01:00Z", created="2026-01-01T00:05:00Z")


def _reran(name, conclusion):
    return _job(name, conclusion, start="2026-01-01T00:05:10Z", end="2026-01-01T00:06:00Z", created="2026-01-01T00:05:00Z")


def _attempt(n, conclusion, jobs):
    return {"run_attempt": n, "conclusion": conclusion, "jobs": jobs}


def _run(attempts, event="pull_request", sha="a" * 40, rid=1):
    return {"id": rid, "run_attempt": len(attempts), "event": event, "head_sha": sha, "attempts": attempts}


def _doc(runs):
    return {"schema": SCHEMA, "workflow_path": WF, "runs": runs}


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


class Collector(unittest.TestCase):
    def test_schema_matches_the_normalizer_and_the_file_is_standalone(self):
        self.assertEqual(collector.SCHEMA, github_actions.SCHEMA)
        source = pathlib.Path(collector.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(from|import)\s+owner_c", source, re.M),
                          "the collector must work as one downloaded file")

    def test_fetches_each_attempt_with_its_conclusion_and_all_its_jobs(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "/workflows/ci.yml/runs" in url:
                return {"workflow_runs": [
                    {"id": 7, "run_attempt": 2, "event": "pull_request", "head_sha": "a" * 40, "conclusion": "success",
                     "created_at": "t", "run_started_at": "t", "updated_at": "t", "extra": "dropped"}]}
            if url.endswith("/attempts/1"):
                return {"conclusion": "failure"}
            return {"jobs": [dict(_job("test", "success"), steps=[{"x": 1}])]}

        doc = collector.collect(fetch, "o/r", "ci.yml")
        run = doc["runs"][0]
        self.assertEqual(doc["schema"], SCHEMA)
        self.assertNotIn("extra", run)
        self.assertEqual([(a["run_attempt"], a["conclusion"]) for a in run["attempts"]], [(1, "failure"), (2, "success")])
        self.assertNotIn("steps", run["attempts"][0]["jobs"][0])
        self.assertIn("created_at", run["attempts"][0]["jobs"][0])
        self.assertTrue(any("/attempts/1/jobs" in c for c in calls))
        self.assertTrue(any("/attempts/2/jobs" in c for c in calls))

    def test_a_run_whose_jobs_cannot_be_fetched_is_kept_and_marked(self):
        def fetch(url):
            if "/workflows/" in url:
                return {"workflow_runs": [{"id": 1, "run_attempt": 1, "event": "push", "head_sha": "b" * 40}]}
            raise _http_error(502)

        run = collector.collect(fetch, "o/r", "ci.yml")["runs"][0]
        self.assertEqual(run["attempts"], [])
        self.assertEqual(run["jobs_unavailable"], 502)


class CollectorResilience(unittest.TestCase):
    def test_runs_are_followed_across_pages(self):
        pages = []

        def fetch(url):
            pages.append(url)
            page = int(url.rsplit("page=", 1)[1])
            runs = [{"id": page * 1000 + i, "run_attempt": 1, "event": "push", "head_sha": "a" * 40} for i in range(100 if page < 3 else 20)]
            return {"workflow_runs": runs}

        doc = collector.collect(fetch, "o/r", "ci.yml", max_runs=250, job_detail_runs=0)
        self.assertEqual(len(doc["runs"]), 220)
        self.assertEqual(len([u for u in pages if "/runs?" in u]), 3)
        capped = collector.collect(fetch, "o/r", "ci.yml", max_runs=150, job_detail_runs=0)
        self.assertEqual(len(capped["runs"]), 150)

    def _response(self, code, headers=None):
        return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(b""))

    def test_a_rate_limit_is_waited_out_then_retried(self):
        from unittest import mock

        calls = []

        class Ok:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_, *a):
                return b'{"ok": true}'

        def urlopen(request, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise self._response(429, {"Retry-After": "3"})
            return Ok()

        sleeps = []
        with mock.patch.object(collector.urllib.request, "urlopen", urlopen), mock.patch.object(collector.time, "sleep", sleeps.append):
            self.assertEqual(collector._http_fetch("t")("https://api.github.com/x"), {"ok": True})
        self.assertEqual((len(calls), sleeps), (2, [4]))

    def test_a_long_rate_limit_or_an_auth_error_is_not_retried(self):
        from unittest import mock

        def urlopen(request, timeout=0):
            raise self._response(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(collector.time.time()) + 3600)})

        with mock.patch.object(collector.urllib.request, "urlopen", urlopen), mock.patch.object(collector.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.HTTPError):
                collector._http_fetch("t")("https://api.github.com/x")

        def unauthorized(request, timeout=0):
            raise self._response(401)

        with mock.patch.object(collector.urllib.request, "urlopen", unauthorized):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                collector._http_fetch("t")("https://api.github.com/x")
        self.assertEqual(caught.exception.code, 401)

    def test_the_cli_explains_a_failure_in_plain_words(self):
        from unittest import mock

        def boom(*a, **k):
            raise self._response(404)

        err = io.StringIO()
        with mock.patch.object(collector, "collect_bundle", boom), mock.patch.object(collector.sys, "stderr", err):
            code = collector.main(["--repo", "o/r", "--workflow", "ci.yml"])
        self.assertEqual(code, 1)
        self.assertIn("not found", err.getvalue())


class GithubSemantics(unittest.TestCase):
    def test_rejects_other_documents(self):
        with self.assertRaises(RawHistoryError):
            normalize({"runs": []})
        with self.assertRaises(RawHistoryError):  # the old v1 format is not accepted silently
            normalize({"schema": "owner-c.github-actions-history.v1", "runs": []})

    def test_an_approval_gate_is_not_a_recheck(self):
        """psf/black: attempt 1 `action_required` (first-time contributor), attempt 2 failure, attempt 3 success."""
        run = _run([_attempt(1, "action_required", []), _attempt(2, "failure", [_reran("t", "failure")]),
                    _attempt(3, "success", [_reran("t", "success")])])
        data = normalize(_doc([run]))
        self.assertEqual(data["max_rechecks_per_sha"], 1)  # only attempt 3 follows a failure
        self.assertEqual(data["fail_then_pass_runs"], 1)

    def test_a_conclusion_the_docs_do_not_list_is_never_counted_as_a_failure(self):
        """`startup_failure` is seen in practice but is not in GitHub's documented values: unknown stays unknown."""
        run = _run([_attempt(1, "startup_failure", []), _attempt(2, "success", [_reran("t", "success")])])
        data = normalize(_doc([run]))
        self.assertEqual((data["max_rechecks_per_sha"], data["fail_then_pass_runs"], data["repeat_failed_reruns"]), (0, 0, 0))

    def test_a_cancelled_attempt_is_not_a_failure(self):
        run = _run([_attempt(1, "cancelled", [_job("t", "cancelled")]), _attempt(2, "success", [_reran("t", "success")])])
        data = normalize(_doc([run]))
        self.assertEqual((data["max_rechecks_per_sha"], data["fail_then_pass_runs"], data["repeat_failed_reruns"]), (0, 0, 0))

    def test_rechecks_are_summed_per_commit_over_pull_request_runs_only(self):
        twice = _run([_attempt(1, "failure", [_job("t", "failure")]), _attempt(2, "failure", [_reran("t", "failure")]),
                      _attempt(3, "success", [_reran("t", "success")])])
        push = dict(twice, event="push", id=2, head_sha="b" * 40)
        data = normalize(_doc([twice, push]))
        self.assertEqual(data["max_rechecks_per_sha"], 2)

    def test_passing_jobs_executed_again_after_a_failure_are_counted(self):
        """Re-run ALL jobs after one failure re-executes `b`, which had passed; failed-jobs-only would not."""
        full = _run([_attempt(1, "failure", [_job("a", "failure"), _job("b", "success")]),
                     _attempt(2, "success", [_reran("a", "success"), _reran("b", "success")])])
        failed_only = _run([_attempt(1, "failure", [_job("a", "failure"), _job("b", "success")]),
                            _attempt(2, "success", [_reran("a", "success"), _carried("b", "success")])],
                           rid=2, sha="b" * 40)
        self.assertEqual(normalize(_doc([full]))["reran_passing_jobs"], 1)
        self.assertEqual(normalize(_doc([failed_only]))["reran_passing_jobs"], 0)
        gate = _run([_attempt(1, "action_required", [_job("b", "success")]), _attempt(2, "success", [_reran("b", "success")])])
        self.assertEqual(normalize(_doc([gate]))["reran_passing_jobs"], 0)  # only after a FAILED attempt
        dispatched = dict(full, event="workflow_dispatch", rid=3)
        self.assertEqual(normalize(_doc([dispatched]))["reran_passing_jobs"], 0)  # pull requests only, like CI-01

    def test_a_failure_carried_over_without_a_re_run_is_not_repeated(self):
        """Single-job re-run: `b` failed in attempt 1 and is only carried over into attempt 2 (not re-executed)."""
        run = _run([_attempt(1, "failure", [_job("a", "failure"), _job("b", "failure")]),
                    _attempt(2, "failure", [_reran("a", "success"), _carried("b", "failure")])])
        data = normalize(_doc([run]))
        self.assertEqual(data["repeat_failed_reruns"], 0)
        self.assertEqual(data["fail_then_pass:a"], 1)
        self.assertNotIn("fail_then_pass:b", data)

    def test_a_job_that_is_re_executed_and_fails_again_is_a_repeated_failure(self):
        run = _run([_attempt(1, "failure", [_job("a", "failure")]), _attempt(2, "failure", [_reran("a", "failure")])])
        self.assertEqual(normalize(_doc([run]))["repeat_failed_reruns"], 1)

    def test_fail_then_pass_needs_the_pass_to_be_re_executed(self):
        carried_success = _run([_attempt(1, "failure", [_job("a", "failure")]), _attempt(2, "failure", [_carried("a", "success")])])
        self.assertNotIn("fail_then_pass:a", normalize(_doc([carried_success])))

    def test_conclusion_falls_back_to_the_jobs_only_when_missing(self):
        run = _run([{"run_attempt": 1, "jobs": [_job("a", "failure")]}, {"run_attempt": 2, "jobs": [_reran("a", "success")]}])
        data = normalize(_doc([run]))
        self.assertEqual((data["max_rechecks_per_sha"], data["fail_then_pass_runs"]), (1, 1))
        empty = _run([{"run_attempt": 1, "jobs": []}, {"run_attempt": 2, "jobs": [_reran("a", "success")]}])
        self.assertEqual(normalize(_doc([empty]))["max_rechecks_per_sha"], 0)  # unknown is never a failure

    def test_scheduled_runs_same_commit_are_counted_in_time_order(self):
        def sched(i, sha, created):
            return {"id": i, "run_attempt": 1, "event": "schedule", "head_sha": sha, "created_at": created}
        data = normalize(_doc([sched(3, "b", "2026-01-03"), sched(1, "a", "2026-01-01"), sched(2, "a", "2026-01-02")]))
        self.assertEqual((data["scheduled_runs_total"], data["scheduled_runs_same_sha"]), (3, 1))

    def test_chain_needs_the_pair_in_at_least_two_runs_and_collapses_legs(self):
        def run(i):
            return _run([_attempt(1, "success", [
                _job("unit (3.11)", "success", "2026-01-01T00:00:00Z", "2026-01-01T00:02:00Z"),
                _job("unit (3.12)", "success", "2026-01-01T00:00:00Z", "2026-01-01T00:03:00Z"),
                _job("integration", "success", "2026-01-01T00:03:10Z", "2026-01-01T00:06:10Z")])], event="push", rid=i)
        self.assertFalse([k for k in normalize(_doc([run(1)])) if k.startswith("chain_seconds:")])
        self.assertEqual(normalize(_doc([run(1), run(2)]))["chain_seconds:unit->integration"], 360.0)

    def test_chain_ignores_jobs_carried_over_from_an_earlier_attempt(self):
        def run(i):
            return _run([_attempt(1, "failure", [_job("a", "failure"), _job("b", "success")]),
                         _attempt(2, "success", [_carried("a", "success"), _carried("b", "success")])], event="push", rid=i)
        self.assertFalse([k for k in normalize(_doc([run(1), run(2)])) if k.startswith("chain_seconds:")])


if __name__ == "__main__":
    unittest.main()
