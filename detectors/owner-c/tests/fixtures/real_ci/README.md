# Real GitHub Actions run history

Captured 2026-10-10 with the standalone collector (`owner_c/ci/collector.py`, schema v2): read-only REST calls, run and
job metadata only (ids, event, head SHA, conclusions, timestamps, job names). No logs, no secrets, no user names.
Used by `tests/test_ci_real.py` to check the normalizer and the history checks against real GitHub data.

| File | Source | Why it is here |
| --- | --- | --- |
| `lab__lab.yml.json` | private scratch repo `Medhansh-741/owner-c-ci-lab`, workflow `lab.yml` | **Ground truth.** The workflow was built to fail in known ways (a job that fails on attempt 1, a dependent job, a failing matrix leg, an always-failing job) and re-run through the API: re-run failed jobs (twice), a single job, all jobs, cancellation, a clean run, and three pull requests (one recovers after Re-run failed jobs, one keeps failing across two re-runs, one re-run with ALL jobs). Expected values follow from how the scenarios were built. |
| `psf__black__test.yml.json` | psf/black `test.yml` | Real pull requests with approval-gated attempts (`action_required`) and failed-then-passed jobs. |
| `numpy__numpy__linux.yml.json` | numpy/numpy `linux.yml` | Re-runs plus staged jobs that run back to back (matrix legs). |
| `tiangolo__sqlmodel__test.yml.json` | tiangolo/sqlmodel `test.yml` | Scheduled runs plus a build -> combine chain. |
| `fastapi__fastapi__issue-manager.yml.json` | fastapi/fastapi `issue-manager.yml` | Scheduled runs. |
| `microsoft__TypeScript__nightly.yaml.json` | microsoft/TypeScript `nightly.yaml` | Schedule-heavy: 59 of 60 runs scheduled on 21 commits (DT-1). |
| `vitejs__vite__ci.yml.json` | vitejs/vite `ci.yml` | Re-runs of a pull request with a result-gate job that ends `skipped`: found a counting bug (fixed). |
| `pytest-dev__pytest__test.yml.json` | pytest-dev/pytest `test.yml` | Large matrix (32 jobs per attempt) and an attempt for which the collector got no job detail. |

What the lab measured (docs do not state it): every attempt lists ALL jobs, each with a new id and `run_attempt` equal to that
attempt, also after "Re-run failed jobs" and a single-job re-run; a job that was not re-executed keeps the earlier attempt's
timestamps (`started_at < created_at`); `GET .../attempts/{n}` gives each attempt's own conclusion.
