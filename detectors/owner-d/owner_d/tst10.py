"""TST-10: Redundant Assertion, an assertion whose outcome can never change.

Detector semantics version 1.0.0. Implements PyNose's Redundant Assertion rule ("a test case
contains an assertion statement in which (1) the expected and actual parameters of equality
are the same, e.g. assertEqual(X, X), or (2) the assertion of truth is carried out on the
unchangeable object, e.g. assertTrue(True)", Wang et al., ASE 2021, adopted from tsDetect)
for unittest and pytest. Python only; static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import operator
import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-10"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-10", "TST10", "F631", "PT009")

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/RedundantAssertionTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Assert on a value the code under test produced (e.g. assertEqual(result, expected)) or delete the "
    "assertion. A parenthesised `assert (cond, \"msg\")` is a non-empty tuple and always passes: write "
    "`assert cond, \"msg\"`."
)
LIMITATION = (
    "Static pattern only (PyNose Redundant Assertion rule): TST-10 proves an assertion is always true from "
    "its source text (a literal, or a literal compared with the same literal), not that CI time is "
    "wasted; the cost is one trivial check per run, so the environmental link is weak (CI time and maintenance) and no impact is measured. Always-false assertions such as "
    "`assert False` or assertTrue(False) are explicit failure markers and are not reported. Comparisons "
    "count only when both sides are the same literal; `assertEqual(obj, obj)` is not reported because "
    "tests use it to check __eq__ or identity, and differently spelled literals (`0o20 == 16`) exercise the "
    "language itself. Tests are recognised by default unittest/pytest discovery; files without recognised "
    "tests are evaluated with no findings."
)

_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}
_SINGLETONS = (None, True, False)

# unittest methods as conditions: name -> how the operands form a condition.
_ONE = {"assertTrue": "truth", "assert_": "truth", "failUnless": "truth", "assertFalse": "not",
        "failIf": "not", "assertIsNone": "is_none", "assertIsNotNone": "is_not_none"}
_TWO = {
    **{name: ast.Eq for name in (
        "assertEqual", "assertEquals", "failUnlessEqual", "assertAlmostEqual", "assertAlmostEquals",
        "failUnlessAlmostEqual", "assertCountEqual", "assertItemsEqual", "assertListEqual",
        "assertTupleEqual", "assertSetEqual", "assertDictEqual", "assertSequenceEqual",
        "assertMultiLineEqual",
    )},
    "assertGreaterEqual": ast.GtE, "assertLessEqual": ast.LtE, "assertIs": ast.Is,
    "assertIn": ast.In, "assertNotEqual": ast.NotEq, "assertNotEquals": ast.NotEq, "assertIsNot": ast.IsNot,
    "assertGreater": ast.Gt, "assertLess": ast.Lt, "assertNotIn": ast.NotIn,
}
# numpy/pandas-style equality helpers: assert_equal(a, a) is redundant too.
_CALL_EQUALITY = frozenset({
    "assert_equal", "assert_array_equal", "assert_allclose", "assert_almost_equal",
    "assert_array_almost_equal", "assert_frame_equal", "assert_series_equal", "assert_index_equal",
})


def _literal(node):
    """(True, value) for a literal built only from constants, else (False, None)."""
    try:
        if isinstance(node, (ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.UnaryOp)):
            return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    return False, None


def _compare(ctx, op, left, right):
    """(outcome, reason) for `left OP right` when both sides are the same literal, else None.

    Following PyNose (expected and actual parameters are the same), both sides must be literals
    with identical source tokens: `assertEqual(1, 1)` or `assert "a" == "a"`. Literals spelled
    differently (`0o20 == 16`, `'a' in 'abc'`) exercise the language itself and are left alone, as
    are non-literal self-comparisons (`assertEqual(obj, obj)`), which test __eq__/identity.
    """
    left_lit, left_value = _literal(left)
    right_lit, right_value = _literal(right)
    if not (left_lit and right_lit):
        return None
    if testsmells.source_tokens(ctx, left) != testsmells.source_tokens(ctx, right):
        return None
    if op in (ast.Is, ast.IsNot):
        if left_value not in _SINGLETONS:
            return None  # identity of equal non-singleton literals is an implementation detail
        return (op is ast.Is), "compares a literal with itself"
    try:
        return bool(_COMPARE[op](left_value, right_value)), "compares a literal with itself"
    except (KeyError, TypeError):
        return None


def _condition(ctx, node):
    """(outcome, reason) for a truth-tested expression, or None if it can change."""
    if isinstance(node, ast.Tuple) and node.elts and not any(isinstance(e, ast.Starred) for e in node.elts):
        # Truthy whatever its elements are (pyflakes F631).
        return True, "is a non-empty tuple (the parenthesised `assert (cond, msg)` mistake)"
    is_lit, value = _literal(node)
    if is_lit:
        return bool(value), "is a literal"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _condition(ctx, node.operand)
        return None if inner is None else (not inner[0], inner[1])
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        return _compare(ctx, type(node.ops[0]), node.left, node.comparators[0])
    return None


def outcome(ctx, assertion):
    """(always_passes, reason) if the assertion's outcome is fixed by its source, else None."""
    name = assertion.name.split(".")[-1]
    ops = assertion.operands
    if assertion.kind == "assert":
        return _condition(ctx, ops[0])
    if assertion.kind == "unittest" and name in _ONE and len(ops) >= 1:
        kind = _ONE[name]
        if kind in ("is_none", "is_not_none"):
            is_lit, value = _literal(ops[0])
            if not is_lit:
                return None
            return ((value is None) == (kind == "is_none")), "is a literal"
        found = _condition(ctx, ops[0])
        if found is None:
            return None
        return (found[0] if kind == "truth" else not found[0]), found[1]
    if assertion.kind == "unittest" and name in _TWO and len(ops) >= 2:
        return _compare(ctx, _TWO[name], ops[0], ops[1])
    if assertion.kind == "call" and name in _CALL_EQUALITY and len(ops) >= 2:
        return _compare(ctx, ast.Eq, ops[0], ops[1])
    return None


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        for assertion in testsmells.assertions(ctx, test.node):
            if not assertion.operands:
                continue
            found = outcome(ctx, assertion)
            if found is None or not found[0]:
                continue  # unknown, or always false: an explicit failure marker, not redundant
            reason = found[1]
            hits.append(Hit(
                node=assertion.node,
                anchor=f"{test.qualname}:{assertion.name}",
                summary=(
                    f"{assertion.name} in {test.qualname} always passes: its condition {reason}, so it checks "
                    f"nothing about the code under test."
                ),
                confidence="medium",
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
