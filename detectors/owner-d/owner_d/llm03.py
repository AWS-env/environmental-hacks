"""LLM-03: bloated system prompts and redundant instructions (static proxy).

Detector semantics version 1.0.0. Flags LLM calls whose statically resolvable system prompt is
estimated (4 characters per token, literal text only) above `context.max_system_prompt_tokens`, or
repeats a normalised sentence of at least `context.min_repeated_instruction_chars` characters within
one placeholder-free stretch. Few-shot examples, labelled example lines, code blocks and templated
sentences are not counted as repeats; files with prompt caching are exempt from the size rule.
Covered: Anthropic `messages.*`, OpenAI chat completions / responses, Bedrock `converse`/
`converse_stream`/`invoke_model`. Python only; static only.
"""

from __future__ import annotations

import ast
import inspect
import re
import string
import sys
import textwrap
from types import SimpleNamespace

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve, static_elements
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-03"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-03", "LLM03")
CHARS_PER_TOKEN = 4  # undercounts tokens (about 3.5 characters per token for Claude)

SETTING_KEYS = ("max_system_prompt_tokens", "min_repeated_instruction_chars")
# Reference values from "LLM-03 > Context settings" in detectors/owner-d/README.md; repository
# scans pass them unless a setting is supplied explicitly.
REFERENCE_SETTINGS = {
    "max_system_prompt_tokens": 4000,  # README LLM-03: a precision-first judgment (about 16,000 characters)
    "min_repeated_instruction_chars": 40,  # README LLM-03: about 7-8 words; shorter repeats are often emphasis
}

# A file that configures prompt caching keeps its large stable prefix on purpose (size rule only).
CACHE_MARKERS = re.compile(r"cache_control|cachePoint|cache_point|prompt_cache_key", re.I)
SYSTEM_ROLES = frozenset({"system", "developer"})
PLACEHOLDER = "\ufffc"  # stands for any dynamic part of a prompt
TEXT_DEPTH = 24

TEXT_WRAPPERS = {"textwrap.dedent": textwrap.dedent, "inspect.cleandoc": inspect.cleandoc}
PERCENT_FIELD = re.compile(r"%(?:\([^)]*\))?[#0 +-]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[diouxXeEfFgGcrsa%]")
JINJA_FIELD = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
EXAMPLE_BLOCK = re.compile(r"<(\w*(?:example|sample|shot|demo)\w*)\b[^>]*>.*?</\1\s*>", re.I | re.S)
CODE_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
EXAMPLE_HEADING = re.compile(
    r"(?:[\w'-]+\s+){0,3}(?:examples?|few[- ]shots?(?:\s+examples?)?|samples?|demonstrations?)(?:\s+\d+)?\s*:?", re.I
)
MARKDOWN_HEADING = re.compile(r"\s{0,3}#{1,6}\s")
LABEL_LINE = re.compile(
    r"\s*(?:[-*+>]\s*)?\**(?:input|output|example|q|a|question|answer|user|assistant|human|ai|customer|agent|bot|"
    r"query|response|result|text|sentence|review|label|category|expected|thought|action|action input|observation|"
    r"final answer|context|document|passage|reasoning|explanation)(?:\s*\d+)?\**\s*:",
    re.I,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
BULLET = re.compile(r"^(?:[-*+•>]+|\d+[.)]|[a-z][.)]|#+)\s+", re.I)
LEAD_IN = re.compile(
    r"^(?:(?:very |extremely )?important|note|nb|remember|reminder|once again|again|please|critical|crucial|"
    r"warning|caution|attention)\b[\s:,!.–—-]*",
    re.I,
)
STRUCTURE = re.compile(r"[{}|]|^\s*\"[^\"]*\"\s*:")  # JSON, tables, "key": value lines
EXAMPLE_LEAD = re.compile(r"(?:for )?examples?\b|e\.g\.|samples?\b", re.I)
EXCERPT_CHARS = 80

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp02.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost03-bp01.html",
    "https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents",
    "https://developers.openai.com/api/docs/guides/latency-optimization",
    "https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
RECOMMENDATION = (
    "Trim the system prompt to the minimal set of instructions the task needs: state each instruction once, "
    "remove restated rules and lengthy persona text, move rarely needed guidance or reference material into "
    "retrieval or tools, and keep few-shot examples to the two or three that matter. Re-run your evaluation "
    "set after trimming, since shorter prompts can change behaviour; cache any large prefix that must stay."
)
LIMITATION = (
    "Static proxy only: LLM-03 proves that a call sends a statically resolvable system prompt whose literal "
    "text is estimated (4 characters per token) above the configured budget, or that repeats a sentence within "
    "one placeholder-free stretch, not that a shorter prompt would behave the same or what the prompt costs; no "
    "token counts are measured. Covered: Anthropic SDK messages (system), OpenAI chat completions (system/"
    "developer messages) and responses (instructions, system/developer input), Bedrock converse/converse_stream "
    "(system) and invoke_model bodies (system) in Python. Not flagged: prompts that are parameters, attributes, "
    "loaded from files or other modules, or built by calls; **kwargs and extra_body; repeats inside few-shot "
    "examples, labelled example lines, code blocks, templated sentences or separated by dynamic content; the "
    "size rule in files that configure prompt caching. Placeholder content is not counted, so sizes are lower "
    "bounds."
)


# --- literal text of a prompt, with a marker for each dynamic part ---------------------------


def _format_template(text):
    """`str.format` template with each replacement field replaced by the placeholder."""
    try:
        parts = list(string.Formatter().parse(text))
    except ValueError:
        cut = text.find("{")
        return text[:cut] + PLACEHOLDER
    return "".join(literal + (PLACEHOLDER if field is not None else "") for literal, field, _, _ in parts)


def prompt_text(ctx, node, depth=0):
    """Literal text of a string expression; each part not known statically becomes PLACEHOLDER."""
    node = resolve(ctx, node) if depth < TEXT_DEPTH else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return prompt_text(ctx, node.left, depth + 1) + prompt_text(ctx, node.right, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        text = prompt_text(ctx, node.left, depth + 1)
        return PERCENT_FIELD.sub(lambda match: "%" if match.group() == "%%" else PLACEHOLDER, text)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(value.value)
            elif value.conversion == -1 and value.format_spec is None:
                parts.append(prompt_text(ctx, value.value, depth + 1))
            else:
                parts.append(PLACEHOLDER)
        return "".join(parts)
    if isinstance(node, ast.Call):
        return _call_text(ctx, node, depth)
    return PLACEHOLDER


def _call_text(ctx, node, depth):
    wrapper = TEXT_WRAPPERS.get(ctx.dotted(node.func) or "")
    if wrapper and len(node.args) == 1 and not node.keywords:
        return wrapper(prompt_text(ctx, node.args[0], depth + 1))
    if not isinstance(node.func, ast.Attribute):
        return PLACEHOLDER
    method, receiver = node.func.attr, node.func.value
    if method in ("strip", "lstrip", "rstrip") and not node.args and not node.keywords:
        return getattr(prompt_text(ctx, receiver, depth + 1), method)()
    if method == "format":
        return _format_template(prompt_text(ctx, receiver, depth + 1))
    if method == "join" and len(node.args) == 1 and not node.keywords:
        items, complete = static_elements(ctx, node.args[0], depth + 1)
        if items is None:
            return PLACEHOLDER
        texts = [prompt_text(ctx, item, depth + 1) for item in items] + ([] if complete else [PLACEHOLDER])
        return prompt_text(ctx, receiver, depth + 1).join(texts)
    return PLACEHOLDER


# --- the system prompt of a call ------------------------------------------------------------


def _content(ctx, node):
    """Text of a string or a list of content blocks (Anthropic text blocks, Bedrock system blocks)."""
    items, complete = static_elements(ctx, node)
    if items is None:
        return prompt_text(ctx, node)
    parts = []
    for item in items:
        fields = dict_items(ctx, item)
        if fields is None:
            parts.append(prompt_text(ctx, item))
        elif "text" in fields:
            parts.append(prompt_text(ctx, fields["text"]))
        elif "cachePoint" not in fields:
            parts.append(PLACEHOLDER)  # image, document or guard content
    return "\n\n".join(parts) + ("" if complete else PLACEHOLDER)


def _system_messages(ctx, node):
    """Text of the system/developer entries of a message list, or None if there are none."""
    items, complete = static_elements(ctx, node)
    parts, found = [], False
    for item in items or []:
        fields = dict_items(ctx, item)
        role = resolve(ctx, fields["role"]) if fields and "role" in fields else None
        if not (isinstance(role, ast.Constant) and isinstance(role.value, str)):
            parts.append(PLACEHOLDER)  # an unknown message may be a dynamic system message
        elif role.value in SYSTEM_ROLES and "content" in fields:
            parts.append(_content(ctx, fields["content"]))
            found = True
    if not found:
        return None
    return "\n\n".join(parts) + ("" if complete else PLACEHOLDER)


def _invoke_request(ctx, keywords):
    body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
    if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
        return None
    return dict_items(ctx, body.args[0])


def system_prompt(ctx, call):
    """Literal text (with placeholders) of the system prompt a call sends, or None if none is known."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None or "extra_body" in keywords:
        return None
    if call.provider == "anthropic" or call.api.startswith("converse"):
        return _content(ctx, keywords["system"]) if "system" in keywords else None
    if call.provider == "bedrock":
        request = _invoke_request(ctx, keywords)
        return _content(ctx, request["system"]) if request and "system" in request else None
    if call.api.startswith("responses"):
        parts = []
        if "instructions" in keywords:
            parts.append(_content(ctx, keywords["instructions"]))
        if "input" in keywords:
            text = _system_messages(ctx, keywords["input"])
            parts += [text] if text is not None else []
        return "\n\n".join(parts) if parts else None
    return _system_messages(ctx, keywords["messages"]) if "messages" in keywords else None


# --- redundancy ------------------------------------------------------------------------------


def _placeholders(text):
    """What is left of removed text: its placeholders, which still separate stretches."""
    return PLACEHOLDER * text.count(PLACEHOLDER)


def _indent(line):
    return len(line) - len(line.lstrip())


def _without_examples(text):
    """The prompt with few-shot example blocks, example sections and code blocks removed."""
    def drop(match):
        return "\n" + _placeholders(match.group())

    text = CODE_FENCE.sub(drop, EXAMPLE_BLOCK.sub(drop, text))
    kept, in_section, example_indent = [], False, None
    for line in text.split("\n"):
        bare = line.strip().strip("#*_ ").strip()
        if example_indent is not None and (not bare or _indent(line) > example_indent):
            kept.append(_placeholders(line))  # a line indented under an "Example ..." line
            continue
        example_indent = None
        if len(bare) <= 60 and EXAMPLE_HEADING.fullmatch(bare):
            in_section = True
        elif in_section and MARKDOWN_HEADING.match(line):
            in_section = False
        elif EXAMPLE_LEAD.match(BULLET.sub("", bare)):
            example_indent = _indent(line)
            kept.append(_placeholders(line))
            continue
        kept.append(_placeholders(line) if in_section else line)
    return "\n".join(kept)


def normalise(sentence):
    """Comparable form of an instruction: no bullets, emphasis, lead-ins or shouting; wording and Title Case kept."""
    text = BULLET.sub("", sentence.strip())
    text = re.sub(r"[*_`]+", "", text).strip()
    previous = None
    while previous != text:
        previous, text = text, LEAD_IN.sub("", text).strip()
    words = [word.lower() if word.isupper() and len(word) > 1 else word for word in text.split()]
    text = " ".join(words).strip(" .!?:;,-–—\"'")
    return text[:1].lower() + text[1:]


def repeated_instructions(text, min_chars):
    """[(first sentence, count)] of sentences repeated within one placeholder-free stretch."""
    seen = {}
    stretch = 0
    for line in _without_examples(JINJA_FIELD.sub(PLACEHOLDER, text)).split("\n"):
        if LABEL_LINE.match(line):
            stretch += line.count(PLACEHOLDER)
            continue
        for sentence in SENTENCE_END.split(line):
            if PLACEHOLDER in sentence:
                stretch += sentence.count(PLACEHOLDER)
                continue
            if STRUCTURE.search(sentence):
                continue
            key = normalise(sentence)
            if len(key) < min_chars or sum(c.isalpha() for c in key) * 2 < len(key):
                continue
            first, count = seen.get((stretch, key), (sentence.strip(), 0))
            seen[(stretch, key)] = (first, count + 1)
    return [(first, count) for first, count in seen.values() if count > 1]


# --- check ----------------------------------------------------------------------------------


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


def _excerpt(sentence):
    sentence = BULLET.sub("", " ".join(sentence.split()))
    return sentence if len(sentence) <= EXCERPT_CHARS else sentence[:EXCERPT_CHARS - 1].rstrip() + "…"


def _summary(call, tokens, repeats, settings):
    target = f"{call.receiver}.{call.api}()"
    reasons = []
    if tokens is not None:
        reasons.append(
            f"is about {tokens:,} tokens (estimated from its literal text; more than "
            f"{settings['max_system_prompt_tokens']:,})"
        )
    if repeats:
        first, count = repeats[0]
        what = "an instruction" if len(repeats) == 1 else f"{len(repeats)} instructions"
        reasons.append(f'repeats {what} within it, e.g. "{_excerpt(first)}" ({count} times)')
    provider = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}[call.provider]
    if call.evidence == "chain":
        provider = "OpenAI-compatible"
    return f"{provider} {target} sends a system prompt that {' and '.join(reasons)}, on every call."


def run(ctx, settings):
    if settings is None:
        return []
    cached = CACHE_MARKERS.search("\n".join(ctx.lines)) is not None
    hits = []
    for call in llm_calls(ctx):
        text = system_prompt(ctx, call)
        if text is None:
            continue
        tokens = len(text.replace(PLACEHOLDER, "")) // CHARS_PER_TOKEN
        too_large = not cached and tokens > settings["max_system_prompt_tokens"]
        repeats = repeated_instructions(text, settings["min_repeated_instruction_chars"])
        if not (too_large or repeats):
            continue
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, tokens if too_large else None, repeats, settings),
            confidence="medium" if repeats and call.evidence != "chain" else "low",
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
