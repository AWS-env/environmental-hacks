"""OBS-11: duplicate/repeated log lines (retry loops, flapping checks) in CloudWatch Logs.

Detector semantics version 1.0.0. Two parts:

* `LOGS_INSIGHTS_QUERY` and `normalize_logs_insights(raw, *, settings)` run on the `owner-d-log-analyzer`
  route. The query normalises `@message` (UUIDs, ISO timestamps, hex IDs, numbers and whitespace become
  placeholders), counts events per log group and normalised message, and folds messages seen fewer than
  `FOLD_THRESHOLD` times into one `<other>` row per log group. The normalizer turns those rows into one
  `telemetry` source per `resource:log-group/<name>` scope item, with the reported text redacted.
* `evaluate(payload)` flags a log group when one normalised message occurs more than `context.min_repeats`
  times in the window and makes up more than `context.min_share` of the group's application events, once the
  group has at least `context.min_events` application events.

Lambda platform lines (START/END/REPORT, INIT_*, RESTORE_*, EXTENSION, TELEMETRY) never count as application
events; START lines give the invocation count. Heartbeat lines (`context.exempt_message_markers`) and lines
logged at most once per invocation on average are exceptions. The detector never calls AWS.
"""

from __future__ import annotations

import hashlib
import re

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "OBS-11"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
SCOPE_PREFIX = "resource:log-group/"
IDENTITY_PREFIX = "repeated-log-line:"
FOLD_THRESHOLD = 10  # the query keeps a message's own row only when it occurs at least this often
OTHER = "<other>"
MAX_MESSAGES = 20  # application messages kept per log group in the source data
MAX_MESSAGE_CHARS = 200  # reported (redacted) text length
NORMALIZED_PREFIX_CHARS = 400  # the query compares the first 400 characters of each message

SETTING_KEYS = ("min_events", "min_repeats", "min_share", "exempt_message_markers")
# Reference values (issue #235 verification plan). All four are required judgment calls.
REFERENCE_SETTINGS = {"min_events": 100, "min_repeats": 50, "min_share": 0.2, "exempt_message_markers": ["heartbeat"]}

_PLATFORM_KEYWORDS = ("INIT_START", "INIT_REPORT", "INIT_RUNTIME_DONE", "RESTORE_START", "RESTORE_REPORT",
                      "RESTORE_RUNTIME_DONE", "EXTENSION", "TELEMETRY")
_PLATFORM_QUERY_REGEX = ("^(START|END|REPORT) RequestId: |^(" + "|".join(_PLATFORM_KEYWORDS)
                         + r')\s|"type" *: *"platform[.]')

# CloudWatch Logs Insights (default query language). regexReplace uses RE2 syntax and its patterns avoid
# backslash escapes. `like /.../` is a different regex dialect: POSIX classes such as [[:space:]] do not match
# there (checked against the live service), so the platform pattern uses \s. Bytes scanned do not depend on
# the query text, only on the window and the log groups.
LOGS_INSIGHTS_QUERY = (
    "fields regexReplace(regexReplace(regexReplace(regexReplace(regexReplace(regexReplace("
    f"substr(@message, 0, {NORMALIZED_PREFIX_CHARS}), "
    '"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<uuid>"), '
    '"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.,][0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?", "<ts>"), '
    '"[0-9a-fA-F]{8,}", "<hex>"), '
    '"[0-9]+", "<n>"), '
    '"[[:space:]]+", " "), '
    '"^ | $", "") as normalized\n'
    "| stats count(*) as n, min(@timestamp) as first, max(@timestamp) as last by @log, normalized\n"
    f"| fields if(n >= {FOLD_THRESHOLD} or normalized like /{_PLATFORM_QUERY_REGEX}/,"
    f' normalized, "{OTHER}") as message\n'
    "| stats sum(n) as occurrences, min(first) as first_seen, max(last) as last_seen"
    " by @log, message\n"
    "| sort @log asc, occurrences desc"
)

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/235",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData_Patterns.html",
    "https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html",
    "https://docs.powertools.aws.dev/lambda/python/latest/core/logger/#sampling-debug-logs",
    "https://aws.amazon.com/cloudwatch/pricing/",
)

GENERAL_LIMITATION = (
    "OBS-11 v1 counts normalised messages (numbers, UUIDs, hex IDs and timestamps replaced; first "
    f"{NORMALIZED_PREFIX_CHARS} characters compared) per log group over the query window. Lines that differ "
    "only in numbers are grouped, bursts within an invocation are not timed, a retry loop that fires on fewer "
    "lines than there are invocations is not flagged, and ingested bytes are not measured."
)

_PLATFORM = re.compile(
    r"^(?:(?:START|END|REPORT) RequestId: |(?:" + "|".join(_PLATFORM_KEYWORDS) + r")\s)"
    r"|\"type\"\s*:\s*\"platform\."
)
_START = re.compile(r"^START RequestId: |\"type\"\s*:\s*\"platform\.start\"")
_ACCOUNT_PREFIX = re.compile(r"^\d{12}:")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Redaction of reported text. The query already replaced numbers, UUIDs, hex IDs and timestamps.
_PART = r"(?:[0-9A-Fa-f]|<n>|<hex>){1,4}"
_REDACTIONS = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[^\s\"',;]+"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|"
                r"client[_-]?secret|authorization|cookie|session[_-]?id)(\"?\s*[:=]\s*\"?)([^\s\"',;}&]+)"),
     r"\1\2<redacted>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"(?<![\w.<>])(?:\d{1,3}|<n>)(?:\.(?:\d{1,3}|<n>)){3}(?![\w.<>])"), "<ip>"),
    (re.compile(rf"(?<![\w:<>]){_PART}(?::{_PART}){{3,7}}(?![\w:<>])"), "<ip>"),
    (re.compile(rf"(?<![\w:<>])(?:{_PART}(?::{_PART}){{0,6}})?::(?:{_PART}(?::{_PART}){{0,6}})?(?![\w:<>])"), "<ip>"),
)
_TOKEN = re.compile(r"(?:[A-Za-z0-9+/=_.\-]|<n>|<hex>)+")


class NormalizationError(ValueError):
    """A Logs Insights response cannot be normalized."""


def _mask_token(match):
    text = match.group(0)
    if len(text) < 24:
        return text
    letters = re.sub(r"<n>|<hex>", "", text)
    has_digit = "<n>" in text or "<hex>" in text or any(c.isdigit() for c in letters)
    has_alpha = any(c.isalpha() for c in letters)
    mixed_case = any(c.islower() for c in letters) and any(c.isupper() for c in letters)
    if (has_digit and has_alpha) or (len(text) >= 32 and mixed_case):
        return "<token>"
    return text


def redact(text):
    """Mask secrets in a (normalised) message: credentials, emails, IP addresses and long tokens."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return _TOKEN.sub(_mask_token, text)


def display(text):
    text = redact(text)
    return text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS - 1] + "…"


def is_platform(message):
    return bool(_PLATFORM.search(message))


def message_hash(redacted):
    return hashlib.sha256(redacted.encode("utf-8")).hexdigest()


def scope_id_for(log_group):
    return SCOPE_PREFIX + log_group


# --------------------------------------------------------------------------------------------------------
# Normalization of raw Logs Insights rows (owner-d-log-analyzer, logs_insights source)
# --------------------------------------------------------------------------------------------------------


def _log_group_name(value):
    if not isinstance(value, str) or not value:
        return None
    return _ACCOUNT_PREFIX.sub("", value)


def _row_count(value):
    try:
        count = float(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0 or count != int(count):
        return None
    return int(count)


def _group_data(name, rows, window):
    events = platform = folded = 0
    invocations = 0
    merged = {}
    for row in rows:
        message, count = row["message"], row["occurrences"]
        events += count
        if message == OTHER:
            folded += count
            continue
        if is_platform(message):
            platform += count
            if _START.search(message):
                invocations += count
            continue
        text = redact(message)
        entry = merged.setdefault(text, {"occurrences": 0, "first_seen": None, "last_seen": None})
        entry["occurrences"] += count
        for key, pick in (("first_seen", min), ("last_seen", max)):
            value = row.get(key)
            if isinstance(value, str) and value:
                entry[key] = value if entry[key] is None else pick(entry[key], value)
    application = events - platform
    ranked = sorted(merged.items(), key=lambda item: (-item[1]["occurrences"], item[0]))
    messages = [{
        "message": display(text),
        "message_sha256": message_hash(text),
        "occurrences": entry["occurrences"],
        "share": round(entry["occurrences"] / application, 4) if application else 0.0,
        "first_seen": entry["first_seen"],
        "last_seen": entry["last_seen"],
    } for text, entry in ranked[:MAX_MESSAGES]]
    return {
        "log_group": name,
        "window": window,
        "events": events,
        "platform_events": platform,
        "application_events": application,
        "invocations": invocations or None,
        "folded_events": folded,
        "fold_threshold": FOLD_THRESHOLD,
        "messages": messages,
        "omitted_messages": max(0, len(ranked) - MAX_MESSAGES),
    }


def normalize_logs_insights(raw, *, settings=None):
    """Raw `{"rows", "log_groups", "window", "truncated", "region"}` from log_handler into OBS-11 sources.

    Every queried log group becomes a scope item. A group gets a source only when its rows are complete:
    when the row limit was reached, the last group in the (log-group-sorted) rows and every group not seen
    are left without a source. Groups with malformed rows are left without a source too."""
    if not isinstance(raw, dict):
        raise NormalizationError("raw Logs Insights data must be an object")
    rows, groups = raw.get("rows"), raw.get("log_groups")
    if not isinstance(rows, list) or not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise NormalizationError("raw Logs Insights data needs a rows list and a log_groups list of names")
    window = raw.get("window")
    region = raw.get("region") or "ap-south-1"
    truncated = bool(raw.get("truncated"))
    queried = list(dict.fromkeys(groups))
    by_group, order, malformed, notes = {}, [], set(), []
    unattributed = unknown = 0
    for row in rows:
        if not isinstance(row, dict):
            unattributed += 1
            continue
        name = _log_group_name(row.get("@log"))
        if name is None:
            unattributed += 1
            continue
        if name not in queried:
            unknown += 1
            continue
        if not order or order[-1] != name:
            order.append(name)
        message, count = row.get("message"), _row_count(row.get("occurrences"))
        if not isinstance(message, str) or count is None:
            malformed.add(name)
            continue
        by_group.setdefault(name, []).append({**row, "occurrences": count})
    if unattributed:
        notes.append(f"{unattributed} Logs Insights row(s) had no @log field; no log group can be evaluated "
                     "because their totals are unknown")
    if unknown:
        notes.append(f"{unknown} Logs Insights row(s) named a log group that was not queried; they were ignored")
    complete = set(queried)
    if truncated:
        contiguous = len(order) == len(set(order))
        complete = set(order[:-1]) if contiguous else set()
        cut = [g for g in queried if g not in complete]
        if cut:
            notes.append(f"the Logs Insights row limit was reached, so {len(cut)} log group(s) have incomplete "
                         "counts and were not evaluated: " + ", ".join(cut))
    for name in sorted(malformed):
        notes.append(f"{scope_id_for(name)}: Logs Insights rows with a missing message or a non-integer "
                     "occurrences value; not evaluated")
    scope, sources = [], []
    for name in queried:
        scope.append(scope_id_for(name))
        if unattributed or name not in complete or name in malformed:
            continue
        sources.append({
            "source_id": f"logs-insights:{name}",
            "scope_id": scope_id_for(name),
            "kind": SUPPORTED_KIND,
            "locator": f"logs-insights://{region}/log-group/{name}",
            "data": _group_data(name, by_group.get(name, []), window),
        })
    return {"scope": scope, "sources": sources, "limitations": notes}


# --------------------------------------------------------------------------------------------------------
# Detection over contract v1 inputs
# --------------------------------------------------------------------------------------------------------


def _count(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    if not _count(context["min_events"], 1):
        return None, "context.min_events must be a positive integer"
    if not _count(context["min_repeats"], FOLD_THRESHOLD - 1):
        return None, (f"context.min_repeats must be an integer of at least {FOLD_THRESHOLD - 1} (the query folds "
                      f"messages seen fewer than {FOLD_THRESHOLD} times)")
    share = context["min_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share < 1:
        return None, "context.min_share must be a number in [0, 1)"
    markers = context["exempt_message_markers"]
    if not isinstance(markers, list) or not all(isinstance(m, str) and m.strip() for m in markers):
        return None, "context.exempt_message_markers must be a list of nonempty strings (may be empty)"
    return {"min_events": context["min_events"], "min_repeats": context["min_repeats"], "min_share": share,
            "exempt_message_markers": tuple(m.casefold() for m in markers)}, None


def _is_timestamp(value):
    return isinstance(value, str) and bool(value)


def _message_problems(index, entry, application):
    if not isinstance(entry, dict):
        return [f"messages[{index}] must be an object"]
    problems = []
    if not isinstance(entry.get("message"), str) or not entry["message"]:
        problems.append(f"messages[{index}].message must be a nonempty string")
    if not isinstance(entry.get("message_sha256"), str) or not _SHA256.match(entry["message_sha256"]):
        problems.append(f"messages[{index}].message_sha256 must be a SHA-256 hex digest")
    occurrences = entry.get("occurrences")
    if not _count(occurrences, 1):
        problems.append(f"messages[{index}].occurrences must be a positive integer")
    elif _count(application) and occurrences > application:
        problems.append(f"messages[{index}].occurrences exceeds application_events")
    return problems


def _data_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    required = ("log_group", "window", "events", "platform_events", "application_events", "invocations",
                "messages")
    missing = [key for key in required if key not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    name = data["log_group"]
    if not isinstance(name, str) or not name:
        problems.append("log_group must be a nonempty string")
    elif scope_id != scope_id_for(name):
        problems.append(f"scope id {scope_id!r} does not match log_group (expected {scope_id_for(name)!r})")
    window = data["window"]
    if not isinstance(window, dict) or not _is_timestamp(window.get("start")) or not _is_timestamp(window.get("end")):
        problems.append("window must be an object with start and end timestamps")
    for key in ("events", "platform_events", "application_events"):
        if not _count(data[key]):
            problems.append(f"{key} must be a nonnegative integer")
    if not problems and data["application_events"] != data["events"] - data["platform_events"]:
        problems.append("application_events must equal events - platform_events")
    if data["invocations"] is not None and not _count(data["invocations"], 1):
        problems.append("invocations must be a positive integer or null (unknown)")
    if not isinstance(data["messages"], list):
        problems.append("messages must be a list")
    else:
        for index, entry in enumerate(data["messages"]):
            problems.extend(_message_problems(index, entry, data["application_events"]))
    return problems


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def _finding(repository_id, scope_id, source, data, entry, share):
    name, occurrences, invocations = data["log_group"], entry["occurrences"], data["invocations"]
    text = display(entry["message"])
    per_invocation = (f", {occurrences / invocations:.1f} per invocation over {_plural(invocations, 'invocation')}"
                      if invocations else ", invocation count unknown")
    summary = (
        f"Log group {name} logged the same normalised message {occurrences} times between "
        f"{data['window']['start']} and {data['window']['end']}: {share:.0%} of its "
        f"{data['application_events']} application events{per_invocation}. Message: \"{text}\""
    )
    exact = "<n>" not in text
    fields = ("messages", "application_events", "invocations", "window")
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY_PREFIX + entry["message_sha256"][:16]),
        "scope_id": scope_id,
        "identity": IDENTITY_PREFIX + entry["message_sha256"][:16],
        "summary": summary,
        "confidence": "high" if exact and invocations else "medium",
        "recommendation": (
            "Log a retry sequence or a flapping check once, with the attempt count and the final outcome, instead "
            "of one line per attempt or per poll. Log per-attempt detail at DEBUG and sample it (for example "
            "Powertools for AWS Lambda log sampling), back off with jitter so retries stay bounded, and fix the "
            "underlying failure or flapping dependency. CloudWatch Logs bills ingestion and storage per GB, so "
            "every repeated line is billed when it is ingested and again while it is stored."
        ),
        "references": list(REFERENCES),
        "evidence": [
            {"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"], "field": field,
             "value": data[field]}
            for field in fields
        ],
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes, omitted_reason) for one log group."""
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [], f"{scope_id}: no telemetry source supplied; OBS-11 requires Logs Insights message counts"
    if len(telemetry) > 1:
        return False, [], [], f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"
    source = telemetry[0]
    data = source.get("data")
    problems = _data_problems(data, scope_id)
    if problems:
        return False, [], [], f"{scope_id}: " + "; ".join(problems)
    application = data["application_events"]
    if application < settings["min_events"]:
        return False, [], [], (f"{scope_id}: only {application} application events in the window, below "
                               f"min_events {settings['min_events']}; not evaluated")
    findings, platform, heartbeat, per_invocation = [], [], [], []
    for entry in data["messages"]:
        occurrences = entry["occurrences"]
        share = occurrences / application
        if occurrences <= settings["min_repeats"] or share <= settings["min_share"]:
            continue
        if is_platform(entry["message"]):
            platform.append(entry)
        elif any(marker in entry["message"].casefold() for marker in settings["exempt_message_markers"]):
            heartbeat.append(entry)
        elif data["invocations"] and occurrences <= data["invocations"]:
            per_invocation.append(entry)
        else:
            findings.append(_finding(repository_id, scope_id, source, data, entry, share))
    notes = []
    if platform:
        notes.append(f"{scope_id}: {_plural(len(platform), 'repeated Lambda platform line')} not flagged")
    if heartbeat:
        notes.append(f"{scope_id}: {_plural(len(heartbeat), 'repeated heartbeat line')} exempt by "
                     "exempt_message_markers")
    if per_invocation:
        quoted = "; ".join(f"\"{display(e['message'])}\" ({e['occurrences']} lines, {data['invocations']} "
                           "invocations)" for e in per_invocation)
        notes.append(f"{scope_id}: logged at most once per invocation on average, so treated as per-request "
                     f"lines, not retry loops: {quoted}")
    if data.get("omitted_messages"):
        notes.append(f"{scope_id}: {data['omitted_messages']} less frequent messages were not supplied")
    return True, findings, notes, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    if not isinstance(payload, dict):
        raise EvaluationError("input payload must be an object")
    if payload.get("kind") != "input":
        raise EvaluationError("expected a contract input payload")
    if payload.get("check_id") != CHECK_ID:
        raise EvaluationError(f"detector only evaluates {CHECK_ID}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError("unsupported schema_version; contract v1 is required")
    if payload.get("detector_version") != DETECTOR_VERSION:
        raise EvaluationError(f"unsupported detector_version {payload.get('detector_version')!r}; this detector "
                              f"implements {DETECTOR_VERSION}")
    for field in IDENTITY_FIELDS:
        if field not in payload:
            raise EvaluationError(f"input is missing required field {field}")
    scope, sources = payload["scope"], payload.get("sources")
    if not isinstance(scope, list) or not scope:
        raise EvaluationError("scope must be a nonempty list")
    if not isinstance(sources, list):
        raise EvaluationError("sources must be a list")

    result = {"schema_version": payload["schema_version"], "kind": "result",
              **{field: payload[field] for field in IDENTITY_FIELDS if field != "schema_version"}}
    settings, reason = _read_settings(payload["context"])
    if settings is None:
        result.update(status="unavailable", findings=[], measurements=[], coverage={
            "evaluated_scope": [],
            "limitations": [f"Missing or invalid context settings: {reason}", GENERAL_LIMITATION]})
        return result

    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        scope_sources = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        ok, scope_findings, notes, omitted = _evaluate_scope(scope_id, scope_sources, settings,
                                                             payload["repository_id"])
        if ok:
            evaluated.append(scope_id)
            findings.extend(scope_findings)
            limitations.extend(notes)
        else:
            limitations.append(omitted)
    limitations.append(GENERAL_LIMITATION)
    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result.update(status=status, coverage={"evaluated_scope": evaluated, "limitations": limitations},
                  findings=findings, measurements=[])
    return result
