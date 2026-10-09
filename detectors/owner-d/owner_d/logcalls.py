"""Recognise logging calls in a parsed Python file (shared by the OBS checks).

Covers the stdlib `logging` module and loggers obtained from it, loguru's `logger`, and
receivers conventionally named as loggers (`logger`, `log`, `self.logger`, `audit_logger`).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

LEVEL_METHODS = {
    "trace": "trace",
    "debug": "debug",
    "info": "info",
    "warning": "warning",
    "warn": "warning",
    "error": "error",
    "exception": "error",
    "critical": "critical",
    "fatal": "critical",
}
LEVEL_CONSTANTS = {
    "TRACE": "trace",
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
    "FATAL": "critical",
}
LEVEL_NUMBERS = {5: "trace", 10: "debug", 20: "info", 30: "warning", 40: "error", 50: "critical"}

LOGGER_FACTORIES = ("getLogger", "get_logger", "getChild", "bind")
LOGGER_NAME = re.compile(r"^_*(log|logger)$|_log(ger)?$", re.I)
GUARD_METHODS = {"isEnabledFor", "getEffectiveLevel"}


@dataclass(frozen=True)
class LogCall:
    node: ast.Call
    receiver: str
    method: str
    level: str | None  # None when a `.log(level, ...)` level cannot be resolved statically
    message: ast.AST | None


def _is_factory_call(ctx, node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOGGER_FACTORIES
    ) or (isinstance(node, ast.Call) and (ctx.dotted(node.func) or "").endswith(".getLogger"))


def logger_variables(ctx):
    """Names and attributes assigned from logger factories, e.g. `self.audit = getLogger(...)`."""
    names = set()
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Assign) and _is_factory_call(ctx, node.value):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_factory_call(ctx, node.value):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, (ast.Name, ast.Attribute)):
                names.add(ast.unparse(target))
    return names


def _is_logger(ctx, receiver, known):
    text = ast.unparse(receiver)
    if text in known:
        return True
    dotted = ctx.dotted(receiver)
    if dotted in ("logging", "loguru.logger", "structlog"):
        return True
    if isinstance(receiver, ast.Call):
        return _is_factory_call(ctx, receiver)
    last = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
    return bool(LOGGER_NAME.search(last))


def _level_of(ctx, node):
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return LEVEL_NUMBERS.get(node.value)
    dotted = ctx.dotted(node) or ""
    return LEVEL_CONSTANTS.get(dotted.rsplit(".", 1)[-1])


def _argument(call, index, keyword):
    if len(call.args) > index and not isinstance(call.args[index], ast.Starred):
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def log_call(ctx, node, known):
    """Return a LogCall if `node` is a call on a recognised logger, else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    method = node.func.attr
    if method not in LEVEL_METHODS and method != "log":
        return None
    if not _is_logger(ctx, node.func.value, known):
        return None
    receiver = ast.unparse(node.func.value)
    if method == "log":
        level_node = _argument(node, 0, "level")
        level = _level_of(ctx, level_node) if level_node is not None else None
        return LogCall(node, receiver, method, level, _argument(node, 1, "msg"))
    return LogCall(node, receiver, method, LEVEL_METHODS[method], _argument(node, 0, "msg"))


def log_calls(ctx):
    known = logger_variables(ctx)
    for node in ast.walk(ctx.tree):
        found = log_call(ctx, node, known)
        if found is not None:
            yield found


def _is_level_check(node, flags=frozenset()):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in GUARD_METHODS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "level":
            return True
        if isinstance(sub, (ast.Name, ast.Attribute)) and ast.unparse(sub) in flags:
            return True
    return False


def _level_flags(scope):
    """Names assigned from a level check, e.g. `enabled = logger.isEnabledFor(DEBUG)`."""
    flags = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and _is_level_check(node.value):
            flags.update(ast.unparse(t) for t in node.targets if isinstance(t, (ast.Name, ast.Attribute)))
    return frozenset(flags)


def is_level_guarded(ctx, node):
    """True if `node` sits in the body of an `if` that checks the logger level in the same scope,
    directly or through a flag variable assigned from such a check."""
    flags = None
    child = node
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, (ast.If, ast.IfExp)):
            in_body = child in ancestor.body if isinstance(ancestor, ast.If) else child is ancestor.body
            if in_body:
                if flags is None:
                    flags = _level_flags(_enclosing_scope(ctx, ancestor))
                if _is_level_check(ancestor.test, flags):
                    return True
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return False
        child = ancestor
    return False


def _enclosing_scope(ctx, node):
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return ancestor
    return ctx.tree
