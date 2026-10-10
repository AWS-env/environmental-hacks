#!/usr/bin/env python3
"""Scan a repository through the deployed scan-api from CI (stdlib only).

    SCAN_API_URL=https://<api-id>.execute-api.ap-south-1.amazonaws.com \\
      python3 scripts/ci_scan.py --repo-url https://github.com/<owner>/<repo>

POST /scans (retrying throttling 429s, 5xx and network errors with backoff; a 200 "reused" answer during the
per-repo cooldown is polled like a new scan), poll GET /scans/{id} until done/error or the deadline, write
report.json and a Markdown summary ($GITHUB_STEP_SUMMARY). A scan that fails, times out, cannot be started
or is skipped by the daily scan cap is a ::warning::, not a failure: exit 1 is reserved for misconfiguration
(scan-api rejected the request with another 4xx) and bugs in this script. No SCAN_API_URL means skip with a
::notice::.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_HUB_URL = "https://own2fw0jyj.execute-api.ap-south-1.amazonaws.com"
GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
POST_ATTEMPTS = 5
DAILY_LIMIT = "daily scan limit"  # scan-api's 429 body once MAX_SCANS_PER_DAY scans started (UTC day)
TIMEOUT_SECONDS = 960  # worker timeout (900 s) plus the API's 60 s staleness margin
POLL_SECONDS = 10
HTTP_TIMEOUT = 30
HEADERS = {"accept": "application/json", "user-agent": "environmental-hacks-ci-scan"}


class ScanFailed(Exception):
    """scan-api could not produce a report: reported as a warning, the job still succeeds."""


class ScanSkipped(ScanFailed):
    """scan-api declined to start a scan (daily cap): nothing is broken, the summary says skipped."""


class ConfigError(Exception):
    """The request itself is wrong (bad SCAN_API_URL or repo_url): reported as an error, exit 1."""


def annotate(level: str, message: str) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title=scan-api::{escaped}", flush=True)


def request_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    """(status, JSON body) for any HTTP response; network errors raise URLError/OSError."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {**HEADERS, "content-type": "application/json"} if data else HEADERS
    try:
        with urlopen(Request(url, data=data, method=method, headers=headers), timeout=HTTP_TIMEOUT) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {"error": raw[:200].decode("utf-8", "replace")}
    return status, parsed if isinstance(parsed, dict) else {"value": parsed}


def start_scan(api_url: str, repo_url: str, sleep=time.sleep) -> tuple[str, bool]:
    """(scan_id, reused). 202 starts a new scan; 200 + "reused" returns the repo's scan within the cooldown."""
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            status, body = request_json("POST", f"{api_url}/scans", {"repo_url": repo_url})
        except (URLError, OSError) as error:
            status, body = None, {"error": str(error)}
        message = str(body.get("error") or body.get("message") or "")
        if status in (200, 202) and body.get("scan_id"):
            return body["scan_id"], bool(body.get("reused"))
        if status == 429 and DAILY_LIMIT in message.lower():  # retrying cannot help before 00:00 UTC
            raise ScanSkipped(f"scan skipped: {message}")
        if status is not None and 400 <= status < 500 and status != 429:
            raise ConfigError(f"POST /scans returned {status}: {message} (check SCAN_API_URL and repo_url)")
        print(f"POST /scans attempt {attempt}/{POST_ATTEMPTS}: {status or 'network error'} {message}", flush=True)
        if attempt < POST_ATTEMPTS:
            sleep(2 ** attempt)
    raise ScanFailed(f"could not start a scan after {POST_ATTEMPTS} attempts (last: {status or 'network error'})")


def wait_for_scan(api_url: str, scan_id: str, timeout: float, interval: float,
                  sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline, state = clock() + timeout, None
    while True:
        try:
            status, body = request_json("GET", f"{api_url}/scans/{scan_id}")
        except (URLError, OSError) as error:
            status, body = None, {"error": str(error)}
        if status == 200 and body.get("status") in ("done", "error"):
            return body
        if status == 200:
            if body.get("status") != state:
                state = body.get("status")
                print(f"scan {scan_id}: {state}", flush=True)
        else:  # transient (throttled, 5xx, network): keep polling until the deadline
            print(f"GET /scans/{scan_id}: {status or 'network error'} {body.get('error', '')}", flush=True)
        if clock() + interval > deadline:
            raise ScanFailed(f"scan {scan_id} did not finish within {int(timeout)} s (last status: {state}); "
                             "it may still complete, see the findings hub")
        sleep(interval)


def fetch_report(result: dict) -> dict:
    if result.get("report") is not None:
        return result["report"]
    if not result.get("report_url"):
        raise ScanFailed("scan is done but the response has neither report nor report_url")
    with urlopen(Request(result["report_url"], headers={"user-agent": HEADERS["user-agent"]}),
                 timeout=120) as response:  # presigned S3 URL: no extra signed headers
        return json.loads(response.read())


def cell(value) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_summary(repo_url: str, hub_link: str | None, scan_id: str | None, outcome: str,
                   report: dict | None = None, message: str | None = None, reused: bool = False,
                   expected_commit: str | None = None) -> str:
    lines = [f"## scan-api: {repo_url}", "", f"**Status:** {outcome}"]
    if scan_id:
        lines.append(f"**Scan ID:** `{scan_id}`" + (" (reused: scan-api returned this repository's recent scan "
                                                    "instead of starting a new one)" if reused else ""))
    commit = ((report or {}).get("repository") or {}).get("commit_sha")
    if commit and expected_commit and commit != expected_commit:
        lines.append(f"**Note:** the report is for commit `{commit}`, not the pushed commit `{expected_commit}`.")
    if message:
        lines.append(f"**Detail:** {cell(message)}")
    if hub_link and scan_id:
        lines.append(f"**Findings hub:** [{hub_link}]({hub_link})")
    if report:
        summary = report.get("summary") or {}
        by_status = ", ".join(f"{k} {v}" for k, v in (summary.get("checks_by_status") or {}).items() if v)
        by_conf = ", ".join(f"{k} {v}" for k, v in (summary.get("findings_by_confidence") or {}).items())
        lines += ["", f"- Commit: `{commit}`" if commit else "- Commit: unknown",
                  f"- Checks: {summary.get('checks_total', 0)} ({by_status or 'none'})",
                  f"- Findings: {summary.get('findings_total', 0)} ({by_conf or 'none'})"]
        down = summary.get("adapters_unavailable_or_failed") or []
        if down:
            lines.append(f"- Owners unavailable or failed: {', '.join(map(str, down))}")
        lines += ["", "| Owner | Check | Status | Findings |", "| --- | --- | --- | ---: |"]
        lines += [f"| {cell(c.get('owner'))} | {cell(c.get('check_id'))} | {cell(c.get('status'))} | "
                  f"{cell(c.get('finding_count'))} |" for c in report.get("checks") or []]
    return "\n".join(lines) + "\n"


def run(api_url: str, repo_url: str, hub_url: str, report_path: Path, summary_path: Path | None,
        timeout: float = TIMEOUT_SECONDS, interval: float = POLL_SECONDS, expected_commit: str | None = None,
        sleep=time.sleep, clock=time.monotonic) -> int:
    def write_summary(text: str) -> None:
        print(text, flush=True)
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(text)

    if not api_url:
        annotate("notice", "SCAN_API_URL is not set; skipping the scan "
                           "(gh variable set SCAN_API_URL --body <ScanApiUrl stack output>)")
        return 0
    match = GITHUB_URL.match(repo_url.strip())
    if not match:
        raise ConfigError(f"repo_url must be https://github.com/<owner>/<repo>, got {repo_url!r}")
    owner, repo = match.groups()
    repo_url, api_url = f"https://github.com/{owner}/{repo}", api_url.rstrip("/")
    scan_id = hub_link = None
    reused = False
    try:
        scan_id, reused = start_scan(api_url, repo_url, sleep=sleep)
        hub_link = f"{hub_url.rstrip('/')}/repos/{owner}/{repo}/scans/{scan_id}"
        print(f"scan {scan_id} {'reused' if reused else 'queued'} for {repo_url}", flush=True)
        result = wait_for_scan(api_url, scan_id, timeout, interval, sleep=sleep, clock=clock)
        if result["status"] == "error":
            raise ScanFailed(f"scan {scan_id} failed: {result.get('error') or 'unknown error'}")
        report = fetch_report(result)
    except ScanFailed as failure:
        annotate("warning", str(failure))
        outcome = "skipped" if isinstance(failure, ScanSkipped) else "error"
        write_summary(render_summary(repo_url, hub_link, scan_id, outcome, message=str(failure), reused=reused))
        return 0
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_summary(render_summary(repo_url, hub_link, scan_id, "done", report, reused=reused,
                                 expected_commit=expected_commit))
    return 0


def main(argv=None) -> int:
    this_repo = os.environ.get("GITHUB_REPOSITORY", "AWS-env/environmental-hacks")
    default_repo = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{this_repo}"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default=os.environ.get("SCAN_API_URL", ""))
    parser.add_argument("--repo-url", default=os.environ.get("REPO_URL") or default_repo)
    parser.add_argument("--hub-url", default=os.environ.get("FINDINGS_HUB_URL") or DEFAULT_HUB_URL)
    parser.add_argument("--report", type=Path, default=Path("report.json"))
    parser.add_argument("--summary", type=Path, default=os.environ.get("GITHUB_STEP_SUMMARY") or None)
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="seconds to poll")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS, help="seconds between polls")
    args = parser.parse_args(argv)
    # GITHUB_SHA is only this run's commit when the scanned repository is the one running the workflow.
    match = GITHUB_URL.match(args.repo_url.strip())
    same_repo = bool(match) and "/".join(match.groups()).lower() == this_repo.lower()
    try:
        return run(args.api_url.strip(), args.repo_url, args.hub_url, args.report, args.summary,
                   timeout=args.timeout, interval=args.interval,
                   expected_commit=os.environ.get("GITHUB_SHA") if same_repo else None)
    except ConfigError as error:
        annotate("error", str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
