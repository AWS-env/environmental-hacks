"""Which checks each telemetry analyzer runs, and how their raw telemetry is normalized.

Each analyzer collects one or more raw *sources*:

    source         analyzer                     raw dict passed to the normalizer
    cpu_metrics    owner-d-telemetry-analyzer   resources + GetMetricData Average/Maximum series (INF-01)
    metrics        owner-d-telemetry-analyzer   {"pages": [raw ListMetrics responses], "Metrics": [...], ...}
    log_groups     owner-d-log-analyzer         {"pages": [raw DescribeLogGroups responses],
                                                 "logGroups": [entries, ARNs removed],
                                                 "tags": {logGroupName: {tag: value}} | None, ...}
    logs_insights  owner-d-log-analyzer         {"rows": [{field: value}], "statistics": {...}, ...}
    traces         owner-d-trace-analyzer       {"TraceSummaries": [...], "Traces": [BatchGetTraces entries], ...}

Every raw dict also carries "window" ({"start", "end"} ISO strings or None), "truncated" (a page/size
bound was hit), "collection" (stable collection parameters, copied into the contract context) and
"limitations" (collection notes appended to each result).

A detector module plugs in by exposing a normalizer, by default named ``normalize_<source>``:

    def normalize_log_groups(raw: dict, *, settings: dict) -> list[dict] | dict

It returns contract v1 ``telemetry`` sources (source_id, scope_id, kind="telemetry", locator, data), or
a dict {"scope": [...], "sources": [...], "limitations": [...]} when some scope items have no source.
Normalizers must be pure, must not call AWS, and must drop account IDs and ARNs from source data.
Logs Insights checks also define ``LOGS_INSIGHTS_QUERY``; X-Ray checks may define
``XRAY_FILTER_EXPRESSION``. Context settings: the registry ``defaults``, then the module's
``DEFAULT_SETTINGS``, then the event's ``settings[<check_id>]``.

Normalizers that take raw API pages instead (``fn(pages)`` / ``fn(pages, tags=None)`` returning data keyed
by scope or resource) are wired through an ``adapter`` (ADAPTERS below), which builds the telemetry
sources with account-free locators: ``list_metrics`` (OBS-06), ``describe_log_groups`` (OBS-07) and
``xray_traces`` (LLM-10: ``fn(Traces)`` plus the module's ``telemetry_sources``).

Wiring a new check is one line in CHECKS (INF-01, OBS-06, OBS-07 and LLM-10 are registered below).
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field

from owner_d.aws.common import accepts_settings

SOURCES = ("cpu_metrics", "metrics", "log_groups", "logs_insights", "traces")

INF01_DEFAULTS = {  # reference values from the INF-01 section of detectors/owner-d/README.md
    "min_window_days": 14,
    "min_sample_count": 100,
    "average_utilization_threshold": 0.10,
    "peak_utilization_threshold": 0.50,
}
OBS06_DEFAULTS = {"max_dimension_values": 100, "min_identifier_values": 10}  # OBS-06 README reference values
OBS07_DEFAULTS = {  # OBS-07 README reference values
    "max_hot_retention_days": 365,
    "min_stored_bytes": 1_073_741_824,
    "compliance_tag_keys": ["compliance", "data-retention", "legal-hold"],
    "exempt_log_group_prefixes": ["aws-controltower/"],
}
LLM10_DEFAULTS = {"max_identical_tool_calls": 3, "max_llm_iterations": 10, "min_traces": 10}  # LLM-10 reference values


@dataclass(frozen=True)
class TelemetryCheck:
    check_id: str
    module: str  # detector module exposing CHECK_ID, DETECTOR_VERSION and evaluate(payload)
    source: str  # one of SOURCES
    normalizer: str | None = None  # "package.module:function"; default "<module>:normalize_<source>"
    defaults: dict = field(default_factory=dict, compare=False, hash=False)
    adapter: str | None = None  # key in ADAPTERS for normalizers with an API-shaped signature


CHECKS = (
    TelemetryCheck("INF-01", "owner_d.inf01", "cpu_metrics",
                   normalizer="owner_d.aws.metrics:normalize_cpu_metrics", defaults=INF01_DEFAULTS),
    TelemetryCheck("OBS-06", "owner_d.obs06", "metrics", normalizer="owner_d.obs06:normalize_list_metrics",
                   adapter="list_metrics", defaults=OBS06_DEFAULTS),
    TelemetryCheck("OBS-07", "owner_d.obs07", "log_groups", normalizer="owner_d.obs07:normalize_describe_log_groups",
                   adapter="describe_log_groups", defaults=OBS07_DEFAULTS),
    TelemetryCheck("LLM-10", "owner_d.llm10", "traces", normalizer="owner_d.llm10:normalize_xray_traces",
                   adapter="xray_traces", defaults=LLM10_DEFAULTS),
)


def select(sources, requested=None):
    """Registered checks whose source is in `sources`; `requested` narrows them by check_id."""
    pool = [check for check in CHECKS if check.source in sources]
    if requested is None:
        return pool
    if not isinstance(requested, list) or not all(isinstance(c, str) for c in requested):
        raise ValueError("checks must be a list of check ids")
    unknown = set(requested) - {check.check_id for check in pool}
    if unknown:
        raise ValueError(f"checks not handled by this analyzer: {sorted(unknown)}")
    return [check for check in pool if check.check_id in requested]


def load(check: TelemetryCheck):
    """(detector module, normalizer function) for a registered check."""
    if check.source not in SOURCES:
        raise ValueError(f"{check.check_id}: unknown source {check.source!r}")
    module = importlib.import_module(check.module)
    if getattr(module, "CHECK_ID", None) != check.check_id or not callable(getattr(module, "evaluate", None)):
        raise ValueError(f"{check.module} does not implement {check.check_id}")
    target = check.normalizer or f"{check.module}:normalize_{check.source}"
    module_name, _, function = target.partition(":")
    normalize = getattr(importlib.import_module(module_name), function, None)
    if not callable(normalize):
        raise ValueError(f"{check.check_id}: normalizer {target} not found")
    return module, normalize


def collector_key(module):
    """Per-check collection parameters that make two checks' raw data differ."""
    return (getattr(module, "LOGS_INSIGHTS_QUERY", None), getattr(module, "XRAY_FILTER_EXPRESSION", None))


def settings(check: TelemetryCheck, module, event: dict) -> dict:
    overrides = event.get("settings") or {}
    if not isinstance(overrides, dict) or not all(isinstance(v, dict) for v in overrides.values()):
        raise ValueError('settings must map check ids to objects, e.g. {"INF-01": {"min_window_days": 7}}')
    return {**check.defaults, **getattr(module, "DEFAULT_SETTINGS", {}), **overrides.get(check.check_id, {})}


def _describe_log_groups(fn, raw, check_settings):
    """fn(pages, tags=None) -> {logGroupName: data} (OBS-07 style) into telemetry sources.
    The locator and data carry no account ID: log_group_arn is dropped, the locator is name-based."""
    by_name = fn(raw["pages"], tags=raw.get("tags"))
    sources = [{
        "source_id": f"logs:{name}",
        "scope_id": f"resource:{name}",
        "kind": "telemetry",
        "locator": f"logs://{raw.get('region', 'ap-south-1')}/log-group/{name}",
        "data": {k: v for k, v in data.items() if k != "log_group_arn"},
    } for name, data in sorted(by_name.items())]
    return {"sources": sources}


def _list_metrics(fn, raw, check_settings):
    """fn(pages) -> {"listing_complete", "metrics": {scope_id: data}} (OBS-06 style) into telemetry sources."""
    out = fn(raw["pages"])
    sources = [{
        "source_id": f"listmetrics:{data['namespace']}/{data['metric_name']}",
        "scope_id": scope_id,
        "kind": "telemetry",
        "locator": f"cloudwatch:ListMetrics/{data['namespace']}/{data['metric_name']}",
        "data": data,
    } for scope_id, data in sorted(out["metrics"].items())]
    notes = [] if out.get("listing_complete", True) else [
        "ListMetrics listing is incomplete; series and dimension value counts are lower bounds"]
    return {"sources": sources, "limitations": notes}


def _xray_traces(fn, raw, check_settings):
    """fn(Traces) -> {"traces_received", "skipped_traces", "entrypoints"} (LLM-10 style); the detector module's
    telemetry_sources(normalized, locator=...) -> (scope, sources) builds the contract sources."""
    normalized = fn(raw["Traces"])
    to_sources = getattr(sys.modules[fn.__module__], "telemetry_sources", None)
    if not callable(to_sources):
        raise ValueError(f"{fn.__module__} needs telemetry_sources(normalized, locator=...) for the xray_traces adapter")
    scope, sources = to_sources(normalized, locator="aws-xray:BatchGetTraces")
    notes = []
    received, minimum = normalized.get("traces_received", 0), check_settings.get("min_traces")
    if isinstance(minimum, int) and received < minimum:
        notes.append(f"only {received} traces were read but min_traces is {minimum}; widen the window or raise "
                     "xray.max_traces")
    if normalized.get("skipped_traces"):
        notes.append(f"{len(normalized['skipped_traces'])} traces could not be parsed and were skipped")
    return {"scope": scope, "sources": sources, "limitations": notes}


ADAPTERS = {"describe_log_groups": _describe_log_groups, "list_metrics": _list_metrics, "xray_traces": _xray_traces}


def normalize(fn, raw: dict, check_settings: dict, adapter: str | None = None) -> dict:
    """Run a normalizer and return {"scope", "sources", "limitations"} with a consistent scope."""
    if adapter is not None:
        if adapter not in ADAPTERS:
            raise ValueError(f"unknown normalizer adapter {adapter!r}")
        out = ADAPTERS[adapter](fn, raw, check_settings)
    else:
        out = fn(raw, settings=check_settings) if accepts_settings(fn) else fn(raw)
    if isinstance(out, list):
        out = {"sources": out}
    if not isinstance(out, dict) or not isinstance(out.get("sources", []), list):
        raise ValueError(f"normalizer {fn.__name__} must return a list of sources or a dict")
    sources = out.get("sources", [])
    scope = list(out.get("scope") or [])
    for source in sources:
        if source.get("scope_id") not in scope:
            scope.append(source.get("scope_id"))
    return {"scope": scope, "sources": sources, "limitations": list(out.get("limitations") or [])}
