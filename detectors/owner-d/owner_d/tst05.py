"""TST-05: Duplicate Assert, the same assertion repeated within one test.

Detector semantics version 1.0.0. Implements PyNose's Duplicate Assert rule ("a test case
contains more than one assertion statement with the same parameters", Wang et al., ASE 2021,
adopted from tsDetect) for unittest and pytest, restricted to repeats that cannot be a
re-check after a state change. Python only; static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-05"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-05", "TST05")

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/DuplicateAssertionTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Delete the repeated assertion. If the same condition must hold for several inputs, use separate or "
    "parametrized tests (pytest.mark.parametrize, self.subTest) so each case is named. Mark a deliberate "
    "idempotence check (e.g. a cached property read twice) with `# noqa: TST-05`."
)
LIMITATION = (
    "Static pattern only (PyNose Duplicate Assert rule, narrowed): TST-05 reports an assertion that repeats "
    "an earlier one in the same block with only side-effect-free assertions in between. Repeats after other "
    "statements, calls (other than len/isinstance-style builtins), awaits, or code-running assertions "
    "(assertRaises/pytest.raises call forms, custom TestCase assertions) are not reported, because the "
    "checked state may have changed; PyNose would report them. Attribute and property reads are assumed "
    "side-effect free, so a deliberate idempotence check of a cached property is reported. Assertions are "
    "compared structurally and by source tokens, ignoring messages, whitespace and comments. The extra "
    "work is one comparison per run: the environmental link is weak (CI time and maintenance) and no "
    "impact is measured. Tests are recognised by default unittest/pytest discovery; files without recognised tests "
    "are evaluated with no findings."
)

# Builtins that neither consume iterators nor run user hooks beyond simple dunders; any other
# call (including list(), str(), sorted(), which iterate or may evaluate lazy objects) may change state.
PURE_CALLS = frozenset({"len", "isinstance", "issubclass", "type", "id", "callable", "abs", "round"})
# Call-form assertRaises/assertWarns/assertLogs/pytest.raises run the callable they are given.
RUNS_CODE = ("Raises", "Warns", "Logs", "raises", "warns", "deprecated_call")
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _key(ctx, assertion):
    """What an assertion checks: callee, operands and non-message keywords, compared both
    structurally and as source tokens.
    Messages are ignored: two messages on one condition still check it twice."""
    node = assertion.node
    keywords = ()
    if isinstance(node, ast.Call):
        keywords = tuple(
            (kw.arg, ast.dump(kw.value), testsmells.source_tokens(ctx, kw.value))
            for kw in node.keywords if kw.arg not in testsmells.MESSAGE_KEYWORDS
        )
    operands = tuple((ast.dump(op), testsmells.source_tokens(ctx, op)) for op in assertion.operands)
    return assertion.name, operands, keywords


def _may_change_state(assertion):
    """True if evaluating the assertion can run code that changes what is being checked."""
    if any(word in assertion.name for word in RUNS_CODE):
        return True
    # Custom TestCase assertions (assertNumQueries, assertTemplateUsed, ...) may run what they get.
    if assertion.kind == "unittest" and assertion.name.split(".")[-1] not in testsmells.UNITTEST_ARITY:
        return True
    for operand in assertion.operands:
        for node in ast.walk(operand):
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in PURE_CALLS):
                return True
            if isinstance(node, (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)):
                return True
    return False


def _statement_assertion(ctx, stmt, aliases):
    """The assertion a statement consists of (`assert ...` or a bare assertion call), or None."""
    if isinstance(stmt, ast.Assert):
        found = testsmells.assertion_of(ctx, stmt)
    elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        found = testsmells.assertion_of(ctx, stmt.value, aliases)
    else:
        return None
    return found if found is not None and found.operands else None


def _blocks(node):
    """Every statement list inside a test body, excluding nested functions, lambdas and classes."""
    for _, value in ast.iter_fields(node):
        if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
            yield value
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ast.AST) and not isinstance(child, _NESTED_SCOPES):
                yield from _blocks(child)


def duplicates(ctx, test):
    """(first, repeat) Assertion pairs: a repeat within one run of consecutive assertion statements."""
    aliases = testsmells.assert_aliases(ctx, test.node)
    pairs = []
    for block in _blocks(test.node):
        seen = {}
        for stmt in block:
            assertion = _statement_assertion(ctx, stmt, aliases)
            if assertion is None or _may_change_state(assertion):
                seen = {}  # other code ran: the checked state may differ from here on
                continue
            key = _key(ctx, assertion)
            if key in seen:
                pairs.append((seen[key], assertion))
            else:
                seen[key] = assertion
    return sorted(pairs, key=lambda pair: pair[1].node.lineno)


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        for first, repeat in duplicates(ctx, test):
            hits.append(Hit(
                node=repeat.node,
                anchor=f"{test.qualname}:{repeat.name}",
                summary=(
                    f"{test.qualname} repeats the assertion from line {first.node.lineno} ({repeat.name}) "
                    f"with only side-effect-free assertions in between, so the repeat cannot fail on its own."
                ),
                confidence="medium",
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
