"""CloudWatch metrics collection (read-only) and the INF-01 normalizer.

Collectors (owner-d-telemetry-analyzer):
  collect_cpu_metrics   explicit resources and/or ListMetrics discovery, then GetMetricData Average and
                        Maximum CPUUtilization series per resource over a bounded window (source cpu_metrics)
  collect_list_metrics  bounded ListMetrics pages for metric-inventory checks such as OBS-06 (source metrics)

normalize_cpu_metrics turns the cpu_metrics raw dict into the normalized CloudWatch CPU summary INF-01
expects (detectors/owner-d/README.md, INF-01 > Input). It is pure, so it is tested without AWS.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass

from owner_d.aws import common


@dataclass(frozen=True)
class ResourceType:
    namespace: str
    metric: str  # CPUUtilization; Lambda only lists Invocations for discovery
    dimensions: tuple
    resource_type: str
    capacity_unit: str
    cpu: bool


RESOURCE_TYPES = {
    "ec2": ResourceType("AWS/EC2", "CPUUtilization", ("InstanceId",), "aws_ec2_instance", "instance", True),
    "ecs": ResourceType("AWS/ECS", "CPUUtilization", ("ClusterName", "ServiceName"), "aws_ecs_service", "service", True),
    "lambda": ResourceType("AWS/Lambda", "Invocations", ("FunctionName",), "aws_lambda_function", "function", False),
}
EC2_ID = re.compile(r"i-[0-9a-f]{8,17}")
NAME = re.compile(r"[A-Za-z0-9._:/-]{1,255}")
DEFAULT_LOOKBACK_DAYS = 15  # one day of headroom over INF-01's 14-day minimum window
MAX_LOOKBACK_DAYS = 30
DEFAULT_PERIOD = 3600
MAX_RESOURCES = 200
DEFAULT_DISCOVER = 50
RESOURCES_PER_CALL = 250  # GetMetricData accepts 500 queries; two per resource
MAX_METRIC_DATA_PAGES = 10
DEFAULT_LIST_PAGES = 5
MAX_LIST_PAGES = 20
LAMBDA_NOTE = ("AWS/Lambda publishes no CPU utilization metric and Lambda Insights is not read; "
               "INF-01 v1 evaluates cpu_utilization only")


# ---- resources ----------------------------------------------------------------------------------

def _resource(kind, values, capacity=None, unit=None):
    spec = RESOURCE_TYPES[kind]
    dims = [{"Name": n, "Value": v} for n, v in zip(spec.dimensions, values)]
    resource = {"type": kind, "id": f"{kind}/{'/'.join(values)}", "dimensions": dims,
                "provisioned_capacity": 1, "capacity_unit": spec.capacity_unit}
    if capacity is not None:
        if isinstance(capacity, bool) or not isinstance(capacity, (int, float)) or not 0 < capacity < 1e6:
            raise ValueError(f"{resource['id']}: provisioned_capacity must be a positive number")
        resource["provisioned_capacity"] = capacity
        resource["capacity_unit"] = unit if isinstance(unit, str) and unit.strip() else "vcpu"
    return resource


def parse_resource(spec: dict) -> dict:
    """{"type": "ec2", "id": "i-..."} | {"type": "ecs", "cluster": c, "service": s} | {"type": "lambda", "name": n},
    with optional provisioned_capacity + capacity_unit (default: 1 instance / service / function)."""
    if not isinstance(spec, dict) or spec.get("type") not in RESOURCE_TYPES:
        raise ValueError(f"resource type must be one of {sorted(RESOURCE_TYPES)}")
    kind = spec["type"]
    if kind == "ec2":
        values = [spec.get("id")]
        if not isinstance(values[0], str) or not EC2_ID.fullmatch(values[0]):
            raise ValueError("ec2 resources need an instance id (i-...)")
    elif kind == "ecs":
        values = [spec.get("cluster"), spec.get("service")]
    else:
        values = [spec.get("name")]
    if not all(isinstance(v, str) and NAME.fullmatch(v) for v in values):
        raise ValueError(f"{kind} resource has a missing or invalid name")
    return _resource(kind, values, spec.get("provisioned_capacity"), spec.get("capacity_unit"))


def discover(cloudwatch, cfg: dict):
    """ListMetrics per resource type. Returns (resources, truncated, limitations)."""
    types = cfg.get("types") or ["ec2", "ecs", "lambda"]
    if not isinstance(types, list) or not set(types) <= set(RESOURCE_TYPES):
        raise ValueError(f"discover.types must be a subset of {sorted(RESOURCE_TYPES)}")
    limit = common.bounded_int(cfg.get("max_resources"), DEFAULT_DISCOVER, 1, MAX_RESOURCES, "discover.max_resources")
    pages = common.bounded_int(cfg.get("max_pages"), DEFAULT_LIST_PAGES, 1, MAX_LIST_PAGES, "discover.max_pages")
    params = {"RecentlyActive": "PT3H"} if cfg.get("recently_active", True) else {}
    found, truncated, notes = {}, False, []
    for kind in types:
        spec = RESOURCE_TYPES[kind]
        metrics, more = common.paginate(cloudwatch.list_metrics, items_key="Metrics", max_pages=pages,
                                        Namespace=spec.namespace, MetricName=spec.metric,
                                        Dimensions=[{"Name": n} for n in spec.dimensions], **params)
        if more:
            truncated = True
            notes.append(f"discovery of {kind} stopped after {pages} ListMetrics pages; some resources were not read")
        for metric in metrics:
            dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
            if set(dims) != set(spec.dimensions):
                continue  # aggregated series (e.g. by InstanceType) are not one resource
            resource = _resource(kind, [dims[n] for n in spec.dimensions])
            found.setdefault(resource["id"], resource)
    resources = sorted(found.values(), key=lambda r: r["id"])
    if len(resources) > limit:
        truncated = True
        notes.append(f"discovered {len(resources)} resources; only the first {limit} (by id) were read")
        resources = resources[:limit]
    return resources, truncated, notes


# ---- GetMetricData --------------------------------------------------------------------------------

def _query(qid, resource, stat, period):
    spec = RESOURCE_TYPES[resource["type"]]
    return {"Id": qid, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": spec.namespace, "MetricName": spec.metric, "Dimensions": resource["dimensions"]},
        "Period": period, "Stat": stat}}


def fetch_cpu_series(cloudwatch, resources, start, end, period, max_pages=MAX_METRIC_DATA_PAGES):
    """Average and Maximum series per CPU resource: {id: {"average": {...}, "maximum": {...}, "complete", "messages"}}."""
    cpu = [r for r in resources if RESOURCE_TYPES[r["type"]].cpu]
    series = {}
    for offset in range(0, len(cpu), RESOURCES_PER_CALL):
        batch = cpu[offset:offset + RESOURCES_PER_CALL]
        ids = {}
        queries = []
        for i, resource in enumerate(batch):
            for stat, prefix in (("Average", "avg"), ("Maximum", "max")):
                qid = f"{prefix}{i}"
                ids[qid] = (resource["id"], stat.lower())
                queries.append(_query(qid, resource, stat, period))
            series[resource["id"]] = {"average": {"timestamps": [], "values": []},
                                      "maximum": {"timestamps": [], "values": []},
                                      "complete": True, "messages": []}
        pages, more = common.paginate(cloudwatch.get_metric_data, items_key="MetricDataResults",
                                      max_pages=max_pages, MetricDataQueries=queries, StartTime=start,
                                      EndTime=end, ScanBy="TimestampAscending")
        final = {}
        for item in pages:
            rid, stat = ids[item["Id"]]
            target = series[rid][stat]
            target["timestamps"].extend(item.get("Timestamps") or [])
            target["values"].extend(item.get("Values") or [])
            final[item["Id"]] = item.get("StatusCode", "Complete")
            series[rid]["messages"].extend(m.get("Value", "") for m in item.get("Messages") or [])
        for qid, (rid, _) in ids.items():
            if more or final.get(qid, "Complete") != "Complete":
                series[rid]["complete"] = False
    return series


def collect_cpu_metrics(event, readers, *, module=None, deadline=None):
    cfg = event.get("window") or {}
    if not isinstance(cfg, dict):
        raise ValueError("window must be an object")
    period = common.bounded_int(cfg.get("period_seconds"), DEFAULT_PERIOD, 60, 86400, "window.period_seconds")
    if period % 60:
        raise ValueError("window.period_seconds must be a multiple of 60")
    start, end = common.time_window(cfg, lookback_key="lookback_days", default=DEFAULT_LOOKBACK_DAYS,
                                    maximum=dt.timedelta(days=MAX_LOOKBACK_DAYS), unit=dt.timedelta(days=1))
    end = dt.datetime.fromtimestamp(int(end.timestamp()) // period * period, dt.timezone.utc)
    start = dt.datetime.fromtimestamp(int(start.timestamp()) // period * period, dt.timezone.utc)
    explicit = event.get("resources") or []
    if not isinstance(explicit, list) or len(explicit) > MAX_RESOURCES:
        raise ValueError(f"resources must be a list of at most {MAX_RESOURCES} items")
    discover_cfg = event.get("discover")
    if discover_cfg is True:
        discover_cfg = {}
    if discover_cfg is not None and not isinstance(discover_cfg, dict):
        raise ValueError("discover must be an object (or true)")
    if not explicit and discover_cfg is None:
        raise ValueError("event needs resources or discover")
    resources = {r["id"]: r for r in (parse_resource(spec) for spec in explicit)}
    cloudwatch = readers.client("cloudwatch")
    truncated, notes = False, []
    if discover_cfg is not None:
        found, truncated, notes = discover(cloudwatch, discover_cfg)
        for resource in found:
            resources.setdefault(resource["id"], resource)
    resources = list(resources.values())[:MAX_RESOURCES]
    series = fetch_cpu_series(cloudwatch, resources, start, end, period)
    return {
        "window": {"start": common.iso(start), "end": common.iso(end)},
        "period_seconds": period,
        "region": common.region(),
        "resources": resources,
        "series": series,
        "truncated": truncated,
        "collection": {"source": "cloudwatch-getmetricdata", "period_seconds": period,
                       "lookback_days": round((end - start).total_seconds() / 86400, 2)},
        "counts": {"resources": len(resources), "with_cpu_series": len(series)},
        "limitations": notes,
    }


# ---- INF-01 normalizer ------------------------------------------------------------------------------

def _finite(values):
    return [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


def summarize_cpu(average: dict, maximum: dict, period: int):
    """Average/peak as fractions of capacity, observed window and sample count; None without data.
    CloudWatch CPUUtilization is a percentage; ECS services can exceed 100% of their reservation, so
    fractions are capped at 1.0 (the `capped` flag records it)."""
    points = [(common.parse_time(t), v) for t, v in zip(average.get("timestamps", []), average.get("values", []))
              if v in _finite([v])]
    if not points:
        return None
    peaks = _finite(maximum.get("values", [])) or [v for _, v in points]
    raw_average = sum(v for _, v in points) / len(points) / 100
    raw_peak = max(max(peaks) / 100, raw_average)
    times = [t for t, _ in points]
    span = (max(times) - min(times)).total_seconds() + period
    return {
        "average_utilization": round(min(max(raw_average, 0.0), 1.0), 4),
        "peak_utilization": round(min(max(raw_peak, 0.0), 1.0), 4),
        "window_days": round(span / 86400, 2),
        "sample_count": len(points),
        "capped": raw_peak > 1.0,
    }


def _locator(region, resource, period):
    spec = RESOURCE_TYPES[resource["type"]]
    dims = "&".join(f"{d['Name']}={d['Value']}" for d in resource["dimensions"])
    return f"cloudwatch://{region}/{spec.namespace}/{spec.metric}?{dims}&period={period}&stat=Average,Maximum"


def normalize_cpu_metrics(raw: dict, *, settings: dict | None = None) -> dict:
    """INF-01 telemetry sources, one per CPU resource with data. Resources without usable data stay in
    scope with no source, so INF-01 reports them as not evaluated instead of clean."""
    scope, sources, notes = [], [], []
    period = raw["period_seconds"]
    for resource in raw["resources"]:
        scope_id = f"resource:{resource['id']}"
        scope.append(scope_id)
        spec = RESOURCE_TYPES[resource["type"]]
        if not spec.cpu:
            notes.append(f"{scope_id}: {LAMBDA_NOTE}")
            continue
        series = raw["series"].get(resource["id"])
        if series is None or not series["complete"]:
            notes.append(f"{scope_id}: CloudWatch returned incomplete {spec.metric} data for the window; not evaluated")
            continue
        summary = summarize_cpu(series["average"], series["maximum"], period)
        if summary is None:
            notes.append(f"{scope_id}: no {spec.namespace} {spec.metric} datapoints in "
                         f"{raw['window']['start']}..{raw['window']['end']}")
            continue
        if summary.pop("capped"):
            notes.append(f"{scope_id}: CPU utilization exceeded 100% of the reservation; values were capped at 1.0")
        sources.append({
            "source_id": f"cloudwatch:{resource['id']}",
            "scope_id": scope_id,
            "kind": "telemetry",
            "locator": _locator(raw.get("region", "ap-south-1"), resource, period),
            "data": {
                "resource_id": resource["id"],
                "resource_type": spec.resource_type,
                "metric": "cpu_utilization",
                "provisioned_capacity": resource["provisioned_capacity"],
                "capacity_unit": resource["capacity_unit"],
                **summary,
                "period_seconds": period,
                "window_start": raw["window"]["start"],
                "window_end": raw["window"]["end"],
            },
        })
    return {"scope": scope, "sources": sources, "limitations": notes}


# ---- ListMetrics inventory (OBS-06 style checks) -----------------------------------------------------

def collect_list_metrics(event, readers, *, module=None, deadline=None):
    cfg = event.get("list_metrics") or {}
    if not isinstance(cfg, dict):
        raise ValueError("list_metrics must be an object")
    pages = common.bounded_int(cfg.get("max_pages"), DEFAULT_LIST_PAGES, 1, MAX_LIST_PAGES, "list_metrics.max_pages")
    params = {}
    for key, name in (("namespace", "Namespace"), ("metric_name", "MetricName")):
        if cfg.get(key) is not None:
            if not isinstance(cfg[key], str) or not cfg[key]:
                raise ValueError(f"list_metrics.{key} must be a nonempty string")
            params[name] = cfg[key]
    # Without RecentlyActive, ListMetrics covers metrics with data in the past two weeks (OBS-06's window).
    recently_active = bool(cfg.get("recently_active", False))
    if recently_active:
        params["RecentlyActive"] = "PT3H"
    list_metrics = readers.client("cloudwatch").list_metrics
    raw_pages, token = [], None
    for _ in range(pages):
        page = list_metrics(**params, **({"NextToken": token} if token else {}))
        token = page.get("NextToken")
        raw_pages.append({"Metrics": [{k: v for k, v in m.items() if k != "OwningAccounts"}
                                      for m in page.get("Metrics") or []],
                          **({"NextToken": token} if token else {})})
        if not token:
            break
    truncated = bool(token)
    metrics = [m for page in raw_pages for m in page["Metrics"]]
    notes = [f"ListMetrics stopped after {pages} pages; the metric inventory is incomplete and series "
             "counts are lower bounds"] if truncated else []
    return {
        "pages": raw_pages,  # raw responses in request order; the last keeps NextToken when truncated
        "Metrics": metrics,
        "window": None,
        "region": common.region(),
        "truncated": truncated,
        "collection": {"source": "cloudwatch-listmetrics", "namespace": cfg.get("namespace"),
                       "metric_name": cfg.get("metric_name"), "recently_active": recently_active,
                       "max_pages": pages},
        "counts": {"metrics": len(metrics)},
        "limitations": notes,
    }
