"""AWS Lambda `owner-c-presign`: the upload handshake from docs/ARCHITECTURE_FLOWS.md.

The client's CI asks for short-lived presigned PUT URLs, uploads its repo zip and profiler artifacts straight
to the private artifact bucket, and finally uploads `manifest.json`, which triggers the parser Lambda through
an S3 event. We never receive credentials and never execute what is uploaded.

Event:  {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "artifacts": ["cpuprofile", "heapprofile"]}
Result: {"prefix": "...", "expires_in": 900, "urls": {"repo": {"key", "url"}, "cpuprofile": {...}, "manifest": {...}}}
Upload order: repo, each artifact, then manifest last. The manifest is {"repository_id", "commit_sha",
"artifacts": ["cpuprofile", ...], "settings": {...optional}}.
"""
from __future__ import annotations

import os
import urllib.parse

from owner_c.aws import common
from owner_c.normalize import NORMALIZERS

EXPIRES_IN = 900
UPLOADABLE = sorted(set(NORMALIZERS) - {"xray"})  # X-Ray is read from the client's account, not uploaded


def prefix_for(repository_id: str, commit_sha: str) -> str:
    return f"uploads/{urllib.parse.quote(repository_id, safe='')}/{commit_sha}/"


def lambda_handler(event, context=None):
    repository_id, commit_sha, _scan = common.read_identity(event)
    wanted = event.get("artifacts") or []
    unknown = set(wanted) - set(UPLOADABLE)
    if not wanted or unknown:
        raise ValueError(f"event needs artifacts from {UPLOADABLE}; unknown: {sorted(unknown)}")
    bucket = os.environ["ARTIFACT_BUCKET"]
    prefix = prefix_for(repository_id, commit_sha)
    keys = {"repo": prefix + "repo.zip", **{name: f"{prefix}{name}.json" for name in wanted}, "manifest": prefix + "manifest.json"}
    s3 = common.client("s3", regional_endpoint=True)  # the default client signs bucket.s3.amazonaws.com, which 307-redirects a PUT body
    urls = {name: {"key": key, "url": s3.generate_presigned_url(
        "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=EXPIRES_IN, HttpMethod="PUT")}
        for name, key in keys.items()}
    return {"prefix": prefix, "expires_in": EXPIRES_IN, "urls": urls}
