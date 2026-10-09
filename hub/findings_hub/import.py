"""Import a checksummed local Owner B pair without EventBridge or S3 acquisition."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

from shared.contracts.validation import ContractError
from findings_hub.store import canonical, persist
from findings_hub.writer import (
    MAX_ARTIFACT_BYTES, MAX_INLINE_RESULT_BYTES, build_items, verify_pair,
)


def load_transfer(path):
    with Path(path).open("rb") as stream:
        body = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(body) > MAX_ARTIFACT_BYTES:
        raise ContractError("Import envelope is too large")
    envelope = json.loads(body.decode("utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("identity"), dict):
        raise ContractError("Import requires a pair, sha256 and independent identity envelope")
    pair_text = canonical(envelope.get("pair"))
    result = verify_pair(pair_text, envelope.get("sha256"), envelope["identity"])
    if len(canonical(result).encode("utf-8")) > MAX_INLINE_RESULT_BYTES:
        raise ContractError("Imported result is too large for inline readback storage")
    return result, envelope["sha256"]


def import_pair(path, table, *, now=None):
    result, digest = load_transfer(path)
    return store_import(result, digest, table, now=now)


def store_import(result, digest, table, *, now=None):
    received = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    item, findings = build_items(
        result, evidence="verified", source="owner-b.detectors",
        event_id=f"local-pair:{digest}", received_at=received,
        artifact={"kind": "local-pair", "sha256": digest,
                  "hash_kind": "canonical_local_pair_sha256"},
    )
    outcome = persist(table, item, findings)
    return {"outcome": outcome, "repository_id": result["repository_id"],
            "scan_id": result["scan_id"], "check_id": result["check_id"],
            "findings": len(findings), "evidence": "verified",
            "source": "owner-b.detectors", "pair_sha256": digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True, help="Local transfer envelope JSON")
    parser.add_argument("--verify-only", action="store_true", help="Validate without any AWS access")
    parser.add_argument("--profile", help="Your local AWS profile; never another team member's")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-south-1"))
    parser.add_argument("--table", default=os.environ.get("FINDINGS_TABLE", "owner-d-findings"))
    args = parser.parse_args(argv)
    # Validate everything before constructing a client or attempting a write.
    result, digest = load_transfer(args.pair)
    if args.verify_only:
        print(json.dumps({"outcome": "validated", "pair_sha256": digest,
                          "check_id": result["check_id"], "persisted": False}))
        return 0
    if args.region != "ap-south-1":
        parser.error("The hub project's selected Region is ap-south-1; cross-Region imports are prohibited")
    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource("dynamodb").Table(args.table)
    print(json.dumps(store_import(result, digest, table), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
