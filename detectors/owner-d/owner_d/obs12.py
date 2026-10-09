"""OBS-12: unused default integrations enabled (static auto-instrumentation launch scan).

Detector semantics version 1.0.0. Flags zero-code OpenTelemetry auto-instrumentation that is
started with its default "instrument everything" selection while the same deployment unit
sets no instrumentation selection:

- Python `opentelemetry-instrument` without `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` ("The
  Python agent by default will detect a Python program's packages and instrument any packages
  it can ... can result in too much or unwanted data");
- Node.js `@opentelemetry/auto-instrumentations-node/register` without
  `OTEL_NODE_ENABLED_INSTRUMENTATIONS`/`OTEL_NODE_DISABLED_INSTRUMENTATIONS` ("By default, all
  supported instrumentation libraries are enabled");
- Node.js `getNodeAutoInstrumentations()` / `getNodeAutoInstrumentations({})` in setup code.

Launches are read from deployment YAML (per document), Dockerfiles (shipped stages),
package.json scripts and JS/TS setup files, as text: nothing is executed, rendered or resolved. Which libraries are
installed and how much telemetry each instrumentation produces are not observed, so no
measurements are emitted.
"""

from __future__ import annotations

import json
import re
import sys

from . import dockerfile, miniyaml, textstatic
from .miniyaml import Mapping
from .obs09 import DEV_NAMES, _dev_path
from .otelconfig import get, text
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "OBS-12"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-12", "OBS12")
FORMATS = (
    "deployment YAML (.yaml/.yml), Dockerfiles, package.json scripts and Node.js setup code "
    "(.js/.mjs/.cjs/.ts/.mts/.cts) that start OpenTelemetry zero-code auto-instrumentation"
)

REFERENCES = (
    "https://opentelemetry.io/docs/zero-code/python/configuration/#disabling-specific-instrumentations",
    "https://opentelemetry.io/docs/zero-code/js/configuration/",
    "https://github.com/open-telemetry/opentelemetry-js-contrib/blob/main/packages/auto-instrumentations-node/README.md",
    "https://opentelemetry.io/docs/languages/js/libraries/",
)
REC_PYTHON = (
    "Set OTEL_PYTHON_DISABLED_INSTRUMENTATIONS to the instrumentations whose telemetry you do not use (for example "
    "`redis,kafka,grpc_client`), or install only the instrumentation packages you need."
)
REC_NODE = (
    "Set OTEL_NODE_ENABLED_INSTRUMENTATIONS to the instrumentations you use (for example `http,express,pg`), or "
    "disable unused ones with OTEL_NODE_DISABLED_INSTRUMENTATIONS or `{ enabled: false }`; or register only the "
    "individual instrumentation packages you need."
)
RECOMMENDATION = REC_PYTHON
LIMITATION = (
    "Static launch scan only: OBS-12 reads one file at a time, so a selection set elsewhere (kustomize overlays, "
    "Helm values, `docker run -e`, env files) is not visible; documents with envFrom/env_file/environmentFiles "
    "are not judged. It does not see which libraries are installed or how much telemetry each instrumentation "
    "produces, so no measurements are emitted. Java agents, vendor agents (ddtrace-run, New Relic), Lambda layer "
    "wrappers, the OpenTelemetry Operator Instrumentation resource, JSON task definitions, npm dev/test scripts and "
    "development/test/CI files are not evaluated."
)

JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")
NODE_PACKAGE = "@opentelemetry/auto-instrumentations-node"
GET_ALL = "getNodeAutoInstrumentations"

# (language, launcher regex, selection regex, recommendation)
LAUNCHERS = (
    ("python", "opentelemetry-instrument",
     re.compile(r"(?<![\w.-])opentelemetry-instrument(?![\w.-])"),
     re.compile(r"OTEL_PYTHON_DISABLED_INSTRUMENTATIONS|--python_disabled_instrumentations"), REC_PYTHON),
    ("node", "auto-instrumentations-node/register",
     re.compile(r"@opentelemetry/auto-instrumentations-node/register(?![\w-])"),
     re.compile(r"OTEL_NODE_(EN|DIS)ABLED_INSTRUMENTATIONS"), REC_NODE),
)
NODE_SELECTION = LAUNCHERS[1][3]
WHAT = {
    "python": "instruments every installed package it can (the Python agent default)",
    "node": "enables all ~45 bundled instrumentations except fs and host-metrics (the default)",
}

_DOC_BOUND = re.compile(r"^(---|\.\.\.)(\s|$)")
_EXTERNAL_ENV = re.compile(r"^\s*(-\s+)?(envFrom|env_file|environmentFiles)\s*:")
_JS_NOQA = re.compile(r"//\s*noqa\b\s*(?::\s*([A-Za-z0-9_, -]+))?", re.I)
_GET_ALL_CALL = re.compile(r"(?<![\w$])" + GET_ALL + r"\s*\(")
# package.json script names that run development/test tooling, not the deployed process.
DEV_SCRIPTS = {"dev", "develop", "debug", "test", "tests", "e2e", "watch", "lint", "local"}


class Ctx:
    def __init__(self, kind, content, **extra):
        self.kind = kind
        self.lines = content.splitlines()
        self.__dict__.update(extra)


def _not_evaluated(locator):
    """Raise NotEvaluated for development/test/CI paths (OBS-09 tokens, also inside directory names such as
    `contract-tests/`, and `.github/`)."""
    parts = [p.lower() for p in re.split(r"[\\/]", locator)]
    tokens = {token for part in parts[:-1] for token in re.split(r"[._-]", part)}
    marked = ".github" if ".github" in parts[:-1] else _dev_path(locator) or min(tokens & DEV_NAMES, default=None)
    if marked:
        raise NotEvaluated(
            f"path marks a development/test/CI file ({marked}); OBS-12 v1 evaluates deployed launches only"
        )


def _has_launcher(content):
    return any(regex.search(content) for _, _, regex, _, _ in LAUNCHERS)


def parse(locator, content):
    lower = locator.lower()
    if lower.endswith(JS_SUFFIXES):
        if GET_ALL not in content or NODE_PACKAGE not in content:
            raise Unsupported(locator)
        _not_evaluated(locator)
        return _parse_js(content)
    if re.split(r"[\\/]", lower)[-1] == "package.json":
        if not _has_launcher(content):
            raise Unsupported(locator)
        _not_evaluated(locator)
        try:
            scripts = json.loads(content).get("scripts")
        except (ValueError, AttributeError):
            raise ParseError("invalid JSON") from None
        scripts = scripts if isinstance(scripts, dict) else {}
        return Ctx("package", content, scripts={k: v for k, v in scripts.items() if isinstance(v, str)})
    if dockerfile.is_dockerfile(locator):
        if not _has_launcher(content):
            raise Unsupported(locator)
        _not_evaluated(locator)
        return Ctx("dockerfile", content, dockerfile=dockerfile.parse(locator, content))
    if not lower.endswith((".yaml", ".yml")) or not _has_launcher(content):
        raise Unsupported(locator)
    _not_evaluated(locator)
    try:
        docs = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    return Ctx("yaml", content, docs=[doc for doc in docs if isinstance(doc, Mapping)])


def _hit(language, launcher, anchor_prefix, line, block_line, where, confidence, recommendation, end_line=None):
    return TextHit(
        line=line,
        end_line=end_line,
        block_line=block_line,
        anchor=f"{anchor_prefix}{language}:{launcher}",
        summary=(
            f"{where} starts OpenTelemetry auto-instrumentation with `{launcher}`, which {WHAT[language]}, and sets "
            f"no instrumentation selection, so every matching integration produces telemetry whether or not it is used."
        ),
        confidence=confidence,
        recommendation=recommendation,
    )


# -- YAML ------------------------------------------------------------------------------------


def _segments(lines):
    """(first, last) 1-based line ranges of the YAML documents (split on `---` / `...`)."""
    bounds, start = [], 1
    for number, line in enumerate(lines, 1):
        if _DOC_BOUND.match(line):
            bounds.append((start, number - 1))
            start = number + 1
    bounds.append((start, len(lines)))
    return [(first, last) for first, last in bounds if first <= last]


def _label(docs, first, last, line):
    """('Kind/name:' | 'service/name:' | '', is a deployment object) for a hit line."""
    doc = next((d for d in docs if first <= d.line <= last), None)
    if doc is None:
        return "", False
    kind, name = text(doc.get("apiVersion")) and text(doc.get("kind")), text(get(doc, "metadata", "name"))
    if kind:
        return f"{kind}/{name or 'unnamed'}:", True
    services = doc.get("services")
    if isinstance(services, Mapping):
        before = [(key_line, key) for key, key_line in services.key_lines.items() if key_line <= line]
        return (f"service/{max(before)[1]}:", True) if before else ("", True)
    return "", False


def _yaml_hits(ctx):
    for first, last in _segments(ctx.lines):
        code = [(n, miniyaml.strip_comment(ctx.lines[n - 1])) for n in range(first, last + 1)]
        code = [(n, line) for n, line in code if not line.lstrip().startswith("#")]
        if any(_EXTERNAL_ENV.match(line) for _, line in code):
            continue
        for language, launcher, regex, selection, recommendation in LAUNCHERS:
            if any(selection.search(line) for _, line in code):
                continue
            for number, line in code:
                if not regex.search(line):
                    continue
                prefix, deployment = _label(ctx.docs, first, last, number)
                where = f"{prefix.rstrip(':')}" if prefix else "This YAML document"
                yield _hit(language, launcher, prefix, number, None, where, "medium" if deployment else "low",
                           recommendation)


# -- Dockerfile ------------------------------------------------------------------------------


def _dockerfile_hits(ctx):
    df = ctx.dockerfile
    shipped = [instruction for stage in df.shipped_chain() for instruction in [stage.instruction] + stage.body]
    effective = {}
    for instruction in shipped:
        if instruction.keyword in ("CMD", "ENTRYPOINT"):
            effective[instruction.keyword] = instruction  # a later CMD/ENTRYPOINT replaces the earlier one
    launches = [i for i in shipped if i.keyword == "ENV"] + list(effective.values())
    texts = [instruction.args for instruction in shipped] + [
        f"{name}={value or ''}" for name, value in df.global_args.items()
    ]
    for language, launcher, regex, selection, recommendation in LAUNCHERS:
        if any(selection.search(value) for value in texts):
            continue
        for instruction in sorted(launches, key=lambda i: i.line):
            found = [n for n in instruction.lines if regex.search(df.lines[n - 1])]
            if found:
                yield _hit(language, launcher, f"{instruction.keyword}:", found[0], instruction.line,
                           f"The image's {instruction.keyword}", "low", recommendation, instruction.lines[-1])


# -- package.json scripts -------------------------------------------------------------------


def _package_hits(ctx):
    for language, launcher, regex, selection, recommendation in LAUNCHERS:
        if any(selection.search(line) for line in ctx.lines):
            continue
        for name, command in ctx.scripts.items():
            if not regex.search(command) or set(re.split(r"[:._-]", name.lower())) & DEV_SCRIPTS:
                continue
            key = json.dumps(name)
            found = [n for n, line in enumerate(ctx.lines, 1) if key in line and regex.search(line)]
            if found:
                yield _hit(language, launcher, f"scripts/{name}:", found[0], None, f"The {name!r} npm script",
                           "low", recommendation)


# -- Node.js setup code ----------------------------------------------------------------------


def _blank(content, strings=True):
    """`content` with comments (and string contents if `strings`) replaced by spaces; newlines kept."""
    out, i, n = list(content), 0, len(content)

    def clear(start, end):
        for k in range(start, end):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        char, pair = content[i], content[i:i + 2]
        if pair == "//":
            end = content.find("\n", i)
            end = n if end < 0 else end
            clear(i, end)
            i = end
        elif pair == "/*":
            end = content.find("*/", i + 2)
            end = n if end < 0 else end + 2
            clear(i, end)
            i = end
        elif char in "'\"`":
            k = i + 1
            while k < n and content[k] != char:
                if content[k] == "\\":
                    k += 1
                elif content[k] == "\n" and char != "`":
                    break
                k += 1
            if strings:
                clear(i + 1, min(k, n))
            i = k + 1
        else:
            i += 1
    return "".join(out)


def _parse_js(content):
    code = _blank(content)
    calls = []
    for match in _GET_ALL_CALL.finditer(code):
        depth, index = 0, match.end() - 1
        while index < len(code):
            depth += {"(": 1, ")": -1}.get(code[index], 0)
            if depth == 0:
                break
            index += 1
        line = code.count("\n", 0, match.start()) + 1
        if depth:
            raise ParseError(f"unbalanced parentheses in {GET_ALL}( call on line {line}")
        calls.append((line, code.count("\n", 0, index) + 1, re.sub(r"\s", "", code[match.end():index])))
    selected = NODE_SELECTION.search(_blank(content, strings=False)) is not None
    return Ctx("js", content, calls=calls, selected=selected)


def _js_suppressed(lines, line):
    def suppresses(value):
        for match in _JS_NOQA.finditer(value):
            named = match.group(1)
            if named is None or {c.strip().upper() for c in re.split(r"[,\s]+", named)} & set(NOQA):
                return True
        return False

    if suppresses(lines[line - 1]):
        return True
    index = line - 2
    while index >= 0 and lines[index].lstrip().startswith("//"):
        if suppresses(lines[index]):
            return True
        index -= 1
    return False


def _js_hits(ctx):
    if ctx.selected:
        return
    for line, end_line, args in ctx.calls:
        if args not in ("", "{}") or _js_suppressed(ctx.lines, line):
            continue
        call = f"{GET_ALL}({args})"
        yield TextHit(
            line=line,
            end_line=end_line,
            anchor=f"node:{GET_ALL}()",
            summary=(
                f"`{call}` enables all ~45 bundled instrumentations except fs and host-metrics (the default), and "
                f"this file sets no instrumentation selection, so every matching integration produces telemetry "
                f"whether or not it is used, unless the deployment sets OTEL_NODE_ENABLED_INSTRUMENTATIONS."
            ),
            confidence="low",
            recommendation=REC_NODE,
        )


def run(ctx):
    if ctx.kind == "yaml":
        return list(_yaml_hits(ctx))
    if ctx.kind == "dockerfile":
        return list(_dockerfile_hits(ctx))
    if ctx.kind == "package":
        return list(_package_hits(ctx))
    return list(_js_hits(ctx))


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
