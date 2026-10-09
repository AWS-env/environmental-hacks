"""OBS-05: tracing without sampling (static proxy: tracing configured to keep 100% of traces).

Detector semantics version 1.0.0. Static only: YAML and JSON are read with `miniyaml` (line
numbers kept), other configuration formats with the OBS-01 line scanners, and Python with
`ast`. Nothing is imported, executed or rendered. Trace volume (the telemetry half of the
check), tail sampling and OpenTelemetry Collector configuration are not part of v1.

A finding needs (1) a sampling setting that keeps every trace, (2) a literal value and (3) a
file or key path that is not marked as non-production (the OBS-01 environment rules).
"""

from __future__ import annotations

import ast
import configparser
import functools
import json
import math
import re
import sys
import tomllib
import types
from dataclasses import dataclass

from . import miniyaml, static
from .miniyaml import Mapping, Scalar, Sequence
from .obs01 import (
    CONFIG_CONFIDENCE,
    PYTHON_CONFIDENCE,
    SUPPORTED_FORMATS,
    _clean,
    _conditional,
    _dockerfile_entries,
    _flat_entries,
    environment,
    file_format,
)
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "OBS-05"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-05", "OBS05")

REFERENCES = (
    "https://opentelemetry.io/docs/concepts/sampling/",
    "https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/",
    "https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.sampling.html",
    "https://docs.aws.amazon.com/xray/latest/devguide/xray-console-sampling.html",
    "https://docs.spring.io/spring-boot/reference/actuator/tracing.html",
    "https://openobserve.ai/blog/observability-cost-optimization-tactics/",
)
RECOMMENDATION = (
    "Sample traces in production: use parentbased_traceidratio with a ratio below 1 (for example 0.05-0.1), a "
    "Spring sampling probability below 1, or an X-Ray rule with a small reservoir and a FixedRate below 1. Keep "
    "errors and slow requests with tail sampling in an OpenTelemetry Collector, and reserve 100% sampling for "
    "short debugging windows or narrowly scoped rules."
)
LIMITATION = (
    "Static configuration does not prove actual trace volume or environmental impact: OBS-05 v1 reads literal "
    "sampler settings only (OpenTelemetry env/config, Spring sampling probability, Python SDK samplers, X-Ray "
    "sampling rules) and emits no measurements. Runtime overrides, env_file/envFrom sources and remote sampling "
    "configuration are not followed, and a missing sampler (the SDK default parentbased_always_on) is not "
    "flagged. Head-only sampling (no tail sampling) is not judged. OpenTelemetry Collector probabilistic_sampler "
    "config: not evaluated in v1; to be added on top of the shared otelconfig.py after the OBS-09 PR (#233) "
    "lands. The environment is inferred from path, key and Docker stage names, so findings in files without a "
    "production marker are low confidence."
)

TIERS = ("low", "medium", "high")
SAMPLER = ("otel", "traces", "sampler")
SAMPLER_ARG = SAMPLER + ("arg",)
SDK_DISABLED = ("otel", "sdk", "disabled")
TRACES_EXPORTER = ("otel", "traces", "exporter")
PROBABILITIES = (("management", "tracing", "sampling", "probability"), ("spring", "sleuth", "sampler", "probability"))
KEYS = (("sampler", SAMPLER), ("arg", SAMPLER_ARG), ("disabled", SDK_DISABLED), ("exporter", TRACES_EXPORTER)) + tuple(
    ("probability", suffix) for suffix in PROBABILITIES
)
# OTEL_TRACES_SAMPLER values that keep every (root) trace: value -> confidence tiers below the base.
ALWAYS_ON = {"always_on": 0, "parentbased_always_on": 1}
RATIO = {"traceidratio": 0, "parentbased_traceidratio": 1}
WHERE = {
    "prod": "Production configuration",
    "template": "Production configuration template",
    "declared": "Configuration in a scan declared as production",
    "unknown": "Configuration with no environment marker (production use not established)",
}


def _lower(confidence, steps):
    return TIERS[max(0, TIERS.index(confidence) - steps)]


def _tokens(segment):
    return [token for token in re.split(r"[._\-\s]+", str(segment).lower()) if token]


def match_key(keys, suffix):
    """Number of trailing key segments that spell `suffix`, aligned on a segment boundary, or 0.

    `OTEL_TRACES_SAMPLER`, `otel.traces.sampler` (split into three segments),
    `quarkus.otel.traces.sampler` and its env form `QUARKUS_OTEL_TRACES_SAMPLER` match SAMPLER;
    `MY_OTEL_TRACES_SAMPLER` does not.
    """
    tokens = []
    for count, segment in enumerate(reversed(keys), 1):
        tokens = _tokens(segment) + tokens
        if len(tokens) >= len(suffix):
            framework = tokens[:-len(suffix)] == ["quarkus"] and count == 1
            return count if tuple(tokens[-len(suffix):]) == suffix and (len(tokens) == len(suffix) or framework) else 0
    return 0


def literal(raw):
    """The literal value of a setting (`${VAR:-x}` gives x), or None for runtime-provided values."""
    if raw is None:
        return None
    value = _clean(str(raw))
    if not value or "$" in value or "{{" in value:
        return None
    return value


def ratio(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def keeps_all(value):
    """True for a literal ratio of exactly 1 (`1`, `1.0`, `"1"`). Ratios above 1 are invalid."""
    return ratio(value) == 1.0


# --- configuration files ---------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    keys: tuple
    value: object
    first: int
    last: int
    block: tuple  # settings in the same block are siblings (one env list, mapping or section)


@dataclass(frozen=True)
class Span:
    lineno: int
    end_lineno: int


_ASSIGNMENT = re.compile(r"^([A-Za-z_][\w.\-]*)=(.*)$")


def _tree_entries(node, keys, loc):
    if isinstance(node, Mapping):
        for key, value in node.items.items():
            key = "" if key is None else str(key)
            if isinstance(value, Scalar):
                yield Entry(tuple(keys + [key]), value.value, node.key_lines.get(key, value.line), value.line, loc)
            else:
                yield from _tree_entries(value, keys + [key], loc + (key,))
    elif isinstance(node, Sequence):
        for index, item in enumerate(node.items):
            if isinstance(item, Mapping):
                name, value = item.get("name"), item.get("value")
                if isinstance(name, Scalar) and isinstance(name.value, str) and isinstance(value, Scalar):
                    lines = (item.key_lines.get("name", name.line), name.line, value.line,
                             item.key_lines.get("value", value.line))
                    yield Entry(tuple(keys + [name.value]), value.value, min(lines), max(lines), loc)
                yield from _tree_entries(item, keys, loc + (index,))
            elif isinstance(item, Scalar) and isinstance(item.value, str):
                assignment = _ASSIGNMENT.match(item.value)  # compose `- OTEL_TRACES_SAMPLER=always_on`
                if assignment:
                    yield Entry(tuple(keys + [assignment.group(1)]), assignment.group(2), item.line, item.line, loc)
            elif isinstance(item, Sequence):
                yield from _tree_entries(item, keys, loc + (index,))


def _flat(entries, dockerfile=False):
    for path, raw, first, last in entries:
        # Dockerfile ENV is inherited across stages, so the whole file is one block.
        yield Entry(tuple(str(segment) for segment in path), raw, first, last, ("dockerfile",) if dockerfile else ())


class UnsupportedConstruct(ValueError):
    """A sampler setting uses syntax the line scanner does not interpret."""


_SAMPLER_TEXT = re.compile(r"(?i)otel[._-]traces[._-]sampler|sampl\w*[._-]probability")


class ConfigCtx:
    evidence_lines = static.Ctx.evidence_lines

    def __init__(self, path, source, fmt, declared):
        self.path, self.format, self.declared = path, fmt, declared
        self.lines = source.splitlines()
        self.docs = []
        if fmt in ("yaml", "json"):
            if fmt == "json":
                json.loads(source)  # JSONDecodeError is a ValueError; JSON with comments is not evaluated
            try:
                self.docs = miniyaml.load_all(source)
            except RecursionError as error:
                raise ValueError("nesting too deep") from error
            self.entries = [entry for doc in self.docs for entry in _tree_entries(doc, [], ())]
        elif fmt == "dockerfile":
            self.entries = list(_flat(_dockerfile_entries(self.lines), dockerfile=True))
        else:
            if fmt == "toml":
                tomllib.loads(source)
            elif fmt == "ini":
                try:
                    configparser.ConfigParser(interpolation=None, strict=False).read_string(source)
                except configparser.Error as error:
                    raise ValueError(str(error)) from error
            self.entries = list(_flat(_flat_entries(self.lines, fmt)))
            if fmt == "toml":
                self._check_unscanned()

    def _check_unscanned(self):
        """A sampler key the TOML line scanner could not attribute (inline table/array) is not judged clean."""
        matched = {entry.first for entry in self.entries if any(match_key(entry.keys, suffix) for _, suffix in KEYS)}
        for number, line in enumerate(self.lines, 1):
            text = line.split("#", 1)[0]
            if _SAMPLER_TEXT.search(text) and number not in matched:
                raise UnsupportedConstruct(f"line {number}: sampler setting inside an inline table or array")


def parse(locator, content, declared=False):
    fmt = file_format(locator)
    if fmt is None:
        return None
    if fmt == "python":
        ctx = static.Ctx(locator, content)
        ctx.format, ctx.declared = "python", declared
        return ctx
    return ConfigCtx(locator, content, fmt, declared)


def _span(first, last):
    return Span(first, last) if last - first < static.EVIDENCE_MAX_LINES else Span(last, last)


def _keypath(entry):
    return ".".join(entry.keys)


def _config_env(ctx, *entries):
    envs = [environment(ctx.path, list(entry.keys), ctx.declared) for entry in entries]
    return "nonprod" if "nonprod" in envs else envs[0]


def _sampler_hits(ctx, group):
    hits = []
    values = {kind: [(entry, literal(entry.value)) for entry in group.get(kind, [])]
              for kind in ("sampler", "arg", "disabled", "exporter")}
    if any((value or "").lower() == "true" for _, value in values["disabled"]) or any(
            (value or "").lower() == "none" for _, value in values["exporter"]):
        return hits  # the SDK or trace export is switched off in this block
    args = values["arg"]
    arg_entry, arg_value = args[-1] if args else (None, None)
    for entry, value in values["sampler"]:
        sampler = (value or "").lower()
        key = _keypath(entry)
        if sampler in ALWAYS_ON:
            env, steps, span = _config_env(ctx, entry), ALWAYS_ON[sampler], _span(entry.first, entry.last)
            what = "every root trace (child spans follow the caller)" if steps else "every trace"
            summary = f"sets {key}={value}, so {what} is recorded and exported"
        elif sampler in RATIO and arg_entry is None:
            env, steps, span = _config_env(ctx, entry), RATIO[sampler] + 1, _span(entry.first, entry.last)
            summary = (f"sets {key}={value} without OTEL_TRACES_SAMPLER_ARG in the same block; the specified "
                       "default ratio is 1.0, so every trace is kept unless the ratio is set elsewhere")
        elif sampler in RATIO and keeps_all(arg_value):
            env, steps = _config_env(ctx, entry, arg_entry), RATIO[sampler]
            first, last = min(entry.first, arg_entry.first), max(entry.last, arg_entry.last)
            span = Span(first, last) if last - first < static.EVIDENCE_MAX_LINES else _span(arg_entry.first, arg_entry.last)
            summary = f"sets {key}={value} with {_keypath(arg_entry)}={arg_value}: a sampling ratio of 100%"
        else:
            continue
        if env == "nonprod":
            continue
        hits.append(Hit(node=span, anchor=f"trace-sampling:{key}", summary=f"{WHERE[env]} {summary}",
                        confidence=_lower(CONFIG_CONFIDENCE[env], steps)))
    if not values["sampler"]:
        for entry, _ in args:
            env = _config_env(ctx, entry)
            if env == "nonprod":
                continue
            hits.append(Hit(
                node=_span(entry.first, entry.last),
                anchor=f"ignored-sampler-arg:{_keypath(entry)}",
                summary=(f"{WHERE[env]} sets {_keypath(entry)} without OTEL_TRACES_SAMPLER in the same block: the "
                         "ratio is ignored and the SDK default parentbased_always_on samples every root trace, "
                         "unless the sampler is set elsewhere"),
                confidence="low",
            ))
    return hits


def _probability_hits(ctx, entries):
    hits = []
    for entry in entries:
        value = literal(entry.value)
        env = _config_env(ctx, entry)
        if not keeps_all(value) or env == "nonprod":
            continue
        key = _keypath(entry)
        hits.append(Hit(
            node=_span(entry.first, entry.last),
            anchor=f"trace-sampling:{key}",
            summary=f"{WHERE[env]} sets {key}={value}: every trace is sampled",
            confidence=CONFIG_CONFIDENCE[env],
        ))
    return hits


# AWS X-Ray sampling rules: API/CloudFormation shape (FixedRate) and SDK local rules (rate).
XRAY_SCOPE = ("ServiceName", "ServiceType", "Host", "HTTPMethod", "URLPath", "ResourceARN")
LOCAL_SCOPE = ("service_name", "host", "http_method", "url_path")


def _scalar(node, key):
    value = node.items.get(key) if isinstance(node, Mapping) else None
    return value.value if isinstance(value, Scalar) else None


def _narrowed(node, keys):
    if any(_scalar(node, key) not in (None, "*") for key in keys):
        return True
    attributes = node.items.get("Attributes")
    return isinstance(attributes, Mapping) and bool(attributes.items)


def _mappings(node, keys):
    if isinstance(node, Mapping):
        yield node, keys
        for key, value in node.items.items():
            yield from _mappings(value, keys + [str(key)])
    elif isinstance(node, Sequence):
        for item in node.items:
            yield from _mappings(item, keys)


def _xray_hit(ctx, node, rate_key, env_keys, name, narrowed):
    rate = node.items[rate_key]
    if not isinstance(rate, Scalar) or not keeps_all(literal(rate.value)):
        return None
    env = environment(ctx.path, env_keys, ctx.declared)
    if env == "nonprod":
        return None
    scope = "a rule narrowed to specific requests" if narrowed else "a catch-all rule"
    return Hit(
        node=_span(node.key_lines.get(rate_key, rate.line), rate.line),
        anchor=f"xray-sampling-rule:{name}",
        summary=(f"{WHERE[env]} defines X-Ray sampling rule {name} with {rate_key}={rate.value} ({scope}): every "
                 "matching request beyond the reservoir is traced"),
        confidence=_lower(CONFIG_CONFIDENCE[env], 1 if narrowed else 0),
    )


def _xray_hits(ctx):
    hits = []
    for doc in ctx.docs:
        default = doc.items.get("default") if isinstance(doc, Mapping) else None
        if isinstance(default, Mapping) and "rate" in default.items and "fixed_target" in default.items:
            hits.append(_xray_hit(ctx, default, "rate", [], "default", False))
            rules = doc.items.get("rules")
            for rule in rules.items if isinstance(rules, Sequence) else []:
                if isinstance(rule, Mapping) and "rate" in rule.items:
                    name = ":".join(_scalar(rule, key) or "*" for key in LOCAL_SCOPE)
                    hits.append(_xray_hit(ctx, rule, "rate", [], name, _narrowed(rule, LOCAL_SCOPE)))
        for node, keys in _mappings(doc, []):
            if "FixedRate" not in node.items:
                continue
            name = _scalar(node, "RuleName")
            if not name:
                name = keys[1] if len(keys) > 1 and keys[0] == "Resources" else ".".join(keys) or "rule"
            hits.append(_xray_hit(ctx, node, "FixedRate", keys + [name], name, _narrowed(node, XRAY_SCOPE)))
    return [hit for hit in hits if hit is not None]


def _config_hits(ctx):
    groups, probabilities = {}, []
    for entry in ctx.entries:
        for kind, suffix in KEYS:
            count = match_key(entry.keys, suffix)
            if not count:
                continue
            if kind == "probability":
                probabilities.append(entry)
            else:
                prefix = () if entry.block == ("dockerfile",) else entry.keys[:-count]
                group = groups.setdefault((entry.block, prefix), {})
                group.setdefault(kind, []).append(entry)
            break
    hits = [hit for group in groups.values() for hit in _sampler_hits(ctx, group)]
    hits += _probability_hits(ctx, probabilities)
    return hits + _xray_hits(ctx)


# --- Python ----------------------------------------------------------------------------------

def _otel(dotted):
    return bool(dotted) and dotted.startswith("opentelemetry.")


def _is_one(node):
    return (isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value == 1)


def _argument(call, index, name):
    keyword = next((kw.value for kw in call.keywords if kw.arg == name), None)
    if keyword is not None:
        return keyword
    return call.args[index] if len(call.args) > index else None


def _scope(ctx, node):
    return next((a for a in ctx.ancestors(node) if isinstance(a, static.FUNC_NODES)), ctx.tree)


def _bindings(scope, name):
    found = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, static.SCOPE_NODES):
                if getattr(child, "name", None) == name:
                    found.append(child)
                continue
            if isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, (ast.Store, ast.Del)):
                found.append(child)
            elif isinstance(child, ast.arg) and child.arg == name:
                found.append(child)
            elif isinstance(child, (ast.Import, ast.ImportFrom)) and any(
                    (alias.asname or alias.name.split(".")[0]) == name for alias in child.names):
                found.append(child)
            elif isinstance(child, (ast.Global, ast.Nonlocal)) and name in child.names:
                found.append(child)
            visit(child)

    if isinstance(scope, ast.Lambda):
        visit(scope.args)
    else:
        visit(scope)
    return found


def _resolve(ctx, node):
    """(value, assignment) for a variable assigned exactly once, unconditionally, in its scope."""
    if not isinstance(node, ast.Name) or node.id in ctx.aliases:
        return node, None
    scope = _scope(ctx, node)
    while True:
        found = _bindings(scope, node.id)
        if found or scope is ctx.tree:
            break
        scope = ctx.tree
    if len(found) != 1:
        return None, None
    parent = ctx.parent(found[0])
    if not (isinstance(parent, ast.Assign) and parent.targets == [found[0]]) or _conditional(ctx, parent):
        return None, None
    return parent.value, parent


def sampler_steps(ctx, node, depth=0):
    """Confidence tiers below the base if `node` keeps every (root) trace, else None."""
    node, _ = _resolve(ctx, node) if depth else (node, None)
    if node is None or depth > 4:
        return None
    if isinstance(node, (ast.Name, ast.Attribute)):
        dotted = ctx.dotted(node)
        if not _otel(dotted):
            return None
        return {"ALWAYS_ON": 0, "DEFAULT_ON": 1}.get(dotted.rsplit(".", 1)[-1])
    if not isinstance(node, ast.Call):
        return None
    dotted = ctx.dotted(node.func)
    if not _otel(dotted):
        return None
    name = dotted.rsplit(".", 1)[-1]
    if name == "TraceIdRatioBased":
        return 0 if _is_one(_argument(node, 0, "rate")) else None
    if name == "ParentBasedTraceIdRatio":
        return 1 if _is_one(_argument(node, 0, "rate")) else None
    if name == "ParentBased":
        root = _argument(node, 0, "root")
        return None if root is None or sampler_steps(ctx, root, depth + 1) is None else 1
    if name == "StaticSampler":
        decision = ctx.dotted(_argument(node, 0, "decision")) if _argument(node, 0, "decision") else None
        return 0 if decision and decision.endswith("Decision.RECORD_AND_SAMPLE") else None
    return None


def _env_key(ctx, node):
    """`os.environ["X"] = v` / `os.environ.setdefault("X", v)`: (key, value node), else None."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
        target = node.targets[0]
        if ctx.dotted(target.value) == "os.environ" and isinstance(target.slice, ast.Constant):
            return target.slice.value, node.value
    if isinstance(node, ast.Call) and ctx.dotted(node.func) == "os.environ.setdefault" and len(node.args) == 2:
        if isinstance(node.args[0], ast.Constant):
            return node.args[0].value, node.args[1]
    return None


def _branch_state(ctx, site, alternatives):
    """None outside conditions; "choice" when the condition picks between settings; else "guard".

    A ternary, or an `if`/`else` whose other branch configures the same thing (another
    TracerProvider, X-Ray configure or OTEL_TRACES_SAMPLER), chooses the sampler at runtime. An
    `if` with no alternative only decides whether tracing runs (`if endpoint:`), so it is a guard.
    """
    state, child = None, site
    for ancestor in ctx.ancestors(site):
        if isinstance(ancestor, ast.IfExp):
            return "choice"
        if isinstance(ancestor, ast.If) and not _is_main_guard(ancestor.test):
            other = ancestor.orelse if child in ancestor.body else ancestor.body if child in ancestor.orelse else []
            inside = {id(node) for statement in other for node in ast.walk(statement)}
            if any(id(node) in inside for node in alternatives):
                return "choice"
            state = "guard"
        child = ancestor
    return state


def _is_main_guard(test):
    text = ast.unparse(test)
    return isinstance(test, ast.Compare) and "__name__" in text and "__main__" in text


def _python_hits(ctx):
    found = []  # (call/statement node, evidence node, anchor, summary, steps)
    candidates = {}  # anchor -> every node configuring it, flagged or not
    for node in ast.walk(ctx.tree):
        env_setting = _env_key(ctx, node)
        if env_setting:
            key, value = env_setting
            if isinstance(key, str) and match_key([key], SAMPLER) == 1:
                anchor = f"os.environ:{key}"
                candidates.setdefault(anchor, []).append(node)
                steps = ALWAYS_ON.get(str(value.value).lower()) if isinstance(value, ast.Constant) else None
                if steps is not None:
                    found.append((node, node, anchor, f"Python code sets os.environ {key}={value.value}", steps))
            continue
        if not isinstance(node, ast.Call):
            continue
        dotted = ctx.dotted(node.func) or ""
        if dotted.rsplit(".", 1)[-1] == "TracerProvider":
            candidates.setdefault("TracerProvider.sampler", []).append(node)
            argument = _argument(node, 0, "sampler")
            value, assignment = _resolve(ctx, argument) if argument is not None else (None, None)
            steps = sampler_steps(ctx, value) if value is not None else None
            if steps is None:
                continue
            via = f" (via {argument.id})" if assignment is not None else ""
            found.append((node, assignment or node, "TracerProvider.sampler",
                          f"TracerProvider is given the sampler {ast.unparse(value)}{via}", steps))
        elif dotted.startswith("aws_xray_sdk.") and dotted.endswith(".configure"):
            sampling = next((kw.value for kw in node.keywords if kw.arg == "sampling"), None)
            if sampling is not None:
                candidates.setdefault("xray_recorder.configure.sampling", []).append(node)
            if isinstance(sampling, ast.Constant) and sampling.value is False:
                found.append((node, node, "xray_recorder.configure.sampling",
                              "The X-Ray recorder is configured with sampling=False, which traces every request", 0))

    hits = []
    env = environment(ctx.path, [], ctx.declared)
    if env == "nonprod":
        return hits
    where = "production code" if env in ("prod", "template") else "application code (production use not established)"
    for site, evidence, anchor, summary, steps in found:
        state = _branch_state(ctx, site, [node for node in candidates[anchor] if node is not site])
        if state == "choice":
            continue
        if anchor == "TracerProvider.sampler":
            summary += ", which samples every root trace (child spans follow the caller)" if steps else \
                ", which samples every trace"
        elif anchor.startswith("os.environ:"):
            summary += ": every root trace is sampled (child spans follow the caller)" if steps else \
                ": every trace is sampled"
        if state == "guard":
            summary += " whenever the enclosing condition enables it"
            steps += 1
        hits.append(Hit(
            node=evidence,
            anchor=f"{ctx.qualname(site)}:{anchor}",
            summary=f"{summary}, in {where}",
            confidence=_lower(PYTHON_CONFIDENCE[env], steps),
        ))
    return hits


def run(ctx):
    return _python_hits(ctx) if ctx.format == "python" else _config_hits(ctx)


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    declared = context.get("environment") if isinstance(context, dict) else None
    if declared is not None and not isinstance(declared, str):
        raise EvaluationError("context.environment must be a string when present")
    check = types.SimpleNamespace(**{
        name: getattr(sys.modules[__name__], name)
        for name in ("CHECK_ID", "DETECTOR_VERSION", "NOQA", "SUPPORTED_FORMATS", "REFERENCES",
                     "RECOMMENDATION", "LIMITATION", "run")
    })
    check.parse = functools.partial(parse, declared=(declared or "").strip().lower() == "production")
    return static.evaluate_static(payload, check)
