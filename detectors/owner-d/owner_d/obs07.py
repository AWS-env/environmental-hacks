"""OBS-07: uniform retention (no tiering) for CloudWatch Logs log groups.

Detector semantics version 1.0.0. Evaluates normalized CloudWatch Logs log-group configuration
from contract v1 input payloads (see docs/DETECTOR_CONTRACT.md). One telemetry source per
`resource:<log-group-name>` scope item. A log group is flagged when it keeps a non-trivial amount
of data in CloudWatch Logs storage forever (no retention policy) or for longer than a configured
hot horizon, and carries no compliance marker.

CloudWatch Logs has no storage tier inside the service: the Standard and Infrequent Access log
classes differ in ingestion price only, so the Infrequent Access class is not an exception.
Tiering means expiring the CloudWatch copy and delivering logs to S3, where Lifecycle rules can
archive or delete them. S3 lifecycle, Firehose, subscription filters and export tasks are out of
scope for v1. The detector never calls AWS APIs; `normalize_describe_log_groups` converts raw
DescribeLogGroups (and optional ListTagsForResource) responses collected by a connector.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

CHECK_ID = "OBS-07"
DETECTOR_VERSION = "1.0.0"
IDENTITY = "hot-log-retention"
SUPPORTED_KIND = "telemetry"
RESOURCE_TYPE = "aws_cloudwatch_log_group"

# PutRetentionPolicy accepts only these values (API_LogGroup.retentionInDays).
VALID_RETENTION_DAYS = frozenset(
    (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653)
)
LOG_GROUP_CLASSES = ("STANDARD", "INFREQUENT_ACCESS", "DELIVERY")
# Delivery-class events stay in CloudWatch Logs only briefly and the retention cannot be changed.
SHORT_LIVED_CLASSES = ("DELIVERY",)

NUMBER_SETTINGS = ("max_hot_retention_days", "min_stored_bytes")
LIST_SETTINGS = ("compliance_tag_keys", "exempt_log_group_prefixes")
SETTING_KEYS = NUMBER_SETTINGS + LIST_SETTINGS

REQUIRED_DATA_FIELDS = (
    "resource_id",
    "resource_type",
    "retention_in_days",
    "stored_bytes",
    "log_group_class",
    "tags",
)

EVIDENCE_FIELDS = ("retention_in_days", "stored_bytes", "log_group_class", "tags")

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
    "https://github.com/AWS-env/environmental-hacks/issues/231",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html#SettingLogRetention",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CloudWatch_Logs_Log_Classes.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Subscriptions.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_data_a4.html",
)

GENERAL_LIMITATION = (
    "OBS-07 v1 audits CloudWatch Logs retention configuration and stored bytes only: query frequency, "
    "S3 lifecycle, Firehose delivery, subscription filters and export tasks are not evaluated, and "
    "compliance mandates that are not tagged cannot be seen; confirm retention requirements before "
    "shortening retention."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an OBS-07 contract input."""


class NormalizationError(ValueError):
    """A DescribeLogGroups response cannot be normalized."""


@dataclass(frozen=True)
class _Outcome:
    evaluated: bool
    finding: dict | None = None
    note: str | None = None
    omitted_reason: str | None = None


# --- normalization -------------------------------------------------------------------------


def _iso_from_epoch_ms(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _log_group_arn(group):
    arn = group.get("logGroupArn")
    if isinstance(arn, str) and arn:
        return arn
    arn = group.get("arn")
    if isinstance(arn, str) and arn:
        return arn[:-2] if arn.endswith(":*") else arn
    return None


def _normalize_tags(raw):
    """Accept a ListTagsForResource response (`{"tags": {...}}`) or a plain tag mapping."""
    if isinstance(raw, dict) and set(raw) == {"tags"} and isinstance(raw["tags"], dict):
        raw = raw["tags"]
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise NormalizationError("tags must map tag keys to string values")
    return dict(raw)


def normalize_describe_log_groups(pages, tags=None):
    """Convert DescribeLogGroups response pages into OBS-07 telemetry data, keyed by log group name.

    `pages` is a list of DescribeLogGroups responses (or one auto-paginated `aws logs
    describe-log-groups` output wrapped in a list). `tags` optionally maps a log group name or
    its `logGroupArn` to a ListTagsForResource response or a plain tag mapping; groups without
    an entry get `tags: None` (not collected), which lowers finding confidence.

    Raw values are passed through when AWS omits or mistypes them so that the detector reports
    the problem instead of the normalizer guessing. `retention_in_days` is `None` when
    `retentionInDays` is absent, which AWS uses for log groups whose events never expire.
    """
    if isinstance(pages, dict):
        raise NormalizationError("pages must be a list of DescribeLogGroups responses, not a single response")
    if not isinstance(pages, list) or not pages:
        raise NormalizationError("pages must be a nonempty list of DescribeLogGroups responses")
    if tags is not None and not isinstance(tags, dict):
        raise NormalizationError("tags must map log group names or ARNs to tags")
    normalized = {}
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("logGroups"), list):
            raise NormalizationError(f"page {index} is not a DescribeLogGroups response with a logGroups list")
        for group in page["logGroups"]:
            if not isinstance(group, dict):
                raise NormalizationError(f"page {index} contains a log group that is not an object")
            name = group.get("logGroupName")
            if not isinstance(name, str) or not name:
                raise NormalizationError(f"page {index} contains a log group without logGroupName")
            if name in normalized:
                raise NormalizationError(f"log group {name!r} appears more than once")
            arn = _log_group_arn(group)
            raw_tags = None
            if tags is not None:
                if name in tags:
                    raw_tags = tags[name]
                elif arn in tags:
                    raw_tags = tags[arn]
            normalized[name] = {
                "resource_id": name,
                "resource_type": RESOURCE_TYPE,
                "log_group_arn": arn,
                "retention_in_days": group.get("retentionInDays"),
                "stored_bytes": group.get("storedBytes"),
                "log_group_class": group.get("logGroupClass"),
                "data_protection_status": group.get("dataProtectionStatus"),
                "creation_time": _iso_from_epoch_ms(group.get("creationTime")),
                "metric_filter_count": group.get("metricFilterCount"),
                "deletion_protection_enabled": group.get("deletionProtectionEnabled"),
                "tags": None if raw_tags is None else _normalize_tags(raw_tags),
            }
    return normalized


# --- evaluation ----------------------------------------------------------------------------


def _canonical(value):
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Observed values (bytes, retention) and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bytes(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _retention_text(days):
    return "never expire" if days is None else f"{days} days"


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in NUMBER_SETTINGS:
        if not _is_number(context[key]):
            return None, f"context.{key} must be a number"
        settings[key] = context[key]
    if settings["max_hot_retention_days"] <= 0:
        return None, "context.max_hot_retention_days must be positive"
    if settings["min_stored_bytes"] < 0:
        return None, "context.min_stored_bytes must not be negative"
    for key in LIST_SETTINGS:
        value = context[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            return None, f"context.{key} must be a list of nonempty strings (may be empty)"
        settings[key] = tuple(value)
    settings["compliance_tag_keys"] = frozenset(key.casefold() for key in settings["compliance_tag_keys"])
    return settings, None


def _telemetry_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    problems = []
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    resource_id = data.get("resource_id")
    if "resource_id" in data and (not isinstance(resource_id, str) or not resource_id.strip()):
        problems.append("resource_id must be a nonempty string")
    if "resource_type" in data and data["resource_type"] != RESOURCE_TYPE:
        problems.append(f"unsupported resource_type {data['resource_type']!r}; v1 supports {RESOURCE_TYPE!r}")
    if "retention_in_days" in data:
        days = data["retention_in_days"]
        if days is not None and not _is_int(days):
            problems.append("retention_in_days must be an integer or null (never expire)")
        elif days is not None and days not in VALID_RETENTION_DAYS and data.get("log_group_class") not in SHORT_LIVED_CLASSES:
            # Delivery-class retention is fixed by AWS and documented inconsistently (one or two days).
            problems.append(f"retention_in_days {days} is not a CloudWatch Logs retention value")
    if "stored_bytes" in data:
        stored = data["stored_bytes"]
        if stored is None:
            problems.append("stored_bytes was not reported")
        elif not _is_int(stored) or stored < 0:
            problems.append("stored_bytes must be a nonnegative integer")
    if "log_group_class" in data and data["log_group_class"] is not None and data["log_group_class"] not in LOG_GROUP_CLASSES:
        problems.append(f"unsupported log_group_class {data['log_group_class']!r}")
    if "tags" in data and data["tags"] is not None:
        tags = data["tags"]
        if not isinstance(tags, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items()):
            problems.append("tags must be an object of strings or null (not collected)")
    if isinstance(resource_id, str) and resource_id.strip() and scope_id != f"resource:{resource_id}":
        problems.append(f"scope id {scope_id!r} does not match resource_id (expected 'resource:{resource_id}')")
    return problems


def _finding(repository_id, scope_id, source, data, settings):
    tags_known = data["tags"] is not None
    log_class = data["log_group_class"] or "unreported"
    if data["retention_in_days"] is None:
        held = "keeps every event forever (no retention policy)"
    else:
        held = (
            f"keeps events for {data['retention_in_days']} days, beyond the "
            f"{settings['max_hot_retention_days']:g}-day hot-retention horizon"
        )
    summary = (
        f"CloudWatch Logs log group {data['resource_id']} {held} and stores {_bytes(data['stored_bytes'])} "
        f"in CloudWatch Logs ({log_class} class); "
        + ("no compliance tag is present" if tags_known else "tags were not collected, so a compliance exception cannot be ruled out")
    )
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": "medium" if tags_known else "low",
        "recommendation": (
            "Confirm the retention requirement for these logs. Then set a retention policy that matches "
            "how long they are actually queried (PutRetentionPolicy) and, if they must be kept longer, "
            "deliver them to Amazon S3 with a subscription filter and Amazon Data Firehose, and use "
            "S3 Lifecycle to move them to Glacier storage classes or expire them. The Infrequent Access "
            "log class lowers ingestion cost only, not storage cost."
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


def _compliance_tag(tags, settings):
    if not tags:
        return None
    for key in sorted(tags):
        if key.casefold() in settings["compliance_tag_keys"]:
            return key
    return None


def _evaluate_scope(scope_id, sources, settings, repository_id):
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return _Outcome(
            False, omitted_reason=f"{scope_id}: no telemetry source supplied; OBS-07 requires normalized log group data"
        )
    if len(telemetry) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one")
    source = telemetry[0]
    data = source.get("data")
    problems = _telemetry_problems(data, scope_id)
    if problems:
        return _Outcome(False, omitted_reason=f"{scope_id}: " + "; ".join(problems))

    days = data["retention_in_days"]
    stored = data["stored_bytes"]
    if data["log_group_class"] in SHORT_LIVED_CLASSES:
        return _Outcome(True)
    if days is not None and days <= settings["max_hot_retention_days"]:
        return _Outcome(True)
    retention = _retention_text(days)
    prefix = next((p for p in settings["exempt_log_group_prefixes"] if data["resource_id"].startswith(p)), None)
    if prefix is not None:
        return _Outcome(
            True,
            note=f"{scope_id}: retention {retention} with {_bytes(stored)} stored is exempt by the configured prefix {prefix!r}",
        )
    tag_key = _compliance_tag(data["tags"], settings)
    if tag_key is not None:
        return _Outcome(
            True,
            note=f"{scope_id}: retention {retention} with {_bytes(stored)} stored is exempt by the compliance tag {tag_key!r}",
        )
    if stored < settings["min_stored_bytes"]:
        return _Outcome(
            True,
            note=(
                f"{scope_id}: retention {retention} but only {_bytes(stored)} stored, below the "
                f"{_bytes(settings['min_stored_bytes'])} minimum; not flagged yet"
            ),
        )
    return _Outcome(True, finding=_finding(repository_id, scope_id, source, data, settings))


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

    result = {field: payload[field] for field in IDENTITY_FIELDS}
    result = {"schema_version": result.pop("schema_version"), "kind": "result", **result}

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
