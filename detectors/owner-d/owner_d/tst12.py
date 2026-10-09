"""TST-12: heavy fixtures, sleeps and real network calls in unit tests.

Detector semantics version 1.0.0. Evaluates normalized client CI test artifacts
from contract v1 input payloads. The detector never runs tests or calls network
APIs; a connector supplies already-produced timing and behavior summaries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CHECK_ID = "TST-12"
DETECTOR_VERSION = "1.0.0"
IDENTITY = "heavy-test-work"
SUPPORTED_KIND = "artifact"

SETTING_KEYS = (
    "max_duration_seconds",
    "max_sleep_seconds",
    "max_network_calls",
    "max_fixture_bytes",
    "max_setup_seconds",
)

REQUIRED_DATA_FIELDS = (
    "test_id",
    "framework",
    "duration_seconds",
    "sleep_seconds",
    "network_call_count",
    "fixture_bytes",
    "setup_seconds",
)

NUMERIC_DATA_FIELDS = (
    "duration_seconds",
    "sleep_seconds",
    "network_call_count",
    "fixture_bytes",
    "setup_seconds",
)

IDENTITY_FIELDS = (
    "schema_version",
    "repository_id",
    "scan_id",
    "commit_sha",
    "check_id",
    "detector_version",
    "context",
    "scope",
)

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/267",
    "https://arxiv.org/abs/2310.14548",
)

GENERAL_LIMITATION = (
    "TST-12 uses normalized test-run artifacts; confirm intentionally slow integration, "
    "load or end-to-end tests before changing the suite."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as a TST-12 contract input."""


@dataclass(frozen=True)
class _Outcome:
    evaluated: bool
    finding: dict | None = None
    note: str | None = None
    omitted_reason: str | None = None


def _canonical(value):
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value):
    if isinstance(value, int):
        return str(value)
    return f"{value:g}"


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in SETTING_KEYS:
        value = context[key]
        if not _is_number(value):
            return None, f"context.{key} must be a number"
        if value < 0:
            return None, f"context.{key} must be nonnegative"
        settings[key] = value
    return settings, None


def _artifact_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["artifact data must be an object"]
    problems = []
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    for field in ("test_id", "framework"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            problems.append(f"{field} must be a nonempty string")
    numeric = {}
    for field in NUMERIC_DATA_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not _is_number(value):
            problems.append(f"{field} must be a number")
        else:
            numeric[field] = value
    for field, value in numeric.items():
        if value < 0:
            problems.append(f"{field} must be nonnegative")
    for field in ("network_call_count", "fixture_bytes"):
        if field in numeric and not isinstance(numeric[field], int):
            problems.append(f"{field} must be an integer")
    test_id = data.get("test_id")
    if isinstance(test_id, str) and test_id.strip() and scope_id != f"test:{test_id}":
        problems.append(f"scope id {scope_id!r} does not match test_id (expected 'test:{test_id}')")
    return problems


def _signals(data, settings):
    signals = []
    if data["duration_seconds"] > settings["max_duration_seconds"]:
        signals.append(("duration_seconds", "duration", data["duration_seconds"], settings["max_duration_seconds"]))
    if data["sleep_seconds"] > settings["max_sleep_seconds"]:
        signals.append(("sleep_seconds", "sleep", data["sleep_seconds"], settings["max_sleep_seconds"]))
    if data["network_call_count"] > settings["max_network_calls"]:
        signals.append(("network_call_count", "network calls", data["network_call_count"], settings["max_network_calls"]))
    if data["fixture_bytes"] > settings["max_fixture_bytes"]:
        signals.append(("fixture_bytes", "fixture bytes", data["fixture_bytes"], settings["max_fixture_bytes"]))
    if data["setup_seconds"] > settings["max_setup_seconds"]:
        signals.append(("setup_seconds", "setup", data["setup_seconds"], settings["max_setup_seconds"]))
    return signals


def _finding(repository_id, scope_id, source, data, settings, signals):
    cited_fields = ["duration_seconds"] + [field for field, _, _, _ in signals if field != "duration_seconds"]
    signal_text = ", ".join(
        f"{label} {_fmt(value)} exceeds {_fmt(limit)}" for _, label, value, limit in signals
    )
    confidence = "high" if data["network_call_count"] > settings["max_network_calls"] or data["sleep_seconds"] > settings["max_sleep_seconds"] else "medium"
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": (
            f"{data['framework']} test {data['test_id']} does more work than the unit assertion likely needs: "
            f"{signal_text}"
        ),
        "confidence": confidence,
        "recommendation": (
            "Replace real waits, network calls or oversized fixtures with fakes/mocks and keep heavyweight "
            "coverage in a separate integration or performance suite."
        ),
        "references": list(REFERENCES),
        "evidence": [
            {
                "source_id": source["source_id"],
                "kind": SUPPORTED_KIND,
                "locator": source["locator"],
                "field": field,
                "value": data[field],
            }
            for field in cited_fields
        ],
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    artifacts = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not artifacts:
        return _Outcome(False, omitted_reason=f"{scope_id}: no test artifact supplied; TST-12 requires normalized test-run artifact data")
    if len(artifacts) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple test artifacts supplied; evaluation requires exactly one")
    source = artifacts[0]
    data = source.get("data")
    problems = _artifact_problems(data, scope_id)
    if problems:
        return _Outcome(False, omitted_reason=f"{scope_id}: " + "; ".join(problems))
    signals = _signals(data, settings)
    if signals:
        return _Outcome(True, finding=_finding(repository_id, scope_id, source, data, settings, signals))
    return _Outcome(True)


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == "1.0", "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements {DETECTOR_VERSION}",
    )
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope = payload["scope"]
    sources = payload["sources"]
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    result = {
        "schema_version": payload["schema_version"],
        "kind": "result",
        "repository_id": payload["repository_id"],
        "scan_id": payload["scan_id"],
        "commit_sha": payload["commit_sha"],
        "check_id": payload["check_id"],
        "detector_version": payload["detector_version"],
        "context": payload["context"],
        "scope": payload["scope"],
    }

    settings, reason = _read_settings(payload["context"])
    if settings is None:
        result.update(
            status="unavailable",
            coverage={
                "evaluated_scope": [],
                "limitations": [f"Missing or invalid context settings: {reason}", GENERAL_LIMITATION],
            },
            findings=[],
            measurements=[],
        )
        return result

    evaluated = []
    findings = []
    limitations = []
    for scope_id in scope:
        scope_sources = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        outcome = _evaluate_scope(scope_id, scope_sources, settings, payload["repository_id"])
        if outcome.evaluated:
            evaluated.append(scope_id)
            if outcome.finding:
                findings.append(outcome.finding)
            if outcome.note:
                limitations.append(outcome.note)
        else:
            limitations.append(outcome.omitted_reason)
    limitations.append(GENERAL_LIMITATION)

    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"

    result.update(
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings,
        measurements=[],
    )
    return result
