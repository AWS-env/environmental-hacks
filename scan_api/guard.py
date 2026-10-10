"""Abuse guard for POST /scans in one on-demand DynamoDB table (`SCAN_GUARD_TABLE`, key `pk`).

- `repo#<owner>/<repo>` (lowercased): the latest scan started for that repository, for the per-repo cooldown.
- `day#<YYYY-MM-DD>` (UTC): how many scans started that day. `reserve_daily_slot` increments it with a single
  conditional `UpdateItem ADD`, so concurrent requests can never push it past the cap (no read-then-write).

Both item kinds carry `expires_at` for DynamoDB TTL. Correctness never depends on TTL deletion: the cooldown
re-checks the scan's status.json and day items are keyed by date.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from scan_api import store

REPO_TTL_SECONDS = 86_400
DAY_TTL_DAYS = 2


def _table() -> str:
    return os.environ["SCAN_GUARD_TABLE"]


def _code(error) -> str | None:
    return getattr(error, "response", {}).get("Error", {}).get("Code")


def repo_key(owner: str, repo: str) -> str:
    return f"{owner}/{repo}".lower()


def day_of(timestamp: str) -> str:
    """UTC date (YYYY-MM-DD) of a store.now() timestamp."""
    return datetime.fromisoformat(timestamp).date().isoformat()


def _epoch(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp).timestamp())


def latest_scan(key: str) -> str | None:
    """scan_id of the latest scan started for this repo key, if one was recorded."""
    item = store.client("dynamodb").get_item(
        TableName=_table(), Key={"pk": {"S": f"repo#{key}"}}, ConsistentRead=True).get("Item")
    return item["scan_id"]["S"] if item else None


def remember_scan(key: str, scan_id: str, created_at: str) -> None:
    store.client("dynamodb").put_item(TableName=_table(), Item={
        "pk": {"S": f"repo#{key}"}, "scan_id": {"S": scan_id}, "created_at": {"S": created_at},
        "expires_at": {"N": str(_epoch(created_at) + REPO_TTL_SECONDS)}})


def reserve_daily_slot(day: str, cap: int) -> bool:
    """Atomically count one more scan for `day`; False (nothing counted) if `cap` scans already started."""
    expires = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=DAY_TTL_DAYS)
    try:
        store.client("dynamodb").update_item(
            TableName=_table(), Key={"pk": {"S": f"day#{day}"}},
            UpdateExpression="ADD scans :one SET expires_at = :expires",
            ConditionExpression="attribute_not_exists(scans) OR scans < :cap",
            ExpressionAttributeValues={":one": {"N": "1"}, ":cap": {"N": str(cap)},
                                       ":expires": {"N": str(int(expires.timestamp()))}})
    except Exception as error:  # botocore ClientError; boto3 is not importable in CI
        if _code(error) == "ConditionalCheckFailedException":
            return False
        raise
    return True


def release_daily_slot(day: str) -> None:
    """Best-effort undo of reserve_daily_slot when the scan could not be started."""
    try:
        store.client("dynamodb").update_item(
            TableName=_table(), Key={"pk": {"S": f"day#{day}"}},
            UpdateExpression="ADD scans :minus_one", ConditionExpression="scans > :zero",
            ExpressionAttributeValues={":minus_one": {"N": "-1"}, ":zero": {"N": "0"}})
    except Exception as error:
        print(f"could not release the daily scan slot for {day}: {type(error).__name__}: {error}")
