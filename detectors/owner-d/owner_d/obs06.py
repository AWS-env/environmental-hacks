"""OBS-06: high-cardinality metric labels (CloudWatch custom metric dimensions).

Detector semantics version 1.0.0. Evaluates normalized CloudWatch ListMetrics
telemetry from contract v1 input payloads (see docs/DETECTOR_CONTRACT.md). The
detector never calls AWS APIs: a connector lists the metrics and passes the raw
pages to ``normalize_list_metrics``, which builds one telemetry ``data`` object
per (namespace, metric name).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

CHECK_ID = "OBS-06"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"

# ListMetrics "doesn't return information about metrics if those metrics haven't
# reported data in the past two weeks" (API reference), so every count is over
# that window.
LIST_METRICS_WINDOW_DAYS = 14
SAMPLE_LIMIT = 5
AWS_NAMESPACE_PREFIX = "AWS/"
SCOPE_PREFIX = "resource:metric/"

SETTING_KEYS = ("max_dimension_values", "min_identifier_values")

REQUIRED_DATA_FIELDS = (
    "namespace",
    "metric_name",
    "series_count",
    "dimension_value_counts",
    "dimension_value_samples",
    "window_days",
    "listing_complete",
)

EVIDENCE_FIELDS = ("series_count", "dimension_value_counts", "dimension_value_samples", "window_days")

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
    "https://github.com/AWS-env/environmental-hacks/issues/230",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html#dimension-combinations",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html",
    "https://prometheus.io/docs/practices/naming/#labels",
    "https://prometheus.io/docs/practices/instrumentation/#do-not-overuse-labels",
)

GENERAL_LIMITATION = (
    f"Counts come from CloudWatch ListMetrics, which only lists metrics with datapoints in the past "
    f"{LIST_METRICS_WINDOW_DAYS} days (new metrics can take up to 15 minutes to appear); they show how many "
    "dimension values exist, not billed metric-hours or that the values keep growing."
)

# Per-request / per-user identifier key names, compared after lowercasing and
# dropping separators (RequestId, request_id and request-id all match).
_ID_KEY = re.compile(
    r"^(?:aws)?(?:request|trace|span|correlation|session|user|customer|visitor|client|device|order|"
    r"transaction|message|invocation|execution|event|job|cart)(?:id|uuid|guid|token|key)$"
    r"|^(?:uuid|guid|email|emailaddress|ip|ipaddress|clientip|sourceip|remoteip|remoteaddr|"
    r"url|uri|fullurl|rawurl|querystring)$"
)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ID_VALUE = re.compile(
    rf"^(?:{_UUID}"
    r"|[0-9a-f]{16,}"  # hex tokens, hashes
    r"|\d{8,}"  # long numeric IDs / epoch timestamps
    r"|[0-9a-hjkmnp-tv-z]{26}"  # ULID
    r"|1-[0-9a-f]{8}-[0-9a-f]{24}"  # X-Ray trace ID
    r"|[^@\s/]+@[^@\s/]+\.[a-z]{2,}"  # email address
    r"|\d{1,3}(?:\.\d{1,3}){3}"  # IPv4 address
    r")$",
    re.IGNORECASE,
)
# A URL or path carrying an ID segment (/orders/12345, /cart/<uuid>).
_ID_PATH = re.compile(rf"/(?:\d{{3,}}|[0-9a-f]{{16,}}|{_UUID})(?:[/?#]|$)", re.IGNORECASE)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an OBS-06 contract input."""


@dataclass(frozen=True)
class _Outcome:
    evaluated: bool
    findings: tuple = ()
    excepted: bool = False
    omitted_reason: str | None = None


def scope_id_for(namespace, metric_name):
    """Scope ID used for one CloudWatch metric (namespace + metric name)."""
    return f"{SCOPE_PREFIX}{namespace}/{metric_name}"


def normalize_list_metrics(pages: list[dict]) -> dict:
    """Normalize CloudWatch ListMetrics response pages into OBS-06 telemetry data.

    ``pages`` are the raw responses (``{"Metrics": [...], "NextToken": ...}``) in
    request order, e.g. from ``boto3.client("cloudwatch").get_paginator("list_metrics")``.
    Returns ``{"window_days", "listing_complete", "page_count", "metrics"}`` where
    ``metrics`` maps each scope ID (``scope_id_for``) to the telemetry ``data`` for
    that metric. Output is bounded: at most SAMPLE_LIMIT sample values per key, and
    CloudWatch allows at most 30 dimensions per metric. ``listing_complete`` is
    false when the last page still carries a ``NextToken``. Raises ValueError on
    pages that are not ListMetrics responses.
    """
    if not isinstance(pages, list) or not pages:
        raise ValueError("pages must be a nonempty list of ListMetrics responses")
    series = {}
    values = {}
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, dict) or not isinstance(page.get("Metrics"), list):
            raise ValueError(f"page {page_number} is not a ListMetrics response (missing 'Metrics' list)")
        for index, metric in enumerate(page["Metrics"]):
            where = f"page {page_number} metric {index}"
            if not isinstance(metric, dict):
                raise ValueError(f"{where} must be an object")
            namespace = metric.get("Namespace")
            name = metric.get("MetricName")
            if not isinstance(namespace, str) or not namespace or not isinstance(name, str) or not name:
                raise ValueError(f"{where} needs nonempty Namespace and MetricName strings")
            dimensions = metric.get("Dimensions", [])
            if not isinstance(dimensions, list):
                raise ValueError(f"{where} Dimensions must be a list")
            pairs = []
            for dimension in dimensions:
                if (
                    not isinstance(dimension, dict)
                    or not isinstance(dimension.get("Name"), str)
                    or not isinstance(dimension.get("Value"), str)
                ):
                    raise ValueError(f"{where} has a dimension without string Name and Value")
                pairs.append((dimension["Name"], dimension["Value"]))
            key = (namespace, name)
            series.setdefault(key, set()).add(frozenset(pairs))
            per_key = values.setdefault(key, {})
            for dimension_name, dimension_value in pairs:
                per_key.setdefault(dimension_name, set()).add(dimension_value)

    last = pages[-1]
    listing_complete = not last.get("NextToken")
    metrics = {}
    for key in sorted(series):
        namespace, name = key
        per_key = values[key]
        metrics[scope_id_for(namespace, name)] = {
            "namespace": namespace,
            "metric_name": name,
            "series_count": len(series[key]),
            "dimension_value_counts": {k: len(per_key[k]) for k in sorted(per_key)},
            "dimension_value_samples": {k: sorted(per_key[k])[:SAMPLE_LIMIT] for k in sorted(per_key)},
            "window_days": LIST_METRICS_WINDOW_DAYS,
            "listing_complete": listing_complete,
        }
    return {
        "window_days": LIST_METRICS_WINDOW_DAYS,
        "listing_complete": listing_complete,
        "page_count": len(pages),
        "metrics": metrics,
    }


def _canonical(value):
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Observed counts, samples and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in SETTING_KEYS:
        value = context[key]
        if not _is_int(value) or value < 1:
            return None, f"context.{key} must be a positive integer"
        settings[key] = value
    return settings, None


def _id_like_key(key):
    return bool(_ID_KEY.match(re.sub(r"[^a-z0-9]", "", key.lower())))


def _id_like_value(value):
    return bool(_ID_VALUE.match(value) or _ID_PATH.search(value))


def _telemetry_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    for field in ("namespace", "metric_name"):
        if not isinstance(data[field], str) or not data[field]:
            problems.append(f"{field} must be a nonempty string")
    series_count = data["series_count"]
    if not _is_int(series_count) or series_count < 1:
        problems.append("series_count must be a positive integer")
        series_count = None
    window = data["window_days"]
    if not (isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0):
        problems.append("window_days must be a positive number")
    if not isinstance(data["listing_complete"], bool):
        problems.append("listing_complete must be a boolean")
    counts = data["dimension_value_counts"]
    samples = data["dimension_value_samples"]
    if not isinstance(counts, dict) or not isinstance(samples, dict):
        problems.append("dimension_value_counts and dimension_value_samples must be objects")
    else:
        if set(counts) != set(samples):
            problems.append("dimension_value_counts and dimension_value_samples must have the same keys")
        for key, count in counts.items():
            if not _is_int(count) or count < 1:
                problems.append(f"dimension_value_counts[{key!r}] must be a positive integer")
            elif series_count is not None and count > series_count:
                problems.append(f"dimension_value_counts[{key!r}] = {count} exceeds series_count {series_count}")
        for key, sample in samples.items():
            if (
                not isinstance(sample, list)
                or not sample
                or not all(isinstance(value, str) for value in sample)
            ):
                problems.append(f"dimension_value_samples[{key!r}] must be a nonempty list of strings")
            elif _is_int(counts.get(key)) and len(sample) > counts[key]:
                problems.append(f"dimension_value_samples[{key!r}] has more values than its count")
    if not problems:
        expected = scope_id_for(data["namespace"], data["metric_name"])
        if scope_id != expected:
            problems.append(f"scope id {scope_id!r} does not match namespace/metric_name (expected {expected!r})")
    return problems


def _judge_key(key, count, sample, settings):
    """Return (confidence, reasons) for a flagged dimension key, or None."""
    over_count = count > settings["max_dimension_values"]
    by_name = _id_like_key(key)
    by_shape = all(_id_like_value(value) for value in sample)
    identifier = (by_name or by_shape) and count > settings["min_identifier_values"]
    if not over_count and not identifier:
        return None
    reasons = []
    if over_count:
        reasons.append(f"more than the configured {settings['max_dimension_values']} (context.max_dimension_values)")
    if by_name or by_shape:
        kinds = [label for label, hit in (("key name", by_name), ("sampled values", by_shape)) if hit]
        reasons.append(f"identifier-like by {' and '.join(kinds)}")
    if over_count and (by_name or by_shape):
        confidence = "high"
    elif over_count or by_shape:
        confidence = "medium"
    else:
        confidence = "low"
    return confidence, reasons


def _finding(repository_id, scope_id, source, data, key, confidence, reasons):
    count = data["dimension_value_counts"][key]
    identity = f"dimension:{key}"
    summary = (
        f"Custom metric {data['namespace']} / {data['metric_name']} has {count} distinct values for dimension "
        f"{key!r} ({'; '.join(reasons)}), across {data['series_count']} dimension combinations listed in the "
        f"past {data['window_days']:g} days; CloudWatch stores and bills each combination as a separate metric"
    )
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, identity),
        "scope_id": scope_id,
        "identity": identity,
        "summary": summary,
        "confidence": confidence,
        "recommendation": (
            f"Remove {key!r} from the metric's dimensions or replace it with a bounded value (route template, "
            "status class, tenant tier). Keep per-request identifiers such as request or trace IDs as log "
            "properties (EMF target members outside the DimensionSet) and query them with Logs Insights."
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
        return _Outcome(False, omitted_reason=f"{scope_id}: no telemetry source supplied; OBS-06 requires normalized ListMetrics telemetry")
    if len(telemetry) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one")
    source = telemetry[0]
    data = source.get("data")
    problems = _telemetry_problems(data, scope_id)
    if problems:
        return _Outcome(False, omitted_reason=f"{scope_id}: " + "; ".join(problems))
    if data["namespace"].startswith(AWS_NAMESPACE_PREFIX):
        return _Outcome(True, excepted=True)
    findings = []
    for key in sorted(data["dimension_value_counts"]):
        judged = _judge_key(key, data["dimension_value_counts"][key], data["dimension_value_samples"][key], settings)
        if judged:
            findings.append(_finding(repository_id, scope_id, source, data, key, *judged))
    if not findings and not data["listing_complete"]:
        return _Outcome(
            False,
            omitted_reason=(
                f"{scope_id}: the ListMetrics listing was incomplete (last page had a NextToken), so the counts "
                "are lower bounds and cannot show this metric is clean"
            ),
        )
    return _Outcome(True, findings=tuple(findings))


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
    sources = payload.get("sources")
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
    excepted = 0
    for scope_id in scope:
        scope_sources = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        outcome = _evaluate_scope(scope_id, scope_sources, settings, payload["repository_id"])
        if outcome.evaluated:
            evaluated.append(scope_id)
            findings.extend(outcome.findings)
            excepted += outcome.excepted
        else:
            limitations.append(outcome.omitted_reason)
    if excepted:
        limitations.append(
            f"{excepted} metric(s) in AWS/* namespaces were evaluated as exceptions and not flagged: they are "
            "vended by AWS services with AWS-defined dimensions, and basic monitoring is not billed as custom metrics."
        )
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
