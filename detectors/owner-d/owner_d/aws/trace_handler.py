"""AWS Lambda `owner-d-trace-analyzer`: read-only AWS X-Ray plumbing for Owner D trace checks (e.g. LLM-10).

Source `traces`: GetTraceSummaries over a bounded window (X-Ray allows at most 24 hours) with an optional
filter expression (event xray.filter_expression, else the check module's XRAY_FILTER_EXPRESSION), then
BatchGetTraces in batches of 5 trace ids. Trace count, pages and time are all bounded; the analyzer
never invokes the traced services and generates no traffic.

Event:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "role_arn": "arn:aws:iam::<client>:role/owner-d-telemetry-readonly", "external_id": "optional",
     "checks": ["LLM-10"],
     "xray": {"filter_expression": "service(\\"agent-fn\\")", "lookback_minutes": 60,
              "start": "<ISO>", "end": "<ISO>", "max_traces": 50, "max_pages": 5},
     "settings": {"LLM-10": {...}}, "scope_per_payload": 50, "dry_run": false}
    {"probe": ["traces"], ...}  -> collect only, return counts, publish nothing
"""
from __future__ import annotations

import datetime as dt

from owner_d.aws import common

ANALYZER = "owner-d-trace-analyzer"
SOURCE = "owner-d.trace-analyzer"
TRACES_PER_CALL = 5  # BatchGetTraces limit
DEFAULT_LOOKBACK_MINUTES = 60
MAX_WINDOW = dt.timedelta(hours=24)  # GetTraceSummaries rejects longer ranges
DEFAULT_MAX_TRACES = 50  # headroom over LLM-10's min_traces (10) for traces without GenAI calls
MAX_TRACES = 100
DEFAULT_PAGES = 5
MAX_PAGES = 20
BATCH_PAGES = 3


def fetch_traces(xray, *, start, end, filter_expression=None, max_traces=DEFAULT_MAX_TRACES,
                 max_pages=DEFAULT_PAGES, deadline=None):
    """(summaries, traces, unprocessed, truncated, notes); every loop is bounded."""
    notes, summaries, seen, token = [], [], set(), None
    truncated = False
    for page_number in range(max_pages):
        params = {"StartTime": start, "EndTime": end, "Sampling": False}
        if filter_expression:
            params["FilterExpression"] = filter_expression
        if token:
            params["NextToken"] = token
        page = xray.get_trace_summaries(**params)
        for summary in page.get("TraceSummaries") or []:
            if summary["Id"] not in seen and len(summaries) < max_traces:
                seen.add(summary["Id"])
                summaries.append(summary)
        token = page.get("NextToken")
        if len(summaries) >= max_traces and token:
            truncated = True
            notes.append(f"more traces matched; only the first {max_traces} were read")
            break
        if not token:
            break
        if page_number == max_pages - 1:
            truncated = True
            notes.append(f"GetTraceSummaries stopped after {max_pages} pages; later traces were not read")
    ids = [s["Id"] for s in summaries]
    traces, unprocessed = [], []
    for i in range(0, len(ids), TRACES_PER_CALL):
        if deadline is not None and deadline.remaining() < 5:
            truncated = True
            unprocessed.extend(ids[i:])
            notes.append("BatchGetTraces stopped early to stay within the Lambda timeout")
            break
        items, more = common.paginate(xray.batch_get_traces, items_key="Traces", max_pages=BATCH_PAGES,
                                      TraceIds=ids[i:i + TRACES_PER_CALL])
        traces.extend(items)
        if more:
            truncated = True
            notes.append("a BatchGetTraces batch had more pages than allowed; some segments were not read")
    return summaries, traces, unprocessed, truncated, notes


def collect_traces(event, readers, *, module=None, deadline=None):
    cfg = event.get("xray") or {}
    if not isinstance(cfg, dict):
        raise ValueError("xray must be an object")
    start, end = common.time_window(cfg, lookback_key="lookback_minutes", default=DEFAULT_LOOKBACK_MINUTES,
                                    maximum=MAX_WINDOW, unit=dt.timedelta(minutes=1))
    max_traces = common.bounded_int(cfg.get("max_traces"), DEFAULT_MAX_TRACES, 1, MAX_TRACES, "xray.max_traces")
    max_pages = common.bounded_int(cfg.get("max_pages"), DEFAULT_PAGES, 1, MAX_PAGES, "xray.max_pages")
    expression = cfg.get("filter_expression") or getattr(module, "XRAY_FILTER_EXPRESSION", None)
    if expression is not None and (not isinstance(expression, str) or len(expression) > 2000):
        raise ValueError("xray.filter_expression must be a string of at most 2000 characters")
    summaries, traces, unprocessed, truncated, notes = fetch_traces(
        readers.client("xray"), start=start, end=end, filter_expression=expression, max_traces=max_traces,
        max_pages=max_pages, deadline=deadline)
    return {
        "TraceSummaries": summaries,
        "Traces": traces,  # segment Documents are JSON strings, as returned by X-Ray
        "UnprocessedTraceIds": unprocessed,
        "window": {"start": common.iso(start), "end": common.iso(end)},
        "region": common.region(),
        "truncated": truncated,
        "collection": {"source": "xray-batchgettraces", "filter_expression": expression,
                       "lookback_minutes": round((end - start).total_seconds() / 60, 2), "max_traces": max_traces},
        "counts": {"trace_summaries": len(summaries), "traces": len(traces)},
        "limitations": notes,
    }


COLLECTORS = {"traces": collect_traces}


def lambda_handler(event, context=None):
    return common.run_analyzer(event, context, analyzer=ANALYZER, source=SOURCE, collectors=COLLECTORS)
