"""AWS Lambda `owner-c-profile-parser`: confirm Python candidates with client-produced profiler artifacts.

Parse-only: reads artifacts a client's CI runner produced, normalizes them into contract `artifact`
sources, evaluates PY-01 / PY-05 / PY-11 and publishes the contract results.

Event:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "source":    {"files": [...]} | {"s3": {"bucket", "key"}},            # repo zip
     "artifacts": {"speedscope":   {"s3": {...}} | {"json": {...}},       # py-spy -f speedscope
                   "memray_stats": {"s3": {...}} | {"json": {...}}},      # memray stats --json
     "settings": {"min_time_share": 0.05, "min_alloc_bytes": 10485760},   # optional overrides
     "include_tests": false, "dry_run": false}
"""
from __future__ import annotations

import json

from owner_c.aws import common
from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.connector import build_inputs, select_files
from owner_c.normalize import normalize_all
from owner_c.runner import evaluate

MAX_ARTIFACT_BYTES = 50_000_000


def _read_json(ref):
    if "json" in ref:
        return ref["json"]
    obj = common.client("s3").get_object(Bucket=ref["s3"]["bucket"], Key=ref["s3"]["key"])
    if obj.get("ContentLength", 0) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact is too large")
    return json.loads(obj["Body"].read(MAX_ARTIFACT_BYTES + 1))


def lambda_handler(event, context=None):
    repository_id, commit_sha, scan_id = common.read_identity(event)
    refs = event.get("artifacts") or {}
    if not refs:
        raise ValueError("event needs at least one artifact (speedscope and/or memray_stats)")
    include_tests = bool(event.get("include_tests"))
    files = select_files(common.load_files(event.get("source", {})), include_tests)
    artifacts = normalize_all({name: _read_json(ref) for name, ref in refs.items()}, files)

    results = []
    for batch in common.batches(files):
        for payload in build_inputs(repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id,
                                    files=batch, artifacts=artifacts, include_tests=include_tests,
                                    settings=event.get("settings"), checks=list(ARTIFACT_CHECKS)):
            results.append(evaluate(payload))
    published = common.publish_if_enabled(event, results)
    return {"scan_id": scan_id, "published": published, "results": common.summarize(results)}
