"""LLM-16: no streaming in request handlers (full LLM response buffered in memory).

Detector semantics version 1.1.0. Two evidence modes, dispatched on source kind (like TST-12):

- static (primary, unchanged since 1.0.0): flags non-streaming LLM calls made while serving a request:
  inside a web route or AgentCore entrypoint declared in the file, or in a module-level function of the
  same file that such a handler calls directly. A call is flagged only when its output cap is absent or
  greater than `context.max_buffered_output_tokens`. Calls that already stream (`stream=True`,
  `messages.stream`, `converse_stream`, `invoke_model_with_response_stream`, ...), structured-output
  calls, forced tool calls, replies parsed as JSON in the same function and calls whose arguments
  cannot be resolved are not flagged. Covered: Anthropic `messages.create`, OpenAI chat completions /
  responses, Bedrock `converse`/`invoke_model`. Python only.
- artifact (optional runtime confirmation): `artifact` sources are allocation sites from a client-CI
  `memray stats --json` export (one normalized frame per source, see `memray_frames`). Next to the
  static source of a `file:<path>` scope, a frame on the line(s) of a static finding that allocated
  more than `context.max_buffered_response_bytes` confirms that finding. In a scope with artifact
  sources only, frames in LLM SDK / HTTP response read functions above that threshold are flagged.

A payload without artifact sources is evaluated exactly as in 1.0.0: runtime confirmation is then
unavailable, and a missing artifact never makes a static result clean or dirty.
"""
from __future__ import annotations

import ast
import copy
import re
import sys
from types import SimpleNamespace

from . import static
from .llm07 import LIMITS as OPENAI_LIMITS, MODEL_TOKEN_KEYS
from .llmcalls import call_keywords, dict_items, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-16"
DETECTOR_VERSION = "1.1.0"
NOQA = ("LLM-16", "LLM16")

SETTING_KEYS = ("max_buffered_output_tokens",)
# Reference values from "LLM-16 > Context settings" in detectors/owner-d/README.md; repository
# scans pass them unless a setting is supplied explicitly.
REFERENCE_SETTINGS = {
    "max_buffered_output_tokens": 256,  # README LLM-16: team judgment, about 1 KB of text
}

# Web frameworks (import roots) whose route decorators mark request-serving functions.
WEB_FRAMEWORKS = frozenset({
    "fastapi", "starlette", "flask", "quart", "sanic", "litestar", "aiohttp", "chalice", "ninja",
    "rest_framework", "bedrock_agentcore",
})
# `@app.post(...)`, `@router.get`, `@bp.route`, `@app.entrypoint` (AgentCore), `@api_view` (DRF), `@post` (Litestar)
ROUTE_DECORATORS = frozenset({
    "get", "post", "put", "patch", "delete", "route", "api_route", "websocket", "websocket_route",
    "entrypoint", "api_view",
})
# APIs that already stream, and the streaming alternative of each non-streaming API.
STREAMING_APIS = frozenset({
    "messages.stream", "beta.messages.stream", "chat.completions.stream", "beta.chat.completions.stream",
    "responses.stream", "converse_stream", "invoke_model_with_response_stream",
})
ALTERNATIVES = {
    "anthropic": "messages.stream() or stream=True",
    "openai": "stream=True",
    "converse": "converse_stream()",
    "invoke_model": "invoke_model_with_response_stream()",
}
# Structured outputs must be complete before they can be parsed, so streaming does not help.
# `response_model` is instructor's structured-output keyword on a patched client.
STRUCTURED_KEYS = ("response_format", "output_format", "outputConfig", "response_model")
FORMAT_CONTAINERS = ("output_config", "text")  # Anthropic output_config.format, OpenAI responses text.format
# A reply parsed as JSON after the call (other than invoke_model's raw `body.read()`) is also used whole.
JSON_PARSERS = frozenset({
    "json.loads", "orjson.loads", "ujson.loads", "ast.literal_eval", "json_repair.loads", "json_repair.repair_json",
})
JSON_PARSE_METHODS = frozenset({"model_validate_json", "parse_raw"})
FREE_TOOL_CHOICES = frozenset({"auto", "none"})

UNKNOWN = object()  # an output cap that is set but not statically known

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html",
    "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/response-streaming.html",
    "https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html",
    "https://platform.claude.com/docs/en/build-with-claude/streaming",
    "https://platform.claude.com/docs/en/api/errors#long-requests",
    "https://developers.openai.com/api/docs/guides/streaming-responses",
    "https://developers.openai.com/api/docs/guides/latency-optimization",
)
RECOMMENDATION = (
    "Stream user-facing responses: use client.messages.stream() (or stream=True) for Anthropic, stream=True for "
    "OpenAI, converse_stream / invoke_model_with_response_stream for Bedrock, and forward the chunks to the "
    "client (FastAPI StreamingResponse / server-sent events, Flask stream_with_context, or an async generator "
    "AgentCore entrypoint that yields events) instead of returning the whole reply at once."
)
LIMITATION = (
    "Static proxy only: LLM-16 proves that a request handler (or a same-file function it calls directly) makes "
    "a non-streaming LLM call whose output cap is absent or above the configured limit, not how long the "
    "responses are, the peak memory they use or the user-visible latency; no memory profile is measured. "
    "Covered: Anthropic SDK messages.create, OpenAI chat completions/responses and Bedrock converse/"
    "invoke_model (messages bodies) in Python handlers of FastAPI, Starlette, Flask, Quart, Sanic, Litestar, "
    "aiohttp, Chalice, Django Ninja, DRF and AgentCore declared in the same file. Not flagged: calls outside "
    "such handlers (batch jobs, scripts, workers, Lambda handlers), handlers whose app or router is imported "
    "from another module, helpers reached through more than one call or through other modules, structured-"
    "output and forced tool calls, replies parsed as JSON in the same function, and calls whose stream, cap "
    "or request arguments are not statically known."
)


def _framework(ctx, node):
    return (ctx.dotted(node) or "").split(".")[0] in WEB_FRAMEWORKS


def _framework_objects(ctx):
    """Names bound in this file to a web framework object (`app = FastAPI()`, `bp = Blueprint(...)`)."""
    names = set()
    for node in ast.walk(ctx.tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
            if _framework(ctx, node.value.func):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _is_route(ctx, decorator, objects):
    func = decorator.func if isinstance(decorator, ast.Call) else decorator
    dotted = ctx.dotted(func) or ""
    if dotted.rsplit(".", 1)[-1] not in ROUTE_DECORATORS:
        return False
    if isinstance(func, ast.Attribute):
        return isinstance(func.value, ast.Name) and func.value.id in objects
    return _framework(ctx, func)  # bare imported decorator: `@post(...)`, `@api_view([...])`


def serving_functions(ctx):
    """{function id: (function, handler)}: route handlers, and module-level functions they call directly."""
    objects = _framework_objects(ctx)
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    handlers = [
        node for node in ast.walk(ctx.tree)
        if isinstance(node, functions) and any(_is_route(ctx, d, objects) for d in node.decorator_list)
    ]
    serving = {id(handler): (handler, handler) for handler in handlers}
    module_functions = {node.name: node for node in ctx.tree.body if isinstance(node, functions)}
    for handler in handlers:
        for node in ast.walk(handler):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in module_functions:
                helper = module_functions[node.func.id]
                serving.setdefault(id(helper), (helper, handler))
    return serving


def _constant(ctx, node):
    value = resolve(ctx, node)
    return value.value if isinstance(value, ast.Constant) else UNKNOWN


def _streams(ctx, call, keywords):
    """True if the call streams or may stream (`stream` not statically False/None)."""
    if call.api in STREAMING_APIS:
        return True
    node = call.node.func
    while isinstance(node, (ast.Attribute, ast.Call)):  # `with_streaming_response` is stripped by llmcalls
        if isinstance(node, ast.Attribute) and node.attr == "with_streaming_response":
            return True
        node = node.value if isinstance(node, ast.Attribute) else node.func
    return "stream" in keywords and _constant(ctx, keywords["stream"]) not in (False, None)


def _free_tool_choice(ctx, node):
    """True if a tool_choice leaves the model free to answer in text (`auto`/`none`)."""
    value = _constant(ctx, node)
    if isinstance(value, str):
        return value in FREE_TOOL_CHOICES
    fields = dict_items(ctx, node)
    if fields is None:
        return False
    if "type" in fields:  # Anthropic / OpenAI {"type": "auto"}
        return _constant(ctx, fields["type"]) in FREE_TOOL_CHOICES
    return set(fields) == {"auto"}  # Bedrock {"auto": {}}


def _needs_whole_reply(ctx, request):
    """True for structured outputs and forced tool calls (or when that cannot be ruled out)."""
    if any(key in request for key in STRUCTURED_KEYS):
        return True
    for key in FORMAT_CONTAINERS:
        if key in request:
            fields = dict_items(ctx, request[key])
            if fields is None or "format" in fields:
                return True
    if "tool_choice" in request and not _free_tool_choice(ctx, request["tool_choice"]):
        return True
    if "toolConfig" in request:
        config = dict_items(ctx, request["toolConfig"])
        if config is None or ("toolChoice" in config and not _free_tool_choice(ctx, config["toolChoice"])):
            return True
    return False


def _parses_reply(ctx, call):
    """True if the enclosing function parses text as JSON after the call (other than a raw response body)."""
    scope = next((a for a in ctx.ancestors(call.node) if isinstance(a, static.FUNC_NODES)), ctx.tree)
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or node.lineno < call.node.lineno:
            continue
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if (ctx.dotted(node.func) or "") in JSON_PARSERS or method in JSON_PARSE_METHODS:
            if not any(".read()" in ast.unparse(arg) for arg in node.args):
                return True
    return False


def _cap(ctx, mapping, keys):
    """Output cap set in `mapping` under one of `keys`: an int, None if absent, or UNKNOWN."""
    for key in sorted(keys):
        if key in mapping:
            value = _constant(ctx, mapping[key])
            if value is None:
                continue
            return value if isinstance(value, int) and not isinstance(value, bool) else UNKNOWN
    return None


def _nested_cap(ctx, mapping, name, keys):
    if name not in mapping:
        return None
    fields = dict_items(ctx, mapping[name])
    return UNKNOWN if fields is None else _cap(ctx, fields, keys)


def _request(ctx, call, keywords):
    """(request fields, output cap) of a non-streaming call, or None if LLM-16 cannot judge it."""
    if call.provider == "anthropic":
        if call.api not in ("messages.create", "beta.messages.create"):
            return None  # `.parse` returns a structured output
        return keywords, (_cap(ctx, keywords, ("max_tokens",)) if "max_tokens" in keywords else UNKNOWN)
    if call.provider == "openai":
        names = OPENAI_LIMITS.get((call.provider, call.api))
        if names is None or call.api.endswith("parse"):
            return None  # legacy completions (16-token default) and structured `.parse` calls
        return keywords, _cap(ctx, keywords, names)
    if "promptVariables" in keywords:
        return None  # Bedrock Prompt management carries its own configuration
    if call.api == "converse":
        cap = _nested_cap(ctx, keywords, "inferenceConfig", ("maxTokens",))
        if cap is None:
            cap = _nested_cap(ctx, keywords, "additionalModelRequestFields", MODEL_TOKEN_KEYS)
        return keywords, cap
    body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
    if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
        return None
    request = dict_items(ctx, body.args[0])
    if request is None or "messages" not in request:
        return None  # embeddings, images and prompt-style bodies are not judged
    cap = _cap(ctx, request, MODEL_TOKEN_KEYS)
    if cap is None:
        cap = _nested_cap(ctx, request, "inferenceConfig", MODEL_TOKEN_KEYS)
    return request, cap


def buffered_call(ctx, call):
    """Output cap (int or None) of a non-streaming call whose whole reply is buffered, else UNKNOWN."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None or "extra_body" in keywords or _streams(ctx, call, keywords):
        return UNKNOWN
    judged = _request(ctx, call, keywords)
    if judged is None:
        return UNKNOWN
    request, cap = judged
    if _needs_whole_reply(ctx, request) or _parses_reply(ctx, call):
        return UNKNOWN
    return cap


def _read_settings(context, keys=SETTING_KEYS):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in keys if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    for key in keys:
        value = context[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None, f"context.{key} must be a positive integer"
    return {key: context[key] for key in keys}, None


def _summary(call, cap, function, handler):
    target = f"{call.receiver}.{call.api}()"
    provider = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}[call.provider]
    if call.evidence == "chain":
        provider = "OpenAI-compatible"
    where = f"request handler {handler.name}()"
    if function is not handler:
        where = f"{function.name}(), which {where} calls,"
    size = "with no output-token cap" if cap is None else f"of up to {cap:,} output tokens"
    alternative = ALTERNATIVES[call.api if call.provider == "bedrock" else call.provider]
    return (
        f"{provider} {target} in {where} does not stream: it waits for the complete response {size} and holds "
        f"it in memory before anything reaches the client; the streaming form is {alternative}."
    )


def run(ctx, settings):
    if settings is None:
        return []
    serving = serving_functions(ctx)
    if not serving:
        return []
    hits = []
    for call in llm_calls(ctx):
        enclosing = next((serving[id(a)] for a in ctx.ancestors(call.node) if id(a) in serving), None)
        if enclosing is None:
            continue
        cap = buffered_call(ctx, call)
        if cap is UNKNOWN or (cap is not None and cap <= settings["max_buffered_output_tokens"]):
            continue
        function, handler = enclosing
        direct = function is handler and call.evidence != "chain"
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, cap, function, handler),
            confidence="medium" if direct else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if isinstance(sources, list) and any(isinstance(s, dict) and s.get("kind") == ARTIFACT_KIND for s in sources):
        return _evaluate_with_artifacts(payload)
    return _evaluate_static(payload)


def _evaluate_static(payload):
    """Static mode (detector semantics 1.0.0, unchanged)."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, run=lambda ctx: module.run(ctx, settings),
    )
    result = static.evaluate_static(payload, check)
    if settings is None:
        result.update(
            status="unavailable",
            coverage={"evaluated_scope": [], "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION]},
            findings=[],
            measurements=[],
        )
    return result


# --------------------------------------------------------------------------- artifact mode (1.1.0)

STATIC_KIND = "static"
ARTIFACT_KIND = "artifact"
PROFILER = "memray"
ARTIFACT_SETTING_KEYS = ("max_buffered_response_bytes",)
# Reference value from "LLM-16 > Context settings (artifact mode, optional)" in detectors/owner-d/README.md.
# Kept out of REFERENCE_SETTINGS so repository scans (static only) send exactly the 1.0.0 context.
ARTIFACT_REFERENCE_SETTINGS = {
    "max_buffered_response_bytes": 1_048_576,  # README LLM-16: team judgment, 1 MiB over the profiled run
}
MAX_FRAMES = 2000
MEMRAY_REFERENCE = "https://bloomberg.github.io/memray/stats.html"
_LOCATION = re.compile(r"^(?P<function>[^:]*):(?P<file>.+):(?P<line>\d+)$")  # memray `func:file:line`
_INSTALLED = re.compile(r"(?:^|/)(?:site|dist)-packages/")

# Functions that read a whole HTTP response body into memory, by installed path (below site-packages).
# LLM SDK frames are specific to LLM replies; transport frames read any response body, so they are `low`.
LLM_SDK_READS = frozenset({"read", "aread", "json", "text", "content", "parse", "_parse", "_process_response",
                           "_process_response_data"})
RESPONSE_READERS = (
    ("anthropic/", "medium", LLM_SDK_READS, "Anthropic SDK"),
    ("openai/", "medium", LLM_SDK_READS, "OpenAI SDK"),
    ("httpx/_models.py", "low", frozenset({"read", "aread", "json", "text", "content"}),
     "httpx (the Anthropic and OpenAI SDK transport)"),
    ("botocore/response.py", "low", frozenset({"read"}), "botocore StreamingBody (Bedrock invoke_model body)"),
)
ARTIFACT_LIMITATION = (
    "LLM-16 runtime confirmation reads a client-CI `memray stats --json` export. It lists only the top "
    "allocation sites, attributes each allocation to the innermost Python frame and sums bytes over the whole "
    "profiled run (not per request, not at the high-water mark; metadata.peak_memory is the run's peak). A static "
    "finding is confirmed only when memray attributes more than max_buffered_response_bytes to its call line(s); "
    "an unconfirmed static finding is not cleared. Frames in LLM SDK or HTTP response read functions carry no "
    "caller, so they are reported in the artifact scope without a repository file:line; other frames are not "
    "judged without their source file."
)


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _bytes(value):
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value} B"


def memray_frames(stats):
    """Normalize a `memray stats --json` export into LLM-16 frame records: (frames, notes).

    One record per `top_allocations_by_size` entry:
    {"profiler": "memray", "location": "chat:app/api.py:24", "function": "chat", "file": "app/api.py",
     "line": 24, "allocated_bytes": 52428800, "peak_memory": 83886080 (metadata.peak_memory, if present)}.
    Raises ValueError when `stats` is not such an export.
    """
    if not isinstance(stats, dict) or not isinstance(stats.get("top_allocations_by_size"), list):
        raise ValueError("not a `memray stats --json` export (needs a top_allocations_by_size list)")
    metadata = stats.get("metadata")
    peak = metadata.get("peak_memory") if isinstance(metadata, dict) else None
    frames, notes = [], []
    for index, entry in enumerate(stats["top_allocations_by_size"]):
        location = entry.get("location") if isinstance(entry, dict) else None
        size = entry.get("size") if isinstance(entry, dict) else None
        match = _LOCATION.match(location) if isinstance(location, str) else None
        if match is None or not _is_count(size):
            notes.append(f"top_allocations_by_size[{index}] has no usable location ('<function>:<file>:<line>') "
                         "and byte size; skipped")
            continue
        frame = {"profiler": PROFILER, "location": location, "function": match["function"], "file": match["file"],
                 "line": int(match["line"]), "allocated_bytes": size}
        if _is_count(peak):
            frame["peak_memory"] = peak
        frames.append(frame)
    return frames, notes


def memray_inputs(data, name, run):
    """Artifact-parser inputs for one uploaded `memray stats --json` export: (context, scope, sources, notes).

    The upload may carry an optional `"settings": {"max_buffered_response_bytes": ...}` next to memray's keys.
    Every allocation site becomes one artifact source in the single `artifact:<name>` scope. Raises ValueError
    when the upload cannot be used.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{name} needs a `memray stats --json` object")
    overrides = data.get("settings") or {}
    if not isinstance(overrides, dict) or set(overrides) - set(ARTIFACT_SETTING_KEYS):
        raise ValueError(f"settings may only contain {', '.join(ARTIFACT_SETTING_KEYS)}")
    frames, notes = memray_frames(data)
    if len(frames) > MAX_FRAMES:
        notes.append(f"only the first {MAX_FRAMES} of {len(frames)} allocation sites in {name} were evaluated")
        frames = frames[:MAX_FRAMES]
    if not frames:
        raise ValueError("no usable allocation sites in top_allocations_by_size" + "".join(f"; {n}" for n in notes[:5]))
    scope_id = f"artifact:{name}"
    sources = [{"source_id": f"memray-{index}", "scope_id": scope_id, "kind": ARTIFACT_KIND,
                "locator": f"{name} from GitHub Actions run {run}: {frame['location']}", "data": frame}
               for index, frame in enumerate(frames)]
    return dict(ARTIFACT_REFERENCE_SETTINGS) | overrides, [scope_id], sources, notes


def _frame_problem(data):
    if not isinstance(data, dict):
        return "artifact data must be an object"
    if data.get("profiler") != PROFILER:
        return f"profiler must be {PROFILER!r}"
    for field in ("location", "file"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            return f"{field} must be a nonempty string"
    if not isinstance(data.get("function"), str):
        return "function must be a string"
    for field in ("line", "allocated_bytes") + (("peak_memory",) if "peak_memory" in data else ()):
        if not _is_count(data.get(field)):
            return f"{field} must be a nonnegative integer"
    return None


def _same_file(frame_file, path):
    """A path recorded on the CI runner matches a repository-relative path (as owner C's memray parser)."""
    frame_file, path = frame_file.replace("\\", "/"), path.replace("\\", "/")
    return frame_file == path or frame_file.endswith("/" + path)


def _artifact_evidence(source):
    return [{"source_id": source["source_id"], "kind": ARTIFACT_KIND, "locator": source["locator"],
             "field": field, "value": source["data"][field]}
            for field in ("location", "allocated_bytes", "peak_memory") if field in source["data"]]


def _valid_frames(scope_id, artifacts):
    frames, notes = [], []
    for source in artifacts:
        problem = _frame_problem(source.get("data"))
        if problem:
            notes.append(f"{scope_id}: artifact {source.get('source_id')!r} is not a usable memray frame ({problem}); "
                         "ignored")
        else:
            frames.append(source)
    return frames, notes


def _confirm_scope(scope_id, findings, artifacts, path, limit):
    """Add memray confirmation to the static findings of one file scope in place; return notes."""
    frames, notes = _valid_frames(scope_id, artifacts)
    inside = [f for f in frames if _same_file(f["data"]["file"], path)]
    if len(inside) < len(frames):
        notes.append(f"{scope_id}: {len(frames) - len(inside)} memray frame(s) are not in {path}; ignored")
    confirmed = 0
    for finding in findings:
        static_evidence = finding["evidence"][0]
        start = static_evidence["line_start"]
        end = start + len(static_evidence["value"].splitlines()) - 1
        matches = [f for f in inside if start <= f["data"]["line"] <= end and f["data"]["allocated_bytes"] > limit]
        if not matches:
            continue
        source = max(matches, key=lambda f: f["data"]["allocated_bytes"])
        data = source["data"]
        finding["evidence"] = finding["evidence"] + _artifact_evidence(source)
        finding["confidence"] = {"low": "medium", "medium": "high"}.get(finding["confidence"], finding["confidence"])
        finding["references"] = finding["references"] + [MEMRAY_REFERENCE]
        finding["summary"] += (f" memray confirms it at runtime: {data['location']} allocated "
                               f"{_bytes(data['allocated_bytes'])}, more than {limit:,} bytes.")
        confirmed += 1
    if findings:
        notes.append(f"{scope_id}: memray confirmed {confirmed} of {len(findings)} static finding(s); unconfirmed "
                     "findings stay as static evidence")
    return notes


def _response_reader(data):
    """(installed path, confidence, label) when the frame is an LLM SDK / HTTP response read function."""
    file = data["file"].replace("\\", "/")
    found = list(_INSTALLED.finditer(file))
    if not found:
        return None
    installed = file[found[-1].end():]
    function = data["function"].rsplit(".", 1)[-1]
    for prefix, confidence, functions, label in RESPONSE_READERS:
        matches = installed.startswith(prefix) if prefix.endswith("/") else installed == prefix
        if matches and function in functions:
            return installed, confidence, label
    return None


def _artifact_items(scope_id, artifacts, limit):
    """(items, or None when nothing could be evaluated; notes) for a scope with artifact sources only."""
    frames, notes = _valid_frames(scope_id, artifacts)
    if not frames:
        return None, notes + [f"{scope_id}: no usable memray allocation frames; runtime evidence unavailable"]
    items, unjudged = [], 0
    for source in frames:
        data = source["data"]
        reader = _response_reader(data)
        if reader is None:
            unjudged += 1
            continue
        if data["allocated_bytes"] <= limit:
            continue
        installed, confidence, label = reader
        function = data["function"].rsplit(".", 1)[-1]
        peak = f" (run peak {_bytes(data['peak_memory'])})" if "peak_memory" in data else ""
        caveat = "; it may be any HTTP response, not only an LLM reply" if confidence == "low" else ""
        items.append({
            "anchor": f"response-read:{installed}:{function}",
            "summary": (f"{label} {function}() at {data['location']} allocated {_bytes(data['allocated_bytes'])}"
                        f"{peak}, more than {limit:,} bytes, reading whole response bodies into memory instead of "
                        f"streaming them{caveat}."),
            "confidence": confidence,
            "evidence": _artifact_evidence(source),
        })
    if unjudged:
        notes.append(f"{scope_id}: {unjudged} memray frame(s) outside LLM SDK / HTTP response read functions were not "
                     "judged; without the source file a call site cannot be recognized as an LLM call")
    return static._unique_identities(items), notes


def _check_header(payload):
    """The same input requirements as static mode (static.evaluate_static)."""
    require = static._require
    require(isinstance(payload, dict), "input payload must be an object")
    require(payload.get("kind") == "input", "expected a contract input payload")
    require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    require(payload.get("schema_version") == static.SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    require(payload.get("detector_version") == DETECTOR_VERSION,
            f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements {DETECTOR_VERSION}")
    for field in static.IDENTITY_FIELDS:
        require(field in payload, f"input is missing required field {field}")
    require(isinstance(payload["scope"], list) and bool(payload["scope"]), "scope must be a nonempty list")
    require(isinstance(payload.get("sources"), list), "sources must be a list")
    require(isinstance(payload["context"], dict), "context must be an object")


def _evaluate_with_artifacts(payload):
    """Static mode for scopes with a static source (plus memray confirmation), artifact mode for the rest."""
    _check_header(payload)
    scope = payload["scope"]
    sources = [s for s in payload["sources"] if isinstance(s, dict)]
    statics = {sid: [s for s in sources if s.get("scope_id") == sid and s.get("kind") == STATIC_KIND] for sid in scope}
    artifacts = {sid: [s for s in sources if s.get("scope_id") == sid and s.get("kind") == ARTIFACT_KIND] for sid in scope}
    static_scope = [sid for sid in scope if statics[sid] or not artifacts[sid]]

    static_result = None
    if static_scope:
        static_result = _evaluate_static({**payload, "scope": static_scope,
                                          "sources": [s for s in sources if s.get("kind") != ARTIFACT_KIND]})
    static_evaluated = set(static_result["coverage"]["evaluated_scope"]) if static_result else set()
    settings, reason = _read_settings(payload["context"], ARTIFACT_SETTING_KEYS)
    limit = settings["max_buffered_response_bytes"] if settings else None

    evaluated, findings, notes = [], [], []
    for sid in scope:
        if sid in static_scope:
            if sid not in static_evaluated:
                continue  # static mode omitted it and explains why
            evaluated.append(sid)
            scope_findings = [copy.deepcopy(f) for f in static_result["findings"] if f["scope_id"] == sid]
            if artifacts[sid] and settings is None:
                notes.append(f"{sid}: runtime confirmation unavailable: missing or invalid context settings for "
                             f"artifact mode: {reason}")
            elif artifacts[sid]:
                notes.extend(_confirm_scope(sid, scope_findings, artifacts[sid], statics[sid][0]["locator"], limit))
            findings.extend(scope_findings)
            continue
        if settings is None:
            notes.append(f"{sid}: Missing or invalid context settings: {reason}")
            continue
        items, scope_notes = _artifact_items(sid, artifacts[sid], limit)
        notes.extend(scope_notes)
        if items is None:
            continue
        evaluated.append(sid)
        findings.extend({
            "fingerprint": fingerprint(payload["repository_id"], CHECK_ID, sid, item["identity"]),
            "scope_id": sid,
            "identity": item["identity"],
            "summary": item["summary"],
            "confidence": item["confidence"],
            "recommendation": RECOMMENDATION,
            "references": list(REFERENCES) + [MEMRAY_REFERENCE],
            "evidence": item["evidence"],
        } for item in items)

    limitations = (static_result["coverage"]["limitations"] if static_result else []) + notes + [ARTIFACT_LIMITATION]
    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"
    result = {field: payload[field] for field in static.IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result
