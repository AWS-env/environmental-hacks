"""AWS Lambda `owner-c-static-scan`: run every owner C static check (Python, JS/TS, CI workflows) and publish results.

Event (read-only; code and workflow files are parsed, never executed):
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "source": {"files": [{"path": "a.py", "content": "..."}]}      # or {"s3": {"bucket", "key"}} repo zip
     "settings": {"max_retention_days": 30},                         # optional CI threshold overrides
     "include_tests": false, "dry_run": false}

One contract v1 result per check (per batch of files) is published to the findings-hub bus
(FINDINGS_BUS_NAME) with detail-type `detector.result.v1`. The CI checks read `.github/workflows/*.y(a)ml` from the
same file list; their run-history checks arrive through the profile parser (artifact `ci_history`).
"""
from __future__ import annotations

from owner_c.aws import common
from owner_c.checks import STATIC_CHECKS
from owner_c.ci.checks import STATIC_CHECKS as CI_STATIC_CHECKS
from owner_c.ci.connector import build_inputs as ci_build_inputs
from owner_c.ci.runner import evaluate as ci_evaluate
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
        for payload in ci_build_inputs(repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id,
                                       files=batch, settings=event.get("settings"), checks=list(CI_STATIC_CHECKS)):
            results.append(ci_evaluate(payload))
    published = common.publish_if_enabled(event, results)
    return common.log_summary("static-scan", {"scan_id": scan_id, "published": published,
                                          "results": common.summarize(results)})
