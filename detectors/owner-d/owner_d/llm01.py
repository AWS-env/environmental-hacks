"""LLM-01: a large, stable prompt prefix sent without prompt caching (static proxy).

Detector semantics version 1.0.0. Flags Anthropic `messages.create/stream/parse` calls and Bedrock
`converse`/`converse_stream`/`invoke_model` calls whose prefix (tools -> system -> leading messages,
the providers' cache order) is static in the file and, estimated at 4 characters per token, reaches
the model's minimum cacheable length, while the file sets no cache marker (`cache_control`,
`cachePoint`). Anything that cannot be resolved statically is not flagged. OpenAI is not flagged:
its prompt caching is automatic. Python only; static only.
"""

from __future__ import annotations

import ast
import re
import sys

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve, static_elements, static_size, static_text
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-01"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-01", "LLM01")

# Conservative estimate: Anthropic documents ~3.5 English characters per token for older Claude
# models and ~30% more tokens for Claude 4.7+, so 4 characters per token underestimates the prefix.
CHARS_PER_TOKEN = 4
# Model id unknown (e.g. a parameter) on the Anthropic SDK: use the largest documented minimum.
UNKNOWN_MODEL_MINIMUM = 4096

# Any of these anywhere in the file means caching is (or may be) configured for some call.
CACHE_MARKERS = re.compile(r"cache_control|cachePoint|cache_point|prompt[-_]caching", re.I)

ANTHROPIC_APIS = frozenset({
    "messages.create", "messages.stream", "messages.parse",
    "beta.messages.create", "beta.messages.stream", "beta.messages.parse",
})
BEDROCK_APIS = frozenset({"converse", "converse_stream", "invoke_model", "invoke_model_with_response_stream"})

# Minimum cacheable prefix (tokens) per Claude model: the larger of the Anthropic API and Bedrock
# figures, and whether the Bedrock explicit-caching table lists the model.
CLAUDE_MINIMUMS = {
    ("haiku", "5.5"): (512, True), ("sonnet", "5.5"): (512, True), ("opus", "5.5"): (512, True),
    ("fable", "5.1"): (512, True), ("mythos", "5.1"): (512, True), ("fable", "5"): (512, True),
    ("mythos", "5"): (512, True), ("opus", "5"): (512, True), ("sonnet", "5"): (1024, True),
    ("opus", "4.8"): (1024, True), ("opus", "4.7"): (4096, True), ("opus", "4.6"): (4096, True),
    ("opus", "4.5"): (4096, True), ("sonnet", "4.6"): (1024, True), ("sonnet", "4.5"): (1024, True),
    ("haiku", "4.5"): (4096, True), ("opus", "4.1"): (1024, False), ("opus", "4"): (1024, False),
    ("sonnet", "4"): (1024, False), ("sonnet", "3.7"): (1024, True), ("haiku", "3.5"): (2048, False),
    ("mythos", "preview"): (2048, False),
}
NOVA_MINIMUM = 1024  # Nova Micro/Lite/Pro/2 Lite: "1K" per checkpoint; tools do not take checkpoints
NOVA_MODELS = re.compile(r"amazon\.nova-(?:micro|lite|pro|2-lite)-v1")
CLAUDE_NEW = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(preview|\d+)(?:-(\d)(?!\d))?")
CLAUDE_OLD = re.compile(r"claude-(\d)-(\d)-(sonnet|haiku|opus)")

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html",
    "https://platform.claude.com/docs/en/build-with-claude/prompt-caching",
    "https://platform.claude.com/docs/en/about-claude/glossary",
    "https://developers.openai.com/api/docs/guides/prompt-caching",
)
RECOMMENDATION = (
    "Cache the stable prefix: for the Anthropic SDK add cache_control={\"type\": \"ephemeral\"} at the top "
    "level (automatic caching) or on the last tool/system block; for Bedrock Converse append "
    "{\"cachePoint\": {\"type\": \"default\"}} after the static tools/system content (cache_control blocks "
    "in InvokeModel Claude bodies). Keep dynamic content (dates, user data) after the cached prefix and "
    "check cache_read_input_tokens / cacheReadInputTokens in responses."
)
LIMITATION = (
    "Static proxy only: LLM-01 proves that a call resends a statically large prompt prefix (estimated at "
    "4 characters per token against the model's documented minimum cacheable length) with no cache marker "
    "in the file, not that calls repeat within the cache TTL, that the cache would be hit, or any cost; "
    "usage logs are needed for that and no token counts are measured. Covered: Anthropic SDK messages "
    "calls and Bedrock converse/converse_stream/invoke_model (Claude and Nova models) in Python. Not "
    "flagged: OpenAI (prompt caching is automatic), Bedrock models without explicit caching or with an "
    "unresolvable modelId, prompts loaded from files or other modules, tools/system/request values that are "
    "not statically resolvable, files that mention cache_control/cachePoint anywhere, and module-level "
    "one-shot calls. Cache markers added to messages in another module are not seen."
)


def claude_minimum(model, bedrock):
    """Minimum cacheable tokens for a Claude model id, or None if not known to support caching."""
    model = model.lower()
    match = CLAUDE_NEW.search(model)
    if match:
        family, major, minor = match.groups()
        version = major if minor in (None, "0") else f"{major}.{minor}"
        key = (family, version)
    else:
        match = CLAUDE_OLD.search(model)
        if not match:
            return None
        major, minor, family = match.groups()
        key = (family, f"{major}.{minor}")
        if key == ("sonnet", "3.5"):  # only Claude 3.5 Sonnet v2 supports caching on Bedrock
            return 1024 if bedrock and "20241022" in model else None
    minimum, on_bedrock = CLAUDE_MINIMUMS.get(key, (None, False))
    if bedrock and not on_bedrock:
        return None
    return minimum


def _model_text(ctx, node):
    value = resolve(ctx, node) if node is not None else None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _block_text(ctx, block):
    """(text, complete, opaque) of one system/message content element."""
    text, done = static_text(ctx, block)
    if done or text:
        return text, done, False
    items = dict_items(ctx, block)
    if items is None:
        resolved = resolve(ctx, block)
        # an unknown container element may carry a cache marker; an unknown string may not
        opaque = not isinstance(resolved, (ast.JoinedStr, ast.BinOp, ast.Call, ast.Constant))
        return "", False, opaque
    if "text" not in items:
        return "", False, False  # image/document/guard block: stop the static prefix here
    text, done = static_text(ctx, items["text"])
    return text, done, False


def _content_text(ctx, node):
    """(text, complete, opaque) of a string or a list of content blocks."""
    items, complete = static_elements(ctx, node)
    if items is None:
        return _block_text(ctx, node)
    parts = []
    for item in items:
        text, done, opaque = _block_text(ctx, item)
        parts.append(text)
        if not done:
            return "".join(parts), False, opaque
    return "".join(parts), complete, not complete


def _messages_text(ctx, node):
    """Leading static text of a messages list (stops at the first dynamic part)."""
    items, _ = static_elements(ctx, node)
    parts = []
    for message in items or []:
        fields = dict_items(ctx, message)
        if fields is None or "content" not in fields:
            break
        text, done, _ = _content_text(ctx, fields["content"])
        parts.append(text)
        if not done:
            break
    return "".join(parts)


def _request(ctx, call, keywords):
    """(model id text, {tools, system, messages} nodes, platform) or None if the request is unknown."""
    if call.provider == "anthropic":
        if "extra_body" in keywords:
            return None
        return _model_text(ctx, keywords.get("model")), keywords, "anthropic"
    if "promptVariables" in keywords:
        return None  # Bedrock Prompt management has its own caching option
    model = _model_text(ctx, keywords.get("modelId"))
    if call.api.startswith("converse"):
        request = keywords
    else:
        body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
        if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
            return None
        request = dict_items(ctx, body.args[0])
        if request is None:
            return None
    fields = {key: request[key] for key in ("system", "messages", "tools") if key in request}
    if "toolConfig" in request:
        config = dict_items(ctx, request["toolConfig"])
        if config is None or "tools" in fields:
            return None
        if "tools" in config:
            fields["tools"] = config["tools"]
    return model, fields, "bedrock"


def _minimum(call, model, platform):
    """(minimum cacheable tokens or None, tools count towards the prefix, name of the missing marker)."""
    if platform == "anthropic":
        if model is None:
            return UNKNOWN_MODEL_MINIMUM, True, "cache_control"
        return claude_minimum(model, bedrock="anthropic." in model.lower()), True, "cache_control"
    converse = call.api.startswith("converse")
    if model is None or ":prompt/" in model:
        return None, False, None
    if NOVA_MODELS.search(model.lower()):
        return NOVA_MINIMUM, False, "cachePoint"
    return claude_minimum(model, bedrock=True), True, "cachePoint" if converse else "cache_control block"


def static_prefix(ctx, call):
    """(estimated tokens, parts, minimum, marker) of a call's static prefix, or None if it cannot be judged."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None or "cache_control" in keywords:
        return None
    request = _request(ctx, call, keywords)
    if request is None:
        return None
    model, fields, platform = request
    minimum, tools_count, marker = _minimum(call, model, platform)
    if minimum is None:
        return None
    chars, parts = 0, []
    if "tools" in fields:
        size = static_size(ctx, fields["tools"])
        if size is None:
            return None  # unknown tools: neither the prefix nor the absence of markers is known
        if tools_count and size:
            chars += size
            parts.append("tool definitions")
    complete = True
    if "system" in fields:
        text, complete, opaque = _content_text(ctx, fields["system"])
        if opaque:
            return None
        if text.strip():
            chars += len(text)
            parts.append("system prompt")
    if complete and "messages" in fields:
        text = _messages_text(ctx, fields["messages"])
        if text.strip():
            chars += len(text)
            parts.append("leading messages")
    return chars // CHARS_PER_TOKEN, parts, minimum, marker


def _reused(ctx, node):
    """True if the call can run more than once: in a loop, or in a function other than a script's `main`."""
    if ctx.enclosing_loop(node) is not None:
        return True
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, static.FUNC_NODES):
            return getattr(ancestor, "name", None) != "main"
    return False


def _summary(call, tokens, parts, minimum, marker):
    target = f"{call.receiver}.{call.api}()"
    what = " and ".join(parts)
    if call.provider == "anthropic":
        return (
            f"Anthropic {target} resends a static prefix ({what}) of about {tokens:,} tokens (estimated) with no "
            f"{marker}, so it is processed at the full input rate on every call; the model caches "
            f"prefixes from {minimum:,} tokens."
        )
    return (
        f"Bedrock {target} resends a static prefix ({what}) of about {tokens:,} tokens (estimated) with no "
        f"{marker}; only best-effort implicit caching applies, and the model supports explicit cache "
        f"checkpoints from {minimum:,} tokens."
    )


def run(ctx):
    if CACHE_MARKERS.search("\n".join(ctx.lines)):
        return []
    hits = []
    for call in llm_calls(ctx):
        checked = (call.provider == "anthropic" and call.api in ANTHROPIC_APIS) or (
            call.provider == "bedrock" and call.api in BEDROCK_APIS
        )
        if not checked or not _reused(ctx, call.node):
            continue
        prefix = static_prefix(ctx, call)
        if prefix is None:
            continue
        tokens, parts, minimum, marker = prefix
        if tokens < minimum:
            continue
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, tokens, parts, minimum, marker),
            confidence="medium" if call.provider == "anthropic" else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
