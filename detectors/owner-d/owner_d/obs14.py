"""OBS-14: dev/QA/staging telemetry ingested by default (static non-production config scan).

Detector semantics version 1.0.0. Reads configuration files as text and flags non-production
telemetry that is shipped to a managed (paid) backend without any volume reduction:

- OpenTelemetry SDK environment settings (Kubernetes/Compose/SAM env blocks, .env,
  .properties, TOML, INI, Dockerfile ENV) that export traces to a managed OTLP endpoint while
  the sampler keeps every trace (explicitly, or by the spec default), or export logs there;
- OpenTelemetry Collector traces/logs pipelines that forward to a managed exporter with no
  sampling or filter processor;
- AWS X-Ray sampling rules with a fixed rate of 100%.

A finding needs a non-production marker (a `deployment.environment[.name]` attribute, key
names, the file path or `context.environment`), a managed destination and no reduction.
Nothing is executed, rendered or resolved, so no measurements are emitted.
"""

from __future__ import annotations

import functools
import re
import sys
import types
from dataclasses import dataclass

from . import otelconfig, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .obs01 import CI_PATH, _clean, file_format, level_key_index, verbose_level
from .obs05 import (
    ALWAYS_ON,
    LOCAL_SCOPE,
    RATIO,
    SAMPLER,
    SAMPLER_ARG,
    SDK_DISABLED,
    TRACES_EXPORTER,
    ConfigCtx,
    _mappings,
    _scalar,
    keeps_all,
    literal,
    match_key,
)
from .obs09 import ENDPOINT_KEYS, OTLP_EXPORTERS, _endpoint_kind, _host
from .otelconfig import get, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "OBS-14"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-14", "OBS14")
FORMATS = (
    "configuration files with OpenTelemetry exporter/sampler settings, OpenTelemetry Collector configs or X-Ray "
    "sampling rules (YAML, JSON, TOML, INI/CFG, .properties, .env files and Dockerfiles)"
)

REFERENCES = (
    "https://opentelemetry.io/docs/specs/semconv/resource/deployment-environment/",
    "https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/",
    "https://docs.aws.amazon.com/xray/latest/devguide/xray-console-sampling.html",
    "https://aws.amazon.com/xray/pricing/",
    "https://docs.newrelic.com/docs/new-relic-solutions/observability-maturity/operational-efficiency/"
    "data-governance-optimize-ingest-guide/",
    "https://oneuptime.com/blog/post/2026-02-06-probabilistic-sampler-processor-opentelemetry-collector/view",
)
REC_TRACES = (
    "Sample non-production traces (for example OTEL_TRACES_SAMPLER=parentbased_traceidratio with "
    "OTEL_TRACES_SAMPLER_ARG=0.1 or lower), send them to a local or self-hosted backend, or drop/sample them by "
    "deployment.environment.name in a collector before the paid backend. Set deployment.environment.name so the "
    "backend can filter by environment."
)
REC_LOGS = (
    "Keep non-production logs at INFO or WARN, and filter or sample them by deployment.environment.name in a "
    "collector before the paid backend (or keep them out of it). Enable DEBUG only temporarily."
)
REC_PIPELINE = (
    "Add a probabilistic_sampler or tail_sampling processor (traces) or a filter processor (logs) to this "
    "non-production pipeline, or route non-production data to a cheaper or local backend."
)
REC_XRAY = (
    "Use a small reservoir and a FixedRate well below 1 (for example 0.05) for non-production services; keep 100% "
    "rules short-lived and narrowly scoped for debugging."
)
RECOMMENDATION = REC_TRACES
LIMITATION = (
    "Static configuration scan only: OBS-14 v1 proves that a non-production config ships unreduced telemetry to a "
    "managed backend, not the ingested volume or its cost, so no measurements are emitted. The environment is "
    "inferred from deployment.environment attributes, key names and the file path (heuristic; confidence is "
    "never high). Only endpoints on known managed ingest domains and vendor exporters count as paid; in-cluster or "
    "self-hosted collector endpoints, unresolved ${...} values, runtime overrides, env_file/envFrom/Secrets, "
    "samplers set in code or remotely, --config merges and probabilistic sampler percentages are not judged. Files "
    "with no non-production marker and test/docs/example material are not evaluated."
)

PROD = frozenset({"prod", "production", "prd", "live"})
NONPROD = frozenset({
    "dev", "develop", "development", "local", "staging", "stage", "stg", "qa", "uat", "test", "testing", "sandbox",
    "preprod", "nonprod",
})
# `stage` is also an ordinary key (serverless `provider.stage`), so it only marks paths.
KEY_NONPROD = NONPROD - {"stage"}
# Single-developer environments: little volume, and full sampling there is a common choice.
LOW_VOLUME = frozenset({"dev", "develop", "development", "local"})
# Test suites, docs and copy-me examples are not deployed environments.
EXCLUDED_DIRS = frozenset({
    "tests", "testdata", "fixture", "fixtures", "e2e", "mock", "mocks", "doc", "docs", "example", "examples",
    "sample", "samples", "tutorial", "tutorials", "spec", "specs",
})
EXCLUDED_NAMES = frozenset({"example", "examples", "sample", "samples", "template", "dist", "conftest"})

# Managed ingest domains (suffix match on the endpoint host).
MANAGED_DOMAINS = (
    "amazonaws.com", "honeycomb.io", "nr-data.net", "datadoghq.com", "datadoghq.eu", "ddog-gov.com", "grafana.net",
    "lightstep.com", "signalfx.com", "dynatrace.com", "cloud.es.io", "googleapis.com", "coralogix.com",
    "coralogix.us", "logz.io", "sumologic.com", "sumologic.net", "axiom.co", "uptrace.dev", "signoz.cloud",
    "oneuptime.com",
)
# Collector exporters whose only destination is a vendor/AWS backend.
VENDOR_EXPORTERS = frozenset({
    "awsxray", "awscloudwatchlogs", "datadog", "googlecloud", "azuremonitor", "coralogix", "logzio", "sumologic",
    "sapm", "alibabacloud_logservice",
})
REDUCERS = frozenset({"probabilistic_sampler", "tail_sampling", "filter", "logdedup"})
ITEMS = {"traces": "span", "logs": "log record"}
ATTRIBUTE_PROCESSORS = frozenset({"resource", "attributes"})
ENV_ATTRIBUTES = ("deployment.environment.name", "deployment.environment")

ENDPOINT = ("otel", "exporter", "otlp", "endpoint")
KEYS = (
    ("sampler", SAMPLER), ("arg", SAMPLER_ARG), ("disabled", SDK_DISABLED), ("traces_exporter", TRACES_EXPORTER),
    ("endpoint", ENDPOINT), ("traces_endpoint", ("otel", "exporter", "otlp", "traces", "endpoint")),
    ("logs_endpoint", ("otel", "exporter", "otlp", "logs", "endpoint")),
    ("logs_exporter", ("otel", "logs", "exporter")), ("resource", ("otel", "resource", "attributes")),
)
_RELEVANT = re.compile(
    r"(?i)otel[._-](exporter[._-]otlp|traces[._-](sampler|exporter)|logs[._-]exporter|resource[._-]attributes)"
    r"|FixedRate|fixed_target"
)


def _tokens(*texts):
    found = set()
    for value in texts:
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value)).lower()
        value = re.sub(r"\b(non|pre)[-_ .]?prod(uction)?\b", r"\1prod", value)
        found |= {token for token in re.split(r"[^a-z0-9]+", value) if token}
    return found


@dataclass(frozen=True)
class Env:
    name: str
    source: str
    tokens: frozenset

    @property
    def where(self):
        return f"Non-production configuration ({self.name}, from {self.source})"


def _declared_env(value):
    tokens = _tokens(value or "")
    if tokens & PROD or not tokens & NONPROD:
        return None
    return Env(value.strip(), "the scan context", frozenset(tokens))


def _environment(ctx, key_texts, attribute):
    """The non-production Env of one setting block, pipeline or rule, else None."""
    if attribute is not None:
        tokens = _tokens(attribute)
        if tokens & NONPROD and not tokens & PROD:
            return Env(attribute, "its deployment.environment attribute", frozenset(tokens))
        return None
    for tokens, allowed, source in ((_tokens(*key_texts), KEY_NONPROD, "its key names"),
                                    (ctx.path_tokens, NONPROD, "the file path")):
        if tokens & PROD:
            return None
        marked = sorted(tokens & allowed)
        if marked:
            return Env(marked[0], source, frozenset(tokens))
    return ctx.declared


def _excluded(locator):
    parts = [part.lower().lstrip(".") for part in re.split(r"[\\/]", locator)]
    marked = sorted(_tokens(*parts[:-1]) & EXCLUDED_DIRS | _tokens(parts[-1]) & EXCLUDED_NAMES)
    if marked:
        return marked[0]
    return "ci" if CI_PATH.search(locator.replace("\\", "/")) else None


def _managed(value):
    """The endpoint host if `value` is a literal endpoint on a managed ingest domain."""
    endpoint = literal(value)
    if not endpoint:
        return None
    host = _host(endpoint).lower().rstrip(".")
    return host if any(host == domain or host.endswith("." + domain) for domain in MANAGED_DOMAINS) else None


def _env_attribute(raw):
    """deployment.environment[.name] from an OTEL_RESOURCE_ATTRIBUTES value, if literal."""
    for pair in _clean(str(raw or "")).split(","):
        key, _, value = pair.partition("=")
        if key.strip() in ENV_ATTRIBUTES and value.strip() and "$" not in value:
            return value.strip()
    return None


# -- configuration settings -------------------------------------------------------------------


@dataclass(frozen=True)
class Setting:
    entry: object  # obs05.Entry
    name: str  # the matched key, e.g. OTEL_TRACES_SAMPLER

    @property
    def value(self):
        return literal(self.entry.value)


@dataclass
class Group:
    settings: dict  # kind -> [Setting]
    levels: list  # (key, level) DEBUG/TRACE log levels in the same block
    attribute: str | None = None  # literal deployment.environment[.name] from OTEL_RESOURCE_ATTRIBUTES
    env: Env | None = None

    def last(self, kind):
        found = self.settings.get(kind)
        return found[-1] if found else None


def _groups(ctx, entries):
    groups, prefixes = {}, {}
    for entry in entries:
        for kind, suffix in KEYS:
            count = match_key(entry.keys, suffix)
            if count:
                prefix = () if entry.block == ("dockerfile",) else entry.keys[:-count]
                group = groups.setdefault((entry.block, prefix), Group({}, []))
                group.settings.setdefault(kind, []).append(Setting(entry, ".".join(entry.keys[-count:])))
                prefixes.setdefault((entry.block, prefix), set()).update(entry.keys[:-count])
                break
    for entry in entries:
        key = (entry.block, () if entry.block == ("dockerfile",) else entry.keys[:-1])
        level = verbose_level(literal(entry.value))
        if key in groups and level and level_key_index(entry.keys[-1:]) is not None:
            groups[key].levels.append((entry.keys[-1], level))
    for key, group in groups.items():
        resource = group.last("resource")
        group.attribute = _env_attribute(resource.entry.value) if resource else None
        group.env = _environment(ctx, prefixes[key], group.attribute)
    return list(groups.values())


def _exporters(setting):
    value = setting.value
    return {part.strip().lower() for part in value.split(",")} if value else set()


def _confidence(env, explicit):
    return "medium" if explicit and not env.tokens & LOW_VOLUME else "low"


def _attribute_note(group):
    if group.attribute is not None:
        return ""
    return (" No deployment.environment.name resource attribute is set in this block, so the backend cannot drop "
            "or sample this data by environment.")


def _hit(setting, anchor, summary, confidence, recommendation):
    entry = setting.entry
    return TextHit(line=entry.first, end_line=entry.last, anchor=anchor, summary=summary, confidence=confidence,
                   recommendation=recommendation)


def _trace_hit(group):
    exporter = group.last("traces_exporter")
    if exporter is not None and "otlp" not in _exporters(exporter):
        return None
    endpoint = group.last("traces_endpoint") or group.last("endpoint")
    host = _managed(endpoint.entry.value) if endpoint else None
    if not host:
        return None
    sampler, arg = group.last("sampler"), group.last("arg")
    value = (sampler.value or "").lower() if sampler else None
    if sampler is None:
        explicit, at = False, endpoint
        how = ("sets no OTEL_TRACES_SAMPLER in this block, so the SDK default parentbased_always_on keeps every "
               "root trace unless the sampler is set elsewhere")
    elif value in ALWAYS_ON:
        explicit, at = True, sampler
        how = f"sets {sampler.name}={sampler.value}, so every {'root ' if ALWAYS_ON[value] else ''}trace is kept"
    elif value in RATIO and arg is None:
        explicit, at = False, sampler
        how = (f"sets {sampler.name}={sampler.value} without a sampler argument in this block; the specified "
               "default ratio is 1.0, so every trace is kept unless the ratio is set elsewhere")
    elif value in RATIO and keeps_all(arg.value):
        explicit, at = True, arg
        how = f"sets {sampler.name}={sampler.value} with {arg.name}={arg.value}: a sampling ratio of 100%"
    else:
        return None
    summary = (f"{group.env.where} exports traces to the managed backend {host} and {how}; all of this "
               f"non-production trace volume is ingested.{_attribute_note(group)}")
    return _hit(at, f"nonprod-traces:{'.'.join(endpoint.entry.keys)}", summary,
                _confidence(group.env, explicit), REC_TRACES)


def _logs_hit(group):
    exporter = group.last("logs_exporter")
    if exporter is None or "otlp" not in _exporters(exporter):
        return None
    endpoint = group.last("logs_endpoint") or group.last("endpoint")
    host = _managed(endpoint.entry.value) if endpoint else None
    if not host:
        return None
    if group.levels:
        key, level = group.levels[-1]
        how = f"{key} enables {level.upper()} logging in the same block, so every {level}-level record is ingested"
    else:
        how = "the SDK does not sample logs, so every exported record is ingested"
    summary = (f"{group.env.where} sets {exporter.name}={exporter.value} and exports logs to the managed backend "
               f"{host}; {how}.{_attribute_note(group)}")
    return _hit(exporter, f"nonprod-logs:{'.'.join(exporter.entry.keys)}", summary,
                _confidence(group.env, bool(group.levels)), REC_LOGS)


# -- collector pipelines ----------------------------------------------------------------------


def _pipeline_attribute(config, pipeline):
    for processor_id in pipeline.processors or ():
        component = config.component("processors", processor_id)
        if component is None or component.type not in ATTRIBUTE_PROCESSORS:
            continue
        actions = get(component.node, "attributes")
        for action in actions.items if isinstance(actions, Sequence) else ():
            value = text(get(action, "value"))
            if text(get(action, "key")) in ENV_ATTRIBUTES and value and not otelconfig.is_unresolved(value):
                return str(value)
    return None


def _destinations(config, pipeline):
    found = []
    for exporter_id in pipeline.exporters:
        component = config.component("exporters", exporter_id)
        if component is None:
            continue
        if component.type in VENDOR_EXPORTERS:
            if _endpoint_kind(component.node) != "loopback":
                found.append(exporter_id)
        elif component.type in OTLP_EXPORTERS:
            hosts = [_managed(text(get(component.node, key))) for key in ENDPOINT_KEYS]
            host = next((host for host in hosts if host), None)
            if host:
                found.append(f"{exporter_id} ({host})")
    return found


def _pipeline_units(ctx):
    for config in ctx.configs:
        for pipeline in config.pipelines.values():
            if pipeline.signal in ("traces", "logs"):
                env = _environment(ctx, [config.label], _pipeline_attribute(config, pipeline))
                yield config, pipeline, env


def _pipeline_hit(config, pipeline, env):
    connectors = config.sections.get("connectors", {})
    if None in (pipeline.receivers, pipeline.processors, pipeline.exporters):
        return None
    if any(receiver in connectors for receiver in pipeline.receivers):
        return None
    if any(otelconfig.component_type(processor) in REDUCERS for processor in pipeline.processors):
        return None
    destinations = _destinations(config, pipeline)
    if not destinations:
        return None
    return TextHit(
        line=pipeline.line,
        end_line=pipeline.end_line,
        anchor=f"{config.label}pipeline/{pipeline.id}:nonprod-export",
        summary=(f"{env.where}: collector pipeline {pipeline.id!r} forwards every {ITEMS[pipeline.signal]} it "
                 f"receives to {', '.join(destinations)} with no sampling or filter processor."),
        confidence="low",
        recommendation=REC_PIPELINE,
    )


# -- X-Ray sampling rules ---------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    node: Mapping
    rate_key: str
    name: str
    env: Env | None


def _xray_rules(ctx, docs):
    for doc in docs:
        default = doc.items.get("default") if isinstance(doc, Mapping) else None
        if isinstance(default, Mapping) and "rate" in default.items and "fixed_target" in default.items:
            yield Rule(default, "rate", "default", _environment(ctx, [], None))
            rules = doc.items.get("rules")
            for rule in rules.items if isinstance(rules, Sequence) else ():
                if isinstance(rule, Mapping) and "rate" in rule.items:
                    name = ":".join(_scalar(rule, key) or "*" for key in LOCAL_SCOPE)
                    yield Rule(rule, "rate", name, _environment(ctx, [_scalar(rule, "service_name") or ""], None))
        for node, keys in _mappings(doc, []):
            if "FixedRate" not in node.items:
                continue
            name = _scalar(node, "RuleName")
            if not name:
                name = keys[1] if len(keys) > 1 and keys[0] == "Resources" else ".".join(keys) or "rule"
            names = keys + [name, _scalar(node, "ServiceName") or ""]
            yield Rule(node, "FixedRate", name, _environment(ctx, names, None))


def _rule_hit(rule):
    rate = rule.node.items[rule.rate_key]
    if not isinstance(rate, Scalar) or not keeps_all(literal(rate.value)):
        return None
    line = rule.node.key_lines.get(rule.rate_key, rate.line)
    return TextHit(
        line=line,
        end_line=rate.line,
        anchor=f"xray-sampling-rule:{rule.name}",
        summary=(f"{rule.env.where} defines X-Ray sampling rule {rule.name} with {rule.rate_key}={rate.value}: every "
                 "matching non-production request beyond the reservoir is traced and recorded."),
        confidence=_confidence(rule.env, True),
        recommendation=REC_XRAY,
    )


# -- runner -----------------------------------------------------------------------------------


class Ctx:
    def __init__(self, locator, content, fmt, declared):
        config = ConfigCtx(locator, content, fmt, False)
        self.lines = config.lines
        self.path_tokens = frozenset(_tokens(locator))
        self.declared = declared
        self.groups = _groups(self, config.entries) if _RELEVANT.search(content) else []
        self.configs = []
        if fmt == "yaml" and otelconfig.might_contain_config(content):
            self.configs = otelconfig.find_configs(config.docs, self.lines)
        self.pipelines = list(_pipeline_units(self))
        self.rules = list(_xray_rules(self, config.docs))

    def environments(self):
        return ([group.env for group in self.groups] + [env for _, _, env in self.pipelines]
                + [rule.env for rule in self.rules])


def parse(locator, content, declared=None):
    fmt = file_format(locator)
    if fmt in (None, "python") or not (_RELEVANT.search(content) or otelconfig.might_contain_config(content)):
        raise Unsupported(locator)
    excluded = _excluded(locator)
    if excluded:
        raise NotEvaluated(f"path marks test, docs, example or CI material ({excluded}), not a deployed environment")
    try:
        ctx = Ctx(locator, content, fmt, declared)
    except (ValueError, RecursionError) as error:
        raise ParseError(str(error) or type(error).__name__) from None
    environments = ctx.environments()
    if not environments:
        raise NotEvaluated("no OpenTelemetry exporter/sampler settings, collector traces/logs pipelines or X-Ray "
                           "sampling rules")
    if not any(environments):
        raise NotEvaluated("no non-production marker in the path, key names or deployment.environment attribute; "
                           "OBS-14 v1 judges non-production configs only")
    return ctx


def run(ctx):
    hits = []
    for group in ctx.groups:
        disabled = group.last("disabled")
        if group.env and not (disabled and (disabled.value or "").lower() == "true"):
            hits.extend(hit for hit in (_trace_hit(group), _logs_hit(group)) if hit)
    for config, pipeline, env in ctx.pipelines:
        hit = _pipeline_hit(config, pipeline, env) if env else None
        hits.extend([hit] if hit else [])
    for rule in ctx.rules:
        hit = _rule_hit(rule) if rule.env else None
        hits.extend([hit] if hit else [])
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    declared = context.get("environment") if isinstance(context, dict) else None
    if declared is not None and not isinstance(declared, str):
        raise EvaluationError("context.environment must be a string when present")
    module = sys.modules[__name__]
    check = types.SimpleNamespace(**{
        name: getattr(module, name)
        for name in ("CHECK_ID", "DETECTOR_VERSION", "NOQA", "FORMATS", "REFERENCES", "RECOMMENDATION",
                     "LIMITATION", "run")
    })
    check.parse = functools.partial(parse, declared=_declared_env(declared))
    return textstatic.evaluate_text(payload, check)
