"""AWS Lambda `owner-d-log-analyzer`: read-only CloudWatch Logs plumbing for Owner D log checks.

Sources (see registry.py for the raw shapes):
  log_groups     DescribeLogGroups (bounded pages, optional name prefix) and, if asked for,
                 ListTagsForResource per group (bounded), e.g. OBS-07 retention audit
  logs_insights  one Logs Insights query per check (module LOGS_INSIGHTS_QUERY) over allowlisted
                 log groups, a bounded time window and a query timeout. Queries run one at a time,
                 so at most one query is in flight per invocation; a query that does not finish in
                 time is stopped (StopQuery) and the check fails rather than reporting partial rows

Logs Insights bills per GB scanned, so the window is capped (MAX_LOOKBACK_HOURS) and only
log groups matching LOG_GROUP_ALLOWLIST (comma-separated fnmatch patterns) can be queried.

Event:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "role_arn": "arn:aws:iam::<client>:role/owner-d-telemetry-readonly", "external_id": "optional",
     "checks": ["OBS-07"],                                         # default: every registered log check
     "log_groups": {"prefix": "/aws/lambda/", "max_pages": 5, "include_tags": false},
     "logs": {"log_groups": ["/aws/lambda/fn"] | omitted (= allowlisted groups found by DescribeLogGroups),
              "prefix": "/aws/lambda/", "lookback_hours": 24, "start": "<ISO>", "end": "<ISO>",
              "limit": 1000, "timeout_seconds": 60},
     "settings": {"OBS-07": {...}}, "scope_per_payload": 50, "dry_run": false}
    {"probe": ["log_groups"], ...}  -> collect only, return counts, publish nothing
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import os

from owner_d.aws import common

ANALYZER = "owner-d-log-analyzer"
SOURCE = "owner-d.log-analyzer"
DEFAULT_PAGES = 5
MAX_PAGES = 20
PAGE_SIZE = 50  # DescribeLogGroups maximum
MAX_TAG_LOOKUPS = 100
MAX_QUERY_GROUPS = 50  # StartQuery accepts at most 50 log groups
DEFAULT_LOOKBACK_HOURS = 24
MAX_LOOKBACK_HOURS = 168
DEFAULT_ROW_LIMIT = 1000
MAX_ROW_LIMIT = 10_000
DEFAULT_QUERY_TIMEOUT = 60
MAX_QUERY_TIMEOUT = 240
POLL_SECONDS = 1.0
WILDCARDS = set("*?[]")


def allowlist():
    return [p.strip() for p in os.environ.get("LOG_GROUP_ALLOWLIST", "").split(",") if p.strip()]


def allowed(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _strip_arns(group):
    return {k: v for k, v in group.items() if k not in ("arn", "logGroupArn")}


def describe_log_groups(logs, *, prefix=None, max_pages=DEFAULT_PAGES):
    """Raw DescribeLogGroups responses, at most max_pages. Returns (pages, truncated)."""
    pages, token = [], None
    for _ in range(max_pages):
        params = {"limit": PAGE_SIZE}
        if prefix:
            params["logGroupNamePrefix"] = prefix
        if token:
            params["nextToken"] = token
        page = logs.describe_log_groups(**params)
        pages.append({"logGroups": page.get("logGroups") or []})
        token = page.get("nextToken")
        if not token:
            return pages, False
    return pages, True


def _tag_arn(group):
    arn = group.get("logGroupArn") or group.get("arn") or ""
    return arn[:-2] if arn.endswith(":*") else arn


def collect_log_groups(event, readers, *, module=None, deadline=None):
    cfg = event.get("log_groups") or {}
    if not isinstance(cfg, dict):
        raise ValueError("log_groups must be an object")
    pages_limit = common.bounded_int(cfg.get("max_pages"), DEFAULT_PAGES, 1, MAX_PAGES, "log_groups.max_pages")
    prefix = cfg.get("prefix")
    if prefix is not None and (not isinstance(prefix, str) or not prefix):
        raise ValueError("log_groups.prefix must be a nonempty string")
    logs = readers.client("logs")
    pages, truncated = describe_log_groups(logs, prefix=prefix, max_pages=pages_limit)
    groups = [g for page in pages for g in page["logGroups"]]
    notes = [f"DescribeLogGroups stopped after {pages_limit} pages; later log groups were not read"] if truncated else []
    tags = None
    if cfg.get("include_tags"):
        tags = {}
        for group in groups[:MAX_TAG_LOOKUPS]:
            if deadline is not None and deadline.remaining() < 5:
                notes.append("tag lookups stopped early to stay within the Lambda timeout")
                break
            try:
                tags[group["logGroupName"]] = logs.list_tags_for_resource(resourceArn=_tag_arn(group)).get("tags", {})
            except Exception as exc:  # AccessDenied etc.: tags stay "not collected" for that group
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
                notes.append(f"ListTagsForResource failed for {group['logGroupName']} ({code}); tags not collected")
        if len(groups) > MAX_TAG_LOOKUPS:
            notes.append(f"tags were read for the first {MAX_TAG_LOOKUPS} log groups only")
    return {
        "pages": pages,  # raw responses (contain ARNs): normalizer input only, never published as-is
        "logGroups": [_strip_arns(g) for g in groups],
        "tags": tags,
        "window": None,
        "region": common.region(),
        "truncated": truncated,
        "collection": {"source": "cloudwatch-logs-describeloggroups", "prefix": prefix, "max_pages": pages_limit,
                       "include_tags": bool(cfg.get("include_tags"))},
        "counts": {"log_groups": len(groups), "tagged": len(tags or {})},
        "limitations": notes,
    }


def run_insights_query(logs, *, log_groups, query, start, end, limit, timeout_seconds):
    """StartQuery, poll GetQueryResults until Complete; StopQuery and raise when the timeout passes."""
    query_id = logs.start_query(logGroupNames=log_groups, startTime=int(start.timestamp()),
                                endTime=int(end.timestamp()), queryString=query, limit=limit)["queryId"]
    began = common._monotonic()
    while True:
        response = logs.get_query_results(queryId=query_id)
        status = response.get("status")
        if status == "Complete":
            rows = [{c["field"]: c.get("value") for c in row if c.get("field") != "@ptr"}
                    for row in response.get("results") or []]
            return {"rows": rows, "statistics": response.get("statistics") or {}, "status": status}
        if status not in ("Scheduled", "Running"):
            raise RuntimeError(f"Logs Insights query ended with status {status}")
        if common._monotonic() - began >= timeout_seconds:
            try:
                logs.stop_query(queryId=query_id)
            finally:
                raise TimeoutError(f"Logs Insights query did not finish within {timeout_seconds:.0f}s; it was stopped")
        common._sleep(POLL_SECONDS)


def resolve_query_groups(logs, cfg, patterns):
    if not patterns:
        raise ValueError("no log groups are allowlisted (LOG_GROUP_ALLOWLIST is empty)")
    requested = cfg.get("log_groups")
    if requested is not None:
        if not isinstance(requested, list) or not requested or len(requested) > MAX_QUERY_GROUPS:
            raise ValueError(f"logs.log_groups must list 1-{MAX_QUERY_GROUPS} log group names")
        for name in requested:
            if not isinstance(name, str) or not name or WILDCARDS & set(name):
                raise ValueError("logs.log_groups must be exact log group names")
            if not allowed(name, patterns):
                raise ValueError(f"log group {name} is not allowlisted for queries")
        return list(dict.fromkeys(requested)), []
    pages, truncated = describe_log_groups(logs, prefix=cfg.get("prefix"), max_pages=DEFAULT_PAGES)
    names = [g["logGroupName"] for page in pages for g in page["logGroups"] if allowed(g["logGroupName"], patterns)]
    notes = ["log group discovery was truncated; some allowlisted groups were not queried"] if truncated else []
    if len(names) > MAX_QUERY_GROUPS:
        notes.append(f"{len(names)} allowlisted log groups matched; only the first {MAX_QUERY_GROUPS} were queried")
        names = names[:MAX_QUERY_GROUPS]
    if not names:
        raise ValueError("no allowlisted log groups were found to query")
    return names, notes


def collect_logs_insights(event, readers, *, module=None, deadline=None):
    cfg = event.get("logs") or {}
    if not isinstance(cfg, dict):
        raise ValueError("logs must be an object")
    query = getattr(module, "LOGS_INSIGHTS_QUERY", None) if module is not None else cfg.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("a Logs Insights check must define LOGS_INSIGHTS_QUERY (probe: logs.query)")
    start, end = common.time_window(cfg, lookback_key="lookback_hours", default=DEFAULT_LOOKBACK_HOURS,
                                    maximum=dt.timedelta(hours=MAX_LOOKBACK_HOURS), unit=dt.timedelta(hours=1))
    limit = common.bounded_int(cfg.get("limit"), DEFAULT_ROW_LIMIT, 1, MAX_ROW_LIMIT, "logs.limit")
    timeout = common.bounded_int(cfg.get("timeout_seconds"), DEFAULT_QUERY_TIMEOUT, 5, MAX_QUERY_TIMEOUT,
                                 "logs.timeout_seconds")
    if deadline is not None:
        timeout = min(timeout, deadline.remaining())
        if timeout < 5:
            raise TimeoutError("not enough Lambda time left to run a Logs Insights query")
    logs = readers.client("logs")
    groups, notes = resolve_query_groups(logs, cfg, allowlist())
    out = run_insights_query(logs, log_groups=groups, query=query, start=start, end=end, limit=limit,
                             timeout_seconds=timeout)
    truncated = len(out["rows"]) >= limit
    if truncated:
        notes.append(f"Logs Insights returned the {limit}-row limit; more matching rows may exist")
    stats = out["statistics"]
    return {
        **out,
        "log_groups": groups,
        "query": query,
        "window": {"start": common.iso(start), "end": common.iso(end)},
        "region": common.region(),
        "truncated": truncated,
        "collection": {"source": "cloudwatch-logs-insights", "lookback_hours": round((end - start).total_seconds() / 3600, 2),
                       "limit": limit, "log_groups": sorted(groups)},
        "counts": {"rows": len(out["rows"]), "log_groups": len(groups),
                   "bytes_scanned": stats.get("bytesScanned"), "records_scanned": stats.get("recordsScanned")},
        "limitations": notes,
    }


COLLECTORS = {"log_groups": collect_log_groups, "logs_insights": collect_logs_insights}


def lambda_handler(event, context=None):
    return common.run_analyzer(event, context, analyzer=ANALYZER, source=SOURCE, collectors=COLLECTORS)
