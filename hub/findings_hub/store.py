"""DynamoDB layout shared by the writer and readback.

One partition per scan, so a single Query returns the whole persisted report:

  pk = SCAN#<sha256([repository_id, scan_id])[:32]>
  sk = RESULT#<check_id>#<result_sha256>              one item per accepted contract result
  sk = FINDING#<check_id>#<result_sha256>#<fingerprint> one item per finding of that result

Items are immutable: a redelivered identical result maps to the same keys, a different result
for the same check gets new keys. Result items carry GSI1 keys so scans can be listed per repository.
"""
from __future__ import annotations

import hashlib
import json

GSI1 = "by-repository"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_pk(repository_id: str, scan_id: str) -> str:
    return "SCAN#" + sha256(canonical([repository_id, scan_id]))[:32]


def repo_pk(repository_id: str) -> str:
    return "REPO#" + repository_id
