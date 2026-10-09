"""Contract v1 helpers (docs/DETECTOR_CONTRACT.md): payload builders and the fingerprint.

`fingerprint` is re-implemented here, as owner D's detector does, so detectors need no
`jsonschema` at runtime. tests/test_contract.py asserts it matches the shared implementation.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = "1.0"


def canonical(value) -> str:
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity) -> str:
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


def file_scope(path: str) -> str:
    return f"file:{path}"


def static_source(path: str, content: str) -> dict:
    return {"source_id": f"src:{path}", "scope_id": file_scope(path), "kind": "static",
            "locator": path, "content": content}


def artifact_source(profiler: str, path: str, data: dict, kind: str = "artifact") -> dict:
    """Normalized client-produced data for one file: an `artifact` (profiler output) or `telemetry` (X-Ray)."""
    scope = path if path.startswith("page:") else file_scope(path)  # `page:<url path>` when no repo file matches
    return {"source_id": f"{profiler}:{path}", "scope_id": scope, "kind": kind,
            "locator": f"{profiler}:{path}", "data": data}


def build_input(*, repository_id, commit_sha, scan_id, check_id, detector_version, context, sources) -> dict:
    scope = []
    for source in sources:
        if source["scope_id"] not in scope:
            scope.append(source["scope_id"])
    return {
        "schema_version": SCHEMA_VERSION, "kind": "input", "repository_id": repository_id,
        "scan_id": scan_id, "commit_sha": commit_sha, "check_id": check_id,
        "detector_version": detector_version, "context": context, "scope": scope, "sources": sources,
    }


def build_result(payload: dict, status: str, evaluated: list, limitations: list, findings: list) -> dict:
    result = {key: payload[key] for key in (
        "schema_version", "repository_id", "scan_id", "commit_sha", "check_id",
        "detector_version", "context", "scope")}
    result.update(kind="result", status=status,
                  coverage={"evaluated_scope": evaluated, "limitations": limitations},
                  findings=findings, measurements=[])
    return result
