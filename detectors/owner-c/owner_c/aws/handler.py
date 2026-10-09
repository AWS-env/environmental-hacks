"""AWS Lambda `owner-c-static-scan`: run every static Python check and publish contract results.

Event (read-only; code is parsed, never executed):
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "source": {"files": [{"path": "a.py", "content": "..."}]}      # or {"s3": {"bucket", "key"}} repo zip
     "include_tests": false, "dry_run": false}

One contract v1 result per check (per batch of files) is published to the findings-hub bus
(FINDINGS_BUS_NAME) with detail-type `detector.result.v1`.
"""
from __future__ import annotations

from owner_c.aws import common
from owner_c.checks import STATIC_CHECKS
from owner_c.connector import build_inputs
from owner_c.runner import evaluate


def lambda_handler(event, context=None):
    repository_id, commit_sha, scan_id = common.read_identity(event)
    files = common.load_files(event.get("source", {}))
    results = []
    for batch in common.batches(files):
        for payload in build_inputs(repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id,
                                    files=batch, include_tests=bool(event.get("include_tests")),
                                    checks=list(STATIC_CHECKS)):
            results.append(evaluate(payload))
    published = common.publish_if_enabled(event, results)
    return {"scan_id": scan_id, "published": published, "results": common.summarize(results)}
