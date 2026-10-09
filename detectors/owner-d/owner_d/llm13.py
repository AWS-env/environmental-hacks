"""LLM-13: LLM responses cached without TTL or invalidation (static proxy).

Detector semantics version 1.0.0. Flags four cache shapes whose entries never expire and are never
invalidated in the file: memoization decorators without TTL (`functools.lru_cache`/`cache`,
`cachetools` non-TTL caches, `alru_cache` without `ttl`) on functions that call an LLM; LangChain
LLM caches without TTL; Redis writes of LLM results without expiry; module-level dict caches of LLM
results that nothing evicts. LLM calls are recognised by `llmcalls` (Anthropic, OpenAI, Bedrock);
embedding calls do not count. Anything that cannot be resolved statically is not flagged. Python
only; static only.
"""

from __future__ import annotations

import ast
import re
import sys

from . import static
from .llmcalls import _own_nodes, call_keywords, llm_calls, resolve
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-13"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-13", "LLM13")

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "bedrock": "Bedrock"}

# Memoization decorators that never expire entries (size-based eviction is not expiry).
NO_TTL_DECORATORS = frozenset({
    "functools.lru_cache", "functools.cache",
    *(f"cachetools.func.{name}" for name in ("lru_cache", "lfu_cache", "fifo_cache", "rr_cache", "mru_cache")),
})
NO_TTL_CACHETOOLS = frozenset(
    f"cachetools.{name}" for name in ("Cache", "LRUCache", "LFUCache", "FIFOCache", "RRCache", "MRUCache")
)
# A memoized function with such a parameter is keyed on a time bucket (the `ttl_hash` idiom).
TIME_BUCKET_PARAM = re.compile(r"ttl|expir|time|stamp|version|epoch|bucket", re.I)

LANGCHAIN_ROOTS = frozenset({"langchain", "langchain_community", "langchain_core", "langchain_redis"})
LANGCHAIN_NO_TTL = frozenset({"InMemoryCache", "SQLiteCache", "SQLAlchemyCache", "SQLAlchemyMd5Cache"})
# Backend -> (TTL parameter, its positional index or None if keyword-only); default TTL is None.
LANGCHAIN_TTL = {
    "RedisCache": ("ttl", None), "AsyncRedisCache": ("ttl", None), "UpstashRedisCache": ("ttl", None),
    "CassandraCache": ("ttl_seconds", 3), "CassandraSemanticCache": ("ttl_seconds", 6),
}
LANGCHAIN_REDIS_TTL = {"RedisCache": ("ttl", 1), "RedisSemanticCache": ("ttl", 3)}

REDIS_ROOTS = frozenset({"redis", "valkey", "aioredis"})
REDIS_FACTORIES = frozenset({"Redis", "StrictRedis", "RedisCluster", "Valkey", "ValkeyCluster", "from_url"})
REDIS_WRITES = frozenset({"set", "setnx", "mset", "msetnx", "hset", "hmset"})
REDIS_READS = frozenset({"get", "mget", "getex", "hget", "hmget", "hgetall", "exists", "hexists"})
REDIS_EXPIRY_KEYWORDS = ("ex", "px", "exat", "pxat", "keepttl")
REDIS_EXPIRY_METHODS = frozenset({
    "expire", "pexpire", "expireat", "pexpireat", "hexpire", "hpexpire", "hexpireat", "hpexpireat",
})

DICT_FACTORIES = frozenset({"dict", "collections.OrderedDict"})
DICT_READS = frozenset({"get", "keys", "values", "items", "setdefault", "__contains__", "__getitem__", "copy"})
DICT_EVICTIONS = frozenset({"pop", "popitem", "clear", "__delitem__"})
CLOCKS = frozenset({"time.time", "time.time_ns", "time.monotonic", "time.monotonic_ns"})

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
    "https://aws.amazon.com/blogs/database/optimize-llm-response-costs-and-latency-with-effective-caching/",
    "https://docs.python.org/3/library/functools.html#functools.lru_cache",
    "https://redis.io/docs/latest/commands/set/",
    "https://python.langchain.com/docs/how_to/llm_caching/",
)
RECOMMENDATION = (
    "Give cached LLM responses a TTL matched to how often the underlying data changes: cachetools.TTLCache "
    "or cachetools.func.ttl_cache instead of lru_cache, alru_cache(ttl=...), Redis set(..., ex=seconds), a "
    "LangChain cache backend with ttl. Invalidate entries when the source data, prompt or model version "
    "changes (include them in the cache key), and track the cache hit rate."
)
LIMITATION = (
    "Static proxy only: LLM-13 proves that a cache stores LLM responses (or values computed from them) with "
    "no expiry and no invalidation in the file, not that the cached data goes stale or how often it is hit; "
    "no measurements are reported. Covered (Python): functools/cachetools/async_lru memoization of functions "
    "that call Anthropic, OpenAI or Bedrock (directly or through a function in the same file), LangChain "
    "set_llm_cache/llm_cache/cache= backends, Redis/Valkey writes on clients created in the file, and "
    "module-level dict caches. Not flagged: embedding calls, provider prompt caching, Redis clients and cache "
    "objects defined elsewhere, **kwargs, instance-attribute caches, files that clear the cache, caches keyed "
    "on a time bucket or written next to a clock read, and test functions/classes. LiteLLM, GPTCache and "
    "diskcache are not evaluated."
)


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _is_embedding(ctx, call):
    """Bedrock invoke_model whose modelId mentions `embed` (`EMBED_MODEL`, `os.getenv(..., "...-embed-...")`)."""
    if call.provider != "bedrock" or not call.api.startswith("invoke_model"):
        return False
    keywords = call_keywords(ctx, call.node) or {}
    if "modelId" not in keywords:
        return False
    model = keywords["modelId"]
    nodes = [model, resolve(ctx, model)]
    return any("embed" in ast.unparse(node).lower() for node in nodes if node is not None)


def _calls_named(node, names):
    """True if `node` calls `name(...)` or `self.name(...)`/`cls.name(...)` for a name in `names`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in names
    return (
        isinstance(func, ast.Attribute) and func.attr in names
        and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls")
    )


class _Scan:
    """LLM calls in one file and the functions that reach them."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = {id(call.node): call for call in llm_calls(ctx) if not _is_embedding(ctx, call)}
        functions = [node for node in ast.walk(ctx.tree) if isinstance(node, FUNCS)]
        self.own = {id(node): list(_own_nodes(node)) for node in functions}
        self.own[id(ctx.tree)] = list(_own_nodes(ctx.tree))
        reaching = {node.name for node in functions if self.direct(node)}
        changed = True
        while changed:  # functions that call an LLM-calling function in this file
            changed = False
            for node in functions:
                if node.name not in reaching and any(_calls_named(n, reaching) for n in self.own[id(node)]):
                    reaching.add(node.name)
                    changed = True
        self.llm_functions = reaching

    def direct(self, scope):
        return [self.calls[id(n)] for n in self.own[id(scope)] if id(n) in self.calls]

    def calls_llm(self, scope):
        return bool(self.direct(scope)) or any(_calls_named(n, self.llm_functions) for n in self.own[id(scope)])

    def source(self, scope):
        """(description of the LLM source, confidence) for a function that calls an LLM."""
        direct = self.direct(scope)
        if not direct:
            helper = next(n for n in self.own[id(scope)] if _calls_named(n, self.llm_functions))
            name = helper.func.id if isinstance(helper.func, ast.Name) else helper.func.attr
            return f"LLM responses (through {name}())", "medium"
        first = direct[0]
        provider = "OpenAI-compatible" if first.evidence == "chain" else PROVIDERS[first.provider]
        confidence = "low" if all(call.evidence == "chain" for call in direct) else "medium"
        return f"{provider} {first.api} responses", confidence

    def derived(self, node, tainted):
        for n in ast.walk(node):
            if id(n) in self.calls or _calls_named(n, self.llm_functions):
                return True
            if isinstance(n, ast.Name) and n.id in tainted:
                return True
        return False

    def tainted_names(self, scope):
        """Names in `scope` assigned (directly or through other names) from an LLM call result."""
        tainted = set()
        changed = True
        while changed:
            changed = False
            for node in self.own[id(scope)]:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                    targets, value = [node.target], node.value
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    targets, value = [node.target], node.iter
                elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                    targets, value = [node.optional_vars], node.context_expr
                else:
                    continue
                if not self.derived(value, tainted):
                    continue
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and name.id not in tainted:
                            tainted.add(name.id)
                            changed = True
        return tainted

    def reads_clock(self, scope):
        for node in self.own[id(scope)]:
            if isinstance(node, ast.Call):
                dotted = self.ctx.dotted(node.func) or ""
                if dotted in CLOCKS or ("datetime" in dotted and dotted.rsplit(".", 1)[-1] in ("now", "utcnow")):
                    return True
        return False


# --- 1. memoized LLM functions -------------------------------------------------------------


def _memo_label(ctx, decorator):
    """Dotted name of a memoization decorator without TTL, or None."""
    call = decorator if isinstance(decorator, ast.Call) else None
    name = ctx.dotted(call.func if call else decorator) or ""
    keywords = call_keywords(ctx, call) if call else {}
    if keywords is None:
        return None
    if name in NO_TTL_DECORATORS:
        size = keywords.get("maxsize", call.args[0] if call and call.args else None)
        size = resolve(ctx, size) if size is not None else None
        return None if isinstance(size, ast.Constant) and size.value == 0 else name  # maxsize=0 caches nothing
    if name == "async_lru.alru_cache":
        return name if _is_none(keywords.get("ttl", ast.Constant(None))) else None
    if name == "cachetools.cached" and call:
        cache = resolve(ctx, call.args[0] if call.args else keywords.get("cache"))
        if isinstance(cache, ast.Dict) and not cache.keys:
            return name
        if isinstance(cache, ast.Call) and (ctx.dotted(cache.func) or "") in NO_TTL_CACHETOOLS:
            return name
    return None


def _cleared(ctx, names):
    """True if `<name>.cache_clear()` / `<name>.clear()` (or pop) is called anywhere in the file."""
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("cache_clear", "clear", "pop", "popitem"):
            continue
        receiver = node.func.value
        name = receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", None)
        if name in names:
            return True
    return False


def _memoized(ctx, scan):
    for function in ast.walk(ctx.tree):
        if not isinstance(function, FUNCS) or not scan.calls_llm(function):
            continue
        args = function.args
        params = args.posonlyargs + args.args + args.kwonlyargs
        if any(TIME_BUCKET_PARAM.search(arg.arg) for arg in params):
            continue
        for decorator in function.decorator_list:
            label = _memo_label(ctx, decorator)
            if label is None:
                continue
            names = {function.name}
            if isinstance(decorator, ast.Call):  # cachetools.cached(CACHE) cleared via CACHE.clear()
                values = decorator.args + [kw.value for kw in decorator.keywords]
                names.update(value.id for value in values if isinstance(value, ast.Name))
            if _cleared(ctx, names):
                continue
            what, confidence = scan.source(function)
            yield Hit(
                node=decorator,
                anchor=f"{ctx.qualname(decorator)}:memoize:{label}",
                summary=(
                    f"{function.name}() caches {what} with @{ast.unparse(decorator)}: entries never expire and "
                    "nothing in the file invalidates them, so an outdated answer is served as fresh for the "
                    "life of the process."
                ),
                confidence=confidence,
            )


# --- 2. LangChain LLM caches ---------------------------------------------------------------


def _langchain_backend(ctx, node):
    """(backend name, reason) for a LangChain cache built without TTL, else None."""
    node = resolve(ctx, node)
    if not isinstance(node, ast.Call):
        return None
    dotted = ctx.dotted(node.func) or ""
    root, name = dotted.split(".")[0], dotted.rsplit(".", 1)[-1]
    if root not in LANGCHAIN_ROOTS:
        return None
    if name in LANGCHAIN_NO_TTL or (name == "RedisSemanticCache" and root != "langchain_redis"):
        return name, "has no TTL support"
    spec = (LANGCHAIN_REDIS_TTL if root == "langchain_redis" else LANGCHAIN_TTL).get(name)
    keywords = call_keywords(ctx, node)
    if spec is None or keywords is None:
        return None
    param, position = spec
    if position is not None and len(node.args) > position:
        value = node.args[position]
    else:
        value = keywords.get(param, ast.Constant(None))
    return (name, f"is created without {param}") if _is_none(resolve(ctx, value)) else None


def _langchain(ctx):
    for node in ast.walk(ctx.tree):
        backend = None
        if isinstance(node, ast.Call):
            dotted = ctx.dotted(node.func) or ""
            if dotted.split(".")[0] in LANGCHAIN_ROOTS and dotted.endswith(".set_llm_cache") and node.args:
                backend = _langchain_backend(ctx, node.args[0])
            else:
                value = next((kw.value for kw in node.keywords if kw.arg == "cache"), None)
                backend = _langchain_backend(ctx, value) if value is not None else None
        elif isinstance(node, ast.Assign) and any(ctx.dotted(t) == "langchain.llm_cache" for t in node.targets):
            backend = _langchain_backend(ctx, node.value)
        if backend is None:
            continue
        name, reason = backend
        yield Hit(
            node=node,
            anchor=f"{ctx.qualname(node)}:langchain:{name}",
            summary=(
                f"LangChain LLM cache {name} {reason}: cached model responses never expire, so an outdated "
                "answer is served as fresh until the cache is cleared by hand."
            ),
            confidence="medium",
        )


# --- 3. Redis writes -----------------------------------------------------------------------


def _attribute_values(ctx):
    values = {}
    for node in ast.walk(ctx.tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Attribute):
                    values.setdefault(ast.unparse(target), []).append(node.value)
    return values


def _is_redis(ctx, node, attributes, depth=0):
    """True if `node` is (or names) a Redis/Valkey client or pipeline created in this file."""
    if depth > 4 or node is None:
        return False
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Attribute):
        found = attributes.get(ast.unparse(node))
        if found:
            return all(_is_redis(ctx, value, attributes, depth + 1) for value in found)
    if isinstance(node, ast.Name):
        value = resolve(ctx, node)
        return value is not None and _is_redis(ctx, value, attributes, depth + 1)
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr == "pipeline":
        return _is_redis(ctx, node.func.value, attributes, depth + 1)
    parts = (ctx.dotted(node.func) or "").split(".")
    return parts[0] in REDIS_ROOTS and parts[-1] in REDIS_FACTORIES


def _redis_values(call):
    """Argument nodes that hold the stored value(s) of a Redis write."""
    method = call.func.attr
    start = 0 if method in ("mset", "msetnx") else 1
    return call.args[start:] + [kw.value for kw in call.keywords if kw.arg in ("value", "mapping", "items")]


def _expires(ctx, call):
    """True/False whether a Redis `set` sets an expiry, None if unknown."""
    keywords = call_keywords(ctx, call)
    if keywords is None:
        return None
    if call.func.attr != "set":
        return False
    if len(call.args) >= 3 and not _is_none(call.args[2]):
        return True
    return any(name in keywords and not _is_none(keywords[name]) for name in REDIS_EXPIRY_KEYWORDS)


def _redis(ctx, scan):
    attributes = _attribute_values(ctx)
    for scope in [ctx.tree] + [node for node in ast.walk(ctx.tree) if isinstance(node, FUNCS)]:
        own = scan.own[id(scope)]
        writes = [
            node for node in own
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REDIS_WRITES
        ]
        if not writes or not scan.calls_llm(scope) or scan.reads_clock(scope):
            continue
        if any(isinstance(n, ast.Call) and getattr(n.func, "attr", None) in REDIS_EXPIRY_METHODS for n in own):
            continue
        reads = [
            node for node in own
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REDIS_READS
        ]
        if not any(_is_redis(ctx, node.func.value, attributes) for node in reads):
            continue  # a cache reads before it writes; a write-only store is not a response cache
        tainted = scan.tainted_names(scope)
        for call in writes:
            if _expires(ctx, call) is not False or not _is_redis(ctx, call.func.value, attributes):
                continue
            if not any(scan.derived(value, tainted) for value in _redis_values(call)):
                continue
            what, confidence = scan.source(scope)
            target = f"{ast.unparse(call.func.value)}.{call.func.attr}()"
            yield Hit(
                node=call,
                anchor=f"{ctx.qualname(call)}:redis.{call.func.attr}",
                summary=(
                    f"Redis {target} stores {what} with no ex/px/exat/pxat and no expire in the same function: "
                    "the entry never expires, so an outdated answer is served as fresh until it is deleted."
                ),
                confidence=confidence,
            )


# --- 4. module-level dict caches -----------------------------------------------------------


def _empty_dict(ctx, node):
    if isinstance(node, ast.Dict):
        return not node.keys
    return (
        isinstance(node, ast.Call) and not node.args and not node.keywords
        and (ctx.dotted(node.func) or "") in DICT_FACTORIES
    )


def _dict_use(ctx, name_node):
    """'read', 'write', 'evict' or None (unknown use) for a use of a module-level dict name."""
    parent = ctx.parent(name_node)
    if isinstance(parent, ast.Subscript) and parent.value is name_node:
        return {ast.Load: "read", ast.Store: "write"}.get(type(parent.ctx), "evict")
    if isinstance(parent, ast.Compare) and name_node in parent.comparators:
        index = parent.comparators.index(name_node)
        return "read" if isinstance(parent.ops[index], (ast.In, ast.NotIn)) else None
    if isinstance(parent, ast.Attribute) and parent.value is name_node:
        if parent.attr in DICT_EVICTIONS:
            return "evict"
        return "read" if parent.attr in DICT_READS else None
    return None


def _cache_dicts(ctx):
    """Names of module-level empty dicts used only through reads/writes, never evicted or rebound."""
    candidates = {}
    for node in ctx.tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if _empty_dict(ctx, node.value):
                candidates.setdefault(node.targets[0].id, []).append(node.targets[0])
    names = set()
    for name, bindings in candidates.items():
        if len(bindings) != 1:
            continue
        uses = [
            _dict_use(ctx, node) for node in ast.walk(ctx.tree)
            if isinstance(node, ast.Name) and node.id == name and node is not bindings[0]
        ]
        if uses and all(use in ("read", "write") for use in uses):
            names.add(name)
    return names


def _dicts(ctx, scan):
    names = _cache_dicts(ctx)
    if not names:
        return
    for function in ast.walk(ctx.tree):
        if not isinstance(function, FUNCS) or not scan.calls_llm(function) or scan.reads_clock(function):
            continue
        own = scan.own[id(function)]
        read = {n.id for n in own if isinstance(n, ast.Name) and n.id in names and _dict_use(ctx, n) == "read"}
        if not read:
            continue
        tainted = scan.tainted_names(function)
        for node in own:
            if not isinstance(node, ast.Assign) or not scan.derived(node.value, tainted):
                continue
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in read:
                    what, confidence = scan.source(function)
                    yield Hit(
                        node=node,
                        anchor=f"{ctx.qualname(node)}:dict:{target.value.id}",
                        summary=(
                            f"{function.name}() caches {what} in the module-level dict {target.value.id}, which "
                            "nothing in the file evicts or timestamps: entries never expire, so an outdated "
                            "answer is served as fresh for the life of the process."
                        ),
                        confidence=confidence,
                    )


def _in_test(ctx, node):
    """True inside a `test*` function or `Test*` class: test caches are not serving answers."""
    return any(
        isinstance(scope, FUNCS) and scope.name.startswith("test")
        or isinstance(scope, ast.ClassDef) and scope.name.startswith("Test")
        for scope in ctx.ancestors(node)
    )


def run(ctx):
    scan = _Scan(ctx)
    hits = list(_langchain(ctx))
    if scan.calls:
        hits += [*_memoized(ctx, scan), *_redis(ctx, scan), *_dicts(ctx, scan)]
    return [hit for hit in hits if not _in_test(ctx, hit.node)]


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
