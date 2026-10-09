"""LLM-09: no token/cost observability for LLM calls (static proxy).

Detector semantics version 1.0.0. Flags Python modules whose LLM API calls (Anthropic `messages.*`,
OpenAI chat completions / responses, Bedrock `converse`/`invoke_model*`) discard the token usage each
response returns, when nothing in the scanned code records token usage: no usage field is read, no
GenAI instrumentation (OpenTelemetry `gen_ai.usage.*`, OpenLLMetry, Langfuse, ...) and no Bedrock model
invocation logging is configured. Markers are collected payload-wide (Python, dependency manifests,
Dockerfiles, Terraform, YAML, CloudFormation JSON, shell scripts); findings are per module. A call
whose response leaves the module's view (returned, passed on, stored) is not judged. Static only.
"""

from __future__ import annotations

import ast
import re
import sys
import types
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import static
from .llmcalls import llm_calls
from .logcalls import log_calls
from .obs04 import SAMPLE_DIRS, SCRIPT_DIRS, SCRIPT_FILES, TEST_PATH, in_main_block, is_vendored
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-09"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-09", "LLM09")
SUPPORTED_FORMATS = (
    "Python (.py); dependency manifests, Dockerfiles, Procfiles, Terraform, YAML, CloudFormation JSON "
    "templates and shell scripts are read for observability markers only"
)

REFERENCES = (
    "https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md",
    "https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-token-metrics.md",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-runtime-metrics.html",
    "https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_TokenUsage.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genops02-bp01.html",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
RECOMMENDATION = (
    "Record the usage every response returns, per call and tagged with model and feature/route: emit "
    "OpenTelemetry GenAI attributes (gen_ai.usage.input_tokens/output_tokens, plus cache reads) via an "
    "instrumentation such as opentelemetry-instrumentation-botocore/openai/anthropic or OpenLLMetry, or log/"
    "publish response.usage (Bedrock Converse usage.inputTokens/outputTokens) as a structured log field or "
    "CloudWatch metric. For Bedrock, also enable model invocation logging with requestMetadata or use "
    "application inference profiles so spend can be attributed."
)
LIMITATION = (
    "Static proxy only: LLM-09 proves that a module's LLM calls discard the returned token usage and that "
    "no token observability is visible in the scanned files, not that spend is unknown; no tokens are "
    "measured. Provider-side totals still exist (Bedrock AWS/Bedrock InputTokenCount/OutputTokenCount per "
    "model, the OpenAI usage dashboard) but do not attribute usage to a code path. Not visible: invocation "
    "logging or instrumentation configured in the console or another repository, gateways that meter "
    "tokens, LangChain/LiteLLM wrappers and other languages. Calls whose response is returned, passed on or "
    "stored are not judged, and any marker in the payload (including tests and samples) suppresses all "
    "findings."
)

DROPPED, ESCAPES = "dropped", "escapes"
MAX_DEPTH = 8

# Response fields and dict keys that report token usage (Anthropic, OpenAI, Bedrock Converse/InvokeModel).
USAGE_NAMES = frozenset({
    "usage", "usage_metadata", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
    "total_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "prompt_tokens_details",
    "completion_tokens_details", "inputTokens", "outputTokens", "totalTokens", "cacheReadInputTokens",
    "cacheWriteInputTokens", "inputTokenCount", "outputTokenCount", "inputTextTokenCount",
    "amazon-bedrock-invocationMetrics",
})
USAGE_TEXT = re.compile(
    r"x-amzn-bedrock-(input|output)-token-count|gen_ai\.usage\.|gen_ai\.client\.(token|inference)\.usage"
    r"|llm\.usage\.|llm\.token_count\.|include_usage|application-inference-profile"
    r"|\b(InputTokenCount|OutputTokenCount)\b"
)
NAME_MARKERS = (
    ("Bedrock model invocation logging", re.compile(r"invocation_?logging_?configuration", re.I)),
    ("Bedrock application inference profile", re.compile(r"application_?inference_?profile", re.I)),
)
GENAI_INSTRUMENTATION = re.compile(
    r"opentelemetry[-_.]instrumentation[-_.](openai([-_]v2|[-_]agents)?|anthropic|bedrock|botocore|langchain"
    r"|llamaindex|llama[-_]index|google[-_]genai|vertexai|cohere|mistralai|groq|together|ollama"
    r"|transformers|haystack|crewai|litellm)(?![\w-])",
    re.I,
)
# Import roots / prefixes of SDKs that record LLM token usage.
OBSERVABILITY_ROOTS = frozenset({
    "traceloop", "langfuse", "langsmith", "openlit", "openinference", "phoenix", "agentops", "logfire",
    "weave", "braintrust", "promptlayer", "portkey_ai", "helicone", "ddtrace", "lunary",
})
OBSERVABILITY_PREFIXES = (
    "opentelemetry.semconv_ai", "opentelemetry.semconv._incubating.attributes.gen_ai", "amazon.opentelemetry",
    "posthog.ai", "sentry_sdk.integrations.openai", "sentry_sdk.integrations.anthropic",
    "sentry_sdk.integrations.langchain",
)
SENTRY_TRACING = frozenset({"traces_sample_rate", "traces_sampler", "enable_tracing"})
# Text markers. Package names are only matched in manifests and Dockerfiles.
PACKAGE_MARKER = re.compile(
    r"(?<![\w-])(traceloop-sdk|langfuse|langsmith|openlit|openinference-instrumentation[\w-]*|arize-phoenix"
    r"|agentops|logfire|weave|braintrust|promptlayer|portkey-ai|ddtrace|lunary|helicone)(?![\w-])",
    re.I,
)
TEXT_MARKERS = (
    ("GenAI OpenTelemetry instrumentation", GENAI_INSTRUMENTATION),
    ("zero-code instrumentation", re.compile(
        r"opentelemetry-instrument\b|opentelemetry-bootstrap|aws[-_]opentelemetry[-_]distro|/opt/otel-instrument"
        r"|/opt/otel-handler|AWSOpenTelemetryDistroPython|aws-otel-python|instrumentation\.opentelemetry\.io/"
        r"inject-python|admission\.datadoghq\.com/python-lib|ddtrace-run|DD_LLMOBS_ENABLED",
        re.I,
    )),
    ("Bedrock model invocation logging", re.compile(r"invocation[-_]?logging[-_]?configuration", re.I)),
    ("Bedrock application inference profile", re.compile(
        r"aws_bedrock_inference_profile|application[-_]?inference[-_]?profile", re.I,
    )),
    ("Bedrock token metrics", re.compile(r"\b(InputTokenCount|OutputTokenCount)\b")),
    ("GenAI token attributes", re.compile(r"gen_ai\.usage\.|gen_ai\.client\.(token|inference)\.usage")),
)

MANIFEST = re.compile(r"(^|/)(requirements[^/]*\.(txt|in)|constraints[^/]*\.txt|pyproject\.toml|Pipfile|setup\.cfg)$")
DOCKERFILE = re.compile(r"(^|/)(Dockerfile[^/]*|[^/]*\.dockerfile|Containerfile)$", re.I)
DEPLOY = re.compile(r"(^|/)Procfile$|\.(tf|ya?ml|sh)$|(^|/)([^/]*\.)?template\.json$", re.I)

EXTRA_SAMPLE_DIRS = frozenset({
    "notebooks", "notebook", "tutorials", "tutorial", "cookbook", "cookbooks", "demos", "demo", "playground",
})

# Response fields / methods whose value may still hold the usage, so its use is followed.
CARRIER_FIELDS = frozenset({
    "body", "stream", "metadata", "message", "response", "chunk", "bytes", "ResponseMetadata",
    "HTTPHeaders", "headers",
})
CARRIER_METHODS = frozenset({"read", "decode", "get_final_message", "get_final_completion", "get_final_response",
                             "until_done", "parse"})
JSON_LOADS = frozenset({"json.loads", "json.load", "orjson.loads", "ujson.loads"})
HARMLESS_CALLS = frozenset({"len", "isinstance", "bool", "type", "id", "hasattr"})
RECORDER_WORDS = frozenset({
    "usage", "token", "tokens", "cost", "costs", "metric", "metrics", "track", "record", "telemetry",
    "observe", "span", "trace", "meter", "emit", "log", "audit",
})
PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}


@dataclass(frozen=True)
class Project:
    markers: tuple  # (locator, reason)
    context_files: int  # manifests and deployment files supplied
    unreadable: int  # sources that could not be parsed (they may hide a marker)


class TextCtx:
    """A non-Python file read only for markers; it never produces findings."""

    def __init__(self, path, source, kind):
        self.path, self.kind, self.lines = path, kind, source.splitlines()


def file_kind(locator):
    if locator.endswith(".py"):
        return "python"
    if MANIFEST.search(locator):
        return "manifest"
    if DOCKERFILE.search(locator):
        return "dockerfile"
    if DEPLOY.search(locator):
        return "deploy"
    return None


def parse(locator, content):
    kind = file_kind(locator)
    if kind is None:
        return None
    if kind == "python":
        return static.Ctx(locator, content)
    return TextCtx(locator, content, kind)


def exempt_reason(locator):
    """Why a module's calls are not judged (vendored, sample, test, script), or None."""
    path = PurePosixPath(locator)
    dirs = {part.lower() for part in path.parts[:-1]}
    if is_vendored(locator):
        return "vendored"
    if (SAMPLE_DIRS | EXTRA_SAMPLE_DIRS) & dirs:
        return "sample"
    if TEST_PATH.search(locator):
        return "test"
    if path.name in SCRIPT_FILES or SCRIPT_DIRS & dirs:
        return "script"
    return None


# --- what happens to a response ------------------------------------------------------------

def _scope_nodes(scope):
    """(own nodes, nested-scope nodes) of a function/class/module, split at nested scopes."""
    own, nested, stack = [], [], [(child, False) for child in ast.iter_child_nodes(scope)]
    while stack:
        node, inner = stack.pop()
        (nested if inner else own).append(node)
        inner = inner or isinstance(node, static.SCOPE_NODES)
        stack.extend((child, inner) for child in ast.iter_child_nodes(node))
    return own, nested


def _scope_of(ctx, node):
    return next((a for a in ctx.ancestors(node) if isinstance(a, static.SCOPE_NODES)), ctx.tree)


def _loads(nodes, name):
    return [n for n in nodes if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)]


def _combine(results):
    fate = ESCAPES if any(f == ESCAPES for f, _ in results) else DROPPED
    return fate, next((reason for _, reason in results if reason), None)


def _name_uses(ctx, target, loggers, depth, within=None):
    """Fate of a value bound to `target`, from every later read of the name in its scope."""
    if within is not None:  # a comprehension variable is only visible inside the comprehension
        own, nested = list(ast.walk(within)), []
    else:
        own, nested = _scope_nodes(_scope_of(ctx, target))
    if _loads(nested, target.id):
        return ESCAPES, None  # read by a nested function or class
    return _combine([_use(ctx, load, loggers, depth + 1) for load in _loads(own, target.id)])


def _recorder(ctx, call, loggers):
    """Description if a call records whatever it is given (a logger, print, a usage/metric helper)."""
    func = call.func
    if id(call) in loggers or (isinstance(func, ast.Name) and func.id == "print"):
        return f"logs a whole LLM response ({ast.unparse(func)}())"
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    words = {w.lower() for w in re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", name) if w}
    if words & RECORDER_WORDS:
        return f"passes an LLM response to {ast.unparse(func)}()"
    return None


def _field(ctx, access, name, loggers, depth):
    """Fate of reading field/key `name` (the `access` node) from a response-like value."""
    parent = ctx.parent(access)
    if isinstance(access, ast.Attribute) and isinstance(parent, ast.Call) and parent.func is access:
        if name in CARRIER_METHODS:
            return _use(ctx, parent, loggers, depth + 1)
        if name == "get" and parent.args and isinstance(parent.args[0], ast.Constant):
            key = parent.args[0].value
            return _field(ctx, parent, key, loggers, depth) if isinstance(key, str) else (ESCAPES, None)
        if name == "keys":
            return DROPPED, None
        return ESCAPES, None  # model_dump(), to_dict(), json(), ...: the whole object, usage included
    if name in CARRIER_FIELDS:
        return _use(ctx, access, loggers, depth + 1)
    return DROPPED, None


def _use(ctx, node, loggers, depth=0):
    """(DROPPED or ESCAPES, recorder description or None) for one use of a response-like value."""
    if depth > MAX_DEPTH:
        return ESCAPES, None
    parent = ctx.parent(node)
    if isinstance(parent, ast.Await):
        node, parent = parent, ctx.parent(parent)
    if isinstance(parent, ast.Expr):
        return DROPPED, None
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            return _name_uses(ctx, targets[0], loggers, depth)
        return ESCAPES, None
    if isinstance(parent, ast.withitem) and parent.context_expr is node:
        if parent.optional_vars is None:
            return DROPPED, None
        if isinstance(parent.optional_vars, ast.Name):
            return _name_uses(ctx, parent.optional_vars, loggers, depth)
        return ESCAPES, None
    if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and parent.iter is node:
        if not isinstance(parent.target, ast.Name):
            return ESCAPES, None
        within = ctx.parent(parent) if isinstance(parent, ast.comprehension) else None
        return _name_uses(ctx, parent.target, loggers, depth, within)
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return _field(ctx, parent, parent.attr, loggers, depth)
    if isinstance(parent, ast.Subscript) and parent.value is node:
        key = parent.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return _field(ctx, parent, key.value, loggers, depth)
        if isinstance(key, ast.Constant) and isinstance(key.value, int):
            return _use(ctx, parent, loggers, depth + 1)
        return ESCAPES, None
    if isinstance(parent, (ast.Compare, ast.UnaryOp)):
        return DROPPED, None
    if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)) and parent.test is node:
        return DROPPED, None
    if isinstance(parent, ast.keyword):
        node, parent = parent, ctx.parent(parent)
    if isinstance(parent, ast.Call) and parent.func is not node:
        if (ctx.dotted(parent.func) or "") in JSON_LOADS:
            return _use(ctx, parent, loggers, depth + 1)
        if isinstance(parent.func, ast.Name) and parent.func.id in HARMLESS_CALLS:
            return DROPPED, None
        return ESCAPES, _recorder(ctx, parent, loggers)
    return ESCAPES, None  # returned, yielded, stored, formatted, ...


def response_fate(ctx, call_node, loggers):
    return _use(ctx, call_node, loggers)


# --- markers -------------------------------------------------------------------------------

def _imported_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node, node.module
            for alias in node.names:
                yield node, f"{node.module}.{alias.name}"


def _import_marker(name):
    if GENAI_INSTRUMENTATION.match(name):
        return f"imports {name} (GenAI OpenTelemetry instrumentation)"
    if name.split(".")[0] in OBSERVABILITY_ROOTS or name.startswith(OBSERVABILITY_PREFIXES):
        return f"imports {name} (LLM observability SDK)"
    return None


def _name_marker(name):
    if name in USAGE_NAMES:
        return f"reads token usage (.{name})"
    for label, pattern in NAME_MARKERS:
        if pattern.search(name):
            return f"{label} ({name})"
    return None


def _python_markers(ctx):
    """[(line, reason)] for token observability visible in one Python module."""
    found = []
    imports = set()
    for node, name in _imported_names(ctx.tree):
        imports.add(name.split(".")[0])
        reason = _import_marker(name)
        if reason:
            found.append((node.lineno, reason))
    for node in ast.walk(ctx.tree):
        reason = None
        if isinstance(node, ast.Attribute):
            reason = _name_marker(node.attr)
        elif isinstance(node, ast.Name):
            reason = _name_marker(node.id) if node.id not in USAGE_NAMES else None  # a local `usage` is not a read
        elif isinstance(node, ast.keyword) and node.arg == "requestMetadata":
            reason = "Bedrock requestMetadata (invocation-log attribution)"
        elif isinstance(node, ast.keyword) and node.arg in SENTRY_TRACING and "sentry_sdk" in imports:
            reason = f"Sentry tracing ({node.arg}=) records GenAI spans"
        elif isinstance(node, ast.Call):
            dotted = ctx.dotted(node.func) or ""
            if dotted.startswith("mlflow.") and dotted.endswith(".autolog"):
                reason = f"{dotted}() traces LLM calls"
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and not isinstance(ctx.parent(node), ast.Expr)):
            if node.value in USAGE_NAMES:
                reason = f"reads token usage ({node.value!r})"
            elif node.value == "requestMetadata":
                reason = "Bedrock requestMetadata (invocation-log attribution)"
            elif match := USAGE_TEXT.search(node.value):
                reason = f"token telemetry ({match.group(0)!r})"
        if reason:
            found.append((getattr(node, "lineno", 0), reason))
    return found


def _text_markers(ctx):
    found = []
    for number, line in enumerate(ctx.lines, 1):
        text = line.strip()
        if not text or text.startswith(("#", "//")):
            continue
        patterns = list(TEXT_MARKERS)
        if ctx.kind in ("manifest", "dockerfile"):
            patterns.append(("LLM observability SDK", PACKAGE_MARKER))
        for label, pattern in patterns:
            match = pattern.search(line)
            if match:
                found.append((number, f"{label} ({match.group(0)})"))
                break
    return found


def analyse(ctx):
    """(markers, [(LLMCall, fate)]) for one parsed Python module, computed once per Ctx."""
    cached = getattr(ctx, "llm09", None)
    if cached is not None:
        return cached
    loggers = {id(call.node) for call in log_calls(ctx)}
    calls, markers = [], _python_markers(ctx)
    for call in llm_calls(ctx):
        fate, recorder = response_fate(ctx, call.node, loggers)
        calls.append((call, fate))
        if recorder:
            markers.append((call.node.lineno, recorder))
    markers.sort(key=lambda item: item[0])
    ctx.llm09 = ([f"{reason}, line {line}" for line, reason in markers], calls)
    return ctx.llm09


def project_markers(payload, cache):
    """Collect markers from every parseable, non-vendored source of the requested scope."""
    markers, context_files, unreadable = [], 0, 0
    scope = payload.get("scope") if isinstance(payload, dict) else None
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(scope, list) or not isinstance(sources, list):
        return Project((), 0, 0)
    for source in sources:
        if not (isinstance(source, dict) and source.get("kind") == "static" and source.get("scope_id") in scope):
            continue
        locator, content = source.get("locator"), source.get("content")
        if not (isinstance(locator, str) and isinstance(content, str)) or file_kind(locator) is None:
            continue
        if is_vendored(locator):
            continue  # installed SDKs mention usage everywhere; they say nothing about the project
        ctx = cache(locator, content, swallow=True)
        if ctx is None:
            unreadable += 1
            continue
        if isinstance(ctx, TextCtx):
            context_files += 1
            reasons = [f"{reason}, line {line}" for line, reason in _text_markers(ctx)]
        else:
            reasons = analyse(ctx)[0]
        markers.extend((locator, reason) for reason in reasons)
    return Project(tuple(markers), context_files, unreadable)


# --- per-module rule -----------------------------------------------------------------------

def _summary(dropped):
    kinds = Counter(f"{PROVIDERS[call.provider]} {call.api}" for call in dropped)
    listed = ", ".join(f"{kind} x{count}" if count > 1 else kind for kind, count in sorted(kinds.items()))
    text = (
        f"{len(dropped)} LLM call(s) in this module ({listed}) discard the token usage each response returns, "
        "and nothing in the scanned code records it: no usage field is read, and no OpenTelemetry GenAI "
        "instrumentation (gen_ai.usage.*), LLM observability SDK or Bedrock model invocation logging is "
        "configured. Token spend from these calls cannot be attributed to this code path."
    )
    if any(call.provider == "bedrock" for call in dropped):
        text += " Bedrock's AWS/Bedrock InputTokenCount/OutputTokenCount metrics give per-model totals only."
    return text


def run(ctx, project=Project((), 0, 0)):
    if isinstance(ctx, TextCtx) or project.markers or exempt_reason(ctx.path) is not None:
        return []
    dropped, escapes = [], False
    for call, fate in analyse(ctx)[1]:
        line = ctx.lines[call.node.lineno - 1] if call.node.lineno <= len(ctx.lines) else ""
        if in_main_block(ctx, call.node) or static.is_noqa(NOQA, line):
            continue
        if fate == ESCAPES:
            escapes = True
        else:
            dropped.append(call)
    if not dropped or escapes:
        return []
    dropped.sort(key=lambda call: (call.node.lineno, call.node.col_offset))
    certain = project.context_files and not project.unreadable and all(c.evidence != "chain" for c in dropped)
    return [Hit(
        node=dropped[0].node,
        anchor="module:token-usage",
        summary=_summary(dropped),
        confidence="medium" if certain else "low",
    )]


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    parsed = {}

    def cached_parse(locator, content, swallow=False):
        key = (locator, content)
        if key not in parsed:
            try:
                parsed[key] = parse(locator, content)
            except (SyntaxError, ValueError) as error:
                parsed[key] = error
        value = parsed[key]
        if isinstance(value, Exception):
            if swallow:
                return None
            raise value
        return value

    project = project_markers(payload, cached_parse)
    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, SUPPORTED_FORMATS=SUPPORTED_FORMATS,
        REFERENCES=REFERENCES, RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION,
        parse=cached_parse, run=lambda ctx: module.run(ctx, project),
    )
    result = static.evaluate_static(payload, check)
    if project.markers and result["coverage"]["evaluated_scope"]:
        locator, reason = project.markers[0]
        more = len({loc for loc, _ in project.markers}) - 1
        others = f" (and {more} other file(s))" if more else ""
        result["coverage"]["limitations"].insert(-1, (
            f"Token observability is visible in {locator}{others}: {reason}; no module was flagged."
        ))
    return result
