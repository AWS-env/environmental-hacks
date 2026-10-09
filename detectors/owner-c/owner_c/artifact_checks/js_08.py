"""JS-08: new Intl.* formatter or constant new RegExp built per call, confirmed by a V8 CPU profile."""
from owner_c.js.profile import confirm_cpu

KEY = "JS-08"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-cpu"
SETTINGS = {"min_time_share": (0, 1)}  # (exclusive minimum, inclusive maximum)
NOQA = ("prefer-regex-literals", "unicorn/better-regex")
REFS = [
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/NumberFormat/format",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/RegExp/RegExp",
]
RECOMMENDATION = "Create the formatter / RegExp once at module scope (or memoize it) and reuse it."
LIMITATION = ("Needs a client-produced V8 CPU profile (`node --no-opt --cpu-prof`); matched to the enclosing function. "
              "RegExp construction is only flagged for constant patterns (a dynamic pattern cannot be hoisted), and V8's "
              "regexp cache makes small cases cheap, which is why a hot profile is required.")
INTL = {"NumberFormat", "DateTimeFormat", "Collator", "PluralRules", "RelativeTimeFormat", "ListFormat", "DisplayNames"}


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "new_expression" or ctx.enclosing_function(node) is None:
            continue
        ctor = ctx.field(node, "constructor")
        name = ctx.text(ctor) if ctor is not None else ""
        if name.startswith("Intl.") and name[5:] in INTL:
            out.append(ctx.candidate(node, f"{ctx.qualname(node)}:new {name}",
                                     f"new {name}(...) is rebuilt every time this function runs"))
        elif name == "RegExp":
            args = ctx.args(node)
            first = args[0] if args else None
            constant = first is not None and (first.type == "string" or (
                first.type == "template_string" and not any(c.type == "template_substitution" for c in first.children)))
            if constant:
                out.append(ctx.candidate(node, f"{ctx.qualname(node)}:new RegExp",
                                         "new RegExp(<constant>) is rebuilt every time this function runs"))
    return out


def confirm(candidate, data, settings):
    return confirm_cpu(candidate, data, settings, "object rebuilt per call")
