"""LLM-12: per-agent isolated caches not shared across the fleet (CloudWatch Logs Insights).

Detector semantics version 1.0.0. Two parts:

* `LOGS_INSIGHTS_QUERY` runs on the Owner D log route (`owner-d-log-analyzer`, source `logs_insights`) over
  allowlisted log groups and a bounded window. It reads structured cache-lookup lines: a cache key
  (`cache_key`, `prompt_hash` or `request_hash`), a hit/miss outcome (`cache_hit` or `cache_status`) and, if
  present, the agent (`agent_id` or `instance_id`; otherwise the log stream, which is one execution environment
  or container). Three chained `stats` aggregate per (key, agent), then per key, then per log group, so at
  most two rows per log group leave CloudWatch Logs: one for keys that missed on several agents, one for the
  rest. Each row carries counts and two example keys.
* `normalize_logs_insights(raw, *, settings)` turns those rows into one telemetry source per
  `resource:log-group/<name>` scope item and replaces the example keys with SHA-256 prefixes, and
  `evaluate(payload)` checks contract v1 inputs built from them.

A key is a *cross-agent duplicate* when at least two agents missed it and the last of those agents' first misses
came at least `CONCURRENT_MISS_SECONDS` after the first one: the later agents missed a key that another agent
had already fetched (and cached in its own memory), so a shared cache would have served them. Each such key
adds (agents that missed it - 1) duplicated misses. A log group is flagged (`cross-agent-duplicate-misses`)
when at least `context.min_duplicated_misses` misses are duplicated and they are more than
`context.min_duplicated_miss_share` of its misses. Groups with fewer than `context.min_lookups` lookups, or
where no key was looked up by at least `context.min_agents` agents, are not evaluated. A group without cache
lookup lines is reported unavailable, never clean.
"""

from __future__ import annotations

import hashlib
import math
import re

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "LLM-12"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
RESOURCE_TYPE = "aws_cloudwatch_log_group"
SCOPE_PREFIX = "resource:log-group/"
IDENTITY = "cross-agent-duplicate-misses"

# Misses of one key on different agents whose first misses lie closer together than this are treated as
# concurrent (a shared cache without request coalescing would have missed them too). The value covers a
# typical LLM or embedding call; the query uses it in milliseconds.
CONCURRENT_MISS_SECONDS = 10
MAX_KEY_CHARS = 200  # longer keys are not recognized (log a hash instead)
MAX_AGENT_CHARS = 128
NO_MISS = 9999999999999  # epoch milliseconds in the year 2286: "no miss" for min()
KINDS = ("duplicated", "other")

# Field patterns, shared by the query and the tests. Logs Insights `parse` uses named groups `(?<name>...)`.
HIT_PATTERN = r'"(cache_hit|cacheHit)"\s*:\s*"?(?<o12_hit>true|false|True|False|TRUE|FALSE|1|0)"?\s*[,}]'
STATUS_PATTERN = r'"(cache_status|cacheStatus)"\s*:\s*"(?<o12_status>hit|miss|HIT|MISS|Hit|Miss)"'
KEY_PATTERN = (r'"(cache_key|cacheKey|prompt_hash|promptHash|request_hash|requestHash)"\s*:\s*"(?<o12_key>[^"\\]{1,'
               + str(MAX_KEY_CHARS) + r'})"')
AGENT_PATTERN = (r'"(agent_id|agentId|instance_id|instanceId)"\s*:\s*"?(?<o12_agent>[^",}\\\s]{1,'
                 + str(MAX_AGENT_CHARS) + r'})')
HIT_VALUES = r"^(true|True|TRUE|1|hit|HIT|Hit)$"
_DUPLICATED = f"o12_k_missed_agents >= 2 and o12_k_last - o12_k_first >= {CONCURRENT_MISS_SECONDS * 1000}"

# Read by the log analyzer (log_handler.collect_logs_insights). Field names are prefixed `o12_` so they cannot
# collide with fields Logs Insights discovers in JSON events. Every stage after the first refers only to the
# fields of the stage before it.
LOGS_INSIGHTS_QUERY = (
    r'filter @message like /"(cache_hit|cacheHit|cache_status|cacheStatus)"\s*:/' "\n"
    f"| parse @message /{HIT_PATTERN}/\n"
    f"| parse @message /{STATUS_PATTERN}/\n"
    f"| parse @message /{KEY_PATTERN}/\n"
    f"| parse @message /{AGENT_PATTERN}/\n"
    "| filter ispresent(o12_key) and (ispresent(o12_hit) or ispresent(o12_status))\n"
    f"| fields if(coalesce(o12_hit, o12_status) like /{HIT_VALUES}/, 0, 1) as o12_miss,\n"
    f"    if(coalesce(o12_hit, o12_status) like /{HIT_VALUES}/, {NO_MISS}, toMillis(@timestamp)) as o12_miss_ms,\n"
    "    coalesce(o12_agent, @logStream) as o12_who, if(ispresent(o12_agent), 1, 0) as o12_named\n"
    "| stats count(*) as o12_lookups, sum(o12_miss) as o12_misses, sum(o12_named) as o12_named_lookups,\n"
    "    min(o12_miss_ms) as o12_first_miss by @log, o12_key, o12_who\n"
    "| fields if(o12_misses > 0, 1, 0) as o12_missed, if(o12_misses > 0, o12_first_miss, 0) as o12_miss_last\n"
    "| stats sum(o12_lookups) as o12_k_lookups, sum(o12_misses) as o12_k_misses,"
    " sum(o12_named_lookups) as o12_k_named,\n"
    "    count(*) as o12_k_agents, sum(o12_missed) as o12_k_missed_agents, min(o12_first_miss) as o12_k_first,\n"
    "    max(o12_miss_last) as o12_k_last by @log, o12_key\n"
    f'| fields if({_DUPLICATED}, "duplicated", "other") as o12_kind,\n'
    f"    if({_DUPLICATED}, o12_k_missed_agents - 1, 0) as o12_k_dup\n"
    "| stats count(*) as keys, sum(o12_k_lookups) as lookups, sum(o12_k_misses) as misses,\n"
    "    sum(o12_k_dup) as duplicated_misses, sum(o12_k_named) as named_lookups, max(o12_k_agents) as max_agents,\n"
    "    max(o12_k_missed_agents) as max_missed_agents, sortsFirst(o12_key) as example_first,\n"
    "    sortsLast(o12_key) as example_last by @log, o12_kind\n"
    "| sort @log asc, o12_kind asc"
)

SETTING_KEYS = ("min_lookups", "min_agents", "min_duplicated_misses", "min_duplicated_miss_share")
# Reference values (team choices; see the LLM-12 section of detectors/owner-d/README.md). The registry copies
# them as LLM12_DEFAULTS; a test keeps the two equal.
REFERENCE_SETTINGS = {
    "min_lookups": 50,
    "min_agents": 2,
    "min_duplicated_misses": 5,
    "min_duplicated_miss_share": 0.2,
}

REQUIRED_DATA_FIELDS = ("resource_id", "resource_type", "lookups", "misses", "keys", "duplicated_keys",
                        "duplicated_misses", "max_agents_per_key", "named_agent_lookups", "example_key_hashes",
                        "concurrent_miss_seconds", "problems")
_COUNT_FIELDS = ("lookups", "misses", "keys", "duplicated_keys", "duplicated_misses", "max_agents_per_key",
                 "named_agent_lookups")

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/205",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://aws.amazon.com/blogs/database/optimize-llm-response-costs-and-latency-with-effective-caching/",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-Stats.html",
)
RECOMMENDATION = (
    "Move the response/embedding cache out of each agent's memory into a cache layer the whole fleet shares "
    "(for example Amazon ElastiCache or DynamoDB with a TTL, or a tool-result cache behind Amazon Bedrock "
    "AgentCore Gateway), keyed by the same normalized request hash, as AWS AGENTSUS02-BP02 recommends. "
    "Coalesce concurrent misses for one key so only one agent calls the model. Keep a small in-process cache "
    "in front of the shared one if latency matters."
)
LIMITATION = (
    "LLM-12 v1 counts cache lookup lines that carry a key (cache_key/prompt_hash/request_hash, at most "
    f"{MAX_KEY_CHARS} characters) and a hit/miss field (cache_hit/cache_status). Agents are agent_id/instance_id, "
    "or the log stream when neither is logged. A key's later agents count as duplicated misses when their first "
    f"misses come {CONCURRENT_MISS_SECONDS} s or more after the first agent's; the query cannot see cache TTLs, so "
    "a miss after the other agent's entry expired is also counted, and concurrent misses among three or more "
    "agents are counted once the spread exceeds the window. The agent count is the most agents that looked up "
    "one key, a lower bound on the fleet size."
)

_ACCOUNT_PREFIX = re.compile(r"^\d{12}:")
_ROW_COUNTS = ("keys", "lookups", "misses", "duplicated_misses", "named_lookups", "max_agents", "max_missed_agents")
_HASH = re.compile(r"^[0-9a-f]{16}$")


# --------------------------------------------------------------------------------------------------------
# Normalization: Logs Insights rows -> one telemetry source per log group
# --------------------------------------------------------------------------------------------------------


def scope_id_for(log_group):
    return f"{SCOPE_PREFIX}{log_group}"


def key_hash(key):
    """16-hex SHA-256 prefix of a cache key: example keys never leave the analyzer in clear text."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _log_group_name(value):
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
    return {"resource_id": name, "resource_type": RESOURCE_TYPE, "lookups": 0, "misses": 0, "keys": 0,
            "duplicated_keys": 0, "duplicated_misses": 0, "max_agents_per_key": 0, "max_missed_agents_per_key": 0,
            "named_agent_lookups": 0, "example_key_hashes": [], "concurrent_miss_seconds": CONCURRENT_MISS_SECONDS,
            "problems": []}


def _row_problem(row):
    """(values, None) or (None, why the row cannot be used)."""
    kind = row.get("o12_kind")
    if kind not in KINDS:
        return None, f"o12_kind={str(kind)[:20]!r} is not one of {', '.join(KINDS)}"
    values = {"kind": kind}
    for key in _ROW_COUNTS:
        values[key] = _count_value(row.get(key))
        if values[key] is None:
            return None, f"{key}={str(row.get(key))[:20]!r} is not a nonnegative whole number"
    if values["keys"] == 0 or values["lookups"] < values["keys"] or values["max_agents"] == 0:
        return None, "a row must cover at least one key, one lookup per key and one agent"
    if values["misses"] > values["lookups"] or values["named_lookups"] > values["lookups"]:
        return None, "misses or named lookups exceed lookups"
    if values["max_missed_agents"] > values["max_agents"]:
        return None, "max_missed_agents exceeds max_agents"
    if kind == "duplicated":
        if values["duplicated_misses"] < values["keys"] or values["duplicated_misses"] > values["misses"] \
                or values["max_missed_agents"] < 2:
            return None, "duplicated keys must each add at least one duplicated miss"
    elif values["duplicated_misses"]:
        return None, "the other-keys row must not carry duplicated misses"
    return values, None


def normalize_logs_insights(raw, *, settings=None):
    """Convert the log analyzer's raw Logs Insights dict ({"rows", "log_groups", "window", "truncated", ...})
    into {"scope", "sources", "limitations"}. Every queried log group gets a scope item and a source, with zero
    counts when it logged no cache lookups; problems that make a group's counts incomplete go into its
    `problems` field, which the detector turns into an unevaluated scope item. Pure; drops account IDs and
    replaces the example keys with SHA-256 prefixes."""
    if not isinstance(raw, dict):
        return {"scope": [], "sources": [], "limitations": ["LLM-12: the Logs Insights response is not an object"]}
    rows = raw.get("rows")
    queried = raw.get("log_groups")
    names = list(dict.fromkeys(n for n in (queried if isinstance(queried, list) else []) if isinstance(n, str) and n))
    if not isinstance(rows, list):
        return {"scope": [scope_id_for(n) for n in names], "sources": [],
                "limitations": ["LLM-12: the Logs Insights response has no rows list; no log group was evaluated"]}
    groups = {name: _empty(name) for name in names}
    shared, limitations, seen, unattributed = [], [], set(), 0
    if raw.get("truncated"):
        shared.append("the query hit its row limit, so this group's counts may be incomplete")
    for index, row in enumerate(rows):
        name = _log_group_name(row.get("@log")) if isinstance(row, dict) else None
        if name is None or (names and name not in groups):
            unattributed += 1
            continue
        data = groups.setdefault(name, _empty(name))
        values, problem = _row_problem(row)
        if problem is None and (name, values["kind"]) in seen:
            problem = f"a second {values['kind']} row for this log group"
        if problem:
            data["problems"].append(f"row {index}: {problem}")
            continue
        seen.add((name, values["kind"]))
        data["keys"] += values["keys"]
        data["lookups"] += values["lookups"]
        data["misses"] += values["misses"]
        data["named_agent_lookups"] += values["named_lookups"]
        data["max_agents_per_key"] = max(data["max_agents_per_key"], values["max_agents"])
        data["max_missed_agents_per_key"] = max(data["max_missed_agents_per_key"], values["max_missed_agents"])
        if values["kind"] == "duplicated":
            data["duplicated_keys"] = values["keys"]
            data["duplicated_misses"] = values["duplicated_misses"]
            examples = [row.get(k) for k in ("example_first", "example_last")]
            data["example_key_hashes"] = list(dict.fromkeys(key_hash(k) for k in examples if isinstance(k, str) and k))
    if unattributed:
        shared.append(f"{unattributed} result rows could not be attributed to a queried log group")
        limitations.append(f"LLM-12: {unattributed} Logs Insights rows had no queried @log value; every group's "
                           "counts may be incomplete")
    window = raw.get("window") if isinstance(raw.get("window"), dict) else None
    sources = []
    for name in sorted(groups):
        data = groups[name]
        data["problems"] = shared + data["problems"]
        data["window"] = window
        sources.append({"source_id": f"logs-insights:{name}", "scope_id": scope_id_for(name), "kind": SUPPORTED_KIND,
                        "locator": f"logs-insights://{raw.get('region') or 'ap-south-1'}/log-group/{name}",
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
    for key, low in (("min_lookups", 1), ("min_agents", 2), ("min_duplicated_misses", 1)):
        if not _is_int(context[key]) or context[key] < low:
            return None, f"context.{key} must be an integer of at least {low}"
    share = context["min_duplicated_miss_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not math.isfinite(share) or not 0 <= share < 1:
        return None, "context.min_duplicated_miss_share must be a number in [0, 1)"
    return {key: context[key] for key in SETTING_KEYS}, None


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
    if data["concurrent_miss_seconds"] != CONCURRENT_MISS_SECONDS:
        problems.append(f"concurrent_miss_seconds must be {CONCURRENT_MISS_SECONDS} (data from another query shape)")
    bad = [field for field in _COUNT_FIELDS if not _is_int(data[field]) or data[field] < 0]
    if bad:
        problems.append("not nonnegative integers: " + ", ".join(bad))
    elif not (data["duplicated_misses"] <= data["misses"] <= data["lookups"]
              and data["duplicated_keys"] <= data["keys"] <= data["lookups"]
              and data["duplicated_keys"] <= data["duplicated_misses"]
              and data["named_agent_lookups"] <= data["lookups"]):
        problems.append("counts are inconsistent (expected duplicated_misses <= misses <= lookups and "
                        "duplicated_keys <= keys)")
    hashes = data["example_key_hashes"]
    if not isinstance(hashes, list) or not all(isinstance(h, str) and _HASH.match(h) for h in hashes):
        problems.append("example_key_hashes must be a list of 16-hex SHA-256 prefixes")
    if not isinstance(data["problems"], list) or not all(isinstance(p, str) for p in data["problems"]):
        problems.append("problems must be a list of strings")
    elif data["problems"]:
        problems.extend(data["problems"])
    return problems


def _pct(value):
    return f"{value * 100:.1f}%"


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def _finding(scope_id, source, share, repository_id):
    data = source["data"]
    named = data["named_agent_lookups"] == data["lookups"]
    hit_rate = (data["lookups"] - data["misses"]) / data["lookups"]
    examples = ", ".join(data["example_key_hashes"]) or "none recorded"
    summary = (
        f"Log group {data['resource_id']}: {data['duplicated_misses']} of {data['misses']} cache misses "
        f"({_pct(share)}) were for keys another agent had already missed, and so fetched and cached, at least "
        f"{CONCURRENT_MISS_SECONDS} s earlier: {_plural(data['duplicated_keys'], 'key')} missed on several agents, "
        f"up to {data['max_agents_per_key']} agents per key. A cache shared across the fleet would have served "
        f"them. Hit rate {_pct(hit_rate)} over {data['lookups']} lookups. Example keys (SHA-256 prefixes): "
        f"{examples}." + ("" if named else " Some lookups carry no agent_id/instance_id, so their agent is the "
                                           "log stream.")
    )
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": "medium" if named else "low",
        "recommendation": RECOMMENDATION,
        "references": list(REFERENCES),
        "evidence": [{"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"],
                      "field": field, "value": data[field]}
                     for field in ("duplicated_misses", "misses", "duplicated_keys", "max_agents_per_key",
                                   "example_key_hashes", "lookups")],
    }


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes)."""
    telemetry = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [f"{scope_id}: no telemetry source supplied; LLM-12 requires Logs Insights cache counts"]
    if len(telemetry) > 1:
        return False, [], [f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"]
    source = telemetry[0]
    problems = _data_problems(source.get("data"), scope_id)
    if problems:
        return False, [], [f"{scope_id}: not evaluated: " + "; ".join(problems)]
    data = source["data"]
    if data["lookups"] == 0:
        return False, [], [(f"{scope_id}: no cache lookup lines with a key (cache_key/prompt_hash/request_hash) and "
                           "a hit/miss field (cache_hit/cache_status) in the window; not evaluated")]
    if data["lookups"] < settings["min_lookups"]:
        return False, [], [(f"{scope_id}: only {data['lookups']} cache lookups in the window, fewer than min_lookups "
                           f"{settings['min_lookups']}; not evaluated")]
    if data["max_agents_per_key"] < settings["min_agents"]:
        return False, [], [(f"{scope_id}: no key was looked up by min_agents {settings['min_agents']} or more agents "
                           f"(at most {data['max_agents_per_key']}): one agent, or agents with disjoint keys; "
                           "not evaluated")]
    share = data["duplicated_misses"] / data["misses"] if data["misses"] else 0.0
    if data["duplicated_misses"] >= settings["min_duplicated_misses"] and share > settings["min_duplicated_miss_share"]:
        return True, [_finding(scope_id, source, share, repository_id)], []
    notes = []
    if data["duplicated_misses"]:
        notes.append(f"{scope_id}: {data['duplicated_misses']} of {data['misses']} misses ({_pct(share)}) were "
                     f"cross-agent duplicates, within min_duplicated_misses {settings['min_duplicated_misses']} / "
                     f"min_duplicated_miss_share {_pct(settings['min_duplicated_miss_share'])}; not flagged")
    return True, [], notes


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
