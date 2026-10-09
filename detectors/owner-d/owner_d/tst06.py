"""TST-06: Unknown Test, a test that contains no assertion.

Detector semantics version 1.0.0. Implements PyNose's Unknown Test rule ("a test case does
not contain a single assertion statement", Wang et al., ASE 2021, adopted from tsDetect)
for unittest and pytest. Python only; static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-06"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-06", "TST06")

# Fixture names whose tests measure rather than assert (pytest-benchmark, pytest-codspeed).
BENCHMARK_FIXTURES = frozenset({"benchmark", "benchmark_weave", "aio_benchmark"})

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/UnknownTestTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Assert the behaviour the test exercises (an assert statement, self.assert*, or pytest.raises for "
    "expected errors), or delete the test if it only repeats work covered elsewhere. Mark a deliberate "
    "smoke test with `# noqa: TST-06` on its def line."
)
LIMITATION = (
    "Static pattern only (PyNose Unknown Test rule): TST-06 proves a test has no recognisable assertion, not "
    "that its CI time is wasted; such a test still fails on exceptions and may be a deliberate smoke test. "
    "The environmental link is indirect (CI time on tests that check nothing) and no impact is measured. "
    "Helpers are followed only when defined in the same file; helpers and decorators from other files are "
    "trusted by name (check*/verify*/expect*/*_test, @image_comparison-style), so assertions in other "
    "imported helpers, base classes, fixtures or matchers are not seen, and a checker-named helper that "
    "does not assert hides a finding. Tests "
    "are recognised by default unittest/pytest discovery (TestCase subclasses; test* functions and Test* "
    "classes in test_*.py/*_test.py); custom discovery settings are not read. Unconditionally skipped tests "
    "and benchmark-fixture tests are not flagged. Non-descriptive test names are not judged."
)


def _is_empty(func):
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    return all(
        isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        for stmt in body
    )


def _is_doctest_container(func):
    """A test whose docstring holds doctests (run via load_tests/DocTestSuite), e.g. CPython's test_pdb."""
    docstring = ast.get_docstring(func, clean=False)
    return bool(docstring) and ">>>" in docstring


def _uses_benchmark(func):
    args = func.args
    return any(arg.arg in BENCHMARK_FIXTURES for arg in args.posonlyargs + args.args + args.kwonlyargs)


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        if testsmells.is_skipped(ctx, test) or _uses_benchmark(test.node) or _is_doctest_container(test.node):
            continue
        if testsmells.assertions(ctx, test.node) or testsmells.delegated_assertion(ctx, test):
            continue
        what = "has an empty body" if _is_empty(test.node) else "contains no assertion"
        hits.append(Hit(
            node=test.node,
            anchor=test.qualname,
            summary=(
                f"{test.framework} test {test.qualname} {what} (no assert statement, assert*/fail call, "
                f"pytest.raises/warns or AssertionError, including same-file helpers), so it passes "
                f"whenever the code it runs does not raise."
            ),
            confidence="medium",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
