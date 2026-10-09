"""JS-07: spreading the accumulator in reduce/loops (quadratic copying), confirmed by a heap profile."""
from owner_c.js.ctx import FUNCTION_TYPES
from owner_c.js.profile import confirm_heap

KEY = "JS-07"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-heap"
SETTINGS = {"min_alloc_bytes": (0, None)}  # (exclusive minimum, no maximum)
NOQA = ("no-accumulating-spread", "oxc/no-accumulating-spread", "performance/noAccumulatingSpread")
REFS = [
    "https://biomejs.dev/linter/rules/no-accumulating-spread/",
    "https://oxc.rs/docs/guide/usage/linter/rules/oxc/no-accumulating-spread.html",
]
RECOMMENDATION = "Mutate one accumulator (acc.push(x), acc[key] = value) instead of copying it on every iteration."
LIMITATION = ("Needs a heap profile captured with the shipped `collect-heap.js` collector; allocations are matched to the "
              "enclosing function (the reduce callback). Small inputs are harmless; the profile threshold decides.")


def _spreads_name(ctx, node, name):
    """True if `node` (an array/object literal) spreads the identifier `name`."""
    return any(c.type == "spread_element" and ctx.text(c.children[-1]) == name for c in node.children)


def _reduce_callbacks(ctx):
    for node in ctx.walk():
        if node.type != "call_expression":
            continue
        _obj, prop = ctx.call_parts(node)
        args = ctx.args(node)
        if prop in ("reduce", "reduceRight") and args and args[0].type in FUNCTION_TYPES:
            params = ctx.field(args[0], "parameters")
            first = next((c for c in params.children if c.type == "identifier"), None) if params is not None else None
            if first is None and args[0].type == "arrow_function":
                first = ctx.field(args[0], "parameter")  # single bare parameter: x => ...
            if first is not None:
                yield args[0], ctx.text(first)


def find(ctx) -> list:
    out, seen = [], set()

    def add(node, anchor, detail):
        if node.id not in seen:
            seen.add(node.id)
            out.append(ctx.candidate(node, anchor, detail))

    for callback, acc in _reduce_callbacks(ctx):
        for node in ctx.walk(callback):
            if node.type in ("array", "object") and _spreads_name(ctx, node, acc):
                add(node, f"{ctx.qualname(node)}:reduce-spread", f"reduce spreads its accumulator `{acc}` on every step")
            elif node.type == "call_expression":
                callee = ctx.dotted_callee(node)
                if callee == f"{acc}.concat" or (callee == "Object.assign" and ctx.args(node)
                                                 and ctx.args(node)[0].type in ("object", "array")
                                                 and any(ctx.text(a) == acc for a in ctx.args(node)[1:])):
                    add(node, f"{ctx.qualname(node)}:reduce-copy", f"reduce copies its accumulator `{acc}` on every step")
    for node in ctx.walk():
        if node.type != "assignment_expression" or not ctx.in_loop(node, include_callbacks=False):
            continue
        left, right = ctx.field(node, "left"), ctx.field(node, "right")
        if left is None or right is None or left.type != "identifier":
            continue
        name = ctx.text(left)
        if right.type in ("array", "object") and _spreads_name(ctx, right, name):
            add(node, f"{ctx.qualname(node)}:loop-spread:{name}", f"`{name} = [...{name}, ...]` copies `{name}` on every iteration")
    return out


def confirm(candidate, data, settings):
    return confirm_heap(candidate, data, settings, "accumulator copies")
