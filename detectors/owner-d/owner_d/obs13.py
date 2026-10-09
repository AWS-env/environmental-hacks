"""OBS-13: noisy health-check/probe telemetry (static collector filter + probe scan).

Detector semantics version 1.0.0. Reads, as text and project-wide, OpenTelemetry Collector
configs (plain files, `OpenTelemetryCollector` resources, ConfigMaps; ADOT uses the same
format), Kubernetes workload manifests and source files with OpenTelemetry URL-exclusion
hooks, and flags a collector `traces` pipeline that ingests app spans and exports them while
nothing in the payload drops the spans of Kubernetes liveness/readiness probes that hit
OpenTelemetry-instrumented workloads over HTTP.

A probe is dropped when a `filter` processor of any traces pipeline in the payload matches its
path, when a traces pipeline has `tail_sampling` (policies are not judged), or when the SDK
excludes the route (`OTEL_PYTHON_*EXCLUDED_URLS`, `excluded_urls=`, `ignoreIncomingRequestHook`,
...). Nothing is executed, rendered or resolved; span counts are not observed, so no
measurements are emitted (the summary estimates the declared probe rate).
"""

from __future__ import annotations

import re
import sys
import types
from dataclasses import dataclass

from . import miniyaml, otelconfig, textstatic
from .inf08 import POD_SPEC
from .miniyaml import Mapping, Scalar, Sequence
from .obs09 import _dev_path
from .otelconfig import component_type, get, is_unresolved, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401
from .tst12 import is_test_path

CHECK_ID = "OBS-13"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-13", "OBS13")
FORMATS = (
    "OpenTelemetry Collector configs and Kubernetes workload manifests (.yaml/.yml), and source files with "
    "OpenTelemetry URL-exclusion hooks"
)

REFERENCES = (
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/filterprocessor/README.md",
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md",
    "https://opentelemetry.io/docs/specs/semconv/http/http-spans/",
    "https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
    "https://opentelemetry.io/docs/platforms/kubernetes/operator/automatic/",
    "https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html",
    "https://oneuptime.com/blog/post/2026-02-06-filter-processor-drop-health-check-telemetry/view",
)
RECOMMENDATION = (
    "Drop successful probe spans before export: add a `filter` processor to the traces pipeline with a condition "
    "such as `span.attributes[\"url.path\"] == \"/healthz\" and span.attributes[\"http.response.status_code\"] < 400` "
    "(older instrumentation sets `http.target`; `user_agent.original` starting with `kube-probe/` matches every "
    "probe), or exclude the routes in the SDK (e.g. OTEL_PYTHON_EXCLUDED_URLS). Keep failing probes: they explain "
    "restarts and pods taken out of service."
)
LIMITATION = (
    "Static scan only: OBS-13 matches Kubernetes HTTP liveness/readiness probes of OpenTelemetry-instrumented "
    "containers against the collector traces pipelines in the same payload; it does not count spans, so no "
    "measurements are emitted. Not visible: collectors, filters and workloads in other repositories, Helm "
    "templates/values, namespace-level operator annotations, Instrumentation sampler settings, SDK exclusions "
    "other than OTEL_PYTHON_*EXCLUDED_URLS and string literals next to the listed code hooks, and HPA replica "
    "counts. Kubernetes events, probe access logs, static-asset 404s and load-balancer health checks are out of "
    "scope for v1; startup/tcpSocket/exec/grpc probes and development/test files are not judged."
)

SPAN_RECEIVERS = {"otlp", "zipkin", "jaeger"}
LOCAL_EXPORTERS = {"debug", "logging", "nop", "file"}
PROBES = ("livenessProbe", "readinessProbe")  # startupProbe stops after the first success
DEFAULT_PERIOD = 10  # Kubernetes default periodSeconds
INJECT = "instrumentation.opentelemetry.io/inject-"
CONTAINER_NAMES = "instrumentation.opentelemetry.io/container-names"
ENDPOINT_ENV = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
COLLECTOR_IMAGES = ("opentelemetry-collector", "otelcol", "aws-otel-collector")
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".go", ".cs", ".java", ".kt")
HOOKS = ("excluded_urls", "ignoreIncomingRequestHook", "ignoreIncomingPaths", "WithFilter(",
         "AddAspNetCoreInstrumentation", "RuleBasedRoutingSampler")
HOOK_WINDOW = 6  # the hook line and the next 5 lines
# Workload kinds whose replica count is spec.replicas (default 1); DaemonSets run one pod per node.
REPLICATED = {"Deployment", "StatefulSet", "ReplicaSet", "ReplicationController", "DeploymentConfig", "Rollout"}

UNKNOWN = object()  # an env value set through valueFrom
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')


@dataclass(frozen=True)
class Probe:
    path: str
    probe: str  # liveness | readiness
    period: int
    replicas: int | None  # None: one pod per node (DaemonSet)
    workload: str  # e.g. Deployment/api
    locator: str


class Ctx:
    def __init__(self, locator, content, configs=(), probes=(), hook_literals=()):
        self.locator = locator
        self.lines = content.splitlines()
        self.configs = list(configs)
        self.probes = list(probes)
        self.hook_literals = list(hook_literals)


# -- parsing ---------------------------------------------------------------------------------


def parse(locator, content):
    lower = locator.lower()
    if lower.endswith(CODE_SUFFIXES):
        if not any(hook in content for hook in HOOKS):
            raise Unsupported(locator)
        if is_test_path(locator) or _dev_path(locator):
            raise NotEvaluated("development/test file; OBS-13 v1 evaluates deployed configuration only")
        return Ctx(locator, content, hook_literals=_hook_literals(content.splitlines()))
    if not lower.endswith((".yaml", ".yml")):
        raise Unsupported(locator)
    collector = otelconfig.might_contain_config(content)
    workload = "httpGet" in content and ("OTEL_" in content or INJECT in content)
    if not collector and not workload:
        raise Unsupported(locator)
    marked = _dev_path(locator)
    if marked:
        raise NotEvaluated(
            f"path marks a development/test config ({marked}); OBS-13 v1 evaluates deployed configs only"
        )
    lines = content.splitlines()
    try:
        docs = miniyaml.load_all(content)
        configs = otelconfig.find_configs(docs, lines) if collector else []
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    probes = list(_probes(docs, locator, lines)) if workload else []
    if not configs and not probes:
        raise NotEvaluated(
            "no OpenTelemetry Collector config with service.pipelines and no OpenTelemetry-instrumented workload "
            "with an HTTP liveness/readiness probe"
        )
    return Ctx(locator, content, configs, probes)


def _literals(value):
    return [match.group(1) if match.group(1) is not None else match.group(2) for match in _LITERAL.finditer(value)]


def _hook_literals(lines):
    found = []
    for index, line in enumerate(lines):
        if any(hook in line for hook in HOOKS):
            for literal in _literals("\n".join(lines[index:index + HOOK_WINDOW])):
                found.extend(part.strip() for part in literal.split(",") if part.strip())
    return found


def _workloads(doc):
    if not isinstance(doc, Mapping):
        return
    kind = text(doc.get("kind"))
    if kind == "List":
        items = doc.get("items")
        for item in items.items if isinstance(items, Sequence) else ():
            yield from _workloads(item)
    elif kind in POD_SPEC:
        yield kind, doc


def _env(container):
    env = {}
    entries = container.get("env")
    for entry in entries.items if isinstance(entries, Sequence) else ():
        name = text(get(entry, "name"))
        if name:
            env[name] = UNKNOWN if get(entry, "valueFrom") is not None else (text(get(entry, "value")) or "")
    return env


def _value(env, name):
    value = env.get(name)
    return value.strip().lower() if isinstance(value, str) else None


def _env_instrumented(container, env):
    if any(name in env for name in ENDPOINT_ENV):
        return True
    if "OTEL_TRACES_EXPORTER" in env and _value(env, "OTEL_TRACES_EXPORTER") != "none":
        return True
    for name in ("JAVA_TOOL_OPTIONS", "JAVA_OPTS"):
        value = _value(env, name) or ""
        if "javaagent" in value and "opentelemetry" in value:
            return True
    for key in ("command", "args"):
        node = container.get(key)
        if isinstance(node, Sequence) and any("opentelemetry-instrument" in (text(item) or "") for item in node.items):
            return True
    return False


def _disabled(env):
    return (_value(env, "OTEL_SDK_DISABLED") == "true" or _value(env, "OTEL_TRACES_EXPORTER") == "none"
            or _value(env, "OTEL_TRACES_SAMPLER") == "always_off")


def _annotated(annotations, containers):
    """Indices of the containers the OpenTelemetry Operator injects auto-instrumentation into."""
    if not isinstance(annotations, Mapping):
        return set()
    names = []
    injected = False
    for key, node in annotations.items.items():
        if not key.startswith(INJECT) or key.endswith("container-names"):
            continue
        value = (text(node) or "").strip().lower()
        if value in ("", "false"):
            continue
        injected = True
        lang = key[len(INJECT):]
        for names_key in (CONTAINER_NAMES, f"instrumentation.opentelemetry.io/{lang}-container-names"):
            listed = text(annotations.get(names_key)) or ""
            names.extend(name.strip() for name in listed.split(",") if name.strip())
    if not injected:
        return set()
    if not names:
        return {0}
    return {index for index, container in enumerate(containers)
            if text(get(container, "name")) in names}


def _sdk_excluded(path, env):
    for name, value in env.items():
        if "EXCLUDED_URLS" not in name:
            continue
        if value is UNKNOWN or "$(" in value:
            return True
        for pattern in (part.strip() for part in value.split(",")):
            if not pattern:
                continue
            try:
                if re.search(pattern, path) or re.search(pattern, f"http://localhost{path}"):
                    return True
            except re.error:
                return True
    return False


def _int(node, default):
    value = text(node)
    return int(value) if value and value.strip().isdigit() and int(value) > 0 else default


def _probes(docs, locator, lines):
    for doc in docs:
        for kind, workload in _workloads(doc):
            path = POD_SPEC[kind]
            spec = get(workload, *path)
            meta = get(workload, "metadata") if kind == "Pod" else get(workload, *path[:-1], "metadata")
            containers = get(spec, "containers")
            if not isinstance(containers, Sequence):
                continue
            containers = containers.items
            name = text(get(workload, "metadata", "name")) or "unnamed"
            replicas = None if kind == "DaemonSet" else (
                _int(get(workload, "spec", "replicas"), 1) if kind in REPLICATED else 1)
            annotated = _annotated(get(meta, "annotations"), containers)
            for index, container in enumerate(containers):
                if not isinstance(container, Mapping):
                    continue
                image = text(container.get("image")) or ""
                if any(token in image for token in COLLECTOR_IMAGES):
                    continue
                env = _env(container)
                if not (index in annotated or _env_instrumented(container, env)) or _disabled(env):
                    continue
                for key in PROBES:
                    probe = _probe(container, key, env, lines)
                    if probe is not None:
                        path_value, period = probe
                        yield Probe(path_value, key[:-len("Probe")], period, replicas, f"{kind}/{name}", locator)


def _probe(container, key, env, lines):
    """(path, period) of an HTTP probe that produces spans, else None."""
    node = container.get(key)
    path_node = get(node, "httpGet", "path")
    path = text(path_node)
    if not path or "$" in path or "{{" in path:
        return None
    path = path.split("?", 1)[0].strip() or "/"
    path = path if path.startswith("/") else f"/{path}"
    hit = TextHit(line=path_node.line, block_line=container.key_lines.get(key), anchor="", summary="",
                  confidence="low")
    if textstatic.is_suppressed(lines, hit, NOQA) or _sdk_excluded(path, env):
        return None
    return path, _int(get(node, "periodSeconds"), DEFAULT_PERIOD)


# -- project-wide evaluation -----------------------------------------------------------------


def _scalars(node):
    if isinstance(node, Scalar):
        return [node.value] if node.value else []
    children = node.items.values() if isinstance(node, Mapping) else node.items if isinstance(node, Sequence) else ()
    return [value for child in children for value in _scalars(child)]


def _matches(token, path, regex):
    if "kube-probe" in token.lower():
        return True
    if path == "/":
        if token == "/":
            return True
        try:
            return regex and re.fullmatch(token, "/") is not None
        except re.error:
            return False
    if path in token:
        return True
    if not regex or len(token) < 3 or not re.search(r"[A-Za-z]", token):
        return False
    try:
        return re.search(token, path) is not None
    except re.error:
        return False


class Project:
    """Probes, filter values, exemptions and code-hook literals of the whole payload."""

    def __init__(self):
        self.has_configs = False
        self.probes = []
        self.filter_values = []
        self.hook_literals = []
        self.exempt = []  # reasons that no pipeline can be judged

    def add(self, scope_id, ctx):
        self.probes.extend(ctx.probes)
        self.hook_literals.extend(ctx.hook_literals)
        for config in ctx.configs:
            self.has_configs = True
            for pipeline in config.pipelines.values():
                if pipeline.signal != "traces":
                    continue
                where = f"{scope_id}: {config.label}pipeline {pipeline.id!r}"
                if pipeline.processors is None:
                    self.exempt.append(f"{where} has an unresolved processors list")
                    continue
                for processor_id in pipeline.processors:
                    kind = component_type(processor_id)
                    if kind == "tail_sampling":
                        self.exempt.append(f"{where} uses {processor_id} (its policies are not judged)")
                    elif kind == "filter":
                        component = config.component("processors", processor_id)
                        values = _scalars(component.node) if component else []
                        if component is None:
                            self.exempt.append(f"{where} uses {processor_id}, which is not defined in the file")
                        elif any(is_unresolved(value) for value in values):
                            self.exempt.append(f"{where} uses {processor_id} with unresolved ${{...}} settings")
                        else:
                            self.filter_values.extend(values)

    def covered(self, path):
        for value in self.filter_values:
            if _matches(value, path, regex=not re.search(r"\s", value.strip())):
                return True
            if any(_matches(literal, path, regex=True) for literal in _literals(value)):
                return True
        return any(_matches(literal, path, regex=True) for literal in self.hook_literals)

    def uncovered(self):
        return [probe for probe in self.probes if not self.covered(probe.path)]


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
        try:
            ctx = parse(locator, content)
        except Exception as error:  # reported per file by the textstatic runner
            parsed[(locator, content)] = error
            continue
        parsed[(locator, content)] = ctx
        project.add(scope_id, ctx)
    return parsed, project


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _describe(probes):
    by_path = {}
    for probe in probes:
        by_path.setdefault(probe.path, []).append(probe)
    parts = []
    for path, items in list(by_path.items())[:4]:
        workloads = []
        for probe in items[:2]:
            workloads.append(f"{probe.workload} in {probe.locator}, {probe.probe} every {probe.period}s")
        more = f", +{len(items) - 2} more" if len(items) > 2 else ""
        parts.append(f"{path} ({'; '.join(workloads)}{more})")
    more = f" and {_plural(len(by_path) - 4, 'more path')}" if len(by_path) > 4 else ""
    return list(by_path), ", ".join(parts) + more


def _recommendation(path):
    return RECOMMENDATION.replace('"/healthz"', f'"{path}"', 1)


def run(ctx, project):
    if not ctx.configs or project.exempt:
        return []
    probes = project.uncovered()
    if not probes:
        return []
    paths, details = _describe(probes)
    per_day = sum((probe.replicas or 1) * 86400 // probe.period for probe in probes)
    per_node = " (DaemonSets counted once)" if any(probe.replicas is None for probe in probes) else ""
    hits = []
    for config in ctx.configs:
        connectors = config.sections.get("connectors", {})
        for pipeline in config.pipelines.values():
            if pipeline.signal != "traces" or None in (pipeline.receivers, pipeline.processors, pipeline.exporters):
                continue
            receivers = [r for r in pipeline.receivers if r not in connectors and component_type(r) in SPAN_RECEIVERS]
            exporters = [e for e in pipeline.exporters
                         if e not in connectors and component_type(e) not in LOCAL_EXPORTERS]
            if not receivers or not exporters:
                continue
            sampled = any(component_type(p) == "probabilistic_sampler" for p in pipeline.processors)
            sampler = (" Its probabilistic_sampler lowers the volume but keeps probe spans in the same proportion."
                       if sampled else "")
            hits.append(TextHit(
                line=pipeline.line,
                end_line=pipeline.end_line,
                anchor=f"{config.label}pipeline/{pipeline.id}:probe-spans",
                summary=(
                    f"Pipeline {pipeline.id!r} ingests spans from {', '.join(receivers)} and exports them to "
                    f"{', '.join(exporters)}, but no filter or tail_sampling processor drops Kubernetes probe "
                    f"requests. OpenTelemetry-instrumented workloads are probed over HTTP at {details}. Each probe "
                    f"becomes a server span: about {per_day:,} spans per day at the declared replica "
                    f"counts{per_node}.{sampler}"
                ),
                confidence="low" if sampled else "medium",
                recommendation=_recommendation(paths[0]),
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
        if not project.has_configs:
            raise NotEvaluated(
                "no OpenTelemetry Collector config in the payload, so probe traffic cannot be judged against a "
                "traces pipeline"
            )
        return ctx

    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES, FORMATS=FORMATS,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, parse=bound_parse,
        run=lambda ctx: module.run(ctx, project),
    )
    result = textstatic.evaluate_text(payload, check)
    notes = []
    if project.exempt:
        notes.append(f"{project.exempt[0]}; probe spans may be dropped there, so no pipeline was flagged"
                     + (f" ({_plural(len(project.exempt) - 1, 'more such pipeline')})" if len(project.exempt) > 1
                        else ""))
    elif project.has_configs and not project.probes:
        notes.append("no OpenTelemetry-instrumented workload with an HTTP liveness/readiness probe in the payload; "
                     "collector pipelines were read but there was no probe traffic to judge")
    for note in notes:
        result["coverage"]["limitations"].insert(-1, note)
    return result
