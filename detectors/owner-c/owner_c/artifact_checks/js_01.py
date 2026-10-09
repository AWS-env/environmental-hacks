"""JS-01: `await` inside loops (independent calls run serially), confirmed by AWS X-Ray traces.

A CPU profile cannot show this (the harm is waiting, not computing), so the evidence is existing
telemetry per docs/ARCHITECTURE_FLOWS.md: the client's X-Ray traces, read-only, normalized into runs of
same-named sibling subsegments that executed one after another with no overlap.
"""
import re

from owner_c.common import Confirmation

KEY = "JS-01"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "xray"
SOURCE_KIND = "telemetry"
SETTINGS = {"min_serial_calls": (1, None), "min_serial_seconds": (0, None)}  # exclusive minimums
NOQA = ("no-await-in-loop",)
REFS = [
    "https://eslint.org/docs/latest/rules/no-await-in-loop",
    "https://docs.aws.amazon.com/xray/latest/devguide/xray-concepts.html#xray-concepts-subsegments",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/all",
]
RECOMMENDATION = ("Start the independent calls together and await them as a group (Promise.all, or a bounded-concurrency "
                  "helper such as p-limit) instead of awaiting each one inside the loop. Keep the loop serial only when "
                  "iterations depend on each other or you must respect a rate limit.")
LIMITATION = ("Needs X-Ray traces of a representative run (read-only). A trace has no source lines, so it is tied to the "
              "file through the function-to-file mapping supplied with the traces; every loop-await candidate in that "
              "file is confirmed together, with higher confidence when the awaited call names the traced service. "
              "Loops that look serial on purpose (retry/backoff/sleep, `for await`, early exit, a result fed into the "
              "next iteration) are not candidates, but intent cannot be proven from code or traces.")

_RETRY = re.compile(r"retry|retries|attempt|tries|backoff", re.I)
_WAIT = re.compile(r"sleep|delay|wait|timeout|backoff|throttle|ratelimit|limiter", re.I)
_LOOPS = ("for_statement", "for_in_statement", "while_statement", "do_statement")
_FUNCTIONS = {"function_declaration", "function_expression", "function", "arrow_function", "method_definition",
              "generator_function", "generator_function_declaration"}


def _loop_of(ctx, node):
    for a in ctx.ancestors(node):
        if a.type in _LOOPS:
            return a
        if a.type in _FUNCTIONS:
            return None
    return None


def _walk_same_function(ctx, root):
    stack = list(root.children)
    while stack:
        n = stack.pop()
        yield n
        if n.type not in _FUNCTIONS:
            stack.extend(n.children)


def _serial_on_purpose(ctx, loop, aw):
    if any(c.type == "await" for c in loop.children):  # `for await (...)`: intentional async iteration
        return True
    body = ctx.field(loop, "body")
    header = ctx.text(loop)[: (body.start_byte - loop.start_byte)] if body is not None else ""
    if _RETRY.search(header):
        return True
    inner = list(_walk_same_function(ctx, body)) if body is not None else []
    if any(n.type in ("break_statement", "return_statement") for n in inner):  # search / early exit / polling
        return True
    if any(n.type == "call_expression" and _WAIT.search(ctx.dotted_callee(n) or "") for n in inner):  # backoff, rate limit
        return True
    parent = aw.parent
    if parent is not None and parent.type == "assignment_expression":  # x = await f(x): next iteration depends on it
        left = ctx.field(parent, "left")
        if left is not None and left.type == "identifier" and re.search(rf"\b{re.escape(ctx.text(left))}\b", ctx.text(aw)[5:]):
            return True
    return False


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "await_expression":
            continue
        loop = _loop_of(ctx, node)
        if loop is None or _serial_on_purpose(ctx, loop, node):
            continue
        inner = next((c for c in node.children if c.is_named), None)
        callee = ctx.dotted_callee(inner) if inner is not None and inner.type == "call_expression" else ""
        shown = (callee or ctx.text(inner or node)[:40]).strip()
        out.append(ctx.candidate(node, f"{ctx.qualname(node)}:await:{shown}",
                                 f"`await {shown}` runs inside a loop, so each iteration waits for the previous one"))
    return out


def confirm(candidate, data: dict, settings: dict):
    calls, seconds = data.get("max_serial_calls", 0), data.get("serial_wall_seconds", 0.0)
    if calls < settings["min_serial_calls"] or seconds < settings["min_serial_seconds"]:
        return None
    services = data.get("serial_services", [])
    named = any(s.lower() in candidate.detail.lower() for s in services)
    return Confirmation(
        "max_serial_calls", calls,
        f"{candidate.detail}; X-Ray shows up to {calls} back-to-back `{', '.join(services) or 'sibling'}` calls "
        f"with no overlap ({seconds:.2f}s of serial waiting).",
        "medium" if named else "low", (("serial_wall_seconds", seconds),))
