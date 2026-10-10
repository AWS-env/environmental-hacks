"""Publish a scan's validated contract results to the Owner D findings hub (EventBridge bus `findings-hub`).

One `detector.result.v1` event per result, source `owner-<owner>.scan-api`. The hub rule accepts sources that
start with `owner-`, and the hub read API serves only `.scan-api` sources (public-repository scans). The writer
(hub/findings_hub/writer.py) validates every event again before storing it.

Publishing is best-effort: the report in S3 is the scan's primary output, so the worker records the outcome
in status.json and never fails a scan because of the hub. An accepted PutEvents does not prove persistence;
the hub read API (or findings_hub.readback) does.
"""
from __future__ import annotations

import json
import os

from scan_api import store

DETAIL_TYPE = "detector.result.v1"
MAX_ENTRY_BYTES = 240_000  # EventBridge caps one entry at 256 KB
MAX_BATCH_BYTES = 240_000  # ... and one PutEvents request at 256 KB
MAX_BATCH_ENTRIES = 10


def bus_name() -> str | None:
    return os.environ.get("FINDINGS_BUS_NAME") or None


def entries(results, bus):
    """(entries, skipped): one entry per result; results too large for one event are skipped."""
    out, skipped = [], []
    for owner, result in results:
        detail = json.dumps(result, ensure_ascii=False, allow_nan=False)
        size = len(detail.encode("utf-8")) + 100  # Source/DetailType/EventBusName count toward the limit
        if size > MAX_ENTRY_BYTES:
            skipped.append(result["check_id"])
            continue
        out.append(({"Source": f"owner-{owner.lower()}.scan-api", "DetailType": DETAIL_TYPE,
                     "EventBusName": bus, "Detail": detail}, size))
    return out, skipped


def batches(sized):
    batch, total = [], 0
    for entry, size in sized:
        if batch and (len(batch) == MAX_BATCH_ENTRIES or total + size > MAX_BATCH_BYTES):
            yield batch
            batch, total = [], 0
        batch.append(entry)
        total += size
    if batch:
        yield batch


def publish(results, bus) -> dict:
    """Summary for status.json: {"bus", "published", "failed", "skipped_too_large"}. Raises on AWS errors."""
    sized, skipped = entries(results, bus)
    events = store.client("events")
    published = failed = 0
    for batch in batches(sized):
        response = events.put_events(Entries=batch)
        failed += response.get("FailedEntryCount", 0)
        published += len(batch) - response.get("FailedEntryCount", 0)
    return {"bus": bus, "published": published, "failed": failed, "skipped_too_large": skipped}
