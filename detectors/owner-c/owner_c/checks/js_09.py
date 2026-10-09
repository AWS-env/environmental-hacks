"""JS-09: throw/catch used for expected control flow (static: V8 profiles cannot attribute exception costs)."""
from owner_c.js.ctx import FUNCTION_TYPES

KEY = "JS-09"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
NOQA = ("no-throw-literal", "no-useless-catch")
REFS = [
    "https://v8.dev/docs/stack-trace-api",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/try...catch",
]
RECOMMENDATION = ("Return a result (or a sentinel/Result object) and check it, instead of throwing to jump to a catch in the "
                  "same function; keep exceptions for exceptional failures.")
LIMITATION = ("Static pattern only. We tried to confirm it with real V8 CPU and heap profiles, but V8 charges stack-trace "
              "capture and GC to native frames, so neither profile attributes exception cost to the throwing function. "
              "Only local patterns are detected: a throw caught in the same function when the try body has no other call, "
              "await or `new` (otherwise the catch is shared error handling), and try/catch in a loop whose catch only "
              "`continue`s (an empty catch isolating callbacks is not flagged). Exceptions crossing function boundaries "
              "are not analysed.")


def _inside(ctx, node, block):
    return any(a.id == block.id for a in ctx.ancestors(node))


def _try_around(ctx, node):
    """Nearest try_statement (in the same function) whose *body* contains node, else None."""
    for a in ctx.ancestors(node):
        if a.type in FUNCTION_TYPES:
            return None
        if a.type == "try_statement":
            body = ctx.field(a, "body")
            if body is not None and _inside(ctx, node, body):
                return a
    return None


def _catch_body(ctx, try_node):
    handler = ctx.field(try_node, "handler")
    return ctx.field(handler, "body") if handler is not None else None


def _only_throws(ctx, try_node):
    """True if the try body can fail only through its own `throw`s (no other call, await or `new`).

    Otherwise the catch is shared error handling for real failures (I/O, awaited work) and the throw just feeds it."""
    throws = [n for n in ctx.walk(ctx.field(try_node, "body")) if n.type == "throw_statement"]
    inside_throw = {d.id for t in throws for d in ctx.walk(t)}
    return not any(n.type in ("call_expression", "await_expression", "new_expression") and n.id not in inside_throw
                   for n in ctx.walk(ctx.field(try_node, "body")))


def run(ctx):
    out, seen = [], set()
    for node in ctx.walk():
        if node.type == "throw_statement":
            try_node = _try_around(ctx, node)
            handler_body = _catch_body(ctx, try_node) if try_node is not None else None
            if (handler_body is not None and _only_throws(ctx, try_node)
                    and not any(n.type == "throw_statement" for n in ctx.walk(handler_body))):
                seen.add(try_node.id)
                out.append(ctx.hit(node, f"{ctx.qualname(node)}:throw-in-try",
                                   "An exception is thrown only to be caught in the same function.", "medium"))
    for node in ctx.walk():
        if node.type != "try_statement" or node.id in seen or not ctx.in_loop(node, include_callbacks=False):
            continue
        body = _catch_body(ctx, node)
        if body is None:
            continue
        statements = [c for c in body.children if c.is_named and c.type != "comment"]
        if statements and all(c.type in ("continue_statement", "empty_statement") for c in statements) and any(
                c.type == "continue_statement" for c in statements):  # an empty catch isolates callbacks; only `continue` skips
            out.append(ctx.hit(node, f"{ctx.qualname(node)}:try-skip-in-loop",
                               "try/catch inside a loop swallows errors to skip items.", "low"))
    return out
