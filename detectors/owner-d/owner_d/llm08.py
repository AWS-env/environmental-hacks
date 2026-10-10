"""LLM-08: an over-sized model hard-coded for a simple task, with no model routing (static proxy).

Detector semantics version 1.0.0. Flags LLM calls whose model ID resolves statically to one
top-tier model (Claude Opus/Fable/Mythos, Amazon Nova Premier, OpenAI `-pro`) and that look like a
simple task: an output cap of at most `context.max_simple_output_tokens`, or classification /
extraction / yes-no wording in a static prompt of at most `context.max_simple_prompt_tokens`
(estimated at 4 characters per token). A model taken from a parameter, environment, configuration or
condition is not flagged (routing may exist), nor are files that also name a smaller model or a
router, calls with tools, and calls with explicit reasoning/effort settings. Covered: Anthropic
`messages.*`, OpenAI chat completions / responses / completions, Bedrock `converse`/`invoke_model`.
Python only; static only. It shows a fixed large model at a call site, not that a smaller model would
be good enough.
"""

from __future__ import annotations

import ast
import re
import sys
from types import SimpleNamespace

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve, static_elements, static_text
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-08"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-08", "LLM08")
CHARS_PER_TOKEN = 4  # the same rough estimate as LLM-01/LLM-15

SETTING_KEYS = ("max_simple_output_tokens", "max_simple_prompt_tokens")
REFERENCE_SETTINGS = {
    "max_simple_output_tokens": 256,  # README LLM-08: a label, yes/no or one extracted field fits well below it
    "max_simple_prompt_tokens": 500,  # README LLM-08: longer static instructions are treated as a real task
}

# Top-tier model IDs: (pattern, family, smaller tiers to evaluate). Kept small and explicit.
# Anthropic: Opus is the largest generally available tier, Fable/Mythos sit above it
#   (https://platform.claude.com/docs/en/about-claude/models/overview, .../about-claude/pricing).
# Amazon: Nova Premier is the most capable Nova model (https://docs.aws.amazon.com/nova/latest/userguide/what-is-nova.html).
# OpenAI: `-pro` variants use more compute than their base model (https://developers.openai.com/api/docs/models).
TOP_TIERS = (
    (re.compile(r"claude-(?:opus|fable|mythos)(?=[-@:]|$)|claude-3-opus"), "Claude Opus/Fable/Mythos tier",
     "Claude Haiku or Sonnet"),
    (re.compile(r"amazon\.nova-premier"), "Amazon Nova Premier", "Nova Micro, Lite or Pro"),
    (re.compile(r"^(?:gpt-\d+(?:\.\d+)?|o\d)-pro(?:-\d{4}-\d{2}-\d{2})?$"), "OpenAI pro tier",
     "the base model or a mini/nano model"),
)
# A string constant that is a whole model ID of one of these families.
MODEL_ID = re.compile(r"^[\w.:/@-]+$")
MODEL_FAMILY = re.compile(r"claude-\w|amazon\.nova-\w|(?:^|[/.])(?:gpt-\d|o\d(?:-|$)|chatgpt-)")
# Mentions of a router anywhere in the file: model selection may happen before the call.
ROUTER_MARKERS = re.compile(
    r"prompt[-_]?router|promptRouter|model[-_]?router|ModelRouter|litellm\.Router|RouteLLM|"
    r"\b(?:route|select|choose|pick|resolve)_model\b|\bmodel_for_(?:task|request)\b",
    re.I,
)

# Request fields that make a call agentic or show that its cost is already tuned on the large model.
TUNED_KEYS = frozenset({
    "tools", "functions", "tool_choice", "toolConfig", "thinking", "reasoning", "reasoning_effort",
    "reasoning_config", "output_config", "effort",
})
OPAQUE_KEYS = frozenset({"extra_body", "promptVariables"})
CAP_KEYS = (
    "max_tokens", "max_completion_tokens", "max_output_tokens", "max_tokens_to_sample", "max_gen_len",
    "maxTokens", "max_new_tokens", "maxTokenCount",
)
CAP_CONTAINERS = ("inferenceConfig", "textGenerationConfig", "generationConfig")
PROMPT_KEYS = ("system", "messages", "instructions", "input", "prompt", "inputText")
NON_TEXT_KEYS = frozenset({"role", "type", "cache_control", "cachePoint", "image", "document", "source", "name"})
TEXT_DEPTH = 8

# Wording of short, well-specified tasks that the providers route to their smaller tiers.
SIMPLE_TASK = re.compile(
    r"\b(?:classif(?:y|ication)|categori[sz]e|sentiment|yes or no|yes/no|true or false|true/false|"
    r"extract (?:the|all|every)|which (?:category|label|language)|detect the language|"
    r"(?:in |with )?(?:one|a single) word|one of the following (?:labels|categories))\b",
    re.I,
)

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost01-bp01.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html",
    "https://platform.claude.com/docs/en/about-claude/models/choosing-a-model",
    "https://platform.claude.com/docs/en/about-claude/models/overview",
    "https://developers.openai.com/api/docs/guides/model-selection",
    "https://docs.aws.amazon.com/nova/latest/userguide/what-is-nova.html",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
RECOMMENDATION = (
    "Route by task: send short classification, extraction and yes/no requests to a smaller tier of the "
    "same family (Claude Haiku/Sonnet, Nova Micro/Lite, an OpenAI mini model), or use Amazon Bedrock "
    "Intelligent Prompt Routing, and keep the large model for requests that need it. Read the model ID "
    "from per-task configuration, and compare quality on a sample of real requests before switching; "
    "lowering effort on the large model is an alternative. If the large model is deliberate, mark the "
    "call with # noqa: LLM-08."
)
LIMITATION = (
    "Static proxy only: LLM-08 proves that a call site hard-codes a top-tier model ID (Claude Opus/Fable/"
    "Mythos, Amazon Nova Premier, OpenAI -pro) for every request and that the call looks like a simple "
    "task (small output cap, or classification/extraction/yes-no wording in a short static prompt). It "
    "does not show that a smaller model would give acceptable quality, nor any cost; no usage is measured. "
    "Covered: Anthropic SDK messages, OpenAI chat completions/responses/completions and Bedrock converse/"
    "invoke_model in Python. Not flagged: model IDs from parameters, environment, configuration, other "
    "modules or conditions; files that also name a smaller model or a router; calls with tools or "
    "explicit reasoning/effort settings; unresolvable **kwargs, extra_body and invoke_model bodies; "
    "Sonnet, Haiku, Nova Pro/Lite/Micro and other non-top-tier models. Usage by task and model "
    "(client or Bedrock invocation logs) is not evaluated in v1."
)


def _string(ctx, node):
    value = resolve(ctx, node) if node is not None else None
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _integer(ctx, node):
    value = resolve(ctx, node)
    if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
        return value.value
    return None


def top_tier(model):
    """(family, smaller tiers) for a top-tier model ID, else None."""
    model = model.lower()
    for pattern, family, smaller in TOP_TIERS:
        if pattern.search(model):
            return family, smaller
    return None


def selects_models(ctx):
    """True if the file names a router, or any model ID of a known family that is not top tier."""
    if ROUTER_MARKERS.search("\n".join(ctx.lines)):
        return True
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if MODEL_ID.match(text) and MODEL_FAMILY.search(text.lower()) and top_tier(text) is None:
                return True
    return False


def _request(ctx, call, keywords):
    """{field: node} of the effective request, or None when it cannot be read statically."""
    if OPAQUE_KEYS & set(keywords):
        return None
    if call.provider != "bedrock" or call.api.startswith("converse"):
        return keywords
    body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
    if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
        return None
    return dict_items(ctx, body.args[0])


def _tuned(ctx, request):
    """True if the request has tools or explicit reasoning/effort settings (or unknown extra fields)."""
    if TUNED_KEYS & set(request):
        return True
    if "additionalModelRequestFields" in request:
        extra = dict_items(ctx, request["additionalModelRequestFields"])
        if extra is None:
            return True
        return any(key in TUNED_KEYS or "reason" in key.lower() or "think" in key.lower() for key in extra)
    return False


def output_cap(ctx, request):
    """Smallest statically known output-token cap of a request, or None."""
    caps = [_integer(ctx, request[key]) for key in CAP_KEYS if key in request]
    for container in CAP_CONTAINERS:
        if container in request:
            config = dict_items(ctx, request[container]) or {}
            caps.extend(_integer(ctx, config[key]) for key in CAP_KEYS if key in config)
    caps = [cap for cap in caps if cap is not None and cap >= 0]
    return min(caps) if caps else None


def _texts(ctx, node, depth=0):
    """Statically known text (or static prefixes) inside a prompt value: strings, lists and dicts."""
    if node is None or depth > TEXT_DEPTH:
        return []
    text, done = static_text(ctx, node)
    if text or done:
        return [text]
    items = dict_items(ctx, node)
    if items is not None:
        return [t for key, value in items.items() if key not in NON_TEXT_KEYS for t in _texts(ctx, value, depth + 1)]
    elements, _ = static_elements(ctx, node)
    return [t for element in elements or [] for t in _texts(ctx, element, depth + 1)]


def prompt_text(ctx, request):
    return "\n".join(t for key in PROMPT_KEYS if key in request for t in _texts(ctx, request[key]))


def simple_task(ctx, request, settings):
    """(reasons, both signals) for a request that looks like a simple task; reasons is empty otherwise."""
    reasons = []
    cap = output_cap(ctx, request)
    capped = cap is not None and cap <= settings["max_simple_output_tokens"]
    if capped:
        unit = "token" if cap == 1 else "tokens"
        reasons.append(f"output capped at {cap:,} {unit} (at most {settings['max_simple_output_tokens']:,})")
    text = prompt_text(ctx, request)
    tokens = len(text) // CHARS_PER_TOKEN
    wording = SIMPLE_TASK.search(text) if tokens <= settings["max_simple_prompt_tokens"] else None
    if wording:
        reasons.append(
            f"the static prompt (about {tokens:,} tokens, estimated) asks for a short, well-defined answer "
            f"({wording.group(0).strip().lower()!r})"
        )
    return reasons, capped and wording is not None


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


def _summary(call, model, family, smaller, reasons):
    provider = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}[call.provider]
    if call.evidence == "chain":
        provider = "OpenAI-compatible"
    return (
        f"{provider} {call.receiver}.{call.api}() hard-codes the top-tier model {model!r} ({family}) for "
        f"every request, with no model selection in this file, for what looks like a simple task: "
        f"{'; '.join(reasons)}. A smaller tier ({smaller}) may be enough; quality and cost are not measured."
    )


def run(ctx, settings):
    if settings is None or selects_models(ctx):
        return []
    hits = []
    for call in llm_calls(ctx):
        keywords = call_keywords(ctx, call.node)
        if keywords is None:
            continue
        model = _string(ctx, keywords.get("modelId" if call.provider == "bedrock" else "model"))
        tier = top_tier(model) if model else None
        if tier is None:
            continue
        request = _request(ctx, call, keywords)
        if request is None or _tuned(ctx, request):
            continue
        reasons, both = simple_task(ctx, request, settings)
        if not reasons:
            continue
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, model, *tier, reasons),
            confidence="medium" if both and call.evidence != "chain" else "low",
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
