"""Shared Lambda plumbing: S3/zip loading, event validation and publishing contract results."""
from __future__ import annotations

import io
import json
import os
import re
import uuid
import zipfile

from owner_c.common import MAX_FILE_BYTES
from owner_c.connector import BINARY_SUFFIXES, SKIP_DIRS

SOURCE = "owner-c.detectors"  # Python, JS/TS and CI results; owner D's hub rule matches the `owner-` prefix
DETAIL_TYPE = "detector.result.v1"  # Detail = one contract v1 result payload
MAX_ZIP_FILES = 20_000
MAX_ZIP_BYTES = 200_000_000  # total uncompressed; guards against zip bombs
PUT_EVENTS_BATCH = 10  # EventBridge PutEvents limit per call
MAX_DETAIL_BYTES = 240_000  # EventBridge entry limit is 256 KB
FILES_PER_PAYLOAD = 200

_clients = {}
_verified_buses = set()


def client(name, regional_endpoint=False):
    key = f"{name}:regional" if regional_endpoint else name
    if key not in _clients:
        import boto3  # provided by the Lambda runtime

        if regional_endpoint:
            from botocore.config import Config

            region = os.environ["AWS_REGION"]
            _clients[key] = boto3.client(name, region_name=region, endpoint_url=f"https://{name}.{region}.amazonaws.com",
                                         config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
        else:
            _clients[key] = boto3.client(name)
    return _clients[key]


def iter_zip(data: bytes):
    """Yield (path, text) for scannable text members without extracting to disk."""
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError("archive has too many entries")
        for info in infos:
            parts = info.filename.split("/")
            if info.is_dir() or info.filename.lower().endswith(BINARY_SUFFIXES) or SKIP_DIRS & set(parts):
                continue
            total += info.file_size
            if total > MAX_ZIP_BYTES:
                raise ValueError("archive is too large")
            if info.file_size > MAX_FILE_BYTES:
                continue
            # drop a leading "<repo>-<sha>/" folder that GitHub archives add
            yield "/".join(parts[1:]) if len(parts) > 1 else parts[0], zf.read(info).decode("utf-8", "replace")


def load_files(source: dict):
    if "files" in source:
        return [(f["path"], f["content"]) for f in source["files"]]
    if "s3" in source:
        obj = client("s3").get_object(Bucket=source["s3"]["bucket"], Key=source["s3"]["key"])
        return list(iter_zip(obj["Body"].read()))
    raise ValueError("event needs source.files or source.s3")


def read_identity(event: dict):
    repository_id, commit_sha = event.get("repository_id"), event.get("commit_sha", "")
    if not repository_id:
        raise ValueError("event needs repository_id")
    if not re.fullmatch(r"[a-f0-9]{40}", commit_sha):
        raise ValueError("event needs a full 40-character lowercase commit_sha")
    return repository_id, commit_sha, event.get("scan_id") or str(uuid.uuid4())


def batches(files, size=None):
    size = size or FILES_PER_PAYLOAD
    files = sorted(files)
    for i in range(0, len(files), size):
        yield files[i:i + size]


def ensure_bus(bus_name):
    """PutEvents to a missing bus succeeds silently (events are dropped), so check it exists."""
    if bus_name in _verified_buses:
        return
    try:
        client("events").describe_event_bus(Name=bus_name)
    except Exception as exc:  # botocore ClientError
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ResourceNotFoundException":
            raise RuntimeError(f"event bus '{bus_name}' does not exist; ask owner D to create it") from exc
        raise
    _verified_buses.add(bus_name)


def as_error_result(result: dict, size: int) -> dict:
    """Same identity, context and scope, status `error`, nothing certified: the contract's way to say a check could
    not be evaluated, used when a result is too large for one EventBridge event (so one big result no longer
    drops the whole batch)."""
    keys = ("schema_version", "repository_id", "scan_id", "commit_sha", "check_id", "detector_version", "context", "scope")
    out = {k: result[k] for k in keys}
    out.update(kind="result", status="error", findings=[], measurements=[], coverage={
        "evaluated_scope": [],
        "limitations": [f"result is {size} bytes, over the {MAX_DETAIL_BYTES} byte event limit; not published. "
                        "Split the files or lower FILES_PER_PAYLOAD."]})
    return out


def publish_results(results, bus_name):
    """Send each contract result as one event. Raises if the bus is missing or any entry fails."""
    ensure_bus(bus_name)
    entries = []
    for result in results:
        detail = json.dumps(result)
        size = len(detail.encode("utf-8"))
        if size > MAX_DETAIL_BYTES:
            print(json.dumps({"warning": "result too large for one event; published as error", "check_id": result["check_id"],
                              "bytes": size}))
            detail = json.dumps(as_error_result(result, size))
        entries.append({"Source": SOURCE, "DetailType": DETAIL_TYPE, "EventBusName": bus_name, "Detail": detail})
    events = client("events")
    for i in range(0, len(entries), PUT_EVENTS_BATCH):
        resp = events.put_events(Entries=entries[i:i + PUT_EVENTS_BATCH])
        if resp.get("FailedEntryCount"):
            raise RuntimeError(f"PutEvents failed for {resp['FailedEntryCount']} entries")


def summarize(results):
    return [{"check_id": r["check_id"], "status": r["status"],
             "evaluated": len(r["coverage"]["evaluated_scope"]), "scope": len(r["scope"]),
             "findings": len(r["findings"])} for r in results]


def log_summary(handler: str, summary: dict) -> dict:
    """One JSON line per invocation (CloudWatch Logs); async S3-triggered runs are otherwise invisible."""
    print(json.dumps({"handler": handler, **summary}))
    return summary


def publish_if_enabled(event, results):
    bus = os.environ.get("FINDINGS_BUS_NAME")
    will_publish = bool(bus and results and not event.get("dry_run"))
    if will_publish:
        publish_results(results, bus)
    return will_publish
