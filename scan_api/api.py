"""HTTP API handler (payload format 2.0). CORS headers are added by API Gateway, not here.

    POST /scans            {"repo_url": "https://github.com/owner/repo"} -> 202 {"scan_id", "status": "queued"}
                           | 200 {"scan_id", "status", "reused": true} (per-repo cooldown) | 429 (daily cap)
    GET  /scans/{scan_id}  -> {"scan_id", "status": queued|running|done|error, "report" | "report_url" | "error"}
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime

from scan_api import guard, store

MAX_BODY_BYTES = 4096
INLINE_REPORT_BYTES = 5_000_000  # Lambda responses are capped at 6 MB; larger reports get a presigned URL
# A worker that times out or runs out of memory cannot record its own failure; the API reports it.
RUNNING_STALE_SECONDS = int(os.environ.get("WORKER_TIMEOUT_SECONDS", "900")) + 60
QUEUED_STALE_SECONDS = int(os.environ.get("WORKER_MAX_EVENT_AGE_SECONDS", "3600")) + 60
# Abuse guard (POST /scans has no auth): reuse a repo's recent scan, and cap new scans per UTC day.
SCAN_COOLDOWN_SECONDS = int(os.environ.get("SCAN_COOLDOWN_SECONDS", "600"))
MAX_SCANS_PER_DAY = int(os.environ.get("MAX_SCANS_PER_DAY", "50"))
DAILY_LIMIT_MESSAGE = "daily scan limit reached; try again after 00:00 UTC"


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def _error(status: int, message: str) -> dict:
    return _response(status, {"error": message})


def _age(timestamp: str) -> float:
    return (datetime.fromisoformat(store.now()) - datetime.fromisoformat(timestamp)).total_seconds()


def _effective_state(status: dict) -> tuple[str, str | None]:
    """(status, error), reporting a dead worker's stale queued/running scan as error."""
    state = status["status"]
    if state == "running" and _age(status["updated_at"]) > RUNNING_STALE_SECONDS:
        return "error", "the scan did not finish in time (worker timed out or ran out of memory)"
    if state == "queued" and _age(status["created_at"]) > QUEUED_STALE_SECONDS:
        return "error", "the scan never started (too many scans queued); try again later"
    return state, status.get("error")


def _reusable_scan(repo_key: str) -> tuple[str, str] | None:
    """(scan_id, status) of the repo's latest scan if it is queued, running or done within the cooldown."""
    scan_id = guard.latest_scan(repo_key)
    raw = store.get_bytes(scan_id, "status.json") if scan_id else None
    if raw is None:
        return None
    status = json.loads(raw)
    state, _ = _effective_state(status)
    if state in ("queued", "running") or (state == "done" and _age(status["updated_at"]) <= SCAN_COOLDOWN_SECONDS):
        return scan_id, state
    return None


def create_scan(event) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        return _error(413, "request body too large")
    try:
        body = json.loads(raw)
        repo_url = body["repo_url"]
        owner, repo = store.parse_repo_url(repo_url)
    except (ValueError, TypeError, KeyError):
        return _error(400, "body must be JSON {\"repo_url\": \"https://github.com/<owner>/<repo>\"}")
    repo_key = guard.repo_key(owner, repo)
    reusable = _reusable_scan(repo_key)
    if reusable:  # never counts against the daily cap
        return _response(200, {"scan_id": reusable[0], "status": reusable[1], "reused": True})
    created_at = store.now()
    day = guard.day_of(created_at)
    if not guard.reserve_daily_slot(day, MAX_SCANS_PER_DAY):
        return _error(429, DAILY_LIMIT_MESSAGE)
    scan_id, repo_url = str(uuid.uuid4()), f"https://github.com/{owner}/{repo}"
    try:
        store.put_status(scan_id, "queued", repo_url, created_at)
        guard.remember_scan(repo_key, scan_id, created_at)
        store.client("lambda").invoke(
            FunctionName=os.environ["WORKER_FUNCTION_NAME"], InvocationType="Event",
            Payload=json.dumps({"scan_id": scan_id, "repo_url": repo_url,
                                "created_at": created_at}).encode("utf-8"))
    except Exception as error:
        print(f"could not start scan {scan_id}: {type(error).__name__}: {error}")
        guard.release_daily_slot(day)  # the scan never started, so it does not count
        try:
            store.put_status(scan_id, "error", repo_url, created_at, error="could not start the scan")
        except Exception as status_error:
            print(f"could not record the failure of {scan_id}: {type(status_error).__name__}: {status_error}")
        return _error(503, "could not start the scan; try again later")
    return _response(202, {"scan_id": scan_id, "status": "queued"})


def get_scan(scan_id: str) -> dict:
    if not store.SCAN_ID.match(scan_id or ""):
        return _error(404, "scan not found")
    raw = store.get_bytes(scan_id, "status.json")
    if raw is None:
        return _error(404, "scan not found")
    status = json.loads(raw)
    out = {k: status.get(k) for k in ("scan_id", "status", "repo_url", "created_at", "updated_at")}
    state, error = _effective_state(status)
    out["status"] = state
    if state == "error":
        out["error"] = error or "scan failed"
    elif state == "done":
        if status.get("report_bytes", 0) > INLINE_REPORT_BYTES:
            out["report"], out["report_url"] = None, store.presign(scan_id, "report.json")
        else:
            report = store.get_bytes(scan_id, "report.json")
            if report is None:
                out["status"], out["error"] = "error", "report missing (expired after the retention period?)"
            else:
                out["report"] = json.loads(report)
    return _response(200, out)


def handler(event, context=None):
    route = event.get("routeKey")
    try:
        if route == "POST /scans":
            return create_scan(event)
        if route == "GET /scans/{scan_id}":
            return get_scan((event.get("pathParameters") or {}).get("scan_id", ""))
    except Exception as error:  # never leak a stack trace to the client
        print(f"{route} failed: {type(error).__name__}: {error}")
        return _error(500, "internal error")
    return _error(404, "not found")
