"""Client-side collector: read GitHub Actions run history for one workflow and print it as JSON.

This runs on the CLIENT'S side (their CI runner or laptop) with their own read-only `actions: read` token. Our
side never runs it and never holds the token: the output JSON is uploaded to S3 by a presigned PUT and parsed by
the history Lambda (docs/ARCHITECTURE_FLOWS.md). It is a single file with no dependencies besides the standard
library, so a client can download just this file:

    GITHUB_TOKEN=... python collector.py --repo owner/name --workflow ci.yml --workflow release.yml > ci_history.json

The output is a bundle (schema `owner-c.github-actions-history-bundle.v1`) with one document per workflow; the client
uploads it as the single artifact `ci_history` through the shared presign -> manifest flow. Each workflow document
(schema `owner-c.github-actions-history.v2`) holds runs with their attempts. Each attempt carries its own
`conclusion` (GET .../runs/{id}/attempts/{n}) and its jobs with `created_at`: GitHub lists EVERY job in every
attempt, and a job that was not re-executed keeps the earlier attempt's timestamps (`started_at < created_at`).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCHEMA = "owner-c.github-actions-history.v2"  # kept equal to normalize.github_actions.SCHEMA by a test
BUNDLE_SCHEMA = "owner-c.github-actions-history-bundle.v1"  # the uploaded artifact `ci_history`; same, tested
API = "https://api.github.com"
PAGE = 100
JOB_PAGE = 30           # job objects carry every step: keep responses small
MAX_JOB_PAGES = 10
TRANSIENT = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError)
RUN_FIELDS = ("id", "run_attempt", "event", "head_sha", "conclusion", "created_at", "run_started_at", "updated_at")
JOB_FIELDS = ("name", "conclusion", "started_at", "completed_at", "created_at")
MAX_RATE_WAIT = 90  # seconds the collector will wait for a rate limit to clear before giving up


def _trim(obj, fields):
    return {f: obj.get(f) for f in fields}


def _jobs(fetch, url):
    jobs = []
    for page in range(1, MAX_JOB_PAGES + 1):
        batch = fetch(f"{url}{'&' if '?' in url else '?'}per_page={JOB_PAGE}&page={page}").get("jobs", [])
        jobs += [_trim(j, JOB_FIELDS) for j in batch]
        if len(batch) < JOB_PAGE:
            break
    return jobs


def _list_runs(fetch, repo, workflow, max_runs):
    """The newest `max_runs` runs of the workflow, following pagination (100 per page)."""
    runs, page = [], 1
    while len(runs) < max_runs:
        batch = fetch(f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?per_page={PAGE}&page={page}").get("workflow_runs", [])
        runs += batch
        if len(batch) < PAGE:
            break
        page += 1
    return runs[:max_runs]


def _attempts(fetch, repo, run, attempt):
    """Every attempt of a run: its conclusion and its jobs (all of them, carried-over ones included)."""
    out = []
    for n in range(1, attempt + 1):
        base = f"{API}/repos/{repo}/actions/runs/{run['id']}/attempts/{n}"
        conclusion = run.get("conclusion") if n == attempt else fetch(base).get("conclusion")
        out.append({"run_attempt": n, "conclusion": conclusion, "jobs": _jobs(fetch, f"{base}/jobs")})
    return out


def collect(fetch, repo: str, workflow: str, max_runs: int = 100, job_detail_runs: int = 30) -> dict:
    """`fetch(url) -> dict` performs one authenticated GET. Attempt and job detail is fetched for runs that were
    re-run and for the newest `job_detail_runs` runs (job timings for CI-18); other runs keep only run fields."""
    runs = []
    for index, run in enumerate(_list_runs(fetch, repo, workflow, max_runs)):
        item = _trim(run, RUN_FIELDS)
        item["attempts"] = []
        attempt = int(run.get("run_attempt") or 1)
        if attempt > 1 or index < job_detail_runs:
            try:
                item["attempts"] = _attempts(fetch, repo, run, attempt)
            except TRANSIENT as error:
                # GitHub fails on some very large runs: keep the run fields, drop its detail, say so.
                item["attempts"] = []
                item["jobs_unavailable"] = getattr(error, "code", type(error).__name__)
        runs.append(item)
    return {"schema": SCHEMA, "repository": repo, "workflow_path": f".github/workflows/{workflow}",
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "runs": runs}


def collect_bundle(fetch, repo: str, workflows: list, max_runs: int = 100, job_detail_runs: int = 30) -> dict:
    """The client uploads ONE artifact, `ci_history`: a bundle with the history of each workflow file."""
    return {"schema": BUNDLE_SCHEMA, "repository": repo,
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "workflows": [collect(fetch, repo, w, max_runs, job_detail_runs) for w in workflows]}


def _rate_limit_wait(error):
    """Seconds until a 403/429 rate limit clears (Retry-After, or X-RateLimit-Reset when the quota is 0), else None."""
    headers = error.headers or {}
    retry = headers.get("Retry-After")
    if retry and str(retry).isdigit():
        return int(retry)
    if headers.get("X-RateLimit-Remaining") == "0" and str(headers.get("X-RateLimit-Reset", "")).isdigit():
        return max(0, int(headers["X-RateLimit-Reset"]) - int(time.time()))
    return None


def _http_fetch(token, attempts=4):
    def fetch(url):
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {})})
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except TRANSIENT as error:
                http = isinstance(error, urllib.error.HTTPError)
                wait = _rate_limit_wait(error) if http and error.code in (403, 429) else None
                if wait is not None and attempt < attempts and wait <= MAX_RATE_WAIT:
                    time.sleep(wait + 1)  # a rate limit clears on its own: wait for it
                    continue
                if attempt == attempts or (http and error.code < 500):
                    raise  # otherwise retry only network errors and 5xx
                time.sleep(2 * attempt)
    return fetch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--workflow", required=True, action="append",
                        help="workflow file name, e.g. ci.yml (repeat for several workflows)")
    parser.add_argument("--max-runs", type=int, default=100)
    parser.add_argument("--job-detail-runs", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        doc = collect_bundle(_http_fetch(os.environ.get("GITHUB_TOKEN")), args.repo, args.workflow,
                             args.max_runs, args.job_detail_runs)
    except TRANSIENT as error:
        code = getattr(error, "code", None)
        hint = {401: "the token is missing or expired", 403: "the token lacks `actions: read` or the API rate limit is exhausted",
                404: "the repository or workflow was not found, or the token cannot see it",
                429: "the API rate limit is exhausted; try again later"}.get(code, "")
        print(f"GitHub API error {code or type(error).__name__} for {args.repo}: {hint}".rstrip(": "), file=sys.stderr)
        return 1
    json.dump(doc, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
