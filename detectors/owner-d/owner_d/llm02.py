"""LLM-02: repeatable LLM requests with no response cache in front (static proxy).

Detector semantics version 1.0.0. Flags LLM calls whose request is fully static, or built only from a
small key (a route path parameter, or a parameter annotated `int`/`bool`/`Literal`/an in-file `Enum`), on
a path that runs repeatedly (a request handler or a function it reaches in the same file, a `while True`
loop, a Streamlit script) with no response cache visible in front: no memoization decorator, no
`cache`/`memo` names or lookup-then-return guard on the path, and no cache library imported by the file.
Explicit sampling (`temperature` > 0, `n` != 1) is treated as intended variety. Calls directly in a `for`
loop are LLM-11's. LLM calls are recognised by `llmcalls`. Python only; static only.
"""

from __future__ import annotations

import ast
import re
import sys

from . import static
from .llmcalls import _own_nodes, call_keywords, dict_items, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-02"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-02", "LLM02")

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}
DEPTH = 24

# Request arguments that configure a deployment rather than vary per request; they may be attributes
# or settings without making two requests different. Sampling (`temperature`, `n`) is judged separately.
CONFIG_KEYWORDS = frozenset({
    "model", "modelId", "max_tokens", "max_completion_tokens", "max_output_tokens", "top_p", "top_k", "stop",
    "stop_sequences", "timeout", "max_retries", "extra_headers", "metadata", "user", "safety_identifier",
    "service_tier", "store", "seed", "reasoning", "reasoning_effort", "thinking", "betas", "verbosity",
    "parallel_tool_calls", "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs", "accept",
    "contentType", "performanceConfig", "guardrailConfig", "guardrailIdentifier", "guardrailVersion", "trace",
    "requestMetadata", "prompt_cache_key", "prompt_cache_retention", "inferenceConfig", "temperature", "n", "stream",
})
PROMPT_KEYWORDS = ("messages", "system", "input", "instructions", "prompt", "body")

TEXT_CALLS = frozenset({"textwrap.dedent", "inspect.cleandoc", "json.dumps", "str", "dict"})
STR_METHODS = frozenset({
    "strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold", "format", "join", "replace",
})
MUTATORS = frozenset({
    "append", "extend", "insert", "update", "setdefault", "pop", "popitem", "clear", "remove", "add", "discard",
    "__setitem__",
})
SMALL_TYPES = frozenset({"int", "bool"})
ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})

ROUTE_METHODS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "route", "api_route", "websocket",
})
ROUTE_PARAMS = re.compile(r"\{([A-Za-z_]\w*)(?::[^}]*)?\}|<(?:[^:<>]+:)?([A-Za-z_]\w*)>")
LAMBDA_NAMES = frozenset({"lambda_handler"})
# Health/readiness probes call the model to check it answers; a cached response would defeat them.
PROBE = re.compile(r"health|liveness|readiness|livez|readyz|(?<![a-z])(?:ping|ready|probe)(?![a-z])", re.I)

# A response cache: names containing these (lowercased), after removing provider prompt-caching names.
CACHE_NAME = re.compile(r"cache|memo|session_state")
PROMPT_CACHE_NAMES = re.compile(
    r"cache_control|cachepoint|cache_point|prompt_cache_key|prompt_cache_retention|cache_read_input_tokens|"
    r"cache_creation_input_tokens|cachereadinputtokens|cachewriteinputtokens"
)
CACHE_LIBRARIES = frozenset({
    "cachetools", "redis", "valkey", "aioredis", "pymemcache", "memcache", "pylibmc", "aiocache", "diskcache",
    "gptcache", "joblib", "requests_cache", "hishel", "flask_caching", "fastapi_cache", "cashews", "dogpile",
    "beaker", "redisvl", "momento",
})
CACHE_DECORATOR_ROOTS = frozenset({"functools", "async_lru", "streamlit"})  # judged per decorated function
LOOKUPS = frozenset({"get", "hget", "mget", "getex", "hgetall", "exists", "hexists", "lookup"})

KINDS = {
    "route": "route handler",
    "lambda": "Lambda handler",
    "chat": "chat message handler",
    "view": "Django view",
    "loop": "`while True` loop",
    "streamlit": "Streamlit script",
}

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://aws.amazon.com/blogs/database/optimize-llm-response-costs-and-latency-with-effective-caching/",
    "https://www.getmaxim.ai/articles/top-7-performance-bottlenecks-in-llm-applications-and-how-to-overcome-them/",
    "https://python.langchain.com/docs/how_to/llm_caching/",
)
RECOMMENDATION = (
    "Put a response cache in front of repeatable requests, keyed on everything that changes the answer "
    "(model, prompt version, the key parameter): cachetools.TTLCache / cachetools.func.ttl_cache, Redis "
    "get-before-call with set(..., ex=seconds), st.cache_data(ttl=...) in Streamlit, or an API Gateway / "
    "CloudFront cache for GET endpoints. Give entries a TTL matched to how fresh the answer must be (see "
    "LLM-13), add a semantic cache only with a conservative similarity threshold, and track the hit rate. "
    "If varied answers are intended, set temperature above 0 to make that explicit."
)
LIMITATION = (
    "Static proxy only: LLM-02 proves that a request is repeatable (fully static, or built only from a route "
    "path parameter or an int/bool/Literal/Enum parameter) on a path that runs repeatedly, with no response "
    "cache visible in the file; not that identical requests actually recur, how often, or what a cache would "
    "hit. No measurements are reported; request logs with prompt hashes and cache hit telemetry are needed for "
    "that. Covered (Python): Anthropic, OpenAI and Bedrock non-streaming calls in route handlers, Lambda "
    "handlers, Chainlit on_message, Django views, functions they reach in the same file, while True loops "
    "and Streamlit scripts. Not flagged: dynamic prompts, prompts read from files or other modules, keys "
    "passed through helper parameters, explicit temperature > 0 or n != 1 (intended variety), calls directly "
    "in for loops or comprehensions (LLM-11), embedding and streaming calls, one-shot scripts and main(), "
    "health/readiness probe routes, test functions/classes, and any file that imports a cache library or "
    "configures an LLM cache. Caches in other modules, middleware, API Gateway or a CDN are not seen."
)


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _imports(ctx, root):
    return any(path.split(".")[0] == root for path in ctx.aliases.values())


def _is_embedding(ctx, call):
    """Bedrock invoke_model whose modelId mentions `embed` (copied from LLM-13)."""
    if call.provider != "bedrock" or not call.api.startswith("invoke_model"):
        return False
    keywords = call_keywords(ctx, call.node) or {}
    if "modelId" not in keywords:
        return False
    model = keywords["modelId"]
    nodes = [model, resolve(ctx, model)]
    return any("embed" in ast.unparse(node).lower() for node in nodes if node is not None)


def _cache_name(name):
    name = PROMPT_CACHE_NAMES.sub("", (name or "").lower())
    return bool(CACHE_NAME.search(name))


def _called_names(nodes):
    """Names called as `name(...)`, `self.name(...)` or `cls.name(...)`."""
    names = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls"):
            names.add(func.attr)
    return names


# --- is a cache visible? -------------------------------------------------------------------


def _file_cached(ctx):
    """True if the file imports a cache library or configures a global LLM cache."""
    for path in ctx.aliases.values():
        root = path.split(".")[0]
        if root in CACHE_LIBRARIES or (root not in CACHE_DECORATOR_ROOTS and _cache_name(path)):
            return True  # e.g. django.core.cache, langchain_community.cache, litellm.caching
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "set_llm_cache":
                return True
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and _cache_name(t.attr) for t in targets):
                return True  # langchain.llm_cache = ..., litellm.cache = Cache()
    return False


def _decorator_name(ctx, decorator):
    func = decorator.func if isinstance(decorator, ast.Call) else decorator
    return ctx.dotted(func) or ast.unparse(func)


def _guarded(ctx, scope, own):
    """True if a cache is visible in this scope: a memoizing decorator, a cache-named identifier, or a
    lookup-then-return guard (`if key in d: return`, `hit = r.get(k); if hit: return hit`)."""
    if isinstance(scope, FUNCS) and any(_cache_name(_decorator_name(ctx, d)) for d in scope.decorator_list):
        return True
    looked_up = set()
    for node in own:
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, (ast.arg, ast.keyword)):
            name = node.arg
        if name and _cache_name(name):
            return True
        if isinstance(node, ast.Assign) and _lookup(node.value, set()):
            looked_up.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for node in own:
        if isinstance(node, ast.If) and _lookup(node.test, looked_up):
            if any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node)):
                return True
    return False


def _lookup(test, looked_up):
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOOKUPS:
            return True
        if isinstance(node, ast.Name) and node.id in looked_up:
            return True
    return False


# --- does the code run repeatedly? ---------------------------------------------------------


def _route_path(decorator):
    if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
        return None
    if decorator.func.attr not in ROUTE_METHODS:
        return None
    path = decorator.args[0] if decorator.args else None
    for kw in decorator.keywords:
        if kw.arg in ("path", "rule"):
            path = kw.value
    path = _string(path)
    return path if path is not None and path.startswith("/") else None


def _entry_kind(ctx, function, django):
    for decorator in function.decorator_list:
        path = _route_path(decorator)
        if path is not None:
            return None if PROBE.search(path) or PROBE.search(function.name) else "route"
        dotted = _decorator_name(ctx, decorator)
        if dotted.split(".")[0] == "chainlit" and dotted.endswith(".on_message"):
            return "chat"
    params = [arg.arg for arg in function.args.posonlyargs + function.args.args]
    if function.name in LAMBDA_NAMES or params[:2] == ["event", "context"]:
        return "lambda"
    if django and (params[:1] == ["request"] or params[:2] == ["self", "request"]):
        return "view"
    return None


def _forever(node):
    """A `while True:` loop with no `break`: it repeats until the process stops."""
    if not isinstance(node, ast.While):
        return False
    if not (isinstance(node.test, ast.Constant) and node.test.value):
        return False
    return not any(isinstance(n, ast.Break) for n in ast.walk(node))


def _in_test(ctx, node):
    return any(
        isinstance(scope, FUNCS) and scope.name.startswith("test")
        or isinstance(scope, ast.ClassDef) and scope.name.startswith("Test")
        for scope in ctx.ancestors(node)
    )


class _Scan:
    """Functions on a repeated path, each with the entry point that reaches it."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.functions = [node for node in ast.walk(ctx.tree) if isinstance(node, FUNCS)]
        self.own = {id(node): list(_own_nodes(node)) for node in self.functions}
        self.own[id(ctx.tree)] = list(_own_nodes(ctx.tree))
        self.guarded = {
            id(scope) for scope in [ctx.tree, *self.functions] if _guarded(ctx, scope, self.own[id(scope)])
        }
        self.streamlit = _imports(ctx, "streamlit") and id(ctx.tree) not in self.guarded
        self.loops = [node for node in ast.walk(ctx.tree) if _forever(node)]
        self.reached = self._reach(_imports(ctx, "django"))

    def _scope(self, node):
        for ancestor in self.ctx.ancestors(node):
            if isinstance(ancestor, (*FUNCS, ast.Lambda, ast.ClassDef)):
                return ancestor
        return self.ctx.tree

    def _reach(self, django):
        by_name = {}
        for function in self.functions:
            by_name.setdefault(function.name, []).append(function)
        queue = []
        for function in self.functions:
            kind = _entry_kind(self.ctx, function, django)
            if kind:
                queue.append((function, (function.name, kind)))
        for loop in self.loops:
            if id(self._scope(loop)) in self.guarded:
                continue
            owner = self._scope(loop)
            entry = (getattr(owner, "name", "<module>"), "loop")
            for name in _called_names(ast.walk(loop)):
                queue.extend((function, entry) for function in by_name.get(name, []))
        if self.streamlit:
            for name in _called_names(self.own[id(self.ctx.tree)]):
                queue.extend((function, ("<module>", "streamlit")) for function in by_name.get(name, []))
        reached = {}
        while queue:
            function, entry = queue.pop(0)
            if id(function) in reached or id(function) in self.guarded:
                continue  # a cached function fronts everything it calls
            reached[id(function)] = entry
            for name in _called_names(self.own[id(function)]):
                queue.extend((callee, entry) for callee in by_name.get(name, []))
        return reached

    def repeated(self, node):
        """(scope, entry (name, kind)) if the call runs repeatedly, else None."""
        scope = self._scope(node)
        if not isinstance(scope, (*FUNCS, ast.Module)):
            return None  # lambdas and class bodies are not judged
        loop = self.ctx.enclosing_loop(node)
        if loop is not None and not isinstance(loop, ast.While):
            return None  # for loops and comprehensions: LLM-11
        if id(scope) in self.guarded:
            return None
        if id(scope) in self.reached:
            return scope, self.reached[id(scope)]
        if loop is not None and _forever(loop):
            return scope, (getattr(scope, "name", "<module>"), "loop")
        if scope is self.ctx.tree and self.streamlit:
            return scope, ("<module>", "streamlit")
        return None


# --- is the request repeatable? ------------------------------------------------------------


def _enums(ctx):
    names = set()
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.ClassDef):
            if any((ctx.dotted(base) or "").rsplit(".", 1)[-1] in ENUM_BASES for base in node.bases):
                names.add(node.name)
    return names


def _small_annotation(ctx, annotation, enums):
    if isinstance(annotation, ast.Name):
        return annotation.id in SMALL_TYPES or annotation.id in enums
    if isinstance(annotation, ast.Subscript):
        return (ctx.dotted(annotation.value) or "").rsplit(".", 1)[-1] == "Literal"
    return False


def _keys(ctx, function, enums):
    """Parameters that are small keys: route path parameters or int/bool/Literal/Enum annotations."""
    if not isinstance(function, FUNCS):
        return set()
    args = function.args
    params = {arg.arg: arg for arg in args.posonlyargs + args.args + args.kwonlyargs}
    keys = {name for name, arg in params.items() if _small_annotation(ctx, arg.annotation, enums)}
    for decorator in function.decorator_list:
        path = _route_path(decorator)
        if path is not None:
            for match in ROUTE_PARAMS.finditer(path):
                name = match.group(1) or match.group(2)
                if name in params:
                    keys.add(name)
    return keys


def _mutated(ctx):
    """Names mutated or rebound outside a plain assignment anywhere in the file."""
    names = set()
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
            if isinstance(node.func.value, ast.Name):
                names.add(node.func.value.id)
        elif isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                names.add(node.value.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


class _Request:
    """Decides whether request values are fixed given a function's small keys."""

    def __init__(self, ctx, keys, enums, mutated):
        self.ctx, self.keys, self.enums, self.mutated = ctx, keys, enums, mutated
        self.used = set()

    def fixed(self, node, depth=0):
        if node is None or depth > DEPTH:
            return False
        if isinstance(node, ast.Name):
            if node.id in self.keys:
                self.used.add(node.id)
                return True
            if node.id in self.mutated:
                return False
            value = resolve(self.ctx, node)
            return value is not None and not isinstance(value, ast.Name) and self.fixed(value, depth + 1)
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.JoinedStr):
            return all(self.fixed(value, depth + 1) for value in node.values)
        if isinstance(node, ast.FormattedValue):
            return self.fixed(node.value, depth + 1) and (
                node.format_spec is None or self.fixed(node.format_spec, depth + 1)
            )
        if isinstance(node, ast.BinOp):
            return self.fixed(node.left, depth + 1) and self.fixed(node.right, depth + 1)
        if isinstance(node, ast.UnaryOp):
            return self.fixed(node.operand, depth + 1)
        if isinstance(node, ast.IfExp):
            return all(self.fixed(n, depth + 1) for n in (node.test, node.body, node.orelse))
        if isinstance(node, ast.Dict):
            keys = [key for key in node.keys if key is not None]
            return all(self.fixed(n, depth + 1) for n in keys + node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self.fixed(n, depth + 1) for n in node.elts)
        if isinstance(node, ast.Starred):
            return self.fixed(node.value, depth + 1)
        if isinstance(node, ast.Subscript):  # TEMPLATES[topic]
            return self.fixed(node.value, depth + 1) and self.fixed(node.slice, depth + 1)
        if isinstance(node, ast.Attribute):  # topic.value, Topic.BILLING
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in self.keys:
                self.used.add(base.id)
                return True
            return isinstance(base, ast.Name) and base.id in self.enums
        if isinstance(node, ast.Call):
            return self._fixed_call(node, depth)
        return False

    def _fixed_call(self, node, depth):
        arguments = node.args + [kw.value for kw in node.keywords]
        if (self.ctx.dotted(node.func) or "") in TEXT_CALLS:
            return all(self.fixed(arg, depth + 1) for arg in arguments)
        if isinstance(node.func, ast.Attribute) and node.func.attr in STR_METHODS:
            return self.fixed(node.func.value, depth + 1) and all(self.fixed(arg, depth + 1) for arg in arguments)
        return False


def _number(ctx, node):
    value = resolve(ctx, node)
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        inner = _number(ctx, value.operand)
        return None if inner is None else -inner
    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
        return value.value
    return None


def _sampling(ctx, call, keywords):
    """'zero' (temperature=0), 'default' (unset) or None (variety intended, or unknown)."""
    temperature = keywords.get("temperature")
    sources = []
    if call.provider == "bedrock":
        if "inferenceConfig" in keywords:
            sources.append(keywords["inferenceConfig"])
        if call.api.startswith("invoke_model"):
            body = resolve(ctx, keywords.get("body"))
            if isinstance(body, ast.Call) and body.args:
                body = body.args[0]  # json.dumps({...})
            request = dict_items(ctx, body) if body is not None else None
            if request is None:
                return None
            temperature = request.get("temperature", temperature)
            sources += [request[key] for key in ("textGenerationConfig", "inferenceConfig") if key in request]
    for source in sources:
        config = dict_items(ctx, source)
        if config is None:
            return None
        temperature = config.get("temperature", temperature)
    if "n" in keywords and _number(ctx, keywords["n"]) != 1:
        return None
    if temperature is None:
        return "default"
    value = _number(ctx, temperature)
    if value is None:
        return None
    return "zero" if value == 0 else None


def _summary(call, scope, entry, keys, sampling):
    target = f"{PROVIDERS[call.provider]} {call.receiver}.{call.api}()"
    if call.evidence == "chain":
        target = f"OpenAI-compatible {call.receiver}.{call.api}()"
    name, kind = entry
    own = getattr(scope, "name", "<module>")
    if kind == "streamlit":
        where = "This Streamlit script reruns on every interaction and" if own == name else (
            f"{own}(), called on every rerun of this Streamlit script,"
        )
    elif kind == "loop":
        where = f"A `while True` loop in {name}()" if own == name else (
            f"{own}(), called from a `while True` loop in {name}(),"
        )
    else:
        where = f"{KINDS[kind].capitalize()} {name}()"
        if own != name:
            where = f"{own}(), reached from {KINDS[kind]} {name}(),"
    built = "is fully static" if not keys else "is built only from " + ", ".join(sorted(keys))
    sampled = "temperature=0" if sampling == "zero" else "temperature not set"
    return (
        f"{where} sends {target} a repeatable request (the prompt {built}; {sampled}) with no response cache "
        "in front: each repeat of the same request is processed and billed again."
    )


def run(ctx):
    if _file_cached(ctx):
        return []
    calls = [call for call in llm_calls(ctx) if "stream" not in call.api and not _is_embedding(ctx, call)]
    if not calls:
        return []
    scan = _Scan(ctx)
    enums = _enums(ctx)
    mutated = _mutated(ctx)
    hits = []
    for call in calls:
        if _in_test(ctx, call.node):
            continue
        repeated = scan.repeated(call.node)
        if repeated is None:
            continue
        scope, entry = repeated
        keywords = call_keywords(ctx, call.node)
        if keywords is None or call.node.args or not any(key in keywords for key in PROMPT_KEYWORDS):
            continue
        stream = keywords.get("stream")
        if stream is not None and not (isinstance(stream, ast.Constant) and stream.value is False):
            continue
        request = _Request(ctx, _keys(ctx, scope, enums), enums, mutated)
        if not all(request.fixed(value) for key, value in keywords.items() if key not in CONFIG_KEYWORDS):
            continue
        sampling = _sampling(ctx, call, keywords)
        if sampling is None:
            continue
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.provider}.{call.api}",
            summary=_summary(call, scope, entry, request.used, sampling),
            confidence="medium" if sampling == "zero" and call.evidence != "chain" else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
