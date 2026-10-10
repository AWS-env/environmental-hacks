"""OBS-17: verbose fields retained (full stack traces, echoed request bodies) in CloudWatch Logs.

Detector semantics version 1.0.0. Two parts:

* `LOGS_INSIGHTS_QUERY` runs on the Owner D log route (`owner-d-log-analyzer`, source `logs_insights`) over
  allowlisted log groups and a bounded window. It skips Lambda platform lines and aggregates application
  events per log group, level and 512-character size bucket: event count, summed and largest `strlen(@message)`,
  events carrying a full stack trace and events carrying a JSON `...body`/`...payload` key. It returns
  counts only; no message text leaves CloudWatch Logs.
* `normalize_logs_insights(raw, *, settings)` turns those rows into one telemetry source per
  `resource:log-group/<name>` scope item, and `evaluate(payload)` checks contract v1 inputs built from them.

A log group is flagged (`oversized-log-events`) when events larger than `context.max_event_bytes` carry more
than `context.min_bytes_share` of its application log bytes, and (`repeated-stack-traces`) when full stack
traces at below-ERROR levels occur more than `context.max_trace_repeats` times, or ERROR-level traces more than
`context.max_error_trace_repeats` times, in the window. Groups with fewer than `context.min_events` events are
not evaluated. Findings report sizes and counts only, never log content: bodies may contain personal data.
"""

from __future__ import annotations

import math
import re

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "OBS-17"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
RESOURCE_TYPE = "aws_cloudwatch_log_group"
SCOPE_PREFIX = "resource:log-group/"
SIZE_IDENTITY = "oversized-log-events"
TRACE_IDENTITY = "repeated-stack-traces"

# Size buckets: bucket k (1..BUCKET_CAP-1) holds events of (BUCKET_CHARS*(k-1), BUCKET_CHARS*k] characters,
# bucket BUCKET_CAP everything above BUCKET_CHARS*(BUCKET_CAP-1), bucket 0 empty messages. Sums per bucket are
# exact, so a threshold that is a multiple of BUCKET_CHARS is evaluated exactly.
BUCKET_CHARS = 512
BUCKET_CAP = 65
MAX_THRESHOLD = BUCKET_CHARS * (BUCKET_CAP - 1)

# Read by the log analyzer (log_handler.collect_logs_insights). Field names are prefixed `o17_` so they cannot
# collide with fields Logs Insights discovers in JSON events.
LOGS_INSIGHTS_QUERY = r"""filter @message not like /^(START|END|REPORT) RequestId: |^(INIT_START|INIT_REPORT|INIT_RUNTIME_FAILURE|RESTORE_START|RESTORE_REPORT|EXTENSION|TELEMETRY) /
| filter @message not like /"type"\s*:\s*"platform\./
| parse @message /^\[(?<o17_text_level>[A-Za-z]+)\]/
| parse @message /(?<o17_trace>Traceback \(most recent call last\)|(\n|\r|\\n|\\r)(\t|\\t| {2,})at [^\s(]+[ (]|goroutine \d+ \[)/
| parse @message /"(?<o17_body>[\w.-]{0,60}([Bb]ody|[Pp]ayload))"\s*:/
| fields strlen(@message) as o17_chars, least(ceil(strlen(@message) / 512), 65) as o17_bucket,
    substr(toupper(coalesce(level, levelname, severity, o17_text_level, "")), 0, 16) as o17_level
| stats count(*) as events, sum(o17_chars) as chars, max(o17_chars) as max_chars,
    count(o17_trace) as trace_events, count(o17_body) as body_events by @log, o17_level, o17_bucket"""

ERROR_LEVELS = frozenset({"ERROR", "ERR", "FATAL", "CRITICAL", "CRIT", "SEVERE", "ALERT", "EMERG", "EMERGENCY",
                          "50", "60"})  # 50/60: pino numeric error/fatal
BELOW_ERROR_LEVELS = frozenset({"TRACE", "DEBUG", "INFO", "INFORMATION", "NOTICE", "WARN", "WARNING",
                                "10", "20", "30", "40"})
LEVEL_CLASSES = ("error", "below_error", "unknown")

SETTING_KEYS = ("max_event_bytes", "min_bytes_share", "max_trace_repeats", "max_error_trace_repeats", "min_events")
# Reference values (team choices; see the OBS-17 section of detectors/owner-d/README.md). The registry copies
# them as OBS17_DEFAULTS; a test keeps the two equal.
REFERENCE_SETTINGS = {
    "max_event_bytes": 4096,
    "min_bytes_share": 0.25,
    "max_trace_repeats": 1,
    "max_error_trace_repeats": 10,
    "min_events": 20,
}

REQUIRED_DATA_FIELDS = ("resource_id", "resource_type", "bucket_chars", "events", "event_chars",
                        "max_event_chars", "size_histogram", "trace_events_by_level", "body_field_events", "problems")
HISTOGRAM_FIELDS = ("bucket", "events", "chars", "trace_events", "body_events")

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/241",
    "https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html",
    "https://aws.amazon.com/cloudwatch/pricing/",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html",
)
SIZE_RECOMMENDATION = (
    "Stop logging whole payloads and stack traces by default. Log a reference instead (an ID, the body size "
    "and a hash), truncate large fields at the logger, and keep a full stack trace only on the ERROR line of a "
    "real failure. If the extended detail is needed, send it to a separate, sampled or short-retention "
    "destination, as the OWASP Logging Cheat Sheet suggests for stack traces and request bodies. Check "
    "that no personal data is being logged in these fields."
)
TRACE_RECOMMENDATION = (
    "Log handled or expected exceptions at INFO/WARN without the traceback (exception type and message are "
    "enough), and log a full stack trace once per real failure at ERROR. Deduplicate or rate-limit repeated "
    "error traces, for example by logging the trace once and a count afterwards."
)
LIMITATION = (
    "OBS-17 v1 measures whole events with Logs Insights (strlen, Unicode code points; a lower bound for "
    "non-ASCII bytes, without CloudWatch's per-event overhead). It recognizes stack traces by Python, Java, "
    ".NET, Node.js and Go markers within one event and echoed bodies by JSON keys ending in body/payload; it "
    "cannot see per-field sizes or whether the detail is needed. Lambda platform lines are excluded."
)

_ACCOUNT_PREFIX = re.compile(r"^\d{12}:")
_ROW_FIELDS = ("@log", "o17_level", "o17_bucket", "events", "chars", "max_chars", "trace_events", "body_events")
_OPTIONAL_COUNTS = ("trace_events", "body_events")


# --------------------------------------------------------------------------------------------------------
# Normalization: Logs Insights rows -> one telemetry source per log group
# --------------------------------------------------------------------------------------------------------


def scope_id_for(log_group):
    return f"{SCOPE_PREFIX}{log_group}"


def level_class(level):
    text = (level or "").strip().upper()
    if text in ERROR_LEVELS:
        return "error"
    if text in BELOW_ERROR_LEVELS:
        return "below_error"
    return "unknown"


def _log_group_name(value):
    """`@log` is `<account id>:<log group name>`; the account ID is dropped."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _ACCOUNT_PREFIX.sub("", value.strip()) or None


def _count_value(value):
    """Logs Insights returns numbers as strings ("12", "12.0"). Nonnegative whole numbers only."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value != int(value):
        return None
    return int(value)


def _empty(name):
    return {"resource_id": name, "resource_type": RESOURCE_TYPE, "bucket_chars": BUCKET_CHARS, "events": 0,
            "event_chars": 0, "max_event_chars": 0, "size_histogram": {}, "events_by_level":
            dict.fromkeys(LEVEL_CLASSES, 0), "trace_events_by_level": dict.fromkeys(LEVEL_CLASSES, 0),
            "body_field_events": 0, "problems": []}


def _row_problem(row):
    """None, or why a row cannot be used. Checks the bucket against the observed maximum size."""
    values = {}
    for key in _ROW_FIELDS[2:]:
        # Logs Insights leaves a count(field) column out of a row when no event in the group had the field.
        value = row.get(key, 0) if key in _OPTIONAL_COUNTS else row.get(key)
        values[key] = _count_value(value)
        if values[key] is None:
            return None, f"{key}={str(row.get(key))[:20]!r} is not a nonnegative whole number"
    bucket, events, maximum = values["o17_bucket"], values["events"], values["max_chars"]
    if bucket > BUCKET_CAP or events == 0:
        return None, f"bucket {bucket} with {events} events is outside the query's shape"
    low = BUCKET_CHARS * (bucket - 1) if bucket else -1
    high = BUCKET_CHARS * bucket if 0 < bucket < BUCKET_CAP else (0 if bucket == 0 else math.inf)
    if not low < maximum <= high or values["chars"] > maximum * events or values["chars"] < maximum:
        return None, f"sizes in bucket {bucket} do not match its bounds"
    if values["trace_events"] > events or values["body_events"] > events:
        return None, "marker counts exceed the event count"
    return values, None


def normalize_logs_insights(raw, *, settings=None):
    """Convert the log analyzer's raw Logs Insights dict ({"rows", "log_groups", "window", "truncated", ...})
    into {"scope", "sources", "limitations"}. Every queried log group gets a scope item and a source, with zero
    counts when it had no application events; problems that make a group's aggregates incomplete are listed in
    its `problems` field, which the detector turns into an unevaluated scope item. Pure; drops account IDs."""
    if not isinstance(raw, dict):
        return {"scope": [], "sources": [], "limitations": ["OBS-17: the Logs Insights response is not an object"]}
    rows = raw.get("rows")
    queried = raw.get("log_groups")
    names = [n for n in (queried if isinstance(queried, list) else []) if isinstance(n, str) and n]
    limitations = []
    if not isinstance(rows, list):
        return {"scope": [scope_id_for(n) for n in names], "sources": [],
                "limitations": ["OBS-17: the Logs Insights response has no rows list; no log group was evaluated"]}
    groups = {name: _empty(name) for name in names}
    shared_problems = []
    if raw.get("truncated"):
        shared_problems.append("the query hit its row limit, so this group's aggregates may be incomplete")
    unattributed = 0
    for index, row in enumerate(rows):
        name = _log_group_name(row.get("@log")) if isinstance(row, dict) else None
        if name is None or (names and name not in groups):
            unattributed += 1
            continue
        data = groups.setdefault(name, _empty(name))
        values, problem = _row_problem(row)
        if problem:
            data["problems"].append(f"row {index}: {problem}")
            continue
        level = level_class(row.get("o17_level"))
        key = str(values["o17_bucket"])
        bucket = data["size_histogram"].setdefault(key, dict.fromkeys(HISTOGRAM_FIELDS[1:], 0))
        for field, column in (("events", "events"), ("chars", "chars"), ("trace_events", "trace_events"),
                              ("body_events", "body_events")):
            bucket[field] += values[column]
        data["events"] += values["events"]
        data["event_chars"] += values["chars"]
        data["max_event_chars"] = max(data["max_event_chars"], values["max_chars"])
        data["events_by_level"][level] += values["events"]
        data["trace_events_by_level"][level] += values["trace_events"]
        data["body_field_events"] += values["body_events"]
    if unattributed:
        shared_problems.append(f"{unattributed} result rows could not be attributed to a queried log group")
        limitations.append(f"OBS-17: {unattributed} Logs Insights rows had no queried @log value; every group's "
                           "aggregates may be incomplete")
    window = raw.get("window") if isinstance(raw.get("window"), dict) else None
    sources = []
    for name in sorted(groups):
        data = groups[name]
        data["problems"] = shared_problems + data["problems"]
        data["size_histogram"] = [{"bucket": int(k), **v} for k, v in sorted(data["size_histogram"].items(),
                                                                             key=lambda kv: int(kv[0]))]
        data["window"] = window
        sources.append({"source_id": f"logs-insights:{name}", "scope_id": scope_id_for(name), "kind": SUPPORTED_KIND,
                        "locator": f"logs-insights://{raw.get('region', 'ap-south-1')}/log-group/{name}",
                        "data": data})
    return {"scope": [s["scope_id"] for s in sources], "sources": sources, "limitations": limitations}


# --------------------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------------------


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    threshold = context["max_event_bytes"]
    if not _is_int(threshold) or not BUCKET_CHARS <= threshold <= MAX_THRESHOLD or threshold % BUCKET_CHARS:
        return None, (f"context.max_event_bytes must be a multiple of {BUCKET_CHARS} between {BUCKET_CHARS} and "
                      f"{MAX_THRESHOLD} (the query's size buckets)")
    share = context["min_bytes_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not math.isfinite(share) or not 0 <= share < 1:
        return None, "context.min_bytes_share must be a number in [0, 1)"
    for key in ("max_trace_repeats", "max_error_trace_repeats"):
        if not _is_int(context[key]) or context[key] < 0:
            return None, f"context.{key} must be a nonnegative integer"
    if not _is_int(context["min_events"]) or context["min_events"] < 1:
        return None, "context.min_events must be a positive integer"
    return {key: context[key] for key in SETTING_KEYS}, None


def _counts_ok(value, keys):
    return isinstance(value, dict) and all(_is_int(value.get(k)) and value[k] >= 0 for k in keys)


def _data_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    if data["resource_type"] != RESOURCE_TYPE:
        problems.append(f"unsupported resource_type {str(data['resource_type'])[:40]!r}")
    if not isinstance(data["resource_id"], str) or scope_id != scope_id_for(data["resource_id"]):
        problems.append(f"scope id does not match resource_id (expected '{SCOPE_PREFIX}<log group name>')")
    if data["bucket_chars"] != BUCKET_CHARS:
        problems.append(f"bucket_chars must be {BUCKET_CHARS} (data from another query shape)")
    for field in ("events", "event_chars", "max_event_chars", "body_field_events"):
        if not _is_int(data[field]) or data[field] < 0:
            problems.append(f"{field} must be a nonnegative integer")
    if not _counts_ok(data["trace_events_by_level"], LEVEL_CLASSES):
        problems.append("trace_events_by_level must map error/below_error/unknown to counts")
    histogram = data["size_histogram"]
    if not isinstance(histogram, list) or not all(_counts_ok(b, HISTOGRAM_FIELDS) for b in histogram):
        problems.append("size_histogram must be a list of bucket counts")
    elif not problems:
        if sum(b["events"] for b in histogram) != data["events"] or sum(b["chars"] for b in histogram) != data["event_chars"]:
            problems.append("size_histogram does not add up to events/event_chars")
        if any(b["bucket"] > BUCKET_CAP for b in histogram) or len({b["bucket"] for b in histogram}) != len(histogram):
            problems.append("size_histogram buckets must be unique and within the query's range")
    if not isinstance(data["problems"], list) or not all(isinstance(p, str) for p in data["problems"]):
        problems.append("problems must be a list of strings")
    elif data["problems"]:
        problems.extend(data["problems"])
    return problems


def _evidence(source, *fields):
    return [{"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"], "field": f,
             "value": source["data"][f]} for f in fields]


def _size(chars):
    if chars < 1024:
        return f"{chars} characters"
    return f"{chars / 1024:.1f} Ki characters"


def _verb(count):
    return "carries" if count == 1 else "carry"


def _traces(count):
    return f"{count} stack trace" + ("" if count == 1 else "s")


def _pct(value):
    return f"{value * 100:.1f}%"


def _size_finding(scope_id, source, settings, repository_id):
    data = source["data"]
    limit_bucket = settings["max_event_bytes"] // BUCKET_CHARS
    large = [b for b in data["size_histogram"] if b["bucket"] > limit_bucket]
    large_events = sum(b["events"] for b in large)
    large_chars = sum(b["chars"] for b in large)
    total = data["event_chars"]
    share = large_chars / total if total else 0.0
    if not large_events or share <= settings["min_bytes_share"]:
        return None, large_events, share
    traced = sum(b["trace_events"] for b in large)
    bodies = sum(b["body_events"] for b in large)
    markers = []
    if traced:
        markers.append(f"{traced} {_verb(traced)} a full stack trace")
    if bodies:
        markers.append(f"{bodies} {_verb(bodies)} a JSON body/payload field")
    summary = (
        f"Log group {data['resource_id']}: {large_events} of {data['events']} application log events are larger "
        f"than {settings['max_event_bytes']} characters and carry {_pct(share)} of the {_size(total)} logged in "
        f"the window (more than the {_pct(settings['min_bytes_share'])} limit; largest event "
        f"{data['max_event_chars']} characters). "
        + (("Of the large events, " + " and ".join(markers) + ".") if markers else
           "The large events carry no recognized stack-trace or body marker.")
    )
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, SIZE_IDENTITY),
        "scope_id": scope_id,
        "identity": SIZE_IDENTITY,
        "summary": summary,
        "confidence": "medium" if markers else "low",
        "recommendation": SIZE_RECOMMENDATION,
        "references": list(REFERENCES),
        "evidence": _evidence(source, "size_histogram", "event_chars", "events"),
    }, large_events, share


def _trace_finding(scope_id, source, settings, repository_id):
    data = source["data"]
    traces = data["trace_events_by_level"]
    reasons = []
    if traces["below_error"] > settings["max_trace_repeats"]:
        reasons.append(f"{traces['below_error']} events below ERROR level carry a full stack trace (limit "
                       f"{settings['max_trace_repeats']})")
    if traces["error"] > settings["max_error_trace_repeats"]:
        reasons.append(f"{traces['error']} ERROR-level events carry a full stack trace (limit "
                       f"{settings['max_error_trace_repeats']})")
    if not reasons:
        return None
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, TRACE_IDENTITY),
        "scope_id": scope_id,
        "identity": TRACE_IDENTITY,
        "summary": f"Log group {data['resource_id']}: " + "; ".join(reasons) + f" in the window, out of "
                   f"{data['events']} application log events.",
        "confidence": "medium",
        "recommendation": TRACE_RECOMMENDATION,
        "references": list(REFERENCES),
        "evidence": _evidence(source, "trace_events_by_level", "events"),
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes)."""
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [f"{scope_id}: no telemetry source supplied; OBS-17 requires Logs Insights aggregates"]
    if len(telemetry) > 1:
        return False, [], [f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"]
    source = telemetry[0]
    problems = _data_problems(source.get("data"), scope_id)
    if problems:
        return False, [], [f"{scope_id}: not evaluated: " + "; ".join(problems)]
    data = source["data"]
    if data["events"] < settings["min_events"]:
        return False, [], [f"{scope_id}: only {data['events']} application log events in the window, fewer than "
                           f"min_events {settings['min_events']}; not evaluated"]
    findings, notes = [], []
    size, large_events, share = _size_finding(scope_id, source, settings, repository_id)
    if size:
        findings.append(size)
    elif large_events:
        notes.append(f"{scope_id}: {large_events} events larger than {settings['max_event_bytes']} characters carry "
                     f"{_pct(share)} of the logged characters, within the {_pct(settings['min_bytes_share'])} "
                     "limit; not flagged")
    trace = _trace_finding(scope_id, source, settings, repository_id)
    if trace:
        findings.append(trace)
    traces = data["trace_events_by_level"]
    if not trace and traces["error"]:
        notes.append(f"{scope_id}: {_traces(traces['error'])} at ERROR level, within max_error_trace_repeats "
                     f"{settings['max_error_trace_repeats']}; not flagged")
    if traces["unknown"]:
        notes.append(f"{scope_id}: {_traces(traces['unknown'])} without a recognized level, not judged by the "
                     "trace rule")
    return True, findings, notes


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(payload.get("detector_version") == DETECTOR_VERSION,
             f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements "
             f"{DETECTOR_VERSION}")
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    result = {"schema_version": payload["schema_version"], "kind": "result",
              **{field: payload[field] for field in IDENTITY_FIELDS if field != "schema_version"}}
    settings, reason = _read_settings(payload["context"])
    if settings is None:
        result.update(status="unavailable", findings=[], measurements=[],
                      coverage={"evaluated_scope": [],
                                "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION]})
        return result

    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        mine = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        ok, found, notes = _evaluate_scope(scope_id, mine, settings, payload["repository_id"])
        if ok:
            evaluated.append(scope_id)
        findings.extend(found)
        limitations.extend(notes)
    limitations.append(LIMITATION)
    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result.update(status=status, coverage={"evaluated_scope": evaluated, "limitations": limitations},
                  findings=findings, measurements=[])
    return result
