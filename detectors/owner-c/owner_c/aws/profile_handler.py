"""AWS Lambda `owner-c-profile-parser`: confirm candidates with client-produced profiler artifacts.

Parse-only: reads artifacts a client's CI runner produced (py-spy, memray, V8 CPU/heap profiles), normalizes
them into contract `artifact` sources, evaluates the profile-backed checks and publishes the contract results.
Two ways to call it:

1. Directly:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "source":    {"files": [...]} | {"s3": {"bucket", "key"}},            # repo zip
     "artifacts": {"speedscope": {"s3": {...}} | {"json": {...}},          # also memray_stats, cpuprofile, heapprofile
                   "cpuprofile": {...}},
     "settings": {"min_time_share": 0.05, "min_alloc_bytes": 10485760},   # optional overrides
     "include_tests": false, "dry_run": false}

2. Event-driven (docs/ARCHITECTURE_FLOWS.md upload handshake): an S3 ObjectCreated event for
   `uploads/<quoted repo>/<sha>/manifest.json`, uploaded last. The manifest lists the artifacts uploaded next
   to `repo.zip` under the same prefix (see presign_handler).
"""
from __future__ import annotations

import json
import urllib.parse

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


def _event_from_manifest(s3_event):
    """S3 ObjectCreated for `uploads/<repo>/<sha>/manifest.json` -> a normal parse event (S3 pointers)."""
    record = s3_event["Records"][0]["s3"]
    bucket, key = record["bucket"]["name"], urllib.parse.unquote_plus(record["object"]["key"])
    prefix = key.rsplit("/", 1)[0] + "/"
    manifest = json.loads(common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read(1_000_000))
    parts = prefix.split("/")  # uploads / <quoted repo> / <sha> /
    repository_id = urllib.parse.unquote(parts[1])
    if manifest.get("repository_id", repository_id) != repository_id or manifest.get("commit_sha", parts[2]) != parts[2]:
        raise ValueError("manifest does not match its upload prefix")
    return {
        "repository_id": repository_id, "commit_sha": parts[2], "source": {"s3": {"bucket": bucket, "key": prefix + "repo.zip"}},
        "artifacts": {name: {"s3": {"bucket": bucket, "key": f"{prefix}{name}.json"}} for name in manifest.get("artifacts", [])},
        "settings": manifest.get("settings"), "include_tests": bool(manifest.get("include_tests")),
    }


def lambda_handler(event, context=None):
    if event.get("Records") and event["Records"][0].get("eventSource") == "aws:s3":
        event = _event_from_manifest(event)
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
                                    settings=event.get("settings"), checks=[k for k, m in ARTIFACT_CHECKS.items() if m.PROFILER != "xray"]):
            results.append(evaluate(payload))
    published = common.publish_if_enabled(event, results)
    return common.log_summary("profile-parser", {"scan_id": scan_id, "published": published,
                                          "results": common.summarize(results)})
