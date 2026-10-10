"""CloudWatch metrics collection (read-only) and the INF-01 normalizer.

Collectors (owner-d-telemetry-analyzer):
  collect_cpu_metrics       explicit resources and/or ListMetrics discovery, then GetMetricData Average and
                            Maximum CPUUtilization series per resource over a bounded window (source cpu_metrics)
  collect_list_metrics      bounded ListMetrics pages for metric-inventory checks such as OBS-06 (source metrics)
  collect_capacity_metrics  GetMetricData Average and Maximum utilization series for the fixed agent/inference
                            capacity declared in the event's `agent_capacity` (source capacity_metrics, LLM-17)

normalize_cpu_metrics turns the cpu_metrics raw dict into the normalized CloudWatch CPU summary INF-01
expects (detectors/owner-d/README.md, INF-01 > Input); normalize_capacity_metrics does the same for LLM-17's
utilization distribution (mean, median, peak). Both are pure, so they are tested without AWS.
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

def _query(qid, metric, stat, period):
    return {"Id": qid, "ReturnData": True, "MetricStat": {"Metric": metric, "Period": period, "Stat": stat}}


def _cpu_metric(resource):
    spec = RESOURCE_TYPES[resource["type"]]
    return {"Namespace": spec.namespace, "MetricName": spec.metric, "Dimensions": resource["dimensions"]}


def fetch_cpu_series(cloudwatch, resources, start, end, period, max_pages=MAX_METRIC_DATA_PAGES):
    """Average and Maximum series per CPU resource: {id: {"average": {...}, "maximum": {...}, "complete", "messages"}}."""
    cpu = [(r["id"], _cpu_metric(r)) for r in resources if RESOURCE_TYPES[r["type"]].cpu]
    return fetch_series(cloudwatch, cpu, start, end, period, max_pages)


def fetch_series(cloudwatch, metrics, start, end, period, max_pages=MAX_METRIC_DATA_PAGES):
    """Average and Maximum series per (series id, CloudWatch metric) pair, at most RESOURCES_PER_CALL pairs per
    GetMetricData request and max_pages pages each: {id: {"average", "maximum", "complete", "messages"}}."""
    series = {}
    for offset in range(0, len(metrics), RESOURCES_PER_CALL):
        batch = metrics[offset:offset + RESOURCES_PER_CALL]
        ids = {}
        queries = []
        for i, (series_id, metric) in enumerate(batch):
            for stat, prefix in (("Average", "avg"), ("Maximum", "max")):
                qid = f"{prefix}{i}"
                ids[qid] = (series_id, stat.lower())
                queries.append(_query(qid, metric, stat, period))
            series[series_id] = {"average": {"timestamps": [], "values": []},
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


def _metric_window(event):
    """(start, end, period) from the event's `window`, aligned to the period."""
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
    return start, end, period


def collect_cpu_metrics(event, readers, *, module=None, deadline=None):
    start, end, period = _metric_window(event)
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


# ---- capacity_metrics: fixed agent/inference capacity (LLM-17) ---------------------------------------

@dataclass(frozen=True)
class CapacityType:
    namespace: str | None  # None: the event names it (custom)
    metric_name: str | None
    resource_type: str
    metric: str  # normalized metric name in LLM-17's input
    scale: float  # divide CloudWatch values by this to get a fraction of capacity
    capacity_unit: str


CAPACITY_TYPES = {
    "ec2": CapacityType("AWS/EC2", "CPUUtilization", "aws_ec2_instance", "cpu_utilization", 100.0, "instance"),
    "ecs": CapacityType("AWS/ECS", "CPUUtilization", "aws_ecs_service", "cpu_utilization", 100.0, "service"),
    # Lambda reports provisioned-concurrency use per alias/version as a fraction (0.5 = 50% in use).
    "lambda": CapacityType("AWS/Lambda", "ProvisionedConcurrencyUtilization", "aws_lambda_function",
                           "provisioned_concurrency_utilization", 1.0, "provisioned_concurrency"),
    # A utilization metric the workload publishes itself, e.g. busy agent workers / pool size.
    "custom": CapacityType(None, None, "custom_capacity_pool", "capacity_utilization", 100.0, "unit"),
}
CAPACITY_UNITS = {"percent": 100.0, "fraction": 1.0}
WORKLOADS = ("agent", "inference")
MAX_CAPACITY_RESOURCES = 50
MAX_CUSTOM_DIMENSIONS = 30
QUALIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
LAMBDA_ON_DEMAND_NOTE = ("on-demand Lambda (no provisioned-concurrency alias or version given) has no fixed capacity "
                         "and publishes no utilization metric; LLM-17 reads ProvisionedConcurrencyUtilization only "
                         "for a `qualifier`")
NO_CAPACITY_NOTE = ("no `agent_capacity` in the event; LLM-17 reads only capacity that the event declares as an "
                    "agent/inference workload")


def _text_field(spec, key):
    value = spec.get(key)
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"agent_capacity {spec.get('type')} needs a valid {key}")
    return value


def parse_capacity(spec: dict) -> dict:
    """One `agent_capacity` entry: {"type": "ec2", "id"} | {"type": "ecs", "cluster", "service"} |
    {"type": "lambda", "name", "qualifier"?} | {"type": "custom", "name", "namespace", "metric_name",
    "dimensions": {}, "unit": "percent" | "fraction"}, each with optional provisioned_capacity, capacity_unit,
    autoscaling (true / false / null = unknown) and workload ("agent" by default, or "inference")."""
    if not isinstance(spec, dict) or spec.get("type") not in CAPACITY_TYPES:
        raise ValueError(f"agent_capacity type must be one of {sorted(CAPACITY_TYPES)}")
    kind = spec["type"]
    kspec = CAPACITY_TYPES[kind]
    namespace, metric_name, dims, scale = kspec.namespace, kspec.metric_name, None, kspec.scale
    if kind in ("ec2", "ecs"):
        base = parse_resource({k: v for k, v in spec.items() if k in ("type", "id", "cluster", "service")})
        rid, dims = base["id"], base["dimensions"]
    elif kind == "lambda":
        name = _text_field(spec, "name")
        qualifier = spec.get("qualifier")
        if qualifier is None:
            rid = f"lambda/{name}"
        else:
            if not isinstance(qualifier, str) or not QUALIFIER.fullmatch(qualifier):
                raise ValueError("agent_capacity lambda qualifier must be an alias or version ($LATEST has no "
                                 "provisioned concurrency)")
            rid = f"lambda/{name}:{qualifier}"
            dims = [{"Name": "FunctionName", "Value": name}, {"Name": "Resource", "Value": f"{name}:{qualifier}"}]
    else:
        name = _text_field(spec, "name")
        namespace = _text_field(spec, "namespace")
        metric_name = _text_field(spec, "metric_name")
        raw_dims = spec.get("dimensions") or {}
        if (not isinstance(raw_dims, dict) or len(raw_dims) > MAX_CUSTOM_DIMENSIONS
                or not all(isinstance(k, str) and NAME.fullmatch(k) and isinstance(v, str) and NAME.fullmatch(v)
                           for k, v in raw_dims.items())):
            raise ValueError(f"agent_capacity custom dimensions must map at most {MAX_CUSTOM_DIMENSIONS} names to "
                             "string values")
        unit = spec.get("unit", "percent")
        if unit not in CAPACITY_UNITS:
            raise ValueError(f"agent_capacity custom unit must be one of {sorted(CAPACITY_UNITS)}")
        rid, scale = f"metric/{name}", CAPACITY_UNITS[unit]
        dims = [{"Name": k, "Value": v} for k, v in sorted(raw_dims.items())]
    capacity = spec.get("provisioned_capacity", 1)
    if isinstance(capacity, bool) or not isinstance(capacity, (int, float)) or not 0 < capacity < 1e6:
        raise ValueError(f"{rid}: provisioned_capacity must be a positive number")
    unit_name = spec.get("capacity_unit", kspec.capacity_unit)
    if not isinstance(unit_name, str) or not unit_name.strip():
        raise ValueError(f"{rid}: capacity_unit must be a nonempty string")
    autoscaling = spec.get("autoscaling")
    if autoscaling is not None and not isinstance(autoscaling, bool):
        raise ValueError(f"{rid}: autoscaling must be true, false or null")
    workload = spec.get("workload", "agent")
    if workload not in WORKLOADS:
        raise ValueError(f"{rid}: workload must be one of {list(WORKLOADS)}")
    metric = None if dims is None else {"Namespace": namespace, "MetricName": metric_name, "Dimensions": dims}
    return {"type": kind, "id": rid, "metric": metric, "scale": scale, "resource_type": kspec.resource_type,
            "utilization_metric": kspec.metric, "provisioned_capacity": capacity, "capacity_unit": unit_name,
            "autoscaling": autoscaling, "workload": workload}


def collect_capacity_metrics(event, readers, *, module=None, deadline=None):
    """GetMetricData Average/Maximum per declared agent capacity. Without `agent_capacity` nothing is read."""
    start, end, period = _metric_window(event)
    declared = event.get("agent_capacity")
    raw = {"window": {"start": common.iso(start), "end": common.iso(end)}, "period_seconds": period,
           "region": common.region(), "resources": [], "series": {}, "truncated": False,
           "collection": {"source": "cloudwatch-getmetricdata", "period_seconds": period,
                          "lookback_days": round((end - start).total_seconds() / 86400, 2)},
           "counts": {"resources": 0, "with_series": 0}, "limitations": []}
    if declared is None:
        raw["limitations"].append(NO_CAPACITY_NOTE)
        return raw
    if not isinstance(declared, list) or not declared or len(declared) > MAX_CAPACITY_RESOURCES:
        raise ValueError(f"agent_capacity must be a list of 1-{MAX_CAPACITY_RESOURCES} items")
    resources = [parse_capacity(spec) for spec in declared]
    ids = [r["id"] for r in resources]
    if len(set(ids)) != len(ids):
        raise ValueError("agent_capacity lists the same resource twice")
    measured = [(r["id"], r["metric"]) for r in resources if r["metric"] is not None]
    series = fetch_series(readers.client("cloudwatch"), measured, start, end, period) if measured else {}
    raw.update(resources=resources, series=series, counts={"resources": len(resources), "with_series": len(series)})
    return raw


def summarize_utilization(average: dict, maximum: dict, period: int, scale: float):
    """Mean, median (p50) and peak of a utilization series as fractions of capacity, plus the observed window
    and sample count; None without data. The mean and median are over the period averages, the peak is the
    highest period maximum. Fractions are clamped to [0, 1]; `capped` records values above 1."""
    points = [(common.parse_time(t), v) for t, v in zip(average.get("timestamps", []), average.get("values", []))
              if v in _finite([v])]
    if not points:
        return None
    raw = sorted(v / scale for _, v in points)
    raw_peak = max([v / scale for v in _finite(maximum.get("values", []))] + [raw[-1]])
    values = [min(max(v, 0.0), 1.0) for v in raw]
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    times = [t for t, _ in points]
    span = (max(times) - min(times)).total_seconds() + period
    return {
        "mean_utilization": round(sum(values) / len(values), 4),
        "median_utilization": round(median, 4),
        "peak_utilization": round(min(max(raw_peak, 0.0), 1.0), 4),
        "window_days": round(span / 86400, 2),
        "sample_count": len(points),
        "capped": raw_peak > 1.0,
    }


def _capacity_locator(region, metric, period):
    dims = "&".join(f"{d['Name']}={d['Value']}" for d in metric["Dimensions"])
    return (f"cloudwatch://{region}/{metric['Namespace']}/{metric['MetricName']}?{dims}&period={period}"
            "&stat=Average,Maximum")


def normalize_capacity_metrics(raw: dict, *, settings: dict | None = None) -> dict:
    """LLM-17 telemetry sources, one per declared capacity with data. Capacity without a utilization metric
    (on-demand Lambda) or without usable data stays in scope with no source, so LLM-17 reports it as not
    evaluated instead of clean."""
    scope, sources, notes = [], [], []
    period = raw["period_seconds"]
    for resource in raw["resources"]:
        scope_id = f"resource:{resource['id']}"
        scope.append(scope_id)
        metric = resource["metric"]
        if metric is None:
            notes.append(f"{scope_id}: {LAMBDA_ON_DEMAND_NOTE}")
            continue
        name = f"{metric['Namespace']} {metric['MetricName']}"
        series = raw["series"].get(resource["id"])
        if series is None or not series["complete"]:
            notes.append(f"{scope_id}: CloudWatch returned incomplete {name} data for the window; not evaluated")
            continue
        summary = summarize_utilization(series["average"], series["maximum"], period, resource["scale"])
        if summary is None:
            notes.append(f"{scope_id}: no {name} datapoints in {raw['window']['start']}..{raw['window']['end']}")
            continue
        if summary.pop("capped"):
            notes.append(f"{scope_id}: utilization exceeded 100% of the declared capacity; values were capped at 1.0")
        sources.append({
            "source_id": f"cloudwatch:{resource['id']}",
            "scope_id": scope_id,
            "kind": "telemetry",
            "locator": _capacity_locator(raw.get("region", "ap-south-1"), metric, period),
            "data": {
                "resource_id": resource["id"],
                "resource_type": resource["resource_type"],
                "workload": resource["workload"],
                "metric": resource["utilization_metric"],
                "provisioned_capacity": resource["provisioned_capacity"],
                "capacity_unit": resource["capacity_unit"],
                "autoscaling": resource["autoscaling"],
                **summary,
                "period_seconds": period,
                "window_start": raw["window"]["start"],
                "window_end": raw["window"]["end"],
            },
        })
    return {"scope": scope, "sources": sources, "limitations": notes}
