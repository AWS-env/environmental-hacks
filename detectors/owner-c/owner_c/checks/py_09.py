"""PY-09: mutable default arguments (shared across calls)."""
import ast

KEY = "PY-09"
DETECTOR_VERSION = "1.0.0"
NOQA = ("B006", "B008")
REFS = [
    "https://docs.astral.sh/ruff/rules/mutable-argument-default/",
    "https://pylint.readthedocs.io/en/v3.3.9/user_guide/messages/warning/dangerous-default-value.html",
]
RECOMMENDATION = "Use None as the default and create the list, dict or set inside the function body."
LIMITATION = ("Static pattern only: proves a shared mutable default exists, not how often the function runs "
              "or that the sharing is unintended (a deliberate cache is a legitimate exception).")

MUTABLE_CALLS = {
    "list", "dict", "set", "bytearray", "collections.defaultdict",
    "collections.deque", "collections.OrderedDict", "collections.Counter",
}
IMMUTABLE_ANNOTATIONS = {
    "Sequence", "Tuple", "tuple", "frozenset", "FrozenSet", "Mapping",
    "Iterable", "str", "int", "float", "bool", "bytes",
}


def _annotation_name(ann) -> str:
    if isinstance(ann, ast.Subscript):
        ann = ann.value
    if isinstance(ann, ast.Attribute):
        return ann.attr
    if isinstance(ann, ast.Name):
        return ann.id
    return ""


def _is_mutable(ctx, node) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return isinstance(node, ast.Call) and ctx.dotted(node.func) in MUTABLE_CALLS


def run(ctx):
    out = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in pairs:
            if arg.annotation is not None and _annotation_name(arg.annotation) in IMMUTABLE_ANNOTATIONS:
                continue
            if _is_mutable(ctx, default):
                out.append(ctx.hit(
                    default, f"{ctx.qualname(default)}({arg.arg})",
                    f"Mutable default for argument '{arg.arg}' is created once and shared across calls.",
                    "high"))
    return out
