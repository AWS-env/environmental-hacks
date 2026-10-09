"""JS-06: listeners, timers and subscriptions registered without cleanup (React effects, class components)."""
from owner_c.js.ctx import FUNCTION_TYPES

KEY = "JS-06"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
NOQA = (
    "react-web-api/no-leaked-event-listener", "react-web-api/no-leaked-interval",
    "@eslint-react/web-api-no-leaked-event-listener", "@eslint-react/web-api-no-leaked-interval",
    "react-hooks/exhaustive-deps",
)
REFS = [
    "https://eslint-react.xyz/docs/rules/web-api-no-leaked-event-listener",
    "https://react.dev/learn/synchronizing-with-effects#step-3-add-cleanup-if-needed",
    "https://nodejs.org/api/events.html#emittersetmaxlistenerslimit",
]
RECOMMENDATION = ("Return a cleanup function from the effect (or implement componentWillUnmount) that removes the "
                  "listener, clears the interval or unsubscribes, using the same function reference you registered.")
LIMITATION = ("Static pattern only: leaks are not observed at runtime. Cleanup is matched by name (removeEventListener, "
              "clearInterval, unsubscribe, off/removeListener), so cleanup done through a helper or returned by "
              "reference (`return unsubscribe;`) is treated as present. Only React effects, class components and "
              "un-stored setInterval calls are analysed.")

EFFECTS = {"useEffect", "useLayoutEffect", "useInsertionEffect"}
# registration -> names that undo it
REMOVERS = {
    "addEventListener": ("removeEventListener",),
    "setInterval": ("clearInterval",),
    "subscribe": ("unsubscribe", "unsub"),
    "addListener": ("removeListener", "removeAllListeners", "off"),
    "on": ("off", "removeListener", "removeAllListeners"),
}


def _registration(ctx, call):
    """Registration kind of a call (addEventListener/setInterval/subscribe/addListener/on) or None."""
    obj, prop = ctx.call_parts(call)
    if prop not in REMOVERS:
        return None
    if prop in ("setInterval",) or obj:
        return prop
    return None


def _has_option(ctx, call, names):
    for arg in ctx.args(call)[2:]:
        if any(ctx.text(arg).find(n) >= 0 for n in names):
            return True
    return False


def _cleanup(ctx, body):
    """The function returned by an effect/lifecycle body, or None."""
    for stmt in body.children:
        if stmt.type == "return_statement":
            value = next((c for c in stmt.children if c.is_named and c.type != "comment"), None)
            if value is not None and value.type in FUNCTION_TYPES:
                return value
            if value is not None:
                return value  # identifier / call: cleanup by reference, treated as present
    return None


def _registrations(ctx, scope, skip):
    for node in ctx.walk(scope):
        if node.type != "call_expression" or (skip is not None and any(a.id == skip.id for a in ctx.ancestors(node))):
            continue
        kind = _registration(ctx, node)
        if kind:
            yield node, kind


def _check_body(ctx, owner_call, body, label, out, seen):
    cleanup = _cleanup(ctx, body)
    cleanup_text = ctx.text(cleanup) if cleanup is not None else ""
    skip = cleanup if cleanup is not None and cleanup.type in FUNCTION_TYPES else None
    for node, kind in _registrations(ctx, body, skip):
        args = ctx.args(node)
        if kind == "addEventListener" and _has_option(ctx, node, ("once", "signal")):
            continue
        if kind == "on" and len(args) < 2:
            continue
        inline = kind == "addEventListener" and len(args) > 1 and args[1].type in FUNCTION_TYPES
        removed = cleanup is not None and (cleanup.type not in FUNCTION_TYPES
                                           or any(r in cleanup_text for r in REMOVERS[kind]))
        if inline:
            summary = f"The {kind} registration uses an inline function, so it can never be removed."
        elif cleanup is None:
            summary = f"The {kind} registration has no cleanup."
        elif not removed:
            summary = f"The cleanup does not undo the {kind} registration."
        else:
            continue
        seen.add(node.id)
        out.append(ctx.hit(node, f"{ctx.qualname(owner_call)}:{label}:{kind}", summary,
                           "medium" if kind != "on" else "low"))


def run(ctx):
    out, seen = [], set()
    for node in ctx.walk():
        if node.type == "call_expression":
            _obj, prop = ctx.call_parts(node)
            args = ctx.args(node)
            if prop in EFFECTS and args and args[0].type in FUNCTION_TYPES:
                body = ctx.field(args[0], "body")
                if body is not None and body.type == "statement_block":
                    _check_body(ctx, node, body, prop, out, seen)
        elif node.type == "class_declaration":
            methods = {ctx.text(ctx.field(m, "name")): m for m in ctx.walk(node)
                       if m.type == "method_definition" and ctx.field(m, "name") is not None}
            mount, unmount = methods.get("componentDidMount"), methods.get("componentWillUnmount")
            if mount is None:
                continue
            unmount_text = ctx.text(unmount) if unmount is not None else ""
            for reg, kind in _registrations(ctx, mount, None):
                if kind == "on" and len(ctx.args(reg)) < 2:
                    continue
                if not any(r in unmount_text for r in REMOVERS[kind]):
                    seen.add(reg.id)
                    out.append(ctx.hit(reg, f"{ctx.qualname(mount)}:componentDidMount:{kind}",
                                       f"{kind} in componentDidMount is not undone in componentWillUnmount.", "medium"))
    for node in ctx.walk():  # timers started and forgotten inside a function
        if node.type != "call_expression" or node.id in seen or ctx.enclosing_function(node) is None:
            continue
        parent = node.parent
        if parent is not None and parent.type == "expression_statement" and _registration(ctx, node) == "setInterval":
            out.append(ctx.hit(node, f"{ctx.qualname(node)}:setInterval-unstored",
                               "setInterval's handle is discarded, so the timer can never be cleared.", "low"))
    return out
