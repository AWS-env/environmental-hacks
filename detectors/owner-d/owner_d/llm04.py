"""LLM-04: the whole context passed to every pipeline step (static proxy).

Detector semantics version 1.0.0. Within one function (or the module body), flags a later LLM step
that is sent again (H) the whole conversation history an earlier step already got, grown since, or
(D) the whole document an earlier step already got, although it also receives an earlier step's
output and is not the final step. Slices, summaries, trimming, prompt caching, tool-use
continuations and server-side context management are not flagged. Covered: Anthropic `messages.*`,
OpenAI chat completions / responses, Bedrock `converse`/`converse_stream`/`invoke_model`. Python
only; static only.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-04"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-04", "LLM04")

# A re-sent prefix is cached when the file configures prompt caching anywhere.
CACHE_MARKERS = re.compile(r"cache_control|cachePoint|cache_point", re.I)
# A tool-use turn must carry the history that produced the tool call.
TOOL_RESULT_MARKERS = re.compile(
    r"tool_result|toolResult|function_call_output|tool_call_id|[\"']role[\"']\s*:\s*[\"']tool[\"']"
)
# Server-side context management, or a request this check cannot read.
OPAQUE_KEYWORDS = frozenset({"context_management", "previous_response_id", "conversation", "truncation", "extra_body"})

GROWTH_METHODS = frozenset({"append", "extend", "insert"})
READ_METHODS = frozenset({"copy", "count", "index"})
READ_FUNCTIONS = frozenset({"len", "print", "str", "repr", "isinstance", "list", "tuple", "json.dumps"})
WHOLE_WRAPPERS = frozenset({"str", "repr", "json.dumps"})
TEXT_METHODS = frozenset({"strip", "lstrip", "rstrip"})
TEXT_KEYS = frozenset({"content", "text"})
# Name parts that suggest a large context rather than a short input such as `question` or `topic`.
CONTEXT_WORDS = frozenset({
    "doc", "document", "context", "history", "transcript", "conversation", "corpus", "article", "report",
    "page", "chunk", "passage", "source", "content", "text", "memory", "note", "thread", "email", "record",
    "knowledge", "file",
})
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
EMBED_DEPTH = 12

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp01.html",
    "https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents",
    "https://platform.claude.com/docs/en/build-with-claude/context-windows",
    "https://developers.openai.com/api/docs/guides/latency-optimization",
    "https://developers.openai.com/api/docs/guides/conversation-state",
    "https://coralogix.com/ai-blog/token-efficiency/",
    "https://www.getmaxim.ai/articles/top-7-performance-bottlenecks-in-llm-applications-and-how-to-overcome-them/",
)
RECOMMENDATION = (
    "Pass each step only what it needs: the previous step's output, a summary of the earlier history, or the "
    "relevant slice (e.g. the last turns or retrieved passages) instead of the whole history or document. "
    "Start each step with a fresh message list, compact long histories (Anthropic compaction/context editing, "
    "AgentCore Memory summaries), and cache any large prefix that must be repeated (cache_control, cachePoint)."
)
LIMITATION = (
    "Static proxy only: LLM-04 proves that one function sends the same whole conversation history or document "
    "to more than one sequential LLM step, not how many tokens are re-sent or what they cost; traces with "
    "per-step input tokens are needed for that and no measurements are reported. Covered: Anthropic SDK "
    "messages, OpenAI chat completions/responses and Bedrock converse/converse_stream/invoke_model in Python. "
    "Documents are only recognised by context-like names (document, context, text, ...). Not evaluated: "
    "LangChain/LangGraph/LlamaIndex/LiteLLM chains and graph state, steps split across functions or modules, "
    "histories held in attributes or subscripts (self.messages, state[\"messages\"]), per-turn re-sending in "
    "loops (LLM-14), and requests that are not statically resolvable. Not flagged: files with prompt caching "
    "markers (cached prefixes still fill the context window), tool-use continuations, and server-side context "
    "management. OpenAI automatic prompt caching may discount a re-sent prefix."
)


@dataclass
class Step:
    call: object  # llmcalls.LLMCall
    history: str | None  # name of a history sent whole as the conversation
    suffix: bool  # messages added after the history in this request
    documents: frozenset  # names embedded whole in the prompt text
    names: frozenset  # every name the request reads


def _pos(node):
    return node.lineno, node.col_offset


def _end(node):
    return node.end_lineno, node.end_col_offset


def _scope_of(ctx, node):
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, SCOPES):
            return ancestor
    return ctx.tree


def _own_nodes(scope):
    """Nodes in `scope`, excluding the bodies of nested functions and classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} if node is not None else set()


def _is_context_name(name):
    """True if a snake_case/camelCase part of the name (or its singular) is a context-like word."""
    parts = re.split(r"[_\d]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())
    return any(part in CONTEXT_WORDS or (part.endswith("s") and part[:-1] in CONTEXT_WORDS) for part in parts)


# --- what a request sends ------------------------------------------------------------------


def _request(ctx, call):
    """(conversation node, [system nodes], keyword values) or None if the request is unknown."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None or OPAQUE_KEYWORDS & keywords.keys():
        return None
    if call.provider == "bedrock" and not call.api.startswith("converse"):
        body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
        if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
            return None
        request = dict_items(ctx, body.args[0])
        if request is None:
            return None
        return request.get("messages"), [request.get("system")], list(keywords.values()) + list(request.values())
    if call.api.startswith("responses"):
        return keywords.get("input"), [keywords.get("instructions")], list(keywords.values())
    return keywords.get("messages"), [keywords.get("system")], list(keywords.values())


def _history(node):
    """(history name, rest nodes) when `node` sends all of one name first: `h`, `h + [...]`, `[*h, ...]`."""
    if isinstance(node, ast.Name):
        return node.id, []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and isinstance(node.left, ast.Name):
        return node.left.id, [node.right]
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        head = node.elts[0]
        if isinstance(head, ast.Starred) and isinstance(head.value, ast.Name):
            return head.value.id, node.elts[1:]
    return None, [node]


def _embedded(ctx, node, found, depth=0):
    """Add to `found` the names whose whole value ends up in the prompt text built by `node`."""
    if node is None or depth > EMBED_DEPTH:
        return
    if isinstance(node, ast.Starred):
        node = node.value
    if isinstance(node, ast.Name):
        value = resolve(ctx, node)
        if isinstance(value, ast.Constant):
            return
        if isinstance(value, (ast.JoinedStr, ast.BinOp, ast.List, ast.Tuple, ast.Dict)) or _is_text_call(ctx, value):
            _embedded(ctx, value, found, depth + 1)  # a prompt or message variable: look inside
        else:
            found.add(node.id)
        return
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                _embedded(ctx, value.value, found, depth + 1)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        if isinstance(node.op, ast.Add):
            _embedded(ctx, node.left, found, depth + 1)
        right = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        for item in right:
            _embedded(ctx, item, found, depth + 1)
    elif _is_text_call(ctx, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in TEXT_METHODS:
            _embedded(ctx, node.func.value, found, depth + 1)
        for item in node.args + [kw.value for kw in node.keywords]:
            _embedded(ctx, item, found, depth + 1)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            _embedded(ctx, item, found, depth + 1)
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in TEXT_KEYS:
                _embedded(ctx, value, found, depth + 1)


def _is_text_call(ctx, node):
    """`str(x)`, `json.dumps(x)`, `"...".format(...)`, `sep.join(x)`, `x.strip()`."""
    if not isinstance(node, ast.Call):
        return False
    if (ctx.dotted(node.func) or "") in WHOLE_WRAPPERS:
        return len(node.args) == 1
    if not isinstance(node.func, ast.Attribute):
        return False
    attr = node.func.attr
    return attr == "format" or (attr == "join" and len(node.args) == 1) or (attr in TEXT_METHODS and not node.args)


def _step(ctx, call):
    request = _request(ctx, call)
    if request is None:
        return None
    conversation, system, values = request
    history, rest = _history(conversation) if conversation is not None else (None, [])
    found = set()
    for node in [conversation] + system:
        _embedded(ctx, node, found)
    found.discard(history)  # the history itself is rule H, not a document
    names = set()
    for value in values:
        names |= _names(value)
    return Step(call, history, bool(rest) and history is not None, frozenset(found), frozenset(names))


# --- data flow and control flow inside one scope -----------------------------------------------


def _targets(node):
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
        return [node.target]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [item.optional_vars for item in node.items if item.optional_vars is not None]
    return []


def _source(node):
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return node.iter
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return ast.Tuple(elts=[item.context_expr for item in node.items])
    return getattr(node, "value", None)


def _derived(nodes, call):
    """Names bound (transitively) from the result of `call` within the scope."""
    tainted = set()
    for node in nodes:
        source = _source(node)
        if source is not None and _targets(node) and any(n is call for n in ast.walk(source)):
            tainted |= set().union(*(_names(t) for t in _targets(node)))
    changed = True
    while changed:
        changed = False
        for node in nodes:
            bound = set()
            if _targets(node) and _names(_source(node)) & tainted:
                bound = set().union(*(_names(t) for t in _targets(node)))
            elif (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.attr in GROWTH_METHODS
                and set().union(*(_names(a) for a in node.args)) & tainted
            ):
                bound = {node.func.value.id}
            if bound - tainted:
                tainted |= bound
                changed = True
    return tainted


def _changes(ctx, nodes, name, start, end, steps):
    """(grown, reset) for `name` between two positions: growth keeps it whole, anything else may not."""
    grown = reset = False
    for node in nodes:
        if not hasattr(node, "lineno") or not (start < _pos(node) < end):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in _targets(node):
                if isinstance(target, ast.Name) and target.id == name:
                    if _grows(node, name):
                        grown = True
                    else:
                        reset = True
                elif name in _names(target):
                    reset = True  # tuple unpacking, item/slice assignment
        elif isinstance(node, (ast.NamedExpr, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Delete)):
            targets = node.targets if isinstance(node, ast.Delete) else _targets(node)
            reset |= any(name in _names(target) for target in targets)
        elif isinstance(node, ast.Call) and id(node) not in steps:
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == name:
                if func.attr in GROWTH_METHODS:
                    grown = True
                elif func.attr not in READ_METHODS:
                    reset = True  # pop/remove/clear/sort or an unknown method
            elif (ctx.dotted(func) or "") not in READ_FUNCTIONS and any(
                isinstance(arg, ast.Name) and arg.id == name for arg in node.args + [kw.value for kw in node.keywords]
            ):
                reset = True  # e.g. trim(history) may compact it in place
    return grown, reset


def _grows(node, name):
    """`h += [...]`, `h = h + [...]` or `h = [*h, ...]`."""
    if isinstance(node, ast.AugAssign):
        return isinstance(node.op, ast.Add)
    history, rest = _history(node.value) if node.value is not None else (None, [])
    return history == name and bool(rest)


def _branches(ctx, node, scope):
    """{id(branching ancestor): (kind, arm)} up to the scope."""
    path, child = {}, node
    for ancestor in ctx.ancestors(node):
        if ancestor is scope:
            break
        arm = None
        if isinstance(ancestor, (ast.If, ast.IfExp)):
            body = ancestor.body if isinstance(ancestor.body, list) else [ancestor.body]
            orelse = ancestor.orelse if isinstance(ancestor.orelse, list) else [ancestor.orelse]
            arm = "body" if child in body else "orelse" if child in orelse else None
            kind = "if"
        elif isinstance(ancestor, ast.Try) or type(ancestor).__name__ == "TryStar":
            arm = f"handler{ancestor.handlers.index(child)}" if child in ancestor.handlers else (
                "finally" if child in ancestor.finalbody else "body")
            kind = "try"
        elif isinstance(ancestor, ast.Match) and child in ancestor.cases:
            arm, kind = ancestor.cases.index(child), "match"
        if arm is not None:
            path[id(ancestor)] = (kind, arm)
        child = ancestor
    return path


def _exclusive(first, second):
    for key, (kind, arm) in first.items():
        if key not in second or second[key][1] == arm:
            continue
        other = second[key][1]
        if kind != "try":
            return True
        if "finally" not in (arm, other) and (str(arm).startswith("handler") or str(other).startswith("handler")):
            return True
    return False


def _ends_scope(ctx, node, scope):
    """True if the scope is left right after `node` (its result is returned or raised)."""
    for ancestor in ctx.ancestors(node):
        if ancestor is scope or isinstance(ancestor, SCOPES):
            return isinstance(ancestor, ast.Lambda)
        if isinstance(ancestor, (ast.Return, ast.Raise)):
            return True
    return False


# --- rule -------------------------------------------------------------------------------------


def _scope_hits(ctx, scope, calls):
    nodes = list(_own_nodes(scope))
    lines = ctx.lines if scope is ctx.tree else ctx.lines[scope.lineno - 1:scope.end_lineno]
    if TOOL_RESULT_MARKERS.search("\n".join(lines)):
        return []  # a tool-use continuation must carry the history that produced the tool call
    steps = [_step(ctx, call) for call in calls]
    call_ids = {id(call.node) for call in calls}
    branches = [_branches(ctx, call.node, scope) for call in calls]
    derived = [_derived(nodes, call.node) for call in calls]
    from_steps = set().union(*derived)

    def follows(i, j):
        return not _ends_scope(ctx, calls[i].node, scope) and not _exclusive(branches[i], branches[j])

    hits = []
    for j, later in enumerate(steps):
        if later is None:
            continue
        earlier = [i for i in reversed(range(j)) if steps[i] is not None and follows(i, j)]
        reason = _history_reason(ctx, nodes, call_ids, steps, earlier, later)
        outputs = set().union(*(derived[i] for i in range(j) if follows(i, j))) & later.names
        final = not any(follows(j, k) for k in range(j + 1, len(calls)))
        if reason is None and outputs and not final:
            reason = _document_reason(ctx, nodes, call_ids, steps, earlier, later, from_steps, min(outputs))
        if reason is not None:
            hits.append(_hit(ctx, calls, j, reason))
    return hits


def _history_reason(ctx, nodes, call_ids, steps, earlier, later):
    """Rule H: the same history, sent whole to an earlier step, is sent again after growing."""
    if not later.history:
        return None
    for i in earlier:
        if steps[i].history != later.history:
            continue
        grown, reset = _changes(ctx, nodes, later.history, _end(steps[i].call.node), _pos(later.call.node), call_ids)
        if reset or not (grown or later.suffix):
            return None
        how = "grown since" if grown else "with new messages appended"
        line = steps[i].call.node.lineno
        return f"re-sends the whole conversation `{later.history}`, which step {i + 1} (line {line}) already received, {how}"
    return None


def _document_reason(ctx, nodes, call_ids, steps, earlier, later, from_steps, output):
    """Rule D: the same document, embedded whole in an earlier step, is embedded again next to its output."""
    for name in sorted(later.documents - from_steps):
        if not _is_context_name(name):
            continue
        for i in earlier:
            if name not in steps[i].documents:
                continue
            grown, reset = _changes(ctx, nodes, name, _end(steps[i].call.node), _pos(later.call.node), call_ids)
            if grown or reset:
                break
            return (
                f"re-embeds the whole `{name}`, already sent to step {i + 1} (line {steps[i].call.node.lineno}), "
                f"although it also receives an earlier step's output (`{output}`)"
            )
    return None


def _hit(ctx, calls, j, reason):
    call = calls[j]
    provider = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}[call.provider]
    if call.evidence == "chain":
        provider = "OpenAI-compatible"
    history = reason.startswith("re-sends")
    return Hit(
        node=call.node,
        anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
        summary=(
            f"{provider} {call.receiver}.{call.api}() is step {j + 1} of {len(calls)} in {ctx.qualname(call.node)} "
            f"and {reason}, instead of a slice, a summary or only the previous step's output."
        ),
        confidence="medium" if history and call.evidence != "chain" else "low",
    )


def run(ctx):
    if CACHE_MARKERS.search("\n".join(ctx.lines)):
        return []
    scopes = {}
    for call in llm_calls(ctx):
        scope = _scope_of(ctx, call.node)
        scopes.setdefault(id(scope), (scope, []))[1].append(call)
    hits = []
    for scope, calls in scopes.values():
        if len(calls) > 1:
            hits.extend(_scope_hits(ctx, scope, sorted(calls, key=lambda call: _pos(call.node))))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
