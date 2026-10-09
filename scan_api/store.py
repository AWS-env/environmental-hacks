"""Scan state in S3: `scans/<id>/status.json` (always) and `scans/<id>/report.json` (when done).

Only status and reports are stored, never repository source; the bucket expires `scans/` after 7 days.
"""
from __future__ import annotations

import functools
import json
import os
import re
from datetime import datetime, timezone

from scanner.source import GITHUB_URL

STATUSES = ("queued", "running", "done", "error")
SCAN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
GITHUB_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$")  # no leading dot: rules out "." and ".."
MAX_ERROR_CHARS = 500


@functools.cache
def client(name: str):
    """boto3 client, created on first use (boto3 ships with the Lambda runtime; tests patch this)."""
    import boto3

    if name == "s3":
        # Regional endpoint + SigV4: presigned URLs on the global s3.amazonaws.com host get a
        # TemporaryRedirect for new buckets outside us-east-1, which breaks the signature.
        from botocore.config import Config

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        return boto3.client(
            "s3", region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com" if region else None,
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return boto3.client(name)


def parse_repo_url(url) -> tuple[str, str]:
    """(owner, repo) for a public https://github.com/<owner>/<repo> URL, else ValueError."""
    match = GITHUB_URL.match(url.strip()) if isinstance(url, str) else None
    if not match or not all(GITHUB_NAME.match(part) for part in match.groups()):
        raise ValueError("repo_url must be a public https://github.com/<owner>/<repo> URL")
    return match.groups()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def key(scan_id: str, name: str) -> str:
    return f"scans/{scan_id}/{name}"


def _bucket() -> str:
    return os.environ["SCAN_BUCKET"]


def put_json(scan_id: str, name: str, value) -> int:
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    client("s3").put_object(Bucket=_bucket(), Key=key(scan_id, name), Body=body,
                            ContentType="application/json")
    return len(body)


def get_bytes(scan_id: str, name: str) -> bytes | None:
    try:
        response = client("s3").get_object(Bucket=_bucket(), Key=key(scan_id, name))
    except Exception as error:  # botocore ClientError; boto3 is not importable in CI
        if getattr(error, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    return response["Body"].read()


def put_status(scan_id: str, status: str, repo_url: str, created_at: str, **extra) -> dict:
    doc = {"scan_id": scan_id, "status": status, "repo_url": repo_url, "created_at": created_at,
           "updated_at": now(), **extra}
    if doc.get("error"):
        doc["error"] = str(doc["error"])[:MAX_ERROR_CHARS]
    put_json(scan_id, "status.json", doc)
    return doc


def presign(scan_id: str, name: str, seconds: int = 900) -> str:
    return client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": _bucket(), "Key": key(scan_id, name)}, ExpiresIn=seconds)
