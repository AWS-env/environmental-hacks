"""HTTP API handler (payload format 2.0). CORS headers are added by API Gateway, not here.

    POST /scans            {"repo_url": "https://github.com/owner/repo"} -> 202 {"scan_id"}
    GET  /scans/{scan_id}  -> {"scan_id", "status": queued|running|done|error, "report" | "report_url" | "error"}
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime

from scan_api import store

MAX_BODY_BYTES = 4096
INLINE_REPORT_BYTES = 5_000_000  # Lambda responses are capped at 6 MB; larger reports get a presigned URL
# A worker that times out or runs out of memory cannot record its own failure; the API reports it.
RUNNING_STALE_SECONDS = int(os.environ.get("WORKER_TIMEOUT_SECONDS", "900")) + 60
QUEUED_STALE_SECONDS = int(os.environ.get("WORKER_MAX_EVENT_AGE_SECONDS", "3600")) + 60


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def _error(status: int, message: str) -> dict:
    return _response(status, {"error": message})


def _age(timestamp: str) -> float:
    return (datetime.fromisoformat(store.now()) - datetime.fromisoformat(timestamp)).total_seconds()


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
    scan_id, repo_url = str(uuid.uuid4()), f"https://github.com/{owner}/{repo}"
    status = store.put_status(scan_id, "queued", repo_url, store.now())
    try:
        store.client("lambda").invoke(
            FunctionName=os.environ["WORKER_FUNCTION_NAME"], InvocationType="Event",
            Payload=json.dumps({"scan_id": scan_id, "repo_url": repo_url,
                                "created_at": status["created_at"]}).encode("utf-8"))
    except Exception as error:
        print(f"worker invoke failed for {scan_id}: {type(error).__name__}: {error}")
        store.put_status(scan_id, "error", repo_url, status["created_at"], error="could not start the scan")
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
    state = status["status"]
    if state == "running" and _age(status["updated_at"]) > RUNNING_STALE_SECONDS:
        state, status["error"] = "error", "the scan did not finish in time (worker timed out or ran out of memory)"
    elif state == "queued" and _age(status["created_at"]) > QUEUED_STALE_SECONDS:
        state, status["error"] = "error", "the scan never started (too many scans queued); try again later"
    out["status"] = state
    if state == "error":
        out["error"] = status.get("error") or "scan failed"
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
