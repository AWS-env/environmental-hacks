"""Recognise LLM API calls in a parsed Python file (shared by the LLM checks).

Nothing is imported or executed. Recognised call forms:

- AWS Bedrock Runtime (boto3/aioboto3): `converse`, `converse_stream`, `invoke_model`,
  `invoke_model_with_response_stream` on a client created with `.client("bedrock-runtime")`
  (or annotated `BedrockRuntimeClient`); on other receivers only when Bedrock's own
  `modelId=` keyword is passed.
- OpenAI SDK: `chat.completions.create/parse/stream` (also under `beta`), `responses.create/
  parse/stream`, legacy `completions.create` and v0 `openai.ChatCompletion.create`. The
  `chat.completions` chain is also accepted on unknown receivers, because OpenAI-compatible
  SDKs share it.
- Anthropic SDK: `messages.create/stream/parse` (also under `beta`) on a known client.

`evidence` says how the provider was established: `client` (constructor, factory or type
annotation in this file), `signature` (Bedrock's `modelId=` keyword) or `chain` (method chain only).
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from dataclasses import dataclass

OPENAI_CLIENTS = frozenset(
    f"openai.{name}" for name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI", "Client", "AsyncClient")
)
ANTHROPIC_CLIENTS = frozenset(
    f"anthropic.{name}"
    for name in (
        "Anthropic", "AsyncAnthropic", "AnthropicBedrock", "AsyncAnthropicBedrock",
        "AnthropicVertex", "AsyncAnthropicVertex", "Client", "AsyncClient",
    )
)
BEDROCK_SERVICE = "bedrock-runtime"
BEDROCK_CLIENT_TYPE = "BedrockRuntimeClient"  # mypy-boto3 / types-boto3 annotation

BEDROCK_APIS = ("converse", "converse_stream", "invoke_model", "invoke_model_with_response_stream")
# Longest chains first so `beta.chat.completions.parse` wins over `chat.completions.parse`.
CHAIN_APIS = (
    ("openai", ("beta", "chat", "completions", "parse")),
    ("openai", ("beta", "chat", "completions", "stream")),
    ("openai", ("chat", "completions", "create")),
    ("openai", ("chat", "completions", "parse")),
    ("openai", ("chat", "completions", "stream")),
    ("anthropic", ("beta", "messages", "create")),
    ("anthropic", ("beta", "messages", "stream")),
    ("anthropic", ("beta", "messages", "parse")),
    ("openai", ("ChatCompletion", "create")),
    ("openai", ("ChatCompletion", "acreate")),
    ("openai", ("responses", "create")),
    ("openai", ("responses", "parse")),
    ("openai", ("responses", "stream")),
    ("openai", ("completions", "create")),
    ("anthropic", ("messages", "create")),
    ("anthropic", ("messages", "stream")),
    ("anthropic", ("messages", "parse")),
)
# Chains specific enough to identify an OpenAI-compatible SDK without knowing the client.
OPENAI_COMPATIBLE_CHAINS = frozenset(api for _, api in CHAIN_APIS if api[-3:-1] == ("chat", "completions"))

OTHER = "other"  # client built by another imported class; never matched to a provider
OWN_SDK_ROOTS = frozenset({"openai", "anthropic", "boto3", "aioboto3", "aiobotocore", "botocore"})

PASSTHROUGH_ATTRS = frozenset({"with_raw_response", "with_streaming_response"})
PASSTHROUGH_CALLS = frozenset({"with_options"})
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
RESOLVE_DEPTH = 4


@dataclass(frozen=True)
class LLMCall:
    node: ast.Call
    provider: str  # bedrock | openai | anthropic
    api: str  # e.g. "converse", "chat.completions.create"
    receiver: str  # source text of the client expression
    evidence: str  # client | signature | chain


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _await(node):
    return node.value if isinstance(node, ast.Await) else node


def _is_passthrough_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PASSTHROUGH_CALLS


def _unwrap(node):
    """Strip `with_options(...)` / `with_raw_response` wrappers from a client expression."""
    while True:
        if isinstance(node, ast.Attribute) and node.attr in PASSTHROUGH_ATTRS:
            node = node.value
        elif _is_passthrough_call(node):
            node = node.func.value
        else:
            return node


def _kind_of_type(dotted):
    if dotted in OPENAI_CLIENTS:
        return "openai"
    if dotted in ANTHROPIC_CLIENTS:
        return "anthropic"
    if dotted.rsplit(".", 1)[-1] == BEDROCK_CLIENT_TYPE:
        return "bedrock"
    return None


def _constructed_kind(ctx, node, factories):
    """Client kind created by a call expression, or None."""
    node = _await(node)
    if not isinstance(node, ast.Call):
        return None
    kind = _kind_of_type(ctx.dotted(node.func) or "")
    if kind and kind != "bedrock":
        return kind
    if isinstance(node.func, ast.Attribute) and node.func.attr in ("client", "create_client"):
        service = node.args[0] if node.args else None
        for kw in node.keywords:
            if kw.arg == "service_name":
                service = kw.value
        if _string(service) == BEDROCK_SERVICE:
            return "bedrock"
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
    if name in factories:
        return factories[name]
    base = node.func
    while isinstance(base, ast.Attribute):
        base = base.value
    root = (ctx.dotted(node.func) or "").split(".")[0]
    imported = isinstance(base, ast.Name) and base.id in ctx.aliases
    if name and name[0].isupper() and imported and root not in OWN_SDK_ROOTS:
        return OTHER  # e.g. Groq(), Together(), ZhipuAI(): an SDK with its own defaults
    return None


def _annotated_kind(ctx, annotation):
    if annotation is None:
        return None
    if isinstance(annotation, ast.BinOp):  # `OpenAI | None`
        return _annotated_kind(ctx, annotation.left) or _annotated_kind(ctx, annotation.right)
    if isinstance(annotation, ast.Subscript):  # `Optional[OpenAI]`
        return _annotated_kind(ctx, annotation.slice)
    return _kind_of_type(ctx.dotted(annotation) or _string(annotation) or "")


def _factories(ctx):
    """Functions in this file that return one kind of client (by annotation or every `return`)."""
    factories = {}
    for node in ast.walk(ctx.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kind = _annotated_kind(ctx, node.returns)
        if kind is None:
            returns = [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None]
            kinds = {_constructed_kind(ctx, value, {}) for value in returns}
            kind = kinds.pop() if len(kinds) == 1 else None
        if kind:
            factories[node.name] = kind
    return factories


def known_clients(ctx):
    """({(scope id, name) or (None, attribute text): kind}, factories).

    Plain names are tracked per scope, so `with httpx.Client() as client` in one function does
    not hide `client = OpenAI()` at module level. A key bound to several kinds is dropped.
    """
    factories = _factories(ctx)
    found = {}

    def add(target, kind, anchor):
        if not kind:
            return
        if isinstance(target, ast.Name):
            key = (id(_scope_of(ctx, anchor)), target.id)
        elif isinstance(target, ast.Attribute):
            key = (None, ast.unparse(target))
        else:
            return
        found.setdefault(key, set()).add(kind)

    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add(target, _constructed_kind(ctx, node.value, factories), node)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            add(node.target, _constructed_kind(ctx, node.value, factories), node)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            add(node.optional_vars, _constructed_kind(ctx, node.context_expr, factories), node)
        if isinstance(node, ast.AnnAssign):
            add(node.target, _annotated_kind(ctx, node.annotation), node)
        elif isinstance(node, ast.arg):
            add(ast.Name(id=node.arg), _annotated_kind(ctx, node.annotation), node)
    return {key: kinds.pop() for key, kinds in found.items() if len(kinds) == 1}, factories


def _known_kind(ctx, receiver, known):
    if isinstance(receiver, ast.Attribute):
        return known.get((None, ast.unparse(receiver)))
    scope = receiver
    while True:
        scope = _scope_of(ctx, scope)
        if (id(scope), receiver.id) in known:
            return known[(id(scope), receiver.id)]
        if scope is ctx.tree:
            return None


def _receiver_kind(ctx, receiver, known, factories):
    kind = _constructed_kind(ctx, receiver, factories)
    if kind:
        return kind
    if isinstance(receiver, (ast.Name, ast.Attribute)):
        kind = _known_kind(ctx, receiver, known)
        if kind:
            return kind
        if ctx.dotted(receiver) == "openai":  # module-level client: openai.chat.completions.create
            return "openai"
    return None


def _imports(ctx, module):
    return any(path.split(".")[0] == module for path in ctx.aliases.values())


def _match_chain(func):
    """(provider, api tuple, receiver node) for a recognised method chain, else None."""
    attrs = []
    node = func
    while isinstance(node, ast.Attribute) or _is_passthrough_call(node):
        if isinstance(node, ast.Call):
            node = node.func.value
            continue
        if node.attr not in PASSTHROUGH_ATTRS:
            attrs.append(node)
        node = node.value
    names = tuple(attr.attr for attr in attrs)
    for provider, api in CHAIN_APIS:
        if names[:len(api)] == tuple(reversed(api)):
            return provider, api, _unwrap(attrs[len(api) - 1].value)
    return None


def llm_call(ctx, node, known, factories):
    """Return an LLMCall if `node` is a recognised LLM API call, else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    method = node.func.attr
    if method in BEDROCK_APIS:
        receiver = _unwrap(node.func.value)
        kind = _receiver_kind(ctx, receiver, known, factories)
        if kind == "bedrock":
            evidence = "client"
        elif kind is None and any(kw.arg == "modelId" for kw in node.keywords):
            evidence = "signature"
        else:
            return None
        return LLMCall(node, "bedrock", method, ast.unparse(receiver), evidence)

    match = _match_chain(node.func)
    if match is None:
        return None
    provider, api, receiver = match
    kind = _receiver_kind(ctx, receiver, known, factories)
    if kind == provider:
        evidence = "client"
    elif kind is None and api in OPENAI_COMPATIBLE_CHAINS and _imports(ctx, "openai"):
        evidence = "chain"
    else:
        return None
    return LLMCall(node, provider, ".".join(api), ast.unparse(receiver), evidence)


def llm_calls(ctx):
    known, factories = known_clients(ctx)
    for node in ast.walk(ctx.tree):
        found = llm_call(ctx, node, known, factories)
        if found is not None:
            yield found


# --- static value resolution ---------------------------------------------------------------


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


def _binds(target, name):
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(target))


def _bindings(scope, name):
    """(plain assigned values, count of other bindings or in-place mutations) of `name` in one scope."""
    values, other = [], 0
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope.args
        params = args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]
        other += sum(arg.arg == name for arg in params)
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    values.append(node.value)
                elif _binds(target, name):
                    other += 1  # tuple unpacking, `cfg[k] = v`, `cfg.attr = v`
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if node.value is not None:
                values.append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
            other += _binds(node.target, name)
        elif isinstance(node, ast.withitem):
            other += node.optional_vars is not None and _binds(node.optional_vars, name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            other += name in node.names
        elif isinstance(node, ast.Delete):
            other += any(_binds(target, name) for target in node.targets)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in ("update", "setdefault", "pop", "popitem", "clear", "__setitem__", "insert", "remove")
        ):
            other += 1
    return values, other


def resolve(ctx, node, depth=0):
    """Follow a name to its only assignment (own scope, else enclosing ones); None if unknown."""
    node = _await(node)
    if not isinstance(node, ast.Name):
        return node
    if depth >= RESOLVE_DEPTH:
        return None
    scope = _scope_of(ctx, node)
    while True:
        values, other = _bindings(scope, node.id)
        if values or other or scope is ctx.tree:
            break
        scope = _scope_of(ctx, scope)
    if len(values) != 1 or other:
        return None
    return resolve(ctx, values[0], depth + 1)


def literal_dict(ctx, node):
    """{key: value node} for a statically known dict (literal or `dict(k=v)`), else None."""
    node = resolve(ctx, node)
    if isinstance(node, ast.Dict):
        keys = [_string(key) if key is not None else None for key in node.keys]
        if None in keys:
            return None  # `**spread` or a non-literal key
        return dict(zip(keys, node.values))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" and not node.args:
        if any(kw.arg is None for kw in node.keywords):
            return None
        return {kw.arg: kw.value for kw in node.keywords}
    return None


def call_keywords(ctx, call):
    """Effective keyword arguments of a call, expanding `**d` for known dicts; None if unknown."""
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        return None
    keywords = {}
    for kw in call.keywords:
        if kw.arg is not None:
            keywords[kw.arg] = kw.value
            continue
        expanded = literal_dict(ctx, kw.value)
        if expanded is None:
            return None
        keywords.update(expanded)
    return keywords


# --- static content of prompts and tool definitions (LLM-01, LLM-15) ------------------------

TEXT_WRAPPERS = {"textwrap.dedent": textwrap.dedent, "inspect.cleandoc": inspect.cleandoc}
STRIPS = ("strip", "lstrip", "rstrip")
STATIC_DEPTH = 24


def static_text(ctx, node, depth=0):
    """(leading text, complete) of a string expression.

    `complete` is False when the text continues with something not known statically (an f-string
    placeholder, `.format()` field, unknown name); the returned text is then the static prefix.
    """
    node = resolve(ctx, node) if depth < STATIC_DEPTH else None
    if node is None:
        return "", False
    if isinstance(node, ast.Constant):
        return (node.value, True) if isinstance(node.value, str) else ("", False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, done = static_text(ctx, node.left, depth + 1)
        if not done:
            return left, False
        right, done = static_text(ctx, node.right, depth + 1)
        return left + right, done
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):  # "..." % args
        text, _ = static_text(ctx, node.left, depth + 1)
        cut = text.find("%")
        return (text, False) if cut < 0 else (text[:cut], False)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(value.value)
                continue
            text, done = static_text(ctx, value.value, depth + 1)
            if value.conversion != -1 or value.format_spec is not None:
                text, done = "", False
            parts.append(text)
            if not done:
                return "".join(parts), False
        return "".join(parts), True
    if isinstance(node, ast.Call):
        return _static_call_text(ctx, node, depth)
    return "", False


def _static_call_text(ctx, node, depth):
    wrapper = TEXT_WRAPPERS.get(ctx.dotted(node.func) or "")
    if wrapper and len(node.args) == 1 and not node.keywords:
        text, done = static_text(ctx, node.args[0], depth + 1)
        return wrapper(text), done
    if not isinstance(node.func, ast.Attribute):
        return "", False
    method, receiver = node.func.attr, node.func.value
    if method in STRIPS and not node.args and not node.keywords:
        text, done = static_text(ctx, receiver, depth + 1)
        return (getattr(text, method)() if done else text.lstrip() if method != "rstrip" else text), done
    if method == "format":
        text, done = static_text(ctx, receiver, depth + 1)
        cut = text.find("{")
        return (text, done) if cut < 0 else (text[:cut], False)
    if method == "join" and len(node.args) == 1 and not node.keywords:
        sep, done = static_text(ctx, receiver, depth + 1)
        items, complete = static_elements(ctx, node.args[0], depth + 1)
        if not done or items is None:
            return "", False
        parts = []
        for item in items:
            text, done = static_text(ctx, item, depth + 1)
            parts.append(text)
            if not done:
                return sep.join(parts), False
        return sep.join(parts), complete
    return "", False


def static_elements(ctx, node, depth=0):
    """(element nodes, complete) of a list/tuple literal, following names, `*spread` and `+`.

    Returns (None, False) when the value is not a statically known sequence at all.
    """
    node = resolve(ctx, node) if depth < STATIC_DEPTH else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, done = static_elements(ctx, node.left, depth + 1)
        if left is None or not done:
            return left, False
        right, done = static_elements(ctx, node.right, depth + 1)
        return left + (right or []), done and right is not None
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None, False
    items = []
    for element in node.elts:
        if isinstance(element, ast.Starred):
            spread, done = static_elements(ctx, element.value, depth + 1)
            items.extend(spread or [])
            if not done:
                return items, False
        else:
            items.append(element)
    return items, True


def static_size(ctx, node, depth=0):
    """Length of the compact JSON encoding of a fully static literal (tool definitions), else None."""
    node = resolve(ctx, node) if depth < STATIC_DEPTH else None
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, (bool, int, float, str)):
            return len(json.dumps(node.value, ensure_ascii=False))
        return None
    if isinstance(node, ast.Dict) or _is_dict_call(node):
        pairs = _dict_pairs(ctx, node, depth)
        if pairs is None:
            return None
        sizes = [static_size(ctx, value, depth + 1) for _, value in pairs]
        if None in sizes:
            return None
        return 1 + sum(len(json.dumps(key)) + 1 + size + 1 for (key, _), size in zip(pairs, sizes)) + (not pairs)
    items, complete = static_elements(ctx, node, depth + 1)
    if items is not None:
        if not complete:
            return None
        sizes = [static_size(ctx, item, depth + 1) for item in items]
        return None if None in sizes else 1 + sum(size + 1 for size in sizes) + (not items)
    text, done = static_text(ctx, node, depth + 1)
    return len(json.dumps(text, ensure_ascii=False)) if done else None


def _is_dict_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" and not node.args


def _dict_pairs(ctx, node, depth):
    """[(key, value node)] of a dict literal or `dict(k=v)`, expanding known `**spread`; else None."""
    if _is_dict_call(node):
        pairs = []
        for kw in node.keywords:
            if kw.arg is None:
                return None
            pairs.append((kw.arg, kw.value))
        return pairs
    pairs = []
    for key, value in zip(node.keys, node.values):
        if key is None:
            spread = resolve(ctx, value) if depth < STATIC_DEPTH else None
            if not isinstance(spread, ast.Dict) and not _is_dict_call(spread):
                return None
            inner = _dict_pairs(ctx, spread, depth + 1)
            if inner is None:
                return None
            pairs.extend(inner)
            continue
        name = _string(key)
        if name is None:
            return None
        pairs.append((name, value))
    return pairs


def dict_items(ctx, node):
    """{key: value node} of a statically known dict (literal, `dict(k=v)` or known `**spread`), else None."""
    node = resolve(ctx, node)
    if not (isinstance(node, ast.Dict) or _is_dict_call(node)):
        return None
    pairs = _dict_pairs(ctx, node, 0)
    return None if pairs is None else dict(pairs)
