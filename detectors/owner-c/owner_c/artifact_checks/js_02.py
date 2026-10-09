"""JS-02: Array.includes/indexOf/find/some inside loops, reported only when a V8 CPU profile shows the function is hot."""
from owner_c.js.profile import confirm_cpu

KEY = "JS-02"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-cpu"
SETTINGS = {"min_time_share": (0, 1)}  # (exclusive minimum, inclusive maximum)
NOQA = ("unicorn/prefer-set-has",)
REFS = [
    "https://github.com/sindresorhus/eslint-plugin-unicorn/blob/main/docs/rules/prefer-set-has.md",
    "https://nodejs.org/api/cli.html",
]
RECOMMENDATION = "Build a Set (or Map) once outside the loop and use .has() / .get() for O(1) lookups."
LIMITATION = ("Needs a client-produced V8 CPU profile (`node --no-opt --cpu-prof`); matched to the enclosing function "
              "(V8 attributes work to functions), so an inlined function is not confirmed. The receiver's type is not "
              "resolved: string receivers (literals, templates) are skipped, other non-array receivers can still match.")
LOOKUPS = {"includes", "indexOf", "find", "findIndex", "some"}


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "call_expression":
            continue
        receiver_text, prop = ctx.call_parts(node)
        if prop not in LOOKUPS or not receiver_text or not ctx.in_loop(node):
            continue
        member = ctx.field(node, "function")
        receiver = ctx.field(member, "object")
        if receiver is None or receiver.type in ("string", "template_string"):
            continue
        shown = receiver_text if len(receiver_text) <= 40 else receiver_text[:37] + "..."
        out.append(ctx.candidate(node, f"{ctx.qualname(node)}:{shown}.{prop}",
                                 f"{receiver_text}.{prop}() is an O(n) lookup inside a loop"))
    return out


def confirm(candidate, data, settings):
    return confirm_cpu(candidate, data, settings, "O(n) lookup per iteration")
