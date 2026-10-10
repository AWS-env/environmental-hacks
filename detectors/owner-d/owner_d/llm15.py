"""LLM-15: tool-definition sprawl (large tool registries sent with every call), static proxy.

Detector semantics version 1.0.0. Flags LLM calls whose statically resolvable tool list loads more
tools up front than `context.max_tools_per_call`, or whose fully static tool definitions are
estimated (4 characters per token) above `context.max_tool_definition_tokens`. Tools deferred with
`defer_loading`, tool-search tools and Bedrock `cachePoint` entries do not count. Covered: Anthropic
`messages.*`, OpenAI chat completions / responses, Bedrock `converse`/`converse_stream`/
`invoke_model`. Unknown tool lists are not flagged. Python only; static only.
"""

from __future__ import annotations

import ast
import re
import sys
from types import SimpleNamespace

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve, static_elements, static_size
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-15"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-15", "LLM15")
CHARS_PER_TOKEN = 4  # undercounts tokens (about 3.5 characters per token for Claude, less for JSON)

SETTING_KEYS = ("max_tools_per_call", "max_tool_definition_tokens")
# Reference values from "LLM-15 > Context settings" in detectors/owner-d/README.md; repository
# scans pass them unless a setting is supplied explicitly.
REFERENCE_SETTINGS = {
    "max_tools_per_call": 20,  # README LLM-15: OpenAI's "fewer than 20 functions" soft limit
    "max_tool_definition_tokens": 10000,  # README LLM-15: Anthropic's 10k-token tool-search guidance
}
# A file that defers tool loading anywhere is treated as already managing its tool context.
DEFERRAL_MARKERS = re.compile(r"defer_loading|tool_search|toolSearch|allowed_tools", re.I)
ITERATION_METHODS = frozenset({"keys", "values", "items"})

REFERENCES = (
    "https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool",
    "https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview",
    "https://developers.openai.com/api/docs/guides/function-calling",
    "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.html",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
RECOMMENDATION = (
    "Send only the tools the step needs: split the registry per task or agent, or load tools on demand "
    "(Anthropic/OpenAI tool search with defer_loading, AgentCore Gateway semantic tool search). Keep the "
    "3-5 most used tools loaded, shorten descriptions, and cache the remaining stable tool prefix."
)
LIMITATION = (
    "Static proxy only: LLM-15 proves that a call sends a statically known number (or estimated size) of "
    "tool definitions above the configured thresholds, not that they degrade accuracy or cost a measured "
    "amount; no token counts are measured. Covered: Anthropic SDK messages, OpenAI chat completions/"
    "responses (tools, legacy functions) and Bedrock converse/converse_stream/invoke_model tool lists in "
    "Python. Not flagged: tool lists that are parameters, built by calls or loaded from MCP servers at "
    "runtime; files that use defer_loading, tool search or allowed_tools; **kwargs. MCP toolsets and "
    "server-side connectors count as one entry each, so counts are lower bounds."
)


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _iteration_count(ctx, node):
    """Number of items a comprehension iterates over, if statically known."""
    node = resolve(ctx, node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and not node.args:
        if node.func.attr in ITERATION_METHODS:
            items = dict_items(ctx, node.func.value)
            return None if items is None else len(items)
    items = dict_items(ctx, node)
    if items is not None:
        return len(items)
    elements, complete = static_elements(ctx, node)
    return len(elements) if elements is not None and complete else None


def tool_entries(ctx, node, depth=0):
    """(entry nodes or None, count, complete) of a tools list; the count is a lower bound if incomplete."""
    node = resolve(ctx, node)
    if node is None or depth > 8:
        return None, 0, False
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
        [generator] = node.generators if len(node.generators) == 1 else [None]
        if generator is None or generator.ifs or generator.is_async:
            return None, 0, False
        count = _iteration_count(ctx, generator.iter)
        return (None, count, True) if count is not None else (None, 0, False)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("list", "tuple"):
        if len(node.args) == 1 and not node.keywords:
            return tool_entries(ctx, node.args[0], depth + 1)
        return None, 0, False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values":
        items = dict_items(ctx, node.func.value)
        return (list(items.values()), len(items), True) if items is not None else (None, 0, False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [tool_entries(ctx, node.left, depth + 1), tool_entries(ctx, node.right, depth + 1)]
    elif isinstance(node, (ast.List, ast.Tuple)):
        parts = [
            tool_entries(ctx, element.value, depth + 1) if isinstance(element, ast.Starred) else ([element], 1, True)
            for element in node.elts
        ]
    else:
        return None, 0, False
    entries, count = [], 0
    for part_entries, part_count, part_complete in parts:
        count += part_count
        entries = None if entries is None or part_entries is None else entries + part_entries
        if not part_complete:
            return entries, count, False
    return entries, count, True


def _loaded(ctx, entry):
    """False for entries that do not load a tool definition up front."""
    fields = dict_items(ctx, entry)
    if fields is None:
        return True
    if "cachePoint" in fields or _is_true(fields.get("defer_loading")):
        return False
    kind = resolve(ctx, fields["type"]) if "type" in fields else None
    return not (isinstance(kind, ast.Constant) and isinstance(kind.value, str) and kind.value.startswith("tool_search"))


def _tools_node(ctx, call, keywords):
    """The tools argument of a call, False if it has none, or None if the request is unknown."""
    if call.provider in ("anthropic", "openai"):
        if "extra_body" in keywords:
            return None
        return keywords.get("tools", keywords.get("functions", False))
    if call.api.startswith("converse"):
        request = keywords
    else:
        body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
        if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
            return None
        request = dict_items(ctx, body.args[0])
        if request is None:
            return None
    if "tools" in request:
        return request["tools"]
    if "toolConfig" not in request:
        return False
    config = dict_items(ctx, request["toolConfig"])
    return None if config is None else config.get("tools", False)


def tool_load(ctx, call):
    """(tools loaded up front, estimated tokens or None, tools node) for a call, or None if unknown."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None:
        return None
    node = _tools_node(ctx, call, keywords)
    if node is None or node is False:
        return None
    entries, count, complete = tool_entries(ctx, node)
    if entries is None:
        return (count, None, resolve(ctx, node)) if count else None
    loaded = [entry for entry in entries if _loaded(ctx, entry)]
    sizes = [static_size(ctx, entry) for entry in loaded]
    tokens = None if None in sizes or not complete else sum(sizes) // CHARS_PER_TOKEN
    return count - (len(entries) - len(loaded)), tokens, resolve(ctx, node)


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    for key in SETTING_KEYS:
        value = context[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None, f"context.{key} must be a positive integer"
    return {key: context[key] for key in SETTING_KEYS}, None


def _summary(call, count, tokens, settings, shared):
    target = f"{call.receiver}.{call.api}()"
    reasons = []
    if count > settings["max_tools_per_call"]:
        reasons.append(f"{count} tool definitions (more than {settings['max_tools_per_call']})")
    if tokens is not None and tokens > settings["max_tool_definition_tokens"]:
        reasons.append(
            f"about {tokens:,} tokens of tool definitions (estimated; more than "
            f"{settings['max_tool_definition_tokens']:,})"
        )
    provider = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}[call.provider]
    if call.evidence == "chain":
        provider = "OpenAI-compatible"
    text = f"{provider} {target} loads {' and '.join(reasons)} into the context of every call, before the task begins"
    if shared > 1:
        text += f"; the same registry is passed to {shared} calls in this file"
    return text + "."


def run(ctx, settings):
    if settings is None or DEFERRAL_MARKERS.search("\n".join(ctx.lines)):
        return []
    loads = []
    for call in llm_calls(ctx):
        load = tool_load(ctx, call)
        if load is not None:
            loads.append((call, load))
    registries = {}
    for _, (_, _, node) in loads:
        registries[id(node)] = registries.get(id(node), 0) + 1
    hits = []
    for call, (count, tokens, node) in loads:
        too_many = count > settings["max_tools_per_call"]
        too_large = tokens is not None and tokens > settings["max_tool_definition_tokens"]
        if not (too_many or too_large):
            continue
        shared = registries[id(node)] if isinstance(node, (ast.List, ast.Tuple, ast.ListComp)) else 1
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, count, tokens, settings, shared),
            confidence="low" if call.evidence == "chain" else "medium",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
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
