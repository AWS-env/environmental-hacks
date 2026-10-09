"""TST-09: Conditional Test Logic, assertions that run only under a condition or loop.

Detector semantics version 1.0.0. Implements PyNose's Conditional Test Logic rule ("a test case
contains one or more control statements (i.e., if, for, while)", Wang et al., ASE 2021, adopted
from tsDetect), in the refined form of PyNose's current inspection: a control structure is
reported only when it contains an assertion, so whether or how often the check runs depends
on runtime state. Python only; static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-09"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-09", "TST09", "PT018")

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/ConditionalTestLogicTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://github.com/jest-community/eslint-plugin-jest/blob/main/docs/rules/no-conditional-in-test.md",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Make every assertion run unconditionally: split branches into separate tests, use "
    "pytest.mark.parametrize or self.subTest for loops over cases, and use skip markers instead of "
    "if-guards. If a loop is intended, assert the collection is non-empty first."
)
LIMITATION = (
    "Static pattern only (PyNose Conditional Test Logic, refined to control structures that contain an "
    "assertion): TST-09 shows that whether, or how often, an assertion runs depends on runtime state; it "
    "does not show that a check was skipped in CI. The environmental link is weak (CI time spent on tests "
    "that may check nothing, plus maintenance) and no impact is measured. Branches where every path "
    "asserts and loops over literal collections always assert and are low confidence. Loops whose body "
    "uses self.subTest/subtests.test, conditionals whose only assertion is a fail()/AssertionError (a "
    "hand-written assertion), and control flow inside nested functions are not reported. Tests are "
    "recognised by default unittest/pytest discovery; files without recognised tests are evaluated with "
    "no findings."
)

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_LABEL = {"if": "an if", "for": "a for loop", "while": "a while loop", "if-expression": "a conditional expression",
          "comprehension": "a comprehension", "match": "a match statement"}
_SUBTEST_CALLS = ("subTest", "test")  # self.subTest(...), pytest-subtests' subtests.test(...)
_KIND = {ast.If: "if", ast.For: "for", ast.AsyncFor: "for", ast.While: "while", ast.IfExp: "if-expression",
         ast.ListComp: "comprehension", ast.SetComp: "comprehension", ast.DictComp: "comprehension",
         ast.GeneratorExp: "comprehension"}


def _walk(node):
    """Descendants of node, not entering nested functions, lambdas or classes."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _NESTED_SCOPES):
            yield from _walk(child)


def _asserts(ctx, nodes, aliases):
    """True if any node (or descendant) is a real assertion; fail()/AssertionError alone do not count,
    because `if bad: self.fail()` is itself a hand-written assertion of the condition."""
    for root in nodes:
        for node in [root, *_walk(root)]:
            found = testsmells.assertion_of(ctx, node, aliases)
            if found is None or found.kind == "raise" or found.name.split(".")[-1] == "fail":
                continue
            return True
    return False


def _uses_subtest(ctx, loop):
    for node in _walk(loop):
        if isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            func = node.context_expr.func
            if isinstance(func, ast.Attribute) and func.attr in _SUBTEST_CALLS and isinstance(func.value, ast.Name) \
                    and func.value.id in ("self", "subtests"):
                return True
    return False


_WRAPPERS = frozenset({"enumerate", "zip", "sorted", "reversed", "list", "tuple", "set", "chain", "product",
                       "permutations", "combinations", "cycle"})


def _constants(ctx):
    """Module- and class-level names bound once to a literal display (fixed test-case tables)."""
    cached = ctx.__dict__.get("_tst09_constants")
    if cached is None:
        cached, counts = {}, {}
        bodies = [ctx.tree.body] + [n.body for n in ast.walk(ctx.tree) if isinstance(n, ast.ClassDef)]
        for body in bodies:
            for stmt in body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    name = stmt.targets[0].id
                    counts[name] = counts.get(name, 0) + 1
                    cached[name] = stmt.value
        cached = {name: value for name, value in cached.items() if counts[name] == 1}
        ctx.__dict__["_tst09_constants"] = cached
    return cached


def _fixed_iterable(ctx, node, depth=0):
    """An iterable fixed by the source and non-empty: a literal display, range(<positive const>),
    a method call on a literal ('a b'.split()), a module/class constant bound to such a literal, or
    enumerate/zip/sorted/chain/... and `+` of fixed iterables. Its loop always runs its assertions."""
    if depth > 5:
        return False
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes)) and bool(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _fixed_iterable(ctx, node.left, depth + 1) or _fixed_iterable(ctx, node.right, depth + 1)
    if isinstance(node, ast.Name):
        value = _constants(ctx).get(node.id)
        return value is not None and _fixed_iterable(ctx, value, depth + 1)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
        value = _constants(ctx).get(node.attr)
        return value is not None and _fixed_iterable(ctx, value, depth + 1)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name == "range":
            stop = node.args[1] if len(node.args) > 1 else node.args[0] if node.args else None
            start = node.args[0] if len(node.args) > 1 else ast.Constant(0)
            return (isinstance(stop, ast.Constant) and isinstance(start, ast.Constant)
                    and isinstance(stop.value, int) and isinstance(start.value, int) and stop.value > start.value)
        if name in _WRAPPERS and node.args:
            return all(_fixed_iterable(ctx, arg, depth + 1) for arg in node.args if not isinstance(arg, ast.Starred))
        if isinstance(func, ast.Attribute) and name in ("split", "items", "keys", "values"):
            return _fixed_iterable(ctx, func.value, depth + 1)
    return False


def _if_all_paths_assert(ctx, node, aliases):
    """True when both the body and every else/elif branch contain an assertion."""
    if not node.orelse or not _asserts(ctx, node.body, aliases):
        return False
    if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        return _if_all_paths_assert(ctx, node.orelse[0], aliases)
    return _asserts(ctx, node.orelse, aliases)


def _is_elif(ctx, node):
    parent = ctx.parent(node)
    return isinstance(parent, ast.If) and len(parent.orelse) == 1 and parent.orelse[0] is node


def _assessment(ctx, node, aliases):
    """(confidence, explanation) for a control structure that contains an assertion, or None."""
    if isinstance(node, ast.If):
        if _is_elif(ctx, node) or not _asserts(ctx, [node], aliases):
            return None
        if _if_all_paths_assert(ctx, node, aliases):
            return "low", "the assertions that run depend on the branch taken (every branch asserts)"
        return "medium", "on some path through the if no assertion runs"
    if isinstance(node, ast.IfExp):
        if not _asserts(ctx, [node.body, node.orelse], aliases):
            return None
        return "medium", "the conditional expression decides which assertion runs"
    if isinstance(node, ast.Match):
        if not _asserts(ctx, [node], aliases):
            return None
        return "medium", "the match statement decides which assertions run"
    if isinstance(node, _LOOPS):
        if not _asserts(ctx, node.body, aliases) or _uses_subtest(ctx, node):
            return None
        if isinstance(node, ast.While):
            return "medium", "the loop condition decides how often the assertions run"
        if _fixed_iterable(ctx, node.iter):
            return "low", "the assertions run once per case of a fixed collection instead of a parametrized test"
        return "medium", "the assertions run once per item, so an empty iterable checks nothing"
    if isinstance(node, _COMPREHENSIONS):
        element = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        if not _asserts(ctx, element, aliases):
            return None
        if all(_fixed_iterable(ctx, gen.iter) for gen in node.generators) and not any(gen.ifs for gen in node.generators):
            return "low", "the assertions run once per case of a fixed collection inside a comprehension"
        return "medium", "the assertions run inside a comprehension, so an empty or filtered iterable checks nothing"
    return None


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        aliases = testsmells.assert_aliases(ctx, test.node)
        for node in _walk(test.node):
            if not isinstance(node, (ast.If, ast.IfExp, ast.Match, *_LOOPS, *_COMPREHENSIONS)):
                continue
            found = _assessment(ctx, node, aliases)
            if found is None:
                continue
            confidence, why = found
            kind = "match" if isinstance(node, ast.Match) else _KIND[type(node)]
            hits.append(Hit(
                node=node,
                anchor=f"{test.qualname}:{kind}",
                summary=f"{test.qualname} has assertions inside {_LABEL[kind]}: {why}.",
                confidence=confidence,
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
