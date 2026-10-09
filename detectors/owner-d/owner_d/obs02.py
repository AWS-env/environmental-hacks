"""OBS-02: eager log message construction when the level is disabled.

Detector semantics version 1.0.0. Flags DEBUG/TRACE/INFO logging calls whose message is
built before the logger checks its level (f-string, `%`, `.format()` or concatenation), so
the formatting work runs even when production drops the record. Python only; static only.
"""

from __future__ import annotations

import ast
import sys

from . import static
from .logcalls import is_level_guarded, log_calls
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "OBS-02"
DETECTOR_VERSION = "1.0.0"
NOQA = ("G001", "G002", "G003", "G004", "W1201", "W1202", "W1203", "OBS-02", "OBS02")

# Levels usually disabled in production. WARNING and above are normally emitted, so their
# formatting is not wasted work and they are not flagged.
CONFIDENCE_BY_LEVEL = {"trace": "medium", "debug": "medium", "info": "low"}

REFERENCES = (
    "https://docs.python.org/3/howto/logging.html#optimization",
    "https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/logging-fstring-interpolation.html",
    "https://docs.astral.sh/ruff/rules/logging-f-string/",
    "https://arxiv.org/abs/2604.04809",
)
RECOMMENDATION = (
    "Pass a constant format string and the values as arguments, e.g. logger.debug(\"user=%s\", user_id), "
    "so formatting is deferred until a handler emits the record. Guard genuinely expensive argument "
    "computation with logger.isEnabledFor(logging.DEBUG)."
)
LIMITATION = (
    "Static pattern only: OBS-02 does not know the production log level or how often the call runs, so it "
    "proves eager formatting, not wasted CPU. Arguments passed lazily are not inspected for expensive calls."
)


def _is_str_constant(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _concat_operands(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concat_operands(node.left) + _concat_operands(node.right)
    return [node]


def eager_kind(node):
    """Describe how `node` eagerly builds a message string, or None if it does not."""
    if isinstance(node, ast.JoinedStr):
        if any(isinstance(part, ast.FormattedValue) for part in node.values):
            return "an f-string"
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        if _is_str_constant(node.left) or isinstance(node.left, ast.JoinedStr):
            return "%-formatting"
        return None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and (_is_str_constant(node.func.value) or isinstance(node.func.value, ast.JoinedStr))
    ):
        return "str.format()"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        operands = _concat_operands(node)
        has_text = any(_is_str_constant(op) or isinstance(op, ast.JoinedStr) for op in operands)
        has_value = any(not isinstance(op, ast.Constant) for op in operands)
        if has_text and has_value:
            return "string concatenation"
    return None


def run(ctx):
    hits = []
    for call in log_calls(ctx):
        confidence = CONFIDENCE_BY_LEVEL.get(call.level)
        if confidence is None or call.message is None:
            continue
        kind = eager_kind(call.message)
        if kind is None or is_level_guarded(ctx, call.node):
            continue
        level = call.level.upper()
        hits.append(Hit(
            node=call.node,
            anchor=f"{ctx.qualname(call.node)}:{call.receiver}.{call.method}",
            summary=(
                f"{call.receiver}.{call.method}() builds its {level} message with {kind} before the logger "
                f"checks the level, so the formatting runs even when {level} is disabled."
            ),
            confidence=confidence,
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
