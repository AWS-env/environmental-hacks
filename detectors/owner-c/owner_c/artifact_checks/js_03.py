"""JS-03: JSON.parse(JSON.stringify(x)) deep clone, reported only when a heap profile shows big allocations."""
from owner_c.js.profile import confirm_heap

KEY = "JS-03"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-heap"
SETTINGS = {"min_alloc_bytes": (0, None)}  # (exclusive minimum, no maximum)
NOQA = ("unicorn/prefer-structured-clone",)
REFS = [
    "https://github.com/sindresorhus/eslint-plugin-unicorn/blob/main/docs/rules/prefer-structured-clone.md",
    "https://developer.mozilla.org/en-US/docs/Web/API/Window/structuredClone",
]
RECOMMENDATION = ("Use structuredClone(x) (Node 17+), or copy only the fields you need. Check the semantics: JSON "
                  "drops undefined/functions and turns Dates into strings, structuredClone keeps Dates/Map/Set but "
                  "throws on functions.")
LIMITATION = ("Needs a heap profile captured with the shipped `collect-heap.js` collector (V8's default heap profile "
              "omits freed objects, which hides clone churn); allocations are matched to the enclosing function. "
              "The two cloning approaches differ in behaviour, so the replacement needs a manual check.")


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "call_expression" or ctx.dotted_callee(node) != "JSON.parse":
            continue
        args = ctx.args(node)
        if len(args) != 1 or args[0].type != "call_expression" or ctx.dotted_callee(args[0]) != "JSON.stringify":
            continue
        if len(ctx.args(args[0])) != 1:  # JSON.stringify(x, replacer, space) is not a plain clone
            continue
        out.append(ctx.candidate(node, f"{ctx.qualname(node)}:JSON.parse(JSON.stringify)",
                                 "JSON.parse(JSON.stringify(x)) deep-clones by serializing and re-parsing"))
    return out


def confirm(candidate, data, settings):
    return confirm_heap(candidate, data, settings, "serialize-and-parse clone")
