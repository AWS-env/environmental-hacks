"""TST-01: Assertion Roulette, several assertions in one test without explanation messages.

Detector semantics version 1.0.0. Implements PyNose's Assertion Roulette rule ("a test case
contains more than one assertion statement without an explanation/message", Wang et al., ASE
2021, adopted from tsDetect) for unittest and pytest. Python only; static only; analysed code
is never executed.
"""

from __future__ import annotations

import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-01"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-01", "TST01")
MIN_UNDOCUMENTED = 2  # tsDetect/PyNose: "more than one" assertion without a message

# Library assertions with a message parameter (`err_msg`), besides unittest's own methods.
MESSAGE_CAPABLE_CALLS = frozenset({
    "assert_equal", "assert_array_equal", "assert_allclose", "assert_almost_equal",
    "assert_array_almost_equal", "assert_approx_equal", "assert_array_less", "assert_array_max_ulp",
    "assert_",
})
_SHOWN_LINES = 5
# Truthiness assertions report only "False is not true": without a message the failure is opaque.
# Equality-style assertions print both operands, and pytest rewrites bare `assert` to show values.
OPAQUE = frozenset({"assertTrue", "assertFalse", "assert_", "failUnless", "failIf"})

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/AssertionRouletteTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Give each assertion an explanation (`assert cond, \"why\"`, `self.assertEqual(a, b, msg=...)`), or split "
    "the test so each failure names one behaviour (pytest.mark.parametrize, self.subTest). Fewer, clearer "
    "failures mean fewer CI re-runs spent diagnosing which check broke."
)
LIMITATION = (
    "Static pattern only (PyNose Assertion Roulette rule): TST-01 counts assertions that cannot explain "
    "their failure, not CI time. The taxonomy's energy association (SRC-15, Kendall tau 0.615) comes from "
    "JUnit/Maven projects and is not shown to transfer to Python; the waste mechanism is indirect "
    "(diagnosis and re-runs after unclear failures) and no impact is measured. Findings are medium "
    "confidence only when at least two undocumented assertions are truthiness checks (assertTrue/False, "
    "numpy assert_) whose failure says only 'False is not true'; equality-style assertions print both "
    "operands and pytest rewrites bare `assert` statements to show values, so those findings are low. "
    "Only assertions with a known message slot are counted: assert statements, unittest assert* methods "
    "and numpy-style assert_* helpers with err_msg; mock assert_called*, pytest.raises and custom "
    "assertions are ignored. Tests are recognised by default unittest/pytest discovery; files without "
    "recognised tests are evaluated with no findings."
)


def message_capable(assertion):
    """True if the assertion has a slot for an explanation message."""
    name = assertion.name.split(".")[-1]
    if assertion.kind == "assert":
        return True
    if assertion.kind == "unittest":
        return name in testsmells.UNITTEST_ARITY
    if assertion.kind == "call":
        return name in MESSAGE_CAPABLE_CALLS
    return False


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        capable = [a for a in testsmells.assertions(ctx, test.node) if message_capable(a)]
        undocumented = [a for a in capable if a.message is None]
        if len(undocumented) < MIN_UNDOCUMENTED:
            continue
        lines = ", ".join(str(a.node.lineno) for a in undocumented[:_SHOWN_LINES])
        if len(undocumented) > _SHOWN_LINES:
            lines += ", ..."
        opaque = sum(1 for a in undocumented if a.name.split(".")[-1] in OPAQUE)
        hits.append(Hit(
            node=test.node,
            anchor=test.qualname,
            summary=(
                f"{test.qualname} has {len(undocumented)} of {len(capable)} assertions without an explanation "
                f"message (lines {lines}); "
                + (f"{opaque} are truthiness checks whose failure reads only 'False is not true', so a failure "
                   f"does not say which expectation broke." if opaque >= MIN_UNDOCUMENTED else
                   "their failures still show the compared values, so the cost is mainly readability.")
            ),
            confidence="medium" if opaque >= MIN_UNDOCUMENTED else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
