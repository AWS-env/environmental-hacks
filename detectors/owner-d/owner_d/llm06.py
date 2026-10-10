"""LLM-06: parallel calls defeating the prompt cache (static proxy).

Detector semantics version 1.0.0. Flags a concurrent fan-out (`asyncio.gather`/`wait`/
`as_completed`, `asyncio.TaskGroup`/anyio task groups, `ThreadPoolExecutor.map`/`submit`) whose
units reach the same Anthropic `messages.*` or Bedrock `converse`/`converse_stream`/`invoke_model`
call, when that call sends a static prefix with an explicit cache breakpoint (`cache_control` block,
`cachePoint`) that reaches the model's minimum cacheable length, and nothing earlier in the fan-out's
function (or anywhere in the file, for named warm-ups) sends that prefix first. A cache entry is only
readable after the first response begins, so concurrent requests over a cold prefix each write it.
This is the complement of LLM-01 (large prefix with no cache marker). Python only; static only.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass

from . import static
from .llm01 import CHARS_PER_TOKEN, NOVA_MINIMUM, NOVA_MODELS, UNKNOWN_MODEL_MINIMUM, claude_minimum
from .llmcalls import SCOPES, call_keywords, dict_items, llm_calls, resolve, static_elements, static_size, static_text
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-06"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-06", "LLM06")

ANTHROPIC_APIS = frozenset({
    "messages.create", "messages.stream", "messages.parse",
    "beta.messages.create", "beta.messages.stream", "beta.messages.parse",
})
BEDROCK_APIS = frozenset({"converse", "converse_stream", "invoke_model", "invoke_model_with_response_stream"})

MAX_DEPTH = 3  # same-file helper functions followed from a fan-out unit to the LLM call
UNKNOWN_WIDTH = 2  # a comprehension/loop over a non-literal iterable counts as at least two calls
TASK_GROUPS = frozenset({"asyncio.TaskGroup", "anyio.create_task_group", "trio.open_nursery"})
EXECUTORS = ("ThreadPoolExecutor", "ProcessPoolExecutor")
PARTIALS = frozenset({"functools.partial", "partial"})
# Any of these in a file means the cache is (or may be) warmed before the fan-out.
PREWARM = re.compile(r"""(?:max_tokens|maxTokens)["']?\s*[=:]\s*0(?![\d.])""")
WARM_NAME = re.compile(r"warm", re.I)

REFERENCES = (
    "https://platform.claude.com/docs/en/build-with-claude/prompt-caching",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
)
RECOMMENDATION = (
    "Warm the cache before fanning out: send one request with the shared cached prefix and wait for its "
    "first streamed token (or send a max_tokens=0 pre-warm request on the Anthropic API), then launch the "
    "remaining calls so they read the entry instead of each writing it. Check cache_read_input_tokens / "
    "cacheReadInputTokens on the fanned-out responses."
)
LIMITATION = (
    "Static proxy only: LLM-06 proves that a file launches concurrent LLM calls (asyncio.gather/wait/"
    "as_completed, asyncio/anyio task groups, ThreadPoolExecutor/ProcessPoolExecutor map/submit) over a "
    "static prefix with an explicit cache_control/cachePoint breakpoint at or above the model's minimum "
    "cacheable length (estimated at 4 characters per token), with no call sending that prefix earlier in the "
    "fan-out's function. It does not prove cache misses: traffic from other requests within the cache TTL may "
    "already have warmed the entry, and no cache usage or token counts are measured. Covered: Anthropic SDK "
    "messages calls and Bedrock converse/converse_stream/invoke_model (Claude and Nova) in Python, reached "
    "directly or through up to 3 same-file functions. Not flagged: calls without an explicit breakpoint "
    "(LLM-01), top-level automatic cache_control only, OpenAI, dynamic or unresolvable prefixes, targets in "
    "other modules, sync clients awaited from coroutines (no real concurrency), executors with max_workers=1, "
    "files that pre-warm (max_tokens=0 or functions/calls named *warm*) or use Semaphore(1). Warm-ups done by "
    "callers in other functions or files are not seen. The taxonomy's CloudWatch Logs Insights route over "
    "client usage logs is not implemented in this version."
)


# --- cached static prefix of one LLM call -----------------------------------------------------


@dataclass(frozen=True)
class Prefix:
    tokens: int  # estimated tokens up to the last static cache breakpoint
    minimum: int
    parts: tuple
    marker: str
    key: tuple  # (model, static pieces): equal keys mean the same cached prefix


class _Walk:
    """Static prefix accumulated in cache order; remembers the state at the last breakpoint."""

    def __init__(self):
        self.chars, self.pieces, self.parts = 0, [], []
        self.cached = None

    def add(self, size, piece, part):
        if size:
            self.chars += size
            self.pieces.append(piece)
            if part not in self.parts:
                self.parts.append(part)

    def breakpoint(self, marker):
        self.cached = (self.chars, tuple(self.pieces), tuple(self.parts), marker)


def _model_text(ctx, node):
    value = resolve(ctx, node) if node is not None else None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _request(ctx, call):
    """(model, {tools, system, messages} nodes, minimum, tools count) or None if unknown/uncacheable."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None:
        return None
    if call.provider == "anthropic":
        if call.api not in ANTHROPIC_APIS or "extra_body" in keywords:
            return None
        model = _model_text(ctx, keywords.get("model"))
        if model is None:
            return None, keywords, UNKNOWN_MODEL_MINIMUM, True
        return model, keywords, claude_minimum(model, bedrock="anthropic." in model.lower()), True
    if call.provider != "bedrock" or call.api not in BEDROCK_APIS or "promptVariables" in keywords:
        return None
    model = _model_text(ctx, keywords.get("modelId"))
    if model is None or ":prompt/" in model:
        return None
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
    if NOVA_MODELS.search(model.lower()):
        return model, fields, NOVA_MINIMUM, False  # Nova tool definitions take no checkpoints
    return model, fields, claude_minimum(model, bedrock=True), True


def _tools(ctx, walk, node, count):
    """Add static tool definitions; False when the static prefix ends here."""
    items, complete = static_elements(ctx, node)
    if items is None:
        return False
    for item in items:
        fields = dict_items(ctx, item)
        if fields is not None and "cachePoint" in fields:
            walk.breakpoint("cachePoint")
            continue
        size = static_size(ctx, item)
        if size is None:
            return False
        marked = fields is not None and "cache_control" in fields
        if marked:  # the marker itself is not prompt content
            size -= len('"cache_control":') + (static_size(ctx, fields["cache_control"]) or 0) + 1
        if count:
            walk.add(size, ast.dump(resolve(ctx, item)), "tool definitions")
        if marked:
            walk.breakpoint("cache_control")
    return complete


def _blocks(ctx, walk, node, part):
    """Add a string or a list of content blocks; False when the static prefix ends here."""
    items, complete = static_elements(ctx, node)
    if items is None:
        text, done = static_text(ctx, node)
        walk.add(len(text), text, part)
        return done
    for item in items:
        fields = dict_items(ctx, item)
        if fields is None:
            text, done = static_text(ctx, item)
            walk.add(len(text), text, part)
            if not done:
                return False
            continue
        if "cachePoint" in fields:
            walk.breakpoint("cachePoint")
            continue
        if "text" not in fields:
            return False  # image/document/tool block: the static prefix stops here
        text, done = static_text(ctx, fields["text"])
        walk.add(len(text), text, part)
        if not done:
            return False
        if "cache_control" in fields:
            walk.breakpoint("cache_control")
    return complete


def _messages(ctx, walk, node):
    items, complete = static_elements(ctx, node)
    for message in items or []:
        fields = dict_items(ctx, message)
        if fields is None or "content" not in fields or not _blocks(ctx, walk, fields["content"], "leading messages"):
            return False
    return items is not None and complete


def cached_prefix(ctx, call):
    """Prefix of a call up to its last static cache breakpoint, or None if there is none or it is unknown."""
    request = _request(ctx, call)
    if request is None:
        return None
    model, fields, minimum, tools_count = request
    if minimum is None:
        return None
    walk = _Walk()
    static_so_far = True
    if "tools" in fields:
        static_so_far = _tools(ctx, walk, fields["tools"], tools_count)
    if static_so_far and "system" in fields:
        static_so_far = _blocks(ctx, walk, fields["system"], "system prompt")
    if static_so_far and "messages" in fields:
        _messages(ctx, walk, fields["messages"])
    if walk.cached is None:
        return None
    chars, pieces, parts, marker = walk.cached
    return Prefix(chars // CHARS_PER_TOKEN, minimum, parts, marker, (model, pieces))


# --- fan-out sites ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    node: ast.AST  # a call (coroutine or LLM call) or, if `ref`, a function expression
    ref: bool
    thread: bool  # runs in a worker thread/process: the LLM call must be synchronous
    copies: int | None  # None: a comprehension/loop over a non-literal iterable
    source: ast.AST  # the expression the unit came from (excluded from the warm-up search)


@dataclass(frozen=True)
class FanOut:
    node: ast.Call  # evidence anchor
    api: str
    units: tuple


def _own_nodes(scope):
    """Nodes in `scope`, excluding the bodies of nested functions and classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _scope(ctx, node):
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, static.FUNC_NODES):
            return ancestor
    return ctx.tree


def _iter_width(ctx, node):
    """Number of items of a statically known iterable, else None."""
    node = resolve(ctx, node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
        if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
            return max(node.args[0].value, 0)
        return None
    items, complete = static_elements(ctx, node)
    return len(items) if items is not None and complete else None


def _loop_width(ctx, loop):
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        return _iter_width(ctx, loop.iter)
    if isinstance(loop, static.COMP_NODES) and len(loop.generators) == 1 and not loop.generators[0].ifs:
        return _iter_width(ctx, loop.generators[0].iter)
    return None


def _copies(ctx, node, within=None):
    """How many times `node` runs: the width of its enclosing loop (inside `within`), else 1."""
    loop = ctx.enclosing_loop(node)
    if loop is None or (within is not None and not any(a is within for a in ctx.ancestors(loop))):
        return 1
    return _loop_width(ctx, loop)


def _unit(ctx, expr, copies, source=None):
    source = source if source is not None else expr
    if isinstance(expr, ast.Name):
        value = resolve(ctx, expr)
        return _unit(ctx, value, copies, value) if isinstance(value, ast.Call) else None
    if not isinstance(expr, ast.Call):
        return None
    dotted = ctx.dotted(expr.func) or ""
    attr = expr.func.attr if isinstance(expr.func, ast.Attribute) else None
    if (dotted in ("asyncio.create_task", "asyncio.ensure_future") or attr in ("create_task", "ensure_future")) and expr.args:
        return _unit(ctx, expr.args[0], copies, source)
    if dotted == "asyncio.to_thread" and expr.args:
        return Unit(expr.args[0], True, True, copies, source)
    if attr == "run_in_executor" and len(expr.args) >= 2:
        return Unit(expr.args[1], True, True, copies, source)
    return Unit(expr, False, False, copies, source)


def _sequence(ctx, node):
    """Units of a sequence of awaitables: a literal, a comprehension, or a list built with .append."""
    units = []
    if isinstance(node, ast.Name):
        scope = _scope(ctx, node)
        for call in _own_nodes(scope):
            if (
                isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "append"
                and isinstance(call.func.value, ast.Name) and call.func.value.id == node.id and len(call.args) == 1
            ):
                units.append(_unit(ctx, call.args[0], _copies(ctx, call)))
        if units:
            return [u for u in units if u]
    value = resolve(ctx, node)
    if isinstance(value, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        units.append(_unit(ctx, value.elt, _loop_width(ctx, value)))
    else:
        items, _ = static_elements(ctx, value)
        units.extend(_unit(ctx, item, 1) for item in items or [])
    return [u for u in units if u]


def _executor_names(ctx):
    """{name: concurrent} for names bound to ThreadPoolExecutor/ProcessPoolExecutor objects."""
    found = {}

    def add(target, value):
        if not (isinstance(target, ast.Name) and isinstance(value, ast.Call)):
            return
        if not (ctx.dotted(value.func) or "").endswith(EXECUTORS):
            return
        workers = next((kw.value for kw in value.keywords if kw.arg == "max_workers"), value.args[0] if value.args else None)
        workers = resolve(ctx, workers) if workers is not None else None
        single = isinstance(workers, ast.Constant) and workers.value == 1
        found[target.id] = found.get(target.id, True) and not single

    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.withitem) and node.optional_vars is not None:
            add(node.optional_vars, node.context_expr)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            add(node.targets[0], node.value)
    return found


def fanouts(ctx):
    """Concurrent fan-out sites in the file."""
    executors = _executor_names(ctx)
    submits = {}
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and isinstance(item.context_expr, ast.Call):
                    if (ctx.dotted(item.context_expr.func) or "") in TASK_GROUPS:
                        found = _task_group(ctx, node, item.optional_vars.id)
                        if found:
                            yield found
            continue
        if not isinstance(node, ast.Call):
            continue
        dotted = ctx.dotted(node.func) or ""
        if dotted == "asyncio.gather":
            units = []
            for arg in node.args:
                if isinstance(arg, ast.Starred):
                    units.extend(_sequence(ctx, arg.value))
                else:
                    units.append(_unit(ctx, arg, 1))
            yield FanOut(node, "asyncio.gather", tuple(u for u in units if u))
        elif dotted in ("asyncio.wait", "asyncio.as_completed") and node.args:
            yield FanOut(node, dotted, tuple(_sequence(ctx, node.args[0])))
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            name, method = node.func.value.id, node.func.attr
            if not executors.get(name) or not node.args:
                continue
            if method == "map":
                width = _iter_width(ctx, node.args[1]) if len(node.args) > 1 else None
                yield FanOut(node, "Executor.map", (Unit(node.args[0], True, True, width, node),))
            elif method == "submit":
                key = (id(_scope(ctx, node)), name)
                submits.setdefault(key, []).append(node)
    for calls in submits.values():
        calls.sort(key=lambda c: (c.lineno, c.col_offset))
        units = tuple(Unit(call.args[0], True, True, _copies(ctx, call), call) for call in calls)
        yield FanOut(calls[0], "Executor.submit", units)


def _task_group(ctx, block, name):
    calls = []
    for node in ast.walk(block):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == name
            and node.func.attr in ("create_task", "start_soon") and node.args
        ):
            calls.append(node)
    if not calls:
        return None
    calls.sort(key=lambda c: (c.lineno, c.col_offset))
    units = []
    for call in calls:
        copies = _copies(ctx, call, within=block)
        if call.func.attr == "start_soon":
            units.append(Unit(call.args[0], True, False, copies, call))
        else:
            units.append(_unit(ctx, call.args[0], copies, call))
    return FanOut(calls[0], "TaskGroup.create_task", tuple(u for u in units if u))


# --- from a unit to the LLM calls it runs ------------------------------------------------------


class _Index:
    def __init__(self, ctx):
        self.ctx = ctx
        self.llm = {id(call.node): call for call in llm_calls(ctx)}
        self.defs = {}
        for node in ast.walk(ctx.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not isinstance(ctx.parent(node), ast.ClassDef):
                self.defs.setdefault(node.name, []).append(node)
        self.prefixes = {}

    def prefix(self, call):
        if id(call.node) not in self.prefixes:
            self.prefixes[id(call.node)] = cached_prefix(self.ctx, call)
        return self.prefixes[id(call.node)]

    def function(self, expr, site):
        """The same-file function (or lambda) an expression refers to, else None."""
        ctx = self.ctx
        if isinstance(expr, ast.Call) and (ctx.dotted(expr.func) or "") in PARTIALS and expr.args:
            expr = expr.args[0]
        if isinstance(expr, ast.Lambda):
            return expr
        if isinstance(expr, ast.Name):
            defs = self.defs.get(expr.id, [])
            return defs[0] if len(defs) == 1 else None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in ("self", "cls"):
            owner = next((a for a in ctx.ancestors(site) if isinstance(a, ast.ClassDef)), None)
            if owner is None:
                return None
            methods = [n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == expr.attr]
            return methods[0] if len(methods) == 1 else None
        return None

    def awaited(self, node):
        parent = self.ctx.parent(node)
        if isinstance(parent, ast.Await):
            return True
        return isinstance(parent, ast.withitem) and isinstance(self.ctx.parent(parent), ast.AsyncWith)

    def reached(self, func, mode, depth=0, seen=None):
        """LLM calls run by `func` (mode "async": awaited; "sync": blocking; None: either)."""
        seen = seen if seen is not None else set()
        seen.add(id(func))
        for node in _own_nodes(func):
            call = self.llm.get(id(node))
            if call is not None:
                if mode is None or self.awaited(node) == (mode == "async"):
                    yield call
                continue
            if not isinstance(node, ast.Call) or depth >= MAX_DEPTH:
                continue
            callee = self.function(node.func, node)
            if callee is None or id(callee) in seen or isinstance(callee, ast.Lambda):
                continue
            if mode == "async" and not (isinstance(callee, ast.AsyncFunctionDef) and self.awaited(node)):
                continue
            if mode == "sync" and not isinstance(callee, ast.FunctionDef):
                continue
            yield from self.reached(callee, mode, depth + 1, seen)

    def unit_calls(self, unit):
        """LLM calls a fan-out unit runs concurrently with its siblings."""
        if not unit.ref:
            call = self.llm.get(id(unit.node))
            if call is not None:
                return [call]  # a coroutine created for gather/create_task: the client is async
            func = self.function(unit.node.func, unit.node)
            if not isinstance(func, ast.AsyncFunctionDef):
                return []  # a plain call runs before gather sees it: no concurrency
            return list(self.reached(func, "async"))
        func = self.function(unit.node, unit.node)
        if func is None:
            return []
        if unit.thread:
            return [] if isinstance(func, ast.AsyncFunctionDef) else list(self.reached(func, "sync"))
        return list(self.reached(func, "async")) if isinstance(func, ast.AsyncFunctionDef) else []

    def call_reaches(self, node):
        """LLM calls any call expression may run (for the warm-up search)."""
        call = self.llm.get(id(node))
        if call is not None:
            return [call]
        func = self.function(node.func, node)
        return list(self.reached(func, None)) if func is not None else []


def _file_prewarms(ctx):
    if PREWARM.search("\n".join(ctx.lines)):
        return True
    for node in ast.walk(ctx.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and WARM_NAME.search(node.name):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if WARM_NAME.search(name):
                return True
            if name in ("Semaphore", "BoundedSemaphore") and node.args:
                limit = resolve(ctx, node.args[0])
                if isinstance(limit, ast.Constant) and limit.value == 1:
                    return True
    return False


def _warmed_keys(ctx, index, fanout):
    """Prefix keys sent by calls that run before the fan-out in the same function."""
    excluded = {id(n) for unit in fanout.units for n in ast.walk(unit.source)}
    excluded.update(id(n) for n in ast.walk(fanout.node))
    start = (fanout.node.lineno, fanout.node.col_offset)
    keys = set()
    for node in _own_nodes(_scope(ctx, fanout.node)):
        if not isinstance(node, ast.Call) or id(node) in excluded or (node.lineno, node.col_offset) >= start:
            continue
        for call in index.call_reaches(node):
            prefix = index.prefix(call)
            if prefix is not None:
                keys.add(prefix.key)
    return keys


def _entry_point(ctx, node):
    scope = _scope(ctx, node)
    return scope is ctx.tree or getattr(scope, "name", None) == "main"


def _label(call):
    return "Anthropic" if call.provider == "anthropic" else "Bedrock"


def _summary(fanout, call, target, prefix, width):
    what = " and ".join(prefix.parts)
    count = f"{width}" if width is not None else "a variable number of"
    via = f" (through {target}())" if target else ""
    return (
        f"{fanout.api} launches {count} concurrent {_label(call)} {call.receiver}.{call.api}() calls{via} that share "
        f"a static prefix ({what}) of about {prefix.tokens:,} tokens (estimated) cached with {prefix.marker}, and "
        f"nothing in this function sends that prefix first. A cache entry is readable only after the first response "
        f"begins, so each concurrent request writes the same entry instead of reading it (model minimum "
        f"{prefix.minimum:,} tokens)."
    )


def run(ctx):
    if _file_prewarms(ctx):
        return []
    index = _Index(ctx)
    hits = []
    for fanout in fanouts(ctx):
        groups = {}
        for unit in fanout.units:
            for call in index.unit_calls(unit):
                group = groups.setdefault(id(call.node), [call, []])
                group[1].append(unit.copies)
        if not groups:
            continue
        warmed = None
        for call, copies in groups.values():
            total = sum(UNKNOWN_WIDTH if c is None else c for c in copies)
            if total < 2:
                continue
            prefix = index.prefix(call)
            if prefix is None or prefix.tokens < prefix.minimum:
                continue
            if warmed is None:
                warmed = _warmed_keys(ctx, index, fanout)
            if prefix.key in warmed:
                continue
            line = call.node.lineno
            if line <= len(ctx.lines) and static.is_noqa(NOQA, ctx.lines[line - 1]):
                continue
            width = None if None in copies else total
            target_scope = _scope(ctx, call.node)
            target = None if target_scope is _scope(ctx, fanout.node) else ctx.qualname(call.node)
            confidence = "medium" if call.provider == "anthropic" and _entry_point(ctx, fanout.node) else "low"
            hits.append(Hit(
                node=fanout.node,
                anchor=f"{ctx.qualname(fanout.node)}:{fanout.api}->{ctx.qualname(call.node)}:{call.provider}.{call.api}",
                summary=_summary(fanout, call, target, prefix, width),
                confidence=confidence,
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
