"""LLM-07: LLM API calls with no output-token limit (static proxy for unbounded outputs).

Detector semantics version 1.0.0. Flags Bedrock `converse`/`converse_stream` calls without
`inferenceConfig.maxTokens` (Bedrock then defaults to the model's maximum) and OpenAI chat
completions / responses calls without `max_completion_tokens`/`max_tokens`/`max_output_tokens`.
A call whose arguments cannot be resolved statically (`**kwargs` from elsewhere, a config built
by a function) is not flagged. Python only; static only.
"""

from __future__ import annotations

import ast
import sys

from . import static
from .llmcalls import call_keywords, literal_dict, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-07"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-07", "LLM07")

BOUNDED, UNBOUNDED, UNKNOWN = "bounded", "unbounded", "unknown"

OPENAI_CHAT = ("max_completion_tokens", "max_tokens")
OPENAI_RESPONSES = ("max_output_tokens",)
# Calls checked by LLM-07 and the keyword(s) that cap their output.
LIMITS = {
    ("openai", "chat.completions.create"): OPENAI_CHAT,
    ("openai", "chat.completions.parse"): OPENAI_CHAT,
    ("openai", "chat.completions.stream"): OPENAI_CHAT,
    ("openai", "beta.chat.completions.parse"): OPENAI_CHAT,
    ("openai", "beta.chat.completions.stream"): OPENAI_CHAT,
    ("openai", "ChatCompletion.create"): ("max_tokens",),
    ("openai", "ChatCompletion.acreate"): ("max_tokens",),
    ("openai", "responses.create"): OPENAI_RESPONSES,
    ("openai", "responses.parse"): OPENAI_RESPONSES,
    ("openai", "responses.stream"): OPENAI_RESPONSES,
}
BEDROCK_CONVERSE = ("converse", "converse_stream")
# Model-specific keys that cap output when passed through additionalModelRequestFields.
MODEL_TOKEN_KEYS = frozenset({
    "maxTokens", "max_tokens", "max_new_tokens", "max_gen_len", "maxTokenCount",
    "max_tokens_to_sample", "max_completion_tokens", "max_output_tokens",
})
# Configuration that may carry a cap this check cannot see (stored prompts, prompt management).
OPAQUE_CONFIG = frozenset({"prompt", "promptVariables"})

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost03-bp02.html",
    "https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InferenceConfiguration.html",
    "https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create",
    "https://developers.openai.com/api/reference/resources/responses/methods/create",
    "https://developers.openai.com/api/docs/guides/latency-optimization",
    "https://coralogix.com/ai-blog/token-efficiency/",
    "https://www.getmaxim.ai/articles/top-7-performance-bottlenecks-in-llm-applications-and-how-to-overcome-them/",
)
RECOMMENDATION = (
    "Set an explicit output cap sized to the task: inferenceConfig={\"maxTokens\": N} for Bedrock Converse, "
    "max_completion_tokens for OpenAI chat completions, max_output_tokens for the Responses API. Ask for "
    "terse output (short field names, no preamble) and tune the cap from truncated responses "
    "(Bedrock stopReason \"max_tokens\", OpenAI finish_reason \"length\")."
)
LIMITATION = (
    "Static proxy only: LLM-07 proves that a call sets no explicit output-token limit, not that responses "
    "are long or tokens are wasted; usage logs are needed for that and no token counts are reported. "
    "Covered: Bedrock converse/converse_stream and OpenAI chat completions/responses (Python). Not "
    "evaluated: Bedrock invoke_model bodies (model-specific defaults, several capped at 512 tokens), the "
    "Anthropic SDK (max_tokens is required), LangChain/LiteLLM wrappers, other languages, and calls whose "
    "arguments are not statically resolvable (e.g. **kwargs built elsewhere), which are not flagged."
)


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _sets_any(mapping, keys):
    return any(key in mapping and not _is_none(mapping[key]) for key in keys)


def _dict_variants(ctx, node):
    """Statically known dicts `node` may evaluate to (both arms of `a if c else b`), or None."""
    node = resolve(ctx, node)
    if isinstance(node, ast.IfExp):
        body, orelse = _dict_variants(ctx, node.body), _dict_variants(ctx, node.orelse)
        return None if body is None or orelse is None else body + orelse
    found = literal_dict(ctx, node)
    return None if found is None else [found]


def _nested_cap(ctx, node, keys):
    """BOUNDED if a config dict sets one of `keys` in any variant, UNKNOWN if it cannot be read."""
    variants = _dict_variants(ctx, node)
    if variants is None:
        return UNKNOWN
    return BOUNDED if any(_sets_any(variant, keys) for variant in variants) else UNBOUNDED


def _openai_state(ctx, keywords, names):
    if _sets_any(keywords, names):
        return BOUNDED
    if "extra_body" in keywords:
        return _nested_cap(ctx, keywords["extra_body"], names)
    return UNBOUNDED


def _uses_managed_prompt(ctx, model):
    """Bedrock Prompt management ARNs carry their own inference configuration."""
    value = resolve(ctx, model)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return ":prompt/" in value.value
    return model is not None and "prompt" in ast.unparse(model).lower()


def _bedrock_state(ctx, keywords):
    if _uses_managed_prompt(ctx, keywords.get("modelId")):
        return UNKNOWN
    for name, keys in (("inferenceConfig", ("maxTokens",)), ("additionalModelRequestFields", MODEL_TOKEN_KEYS)):
        if name in keywords:
            state = _nested_cap(ctx, keywords[name], keys)
            if state != UNBOUNDED:
                return state
    return UNBOUNDED


def output_limit_state(ctx, call):
    """BOUNDED, UNBOUNDED or UNKNOWN for a checked call; None if LLM-07 does not check it."""
    is_converse = call.provider == "bedrock" and call.api in BEDROCK_CONVERSE
    names = LIMITS.get((call.provider, call.api))
    if not is_converse and names is None:
        return None
    keywords = call_keywords(ctx, call.node)
    if keywords is None or OPAQUE_CONFIG & keywords.keys():
        return UNKNOWN
    if is_converse:
        return _bedrock_state(ctx, keywords)
    return _openai_state(ctx, keywords, names)


def _summary(call):
    target = f"{call.receiver}.{call.api}()"
    if call.provider == "bedrock":
        return (
            f"Bedrock {target} sets no inferenceConfig maxTokens, so the response may run to the model's "
            "maximum output length (the documented default)."
        )
    names = " or ".join(LIMITS[(call.provider, call.api)])
    if call.evidence == "chain":
        return (
            f"OpenAI-compatible {target} sets no {names}; the client is not created in this file, so the "
            "endpoint's default output limit applies."
        )
    return f"OpenAI {target} sets no {names}, so the response can run to the model's (or endpoint's) default limit."


def run(ctx):
    hits = []
    for call in llm_calls(ctx):
        if output_limit_state(ctx, call) != UNBOUNDED:
            continue
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call),
            confidence="low" if call.evidence == "chain" else "medium",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
