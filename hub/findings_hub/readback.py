"""Read a persisted report back from the findings table (proof of persistence, not of PutEvents).

  python -m findings_hub.readback --repository-id REPO --scan-id SCAN [--check-id ID] [--table T]
  python -m findings_hub.readback --repository-id REPO            # list recent scans
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from findings_hub.store import GSI1, repo_pk, scan_pk


def _query_all(table, **kwargs):
    items = []
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def report(table, repository_id: str, scan_id: str, check_id: str | None = None) -> dict:
    items = _query_all(table, KeyConditionExpression="pk = :pk",
                       ExpressionAttributeValues={":pk": scan_pk(repository_id, scan_id)})
    results = [i for i in items if i["type"] == "result" and check_id in (None, i["check_id"])]
    committed = {i["result_sha256"] for i in results}
    findings = [i for i in items if i["type"] == "finding" and i["result_sha256"] in committed]
    return {
        "repository_id": repository_id,
        "scan_id": scan_id,
        "persisted": bool(results),
        "results": [{k: _plain(i.get(k)) for k in (
            "check_id", "status", "commit_sha", "detector_version", "scope_count", "evaluated_count", "finding_count",
            "evidence", "source", "event_id", "received_at", "result_sha256", "artifact")} for i in results],
        "findings": [{"check_id": f["check_id"], "fingerprint": f["fingerprint"], "scope_id": f["scope_id"],
                      "summary": f["summary"], "confidence": f["confidence"],
                      "evidence": json.loads(f["evidence_json"])} for f in findings],
    }


def recent_scans(table, repository_id: str, limit: int = 20) -> list[dict]:
    page = table.query(IndexName=GSI1, KeyConditionExpression="gsi1pk = :pk",
                       ExpressionAttributeValues={":pk": repo_pk(repository_id)}, ScanIndexForward=False, Limit=limit)
    return [{k: _plain(i.get(k)) for k in ("received_at", "scan_id", "check_id", "status", "finding_count", "evidence")}
            for i in page.get("Items", [])]


def _plain(value):
    """DynamoDB returns Decimal for numbers."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if hasattr(value, "as_integer_ratio") and not isinstance(value, (int, float)):
        return int(value) if value == int(value) else float(value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--scan-id")
    parser.add_argument("--check-id")
    parser.add_argument("--table", default=os.environ.get("FINDINGS_TABLE", "owner-d-findings"))
    args = parser.parse_args(argv)
    import boto3

    table = boto3.resource("dynamodb").Table(args.table)
    if args.scan_id:
        out = report(table, args.repository_id, args.scan_id, args.check_id)
        print(json.dumps(out, indent=2))
        return 0 if out["persisted"] else 1
    print(json.dumps(recent_scans(table, args.repository_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
