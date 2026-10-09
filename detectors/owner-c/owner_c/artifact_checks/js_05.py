"""JS-05: chained map/filter/flatMap/slice/... building intermediate arrays, confirmed by a heap profile."""
from owner_c.js.profile import confirm_heap

KEY = "JS-05"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-heap"
SETTINGS = {"min_alloc_bytes": (0, None)}  # (exclusive minimum, no maximum)
NOQA = ("unicorn/no-array-callback-reference",)
REFS = [
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/flatMap",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Iterator/map",
]
RECOMMENDATION = ("Do it in a single pass (one reduce, a for...of loop, or iterator helpers such as "
                  "array.values().filter(...).map(...).toArray()) instead of allocating an array per step.")
LIMITATION = ("Needs a heap profile captured with the shipped `collect-heap.js` collector; allocations are matched to the "
              "enclosing function. V8 can fuse some simple chains, which the profile (not this check) would show.")
CHAIN = {"map", "filter", "flatMap", "reduce", "reduceRight", "slice", "concat", "flat", "sort", "toSorted", "toReversed"}
ALLOCATING = {"map", "filter", "flatMap", "slice", "concat", "flat", "toSorted", "toReversed"}


def _chain(ctx, call):
    """Methods of a call chain ending at `call`, innermost first, or []."""
    methods, current = [], call
    while current is not None and current.type == "call_expression":
        fn = ctx.field(current, "function")
        if fn is None or fn.type != "member_expression":
            break
        prop = ctx.field(fn, "property")
        name = ctx.text(prop) if prop else ""
        if name not in CHAIN:
            break
        methods.append(name)
        current = ctx.field(fn, "object")
    return list(reversed(methods))


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "call_expression":
            continue
        parent = node.parent
        if parent is not None and parent.type == "member_expression" and ctx.field(parent, "object") == node:
            continue  # not the outermost call of the chain
        methods = _chain(ctx, node)
        if len(methods) >= 2 and sum(m in ALLOCATING for m in methods) >= 2:
            chain = ".".join(methods)
            out.append(ctx.candidate(node, f"{ctx.qualname(node)}:{chain}",
                                     f"the chain .{chain.replace('.', '().')}() allocates an intermediate array per step"))
    return out


def confirm(candidate, data, settings):
    return confirm_heap(candidate, data, settings, "intermediate arrays")
