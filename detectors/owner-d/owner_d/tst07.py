"""TST-07: Verbose Test, a test whose body has more statements than the configured limit.

Detector semantics version 1.0.0. Implements the size rule of JNose's Verbose Test ("if a test
method contains statements that exceed a certain threshold, the method is marked as smelly",
MAX_STATEMENTS = 30) for unittest and pytest; Meszaros lists Verbose Test as another name for
Obscure Test. JNose compares the line span of the body; this check counts statements instead,
so blank lines, comments, docstrings and multi-line literals do not make a test verbose. The
limit is a judgment call, so `context.max_test_statements` is required. Python only; static
only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import sys
from types import SimpleNamespace

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-07"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-07", "TST07")
SETTING_KEY = "max_test_statements"
MEDIUM_FACTOR = 2  # more than twice the limit is medium confidence

REFERENCES = (
    "https://github.com/arieslab/jnose-core/blob/main/src/main/java/io/github/arieslab/core/testsmelldetector/testsmell/smell/VerboseTest.java",
    "http://xunitpatterns.com/Obscure%20Test.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Shorten the test so it reads as one behaviour: move in-line setup into fixtures, setUp or builder "
    "helpers, split independent checks into separate tests, and use pytest.mark.parametrize or "
    "self.subTest for repeated cases. Mark a deliberately long scenario test with `# noqa: TST-07` on "
    "its def line."
)
LIMITATION = (
    "Static size rule only (JNose Verbose Test, counted in statements rather than lines): TST-07 proves a "
    "test body has more statements than context.max_test_statements, not that it runs longer or costs "
    "more CI energy. The taxonomy's energy association (SRC-15, Kendall tau 0.246) comes from JUnit/Maven "
    "projects and is not shown to transfer to Python, and no impact is measured. Statements in nested "
    "blocks and nested functions count; the docstring, comments, blank lines and the extra lines of a "
    "multi-line statement do not. setUp methods, fixtures and helpers called by the test are not counted, "
    "so in-line setup moved elsewhere is not judged. Findings are medium confidence only above twice the "
    "limit. Unconditionally skipped tests are not flagged. Tests are recognised by default unittest/pytest "
    "discovery; files without recognised tests are evaluated with no findings."
)


def _body(func):
    """The function body without its docstring."""
    body = func.body
    if body and ast.get_docstring(func, clean=False) is not None:
        body = body[1:]
    return body


def _count(stmts):
    """Statements in `stmts`, including those in nested blocks and nested functions."""
    return sum(1 for stmt in stmts for node in ast.walk(stmt) if isinstance(node, ast.stmt))


def _code_lines(ctx, body):
    """Non-blank, non-comment lines from the first statement to the end of the body."""
    lines = ctx.lines[body[0].lineno - 1:body[-1].end_lineno]
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def _setup_size(ctx, test, body):
    """Statements before the first top-level statement that asserts, or None without an assertion."""
    aliases = testsmells.assert_aliases(ctx, test.node)
    for index, stmt in enumerate(body):
        if any(testsmells.assertion_of(ctx, node, aliases) is not None for node in ast.walk(stmt)):
            return _count(body[:index])
    return None


def run(ctx, settings):
    limit = settings[SETTING_KEY]
    hits = []
    for test in testsmells.test_functions(ctx):
        body = _body(test.node)
        statements = _count(body)
        if statements <= limit or testsmells.is_skipped(ctx, test):
            continue
        setup = _setup_size(ctx, test, body)
        hits.append(Hit(
            node=test.node,
            anchor=test.qualname,
            summary=(
                f"{test.qualname} has {statements} statements over {_code_lines(ctx, body)} code lines, more "
                f"than the configured {limit} (context.{SETTING_KEY}); "
                + (f"the first assertion comes after {setup} {'statement' if setup == 1 else 'statements'} of "
                   f"setup." if setup is not None else
                   "none of them is a recognised assertion.")
            ),
            confidence="medium" if statements > MEDIUM_FACTOR * limit else "low",
        ))
    return hits


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    if SETTING_KEY not in context:
        return None, f"missing required context setting: {SETTING_KEY}"
    value = context[SETTING_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None, f"context.{SETTING_KEY} must be a positive integer"
    return {SETTING_KEY: value}, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, run=lambda ctx: module.run(ctx, settings),
    )
    result = testsmells.evaluate_tests(payload, check)
    if settings is None:
        result.update(
            status="unavailable",
            coverage={"evaluated_scope": [], "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION]},
            findings=[],
            measurements=[],
        )
    return result
