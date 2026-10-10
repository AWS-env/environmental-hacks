"""CloudWatch activity collection for INF-04 (read-only; source activity_metrics, owner-d-telemetry-analyzer).

Reads one activity metric per resource with GetMetricData over a bounded window of whole UTC days, and
optionally discovers RDS instances with ListMetrics. These are the only two calls, both already granted to the
analyzer role and to owner-d-telemetry-readonly, so INF-04 needs no new IAM.

    type    metric (statistic)                         dimension              idle resource publishes
    lambda  AWS/Lambda Invocations (Sum)               FunctionName           nothing
    alb     AWS/ApplicationELB RequestCount (Sum)      LoadBalancer           nothing
    rds     AWS/RDS DatabaseConnections (Maximum)      DBInstanceIdentifier   zeros, every minute while it runs

Lambda and Application Load Balancer metrics exist only while there is traffic, and ListMetrics only lists
metrics with data in the last two weeks, so idle functions and load balancers cannot be discovered: the event
lists them (for example from the INF-04 static findings or the team's inventory). RDS instances that are
running report DatabaseConnections, so `discover` (RecentlyActive, i.e. reporting now) finds them.

The pure normalizer is owner_d.inf04.normalize_activity_metrics.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from owner_d.aws import common


@dataclass(frozen=True)
class ActivityType:
    namespace: str
    metric_name: str
    statistic: str
    dimension: str
    resource_type: str
    metric: str  # INF-04 metric key (owner_d.inf04.METRICS)
    pattern: re.Pattern
    discoverable: bool


ACTIVITY_TYPES = {
    "lambda": ActivityType("AWS/Lambda", "Invocations", "Sum", "FunctionName", "aws_lambda_function", "invocations",
                           re.compile(r"[A-Za-z0-9_-]{1,64}"), False),
    "alb": ActivityType("AWS/ApplicationELB", "RequestCount", "Sum", "LoadBalancer", "aws_lb", "request_count",
                        re.compile(r"app/[A-Za-z0-9-]{1,32}/[0-9a-f]{16}"), False),
    "rds": ActivityType("AWS/RDS", "DatabaseConnections", "Maximum", "DBInstanceIdentifier", "aws_rds_db_instance",
                        "database_connections", re.compile(r"[A-Za-z][A-Za-z0-9-]{0,62}"), True),
}
KEYS = {"lambda": "name", "alb": "name", "rds": "id"}
PERIOD = 86400  # one datapoint per UTC day
DEFAULT_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 90
MAX_RESOURCES = 200
DEFAULT_DISCOVER = 50
DEFAULT_LIST_PAGES = 5
MAX_LIST_PAGES = 20
QUERIES_PER_CALL = 500  # GetMetricData limit
MAX_METRIC_DATA_PAGES = 10
NOT_CONFIGURED = ("INF-04: no activity resources were requested (event \"activity\"), so CloudWatch activity was "
                  "not read")


def _resource(kind, value):
    spec = ACTIVITY_TYPES[kind]
    return {"type": kind, "id": f"{kind}/{value}", "namespace": spec.namespace, "metric_name": spec.metric_name,
            "statistic": spec.statistic, "dimensions": [{"Name": spec.dimension, "Value": value}],
            "resource_type": spec.resource_type, "metric": spec.metric}


def parse_resource(spec: dict) -> dict:
    """{"type": "lambda", "name": n} | {"type": "alb", "name": "app/<name>/<id>"} | {"type": "rds", "id": i}."""
    if not isinstance(spec, dict) or spec.get("type") not in ACTIVITY_TYPES:
        raise ValueError(f"activity resource type must be one of {sorted(ACTIVITY_TYPES)}")
    kind = spec["type"]
    value = spec.get(KEYS[kind])
    if not isinstance(value, str) or not ACTIVITY_TYPES[kind].pattern.fullmatch(value):
        raise ValueError(f"{kind} activity resources need a valid {KEYS[kind]!r}")
    return _resource(kind, value)


def discover(cloudwatch, cfg: dict):
    """ListMetrics (RecentlyActive) for discoverable types. Returns (resources, truncated, limitations)."""
    types = cfg.get("types") or [k for k, v in ACTIVITY_TYPES.items() if v.discoverable]
    if not isinstance(types, list) or not set(types) <= set(ACTIVITY_TYPES):
        raise ValueError(f"activity.discover.types must be a subset of {sorted(ACTIVITY_TYPES)}")
    hidden = sorted(t for t in types if not ACTIVITY_TYPES[t].discoverable)
    if hidden:
        raise ValueError(f"activity.discover cannot find idle {', '.join(hidden)} resources: CloudWatch publishes "
                         "their metrics only when there is traffic; list them in activity.resources")
    limit = common.bounded_int(cfg.get("max_resources"), DEFAULT_DISCOVER, 1, MAX_RESOURCES,
                               "activity.discover.max_resources")
    pages = common.bounded_int(cfg.get("max_pages"), DEFAULT_LIST_PAGES, 1, MAX_LIST_PAGES,
                               "activity.discover.max_pages")
    found, truncated, notes = {}, False, []
    for kind in types:
        spec = ACTIVITY_TYPES[kind]
        listed, more = common.paginate(cloudwatch.list_metrics, items_key="Metrics", max_pages=pages,
                                       Namespace=spec.namespace, MetricName=spec.metric_name,
                                       Dimensions=[{"Name": spec.dimension}], RecentlyActive="PT3H")
        if more:
            truncated = True
            notes.append(f"discovery of {kind} stopped after {pages} ListMetrics pages; some resources were not read")
        for metric in listed:
            dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
            if set(dims) != {spec.dimension} or not spec.pattern.fullmatch(dims[spec.dimension]):
                continue  # aggregated series (e.g. by engine) are not one resource
            resource = _resource(kind, dims[spec.dimension])
            found.setdefault(resource["id"], resource)
    resources = sorted(found.values(), key=lambda r: r["id"])
    if len(resources) > limit:
        truncated = True
        notes.append(f"discovered {len(resources)} resources; only the first {limit} (by id) were read")
        resources = resources[:limit]
    return resources, truncated, notes


def fetch_activity(cloudwatch, resources, start, end, max_pages=MAX_METRIC_DATA_PAGES):
    """One daily series per resource: {id: {"timestamps", "values", "complete", "messages"}}."""
    series = {}
    for offset in range(0, len(resources), QUERIES_PER_CALL):
        batch = resources[offset:offset + QUERIES_PER_CALL]
        ids, queries = {}, []
        for i, resource in enumerate(batch):
            qid = f"q{i}"
            ids[qid] = resource["id"]
            queries.append({"Id": qid, "ReturnData": True, "MetricStat": {
                "Metric": {"Namespace": resource["namespace"], "MetricName": resource["metric_name"],
                           "Dimensions": resource["dimensions"]},
                "Period": PERIOD, "Stat": resource["statistic"]}})
            series[resource["id"]] = {"timestamps": [], "values": [], "complete": True, "messages": []}
        items, more = common.paginate(cloudwatch.get_metric_data, items_key="MetricDataResults", max_pages=max_pages,
                                      MetricDataQueries=queries, StartTime=start, EndTime=end,
                                      ScanBy="TimestampAscending")
        final = {}
        for item in items:
            target = series[ids[item["Id"]]]
            target["timestamps"].extend(common.iso(common.parse_time(t)) for t in item.get("Timestamps") or [])
            target["values"].extend(item.get("Values") or [])
            target["messages"].extend(m.get("Value", "") for m in item.get("Messages") or [])
            final[item["Id"]] = item.get("StatusCode", "Complete")
        for qid, rid in ids.items():
            if more or final.get(qid, "Complete") != "Complete":
                series[rid]["complete"] = False
    return series


def collect_activity_metrics(event, readers, *, module=None, deadline=None):
    cfg = event.get("activity")
    if cfg is None:
        return {"window": None, "period_seconds": PERIOD, "window_days": None, "region": common.region(),
                "resources": [], "series": {}, "truncated": False, "collection": None,
                "counts": {"resources": 0}, "limitations": [NOT_CONFIGURED]}
    if not isinstance(cfg, dict):
        raise ValueError("activity must be an object")
    start, end = common.time_window(cfg, lookback_key="lookback_days", default=DEFAULT_LOOKBACK_DAYS,
                                    maximum=dt.timedelta(days=MAX_LOOKBACK_DAYS), unit=dt.timedelta(days=1))
    end = dt.datetime.fromtimestamp(int(end.timestamp()) // PERIOD * PERIOD, dt.timezone.utc)
    start = dt.datetime.fromtimestamp(int(start.timestamp()) // PERIOD * PERIOD, dt.timezone.utc)
    if not start < end:
        raise ValueError("activity window must cover at least one whole UTC day")
    explicit = cfg.get("resources") or []
    if not isinstance(explicit, list) or len(explicit) > MAX_RESOURCES:
        raise ValueError(f"activity.resources must be a list of at most {MAX_RESOURCES} items")
    discover_cfg = cfg.get("discover")
    if discover_cfg is True:
        discover_cfg = {}
    if discover_cfg is not None and not isinstance(discover_cfg, dict):
        raise ValueError("activity.discover must be an object (or true)")
    if not explicit and discover_cfg is None:
        raise ValueError("activity needs resources or discover")
    resources = {r["id"]: r for r in (parse_resource(spec) for spec in explicit)}
    cloudwatch = readers.client("cloudwatch")
    truncated, notes = False, []
    if discover_cfg is not None:
        found, truncated, notes = discover(cloudwatch, discover_cfg)
        for resource in found:
            resources.setdefault(resource["id"], resource)
    resources = list(resources.values())[:MAX_RESOURCES]
    series = fetch_activity(cloudwatch, resources, start, end)
    window_days = round((end - start).total_seconds() / 86400, 2)
    return {
        "window": {"start": common.iso(start), "end": common.iso(end)},
        "period_seconds": PERIOD,
        "window_days": window_days,
        "region": common.region(),
        "resources": resources,
        "series": series,
        "truncated": truncated,
        "collection": {"source": "cloudwatch-getmetricdata", "period_seconds": PERIOD, "lookback_days": window_days},
        "counts": {"resources": len(resources), "with_datapoints": sum(1 for s in series.values() if s["values"])},
        "limitations": notes,
    }
