"""LLM-17: static provisioning / sizing for the theoretical maximum in agent workloads (CloudWatch Logs).

Detector semantics version 1.0.0. Telemetry only; a repository scan reports it `unavailable`. Two parts:

* `LOGS_INSIGHTS_QUERY` runs on the Owner D log route (`owner-d-log-analyzer`, source `logs_insights`) over
  allowlisted log groups and a bounded window. It reads two kinds of capacity record and returns aggregates
  only (counts, peak, p99 and average use per workload), never message text:
  - Lambda `REPORT` lines, which Logs Insights parses for every function without app changes:
    `@memorySize` (provisioned memory) and `@maxMemoryUsed` (peak memory of one invocation);
  - structured agent capacity lines: a JSON log event with `agent`, `capacity_kind` (for example `workers`,
    `concurrency`, `max_tokens`), `capacity_provisioned` and `capacity_used`, one per agent run.
* `normalize_logs_insights(raw, *, settings)` turns those rows into one telemetry source per workload:
  `resource:log-group/<name>#lambda-memory` or `resource:log-group/<name>#agent/<agent>/<capacity_kind>`.
  A queried log group with no capacity record at all keeps a `resource:log-group/<name>` scope item without
  a source, so it is reported as not evaluated, never as clean.

`evaluate(payload)` flags a workload (`lambda-memory` / `agent-capacity`) whose largest use in the window stays
below `context.max_peak_utilization` of what it provisions, once the window covers at least
`context.min_window_hours` and the workload logged at least `context.min_invocations` invocations (REPORT lines
or agent capacity lines). Workloads already at the smallest size (128 MB Lambda memory, 1 unit of agent
capacity) are not flagged. The detector never calls AWS.
"""

from __future__ import annotations

import datetime as dt
import math
import re

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "LLM-17"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
SCOPE_PREFIX = "resource:log-group/"
LAMBDA = "lambda-memory"
AGENT = "agent-capacity"
LAMBDA_MIN_MEMORY_MB = 128  # smallest Lambda memory setting
AGENT_MIN_CAPACITY = 1
# Logs Insights reports @memorySize/@maxMemoryUsed in units of 10^6 per "MB" (AWS sample query: / 1000 / 1000).
BYTES_PER_MB = 1_000_000

# Read by the log analyzer (log_handler.collect_logs_insights). Computed fields are prefixed `l17_` so they cannot
# collide with fields Logs Insights discovers in JSON events. REPORT lines have no `agent`/`capacity_kind`, so
# their l17_agent and l17_kind are empty; agent lines have no @memorySize/@maxMemoryUsed.
LOGS_INSIGHTS_QUERY = """filter (@type = "REPORT" and ispresent(@memorySize) and ispresent(@maxMemoryUsed))
    or (ispresent(agent) and ispresent(capacity_kind) and ispresent(capacity_provisioned) and ispresent(capacity_used))
| fields coalesce(@memorySize, capacity_provisioned) as l17_provisioned,
    coalesce(@maxMemoryUsed, capacity_used) as l17_used,
    coalesce(agent, "") as l17_agent, coalesce(capacity_kind, "") as l17_kind
| stats count(*) as invocations, max(l17_used) as peak_used, pct(l17_used, 99) as p99_used,
    avg(l17_used) as avg_used, min(@timestamp) as first_seen, max(@timestamp) as last_seen
    by @log, l17_agent, l17_kind, l17_provisioned"""

SETTING_KEYS = ("min_invocations", "min_window_hours", "max_peak_utilization")
# Reference values (see the LLM-17 section of detectors/owner-d/README.md). The registry's LLM-17 defaults copy
# them; a test keeps the two equal.
REFERENCE_SETTINGS = {"min_invocations": 100, "min_window_hours": 24, "max_peak_utilization": 0.3}

REQUIRED_DATA_FIELDS = ("log_group", "workload", "agent", "capacity_kind", "unit", "window", "window_hours",
                        "provisioned", "invocations", "peak_used", "p99_used", "avg_used", "first_seen",
                        "last_seen", "other_sizes", "problems")

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/210",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/configuration-memory.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-examples.html",
)
LAMBDA_RECOMMENDATION = (
    "Lower the function's MemorySize toward the observed peak plus headroom, then confirm duration and cost at "
    "the new size (for example with AWS Lambda Power Tuning): Lambda allocates CPU in proportion to memory, so a "
    "CPU-bound function can run longer when it gets less memory. Agent and LLM-calling functions mostly wait on "
    "the network and rarely need memory sized for a theoretical maximum. Lambda scales out per request, so "
    "bursts are absorbed by more concurrent environments, not by a larger one."
)
AGENT_RECOMMENDATION = (
    "Size this capacity for the observed peak plus headroom instead of the theoretical maximum, and scale it "
    "with demand (autoscaling, queue-driven workers or serverless concurrency) so bursts are absorbed when they "
    "happen. Re-check after the busiest period of the week or month, because a peak outside the query window "
    "is not visible here."
)
LIMITATION = (
    "LLM-17 v1 reads Lambda REPORT lines in the text log format (@memorySize, @maxMemoryUsed; JSON-format "
    "platform.report records are not read) and JSON agent capacity lines carrying agent, capacity_kind, "
    "capacity_provisioned and capacity_used. Peak use is the largest value in the query window, so a peak "
    "outside the window, or capacity reserved for a known event, is not visible. Several functions sharing one "
    "log group cannot be told apart; only the most recently seen size of a workload is judged. Lambda memory "
    "also sets CPU, which this check does not measure. Provisioned concurrency and other settings that are not "
    "logged are not covered."
)

_ACCOUNT_PREFIX = re.compile(r"^\d{12}:")
_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,99}$")
_KIND_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
_NUMBER_FIELDS = ("l17_provisioned", "invocations", "peak_used", "p99_used", "avg_used")


class NormalizationError(ValueError):
    """A Logs Insights response cannot be normalized."""


def group_scope_id(log_group):
    return f"{SCOPE_PREFIX}{log_group}"


def scope_id_for(log_group, agent=None, capacity_kind=None):
    if agent is None:
        return f"{SCOPE_PREFIX}{log_group}#{LAMBDA}"
    return f"{SCOPE_PREFIX}{log_group}#agent/{agent}/{capacity_kind}"


def window_hours(window):
    """Hours between window.start and window.end (ISO 8601), or None when unknown."""
    if not isinstance(window, dict):
        return None
    try:
        start, end = (dt.datetime.fromisoformat(str(window.get(k)).replace("Z", "+00:00")) for k in ("start", "end"))
        hours = (end - start).total_seconds() / 3600
    except (TypeError, ValueError):
        return None
    return round(hours, 2) if hours > 0 else None


# --------------------------------------------------------------------------------------------------------
# Normalization: Logs Insights rows -> one telemetry source per workload
# --------------------------------------------------------------------------------------------------------


def _log_group_name(value):
    """`@log` is `<account id>:<log group name>`; the account ID is dropped."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _ACCOUNT_PREFIX.sub("", value.strip()) or None


def _number(value):
    """Logs Insights returns numbers as strings. Finite, nonnegative numbers only."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def _whole(value):
    return int(value) if value == int(value) else value


def _row_values(row, lambda_row):
    """(values, problem) for one stats row."""
    values = {key: _number(row.get(key)) for key in _NUMBER_FIELDS}
    bad = [key for key, value in values.items() if value is None]
    if bad:
        return None, ", ".join(bad) + " not a nonnegative number"
    if values["invocations"] < 1 or values["invocations"] != int(values["invocations"]):
        return None, "invocations is not a positive whole number"
    if values["l17_provisioned"] <= 0:
        return None, "provisioned capacity is not positive"
    peak = values["peak_used"]
    tolerance = 1e-9 * max(1.0, peak)
    if values["p99_used"] > peak + tolerance or values["avg_used"] > peak + tolerance:
        return None, "p99 or average use exceeds peak use"
    scale = BYTES_PER_MB if lambda_row else 1
    out = {"provisioned": values["l17_provisioned"] / scale, "invocations": int(values["invocations"]),
           "peak_used": peak / scale, "p99_used": values["p99_used"] / scale, "avg_used": values["avg_used"] / scale}
    for key in ("provisioned", "peak_used", "p99_used", "avg_used"):
        out[key] = _whole(round(out[key], 1 if lambda_row else 2))
    for key in ("first_seen", "last_seen"):
        out[key] = row[key] if isinstance(row.get(key), str) and row[key] else None
    return out, None


def _workload_data(log_group, key, configs, problems, window):
    agent, kind = key
    configs = sorted(configs, key=lambda c: (c["last_seen"] or "", c["invocations"]), reverse=True)
    current = configs[0] if configs else None
    return {
        "log_group": log_group,
        "workload": LAMBDA if agent is None else AGENT,
        "agent": agent,
        "capacity_kind": "memory" if agent is None else kind,
        "unit": "MB" if agent is None else "count",
        "window": window,
        "window_hours": window_hours(window),
        "provisioned": current["provisioned"] if current else None,
        "invocations": current["invocations"] if current else 0,
        "peak_used": current["peak_used"] if current else None,
        "p99_used": current["p99_used"] if current else None,
        "avg_used": current["avg_used"] if current else None,
        "first_seen": current["first_seen"] if current else None,
        "last_seen": current["last_seen"] if current else None,
        "other_sizes": [{k: c[k] for k in ("provisioned", "invocations", "peak_used", "last_seen")}
                        for c in configs[1:]],
        "problems": list(problems),
    }


def normalize_logs_insights(raw, *, settings=None):
    """Convert the log analyzer's raw Logs Insights dict ({"rows", "log_groups", "window", "truncated", ...}) into
    {"scope", "sources", "limitations"}. Each workload seen in a queried log group gets a scope item and a source;
    a queried group without any capacity record gets a scope item only. Problems that make a workload's
    aggregates incomplete go into its `problems` field, which the detector turns into an unevaluated scope item.
    Pure; drops account IDs."""
    if not isinstance(raw, dict):
        raise NormalizationError("raw Logs Insights data must be an object")
    rows, queried = raw.get("rows"), raw.get("log_groups")
    if not isinstance(rows, list) or not isinstance(queried, list) or not all(isinstance(g, str) for g in queried):
        raise NormalizationError("raw Logs Insights data needs a rows list and a log_groups list of names")
    groups = list(dict.fromkeys(g for g in queried if g))
    window = raw.get("window") if isinstance(raw.get("window"), dict) else None
    region = raw.get("region") or "ap-south-1"
    limitations, shared = [], []
    if raw.get("truncated"):
        shared.append("the query hit its row limit, so this workload's aggregates may be incomplete")
    if window_hours(window) is None:
        shared.append("the query window is unknown")
    configs, problems, unattributed, unknown, unnamed = {}, {}, 0, 0, {}
    for index, row in enumerate(rows):
        name = _log_group_name(row.get("@log")) if isinstance(row, dict) else None
        if name is None:
            unattributed += 1
            continue
        if name not in groups:
            unknown += 1
            continue
        agent, kind = row.get("l17_agent") or "", row.get("l17_kind") or ""
        if not isinstance(agent, str) or not isinstance(kind, str):
            unnamed[name] = unnamed.get(name, 0) + 1
            continue
        if not agent and not kind:
            key = (None, None)
        elif _AGENT_NAME.match(agent) and _KIND_NAME.match(kind):
            key = (agent, kind)
        else:
            unnamed[name] = unnamed.get(name, 0) + 1
            continue
        configs.setdefault((name, key), [])
        values, problem = _row_values(row, key[0] is None)
        if problem:
            problems.setdefault((name, key), []).append(f"row {index}: {problem}")
            continue
        configs[(name, key)].append(values)
    if unattributed:
        shared.append(f"{unattributed} result rows could not be attributed to a log group")
        limitations.append(f"LLM-17: {unattributed} Logs Insights rows had no @log value; no workload was evaluated")
    if unknown:
        limitations.append(f"LLM-17: {unknown} Logs Insights rows named a log group that was not queried; ignored")
    for name, count in sorted(unnamed.items()):
        limitations.append(f"{group_scope_id(name)}: {count} agent capacity row(s) without a simple agent name and "
                           "capacity_kind (letters, digits and _.:@/-); those workloads were not evaluated")
    scope, sources = [], []
    for name in groups:
        keys = sorted((k for (g, k) in configs if g == name), key=lambda k: (k[0] is not None, k))
        if not keys:
            scope.append(group_scope_id(name))
            continue
        for key in keys:
            data = _workload_data(name, key, configs[(name, key)], shared + problems.get((name, key), []), window)
            scope_id = scope_id_for(name, *key) if key[0] else scope_id_for(name)
            scope.append(scope_id)
            suffix = "lambda-memory" if key[0] is None else f"agent/{key[0]}/{key[1]}"
            sources.append({"source_id": f"logs-insights:{name}#{suffix}", "scope_id": scope_id,
                            "kind": SUPPORTED_KIND, "locator": f"logs-insights://{region}/log-group/{name}",
                            "data": data})
    return {"scope": scope, "sources": sources, "limitations": limitations}


# --------------------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------------------


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    if not _is_int(context["min_invocations"]) or context["min_invocations"] < 1:
        return None, "context.min_invocations must be a positive integer"
    if not _is_number(context["min_window_hours"]) or context["min_window_hours"] <= 0:
        return None, "context.min_window_hours must be a positive number"
    share = context["max_peak_utilization"]
    if not _is_number(share) or not 0 < share < 1:
        return None, "context.max_peak_utilization must be a number in (0, 1)"
    return {key: context[key] for key in SETTING_KEYS}, None


def _data_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    workload, name, agent, kind = data["workload"], data["log_group"], data["agent"], data["capacity_kind"]
    if not isinstance(name, str) or not name:
        problems.append("log_group must be a nonempty string")
    elif workload == LAMBDA:
        if agent is not None or data["unit"] != "MB":
            problems.append("a lambda-memory workload has no agent and unit MB")
        elif scope_id != scope_id_for(name):
            problems.append(f"scope id does not match the workload (expected {scope_id_for(name)!r})")
    elif workload == AGENT:
        if not (isinstance(agent, str) and _AGENT_NAME.match(agent) and isinstance(kind, str)
                and _KIND_NAME.match(kind)) or data["unit"] != "count":
            problems.append("an agent-capacity workload needs a simple agent name, capacity_kind and unit count")
        elif scope_id != scope_id_for(name, agent, kind):
            problems.append(f"scope id does not match the workload (expected {scope_id_for(name, agent, kind)!r})")
    else:
        problems.append(f"unsupported workload {str(workload)[:40]!r}")
    if not isinstance(data["problems"], list) or not all(isinstance(p, str) for p in data["problems"]):
        problems.append("problems must be a list of strings")
    elif data["problems"]:
        problems.extend(data["problems"])
    if data["window_hours"] != window_hours(data["window"]) or data["window_hours"] is None:
        problems.append("window must have ISO start/end timestamps and window_hours must match them")
    if not _is_int(data["invocations"]) or data["invocations"] < 0:
        problems.append("invocations must be a nonnegative integer")
    elif data["invocations"] and not problems:
        values = [data[k] for k in ("provisioned", "peak_used", "p99_used", "avg_used")]
        if not all(_is_number(v) for v in values) or data["provisioned"] <= 0:
            problems.append("provisioned, peak_used, p99_used and avg_used must be nonnegative numbers")
        elif data["p99_used"] > data["peak_used"] or data["avg_used"] > data["peak_used"]:
            problems.append("p99_used and avg_used cannot exceed peak_used")
    if not isinstance(data["other_sizes"], list):
        problems.append("other_sizes must be a list")
    return problems


def _evidence(source, *fields):
    return [{"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"], "field": f,
             "value": source["data"][f]} for f in fields]


def _fmt(value):
    return f"{value:g}" if isinstance(value, float) else str(value)


def _pct(value):
    return f"{value * 100:.1f}%"


def _describe(data):
    """(subject, unit label, event noun)."""
    if data["workload"] == LAMBDA:
        return f"Lambda log group {data['log_group']}", "MB", "invocations"
    return f"Agent {data['agent']} in log group {data['log_group']}", data["capacity_kind"], "logged runs"


def _finding(scope_id, source, settings, repository_id):
    data = source["data"]
    what, unit, events = _describe(data)
    of_memory = " of memory" if data["workload"] == LAMBDA else ""
    share = data["peak_used"] / data["provisioned"]
    window = data["window"]
    summary = (
        f"{what} provisions {_fmt(data['provisioned'])} {unit}{of_memory}, but its peak use over "
        f"{data['invocations']} {events} between {window['start']} and {window['end']} "
        f"({_fmt(data['window_hours'])} h) was {_fmt(data['peak_used'])} {unit} ({_pct(share)} of provisioned; "
        f"p99 {_fmt(data['p99_used'])}, average {_fmt(data['avg_used'])}), below the "
        f"{_pct(settings['max_peak_utilization'])} limit. "
        f"{_fmt(round(data['provisioned'] - data['peak_used'], 2))} {unit} stayed unused even at the peak."
    )
    lam = data["workload"] == LAMBDA
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, data["workload"]),
        "scope_id": scope_id,
        "identity": data["workload"],
        "summary": summary,
        "confidence": "medium",
        "recommendation": LAMBDA_RECOMMENDATION if lam else AGENT_RECOMMENDATION,
        "references": list(REFERENCES),
        "evidence": _evidence(source, "provisioned", "peak_used", "p99_used", "invocations", "window_hours"),
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes)."""
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [f"{scope_id}: no Lambda REPORT lines or agent capacity lines in the window; LLM-17 "
                           "requires provisioned and used capacity"]
    if len(telemetry) > 1:
        return False, [], [f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"]
    source = telemetry[0]
    problems = _data_problems(source.get("data"), scope_id)
    if problems:
        return False, [], [f"{scope_id}: not evaluated: " + "; ".join(problems)]
    data = source["data"]
    if data["window_hours"] < settings["min_window_hours"]:
        return False, [], [f"{scope_id}: the query window covers {_fmt(data['window_hours'])} hours, shorter than "
                           f"min_window_hours {_fmt(settings['min_window_hours'])}; not evaluated"]
    if data["invocations"] < settings["min_invocations"]:
        return False, [], [f"{scope_id}: only {data['invocations']} invocations at the current size in the window, "
                           f"fewer than min_invocations {settings['min_invocations']}; not evaluated"]
    notes, unit = [], _describe(data)[1]
    if data["other_sizes"]:
        sizes = ", ".join(_fmt(o.get("provisioned")) for o in data["other_sizes"] if isinstance(o, dict))
        notes.append(f"{scope_id}: other provisioned sizes were also seen in the window ({sizes} {unit}; "
                     "resized, or several workloads share the log group); only the most recent size, "
                     f"{_fmt(data['provisioned'])} {unit}, was judged")
    floor = LAMBDA_MIN_MEMORY_MB if data["workload"] == LAMBDA else AGENT_MIN_CAPACITY
    share = data["peak_used"] / data["provisioned"]
    if data["peak_used"] > data["provisioned"]:
        notes.append(f"{scope_id}: peak use {_fmt(data['peak_used'])} exceeds the provisioned "
                     f"{_fmt(data['provisioned'])} {unit} (under-provisioned or bursting); not flagged")
        return True, [], notes
    if share >= settings["max_peak_utilization"]:
        return True, [], notes
    if data["provisioned"] <= floor:
        notes.append(f"{scope_id}: peak use is {_pct(share)} of {_fmt(data['provisioned'])} {unit}, but "
                     f"that is already the smallest size ({floor} {unit}); not flagged")
        return True, [], notes
    return True, [_finding(scope_id, source, settings, repository_id)], notes


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
