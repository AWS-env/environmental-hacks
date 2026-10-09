"""OpenTelemetry Collector configuration reader (shared by the Owner D OBS-* config checks).

Builds a small, line-numbered view of a collector config parsed by `miniyaml`: the component
sections (receivers, processors, exporters, connectors, extensions) keyed by component ID
(`type[/name]`) and `service.pipelines` with their receiver/processor/exporter ID lists.
Configs are found as plain collector files, as `OpenTelemetryCollector` custom resources
(`spec.config` as a mapping or a `|` block string) and as `ConfigMap` data entries written as
`|` block strings; embedded configs keep file line numbers.

Nothing is resolved: `${env:VAR}`, `${VAR}` and `${file:...}` references stay as text and
`is_unresolved` reports them, so callers can treat such values as unknown. A list that is
not a plain sequence of IDs (e.g. a `${env:...}` string) is reported as None (unknown).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import miniyaml
from .miniyaml import Mapping, Scalar, Sequence

SECTIONS = ("receivers", "processors", "exporters", "connectors", "extensions")
LIST_KEYS = ("receivers", "processors", "exporters")

_UNRESOLVED = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")
_EXPORTERS_KEY = re.compile(r"^\s*exporters\s*:", re.M)
_PIPELINES_KEY = re.compile(r"^\s*pipelines\s*:", re.M)
_BLOCK_HEADER = re.compile(r":\s*[|][-+0-9]*\s*(#.*)?$")


@dataclass(frozen=True)
class Component:
    id: str  # e.g. "otlp/backend"
    type: str  # e.g. "otlp"
    line: int  # line of the component key
    node: object  # settings Mapping, or a Scalar (often None-valued for `batch:`)


@dataclass(frozen=True)
class Pipeline:
    id: str  # e.g. "traces/2"
    signal: str  # traces | metrics | logs | profiles | ...
    line: int  # line of the pipeline key
    end_line: int  # last line of the pipeline block
    receivers: tuple | None  # None: present but not a plain list of IDs
    processors: tuple | None
    exporters: tuple | None


@dataclass
class CollectorConfig:
    label: str  # "" for a plain file, "OpenTelemetryCollector/<name>:" / "ConfigMap/<name>:<key>:" otherwise
    sections: dict = field(default_factory=dict)  # section -> {component id: Component}
    pipelines: dict = field(default_factory=dict)  # pipeline id -> Pipeline

    def component(self, section, component_id):
        return self.sections.get(section, {}).get(component_id)


def component_type(component_id):
    """`otlp/backend` -> `otlp`."""
    return component_id.split("/", 1)[0]


def is_unresolved(value):
    """True when a string still contains a `${...}`/`$VAR` reference the collector resolves at start-up."""
    return isinstance(value, str) and _UNRESOLVED.search(value) is not None


def text(node):
    """Scalar value of a node (None for missing nodes, nulls and collections)."""
    return node.value if isinstance(node, Scalar) else None


def get(node, *path):
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.items.get(key)
    return node


def end_line(node):
    """Last line that belongs to a parsed node (its deepest last child)."""
    last = node.line
    if isinstance(node, Mapping):
        for key, value in node.items.items():
            last = max(last, node.key_lines.get(key, last), end_line(value))
    elif isinstance(node, Sequence):
        for item in node.items:
            last = max(last, end_line(item))
    return last


def might_contain_config(content):
    """Cheap pre-filter: a collector config has `exporters:` and `pipelines:` keys somewhere."""
    return _EXPORTERS_KEY.search(content) is not None and _PIPELINES_KEY.search(content) is not None


def _id_list(node):
    if node is None:
        return ()
    if isinstance(node, Sequence) and all(isinstance(item, Scalar) and item.value for item in node.items):
        values = tuple(item.value for item in node.items)
        return None if any(is_unresolved(value) for value in values) else values
    return None


def from_mapping(node, label=""):
    """CollectorConfig for a mapping with `service.pipelines`, else None."""
    pipelines = get(node, "service", "pipelines")
    if not isinstance(pipelines, Mapping):
        return None
    config = CollectorConfig(label)
    for section in SECTIONS:
        components = {}
        section_node = node.items.get(section)
        if isinstance(section_node, Mapping):
            for component_id, settings in section_node.items.items():
                components[component_id] = Component(
                    component_id, component_type(component_id), section_node.key_lines.get(component_id, settings.line),
                    settings,
                )
        config.sections[section] = components
    for pipeline_id, body in pipelines.items.items():
        line = pipelines.key_lines.get(pipeline_id, body.line)
        lists = {key: _id_list(get(body, key)) if isinstance(body, Mapping) else None for key in LIST_KEYS}
        config.pipelines[pipeline_id] = Pipeline(
            pipeline_id, component_type(pipeline_id), line, max(line, end_line(body)), **lists
        )
    return config


def _shift(node, offset, seen=None):
    """Move every line of a freshly parsed tree by `offset` (aliased nodes only once)."""
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


def _embedded(scalar, lines, label):
    """Parse a `|` block string that holds a collector config, keeping file line numbers.

    Returns None when the string is not a block scalar (its lines cannot be mapped back) or
    holds no config. Raises miniyaml.YamlError when the embedded text cannot be parsed.
    """
    if not isinstance(scalar, Scalar) or not scalar.value or not might_contain_config(scalar.value):
        return None
    header = lines[scalar.line - 1] if 0 < scalar.line <= len(lines) else ""
    if not _BLOCK_HEADER.search(header):
        return None
    try:
        docs = miniyaml.load_all(scalar.value)
    except miniyaml.YamlError as error:
        raise miniyaml.YamlError(f"embedded config {label.rstrip(':')} at line {scalar.line}: {error}") from None
    found = []
    for doc in docs:
        _shift(doc, scalar.line)
        config = from_mapping(doc, label)
        if config is not None:
            found.append(config)
    return found


def find_configs(docs, lines):
    """Collector configs in parsed YAML documents (`lines` are the file lines, for block strings)."""
    configs = []
    for doc in docs:
        if not isinstance(doc, Mapping):
            continue
        kind, name = text(doc.get("kind")), text(get(doc, "metadata", "name")) or "unnamed"
        if kind == "OpenTelemetryCollector":
            label = f"OpenTelemetryCollector/{name}:"
            spec_config = get(doc, "spec", "config")
            if isinstance(spec_config, Mapping):
                config = from_mapping(spec_config, label)
                configs.extend([config] if config else [])
            else:
                configs.extend(_embedded(spec_config, lines, label) or [])
        elif kind == "ConfigMap":
            data = get(doc, "data")
            for key, value in data.items.items() if isinstance(data, Mapping) else ():
                configs.extend(_embedded(value, lines, f"ConfigMap/{name}:{key}:") or [])
        else:
            config = from_mapping(doc)
            configs.extend([config] if config else [])
    return configs
