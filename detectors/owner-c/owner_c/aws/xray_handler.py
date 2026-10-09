"""AWS Lambda `owner-c-xray-parser`: confirm JS-01 (await in loops) with the client's AWS X-Ray traces.

Read-only telemetry route from docs/ARCHITECTURE_FLOWS.md: the Lambda reads existing traces with
GetTraceSummaries / BatchGetTraces (optionally through a read-only role in the client's account), normalizes
them into serial runs per traced function, evaluates JS-01 and publishes the contract results. It never
invokes the client's functions and generates no traffic.

Event:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "source": {"files": [...]} | {"s3": {"bucket", "key"}},                  # repo zip
     "xray": {"function_files": {"orders-fn": "src/orders.js"},               # traced name -> repo file
              "lookback_minutes": 60, "start": "<ISO>", "end": "<ISO>",       # window (start/end win)
              "max_traces": 50, "role_arn": "arn:aws:iam::<client>:role/..."},
     "settings": {"min_serial_calls": 3, "min_serial_seconds": 0.05},
     "include_tests": false, "dry_run": false}
"""
from __future__ import annotations

import datetime

from owner_c.aws import common
from owner_c.connector import build_inputs, select_files
from owner_c.normalize import normalize_all
from owner_c.runner import evaluate

TRACES_PER_CALL = 5  # BatchGetTraces limit


def _xray_client(role_arn=None):
    if not role_arn:
        return common.client("xray")
    key = f"xray:{role_arn}"
    if key not in common._clients:
        import boto3  # provided by the Lambda runtime

        creds = common.client("sts").assume_role(RoleArn=role_arn, RoleSessionName="owner-c-xray-reader",
                                                 DurationSeconds=900)["Credentials"]
        common._clients[key] = boto3.client(
            "xray", aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"])
    return common._clients[key]


def _window(cfg):
    end = datetime.datetime.fromisoformat(cfg["end"]) if cfg.get("end") else datetime.datetime.now(datetime.timezone.utc)
    start = (datetime.datetime.fromisoformat(cfg["start"]) if cfg.get("start")
             else end - datetime.timedelta(minutes=int(cfg.get("lookback_minutes", 60))))
    return start, end


def fetch_traces(cfg: dict) -> dict:
    """Read-only: list trace ids per traced function, then fetch the full traces."""
    xray = _xray_client(cfg.get("role_arn"))
    start, end = _window(cfg)
    limit = int(cfg.get("max_traces", 50))
    trace_ids = []
    for name in cfg["function_files"]:
        token = None
        while len(trace_ids) < limit:
            kwargs = {"StartTime": start, "EndTime": end, "FilterExpression": f'service("{name}")', "Sampling": False}
            if token:
                kwargs["NextToken"] = token
            page = xray.get_trace_summaries(**kwargs)
            trace_ids += [t["Id"] for t in page.get("TraceSummaries", []) if t["Id"] not in trace_ids]
            token = page.get("NextToken")
            if not token:
                break
    trace_ids = trace_ids[:limit]
    traces = []
    for i in range(0, len(trace_ids), TRACES_PER_CALL):
        token = None
        while True:
            kwargs = {"TraceIds": trace_ids[i:i + TRACES_PER_CALL]}
            if token:
                kwargs["NextToken"] = token
            page = xray.batch_get_traces(**kwargs)
            traces += [{"Id": t["Id"], "Segments": [{"Id": s["Id"], "Document": s["Document"]} for s in t["Segments"]]}
                       for t in page.get("Traces", [])]
            token = page.get("NextToken")
            if not token:
                break
    return {"traces": traces, "function_files": cfg["function_files"]}


def lambda_handler(event, context=None):
    repository_id, commit_sha, scan_id = common.read_identity(event)
    cfg = event.get("xray") or {}
    if not cfg.get("function_files"):
        raise ValueError("event needs xray.function_files (traced function name -> repo file)")
    include_tests = bool(event.get("include_tests"))
    files = select_files(common.load_files(event.get("source", {})), include_tests)
    artifacts = normalize_all({"xray": fetch_traces(cfg)}, files)

    results = []
    for batch in common.batches(files):
        for payload in build_inputs(repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id, files=batch,
                                    artifacts=artifacts, include_tests=include_tests, settings=event.get("settings"),
                                    checks=["JS-01"]):
            results.append(evaluate(payload))
    published = common.publish_if_enabled(event, results)
    return common.log_summary("xray-parser", {
        "scan_id": scan_id, "published": published, "results": common.summarize(results),
        "functions_matched": len(artifacts.get("xray") or {})})
