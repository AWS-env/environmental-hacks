"""OBS-03: logging inside hot loops (one log call per iteration).

Detector semantics version 1.0.0. Flags TRACE/DEBUG/INFO logging calls that run on every
iteration of a loop or comprehension in the same function, so log volume and CPU scale with
the number of items. Python only; static only (the profiler half of OBS-03 is not covered).
"""

from __future__ import annotations

import ast
import re
import sys

from . import static
from .logcalls import is_level_guarded, log_calls
from .static import COMP_NODES, SCOPE_NODES, EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported)

CHECK_ID = "OBS-03"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-03", "OBS03")

# WARNING and above report failures, so their volume follows the failure rate rather than the
# item count; they are not flagged. A `.log(level, ...)` with a non-constant level is `low`.
PER_ITEM_LEVELS = {"trace", "debug", "info"}
SLEEP_CALLS = {"sleep", "wait"}
# Opt-in diagnostics switches, e.g. `if self.debug:` / `if verbose:` / `if options.log_unprocessed_tags:`.
VERBOSITY_FLAG = re.compile(r"(^|_)(debug|verbose|verbosity|disp|dump)(_|$)|^log_", re.I)
# Literal collections and constant ranges up to this size are not hot loops.
SMALL_LOOP = 10

REFERENCES = (
    "https://docs.python.org/3/howto/logging.html#optimization",
    "https://docs.python.org/3/library/logging.html#logging.Logger.isEnabledFor",
    "https://arxiv.org/abs/2604.04809",
)
RECOMMENDATION = (
    "Log once per batch instead of once per item: aggregate counts or a summary and log it after the "
    "loop, sample progress (e.g. every N items), or hoist a single logger.isEnabledFor(logging.DEBUG) "
    "check out of the loop and guard the per-item call with it."
)
LIMITATION = (
    "Static pattern only: OBS-03 does not know how many times a loop runs, how often the enclosing "
    "function is called or the production log level, so it proves per-iteration logging, not wasted CPU "
    "or ingest. Calls reached through helper functions are not followed, and the profiler-based half of "
    "OBS-03 is not implemented."
)


def _in_body(child, stmts):
    return any(child is stmt for stmt in stmts)


def _per_iteration(loop, child, grandchild):
    """True if `child` (a direct child of `loop`) is evaluated on every iteration."""
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        return _in_body(child, loop.body) or child is loop.target
    if isinstance(loop, ast.While):
        return _in_body(child, loop.body) or child is loop.test
    if isinstance(loop, COMP_NODES):
        # The first generator's iterable is evaluated once, in the enclosing scope.
        first = loop.generators[0]
        return not (child is first and grandchild is first.iter)
    return False


def iteration_path(ctx, node):
    """(loops, conditional, in_handler) for `node` within its function scope.

    `loops` lists, innermost first, the loops whose every iteration evaluates `node`.
    `conditional` is True if an `if`/ternary/boolean/match sits between `node` and the innermost
    loop; `in_handler` is True if `node` is inside an `except` clause within that loop.
    """
    loops, conditional, in_handler = [], False, False
    grandchild, child = None, node
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, SCOPE_NODES):
            break
        if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While) + COMP_NODES):
            if _per_iteration(ancestor, child, grandchild):
                if not loops and isinstance(ancestor, COMP_NODES) and not isinstance(child, ast.comprehension):
                    conditional = conditional or any(gen.ifs for gen in ancestor.generators)
                loops.append(ancestor)
        elif not loops:
            if isinstance(ancestor, ast.ExceptHandler):
                in_handler = True
            elif isinstance(ancestor, (ast.If, ast.IfExp, ast.BoolOp, ast.match_case)):
                conditional = True
        grandchild, child = child, ancestor
    return loops, conditional, in_handler


def _guarding_tests(ctx, node, stop=None):
    """Tests of the `if`s (within the function scope) whose taken branch contains `node`."""
    child = node
    for ancestor in ctx.ancestors(node):
        if ancestor is stop or isinstance(ancestor, SCOPE_NODES):
            return
        if isinstance(ancestor, ast.If) and _in_body(child, ancestor.body):
            yield ancestor.test
        elif isinstance(ancestor, ast.IfExp) and child is ancestor.body:
            yield ancestor.test
        child = ancestor


def _is_sampled(ctx, node, loop):
    """True if `node` sits under an `if` in `loop` whose test uses `%` (e.g. `if i % 100 == 0`)."""
    return any(
        isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod)
        for test in _guarding_tests(ctx, node, loop)
        for sub in ast.walk(test)
    )


def _is_verbosity_switched(ctx, node):
    """True if `node` only runs when an opt-in debug/verbose flag is set, e.g. `if self.debug:`."""
    for test in _guarding_tests(ctx, node):
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            continue
        for sub in ast.walk(test):
            name = sub.attr if isinstance(sub, ast.Attribute) else getattr(sub, "id", None)
            if isinstance(sub, (ast.Name, ast.Attribute)) and VERBOSITY_FLAG.search(name):
                return True
    return False


def _literal_size(node):
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if any(isinstance(elt, ast.Starred) for elt in node.elts):
            return None
        return len(node.elts)
    if isinstance(node, ast.Dict):
        return None if None in node.keys else len(node.keys)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
        and not node.keywords
        and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in node.args)
        and 1 <= len(node.args) <= 3
    ):
        try:
            return len(range(*(a.value for a in node.args)))
        except ValueError:
            return None
    return None


def _stops_after_one(stmts):
    """A loop body whose top level ends in break/return/raise runs at most once."""
    return any(isinstance(stmt, (ast.Break, ast.Return, ast.Raise)) for stmt in stmts)


def _exit_after(ctx, node, loop):
    """'return' if a return/raise, or 'break' if a break, unconditionally follows `node` on its
    path to `loop`: the call then runs once per loop run (e.g. a final "done" message)."""
    child = node
    for ancestor in ctx.ancestors(node):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(ancestor, field, None)
            if not isinstance(stmts, list) or not _in_body(child, stmts):
                continue
            index = next(i for i, stmt in enumerate(stmts) if stmt is child)
            for stmt in stmts[index + 1:]:
                if isinstance(stmt, (ast.Return, ast.Raise)):
                    return "return"
                if isinstance(stmt, ast.Break):
                    return "break"
        if ancestor is loop:
            return None
        child = ancestor
    return None


def _scope_walk(stmts):
    """Walk statements without descending into nested functions, classes or lambdas."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(node))


def _sleeps(loop):
    """A loop that sleeps or waits on every pass is a polling loop, not a hot loop."""
    parts = loop.body + ([loop.test] if isinstance(loop, ast.While) else [])
    for node in _scope_walk(parts):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in SLEEP_CALLS:
                return True
    return False


def _not_hot(loop):
    """Why `loop` is not a hot loop, or None."""
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        size = _literal_size(loop.iter)
        if size is not None and size <= SMALL_LOOP:
            return "small literal"
    if isinstance(loop, COMP_NODES):
        size = _literal_size(loop.generators[0].iter) if len(loop.generators) == 1 else None
        if size is not None and size <= SMALL_LOOP:
            return "small literal"
        return None
    if _stops_after_one(loop.body):
        return "runs once"
    if _sleeps(loop):
        return "polling"
    return None


def _unbounded(loop):
    """`while True` and `async for` loops usually serve requests/messages, not batch items."""
    if isinstance(loop, ast.AsyncFor):
        return True
    return isinstance(loop, ast.While) and isinstance(loop.test, ast.Constant) and bool(loop.test.value)


def _describe(loop):
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        return f"`{'async for' if isinstance(loop, ast.AsyncFor) else 'for'}` loop (line {loop.lineno})"
    if isinstance(loop, ast.While):
        return f"`while` loop (line {loop.lineno})"
    return f"comprehension (line {loop.lineno})"


def run(ctx):
    hits = []
    for call in log_calls(ctx):
        if call.level is not None and call.level not in PER_ITEM_LEVELS:
            continue
        loops, conditional, in_handler = iteration_path(ctx, call.node)
        if not loops or in_handler:
            continue
        exit_kind = _exit_after(ctx, call.node, loops[0])
        if exit_kind == "return":
            continue
        if exit_kind == "break":
            loops = loops[1:]  # once per run of the innermost loop; still per iteration of the outer ones
        hot = [loop for loop in loops if _not_hot(loop) is None]
        if not hot or is_level_guarded(ctx, call.node):
            continue
        if _is_sampled(ctx, call.node, loops[0]) or _is_verbosity_switched(ctx, call.node):
            continue
        low = call.level is None or conditional or all(_unbounded(loop) for loop in hot)
        level = call.level.upper() if call.level else "an unresolved level"
        nesting = f", inside {len(hot)} nested loops" if len(hot) > 1 else ""
        when = "on some iterations" if conditional else "on every iteration"
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.receiver}.{call.method}",
            summary=(
                f"{call.receiver}.{call.method}() logs at {level} {when} of the {_describe(hot[0])}"
                f"{nesting}, so log volume and logging CPU grow with the number of items."
            ),
            confidence="low" if low else "medium",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
