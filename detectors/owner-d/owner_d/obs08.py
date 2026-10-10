"""OBS-08: raw high-resolution metrics kept long without downsampling (static config scan).

Detector semantics version 1.0.0. Reads Prometheus-style metric configs as text (never applied,
rendered or sent anywhere) and flags:

- a scrape interval strictly below `context.min_scrape_interval_seconds` whose raw samples a
  store visible in the same file keeps for longer than `context.max_raw_retention_days`:
  - Prometheus Operator `Prometheus`/`PrometheusAgent` resources and kube-prometheus-stack values
    (`spec.scrapeInterval`, `spec.retention`, `spec.remoteWrite`);
  - Prometheus server configs (plain, or `|` strings in ConfigMaps) paired with the single
    Prometheus server container in the same file (`--storage.tsdb.retention.time`), or with a
    `remote_write` to Amazon Managed Service for Prometheus (AMP, 150-day default retention);
  - OpenTelemetry Collector `prometheus` receivers whose metrics pipeline exports to an AMP
    `prometheusremotewrite` exporter;
- a Thanos compactor run with `--downsampling.disable` that keeps raw blocks forever or long.

Prometheus local storage and AMP keep every raw sample until retention; only the Thanos
compactor downsamples. CloudWatch high-resolution metrics are out of scope: CloudWatch rolls
sub-minute data points up after 3 hours by itself. Sample volumes are not observed, so no
measurements are emitted.
"""

from __future__ import annotations

import math
import re
import shlex
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

from . import miniyaml, otelconfig, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .otelconfig import get, is_unresolved, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "OBS-08"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-08", "OBS08")
SETTING_KEYS = ("min_scrape_interval_seconds", "max_raw_retention_days")
# Reference values documented in the README (both settings are required in the context).
REFERENCE_SETTINGS = {"min_scrape_interval_seconds": 15, "max_raw_retention_days": 15}
FORMATS = (
    "Prometheus Operator resources, kube-prometheus-stack values, Prometheus server configs, OpenTelemetry "
    "Collector prometheus receivers and Thanos compactor workloads (.yaml/.yml)"
)

REFERENCES = (
    "https://prometheus.io/docs/prometheus/latest/storage/",
    "https://prometheus.io/docs/prometheus/latest/configuration/configuration/",
    "https://prometheus-operator.dev/docs/api-reference/api/",
    "https://thanos.io/tip/components/compact.md/#downsampling",
    "https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-workspace-configuration.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html#metrics-retention",
)
RECOMMENDATION = (
    "Scrape at 15s-60s unless a specific alert or autoscaler needs sub-minute data. Keep high-resolution jobs few "
    "and their raw retention short, and keep long history as 1m/5m rollups (recording rules, or the Thanos "
    "compactor with a raw retention above 40h and shorter than the 5m/1h retention). If the resolution and "
    "retention are required, keep them and add `# noqa: OBS-08` with the reason."
)
REC_AMP = (
    "AMP stores every sample at full resolution for the workspace retention period (150 days by default). Scrape "
    "at 15s-60s unless a specific alert needs sub-minute data, or keep the high-resolution series out of remote "
    "write (write_relabel_configs) and send recording-rule rollups instead; lower the workspace retention period "
    "if long history is not needed. If the resolution is required, add `# noqa: OBS-08` with the reason."
)
REC_THANOS = (
    "Remove --downsampling.disable and set --retention.resolution-raw shorter than --retention.resolution-5m/-1h "
    "(raw above 40h and 5m above 10d, so each downsampling pass completes), so long-range history is kept as "
    "5m/1h blocks instead of raw samples."
)
LIMITATION = (
    "Static config scan only: OBS-08 sees one file at a time and pairs a scrape interval only with a store "
    "visible in the same file (Prometheus Operator retention, the single Prometheus server's retention flags, an "
    "AMP remote write). ServiceMonitor/PodMonitor intervals, scrape_config_files, additionalScrapeConfigs, stores "
    "defined in other files and the AMP workspace retention (assumed to be the 150-day default) are not visible; "
    "non-AMP remote-write targets, retention-size-only stores and unresolved ${...}/$(...) values are not judged. "
    "Sample volumes and the alerts that may need sub-minute data are not observed, so no measurements are emitted. "
    "Development/test files, Helm templates and CloudWatch agent configs are not evaluated."
)

DAY = 86400.0
AMP_RETENTION = 150 * DAY  # AMP default workspace retention period
SERVER_DEFAULT_RETENTION = 15 * DAY  # Prometheus: neither retention time nor size set
OPERATOR_DEFAULT_RETENTION = DAY  # Prometheus Operator: retention, retentionSize, retentionPercentage unset
OPERATOR_DEFAULT_INTERVAL = "30s"
POD_SPEC = {"Pod": ("spec",), "CronJob": ("spec", "jobTemplate", "spec", "template", "spec")}
for _kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "Rollout"):
    POD_SPEC[_kind] = ("spec", "template", "spec")
OPERATOR_KINDS = ("Prometheus", "PrometheusAgent")
# File/directory name tokens that mark development, debug or test configs (as in OBS-09).
DEV_NAMES = {"dev", "development", "debug", "local", "test", "tests", "testing", "testdata", "e2e", "ci",
             "devcontainer"}

_CANDIDATE = re.compile(
    r"scrape_configs\s*:|scrape_interval\s*:|scrapeInterval\s*:|prometheusSpec\s*:|storage\.tsdb\.retention"
    r"|downsampling\.disable|kind:\s*Prometheus(Agent)?\s*$|(prom|prometheus)/prometheus\b",
    re.M,
)
_DURATION = re.compile(r"^(?:(\d+)y)?(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?$")
_UNITS = (365 * DAY, 7 * DAY, DAY, 3600.0, 60.0, 1.0, 0.001)
_AMP = re.compile(r"aps-workspaces\.[a-z0-9-]+\.amazonaws\.com(\.cn)?/workspaces/", re.I)
_UNRESOLVED_K8S = re.compile(r"\$\([A-Za-z_][A-Za-z0-9_]*\)")
_BLOCK_HEADER = re.compile(r":\s*[|][-+0-9]*\s*(#.*)?$")


def seconds(value):
    """Seconds in a Prometheus `<duration>` (`1y2w3d4h5m6s7ms`, `0`), else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value == "0":
        return 0.0
    match = _DURATION.match(value)
    if not value or not match:
        return None
    return sum(int(part) * unit for part, unit in zip(match.groups(), _UNITS) if part)


def _days(value):
    return "forever" if math.isinf(value) else f"{value / DAY:g} days"


@dataclass(frozen=True)
class Store:
    """Somewhere the raw samples of a scrape are kept."""

    what: str
    retention: float  # seconds; math.inf when kept forever
    amp: bool = False


@dataclass(frozen=True)
class Scrape:
    anchor: str
    line: int
    block_line: int
    raw: str
    interval: float
    where: str


@dataclass
class Group:
    """Scrapes that share their stores (one Prometheus resource, config or receiver)."""

    label: str
    scrapes: list = field(default_factory=list)
    stores: list = field(default_factory=list)
    unknown: list = field(default_factory=list)  # reasons a store's retention is not visible


@dataclass
class Compactor:
    anchor: str
    name: str
    line: int
    block_line: int
    raw_retention: float | None  # None: unresolved/invalid


class Ctx:
    def __init__(self, content):
        self.lines = content.splitlines()
        self.groups = []
        self.compactors = []
        self.servers = []  # Prometheus server stores (or unknown reasons) found in the file
        self.hits = []


def _dev_path(locator):
    parts = [p.lower() for p in re.split(r"[\\/]", locator)]
    tokens = set(re.split(r"[._-]", parts[-1])) | {p.lstrip(".") for p in parts[:-1]}
    marked = sorted(tokens & DEV_NAMES)
    return marked[0] if marked else None


def _unresolved(value):
    return is_unresolved(value) or (isinstance(value, str) and _UNRESOLVED_K8S.search(value) is not None)


def _key_line(node, key):
    return node.key_lines.get(key, node.line) if isinstance(node, Mapping) else 0


# -- Prometheus scrape configs ----------------------------------------------------------------


def _amp_stores(remote_writes, key):
    stores = []
    for entry in remote_writes.items if isinstance(remote_writes, Sequence) else ():
        url = text(get(entry, key))
        if url and _AMP.search(url):
            stores.append(Store("its Amazon Managed Service for Prometheus remote write", AMP_RETENTION, amp=True))
    return stores


def _remote_unknown(remote_writes, key):
    count = 0
    for entry in remote_writes.items if isinstance(remote_writes, Sequence) else ():
        url = text(get(entry, key))
        if not (url and _AMP.search(url)):
            count += 1
    return [f"{count} non-AMP remote write target(s)"] if count else []


def _scrapes(config, label):
    """Scrapes of a Prometheus config mapping: explicit job intervals and the global interval
    when at least one job (or a scrape_config_files entry) inherits it."""
    found = []
    global_node = get(config, "global", "scrape_interval")
    jobs = get(config, "scrape_configs")
    inherited = bool(get(config, "scrape_config_files"))
    for index, job in enumerate(jobs.items if isinstance(jobs, Sequence) else ()):
        if not isinstance(job, Mapping):
            continue
        node = job.get("scrape_interval")
        if node is None:
            inherited = True
            continue
        name = text(job.get("job_name")) or f"#{index}"
        raw = text(node)
        interval = None if _unresolved(raw) else seconds(raw)
        if interval is not None:
            found.append(Scrape(f"{label}job/{name}:scrape_interval", node.line, job.line, raw, interval,
                                f"Scrape job {name!r}"))
    raw = text(global_node)
    if global_node is not None and inherited and not _unresolved(raw) and seconds(raw) is not None:
        found.insert(0, Scrape(f"{label}global:scrape_interval", global_node.line, global_node.line, raw,
                               seconds(raw), "The global scrape_interval (used by jobs that do not set their own)"))
    return found


def _is_prometheus_config(node):
    return isinstance(node, Mapping) and (
        isinstance(get(node, "scrape_configs"), Sequence) or get(node, "global", "scrape_interval") is not None
    ) and not text(node.get("kind")) and get(node, "service", "pipelines") is None


def _shift(node, offset, seen=None):
    """Move every line of an embedded parsed tree by `offset` (aliased nodes only once)."""
    seen = set() if seen is None else seen
    if id(node) in seen:
        return
    seen.add(id(node))
    node.line += offset
    if isinstance(node, Mapping):
        node.key_lines = {key: line + offset for key, line in node.key_lines.items()}
        children = node.items.values()
    else:
        children = node.items if isinstance(node, Sequence) else ()
    for child in children:
        _shift(child, offset, seen)


def _embedded_configs(doc, lines):
    """(label, config) for ConfigMap `data` entries that hold a Prometheus config as a `|` string."""
    name = text(get(doc, "metadata", "name")) or "unnamed"
    data = get(doc, "data")
    for key, value in data.items.items() if isinstance(data, Mapping) else ():
        if not isinstance(value, Scalar) or not value.value or "scrape_" not in value.value:
            continue
        header = lines[value.line - 1] if 0 < value.line <= len(lines) else ""
        if not _BLOCK_HEADER.search(header):
            continue
        try:
            docs = miniyaml.load_all(value.value)
        except miniyaml.YamlError as error:
            raise ParseError(f"embedded config ConfigMap/{name}:{key} at line {value.line}: {error}") from None
        for embedded in docs:
            _shift(embedded, value.line)
            if _is_prometheus_config(embedded):
                yield f"ConfigMap/{name}:{key}:", embedded


# -- Prometheus Operator ----------------------------------------------------------------------


def _operator_group(spec, label, who, kind_line, agent, explicit_retention_only):
    group = Group(label)
    node = get(spec, "scrapeInterval")
    raw = text(node) or OPERATOR_DEFAULT_INTERVAL
    interval = None if _unresolved(raw) else seconds(raw)
    if interval is not None:
        explicit = bool(text(node))
        line = node.line if explicit else kind_line
        where = f"{who} ({'scrapeInterval' if explicit else 'default scrapeInterval'})"
        group.scrapes.append(Scrape(f"{label}scrapeInterval", line, line, raw, interval, where))
    if not agent:
        retention = text(get(spec, "retention"))
        sized = any(text(get(spec, key)) for key in ("retentionSize", "retentionPercentage"))
        if retention:
            value = None if _unresolved(retention) or retention.strip() == "0" else seconds(retention)
            if value is None:
                group.unknown.append(f"retention {retention!r}")
            else:
                group.stores.append(Store(f"its local TSDB (retention: {retention})", value))
        elif sized or explicit_retention_only:
            group.unknown.append("retention not set (size-bounded or chart default)")
        else:
            group.stores.append(Store("its local TSDB (operator default retention 24h)", OPERATOR_DEFAULT_RETENTION))
    remote = get(spec, "remoteWrite")
    group.stores.extend(_amp_stores(remote, "url"))
    group.unknown.extend(_remote_unknown(remote, "url"))
    return group


# -- containers ------------------------------------------------------------------------------


def _tokens(node):
    """[(token, line)] of a command/args node (a sequence, or a shell-like string)."""
    if isinstance(node, Sequence):
        return [(item.value, item.line) for item in node.items if isinstance(item, Scalar) and item.value]
    if isinstance(node, Scalar) and node.value:
        try:
            parts = shlex.split(node.value)
        except ValueError:
            parts = node.value.split()
        return [(part, node.line) for part in parts]
    return []


def _flag(tokens, *names):
    """(value, line) of the last `--name=value` / `--name value`, else (None, None)."""
    found = (None, None)
    for index, (token, line) in enumerate(tokens):
        for name in names:
            if token.startswith(name + "="):
                found = (token[len(name) + 1:], line)
            elif token == name and index + 1 < len(tokens) and not tokens[index + 1][0].startswith("-"):
                found = (tokens[index + 1][0], line)
    return found


def _image_name(image):
    name = (image or "").split("@", 1)[0].rsplit("/", 1)[-1]
    return name.split(":", 1)[0].lower()


def _containers(doc):
    """(owner, container name, container mapping, tokens) for Kubernetes workloads and Compose services."""
    kind = text(doc.get("kind"))
    if kind == "List":
        items = doc.get("items")
        for item in items.items if isinstance(items, Sequence) else ():
            if isinstance(item, Mapping):
                yield from _containers(item)
        return
    if kind in POD_SPEC:
        name = text(get(doc, "metadata", "name")) or "?"
        namespace = text(get(doc, "metadata", "namespace"))
        owner = f"{kind}/{namespace + '/' if namespace else ''}{name}"
        pod = get(doc, *POD_SPEC[kind])
        for section in ("initContainers", "containers"):
            containers = get(pod, section)
            for index, container in enumerate(containers.items if isinstance(containers, Sequence) else ()):
                if isinstance(container, Mapping):
                    cname = text(container.get("name")) or f"#{index}"
                    tokens = _tokens(container.get("command")) + _tokens(container.get("args"))
                    yield owner, cname, container, tokens
        return
    services = get(doc, "services")
    if kind is None and isinstance(services, Mapping):
        for name, service in services.items.items():
            if isinstance(service, Mapping):
                tokens = _tokens(service.get("entrypoint")) + _tokens(service.get("command"))
                yield "service", name, service, tokens


def _runs(container, tokens, binary):
    image = _image_name(text(container.get("image")))
    first = tokens[0][0].rsplit("/", 1)[-1] if tokens else ""
    return image == binary or first == binary


def _server_store(owner, cname, tokens):
    """Store (or unknown reason) of a Prometheus server container; None in agent mode."""
    words = [token for token, _ in tokens]
    if "--agent" in words or any(w.startswith("--enable-feature=") and "agent" in w.split("=", 1)[1].split(",")
                                 for w in words):
        return None
    who = f"Prometheus server {owner}:{cname}"
    value, line = _flag(tokens, "--storage.tsdb.retention.time", "--storage.tsdb.retention")
    if value is None:
        if _flag(tokens, "--storage.tsdb.retention.size")[0] is not None:
            return f"{who} is bounded by size only"
        return Store(f"{who} (default retention 15d)", SERVER_DEFAULT_RETENTION)
    retention = None if _unresolved(value) or value.strip() == "0" else seconds(value)
    if retention is None:
        return f"{who} retention {value!r}"
    return Store(f"{who} (retention {value}, line {line})", retention)


def _compactor(owner, cname, container, tokens):
    words = [token for token, _ in tokens]
    if not _runs(container, tokens, "thanos") or "compact" not in words:
        return None
    disabled = [line for token, line in tokens if token in ("--downsampling.disable", "--downsampling.disable=true")]
    if not disabled:
        return None
    value, _ = _flag(tokens, "--retention.resolution-raw")
    if value is None:
        raw_retention = math.inf
    else:
        raw_retention = None if _unresolved(value) else seconds(value)
        raw_retention = math.inf if raw_retention == 0 else raw_retention
    prefix = f"{owner}:{cname}" if owner != "service" else f"service/{cname}"
    where = f"container {cname!r} in {owner}" if owner != "service" else f"Compose service {cname!r}"
    return Compactor(f"{prefix}:downsampling.disable", where, disabled[-1], container.line, raw_retention)


# -- OpenTelemetry Collector -----------------------------------------------------------------


def _collector_groups(config):
    stores, unknown = {}, {}
    for pipeline in config.pipelines.values():
        if pipeline.signal != "metrics" or not pipeline.receivers or not pipeline.exporters:
            continue
        for receiver in pipeline.receivers:
            if otelconfig.component_type(receiver) != "prometheus":
                continue
            for exporter in pipeline.exporters:
                component = config.component("exporters", exporter)
                endpoint = text(get(component.node, "endpoint")) if component else None
                if (otelconfig.component_type(exporter) == "prometheusremotewrite" and endpoint
                        and _AMP.search(endpoint)):
                    what = f"exporter {exporter!r} (Amazon Managed Service for Prometheus)"
                    stores.setdefault(receiver, []).append(Store(what, AMP_RETENTION, amp=True))
                else:
                    unknown.setdefault(receiver, []).append(f"exporter {exporter!r}")
    for receiver_id in sorted(set(stores) | set(unknown)):
        component = config.component("receivers", receiver_id)
        scrape_config = get(component.node, "config") if component else None
        label = f"{config.label}receiver/{receiver_id}:"
        group = Group(label, _scrapes(scrape_config, label) if isinstance(scrape_config, Mapping) else [],
                      stores.get(receiver_id, []), unknown.get(receiver_id, []))
        yield group


# -- parse / run -----------------------------------------------------------------------------


def parse(locator, content, settings=None):
    lower = locator.lower()
    if not lower.endswith((".yaml", ".yml")) or not _CANDIDATE.search(content):
        raise Unsupported(locator)
    marked = _dev_path(locator)
    if marked:
        raise NotEvaluated(
            f"path marks a development/test config ({marked}); OBS-08 v1 evaluates deployed configs only"
        )
    try:
        docs = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    ctx = Ctx(content)
    configs = []
    for doc in docs:
        if not isinstance(doc, Mapping):
            continue
        kind = text(doc.get("kind"))
        api = text(doc.get("apiVersion")) or ""
        if kind in OPERATOR_KINDS and api.startswith("monitoring.coreos.com/"):
            name = text(get(doc, "metadata", "name")) or "unnamed"
            namespace = text(get(doc, "metadata", "namespace"))
            label = f"{kind}/{namespace + '/' if namespace else ''}{name}:"
            ctx.groups.append(_operator_group(get(doc, "spec"), label, f"{kind} {name!r}", _key_line(doc, "kind"),
                                              kind == "PrometheusAgent", False))
        elif kind == "ConfigMap":
            configs.extend(_embedded_configs(doc, ctx.lines))
        elif kind is None and isinstance(get(doc, "prometheus", "prometheusSpec"), Mapping):
            spec = get(doc, "prometheus", "prometheusSpec")
            line = _key_line(get(doc, "prometheus"), "prometheusSpec")
            ctx.groups.append(_operator_group(spec, "values/prometheus.prometheusSpec:", "prometheus.prometheusSpec",
                                              line, False, True))
        elif _is_prometheus_config(doc):
            configs.append(("", doc))
        for owner, cname, container, tokens in _containers(doc):
            if _runs(container, tokens, "prometheus"):
                store = _server_store(owner, cname, tokens)
                if store is not None:
                    ctx.servers.append(store)
            compactor = _compactor(owner, cname, container, tokens)
            if compactor is not None:
                ctx.compactors.append(compactor)
    try:
        collectors = otelconfig.find_configs(docs, ctx.lines) if otelconfig.might_contain_config(content) else []
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    for collector in collectors:
        ctx.groups.extend(_collector_groups(collector))
    for label, config in configs:
        group = Group(label, _scrapes(config, label))
        group.stores.extend(_amp_stores(get(config, "remote_write"), "url"))
        group.unknown.extend(_remote_unknown(get(config, "remote_write"), "url"))
        group.stores.extend(s for s in ctx.servers if isinstance(s, Store))
        group.unknown.extend(s for s in ctx.servers if isinstance(s, str))
        if not ctx.servers and not group.stores and not group.unknown:
            group.unknown.append("no Prometheus server or remote write in this file")
        ctx.groups.append(group)
    if not ctx.groups and not ctx.compactors and not ctx.servers:
        raise NotEvaluated(
            "no Prometheus resource, scrape config, Prometheus server or Thanos compactor found; Helm templates "
            "and other formats are not evaluated"
        )
    if settings is not None:
        ctx.hits, unjudged = _judge(ctx, settings)
        if unjudged and not ctx.hits:
            raise NotEvaluated("raw retention not visible in this file for " + "; ".join(unjudged))
    return ctx


def _judge(ctx, settings):
    """(hits, reasons why parts of the file could not be judged)."""
    fast = settings["min_scrape_interval_seconds"]
    horizon = settings["max_raw_retention_days"] * DAY
    hits, unjudged = [], []
    for group in ctx.groups:
        high = [scrape for scrape in group.scrapes if scrape.interval < fast]
        if not high:
            continue
        servers = [s for s in group.stores if not s.amp]
        if len({s.retention > horizon for s in servers}) > 1:  # several servers disagree: unknown pairing
            group.stores = [s for s in group.stores if s.amp]
            group.unknown.append("several Prometheus servers with different retention")
        long = [store for store in group.stores if store.retention > horizon]
        if not long:
            if group.unknown:
                unjudged.append(f"{group.label or 'the scrape config'} ({', '.join(group.unknown)})")
            continue
        local = [store for store in long if not store.amp]
        stores = "; ".join(f"{store.what} keeps them {_days(store.retention)}" for store in long)
        for scrape in high:
            hits.append(TextHit(
                line=scrape.line,
                block_line=scrape.block_line,
                anchor=scrape.anchor,
                summary=(
                    f"{scrape.where} scrapes every {scrape.raw} (below {fast:g}s), and the raw samples are stored "
                    f"at that resolution for more than {settings['max_raw_retention_days']:g} days with no "
                    f"downsampling: {stores}."
                    + ("" if local else " The AMP workspace retention is not visible here (150 days by default).")
                ),
                confidence="medium" if local else "low",
                recommendation=RECOMMENDATION if local else REC_AMP,
            ))
    for compactor in ctx.compactors:
        if compactor.raw_retention is None:
            unjudged.append(f"the Thanos compactor {compactor.name} (raw retention is not a literal duration)")
            continue
        if compactor.raw_retention <= horizon:
            continue
        hits.append(TextHit(
            line=compactor.line,
            block_line=compactor.block_line,
            anchor=compactor.anchor,
            summary=(
                f"The Thanos compactor ({compactor.name}) runs with --downsampling.disable and keeps raw blocks "
                f"{_days(compactor.raw_retention)}, so all history stays at scrape resolution and long-range "
                f"queries read raw samples."
            ),
            confidence="medium",
            recommendation=REC_THANOS,
        ))
    if not ctx.groups and not ctx.compactors:
        long = [s for s in ctx.servers if isinstance(s, str) or s.retention > horizon]
        if long:
            unjudged.append("the Prometheus server(s) here, whose scrape intervals are configured in another file")
    return hits, unjudged


def run(ctx):
    return list(ctx.hits)


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    for key in SETTING_KEYS:
        value = context[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return None, f"context.{key} must be a positive number"
    return {key: context[key] for key in SETTING_KEYS}, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, FORMATS=FORMATS,
        parse=lambda locator, content: module.parse(locator, content, settings),
        run=module.run,
    )
    result = textstatic.evaluate_text(payload, check)
    if settings is None:
        result.update(
            status="unavailable",
            coverage={
                "evaluated_scope": [],
                "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION],
            },
            findings=[],
            measurements=[],
        )
    return result
