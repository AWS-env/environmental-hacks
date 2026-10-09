"""TST-04: Magic Number Test, unexplained numeric literals as the operands of assertions.

Detector semantics version 1.0.0. Implements PyNose's Magic Number Test rule ("an assertion
method that contains a numeric literal as an argument", Wang et al., ASE 2021, adopted from
tsDetect) for unittest and pytest, extended to the equivalent comparison inside `assert` and
assertTrue/assertFalse. Python only; static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize

from . import static, testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-04"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-04", "TST04", "PLR2004")

# Identity and sentinel values read as themselves (ESLint no-magic-numbers is usually run with
# `ignore: [-1, 0, 1]`); every other numeric literal needs a name, a message or a comment.
ORDINARY = frozenset({-1, 0, 1})
# numpy.testing-style comparisons: the first two positional arguments are actual and desired.
LIBRARY_COMPARISONS = frozenset({
    "assert_equal", "assert_array_equal", "assert_allclose", "assert_almost_equal",
    "assert_array_almost_equal", "assert_approx_equal", "assert_array_less",
})
# A literal compared with len(...) is a count, and one compared with a `status_code`/`returncode`/
# `errno`-like name is a well-known code: both say what the number is.
_CODE_NAME = re.compile(r"^(?:status(?:_?code)?|(?:return|exit)_?code|code|errno)$", re.I)
# Comments that are tool directives, not explanations.
_DIRECTIVE = re.compile(r"#\s*(?:noqa|type:|pragma|pylint:|fmt:|nosec)", re.I)
_SHOWN = 5

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/MagicNumberTestTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://eslint.org/docs/latest/rules/no-magic-numbers",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Name the expected value (a constant, or a variable derived from the test's inputs such as "
    "`expected = price * quantity`), or explain it with the assertion message or a comment. A failure "
    "that says what the number means is diagnosed without re-running CI to find out."
)
LIMITATION = (
    "Static pattern only (PyNose Magic Number Test rule): TST-04 shows that an assertion compares against "
    "a bare numeric literal that nothing in the test names or explains; it does not show that the value is "
    "wrong or that CI time was spent because of it. The taxonomy's energy association (SRC-15, Kendall tau "
    "0.385) comes from JUnit/Maven projects and is not shown to transfer to Python; no impact is measured. "
    "Counted literals are direct operands of unittest assert* methods, numpy.testing comparisons, the "
    "comparison inside `assert`/assertTrue/assertFalse, and pytest.approx(...). -1, 0, 1, booleans, "
    "literals inside expressions or call arguments (`f(3)`, `3 * 14`, `xs[2]`), tolerances (places, delta, "
    "rtol) and assertions with a message, a trailing comment or a comment line directly above are not "
    "counted. Findings are low confidence when every literal is a len() count, a status/exit code, or "
    "repeats a literal from the test's own setup. Tests are recognised by default unittest/pytest "
    "discovery; files without recognised tests are evaluated with no findings."
)


def _number(node):
    """The value of a numeric literal (`42`, `-2.5`, `+3`), or None. Booleans are not numbers."""
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if isinstance(node, ast.Constant) and type(node.value) in (int, float, complex):
        return sign * node.value
    return None


def _unwrap_approx(ctx, node):
    """`pytest.approx(0.3)` -> `0.3`; anything else unchanged."""
    if isinstance(node, ast.Call) and node.args and (ctx.dotted(node.func) or "").endswith("approx"):
        return node.args[0]
    return node


def _comparisons(expr):
    """Operand groups compared in a truthiness expression: `a == 5`, `0 < x < 10`, `a == 1 and b == 2`."""
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        for value in expr.values:
            yield from _comparisons(value)
    elif isinstance(expr, ast.Compare):
        yield [expr.left, *expr.comparators]


def _operand_groups(assertion):
    """The operands an assertion compares with each other, as lists, or nothing."""
    name = assertion.name.split(".")[-1]
    if assertion.kind == "assert":
        yield from _comparisons(assertion.operands[0])
    elif assertion.kind == "unittest" and name in testsmells.UNITTEST_ARITY:
        count, _ = testsmells.UNITTEST_ARITY[name]
        if count == 1 and assertion.operands:
            yield from _comparisons(assertion.operands[0])
        elif count == 2:
            yield list(assertion.operands)
    elif assertion.kind == "call" and name in LIBRARY_COMPARISONS:
        yield list(assertion.operands[:2])


def _explaining_comment_lines(ctx):
    """Line numbers that carry an explanatory comment (tool directives excluded); cached on the Ctx."""
    cached = ctx.__dict__.get("_tst04_comments")
    if cached is not None:
        return cached
    lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(ctx.lines) + "\n").readline):
            if tok.type == tokenize.COMMENT and not _DIRECTIVE.match(tok.string):
                lines.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError):
        pass
    ctx.__dict__["_tst04_comments"] = lines
    return lines


def _documented(ctx, assertion):
    """A message, a comment on the assertion's lines, or a comment-only line directly above it."""
    if assertion.message is not None:
        return True
    comments = _explaining_comment_lines(ctx)
    start, end = assertion.node.lineno, getattr(assertion.node, "end_lineno", assertion.node.lineno)
    if any(line in comments for line in range(start, end + 1)):
        return True
    above = start - 1
    return above in comments and ctx.lines[above - 1].lstrip().startswith("#")


def _self_described(ctx, partners):
    """The other side of the comparison says what the number is: a len() count or a status/exit code."""
    for partner in partners:
        if isinstance(partner, ast.Call) and (ctx.dotted(partner.func) or "") == "len":
            return True
        name = partner.attr if isinstance(partner, ast.Attribute) else getattr(partner, "id", None)
        if name and _CODE_NAME.search(name):
            return True
    return False


def _setup_numbers(ctx, test, inside_assertions):
    """Numeric literal values in the test body outside its assertions (arguments, assignments)."""
    values = set()
    for stmt in test.node.body:
        for node in ast.walk(stmt):
            if id(node) in inside_assertions or isinstance(ctx.parent(node), ast.UnaryOp):
                continue
            value = _number(node)
            if value is not None:
                values.add(value)
    return values


def _spelling(ctx, node):
    """The literal as written (`-2.5`, `1_000`)."""
    return "".join(testsmells.source_tokens(ctx, node).split())


def magic_numbers(ctx, test):
    """[(literal node, value, line, explained_by_context)] for one test, in source order."""
    found = []
    inside = set()
    for assertion in testsmells.assertions(ctx, test.node):
        inside.update(id(node) for node in ast.walk(assertion.node))
        line = assertion.node.lineno
        if _documented(ctx, assertion) or static.is_noqa(NOQA, ctx.lines[line - 1]):
            continue
        for group in _operand_groups(assertion):
            operands = [_unwrap_approx(ctx, operand) for operand in group]
            for index, operand in enumerate(operands):
                value = _number(operand)
                if value is None or value in ORDINARY:
                    continue
                partners = [other for i, other in enumerate(operands) if i != index and _number(other) is None]
                found.append([operand, value, operand.lineno, _self_described(ctx, partners)])
    setup = _setup_numbers(ctx, test, inside)
    for item in found:
        item[3] = item[3] or item[1] in setup
    return [tuple(item) for item in found]


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        found = magic_numbers(ctx, test)
        if not found:
            continue
        shown = ", ".join(f"{_spelling(ctx, node)} (line {line})" for node, _, line, _ in found[:_SHOWN])
        if len(found) > _SHOWN:
            shown += ", ..."
        unexplained = sum(1 for *_, explained in found if not explained)
        plural = "s" if len(found) > 1 else ""
        hits.append(Hit(
            node=test.node,
            anchor=test.qualname,
            summary=(
                f"{test.qualname} asserts against {len(found)} bare numeric literal{plural} with no name, message "
                f"or comment: {shown}; "
                + (f"{unexplained} of {len(found)} {'is' if unexplained == 1 else 'are'} not a len() count, a "
                   f"status/exit code or a value from the test's setup, so a failure does not say what the "
                   f"expected number means." if unexplained else
                   "each is a len() count, a status/exit code or repeats a setup value, so the meaning can be "
                   "read from the test.")
            ),
            confidence="medium" if unexplained else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
