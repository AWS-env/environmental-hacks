"""LLM-17: static provisioning / sizing for the theoretical max for agent workloads.

Detector semantics version 1.0.0. Evaluates normalized utilization telemetry of fixed agent/inference capacity
(EC2 or ECS agent workers, Lambda provisioned concurrency, a self-reported worker-pool metric) from contract v1
input payloads (see docs/DETECTOR_CONTRACT.md). It flags capacity that idles most of the time and is only used
in short bursts: a low median (p50) utilization, a peak that reaches real demand, and a high peak-to-mean ratio.
That capacity is sized for the burst and held all the time; autoscaling or serverless inference would follow
the bursts instead. INF-01 covers the complementary case (a low average with no real peak); the two never flag
the same telemetry. The detector never calls AWS; a connector supplies normalized CloudWatch readings
(owner_d/aws/metrics.py normalize_capacity_metrics).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CHECK_ID = "LLM-17"
DETECTOR_VERSION = "1.0.0"
IDENTITY = "bursty-fixed-capacity"
SUPPORTED_KIND = "telemetry"
SUPPORTED_METRICS = ("cpu_utilization", "provisioned_concurrency_utilization", "capacity_utilization")
WORKLOADS = ("agent", "inference")

SETTING_KEYS = (
    "min_window_days",
    "min_sample_count",
    "median_utilization_threshold",
    "peak_utilization_threshold",
    "min_peak_to_mean_ratio",
)
# Reference values (README "LLM-17"); the registry supplies them as defaults. Settings stay required here.
REFERENCE_SETTINGS = {
    "min_window_days": 7,
    "min_sample_count": 100,
    "median_utilization_threshold": 0.10,
    "peak_utilization_threshold": 0.50,
    "min_peak_to_mean_ratio": 4,
}

REQUIRED_DATA_FIELDS = (
    "resource_id",
    "resource_type",
    "workload",
    "metric",
    "provisioned_capacity",
    "capacity_unit",
    "autoscaling",
    "mean_utilization",
    "median_utilization",
    "peak_utilization",
    "window_days",
    "sample_count",
)

EVIDENCE_FIELDS = (
    "median_utilization",
    "mean_utilization",
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
    "https://github.com/AWS-env/environmental-hacks/issues/210",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/framework/sus_sus_user_a2.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-concurrency.html",
)

RECOMMENDATION = (
    "Size the fixed part of this agent capacity for the sustained load, not the burst, and let the bursts scale: "
    "target-tracking autoscaling on utilization or queue depth (ECS Service Auto Scaling, Application Auto Scaling "
    "for provisioned concurrency), on-demand or serverless inference (Lambda without provisioned concurrency, "
    "Bedrock on-demand, SageMaker Serverless Inference), or a queue that smooths the bursts. Keep a warm floor only "
    "where the agent's latency target needs it."
)

GENERAL_LIMITATION = (
    "Utilization metrics show how the capacity was used in the window, not why it is fixed: a latency target that "
    "needs warm capacity, a committed term that is already paid, or a peak season outside the window can justify "
    "it. Autoscaling is taken from the declared `autoscaling` value; it is not read from AWS. Confirm with the "
    "owning team before resizing."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an LLM-17 contract input."""


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
    for key in ("median_utilization_threshold", "peak_utilization_threshold"):
        if not 0 < settings[key] <= 1:
            return None, f"context.{key} must be within (0, 1]"
    if settings["min_peak_to_mean_ratio"] <= 1:
        return None, "context.min_peak_to_mean_ratio must be greater than 1"
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
    if "metric" in data and data["metric"] not in SUPPORTED_METRICS:
        problems.append(f"unsupported metric {data['metric']!r}; v1 supports {', '.join(SUPPORTED_METRICS)}")
    if "workload" in data and data["workload"] not in WORKLOADS:
        problems.append(f"workload must be one of {', '.join(WORKLOADS)}; LLM-17 only judges agent/inference capacity")
    if "autoscaling" in data and data["autoscaling"] not in (True, False, None):
        problems.append("autoscaling must be true, false or null")
    numeric = {}
    for field in ("provisioned_capacity", "mean_utilization", "median_utilization", "peak_utilization",
                  "window_days", "sample_count"):
        if field not in data:
            continue
        value = data[field]
        if not _is_number(value):
            problems.append(f"{field} must be a number")
        else:
            numeric[field] = value
    if numeric.get("provisioned_capacity", 1) <= 0:
        problems.append("provisioned_capacity must be positive")
    for field in ("mean_utilization", "median_utilization", "peak_utilization"):
        if field in numeric and not 0 <= numeric[field] <= 1:
            problems.append(f"{field} must be within [0, 1]")
    if "window_days" in numeric and numeric["window_days"] <= 0:
        problems.append("window_days must be positive")
    if "sample_count" in numeric and numeric["sample_count"] < 1:
        problems.append("sample_count must be at least 1")
    if "peak_utilization" in numeric:
        for field in ("mean_utilization", "median_utilization"):
            if field in numeric and numeric["peak_utilization"] < numeric[field]:
                problems.append(f"peak_utilization must not be below {field}")
    resource_id = data.get("resource_id")
    if isinstance(resource_id, str) and resource_id.strip() and scope_id != f"resource:{resource_id}":
        problems.append(f"scope id {scope_id!r} does not match resource_id (expected 'resource:{resource_id}')")
    return problems


def _ratio_text(data):
    mean, peak = data["mean_utilization"], data["peak_utilization"]
    if mean == 0:
        return "with a 0% mean"
    return f"{peak / mean:.1f}x the {_pct(mean)} mean"


def _finding(repository_id, scope_id, source, data):
    peak = data["peak_utilization"]
    declared_fixed = data["autoscaling"] is False
    summary = (
        f"{data['workload']} {data['resource_type']} {data['resource_id']} holds "
        f"{_fmt(data['provisioned_capacity'])} {data['capacity_unit']} of fixed capacity "
        f"({'no autoscaling' if declared_fixed else 'autoscaling not declared'}) whose median utilization is "
        f"{_pct(data['median_utilization'])} over {_fmt(data['window_days'])} days, while bursts peak at "
        f"{_pct(peak)} ({_ratio_text(data)}); capacity is sized for bursts it serves only briefly"
    )
    if peak >= 1:
        summary += ", and the bursts saturate it, so it is also short at the peak"
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": "high" if declared_fixed else "medium",
        "recommendation": RECOMMENDATION,
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
        return _Outcome(False, omitted_reason=f"{scope_id}: no telemetry source supplied; LLM-17 requires normalized "
                                              "utilization telemetry")
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
    median, mean, peak = data["median_utilization"], data["mean_utilization"], data["peak_utilization"]
    idle = median < settings["median_utilization_threshold"]
    bursts = peak >= settings["peak_utilization_threshold"]
    spiky = peak >= settings["min_peak_to_mean_ratio"] * mean
    if not (idle and bursts and spiky):
        if idle and not bursts:
            return _Outcome(True, note=(
                f"{scope_id}: median utilization {_pct(median)} is low but the peak {_pct(peak)} stays below the "
                f"{_pct(settings['peak_utilization_threshold'])} burst threshold; there are no bursts to scale for "
                "(sustained over-provisioning is INF-01's pattern), not flagged"))
        if idle and bursts:
            return _Outcome(True, note=(
                f"{scope_id}: median utilization {_pct(median)} is low and the peak reaches {_pct(peak)}, but the peak "
                f"is below {_fmt(settings['min_peak_to_mean_ratio'])}x the {_pct(mean)} mean; the load is busy for "
                "long stretches rather than bursty, not flagged"))
        return _Outcome(True)
    if data["autoscaling"] is True:
        return _Outcome(True, note=(
            f"{scope_id}: utilization is bursty (median {_pct(median)}, peak {_pct(peak)}), but the capacity is "
            "declared autoscaled, so it is not static provisioning; not flagged"))
    return _Outcome(True, finding=_finding(repository_id, scope_id, source, data))


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
