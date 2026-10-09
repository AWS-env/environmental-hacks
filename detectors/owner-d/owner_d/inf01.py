"""INF-01: over-provisioning for unforeseen demand spikes.

Detector semantics version 1.0.0. Evaluates normalized compute telemetry from
contract v1 input payloads (see docs/DETECTOR_CONTRACT.md). The detector never
calls AWS APIs; a connector supplies normalized CloudWatch readings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CHECK_ID = "INF-01"
DETECTOR_VERSION = "1.0.0"
IDENTITY = "cpu-capacity-headroom"
SUPPORTED_METRIC = "cpu_utilization"
SUPPORTED_KIND = "telemetry"

SETTING_KEYS = (
    "min_window_days",
    "min_sample_count",
    "average_utilization_threshold",
    "peak_utilization_threshold",
)

REQUIRED_DATA_FIELDS = (
    "resource_id",
    "resource_type",
    "metric",
    "provisioned_capacity",
    "capacity_unit",
    "average_utilization",
    "peak_utilization",
    "window_days",
    "sample_count",
)

EVIDENCE_FIELDS = (
    "average_utilization",
    "peak_utilization",
    "provisioned_capacity",
    "window_days",
    "sample_count",
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
    "https://github.com/AWS-env/environmental-hacks/issues/169",
    "https://docs.aws.amazon.com/wellarchitected/latest/framework/sus_sus_user_a2.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/framework/sus_sus_software_a2.html",
)

GENERAL_LIMITATION = (
    "Utilization thresholds cannot prove that this workload can scale down; "
    "confirm with the owning team before resizing."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an INF-01 contract input."""


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


def _pct(value):
    return f"{value * 100:.1f}%"


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
        settings[key] = value
    if settings["min_window_days"] <= 0:
        return None, "context.min_window_days must be positive"
    if settings["min_sample_count"] < 1:
        return None, "context.min_sample_count must be at least 1"
    for key in ("average_utilization_threshold", "peak_utilization_threshold"):
        if not 0 < settings[key] <= 1:
            return None, f"context.{key} must be within (0, 1]"
    return settings, None


def _telemetry_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    problems = []
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    for field in ("resource_id", "resource_type", "capacity_unit"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            problems.append(f"{field} must be a nonempty string")
    if "metric" in data and data["metric"] != SUPPORTED_METRIC:
        problems.append(f"unsupported metric {data['metric']!r}; v1 supports {SUPPORTED_METRIC!r}")
    numeric = {}
    for field in ("provisioned_capacity", "average_utilization", "peak_utilization", "window_days", "sample_count"):
        if field not in data:
            continue
        value = data[field]
        if not _is_number(value):
            problems.append(f"{field} must be a number")
        else:
            numeric[field] = value
    if numeric.get("provisioned_capacity", 1) <= 0:
        problems.append("provisioned_capacity must be positive")
    for field in ("average_utilization", "peak_utilization"):
        if field in numeric and not 0 <= numeric[field] <= 1:
            problems.append(f"{field} must be within [0, 1]")
    if "window_days" in numeric and numeric["window_days"] <= 0:
        problems.append("window_days must be positive")
    if "sample_count" in numeric and numeric["sample_count"] < 1:
        problems.append("sample_count must be at least 1")
    if "average_utilization" in numeric and "peak_utilization" in numeric:
        if numeric["peak_utilization"] < numeric["average_utilization"]:
            problems.append("peak_utilization must not be below average_utilization")
    resource_id = data.get("resource_id")
    if isinstance(resource_id, str) and resource_id.strip() and scope_id != f"resource:{resource_id}":
        problems.append(f"scope id {scope_id!r} does not match resource_id (expected 'resource:{resource_id}')")
    return problems


def _finding(repository_id, scope_id, source, data, settings):
    peak = data["peak_utilization"]
    confidence = "high" if peak <= settings["peak_utilization_threshold"] / 2 else "medium"
    summary = (
        f"{data['resource_type']} {data['resource_id']} averages {_pct(data['average_utilization'])} CPU "
        f"and peaks at {_pct(peak)} over {_fmt(data['window_days'])} days on "
        f"{_fmt(data['provisioned_capacity'])} {data['capacity_unit']} of provisioned capacity; "
        "capacity is sized for peaks that did not occur"
    )
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": confidence,
        "recommendation": (
            "Confirm the workload can scale down, then right-size the resource or move to "
            "queue-driven autoscaling so capacity follows demand instead of worst-case peaks."
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
            for field in EVIDENCE_FIELDS
        ],
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return _Outcome(False, omitted_reason=f"{scope_id}: no telemetry source supplied; INF-01 requires normalized telemetry")
    if len(telemetry) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one")
    source = telemetry[0]
    data = source.get("data")
    problems = _telemetry_problems(data, scope_id)
    if problems:
        return _Outcome(False, omitted_reason=f"{scope_id}: " + "; ".join(problems))
    if data["window_days"] < settings["min_window_days"]:
        return _Outcome(
            False,
            omitted_reason=(
                f"{scope_id}: telemetry window {_fmt(data['window_days'])} days is below the required "
                f"{_fmt(settings['min_window_days'])} days"
            ),
        )
    if data["sample_count"] < settings["min_sample_count"]:
        return _Outcome(
            False,
            omitted_reason=(
                f"{scope_id}: {_fmt(data['sample_count'])} samples is below the required "
                f"{_fmt(settings['min_sample_count'])}"
            ),
        )
    average = data["average_utilization"]
    peak = data["peak_utilization"]
    if average < settings["average_utilization_threshold"] and peak < settings["peak_utilization_threshold"]:
        return _Outcome(True, finding=_finding(repository_id, scope_id, source, data, settings))
    if average < settings["average_utilization_threshold"]:
        return _Outcome(
            True,
            note=(
                f"{scope_id}: average utilization {_pct(average)} is below the "
                f"{_pct(settings['average_utilization_threshold'])} threshold, but the observed peak "
                f"{_pct(peak)} reaches the {_pct(settings['peak_utilization_threshold'])} peak threshold; "
                "capacity appears driven by real peaks and was not flagged"
            ),
        )
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
