"""OBS-10: filtering after ingestion (static pipeline-stage scan).

Detector semantics version 1.0.0. Reads, as text and project-wide, OpenTelemetry Collector
configs (plain files, `OpenTelemetryCollector` resources, ConfigMaps; ADOT uses the same
format), Terraform files with Datadog log index resources and any file that declares a
Datadog Agent `exclude_at_match` rule, and flags telemetry that is shipped in full to a later
stage which then drops part of it, while the earlier stage declares no filtering:

- an agent collector pipeline that exports to a gateway collector in the payload (matched by
  the OTLP endpoint host) whose OTLP-fed pipelines of the same signal all drop data with a
  `filter` processor, while the agent pipeline has no filter or sampler;
- a Datadog `datadog_logs_index` exclusion filter that excludes 100% of the matching logs
  (`sample_rate = 1.0`). Excluded logs are still ingested and billed for ingestion; they are
  only not indexed. Not flagged when the payload declares an Agent `exclude_at_match` rule
  (source-side filtering) or a `datadog_logs_archive` (excluded logs are still archived).

It proves "dropped downstream, nothing dropped upstream", not ingested volume: nothing is
executed, rendered or resolved and no measurements are emitted. OBS-10 is an OQ-8 judgement
row; findings are candidates for reviewer confirmation.
"""

from __future__ import annotations

import re
import sys
import types
from dataclasses import dataclass, field

from . import miniyaml, otelconfig, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .obs09 import OTLP_EXPORTERS, _dev_path, _host
from .otelconfig import component_type, get, is_unresolved, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "OBS-10"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-10", "OBS10")
FORMATS = (
    "OpenTelemetry Collector configs (.yaml/.yml), Terraform files with Datadog log index resources (.tf) and "
    "files that declare a Datadog Agent exclude_at_match rule"
)

REFERENCES = (
    "https://docs.datadoghq.com/logs/log_configuration/indexes/#exclusion-filters",
    "https://docs.datadoghq.com/agent/logs/advanced_log_collection/#filter-logs",
    "https://registry.terraform.io/providers/DataDog/datadog/latest/docs/resources/logs_index",
    "https://opentelemetry.io/docs/collector/deployment/agent/",
    "https://opentelemetry.io/docs/collector/deployment/gateway/",
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/filterprocessor/README.md",
    "https://openobserve.ai/blog/observability-cost-optimization-tactics/",
)
REC_GATEWAY = (
    "Drop the data where it is produced: add a `filter` processor with the gateway's drop conditions to this agent "
    "pipeline (after memory_limiter, before batch), or a stanza `filter` operator on the receiver, so discarded "
    "telemetry is never serialized and sent. Keep a condition only on the gateway when it needs attributes that "
    "only the gateway adds."
)
REC_DATADOG = (
    "Drop these logs before ingestion: add a Datadog Agent `log_processing_rules` entry with `type: "
    "exclude_at_match` (or filter in the log shipper / Observability Pipelines), or lower the log level at the "
    "source. Index exclusion filters only stop indexing; excluded logs are still ingested and billed for ingestion."
)
RECOMMENDATION = REC_GATEWAY
LIMITATION = (
    "Static pipeline scan only: OBS-10 proves that a later stage drops data while the earlier stage declares no "
    "filter, not how much is ingested, so no measurements are emitted. It is an OQ-8 judgement row: findings are "
    "candidates for reviewer confirmation. Not visible: gateways in other repositories or behind Services, "
    "ingresses, load balancers, unresolved or loopback endpoints; whether a gateway filter needs attributes only the "
    "gateway adds; Datadog index JSON/API exports, Observability Pipelines, Vector, Fluent Bit, Fluentd and "
    "Splunk stages, and whether an Agent rule covers the same logs as an exclusion filter. Partial exclusions "
    "(sample_rate < 1) are OBS-08 downsampling; CloudWatch subscription filters with an empty pattern prove "
    "forwarding, not a later drop, and are not flagged. Helm values and development/test files are not judged."
)

SIGNALS = {"traces", "metrics", "logs"}
REDUCERS = {"filter", "probabilistic_sampler", "tail_sampling"}  # agent-side drops
LOCAL_EXPORTERS = {"debug", "logging", "nop"}
ENRICHERS = {"k8sattributes", "resourcedetection", "resource", "attributes", "transform", "groupbyattrs"}
DD_RULE = "exclude_at_match"
_DD_RESOURCE = re.compile(r'resource\s+"datadog_logs_(index|archive|metric)"')
_SUFFIX = re.compile(r"[-_.](configmap|config|conf|cm|cfg)$")
GENERIC_STEMS = {"config", "collector", "otel-collector", "otelcol", "otel-config", "collector-config", "relay"}


# -- minimal HCL block reader ----------------------------------------------------------------


class HclError(ValueError):
    pass


@dataclass
class Block:
    type: str
    labels: tuple
    line: int
    end_line: int = 0
    attrs: dict = field(default_factory=dict)  # name -> (raw value text, line)
    children: list = field(default_factory=list)

    def blocks(self, kind):
        return [child for child in self.children if child.type == kind]


_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_ATTR = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*=(?!=)\s*(.*)$")
_BLOCK = re.compile(r'^\s*([A-Za-z_][\w-]*)((?:\s+(?:"[^"]*"|[A-Za-z_][\w-]*))*)\s*\{(.*)$')
_HEREDOC = re.compile(r"<<-?\s*([A-Za-z_]\w*)\s*$")
_LABEL = re.compile(r'"([^"]*)"|([A-Za-z_][\w-]*)')


def _mask(line):
    """Blank string contents (same length) and cut comments, so brackets and `#` inside strings are ignored."""
    masked = _STRING.sub(lambda m: '"' + "x" * (len(m.group(0)) - 2) + '"', line)
    cut = re.search(r"#|//", masked)
    return masked[:cut.start()] if cut else masked


def parse_hcl(content):
    """Top-level blocks of a Terraform file. Values stay raw text; nothing is evaluated."""
    root = Block("", (), 0)
    stack, depth, heredoc, comment = [root], 0, None, False
    for number, raw in enumerate(content.splitlines(), 1):
        if heredoc:
            heredoc = None if raw.strip() == heredoc else heredoc
            continue
        if comment:
            if "*/" not in raw:
                continue
            raw, comment = " " * (raw.index("*/") + 2) + raw[raw.index("*/") + 2:], False
        masked = _mask(raw)
        if "/*" in masked:
            start = masked.index("/*")
            end = masked.find("*/", start + 2)
            if end < 0:
                masked, comment = masked[:start], True
            else:
                masked = masked[:start] + " " * (end + 2 - start) + masked[end + 2:]
        if not masked.strip():
            continue
        net = sum(masked.count(c) for c in "{[(") - sum(masked.count(c) for c in "}])")
        if depth:
            depth += net
            if depth < 0:
                raise HclError(f"unbalanced brackets at line {number}")
            continue
        attr, block = _ATTR.match(masked), _BLOCK.match(masked)
        if attr:
            stack[-1].attrs[attr.group(1)] = (raw[attr.start(2):len(masked)].strip(), number)
            tag = _HEREDOC.search(masked)
            if tag:
                heredoc = tag.group(1)
            elif net > 0:
                depth = net
        elif block:
            labels = tuple(a or b for a, b in _LABEL.findall(raw[block.start(2):block.end(2)]))
            node = Block(block.group(1), labels, number)
            stack[-1].children.append(node)
            if net == 0:  # one-line block: `filter { sample_rate = 1.0 }`
                node.end_line = number
                inner = _ATTR.match(masked[block.start(3):masked.rindex("}")])
                if inner:
                    offset = block.start(3)
                    node.attrs[inner.group(1)] = (raw[offset + inner.start(2):offset + inner.end(2)].strip(), number)
            elif net == 1:
                stack.append(node)
            else:
                raise HclError(f"unsupported block syntax at line {number}")
        elif masked.strip().startswith("}") and net == -1 and len(stack) > 1:
            stack.pop().end_line = number
        elif net:
            raise HclError(f"unbalanced brackets at line {number}")
    if len(stack) > 1 or depth or heredoc or comment:
        raise HclError("unbalanced blocks, brackets, heredoc or comment at end of file")
    return root.children


def _string(raw):
    """Literal value of a quoted HCL string (None when interpolated or not a string)."""
    if raw and len(raw) >= 2 and raw[0] == raw[-1] == '"' and "${" not in raw:
        return raw[1:-1].replace('\\"', '"')
    return None


def _number(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# -- parsing ---------------------------------------------------------------------------------


class Ctx:
    def __init__(self, locator, content, configs=(), blocks=()):
        self.locator = locator
        self.lines = content.splitlines()
        self.configs = list(configs)
        self.blocks = list(blocks)


def _not_evaluated(locator):
    marked = _dev_path(locator)
    if marked:
        raise NotEvaluated(f"path marks a development/test config ({marked}); OBS-10 v1 evaluates deployed configs only")


def parse(locator, content):
    lower = locator.lower()
    rule = DD_RULE in content
    if lower.endswith(".tf"):
        if not _DD_RESOURCE.search(content) and not rule:
            raise Unsupported(locator)
        _not_evaluated(locator)
        try:
            return Ctx(locator, content, blocks=parse_hcl(content))
        except HclError as error:
            raise ParseError(str(error)) from None
    if lower.endswith((".yaml", ".yml")) and otelconfig.might_contain_config(content):
        _not_evaluated(locator)
        try:
            configs = otelconfig.find_configs(miniyaml.load_all(content), content.splitlines())
        except miniyaml.YamlError as error:
            raise ParseError(str(error)) from None
        if not configs and not rule:
            raise NotEvaluated(
                "no OpenTelemetry Collector config with service.pipelines (plain, OpenTelemetryCollector or "
                "ConfigMap); Helm chart values are not evaluated"
            )
        return Ctx(locator, content, configs)
    if rule:  # a Datadog Agent config, annotation or env file: source-side context only
        _not_evaluated(locator)
        return Ctx(locator, content)
    raise Unsupported(locator)


# -- collector agent -> gateway --------------------------------------------------------------


def _base(name):
    name = name.lower()
    stripped = _SUFFIX.sub("", name)
    return {name, stripped, f"{stripped}-collector"}


def aliases(locator, config):
    """Host names a collector config is reachable under (first DNS label)."""
    match = re.match(r"(OpenTelemetryCollector|ConfigMap)/([^:]+):", config.label)
    if match and match.group(1) == "OpenTelemetryCollector":
        name = match.group(2).lower()
        return {name, f"{name}-collector", f"{name}-collector-headless"}
    if match:
        return _base(match.group(2))
    parts = [p for p in re.split(r"[\\/]", locator) if p]
    stem = re.sub(r"\.ya?ml$", "", parts[-1], flags=re.I)
    names = _base(stem)
    if stem.lower() in GENERIC_STEMS and len(parts) > 1:
        names |= _base(parts[-2])
    return names


def _filter_drops(config, processor_id):
    """True: a defined filter with conditions; None: undefined, empty or unresolved (unknown)."""
    component = config.component("processors", processor_id)
    if component is None or not isinstance(component.node, Mapping):
        return None
    settings = {key: value for key, value in component.node.items.items() if key != "error_mode"}
    values = [v for node in settings.values() for v in _scalars(node)]
    if not values or any(is_unresolved(value) for value in values):
        return None
    return True


def _scalars(node):
    if isinstance(node, Scalar):
        return [node.value] if node.value else []
    if isinstance(node, Mapping):
        return [v for child in node.items.values() for v in _scalars(child)]
    if isinstance(node, Sequence):
        return [v for child in node.items for v in _scalars(child)]
    return []


def gateway_drops(config, signal):
    """[(pipeline, filter ids, enrichers before the first filter)] when every OTLP-fed `signal` pipeline of
    the gateway that exports somewhere drops data with a filter; None when it keeps (or may keep) the full
    stream or cannot be judged."""
    connectors = config.sections.get("connectors", {})
    pipelines = [p for p in config.pipelines.values() if p.signal == signal
                 and (p.receivers is None or any(component_type(r) == "otlp" for r in p.receivers))]
    found = []
    for pipeline in pipelines:
        if None in (pipeline.receivers, pipeline.processors, pipeline.exporters):
            return None
        exporters = [e for e in pipeline.exporters if component_type(e) not in LOCAL_EXPORTERS]
        if not exporters:
            continue
        filters = [p for p in pipeline.processors if component_type(p) == "filter"]
        if not filters or any(e in connectors for e in exporters):
            return None  # the full stream is kept here (e.g. an audit/archive pipeline) or routed on
        if any(_filter_drops(config, p) is None for p in filters):
            return None
        first = pipeline.processors.index(filters[0])
        enrichers = [p for p in pipeline.processors[:first] if component_type(p) in ENRICHERS]
        found.append((pipeline, filters, enrichers))
    return found or None


def _filters_at_source(config, pipeline):
    if any(component_type(p) in REDUCERS for p in pipeline.processors):
        return True
    for receiver_id in pipeline.receivers:
        component = config.component("receivers", receiver_id)
        operators = get(component.node, "operators") if component else None
        if isinstance(operators, Sequence) and any(text(get(op, "type")) == "filter" for op in operators.items):
            return True
    return False


def _endpoint_host(settings, signal):
    value = text(get(settings, f"{signal}_endpoint")) or text(get(settings, "endpoint"))
    if not value or is_unresolved(value):
        return None
    return _host(value).lower().split(".", 1)[0]  # loopback hosts never name a gateway config


def _where(locator, config):
    return f"{locator} ({config.label.rstrip(':')})" if config.label else locator


def _gateway_hits(config, project):
    connectors = config.sections.get("connectors", {})
    for pipeline in config.pipelines.values():
        if pipeline.signal not in SIGNALS or None in (pipeline.receivers, pipeline.processors, pipeline.exporters):
            continue
        if any(r in connectors for r in pipeline.receivers) or _filters_at_source(config, pipeline):
            continue
        for exporter_id in pipeline.exporters:
            component = config.component("exporters", exporter_id)
            if component_type(exporter_id) not in OTLP_EXPORTERS or component is None:
                continue
            host = _endpoint_host(component.node, pipeline.signal)
            gateways = [(loc, gw) for loc, gw in project.collectors
                        if gw is not config and host and host in aliases(loc, gw)]
            verdicts = [gateway_drops(gw, pipeline.signal) for _, gw in gateways]
            if not gateways or any(v is None for v in verdicts):
                continue
            parts, enriched = [], []
            for (loc, gw), drops in zip(gateways, verdicts):
                ids = ", ".join(f"{p.id!r} ({', '.join(f)})" for p, f, _ in drops)
                parts.append(f"{_where(loc, gw)}: {ids}")
                enriched.extend(e for _, _, enrichers in drops for e in enrichers)
            note = (f" The gateway filter runs after {', '.join(sorted(set(enriched)))}; confirm its conditions do "
                    "not need attributes only the gateway adds." if enriched else "")
            yield TextHit(
                line=pipeline.line,
                end_line=pipeline.end_line,
                anchor=f"{config.label}pipeline/{pipeline.id}:exporter/{exporter_id}:filtered-downstream",
                summary=(
                    f"Pipeline {pipeline.id!r} sends all its {pipeline.signal} through {exporter_id} to {host}, a "
                    f"gateway collector whose {pipeline.signal} pipelines drop data with a filter processor "
                    f"({'; '.join(parts)}). This pipeline declares no filter, sampler or filter operator, so the "
                    f"dropped {pipeline.signal} are still serialized, sent and received before they are "
                    f"discarded.{note}"
                ),
                confidence="low",
                recommendation=REC_GATEWAY,
            )


# -- Datadog index exclusion filters ---------------------------------------------------------


@dataclass(frozen=True)
class Exclusion:
    index: str
    name: str
    query: str
    line: int
    end_line: int


def exclusions(blocks):
    """Enabled exclusion filters of `datadog_logs_index` resources that exclude 100% of their matches."""
    found = []
    for resource in blocks:
        if resource.type != "resource" or resource.labels[:1] != ("datadog_logs_index",) or len(resource.labels) < 2:
            continue
        for number, block in enumerate(resource.blocks("exclusion_filter"), 1):
            if block.attrs.get("is_enabled", ("",))[0] != "true":
                continue
            filters = block.blocks("filter")
            rate = _number(filters[0].attrs.get("sample_rate", ("",))[0]) if filters else None
            if rate is None or rate < 1:
                continue
            name = _string(block.attrs.get("name", ("",))[0]) or f"#{number}"
            query = _string(filters[0].attrs.get("query", ("",))[0]) or ""
            found.append(Exclusion(resource.labels[1], name, query, block.line, block.end_line))
    return found


# -- project ---------------------------------------------------------------------------------


class Project:
    def __init__(self):
        self.collectors = []  # (locator, CollectorConfig)
        self.exclusions = 0
        self.rules, self.archives, self.metrics = [], [], []

    def add(self, ctx):
        self.collectors.extend((ctx.locator, config) for config in ctx.configs)
        self.exclusions += len(exclusions(ctx.blocks))

    def scan_text(self, locator, content):
        """Source-side rules and archives count even when the file itself cannot be parsed."""
        if _dev_path(locator):
            return
        if DD_RULE in content:
            self.rules.append(locator)
        kinds = {m.group(1) for m in _DD_RESOURCE.finditer(content)}
        if "archive" in kinds:
            self.archives.append(locator)
        if "metric" in kinds:
            self.metrics.append(locator)

    @property
    def judgeable(self):
        return bool(self.collectors) or bool(self.exclusions)

    def datadog_exempt(self):
        if self.rules:
            return (f"{self.rules[0]} declares a Datadog Agent {DD_RULE} rule (source-side filtering; queries are "
                    "not matched)")
        if self.archives:
            return f"{self.archives[0]} declares a datadog_logs_archive, so excluded logs are still archived"
        return None


def collect(payload):
    """Parse every evaluable source once: ({(locator, content): Ctx or exception}, Project)."""
    parsed, project = {}, Project()
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        return parsed, project
    scope = payload.get("scope") if isinstance(payload.get("scope"), list) else []
    for scope_id in scope:
        statics = [s for s in payload["sources"] if isinstance(s, dict) and s.get("scope_id") == scope_id
                   and s.get("kind") == textstatic.SUPPORTED_KIND]
        if len(statics) != 1:
            continue
        locator, content = statics[0].get("locator"), statics[0].get("content")
        if not isinstance(locator, str) or not isinstance(content, str):
            continue
        project.scan_text(locator, content)
        try:
            ctx = parse(locator, content)
        except Exception as error:  # reported per file by the textstatic runner
            parsed[(locator, content)] = error
            continue
        parsed[(locator, content)] = ctx
        project.add(ctx)
    return parsed, project


def run(ctx, project):
    hits = []
    for config in ctx.configs:
        hits.extend(_gateway_hits(config, project))
    if project.datadog_exempt():
        return hits
    metric = ""
    if project.metrics:
        metric = (f" {project.metrics[0]} declares a datadog_logs_metric, which can still use excluded logs; "
                  "confirm these logs do not feed it.")
    for item in exclusions(ctx.blocks):
        hits.append(TextHit(
            line=item.line,
            end_line=item.end_line,
            anchor=f"datadog_logs_index.{item.index}:exclusion_filter/{item.name}",
            summary=(
                f"Exclusion filter {item.name!r} of Datadog index {item.index!r} drops 100% of the logs matching "
                f"{repr(item.query) if item.query else 'its filter (no literal query)'} (sample_rate = 1.0) after "
                "ingestion: excluded logs are still ingested and billed "
                f"for ingestion, only not indexed. No Datadog Agent {DD_RULE} rule is declared in the payload."
                f"{metric}"
            ),
            confidence="low" if metric else "medium",
            recommendation=REC_DATADOG,
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    parsed, project = collect(payload)

    def bound_parse(locator, content):
        ctx = parsed.get((locator, content))
        if ctx is None:
            ctx = parse(locator, content)
        if isinstance(ctx, Exception):
            raise ctx
        if not ctx.configs and not ctx.blocks and not project.judgeable:
            raise NotEvaluated(
                "supplies only source-side filtering context, and the payload has no collector config or Datadog "
                "index exclusion filter to judge it against"
            )
        return ctx

    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES, FORMATS=FORMATS,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, parse=bound_parse,
        run=lambda ctx: module.run(ctx, project),
    )
    result = textstatic.evaluate_text(payload, check)
    exempt = project.datadog_exempt()
    if exempt and project.exclusions:
        result["coverage"]["limitations"].insert(
            -1, f"{project.exclusions} Datadog index exclusion filter(s) dropping 100% were not flagged: {exempt}")
    return result
