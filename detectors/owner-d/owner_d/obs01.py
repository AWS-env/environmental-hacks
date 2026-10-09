"""OBS-01: DEBUG/TRACE logging enabled in production.

Detector semantics version 1.0.0. Static only: configuration files are scanned line by line
(so evidence is the exact source line) and Python is parsed with `ast`. Nothing is imported
or executed. The log-volume telemetry half of the check is not part of v1.

A finding needs (1) a log-level setting, (2) a literal DEBUG/TRACE value and (3) a file or
key path that is not marked as non-production. The environment comes from path, key and
Docker stage names; `context.environment: "production"` declares unmarked files production.
"""

from __future__ import annotations

import ast
import configparser
import functools
import json
import re
import sys
import tomllib
import types
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import static
from .logcalls import LEVEL_CONSTANTS, LEVEL_NUMBERS, _is_logger, logger_variables
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "OBS-01"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-01", "OBS01")
SUPPORTED_FORMATS = (
    "YAML, JSON, TOML, INI/CFG, .properties, .env files, Dockerfiles and Python (.py)"
)
VERBOSE = ("debug", "trace")

REFERENCES = (
    "https://openobserve.ai/blog/observability-cost-optimization-tactics/",
    "https://dev.to/anderson_leite/rethinking-observability-costs-how-structured-logging-can-save-you-thousands-54bh",
    "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs-log-level.html",
    "https://docs.python.org/3/howto/logging.html",
)
RECOMMENDATION = (
    "Use INFO (or WARNING) as the production default and enable DEBUG/TRACE only temporarily for an "
    "incident, through an environment variable or runtime setting that is reverted afterwards. Drop "
    "debug records at ingest if a dependency must emit them."
)
LIMITATION = (
    "Static configuration does not prove actual log volume or environmental impact: OBS-01 v1 reads "
    "literal settings only. Runtime overrides (environment variables, CLI flags, deployment parameters, "
    "template variables) and CloudWatch log volume are not observed. The environment is inferred from "
    "path, key and Docker stage names, so findings in files without a production marker are low confidence."
)

PROD = frozenset({"prod", "production", "prd"})
# Environment names, matched in the file path and in the keys leading to the setting.
NONPROD_ENV = frozenset({
    "dev", "develop", "development", "local", "test", "tests", "testing", "staging", "stage", "stg",
    "qa", "uat", "sandbox", "demo", "debug", "ci",
})
# Folder/file names, matched in the path only (`spec` and `template` are also Kubernetes keys).
NONPROD_PATH = NONPROD_ENV | {"conftest", "spec", "specs", "e2e", "fixture", "fixtures", "mock", "mocks", "docs", "tutorial"}
CI_PATH = re.compile(r"(^|/)(\.github|\.circleci|\.buildkite)/|(^|/)\.gitlab-ci\.ya?ml$")
# Copy-me templates: non-production on their own, a production default when combined with PROD.
TEMPLATE = frozenset({"example", "examples", "sample", "samples", "template", "dist"})
LEVEL_KEYS = frozenset({"loglevel", "logginglevel", "loggerlevel", "rustlog"})
LOGISH = frozenset({"log", "logs", "logging", "logger", "loggers", "root", "rootlogger"})


# --- key and value rules -----------------------------------------------------------------

def _norm(segment):
    return re.sub(r"[\s_\-]", "", str(segment)).lower()


def _logish(segment):
    return segment in LOGISH or segment.startswith(("logger", "logging", "log4j")) or segment.endswith(
        ("logger", "loggers", "logging"))


def _handlerish(segment):
    return segment.startswith(("handler", "appender")) or segment.endswith(
        ("handler", "handlers", "appender", "appenders"))


def level_key_index(path):
    """Index of the segment that makes `path` a logger level setting, or None.

    Matches `LOG_LEVEL`-style keys anywhere in the path (`Logging.LogLevel.Default`), a `level`
    below a logger-ish key (`logging.level.root`, `loggers.app.level`, `[logger_root] level`)
    and log4j 1.x `rootLogger`. Handler/appender levels only filter output, and keys named after
    a level (`DEBUG_LOG_LEVEL`) define or select a debug profile, so neither matches.
    """
    norm = [_norm(segment) for segment in path]
    for index, segment in enumerate(norm):
        if any(_handlerish(prev) for prev in norm[:index]):
            return None
        if segment in LEVEL_KEYS or segment.endswith(("loglevel", "logginglevel")):
            # `TRACE_LOG_LEVEL = 5` names a level; it does not select one.
            return None if any(level in segment for level in VERBOSE) else index
        if segment == "level" and any(_logish(prev) for prev in norm[:index]):
            return index
        if segment in ("rootlogger", "rootcategory") and index == len(norm) - 1:
            return index
        if index == 1 and norm[0] == "log4j" and segment in ("logger", "category"):
            return index
    return None


def verbose_level(value):
    """'debug'/'trace' if a literal value enables it (`DEBUG`, `DEBUG, stdout`, `info,app=trace`)."""
    if not isinstance(value, str):
        return None
    for part in value.split(","):
        level = part.split("=")[-1].strip().lower()
        if level in VERBOSE:
            return level
    return None


def _tokens(*texts):
    return {token for text in texts for token in re.split(r"[^a-z0-9]+", str(text).lower()) if token}


def environment(locator, segments, declared):
    """'nonprod' | 'prod' | 'template' | 'declared' | 'unknown' for one setting."""
    path, keys = _tokens(locator), _tokens(*segments)
    if path & NONPROD_PATH or keys & NONPROD_ENV or CI_PATH.search(locator):
        return "nonprod"
    if (path | keys) & PROD:
        return "template" if path & TEMPLATE else "prod"
    if path & TEMPLATE:
        return "nonprod"
    return "declared" if declared else "unknown"


def _clean(value):
    """Strip quotes, inline comments and use the default of `${VAR:-default}`."""
    value = value.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        value = value[1:end] if end > 0 else value[1:]
    else:
        value = re.split(r"\s[#;]", value, maxsplit=1)[0].strip()
    default = re.fullmatch(r"\$\{\w+:?-([^}]*)\}", value)
    return default.group(1) if default else value


class UnsupportedConstruct(ValueError):
    """The file uses syntax this line scanner does not interpret, near a DEBUG/TRACE value."""


# A level key inside an inline `{...}`/`[...]` value; CLI flags such as `-log.level=debug` are not keys.
_INLINE_LEVEL = re.compile(r"""(?i)(?<![-\w.])[\w.]*(log\w*|level)["']?\s*[:=]\s*["']?(debug|trace)\b""")
_INLINE_KEY = re.compile(r"(?i)(?<![-\w.])(log\w*|level)\b")
_INLINE_VERBOSE = re.compile(r"""(?i):\s*["']?(debug|trace)\b""")


def _check_inline(value, number):
    nested = value.lstrip().startswith("{") and _INLINE_KEY.search(value) and _INLINE_VERBOSE.search(value)
    if nested or _INLINE_LEVEL.search(value):
        raise UnsupportedConstruct(f"line {number}: inline collection is not interpreted")


# --- config file scanners: yield (key path, raw value, first line, value line) --------------

def _strip_comment(line):
    quote = None
    for index, char in enumerate(line):
        if quote:
            quote = None if char == quote else quote
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


_YAML_KEY = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s"'#{\[][^:]*?)\s*:(?:\s+(?P<value>.*))?$""")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][\w.\-]*)=(.*)$")


def _unquote(text):
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" else text


def _yaml_entries(lines):
    stack, pending, block = [], {}, None
    for number, raw in enumerate(lines, 1):
        text = _strip_comment(raw).rstrip()
        stripped = text.lstrip(" ")
        if not stripped:
            continue
        indent = len(text) - len(stripped)
        if block is not None:
            if indent > block:
                continue
            block = None
        if stripped.startswith("\t"):
            raise ValueError(f"line {number}: tab indentation is not valid YAML")
        if indent == 0 and stripped.startswith(("---", "...", "%")):
            stack, pending = [], {}
            continue
        is_item = False
        while stripped == "-" or stripped.startswith("- "):
            rest = stripped[1:].lstrip(" ")
            stack = [entry for entry in stack if entry[0] <= indent]
            pending = {k: v for k, v in pending.items() if k < indent + 1}
            indent, stripped, is_item = indent + len(stripped) - len(rest), rest, True
        if not stripped:
            continue
        stack = [entry for entry in stack if entry[0] < indent]
        pending = {k: v for k, v in pending.items() if k <= indent}
        path = [key for _, keys in stack for key in keys]
        match = _YAML_KEY.match(stripped)
        if not match:
            if stripped[0] in "{[":
                _check_inline(stripped, number)
            assignment = _ASSIGNMENT.match(_unquote(stripped)) if is_item else None
            if assignment:
                yield path + [assignment.group(1)], assignment.group(2), number, number
            continue
        key, value = _unquote(match.group("key")), (match.group("value") or "").strip()
        keys = key.split(".")  # Spring-style `logging.level.root: DEBUG`
        value = re.sub(r"^&\S+\s*", "", value)  # an anchor does not change the value
        if not value:
            stack.append((indent, keys))
            continue
        if value[0] in "|>":
            block = indent
            continue
        if value[0] in "{[":
            _check_inline(value, number)
            continue
        if value[0] in "*!":
            continue  # aliases and tags (e.g. CloudFormation !Ref) are not literal values
        if key.lower() == "name" and level_key_index([_clean(value)]) is not None:
            pending[indent] = (number, _clean(value))
        elif key.lower() == "value" and indent in pending:
            first, name = pending.pop(indent)
            yield path + [name], value, first, number
        yield path + keys, value, number, number


_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]:,]|[^\s{}\[\]:,"]+|\n')


def _json_entries(content):
    json.loads(content)  # validation: JSONDecodeError is a ValueError
    tokens, line = [], 1
    for match in _JSON_TOKEN.finditer(content):
        if match.group() == "\n":
            line += 1
        else:
            tokens.append((match.group(), line))
    entries = []

    def scalar(token):
        return json.loads(token) if token.startswith('"') else token

    def walk(index, path):
        token = tokens[index][0]
        if token not in "{[":
            return index + 1
        close, index, pairs = ("}" if token == "{" else "]"), index + 1, {}
        while tokens[index][0] != close:
            key = None
            if close == "}":
                key, index = str(scalar(tokens[index][0])), index + 2
            value, number = tokens[index]
            if key is not None and value not in "{[":
                pairs[key.lower()] = (scalar(value), number)
                entries.append((path + [key], str(scalar(value)), number, number))
            index = walk(index, path + ([key] if key is not None else []))
            if tokens[index][0] == ",":
                index += 1
        name, value = pairs.get("name"), pairs.get("value")
        if name and value and isinstance(name[0], str) and level_key_index([name[0]]) is not None:
            entries.append((path + [name[0]], str(value[0]), min(name[1], value[1]), max(name[1], value[1])))
        return index + 1

    try:
        walk(0, [])
    except RecursionError as error:
        raise ValueError("JSON nesting too deep") from error
    return entries


def _flat_entries(lines, fmt):
    """`.env`, `.properties`, INI and TOML: `[section]` headers and `key = value` lines."""
    section = []
    separators = "=" if fmt in ("env", "toml") else "=:"
    pattern = re.compile(rf"^([^{separators}\s][^{separators}]*?)\s*[{separators}]\s*(.*)$")
    for number, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or text[0] in "#;!":
            continue
        if fmt in ("ini", "toml") and text.startswith("["):
            header = re.match(r"^\[\[?\s*(.+?)\s*\]\]?", text)
            if header:
                name = header.group(1)
                section = [_unquote(s.strip()) for s in name.split(".")] if fmt == "toml" else [name]
            continue
        if fmt == "env":
            text = re.sub(r"^export\s+", "", text)
        match = pattern.match(text)
        if not match:
            continue
        key, value = match.group(1).strip(), match.group(2)
        if fmt == "toml" and value.lstrip()[:1] in ("{", "["):
            _check_inline(value, number)
            continue
        keys = [key] if fmt == "env" else [_unquote(part.strip()) for part in key.split(".")]
        yield section + keys, value, number, number


def _dockerfile_entries(lines):
    stage, continuing = None, False
    for number, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if not continuing:
            source = re.match(r"(?i)^FROM\s+\S+(?:\s+AS\s+(\S+))?", text)
            if source:
                stage = source.group(1)
                continue
            env = re.match(r"(?i)^ENV\s+(.*)$", text)
            if not env:
                continue
            body = env.group(1)
        else:
            body = text
        continuing = body.endswith("\\")
        body = body.rstrip("\\").strip()
        prefix = [stage] if stage else []
        pairs = re.findall(r"""([A-Za-z_]\w*)=("(?:[^"\\]|\\.)*"|'[^']*'|\S+)""", body)
        if not pairs and "=" not in body.split(" ", 1)[0]:
            legacy = re.match(r"^(\S+)\s+(.+)$", body)  # ENV KEY value
            pairs = [legacy.groups()] if legacy else []
        for key, value in pairs:
            yield prefix + [key], value, number, number


# --- parsing -------------------------------------------------------------------------------

def file_format(locator):
    name = PurePosixPath(locator).name.lower()
    suffix = PurePosixPath(name).suffix
    if suffix == ".py":
        return "python"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix in (".json", ".toml", ".properties"):
        return suffix[1:]
    if suffix in (".ini", ".cfg"):
        return "ini"
    if name == ".env" or name.startswith(".env.") or suffix == ".env":
        return "env"
    if name == "dockerfile" or name.startswith("dockerfile.") or suffix == ".dockerfile":
        return "dockerfile"
    return None


@dataclass(frozen=True)
class Span:
    lineno: int
    end_lineno: int


class ConfigCtx:
    """A scanned configuration file: exact lines plus (key path, value, lines) entries."""

    evidence_lines = static.Ctx.evidence_lines

    def __init__(self, path, source, fmt, declared):
        self.path, self.format, self.declared = path, fmt, declared
        self.lines = source.splitlines()
        if fmt == "yaml":
            self.entries = list(_yaml_entries(self.lines))
        elif fmt == "json":
            self.entries = _json_entries(source)
        elif fmt == "dockerfile":
            self.entries = list(_dockerfile_entries(self.lines))
        else:
            if fmt == "toml":
                tomllib.loads(source)
            elif fmt == "ini":
                try:
                    configparser.ConfigParser(interpolation=None, strict=False).read_string(source)
                except configparser.Error as error:
                    raise ValueError(str(error)) from error
            self.entries = list(_flat_entries(self.lines, fmt))


def parse(locator, content, declared=False):
    fmt = file_format(locator)
    if fmt is None:
        return None
    if fmt == "python":
        ctx = static.Ctx(locator, content)
        ctx.format, ctx.declared = "python", declared
        return ctx
    return ConfigCtx(locator, content, fmt, declared)


# --- checks --------------------------------------------------------------------------------

CONFIG_CONFIDENCE = {"prod": "high", "template": "medium", "declared": "medium", "unknown": "low"}
PYTHON_CONFIDENCE = {"prod": "medium", "template": "low", "declared": "low", "unknown": "low"}
WHERE = {
    "prod": "Production configuration",
    "template": "Production configuration template",
    "declared": "Configuration in a scan declared as production",
    "unknown": "Configuration with no environment marker (production use not established)",
}


def _config_hits(ctx):
    hits = []
    for path, raw, first, last in ctx.entries:
        index = level_key_index(path)
        level = verbose_level(_clean(raw)) if index is not None else None
        if level is None:
            continue
        env = environment(ctx.path, path[:index + 1], ctx.declared)
        if env == "nonprod":
            continue
        key = ".".join(str(segment) for segment in path)
        span = Span(first, last) if last - first < static.EVIDENCE_MAX_LINES else Span(last, last)
        hits.append(Hit(
            node=span,
            anchor=f"log-level:{key}",
            summary=f"{WHERE[env]} enables {level.upper()} logging via {key}",
            confidence=CONFIG_CONFIDENCE[env],
        ))
    return hits


def _python_level(ctx, node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value.lower() if node.value.lower() in VERBOSE else None
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            level = LEVEL_NUMBERS.get(node.value)
            return level if level in VERBOSE else None
        return None
    dotted = ctx.dotted(node) or ""
    if "." not in dotted:  # a bare `DEBUG` name is not known to be a logging constant
        return None
    level = LEVEL_CONSTANTS.get(dotted.rsplit(".", 1)[-1])
    return level if level in VERBOSE else None


def _is_main_guard(test):
    return isinstance(test, ast.Compare) and "__name__" in ast.unparse(test) and "__main__" in ast.unparse(test)


def _conditional(ctx, node):
    """Inside an `if` (other than `if __name__ == "__main__"`): the level is chosen at runtime."""
    return any(
        isinstance(a, ast.IfExp) or (isinstance(a, ast.If) and not _is_main_guard(a.test))
        for a in ctx.ancestors(node)
    )


def _target_segments(target):
    segments = []
    while isinstance(target, ast.Subscript):
        key = target.slice
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        segments.append(key.value)
        target = target.value
    if not isinstance(target, (ast.Name, ast.Attribute)):
        return None
    return [ast.unparse(target)] + list(reversed(segments))


def _setting_path(ctx, value):
    """Key path of a dict entry / assignment whose value is `value`, e.g. LOGGING.root.level."""
    path, node = [], value
    while True:
        parent = ctx.parent(node)
        if isinstance(parent, ast.Dict):
            index = next((i for i, v in enumerate(parent.values) if v is node), None)
            key = parent.keys[index] if index is not None else None
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            path.append(key.value)
            node = parent
            continue
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
            target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
            prefix = _target_segments(target)
            return None if prefix is None else prefix + list(reversed(path))
        return list(reversed(path)) if path else None


def _python_hits(ctx):
    known = logger_variables(ctx)
    found = []  # (node, anchor, description, level, segments for environment)
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call):
            dotted = ctx.dotted(node.func) or ""
            func = node.func
            if dotted == "logging.basicConfig":
                level_arg = next((kw.value for kw in node.keywords if kw.arg == "level"), None)
                level = _python_level(ctx, level_arg) if level_arg is not None else None
                if level:
                    found.append((node, "logging.basicConfig", "logging.basicConfig()", level, []))
            elif isinstance(func, ast.Attribute) and func.attr == "setLevel" and node.args:
                level = _python_level(ctx, node.args[0])
                if level and _is_logger(ctx, func.value, known):
                    receiver = ast.unparse(func.value)
                    found.append((node, f"{receiver}.setLevel", f"{receiver}.setLevel()", level, []))
            elif dotted in ("os.getenv", "os.environ.get", "os.environ.setdefault") and len(node.args) >= 2:
                name, default = node.args[0], node.args[1]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    level = _python_level(ctx, default)
                    if level and level_key_index([name.value]) is not None:
                        found.append((node, f"{dotted}:{name.value}", f"the {name.value} default", level, []))
        elif isinstance(node, ast.expr):
            level = _python_level(ctx, node)
            if not level:
                continue
            path = _setting_path(ctx, node)
            index = level_key_index(path) if path else None
            if index is not None:
                key = ".".join(path)
                found.append((node, key, key, level, path[:index + 1]))

    hits = []
    for node, anchor, description, level, segments in found:
        env = environment(ctx.path, segments, ctx.declared)
        if env == "nonprod" or _conditional(ctx, node):
            continue
        where = "production code" if env in ("prod", "template") else "application code (production use not established)"
        hits.append(Hit(
            node=node,
            anchor=f"{ctx.qualname(node)}:{anchor}",
            summary=f"{description} sets an unconditional {level.upper()} log level in {where}",
            confidence=PYTHON_CONFIDENCE[env],
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
