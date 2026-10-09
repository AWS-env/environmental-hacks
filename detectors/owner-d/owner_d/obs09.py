"""OBS-09: uncompressed/unbatched telemetry export (static exporter config scan).

Detector semantics version 1.0.0. Reads OpenTelemetry Collector configs (plain files,
`OpenTelemetryCollector` resources, ConfigMaps; ADOT uses the same format) and Python
OpenTelemetry SDK setup as text, and flags OTLP exports that leave as many small or
uncompressed requests:

- a pipeline that exports to an OTLP exporter with no `batch` processor and no exporter-side
  batching (`sending_queue.batch`, legacy `batcher`);
- an OTLP exporter with `compression: none` (the OTLP exporters default to gzip);
- Python `SimpleSpanProcessor`/`SimpleLogRecordProcessor` wrapping a network exporter, which
  exports each span/log record as its own request.

Nothing is executed, rendered or resolved; request sizes are not observed, so no
measurements are emitted.
"""

from __future__ import annotations

import ast
import re
import sys

from . import miniyaml, otelconfig, static, textstatic
from .miniyaml import Mapping
from .otelconfig import get, is_unresolved, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401
from .tst12 import is_test_path

CHECK_ID = "OBS-09"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-09", "OBS09")
FORMATS = (
    "OpenTelemetry Collector configs (.yaml/.yml: plain, OpenTelemetryCollector resources, ConfigMaps) and "
    "Python OpenTelemetry SDK setup (.py)"
)

REFERENCES = (
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/processor/batchprocessor/README.md",
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/processor/README.md#recommended-processors",
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/exporterhelper/README.md",
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/otlpexporter/README.md",
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/otlphttpexporter/README.md",
    "https://github.com/open-telemetry/opentelemetry-collector/blob/main/config/configgrpc/README.md#compression-comparison",
    "https://opentelemetry.io/docs/languages/python/exporters/#batching-span-and-log-records",
)
REC_BATCH = (
    "Add the `batch` processor to the pipeline (after memory_limiter and any sampling/filtering processors), or "
    "enable exporter-side batching with `sending_queue: {batch: {}}`, so data leaves in fewer, larger, "
    "better-compressed requests."
)
REC_COMPRESSION = (
    "Remove `compression: none` to use the default gzip (or choose zstd/snappy). Disable compression only for a "
    "local hop or a CPU-bound collector on a fast link."
)
REC_SDK = (
    "Use BatchSpanProcessor / BatchLogRecordProcessor instead (call force_flush() before a short-lived process or "
    "Lambda invocation ends) so spans and log records are exported in batches."
)
RECOMMENDATION = REC_BATCH
LIMITATION = (
    "Static config scan only: OBS-09 sees one file at a time, not merged --config files, collector feature gates "
    "(pkg.exporterhelper.queueBatchEnabled turns exporter batching on by default) or the collector version, and it "
    "does not observe request sizes, so no measurements are emitted. Only OTLP exporters are checked; exporters "
    "with loopback endpoints, pipelines fed by connectors, profiles pipelines, exporters defined in other files, "
    "unresolved ${...} settings, Helm chart values and development/test files are not judged."
)

OTLP_EXPORTERS = {"otlp", "otlp_grpc", "otlphttp", "otlp_http"}
# Signals the batch processor supports (profiles pipelines cannot add it, so they are not judged).
BATCH_SIGNALS = {"traces", "metrics", "logs"}
ENDPOINT_KEYS =("endpoint", "traces_endpoint", "metrics_endpoint", "logs_endpoint", "profiles_endpoint")
SIMPLE_PROCESSORS = {"SimpleSpanProcessor": "span", "SimpleLogRecordProcessor": "log record"}
EXPORTER_PACKAGES = ("opentelemetry.exporter.", "azure.monitor.opentelemetry.exporter.")
# File/directory name tokens that mark development, debug or test configs.
DEV_NAMES = {"dev", "development", "debug", "local", "test", "tests", "testing", "testdata", "e2e", "ci",
             "devcontainer"}

_LOOPBACK = re.compile(r"^(localhost|127(\.\d{1,3}){3}|::1|0\.0\.0\.0)$", re.I)
_ZERO_DURATION = re.compile(r"^0+(\.0*)?(ns|us|µs|ms|s|m|h)?$")


class YamlCtx:
    def __init__(self, content, configs):
        self.lines = content.splitlines()
        self.configs = configs


def _dev_path(locator):
    parts = [p.lower() for p in re.split(r"[\\/]", locator)]
    tokens = set(re.split(r"[._-]", parts[-1])) | {p.lstrip(".") for p in parts[:-1]}
    marked = sorted(tokens & DEV_NAMES)
    return marked[0] if marked else None


def parse(locator, content):
    lower = locator.lower()
    if lower.endswith(".py"):
        if "opentelemetry" not in content:
            raise Unsupported(locator)
        if is_test_path(locator) or _dev_path(locator):
            raise NotEvaluated("development/test module; OBS-09 v1 evaluates production telemetry setup only")
        try:
            return static.Ctx(locator, content)
        except (SyntaxError, ValueError):
            raise ParseError("invalid Python") from None
    if not lower.endswith((".yaml", ".yml")) or not otelconfig.might_contain_config(content):
        raise Unsupported(locator)
    marked = _dev_path(locator)
    if marked:
        raise NotEvaluated(
            f"path marks a development/test config ({marked}); OBS-09 v1 evaluates deployed configs only"
        )
    try:
        configs = otelconfig.find_configs(miniyaml.load_all(content), content.splitlines())
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    if not configs:
        raise NotEvaluated(
            "no OpenTelemetry Collector config with service.pipelines (plain, OpenTelemetryCollector or ConfigMap); "
            "Helm chart values are merged with chart defaults and are not evaluated"
        )
    return YamlCtx(content, configs)


# -- collector configs -----------------------------------------------------------------------


def _host(endpoint):
    value = endpoint.strip()
    if value.lower().startswith(("unix:", "unix-abstract:")):
        return "localhost"
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value, flags=re.I).lstrip("/")
    value = value.split("/", 1)[0]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _endpoint_kind(settings):
    """'loopback', 'remote' or 'unknown' (unresolved or missing endpoint)."""
    values = [text(get(settings, key)) for key in ENDPOINT_KEYS if get(settings, key) is not None]
    if not values or any(not value or is_unresolved(value) for value in values):
        return "unknown"
    return "loopback" if all(_LOOPBACK.match(_host(value)) for value in values) else "remote"


def _exporter_batches(settings):
    """True when the exporter batches by itself (or its queue settings are unresolved)."""
    queue = get(settings, "sending_queue")
    if isinstance(queue, Mapping) and "batch" in queue.items:
        return True
    batcher = get(settings, "batcher")
    if isinstance(batcher, Mapping) and (text(batcher.get("enabled")) or "").lower() != "false":
        return True
    return any(is_unresolved(text(node)) for node in (queue, batcher))


def _batch_processors(config, pipeline):
    """(effective batch processor IDs, IDs configured with timeout 0)."""
    effective, immediate = [], []
    for processor_id in pipeline.processors:
        if otelconfig.component_type(processor_id) != "batch":
            continue
        component = config.component("processors", processor_id)
        timeout = text(get(component.node, "timeout")) if component else None
        if timeout is not None and _ZERO_DURATION.match(timeout.strip()):
            immediate.append(processor_id)
        else:
            effective.append(processor_id)
    return effective, immediate


def _otlp_exporter(config, exporter_id):
    if otelconfig.component_type(exporter_id) not in OTLP_EXPORTERS:
        return None
    return config.component("exporters", exporter_id)


def _confidence(kinds):
    return "medium" if all(kind == "remote" for kind in kinds) else "low"


def _unbatched(config):
    connectors = config.sections.get("connectors", {})
    for pipeline in config.pipelines.values():
        if pipeline.signal not in BATCH_SIGNALS:
            continue
        if None in (pipeline.receivers, pipeline.processors, pipeline.exporters):
            continue
        if any(receiver in connectors for receiver in pipeline.receivers):
            continue
        effective, immediate = _batch_processors(config, pipeline)
        if effective:
            continue
        unbatched = []
        for exporter_id in pipeline.exporters:
            component = _otlp_exporter(config, exporter_id)
            if component is None or _exporter_batches(component.node):
                continue
            kind = _endpoint_kind(component.node)
            if kind != "loopback":
                unbatched.append((exporter_id, kind))
        if not unbatched:
            continue
        names = ", ".join(exporter_id for exporter_id, _ in unbatched)
        if immediate:
            why = f"its batch processor ({', '.join(immediate)}) has timeout 0, which sends data immediately,"
        else:
            why = "it has no batch processor"
        exporters = "the exporter has" if len(unbatched) == 1 else "the exporters have"
        kinds = [kind for _, kind in unbatched]
        unknown = "" if "unknown" not in kinds else " The endpoint is unresolved, so the hop may be local."
        yield TextHit(
            line=pipeline.line,
            end_line=pipeline.end_line,
            anchor=f"{config.label}pipeline/{pipeline.id}:unbatched",
            summary=(
                f"Pipeline {pipeline.id!r} exports to {names} unbatched: {why} and {exporters} no exporter-side "
                f"batching (sending_queue.batch), so each incoming request is sent on as its own export.{unknown}"
            ),
            confidence=_confidence(kinds),
            recommendation=REC_BATCH,
        )


def _uncompressed(config):
    used = {exporter_id for pipeline in config.pipelines.values() for exporter_id in pipeline.exporters or ()}
    for exporter_id, component in config.sections.get("exporters", {}).items():
        if exporter_id not in used or _otlp_exporter(config, exporter_id) is None:
            continue
        if not isinstance(component.node, Mapping):
            continue
        value = text(component.node.get("compression"))
        if value is None or value.strip().lower() not in ("none", ""):
            continue
        kind = _endpoint_kind(component.node)
        if kind == "loopback":
            continue
        target = "a non-loopback endpoint." if kind == "remote" else "an unresolved endpoint (the hop may be local)."
        yield TextHit(
            line=component.node.key_lines["compression"],
            block_line=component.line,
            anchor=f"{config.label}exporter/{exporter_id}:compression-none",
            summary=(
                f"Exporter {exporter_id!r} disables compression (the OTLP exporters default to gzip), so "
                f"telemetry is sent uncompressed to {target}"
            ),
            confidence=_confidence([kind]),
            recommendation=REC_COMPRESSION,
        )


# -- Python SDK ------------------------------------------------------------------------------


def _network_exporter(ctx, node, assigned):
    """Class name of a network span/log exporter built by `node`, else None."""
    if isinstance(node, ast.Name):
        values = assigned.get(node.id, [])
        names = {_network_exporter(ctx, value, {}) for value in values}
        return names.pop() if values and len(names) == 1 else None
    if not isinstance(node, ast.Call):
        return None
    dotted = ctx.dotted(node.func) or ""
    name = dotted.rsplit(".", 1)[-1]
    if (dotted.startswith(EXPORTER_PACKAGES) and name.endswith("Exporter")
            and "Console" not in name and "InMemory" not in name):
        return name
    return None


def _assignments(tree):
    """{name: [assigned value nodes]} for the whole file. Names bound any other way (parameters,
    loops, unpacking, augmented assignment, ...) get a non-call placeholder, so they stay unknown."""
    assigned, simple = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) for target in node.targets):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name) and node.value:
            targets = [node.target]
        else:
            continue
        for target in targets:
            simple.add(id(target))
            assigned.setdefault(target.id, []).append(node.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and id(node) not in simple:
            assigned.setdefault(node.id, []).append(node)
        elif isinstance(node, ast.arg):
            assigned.setdefault(node.arg, []).append(node)
    return assigned


def _simple_processors(ctx):
    assigned = _assignments(ctx.tree)
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = ctx.dotted(node.func) or ""
        processor = dotted.rsplit(".", 1)[-1]
        if not dotted.startswith("opentelemetry.") or processor not in SIMPLE_PROCESSORS:
            continue
        argument = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg in ("span_exporter", "exporter")), None)
        exporter = _network_exporter(ctx, argument, assigned)
        if exporter is None:
            continue
        item = SIMPLE_PROCESSORS[processor]
        yield TextHit(
            line=node.lineno,
            end_line=node.end_lineno,
            anchor=f"{ctx.qualname(node)}:{processor}({exporter})",
            summary=(
                f"{processor} sends every {item} to {exporter} as its own export request as soon as it ends, "
                f"instead of batching them."
            ),
            confidence="medium",
            recommendation=REC_SDK,
        )


def run(ctx):
    if isinstance(ctx, YamlCtx):
        hits = []
        for config in ctx.configs:
            hits.extend(_unbatched(config))
            hits.extend(_uncompressed(config))
        return hits
    return list(_simple_processors(ctx))


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
