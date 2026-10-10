"""LLM-12: per-agent isolated caches not shared across the fleet (CloudWatch Logs Insights).

Detector semantics version 1.0.0. Two parts:

* `LOGS_INSIGHTS_QUERY` runs on the Owner D log route (`owner-d-log-analyzer`, source `logs_insights`) over
  allowlisted log groups and a bounded window. It reads the structured cache-lookup lines agents already log
  (`cache_result` hit/miss, a cache key or prompt hash, the agent and the logical cache name) and counts, per
  cache name, backend and key, the lookups, the hits and the distinct *cache holders* that missed the key. A
  holder is one agent in one log stream (one process or Lambda execution environment), so it is the smallest
  unit that can own an in-memory cache. A second `stats` folds the keys into buckets by the number of holders
  that missed them. It returns counts only; keys, prompts and agent names never leave CloudWatch Logs.
* `normalize_logs_insights(raw, *, settings)` turns those rows into one telemetry source per
  `resource:llm-cache/<cache name>` scope item, and `evaluate(payload)` checks contract v1 inputs built from them.

A cache name is flagged (`isolated-agent-caches`) when separate holders keep missing the same keys: the misses a
cache shared by the fleet would have served (for each key, every holder that missed it after the first) are more
than `context.min_redundant_misses` and more than `context.min_redundant_share` of the cache's lookups. Repeat
misses of one holder (its own TTL or eviction) are not counted. Caches with fewer than `context.min_lookups`
lookups are not evaluated, and caches whose lookups are logged against a shared backend
(`context.shared_backends`) are treated as already shared. The detector never calls AWS.
"""

from __future__ import annotations

import math
import re

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "LLM-12"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
RESOURCE_TYPE = "llm_cache"
SCOPE_PREFIX = "resource:llm-cache/"
IDENTITY = "isolated-agent-caches"
UNSET_BACKEND = "(unset)"

# Keys are folded into buckets by how many holders missed them; bucket BUCKET_CAP holds every key missed by
# BUCKET_CAP or more holders. Sums per bucket are exact, so the redundant-miss count does not depend on the cap.
BUCKET_CAP = 20
MAX_CACHE_NAME = 64
MAX_BACKEND = 32

# Read by the log analyzer (log_handler.collect_logs_insights). Field names are prefixed `o12_` so they cannot
# collide with fields Logs Insights discovers in JSON events. A hit line marks itself "-", which can never equal
# a holder (holders contain spaces), so the distinct non-hit marks are the holders that missed the key.
LOGS_INSIGHTS_QUERY = f"""fields tolower(coalesce(cache_result, cache_status, "")) as o12_result,
    coalesce(cache_key_hash, prompt_hash, cache_key, "") as o12_key,
    substr(coalesce(cache_name, "default"), 0, {MAX_CACHE_NAME}) as o12_cache,
    substr(tolower(coalesce(cache_backend, "")), 0, {MAX_BACKEND}) as o12_backend,
    coalesce(agent_id, agent_name, "") as o12_agent
| filter (o12_result = "hit" or o12_result = "miss") and o12_key != ""
| fields concat(@log, " ", @logStream, " ", o12_agent) as o12_holder,
    if(o12_result = "hit", "-", concat(@log, " ", @logStream, " ", o12_agent)) as o12_miss_mark,
    if(o12_result = "hit", 1, 0) as o12_hit
| stats count(*) as o12_lookups, sum(o12_hit) as o12_hits, count_distinct(o12_holder) as o12_holders,
    count_distinct(o12_miss_mark) as o12_marks, count_distinct(o12_agent) as o12_agents
    by o12_cache, o12_backend, o12_key
| fields o12_marks - if(o12_hits > 0, 1, 0) as o12_missed_by,
    least(o12_marks - if(o12_hits > 0, 1, 0), {BUCKET_CAP}) as o12_bucket
| stats count(*) as keys, sum(o12_lookups) as lookups, sum(o12_hits) as hits, sum(o12_missed_by) as missing_holders,
    max(o12_holders) as max_holders, max(o12_agents) as max_agents by o12_cache, o12_backend, o12_bucket"""

# Backends whose name says the cache lives in the agent process: a finding on them is `high` confidence.
LOCAL_BACKENDS = frozenset({"memory", "in-memory", "inmemory", "in_memory", "in-process", "inprocess", "local",
                            "process", "lru", "lru_cache", "functools", "dict", "ttlcache", "cachetools"})

SETTING_KEYS = ("min_lookups", "min_redundant_misses", "min_redundant_share", "shared_backends")
# Reference values (team choices; see the LLM-12 section of detectors/owner-d/README.md). The registry copies
# them as LLM12_DEFAULTS; a test keeps the two equal.
REFERENCE_SETTINGS = {
    "min_lookups": 100,
    "min_redundant_misses": 20,
    "min_redundant_share": 0.1,
    "shared_backends": ["redis", "valkey", "elasticache", "memorydb", "memcached", "dynamodb", "momento"],
}

REQUIRED_DATA_FIELDS = ("resource_id", "resource_type", "bucket_cap", "lookups", "hits", "keys", "backends",
                        "missed_by_histogram", "max_holders_per_key", "max_agents_per_key", "problems")
HISTOGRAM_FIELDS = ("missed_by", "keys", "lookups", "hits", "missing_holders")
BACKEND_FIELDS = ("lookups", "hits", "keys")

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/205",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html",
    "https://aws.amazon.com/elasticache/",
)
RECOMMENDATION = (
    "Serve these lookups from one cache the whole agent fleet reads, instead of a cache inside each agent "
    "process: for example Amazon ElastiCache (Valkey or Redis OSS), Amazon MemoryDB or a DynamoDB table with "
    "TTL, keyed by a hash of the normalized request (model, parameters and prompt) and with a TTL matched to how "
    "quickly the answers go stale. Keep an in-process cache only as a small first tier in front of the shared "
    "one. Keep the tenant, user or permission scope in the key when answers must not cross those boundaries."
)
LIMITATION = (
    "LLM-12 v1 reads structured cache-lookup log lines (cache_result or cache_status hit/miss, a cache_key_hash, "
    "prompt_hash or cache_key, and optionally agent_id or agent_name, cache_name and cache_backend). Caches "
    "that do not log lookups this way are not seen. A holder is one agent in one log stream; misses of the same "
    "key by different holders are counted as misses a shared cache would have served, which overstates them "
    "when the key's entry would have expired in a shared cache between the two misses (keep the window close to "
    "the cache TTL) or when the holders' answers must stay separate. Per-agent hit rates are not reported: "
    "Logs Insights allows two stats commands, which the cross-agent key comparison uses."
)

_ACCOUNT_PREFIX = re.compile(r"^\d{12}:")
_COUNT_COLUMNS = ("o12_bucket", "keys", "lookups", "hits", "missing_holders", "max_holders", "max_agents")


# --------------------------------------------------------------------------------------------------------
# Normalization: Logs Insights rows -> one telemetry source per cache name
# --------------------------------------------------------------------------------------------------------


def scope_id_for(cache_name):
    return f"{SCOPE_PREFIX}{cache_name}"


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


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = value.strip()[:limit]
    return value or None


def _empty(name):
    return {"resource_id": name, "resource_type": RESOURCE_TYPE, "bucket_cap": BUCKET_CAP, "lookups": 0, "hits": 0,
            "keys": 0, "backends": {}, "missed_by_histogram": {}, "max_holders_per_key": 0, "max_agents_per_key": 0,
            "problems": []}


def _row_problem(row):
    """(values, None), or (None, why the row cannot be used). Checks the row against the query's arithmetic."""
    values = {}
    for column in _COUNT_COLUMNS:
        values[column] = _count_value(row.get(column))
        if values[column] is None:
            return None, f"{column}={str(row.get(column))[:20]!r} is not a nonnegative whole number"
    bucket, keys, lookups, hits = values["o12_bucket"], values["keys"], values["lookups"], values["hits"]
    missing = values["missing_holders"]
    if bucket > BUCKET_CAP or keys == 0 or lookups < keys or hits > lookups:
        return None, f"bucket {bucket} with {keys} keys, {lookups} lookups and {hits} hits is outside the query's shape"
    exact = missing == bucket * keys if bucket < BUCKET_CAP else missing >= bucket * keys
    if not exact or missing > lookups - hits or (bucket == 0 and hits != lookups):
        return None, f"missing holders in bucket {bucket} do not match its keys and misses"
    if values["max_holders"] < max(1, min(bucket, BUCKET_CAP)) or values["max_agents"] < 1:
        return None, f"holder counts in bucket {bucket} are below the bucket's bound"
    return values, None


def normalize_logs_insights(raw, *, settings=None):
    """Convert the log analyzer's raw Logs Insights dict ({"rows", "log_groups", "window", "truncated", ...})
    into {"scope", "sources", "limitations"}: one scope item and source per cache name seen in the rows.
    Problems that make a cache's counts incomplete are listed in its `problems` field, which the detector turns
    into an unevaluated scope item. Pure; drops account IDs and never receives keys, prompts or agent names."""
    if not isinstance(raw, dict):
        return {"scope": [], "sources": [], "limitations": ["LLM-12: the Logs Insights response is not an object"]}
    rows = raw.get("rows")
    queried = raw.get("log_groups")
    groups = sorted({_ACCOUNT_PREFIX.sub("", n) for n in (queried if isinstance(queried, list) else [])
                     if isinstance(n, str) and n})
    if not isinstance(rows, list):
        return {"scope": [], "sources": [],
                "limitations": ["LLM-12: the Logs Insights response has no rows list; no cache was evaluated"]}
    caches, shared_problems, limitations = {}, [], []
    if raw.get("truncated"):
        shared_problems.append("the query hit its row limit, so this cache's counts may be incomplete")
    unattributed = 0
    for index, row in enumerate(rows):
        name = _text(row.get("o12_cache"), MAX_CACHE_NAME) if isinstance(row, dict) else None
        if name is None:
            unattributed += 1
            continue
        data = caches.setdefault(name, _empty(name))
        values, problem = _row_problem(row)
        if problem:
            data["problems"].append(f"row {index}: {problem}")
            continue
        backend = _text(row.get("o12_backend"), MAX_BACKEND) or UNSET_BACKEND
        entry = data["backends"].setdefault(backend.lower(), dict.fromkeys(BACKEND_FIELDS, 0))
        for field in BACKEND_FIELDS:
            entry[field] += values[field]
        bucket = data["missed_by_histogram"].setdefault(values["o12_bucket"], dict.fromkeys(HISTOGRAM_FIELDS[1:], 0))
        for field in HISTOGRAM_FIELDS[1:]:
            bucket[field] += values[field]
        data["lookups"] += values["lookups"]
        data["hits"] += values["hits"]
        data["keys"] += values["keys"]
        data["max_holders_per_key"] = max(data["max_holders_per_key"], values["max_holders"])
        data["max_agents_per_key"] = max(data["max_agents_per_key"], values["max_agents"])
    if unattributed:
        shared_problems.append(f"{unattributed} result rows had no cache name")
        limitations.append(f"LLM-12: {unattributed} Logs Insights rows had no o12_cache value; every cache's counts "
                           "may be incomplete")
    if not caches:
        limitations.append("LLM-12: no cache-lookup lines (cache_result or cache_status hit/miss with a cache key) "
                           "were found in the queried log groups; nothing to evaluate")
    window = raw.get("window") if isinstance(raw.get("window"), dict) else None
    region = raw.get("region") or "ap-south-1"
    sources = []
    for name in sorted(caches):
        data = caches[name]
        data["problems"] = shared_problems + data["problems"]
        data["missed_by_histogram"] = [{"missed_by": b, **v} for b, v in sorted(data["missed_by_histogram"].items())]
        data["backends"] = dict(sorted(data["backends"].items()))
        data["log_groups"] = groups
        data["window"] = window
        sources.append({"source_id": f"logs-insights:llm-cache/{name}", "scope_id": scope_id_for(name),
                        "kind": SUPPORTED_KIND, "locator": f"logs-insights://{region}/llm-cache/{name}",
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
    for key in ("min_lookups", "min_redundant_misses"):
        if not _is_int(context[key]) or context[key] < (1 if key == "min_lookups" else 0):
            return None, f"context.{key} must be a {'positive' if key == 'min_lookups' else 'nonnegative'} integer"
    share = context["min_redundant_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not math.isfinite(share) or not 0 <= share < 1:
        return None, "context.min_redundant_share must be a number in [0, 1)"
    backends = context["shared_backends"]
    if not isinstance(backends, list) or not all(isinstance(b, str) and b.strip() for b in backends):
        return None, "context.shared_backends must be a list of nonempty strings (may be empty)"
    return {**{key: context[key] for key in SETTING_KEYS[:3]},
            "shared_backends": frozenset(b.strip().lower() for b in backends)}, None


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
        problems.append(f"scope id does not match resource_id (expected '{SCOPE_PREFIX}<cache name>')")
    if data["bucket_cap"] != BUCKET_CAP:
        problems.append(f"bucket_cap must be {BUCKET_CAP} (data from another query shape)")
    for field in ("lookups", "hits", "keys", "max_holders_per_key", "max_agents_per_key"):
        if not _is_int(data[field]) or data[field] < 0:
            problems.append(f"{field} must be a nonnegative integer")
    backends = data["backends"]
    if not isinstance(backends, dict) or not all(isinstance(k, str) and k and _counts_ok(v, BACKEND_FIELDS)
                                                 for k, v in backends.items()):
        problems.append("backends must map backend names to lookups/hits/keys counts")
    histogram = data["missed_by_histogram"]
    if not isinstance(histogram, list) or not all(_counts_ok(b, HISTOGRAM_FIELDS) for b in histogram):
        problems.append("missed_by_histogram must be a list of bucket counts")
    elif not problems:
        for field in ("lookups", "hits", "keys"):
            if sum(b[field] for b in histogram) != data[field] or sum(v[field] for v in backends.values()) != data[field]:
                problems.append(f"missed_by_histogram and backends must add up to {field}")
        buckets = [b["missed_by"] for b in histogram]
        if any(b > BUCKET_CAP for b in buckets) or len(set(buckets)) != len(buckets):
            problems.append("missed_by_histogram buckets must be unique and within the query's range")
        for b in histogram:
            low = b["missed_by"] * b["keys"]
            if b["missing_holders"] < low or (b["missed_by"] < BUCKET_CAP and b["missing_holders"] != low) \
                    or b["missing_holders"] > b["lookups"] - b["hits"]:
                problems.append(f"missed_by bucket {b['missed_by']} does not match its keys and misses")
                break
        if data["hits"] > data["lookups"]:
            problems.append("hits must not exceed lookups")
    if not isinstance(data["problems"], list) or not all(isinstance(p, str) for p in data["problems"]):
        problems.append("problems must be a list of strings")
    elif data["problems"]:
        problems.extend(data["problems"])
    return problems


def redundant_misses(data):
    """(keys missed by two or more holders, misses a cache shared by those holders would have served)."""
    shared = [b for b in data["missed_by_histogram"] if b["missed_by"] >= 2]
    return sum(b["keys"] for b in shared), sum(b["missing_holders"] - b["keys"] for b in shared)


def _evidence(source, *fields):
    return [{"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"], "field": f,
             "value": source["data"][f]} for f in fields]


def _pct(value):
    return f"{value * 100:.1f}%"


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def _window(data):
    window = data.get("window")
    if isinstance(window, dict) and window.get("start") and window.get("end"):
        return f" between {window['start']} and {window['end']}"
    return " in the window"


def _finding(scope_id, source, cross_keys, redundant, repository_id):
    data = source["data"]
    lookups, hits = data["lookups"], data["hits"]
    misses = lookups - hits
    backends = set(data["backends"])
    local = bool(backends) and backends <= LOCAL_BACKENDS
    summary = (
        f"Cache {data['resource_id']}: {cross_keys} of {_plural(data['keys'], 'key')} looked up{_window(data)} were "
        f"missed by two or more separate agent caches (an agent in one log stream), up to "
        f"{data['max_holders_per_key']} caches for one key. {redundant} of the {misses} misses ({_pct(redundant / lookups)} "
        f"of {lookups} lookups) repeat a miss another agent's cache had already paid for. Observed hit rate "
        f"{_pct(hits / lookups)}; one cache shared by these agents could reach about {_pct((hits + redundant) / lookups)}."
    )
    if local:
        summary += " The lookups are logged against in-process backends: " + ", ".join(sorted(backends)) + "."
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": "high" if local else "medium",
        "recommendation": RECOMMENDATION,
        "references": list(REFERENCES),
        "evidence": _evidence(source, "missed_by_histogram", "lookups", "hits", "backends"),
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
    if data["lookups"] < settings["min_lookups"]:
        return False, [], [f"{scope_id}: only {data['lookups']} cache lookups in the window, fewer than min_lookups "
                           f"{settings['min_lookups']}; not evaluated"]
    shared = sorted(b for b, v in data["backends"].items() if b in settings["shared_backends"] and v["lookups"])
    if shared:
        return True, [], [f"{scope_id}: lookups are logged against a shared backend ({', '.join(shared)}), so the "
                          "fleet already has a shared cache tier; not flagged"]
    cross_keys, redundant = redundant_misses(data)
    share = redundant / data["lookups"]
    if redundant > settings["min_redundant_misses"] and share > settings["min_redundant_share"]:
        return True, [_finding(scope_id, source, cross_keys, redundant, repository_id)], []
    notes = []
    if redundant:
        notes.append(f"{scope_id}: {redundant} misses on keys another agent's cache had already missed "
                     f"({_pct(share)} of {data['lookups']} lookups), within min_redundant_misses "
                     f"{settings['min_redundant_misses']} or min_redundant_share "
                     f"{_pct(settings['min_redundant_share'])}; not flagged")
    if data["max_holders_per_key"] < 2:
        notes.append(f"{scope_id}: every key was looked up by a single agent cache, so sharing cannot be judged "
                     "from these lookups")
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
