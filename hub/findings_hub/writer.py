"""findings-hub writer: validate each detector result event and persist it to the findings table.

Accepted EventBridge detail types:
  detector.result.v1        Detail is one contract v1 result (owner C). No input travels with it,
                            so only shape/invariants are validated: evidence = "unverified".
  DetectorResultPointer.v1  Detail points at a content-addressed {input, result} object in an
                            allowlisted bucket (owner B). The pair is checked with validate_pair:
                            evidence = "verified".

Invalid events raise, so EventBridge/Lambda retries and then the DLQ keep them visible; nothing
invalid is stored. Findings are written before the result item, so a result item in the table
means its findings are persisted too.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from shared.contracts.validation import ContractError, validate, validate_pair

from findings_hub.store import canonical, persist, repo_pk, scan_pk, sha256

RESULT_TYPE = "detector.result.v1"
POINTER_TYPE = "DetectorResultPointer.v1"
POINTER_FIELDS = ("repository_id", "scan_id", "check_id", "detector_version", "commit_sha")
POINTER_KEY = re.compile(r"results/(?:[a-z0-9-]+/)?[a-f0-9]{32}/(?P<digest>[a-f0-9]{64})\.json")
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_INLINE_RESULT_BYTES = 300_000  # DynamoDB items cap at 400 KB; larger results stay in the artifact

_clients = {}


def _resource(name):
    if name not in _clients:
        import boto3  # provided by the Lambda runtime

        _clients[name] = boto3.resource(name) if name == "dynamodb" else boto3.client(name)
    return _clients[name]


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def load_pointer(detail: dict, s3, allowed_buckets: set[str]):
    """Fetch and verify an {input, result} pair; returns (result, artifact)."""
    artifact = detail.get("artifact") or {}
    bucket, key, digest = artifact.get("bucket"), artifact.get("key", ""), artifact.get("sha256", "")
    _require(bucket in allowed_buckets, f"Pointer bucket {bucket!r} is not allowlisted")
    match = POINTER_KEY.fullmatch(key)
    _require(match is not None, "Pointer key is not a content-addressed results/ object")
    _require(match["digest"] == digest, "Pointer sha256 does not match its key")
    params = {"Bucket": bucket, "Key": key}
    if artifact.get("version_id"):
        params["VersionId"] = artifact["version_id"]
    obj = s3.get_object(**params)
    _require(obj.get("ContentLength", 0) <= MAX_ARTIFACT_BYTES, "Pointer object is too large")
    body = obj["Body"].read(MAX_ARTIFACT_BYTES + 1)
    _require(len(body) <= MAX_ARTIFACT_BYTES, "Pointer object is too large")
    text = body.decode("utf-8")
    result = verify_pair(text, digest, detail)
    return result, {k: v for k, v in artifact.items() if k in ("bucket", "key", "sha256", "version_id")}


def verify_pair(text: str, digest: str, identity: dict):
    """Shared integrity, contract and identity checks for S3 pointers and local imports."""
    _require(len(text.encode("utf-8")) <= MAX_ARTIFACT_BYTES, "Pair object is too large")
    _require(sha256(text) == digest, "Pair object checksum mismatch")
    pair = json.loads(text)
    _require(isinstance(pair, dict) and {"input", "result"} <= pair.keys(), "Pointer object is not an input/result pair")
    validate_pair(pair["input"], pair["result"])
    result = pair["result"]
    _require(all(identity.get(f) == result[f] for f in POINTER_FIELDS), "Pair identity does not match its result")
    return result


def build_items(result: dict, *, evidence: str, source: str, event_id: str, received_at: str, artifact=None):
    result_text = canonical(result)
    result_sha = sha256(result_text)
    pk = scan_pk(result["repository_id"], result["scan_id"])
    check = result["check_id"]
    findings = [{
        "pk": pk,
        "sk": f"FINDING#{check}#{result_sha}#{f['fingerprint']}",
        "type": "finding",
        "check_id": check,
        "result_sha256": result_sha,
        "fingerprint": f["fingerprint"],
        "scope_id": f["scope_id"],
        "identity": f["identity"],
        "summary": f["summary"],
        "confidence": f["confidence"],
        "recommendation": f["recommendation"],
        "references": f["references"],
        "evidence_json": canonical(f["evidence"]),  # JSON text: telemetry values may be floats
    } for f in result["findings"]]
    item = {
        "pk": pk,
        "sk": f"RESULT#{check}#{result_sha}",
        "gsi1pk": repo_pk(result["repository_id"]),
        "gsi1sk": f"{received_at}#{result['scan_id']}#{check}",
        "type": "result",
        "repository_id": result["repository_id"],
        "scan_id": result["scan_id"],
        "commit_sha": result["commit_sha"],
        "check_id": check,
        "detector_version": result["detector_version"],
        "status": result["status"],
        "scope_count": len(result["scope"]),
        "evaluated_count": len(result["coverage"]["evaluated_scope"]),
        "finding_count": len(findings),
        "measurement_count": len(result["measurements"]),
        "limitations": result["coverage"]["limitations"],
        "evidence": evidence,
        "result_sha256": result_sha,
        "source": source,
        "event_id": event_id,
        "received_at": received_at,
    }
    if len(result_text.encode("utf-8")) <= MAX_INLINE_RESULT_BYTES:
        item["result_json"] = result_text
    if artifact:
        item["artifact"] = artifact
    _require("result_json" in item or artifact, "Result is too large to store without an artifact pointer")
    return item, findings


def ingest(event: dict, *, s3, table, allowed_buckets: set[str], now=None) -> dict:
    detail_type, detail = event.get("detail-type"), event.get("detail")
    _require(isinstance(detail, dict), "Event has no detail object")
    if detail_type == RESULT_TYPE:
        validate(detail)
        _require(detail["kind"] == "result", "detector.result.v1 detail must be a contract result")
        result, artifact, evidence = detail, None, "unverified"
    elif detail_type == POINTER_TYPE:
        result, artifact = load_pointer(detail, s3, allowed_buckets)
        evidence = "verified"
    else:
        raise ContractError(f"Unsupported detail-type {detail_type!r}")
    received_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    item, findings = build_items(result, evidence=evidence, source=event.get("source", ""),
                                 event_id=event.get("id", ""), received_at=received_at, artifact=artifact)
    outcome = persist(table, item, findings)
    return {"outcome": outcome, "pk": item["pk"], "sk": item["sk"], "repository_id": item["repository_id"],
            "scan_id": item["scan_id"], "check_id": item["check_id"], "status": item["status"],
            "findings": len(findings), "evidence": evidence, "source": item["source"], "event_id": item["event_id"]}


def lambda_handler(event, context):
    buckets = {b.strip() for b in os.environ.get("POINTER_BUCKETS", "").split(",") if b.strip()}
    table = _resource("dynamodb").Table(os.environ["FINDINGS_TABLE"])
    try:
        summary = ingest(event, s3=_resource("s3"), table=table, allowed_buckets=buckets)
    except ContractError as exc:
        print(json.dumps({"handler": "findings-writer", "outcome": "rejected", "reason": str(exc),
                          "source": event.get("source"), "detail_type": event.get("detail-type"), "event_id": event.get("id")}))
        raise
    print(json.dumps({"handler": "findings-writer", **summary}))
    return summary
