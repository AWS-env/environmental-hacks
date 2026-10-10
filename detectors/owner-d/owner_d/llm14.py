"""LLM-14: unbounded agent memory, the whole growing history re-sent every turn (static proxy).

Detector semantics version 1.0.0. Flags an LLM call whose conversation argument is all of one history
list that grows on every turn with no visible bound: (L) inside a loop, where the list is created once
before the loop and grows inside it, or (P) across requests, where the list is an instance attribute
(`self.history`) or a module-level list that a method/function grows on every call. Slices, summaries,
`deque(maxlen=...)`, any trimming or rebinding, `len()` checks, passing the history to another function,
closures and server-side context management are treated as a bound (or as unclear data flow) and are not
flagged. Covered: Anthropic `messages.*`, OpenAI chat completions / responses, Bedrock
`converse`/`converse_stream`/`invoke_model`. Python only; static only.
"""

from __future__ import annotations

import ast
import sys

from . import static
from .llmcalls import call_keywords, dict_items, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-14"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-14", "LLM14")

# Server-side conversation state or truncation, or a request this check cannot read.
OPAQUE_KEYWORDS = frozenset({"context_management", "previous_response_id", "conversation", "truncation", "extra_body"})
GROWTH_METHODS = frozenset({"append", "extend", "insert"})
READ_METHODS = frozenset({"copy", "count", "index"})
READ_FUNCTIONS = frozenset({
    "print", "str", "repr", "isinstance", "list", "tuple", "json.dumps", "enumerate", "reversed", "sorted", "iter",
    "any", "all", "bool",
})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})
FACTORY_CALLS = frozenset({"field", "dataclasses.field", "Field", "pydantic.Field", "attr.ib", "attrs.field"})
DEQUES = frozenset({"deque", "collections.deque"})
INIT_METHODS = frozenset({"__init__", "__post_init__"})
STATIC_ITERABLES = frozenset({"enumerate", "reversed", "sorted", "list", "tuple"})
BUDGETS = frozenset({"range", "itertools.islice"})  # explicit iteration counts
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)

INIT, GROWTH, RESET, READ = "init", "growth", "reset", "read"
PARAM = "param"  # initial value of a history that is a function parameter
PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp01.html",
    "https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents",
    "https://platform.claude.com/docs/en/build-with-claude/context-windows",
    "https://developers.openai.com/api/docs/guides/conversation-state",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
RECOMMENDATION = (
    "Bound the memory the agent re-sends: keep a window of recent turns (messages[-N:] or "
    "collections.deque(maxlen=N)), summarise or compact older turns (Anthropic compaction/context editing, "
    "AgentCore Memory summaries), store long-term facts outside the prompt and retrieve only the relevant ones, "
    "or let the provider hold the state (OpenAI previous_response_id with truncation=\"auto\"). Clear "
    "per-session memory when the session ends."
)
LIMITATION = (
    "Static proxy only: LLM-14 proves that one call site is sent a whole conversation history that grows every "
    "turn (in a loop, or per request in an instance or module-level list) with no visible bound, not how many "
    "tokens are re-sent, how long conversations last or what they cost; client logs with input tokens per turn "
    "are needed for that and no measurements are reported. Covered: Anthropic SDK messages, OpenAI chat "
    "completions/responses and Bedrock converse/converse_stream/invoke_model in Python. Not evaluated: "
    "LangChain/LangGraph/LlamaIndex/LiteLLM/Agents SDK memory, histories on other objects (state.messages, "
    "agent.memory), trimming done by a caller or in another file (including subclasses there), and object "
    "lifetimes (an instance created per conversation is still flagged). Not flagged (treated as bounded or "
    "unclear): slices, summaries, deque(maxlen=...), pop/remove/clear/del/rebinding, len() checks, histories "
    "passed to other functions or used in closures, loops with an explicit iteration budget (range(...), "
    "while n < limit), server-side state (previous_response_id, conversation, truncation, context_management) "
    "and requests that are not statically resolvable."
)


# --- scopes and bindings ---------------------------------------------------------------------


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


def _params(scope):
    if not isinstance(scope, (*FUNCTIONS, ast.Lambda)):
        return []
    args = scope.args
    return args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]


def _declared(scope, name):
    for node in _own_nodes(scope):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return "global" if isinstance(node, ast.Global) else "nonlocal"
    return None


def _binds(scope, name):
    """True if `name` is local to `scope` (parameter, assignment target, import, def, ...)."""
    if any(arg.arg == name for arg in _params(scope)):
        return True
    for node in _own_nodes(scope):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                return True
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)) and node.name == name:
            return True
    return False


def _binding_scope(ctx, scope, name):
    """The scope that owns `name` as seen from `scope` (module if unbound), or None if unclear."""
    first = True
    while scope is not ctx.tree:
        if isinstance(scope, ast.ClassDef):
            if first and _binds(scope, name):
                return None  # class-body names are not histories this check follows
        else:
            declared = _declared(scope, name)
            if declared == "global":
                return ctx.tree
            if declared is None and _binds(scope, name):
                return scope
        first = False
        scope = _scope_of(ctx, scope)
    return ctx.tree


# --- what a request sends ------------------------------------------------------------------


def _conversation(ctx, call):
    """The conversation argument of a recognised call, or None if absent or not statically readable."""
    keywords = call_keywords(ctx, call.node)
    if keywords is None or OPAQUE_KEYWORDS & keywords.keys():
        return None
    if call.provider == "bedrock" and not call.api.startswith("converse"):
        body = resolve(ctx, keywords.get("body")) if "body" in keywords else None
        if not (isinstance(body, ast.Call) and (ctx.dotted(body.func) or "") == "json.dumps" and len(body.args) == 1):
            return None
        request = dict_items(ctx, body.args[0])
        return None if request is None else request.get("messages")
    if call.api.startswith("responses"):
        return keywords.get("input")
    return keywords.get("messages")


def _is_history_ref(node):
    return isinstance(node, ast.Name) or (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name))


def _whole(ctx, node):
    """The history expression sent whole by `node`: `h`, `h + [...]`, `[sys, *h]`, `list(h)`, `h.copy()`, `h[:]`."""
    if node is None:
        return None
    if _is_history_ref(node):
        return node
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        whole_slice = node.slice.lower is None and node.slice.upper is None and node.slice.step is None
        return _whole(ctx, node.value) if whole_slice else None
    if isinstance(node, ast.Call) and not node.keywords:
        if (ctx.dotted(node.func) or "") == "list" and len(node.args) == 1:
            return _whole(ctx, node.args[0])
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy" and not node.args:
            return _whole(ctx, node.func.value)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _whole(ctx, node.left)
        if left is not None:
            return left
        if isinstance(node.left, (ast.List, ast.Tuple)) and not _starred(node.left):
            return _whole(ctx, node.right)
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        spread = [_whole(ctx, elt.value) for elt in node.elts if isinstance(elt, ast.Starred)]
        if len(spread) == 1 and spread[0] is not None:
            return spread[0]
    return None


def _starred(node):
    return any(isinstance(elt, ast.Starred) for elt in node.elts)


def _same(node, ref):
    """True if `node` is the same history expression as `ref` (a Name or `x.attr`)."""
    if isinstance(ref, ast.Name):
        return isinstance(node, ast.Name) and node.id == ref.id
    return (
        isinstance(node, ast.Attribute) and node.attr == ref.attr
        and isinstance(node.value, ast.Name) and node.value.id == ref.value.id
    )


def _grows(value, ref):
    """`h = h + [...]` or `h = [*h, ...]`."""
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return _same(value.left, ref)
    if isinstance(value, ast.List) and len(value.elts) > 1:
        head = value.elts[0]
        return isinstance(head, ast.Starred) and _same(head.value, ref)
    return False


# --- how each reference uses the history ---------------------------------------------------------


def _classify(ctx, ref, llm_ids):
    """(INIT, value) | (GROWTH, None) | (RESET, None) | (READ, None) for one reference to a history."""
    parent = ctx.parent(ref)
    if isinstance(parent, ast.Attribute) and parent.value is ref:
        grand = ctx.parent(parent)
        if isinstance(grand, ast.Call) and grand.func is parent:
            if parent.attr in GROWTH_METHODS:
                return GROWTH, None
            return (READ, None) if parent.attr in READ_METHODS else (RESET, None)  # pop/clear/remove/...
        return RESET, None  # `h.append` passed on, `h.attr = ...`: unclear
    if isinstance(parent, ast.Assign):
        if any(target is ref for target in parent.targets):
            return (GROWTH, None) if _grows(parent.value, ref) else (INIT, parent.value)
        if parent.value is ref:
            return RESET, None  # an alias may be trimmed under another name
    if isinstance(parent, ast.AnnAssign) and parent.target is ref:
        if parent.value is None:
            return READ, None
        return (GROWTH, None) if _grows(parent.value, ref) else (INIT, parent.value)
    if isinstance(parent, ast.AugAssign) and parent.target is ref:
        return (GROWTH, None) if isinstance(parent.op, ast.Add) else (RESET, None)
    if isinstance(parent, ast.Subscript) and parent.value is ref:
        return (READ, None) if isinstance(parent.ctx, ast.Load) else (RESET, None)  # h[i] = x, del h[:-n]
    if isinstance(getattr(ref, "ctx", None), (ast.Store, ast.Del)):
        return RESET, None  # tuple unpacking, loop/with/walrus targets, `del h`
    if isinstance(parent, ast.NamedExpr) and parent.value is ref:
        return RESET, None
    call = None
    if isinstance(parent, ast.Call) and any(arg is ref for arg in parent.args):
        call = parent
    elif isinstance(parent, ast.keyword):
        call = ctx.parent(parent)
    if isinstance(call, ast.Call):
        return _argument_use(ctx, call, llm_ids)
    return READ, None


def _argument_use(ctx, call, llm_ids):
    if id(call) in llm_ids:
        return READ, None
    dotted = ctx.dotted(call.func) or ""
    if dotted == "len":
        return (RESET, None) if isinstance(ctx.parent(call), ast.Compare) else (READ, None)  # a length bound
    if dotted in READ_FUNCTIONS:
        return READ, None
    if isinstance(call.func, ast.Attribute) and call.func.attr in LOG_METHODS:
        return READ, None
    return RESET, None  # trim(h), count_tokens(h), memory.save(h): may bound it


def _initial_kind(ctx, value):
    """'list' for an unbounded list-like initial value, 'bounded' for deque(maxlen=...), else None."""
    if value == PARAM:
        return PARAM
    value = resolve(ctx, value)
    if isinstance(value, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left, right = _initial_kind(ctx, value.left), _initial_kind(ctx, value.right)
        return "list" if left == right == "list" else None
    if not isinstance(value, ast.Call):
        return None
    dotted = ctx.dotted(value.func) or ""
    if dotted == "list" and len(value.args) <= 1 and not value.keywords:
        return "list"
    if dotted in DEQUES:
        maxlen = [kw.value for kw in value.keywords if kw.arg == "maxlen"] + value.args[1:2]
        unbounded = not maxlen or all(isinstance(m, ast.Constant) and m.value is None for m in maxlen)
        return "list" if unbounded else "bounded"
    if dotted in FACTORY_CALLS:
        factory = [kw.value for kw in value.keywords if kw.arg in ("default_factory", "factory")]
        if len(factory) == 1 and (ctx.dotted(factory[0]) or "") == "list":
            return "list"
    return None


# --- loops ------------------------------------------------------------------------------------


def _budgeted(ctx, node, depth=0):
    """True if iterating `node` runs an explicit number of steps: `range(...)` or a literal collection."""
    node = resolve(ctx, node) if depth < 6 else None
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not _starred(node)
    if isinstance(node, ast.Dict):
        return None not in node.keys
    if isinstance(node, ast.Call):
        dotted = ctx.dotted(node.func) or ""
        if dotted in BUDGETS:
            return True
        if dotted == "zip":  # stops at the shortest iterable
            return any(_budgeted(ctx, arg, depth + 1) for arg in node.args)
        if dotted in STATIC_ITERABLES and node.args:
            return _budgeted(ctx, node.args[0], depth + 1)
    return False


def _loop_bound(ctx, loop):
    """'budget' (an explicit iteration count: not flagged), 'data' (low confidence) or 'open' (medium)."""
    if isinstance(loop, ast.While):
        test = loop.test
        if isinstance(test, ast.Compare) and any(isinstance(op, ORDERING) for op in test.ops):
            return "budget"  # `while turn < max_turns`
        return "open"
    return "budget" if _budgeted(ctx, loop.iter) else "data"


def _loops(ctx, node, scope):
    """Loops around `node` in `scope` whose body (not `else`) contains it, innermost first."""
    found, child = [], node
    for ancestor in ctx.ancestors(node):
        if ancestor is scope or isinstance(ancestor, SCOPES):
            break
        if isinstance(ancestor, LOOPS) and any(child is stmt for stmt in ancestor.body):
            found.append(ancestor)
        child = ancestor
    return found


def _inside(ctx, node, container):
    return node is container or any(ancestor is container for ancestor in ctx.ancestors(node))


# --- rules ------------------------------------------------------------------------------------


def _name_refs(ctx, call, name):
    """(binding scope, [(ref, use, value)]) for a plain-name history, or None if the data flow is unclear."""
    owner = _binding_scope(ctx, _scope_of(ctx, call.node), name)
    if owner is None:
        return None
    refs = []
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Name) and node.id == name):
            continue
        scope = _scope_of(ctx, node)
        if _binding_scope(ctx, scope, name) is not owner:
            continue
        if owner is not ctx.tree and scope is not owner:
            return None  # used in a closure: not followed
        refs.append(node)
    return owner, refs


def _summarise(ctx, refs, llm_ids):
    uses = []
    for ref in refs:
        use, value = _classify(ctx, ref, llm_ids)
        uses.append((ref, use, value))
    return uses


def _loop_rule(ctx, call, ref_text, uses, inits, scope):
    """Rule L: initialised once outside a loop around the call, grown inside it, never trimmed."""
    if any(use == RESET for _, use, _ in uses) or len(inits) != 1:
        return None
    init_node, init_value = inits[0]
    kind = _initial_kind(ctx, init_value)
    if kind not in ("list", PARAM):
        return None
    growths = [ref for ref, use, _ in uses if use == GROWTH]
    for loop in _loops(ctx, call.node, scope):
        if init_node is not None and _inside(ctx, init_node, loop):
            continue
        inside = [ref for ref in growths if _inside(ctx, ref, loop)]
        bound = _loop_bound(ctx, loop)
        if not inside or bound == "budget":
            continue
        how = "`while` loop" if isinstance(loop, ast.While) else f"`for` loop over `{ast.unparse(loop.iter)}`"
        reason = (
            f"sends the whole `{ref_text}` on every iteration of the {how} (line {loop.lineno}); `{ref_text}` "
            f"grows inside the loop (line {inside[0].lineno}) and is never sliced, trimmed or summarised, so each "
            "turn re-sends every earlier turn and the history grows without bound"
        )
        weak = kind == PARAM or bound == "data"
        return reason, weak
    return None


def _global_rule(ctx, call, name, uses, inits):
    """Rule P (module): a module-level list grown inside a function and re-sent on every request."""
    if _scope_of(ctx, call.node) is ctx.tree or any(use == RESET for _, use, _ in uses) or len(inits) != 1:
        return None
    init_node, init_value = inits[0]
    if init_node is None or _scope_of(ctx, init_node) is not ctx.tree or _initial_kind(ctx, init_value) != "list":
        return None
    growths = [ref for ref, use, _ in uses if use == GROWTH and _scope_of(ctx, ref) is not ctx.tree]
    if not growths:
        return None
    where = ctx.qualname(growths[0])
    reason = (
        f"sends the whole module-level `{name}` (created at line {init_node.lineno}), which grows in {where}() "
        f"(line {growths[0].lineno}) on every request and is never trimmed; it lives as long as the process "
        "(for example a warm Lambda execution environment), so every call re-sends all earlier turns"
    )
    return reason, False


def _method_self(node):
    """Name of the instance parameter of a method, or None."""
    decorators = {ast.unparse(d) for d in node.decorator_list}
    if "staticmethod" in decorators:
        return None
    params = node.args.posonlyargs + node.args.args
    return params[0].arg if params else None


def _foreign_self(ctx, node, scope, cls):
    """True for `self.attr` inside a method of another class that does not subclass `cls`."""
    owner = ctx.parent(scope) if isinstance(scope, FUNCTIONS) else None
    if not isinstance(owner, ast.ClassDef) or owner is cls or _inside(ctx, owner, cls):
        return False
    if not (isinstance(node.value, ast.Name) and node.value.id == _method_self(scope)):
        return False
    return not any((ctx.dotted(base) or "").rsplit(".", 1)[-1] == cls.name for base in owner.bases)


def _self_rule(ctx, call, history, llm_ids):
    """Rule P (instance): `self.attr` created once per instance and grown by a method on every call."""
    method = _scope_of(ctx, call.node)
    cls = ctx.parent(method) if isinstance(method, FUNCTIONS) else None
    if not isinstance(cls, ast.ClassDef) or _method_self(method) != history.value.id:
        return None
    attr = history.attr
    methods = {id(m): m for m in cls.body if isinstance(m, FUNCTIONS) and _method_self(m)}
    uses, inits = [], []
    for stmt in cls.body:  # class attribute or dataclass/pydantic field
        target = getattr(stmt, "target", None) or (stmt.targets[0] if isinstance(stmt, ast.Assign) else None)
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(target, ast.Name) and target.id == attr:
            if stmt.value is not None:
                inits.append((stmt, stmt.value, None))
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Attribute) and node.attr == attr):
            continue
        scope = _scope_of(ctx, node)
        own = id(scope) in methods and isinstance(node.value, ast.Name) and node.value.id == _method_self(scope)
        if not own and _foreign_self(ctx, node, scope, cls):
            continue  # another class's own attribute of the same name
        if not own:
            if _inside(ctx, node, cls) and isinstance(node.value, ast.Name) and node.value.id == history.value.id:
                return None  # used in a nested function or lambda inside the class: not followed
            if _classify(ctx, node, llm_ids)[0] != READ:
                return None  # mutated or rebound through another object
            continue
        use, value = _classify(ctx, node, llm_ids)
        if use == RESET:
            return None
        if use == INIT:
            inits.append((node, value, scope))
        uses.append((node, use, scope))
    if len(inits) != 1:
        return None
    init_node, init_value, init_scope = inits[0]
    if init_scope is not None and init_scope.name not in INIT_METHODS:
        return None  # rebound outside the constructor (e.g. a reset()): treated as a bound
    if _initial_kind(ctx, init_value) != "list":
        return None
    growths = [(ref, scope) for ref, use, scope in uses if use == GROWTH and scope.name not in INIT_METHODS]
    if not growths:
        return None
    ref, scope = growths[0]
    where = "the class body" if init_scope is None else f"{init_scope.name}()"
    reason = (
        f"sends the whole `{ast.unparse(history)}`, an instance-level history created once in {where} "
        f"(line {init_node.lineno}) that grows in {cls.name}.{scope.name}() (line {ref.lineno}) on every call and "
        "is never trimmed, so every request re-sends all earlier turns of this instance"
    )
    return reason, False


def _reason(ctx, call, history, llm_ids):
    if isinstance(history, ast.Attribute):
        return _self_rule(ctx, call, history, llm_ids)
    name = history.id
    found = _name_refs(ctx, call, name)
    if found is None:
        return None
    owner, refs = found
    uses = _summarise(ctx, refs, llm_ids)
    inits = [(ref, value) for ref, use, value in uses if use == INIT]
    if owner is not ctx.tree and any(arg.arg == name for arg in _params(owner)):
        inits.append((None, PARAM))
    scope = _scope_of(ctx, call.node)
    return _loop_rule(ctx, call, name, uses, inits, scope) or (
        _global_rule(ctx, call, name, uses, inits) if owner is ctx.tree else None)


def run(ctx):
    calls = list(llm_calls(ctx))
    llm_ids = {id(call.node) for call in calls}
    hits = []
    for call in calls:
        history = _whole(ctx, _conversation(ctx, call))
        if history is None:
            continue
        found = _reason(ctx, call, history, llm_ids)
        if found is None:
            continue
        reason, weak = found
        provider = "OpenAI-compatible" if call.evidence == "chain" else PROVIDERS[call.provider]
        text = ast.unparse(history)
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}:{text}",
            summary=f"{provider} {call.receiver}.{call.api}() in {ctx.qualname(call.node)} {reason}.",
            confidence="low" if weak or call.evidence == "chain" else "medium",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
