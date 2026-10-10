"""AWS Lambda `owner-d-hub-api` behind `GET /repos/{owner}/{repo}/scans/{scan_id}` (HTTP API, payload 2.0).

Serves a persisted scan from the findings table, for public-repository scans only: results whose source is
`owner-<x>.scan-api` (published by scan-api after `POST /scans`, see scan_api/hub.py), looked up by their
random scan-api UUID. Results from other sources, such as client-CI uploads (`owner-d.artifact-parser`) and
telemetry analyzers, can share a repository but never a scan-api UUID, and are filtered out regardless.
They are never served here.

    200 {"repository_id", "scan_id", "commit_sha", "results": [...], "findings": [...], "truncated": false}
    404 when no public result is stored for that scan (unknown, not yet persisted, or not a scan-api scan)

Reads are one DynamoDB Query on the scan partition (findings_hub.store layout); nothing is written.
"""
from __future__ import annotations

import json
import os
import re

from findings_hub.readback import _plain, _query_all
from findings_hub.store import scan_pk

PUBLIC_SOURCE = re.compile(r"owner-[a-z]\.scan-api")
GITHUB_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}")  # same rule as scan_api.store
SCAN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
MAX_FINDINGS = 2000  # keeps the response far below the 6 MB Lambda limit
RESULT_FIELDS = ("check_id", "status", "commit_sha", "detector_version", "scope_count", "evaluated_count",
                 "finding_count", "limitations", "evidence", "source", "received_at")
FINDING_FIELDS = ("check_id", "fingerprint", "scope_id", "identity", "summary", "confidence", "recommendation",
                  "references")

_table = None  # tests: object with query(**kwargs)


def table():
    global _table
    if _table is None:
        import boto3  # provided by the Lambda runtime

        _table = boto3.resource("dynamodb").Table(os.environ["FINDINGS_TABLE"])
    return _table


def public_scan(repository_id: str, scan_id: str) -> dict | None:
    items = _query_all(table(), KeyConditionExpression="pk = :pk",
                       ExpressionAttributeValues={":pk": scan_pk(repository_id, scan_id)})
    results = [i for i in items if i.get("type") == "result" and PUBLIC_SOURCE.fullmatch(i.get("source", ""))
               and i.get("repository_id") == repository_id and i.get("scan_id") == scan_id]
    if not results:
        return None
    committed = {r["result_sha256"] for r in results}
    findings = [i for i in items if i.get("type") == "finding" and i.get("result_sha256") in committed]
    findings.sort(key=lambda f: (f["check_id"], f["scope_id"], f["fingerprint"]))
    results.sort(key=lambda r: (r["check_id"], r["received_at"]))
    return {
        "repository_id": repository_id,
        "scan_id": scan_id,
        "commit_sha": results[0]["commit_sha"],
        "results": [{k: _plain(r.get(k)) for k in RESULT_FIELDS} for r in results],
        "findings": [{**{k: _plain(f.get(k)) for k in FINDING_FIELDS}, "evidence": json.loads(f["evidence_json"])}
                     for f in findings[:MAX_FINDINGS]],
        "findings_total": len(findings),
        "truncated": len(findings) > MAX_FINDINGS,
    }


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context=None):
    params = event.get("pathParameters") or {}
    owner, repo, scan_id = params.get("owner", ""), params.get("repo", ""), params.get("scan_id", "")
    if not (GITHUB_NAME.fullmatch(owner) and GITHUB_NAME.fullmatch(repo) and SCAN_ID.fullmatch(scan_id)):
        return _response(404, {"error": "scan not found"})
    try:
        out = public_scan(f"github:{owner}/{repo}", scan_id)
    except Exception as error:  # never leak a stack trace to the client
        print(json.dumps({"handler": "owner-d-hub-api", "outcome": "error", "error": type(error).__name__}))
        return _response(500, {"error": "internal error"})
    print(json.dumps({"handler": "owner-d-hub-api", "outcome": "found" if out else "not_found",
                      "results": len(out["results"]) if out else 0}))
    if out is None:
        return _response(404, {"error": "scan not found"})
    return _response(200, out)
