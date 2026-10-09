"""Normalize collected GitHub Actions run history for one workflow into contract artifact data.

Input is the collector's JSON (see ci/collector.py): runs with their attempts and jobs, shaped like the REST
objects (https://docs.github.com/en/rest/actions/workflow-runs, .../workflow-jobs). Output is a flat object of
derived counts; each history check cites one top-level field, as the contract requires. Nothing is invented: a
field is only produced from data the collector actually fetched.

Facts about GitHub, measured in a controlled lab (docs/research/category-3-ci.md, tracker CD-4) because the docs
do not state them:
- Every attempt lists ALL jobs of the run, each with a new id and `run_attempt` = that attempt, also after
  "Re-run failed jobs" and single-job re-runs. A job that was NOT re-executed keeps the earlier attempt's
  timestamps: `started_at < created_at`. A re-executed job has `started_at >= created_at`.
- `GET .../attempts/{n}` gives the attempt's own `conclusion`. `action_required` is the first-time-contributor
  approval gate (not a failure, not a recheck); `cancelled` is a cancellation (also not a failure).
- An attempt whose job list is empty ran no jobs (approval gate, or cancelled before start).
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime

PROFILER = "github-actions"
SCHEMA = "owner-c.github-actions-history.v2"
BUNDLE_SCHEMA = "owner-c.github-actions-history-bundle.v1"  # the artifact `ci_history`: one document per workflow
MAX_WORKFLOWS = 200
HANDOFF_SECONDS = 60       # B counts as "right after" A when it starts within this gap of A finishing
MIN_CHAIN_RUNS = 2         # a back-to-back pair must be seen in at least this many runs
MAX_CHAIN_FIELDS = 200
MAX_NAME_CHARS = 500         # job names longer than this are cut before pattern matching
MAX_STAGES_PER_RUN = 300   # more distinct stages than this in one run: skip chain timing for that run

_FAILED = {"failure", "timed_out"}
_PASSED = {"success", "skipped", "neutral"}


class RawHistoryError(ValueError):
    """The collector JSON does not have the expected shape."""


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None


def _executed(job) -> bool:
    """True when the job ran in its attempt (a carried-over job started before it was created in that attempt)."""
    started, created = _time(job.get("started_at")), _time(job.get("created_at"))
    return started is not None and created is not None and started >= created


def _conclusion(attempt):
    """The attempt's own conclusion; derived from its jobs only when the collector did not store one."""
    stored = attempt.get("conclusion")
    if isinstance(stored, str):
        return stored
    jobs = attempt.get("jobs", [])
    if any(j.get("conclusion") in _FAILED for j in jobs):
        return "failure"
    if jobs and all(j.get("conclusion") in _PASSED for j in jobs):
        return "success"
    return None  # unknown: never counted as a failure or a pass


def _failed_jobs(attempt) -> frozenset:
    return frozenset(j["name"] for j in attempt.get("jobs", []) if j.get("conclusion") in _FAILED)


def _failed_again(previous, current) -> bool:
    """A job that failed in `previous` was re-executed in `current` and failed again."""
    again = {j["name"] for j in current.get("jobs", []) if j.get("conclusion") in _FAILED and _executed(j)}
    return bool(_failed_jobs(previous) & again)


def _reran_passing(prev, cur) -> int:
    """Jobs that PASSED in `prev` and were executed again in `cur` (a full re-run instead of failed-jobs-only); a job that
    ended `skipped` did no work (vite's result-gate jobs), so it does not count."""
    passed = {j["name"] for j in prev.get("jobs", []) if j.get("conclusion") == "success"}
    return sum(1 for j in cur.get("jobs", []) if j.get("name") in passed and _executed(j) and j.get("conclusion") != "skipped")


def _job(attempt, name):
    return next((j for j in attempt.get("jobs", []) if j.get("name") == name), None)


def _runs_with_attempts(runs):
    return [r for r in runs if r.get("attempts")]


def stage_name(name: str) -> str:
    """`smoke_test (3.14t)` -> `smoke_test`: matrix legs of one job form one stage. Shared with CI-18, which
    applies the same rule to the workflow's job names so both sides agree."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", name[:MAX_NAME_CHARS])


def _chain_seconds(runs) -> dict:
    """Stages that ran back to back (matrix legs collapsed): median of wall(A) + wall(B) over runs."""
    seen = {}
    for run in runs:
        attempts = run.get("attempts") or []
        if not attempts:
            continue
        stages = {}  # stage -> (first start, last end) over its matrix legs; only jobs executed in that attempt
        for job in attempts[-1].get("jobs", []):
            start, end = _time(job.get("started_at")), _time(job.get("completed_at"))
            if start and end and end >= start and job.get("conclusion") in _PASSED | _FAILED and \
                    (len(attempts) == 1 or _executed(job)):
                lo, hi = stages.get(stage_name(job["name"]), (start, end))
                stages[stage_name(job["name"])] = (min(lo, start), max(hi, end))
        if len(stages) > MAX_STAGES_PER_RUN:
            continue
        for a_name, (a_start, a_end) in stages.items():
            for b_name, (b_start, b_end) in stages.items():
                gap = (b_start - a_end).total_seconds()
                if a_name != b_name and 0 <= gap <= HANDOFF_SECONDS:
                    seen.setdefault((a_name, b_name), []).append(
                        (a_end - a_start).total_seconds() + (b_end - b_start).total_seconds())
    pairs = {k: statistics.median(v) for k, v in seen.items() if len(v) >= MIN_CHAIN_RUNS}
    top = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CHAIN_FIELDS]
    return {f"chain_seconds:{a}->{b}": round(sec, 1) for (a, b), sec in top}


def normalize(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or not isinstance(raw.get("runs"), list):
        raise RawHistoryError("not a owner-c GitHub Actions history document (schema v2)")
    runs = raw["runs"]
    data = {"profiler": PROFILER, "workflow_path": raw.get("workflow_path", ""), "runs_total": len(runs)}
    with_attempts = _runs_with_attempts(runs)
    # Runs that were re-run but whose attempts could not be fetched (very large runs) are left out of the re-run
    # metrics below; this says how many.
    data["runs_without_detail"] = sum(1 for r in runs if int(r.get("run_attempt") or 1) > 1 and not r.get("attempts"))

    # CI-01 (SRC-14): a recheck is a re-run after a FAILED attempt. Approval gates and cancellations are not.
    # Counted per pull request commit (summed over the runs of that commit); the largest count is reported.
    per_sha = {}
    for run in with_attempts:
        if run.get("event") != "pull_request":
            continue
        rechecks = sum(1 for prev, _cur in zip(run["attempts"], run["attempts"][1:]) if _conclusion(prev) in _FAILED)
        per_sha[run["head_sha"]] = per_sha.get(run["head_sha"], 0) + rechecks
    data["max_rechecks_per_sha"] = max(per_sha.values(), default=0)

    # CI-04 (absorbed into CI-01 by the taxonomy): passing jobs executed again after a failed attempt, which
    # "Re-run failed jobs" would have skipped. Pull request runs only, like the rechecks above.
    data["reran_passing_jobs"] = sum(
        _reran_passing(prev, cur) for run in with_attempts if run.get("event") == "pull_request"
        for prev, cur in zip(run["attempts"], run["attempts"][1:]) if _conclusion(prev) in _FAILED)

    # CI-02: a re-run after a failure in which a failed job was re-executed and failed again.
    data["repeat_failed_reruns"] = sum(
        1 for run in with_attempts
        if any(_conclusion(prev) in _FAILED and _failed_again(prev, cur)
               for prev, cur in zip(run["attempts"], run["attempts"][1:])))

    # CI-05: an attempt failed and a later attempt of the same run succeeded (workflow level).
    data["fail_then_pass_runs"] = sum(
        1 for run in with_attempts
        if any(_conclusion(a) in _FAILED and any(_conclusion(b) == "success" for b in run["attempts"][i + 1:])
               for i, a in enumerate(run["attempts"])))

    # CI-03: per job, an attempt failed and a later attempt re-executed the same job and it passed.
    flaky = {}
    for run in with_attempts:
        names = {j["name"] for a in run["attempts"] for j in a.get("jobs", [])}
        for name in names:
            failed_at = next((i for i, a in enumerate(run["attempts"])
                              if (_job(a, name) or {}).get("conclusion") in _FAILED), None)
            if failed_at is not None and any(
                    (_job(a, name) or {}).get("conclusion") == "success" and _executed(_job(a, name))
                    for a in run["attempts"][failed_at + 1:]):
                flaky[name] = flaky.get(name, 0) + 1
    for name in sorted(flaky):
        data[f"fail_then_pass:{name}"] = flaky[name]

    # CI-12: scheduled runs that used the same commit as the previous scheduled run.
    scheduled = sorted((r for r in runs if r.get("event") == "schedule"), key=lambda r: r.get("created_at", ""))
    data["scheduled_runs_total"] = len(scheduled)
    data["scheduled_runs_same_sha"] = sum(
        1 for prev, cur in zip(scheduled, scheduled[1:]) if prev.get("head_sha") == cur.get("head_sha"))

    # CI-18: job pairs that ran back to back, with the median combined duration.
    data.update(_chain_seconds(runs))
    return data
