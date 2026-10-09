"""Shared test-function and assertion recognition for Owner D test-smell checks (TST-xx).

Source is parsed with `ast` by `static.Ctx`; it is never imported or executed. Detection
follows PyNose (Wang et al., ASE 2021, arXiv 2108.04639) and its tsDetect-derived rules,
adapted from the PyCharm PSI to the standard `ast` module:

- Tests are recognised by the default discovery conventions of unittest and pytest:
  `test*` methods of `TestCase` subclasses (any file), and in pytest modules
  (`test_*.py` / `*_test.py`) module-level `test*` functions and `test*` methods of
  `Test*` classes. Custom `python_files`/`python_classes` settings are not read.
- Assertions are `assert` statements, `self.assert*`/`self.fail*` calls, any call whose
  final name contains "assert" (PyNose's Unknown Test rule, which also covers mock
  `assert_called*` and `numpy.testing.assert_*`), `pytest.raises`/`warns`/
  `deprecated_call`/`fail`, and `raise AssertionError` / `raise self.failureException`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from types import SimpleNamespace

from . import static

# unittest's default discovery pattern is test*.py; pytest's python_files default is
# test_*.py and *_test.py. Module-level test functions only exist under pytest.
_PYTEST_MODULE = re.compile(r"(?:^test_.*|.*_test)\.py$")
_TESTCASE_BASE = re.compile(r"TestCase$")
_TESTISH_BASE = re.compile(r"^Test|Tests?$|TestBase$")
_TEST_PREFIX = "test"

# PyNose Util.ASSERT_METHOD_ONE_PARAM / ASSERT_METHOD_TWO_PARAMS, extended with the other
# unittest.TestCase assert methods. Value: (number of asserted operands, positional index
# of `msg`). Legacy aliases map onto the same arity.
UNITTEST_ARITY = {
    **{name: (1, 1) for name in ("assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone",
                                 "assert_", "failUnless", "failIf")},
    **{name: (2, 2) for name in (
        "assertEqual", "assertNotEqual", "assertIs", "assertIsNot", "assertIn", "assertNotIn",
        "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual", "assertCountEqual",
        "assertMultiLineEqual", "assertSequenceEqual", "assertListEqual", "assertTupleEqual",
        "assertSetEqual", "assertDictEqual", "assertIsInstance", "assertNotIsInstance",
        "assertRegex", "assertNotRegex", "assertDictContainsSubset", "assertEquals",
        "assertNotEquals", "failUnlessEqual", "failIfEqual", "assertRegexpMatches",
        "assertNotRegexpMatches", "assertItemsEqual",
    )},
    **{name: (2, 3) for name in ("assertAlmostEqual", "assertNotAlmostEqual", "assertAlmostEquals",
                                 "assertNotAlmostEquals", "failUnlessAlmostEqual", "failIfAlmostEqual")},
}
PYTEST_ASSERTIONS = frozenset({"pytest.raises", "pytest.warns", "pytest.deprecated_call", "pytest.fail"})
_MESSAGE_KEYWORDS = ("msg", "err_msg", "message", "msg_prefix")
_SKIP_DECORATORS = frozenset({"unittest.skip", "pytest.mark.skip", "skip"})
_SKIP_CALLS = frozenset({"pytest.skip", "self.skipTest"})
_HELPER_DEPTH = 3
# Names that by convention check something: helpers like `self.check_html(...)`, `verify(...)`,
# `self._test_roundtrip(...)`, and decorators like matplotlib's `@image_comparison`.
CHECKER_CALL = re.compile(r"^_*(?:check|verify|expect|(?:do_|run_)?test)|_test$", re.I)
CHECKER_DECORATOR = re.compile(r"assert|check|compar|expect|verif|_test$", re.I)


@dataclass(frozen=True)
class TestFunction:
    """A test method or function recognised under default unittest/pytest discovery."""

    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str  # e.g. "TestCart.test_total" or "test_total"
    framework: str  # "unittest" | "pytest"
    cls: ast.ClassDef | None


@dataclass(frozen=True)
class Assertion:
    """One assertion inside a test.

    kind: "assert" (statement), "unittest" (self.assert*/fail*), "pytest" (pytest.raises etc.),
    "call" (other call whose name contains "assert"), "raise" (raise AssertionError).
    operands: the asserted expressions, message excluded. message: the explanation, or None.
    """

    node: ast.AST
    kind: str
    name: str
    operands: tuple
    message: ast.AST | None
    in_with: bool = False  # used as a context manager, e.g. `with self.assertRaises(...)`


def is_pytest_module(path):
    return bool(_PYTEST_MODULE.match(path.replace("\\", "/").rsplit("/", 1)[-1]))


def _base_name(node):
    """Last component of a base-class expression (`unittest.TestCase` -> `TestCase`)."""
    if isinstance(node, ast.Subscript):  # Generic[T]-style bases
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _disables_collection(cls):
    """`__test__ = False` in the class body opts out of both runners' collection."""
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and stmt.value.value is False:
            if any(isinstance(t, ast.Name) and t.id == "__test__" for t in stmt.targets):
                return True
    return False


class _Classes:
    """Classifies the classes of one file as unittest, pytest or non-test classes."""

    def __init__(self, ctx, pytest_module):
        self.ctx = ctx
        self.pytest_module = pytest_module
        self.by_name = {}
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef):
                self.by_name.setdefault(node.name, node)
        self._cache = {}

    def framework(self, cls, seen=frozenset()):
        """'unittest', 'pytest' or None."""
        key = id(cls)
        if key in self._cache:
            return self._cache[key]
        if cls.name in seen or _disables_collection(cls):
            return None
        seen = seen | {cls.name}
        result = None
        names = [_base_name(base) for base in cls.bases]
        if any(name and _TESTCASE_BASE.search(name) for name in names):
            result = "unittest"
        for name in names:
            local = self.by_name.get(name)
            if result is None and local is not None and local is not cls:
                result = self.framework(local, seen)
        if result is None and self.pytest_module:
            if any(n and n not in self.by_name and _TESTISH_BASE.search(n) for n in names):
                result = "unittest"  # an imported `BaseTest`-style base is normally a TestCase subclass
            elif cls.name.startswith("Test") and not any(
                isinstance(s, ast.FunctionDef) and s.name == "__init__" for s in cls.body
            ):
                result = "pytest"  # pytest skips Test* classes that define __init__
        self._cache[key] = result
        return result

    def bases_in_file(self, cls):
        """Same-file base classes, nearest first, for helper-method lookup."""
        order, queue, seen = [], [cls], {id(cls)}
        while queue:
            current = queue.pop(0)
            order.append(current)
            for base in current.bases:
                local = self.by_name.get(_base_name(base))
                if local is not None and id(local) not in seen:
                    seen.add(id(local))
                    queue.append(local)
        return order


def _is_fixture(ctx, func):
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        dotted = ctx.dotted(target) or ""
        if dotted.split(".")[-1] == "fixture":
            return True
    return False


def _classes(ctx):
    cached = ctx.__dict__.get("_tst_classes")
    if cached is None:
        cached = _Classes(ctx, is_pytest_module(ctx.path))
        ctx.__dict__["_tst_classes"] = cached
    return cached


def test_functions(ctx):
    """All recognised tests in the file, in source order (cached on the Ctx)."""
    cached = ctx.__dict__.get("_tst_tests")
    if cached is not None:
        return cached
    classes = _classes(ctx)
    tests = []

    def visit_class(cls, prefix):
        framework = classes.framework(cls)
        qual = f"{prefix}{cls.name}"
        for stmt in cls.body:
            if isinstance(stmt, ast.ClassDef):
                visit_class(stmt, f"{qual}.")
            elif (
                framework
                and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name.startswith(_TEST_PREFIX)
                and not _is_fixture(ctx, stmt)
            ):
                tests.append(TestFunction(stmt, f"{qual}.{stmt.name}", framework, cls))

    for stmt in ctx.tree.body:
        if isinstance(stmt, ast.ClassDef):
            visit_class(stmt, "")
        elif (
            classes.pytest_module
            and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name.startswith(_TEST_PREFIX)
            and not _is_fixture(ctx, stmt)
        ):
            tests.append(TestFunction(stmt, stmt.name, "pytest", None))
    tests.sort(key=lambda test: test.node.lineno)
    ctx.__dict__["_tst_tests"] = tests
    return tests


def is_skipped(ctx, test):
    """True if the test never runs its body: an unconditional skip decorator on the test or its
    class, or a first statement that skips (`pytest.skip(...)`, `self.skipTest(...)`, `raise SkipTest`)."""
    decorators = list(test.node.decorator_list)
    if test.cls is not None:
        decorators += test.cls.decorator_list
    for decorator in decorators:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (ctx.dotted(target) or "") in _SKIP_DECORATORS:
            return True
    body = test.node.body
    if body and ast.get_docstring(test.node, clean=False) is not None:
        body = body[1:]
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
        return (ctx.dotted(first.value.func) or "") in _SKIP_CALLS
    if isinstance(first, ast.Raise) and first.exc is not None:
        target = first.exc.func if isinstance(first.exc, ast.Call) else first.exc
        return (_final_name(target) or "") == "SkipTest"
    return False


def _message_keyword(call):
    for keyword in call.keywords:
        if keyword.arg in _MESSAGE_KEYWORDS:
            return keyword.value
    return None


def _final_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _assertion_from_call(ctx, call, aliases=None):
    func = call.func
    if aliases and isinstance(func, ast.Name) and func.id in aliases:
        func = aliases[func.id]  # `eq = self.assertEqual; eq(a, b)`
    name = _final_name(func)
    if name is None:
        return None
    dotted = ctx.dotted(func) or name
    args = tuple(a for a in call.args if not isinstance(a, ast.Starred))
    receiver = func.value if isinstance(func, ast.Attribute) else None
    if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls") and (
        name.startswith("assert") or name.startswith("fail")
    ):
        arity = UNITTEST_ARITY.get(name)
        message = _message_keyword(call)
        if name == "fail":
            return Assertion(call, "unittest", f"self.{name}", (), args[0] if args else message)
        if arity is not None:
            count, msg_index = arity
            if message is None and len(args) > msg_index:
                message = args[msg_index]
            return Assertion(call, "unittest", f"self.{name}", args[:count], message)
        return Assertion(call, "unittest", f"self.{name}", args, message)
    if dotted in PYTEST_ASSERTIONS:
        message = args[0] if dotted == "pytest.fail" and args else _message_keyword(call)
        return Assertion(call, "pytest", dotted, args, message)
    if "assert" in name.lower():
        return Assertion(call, "call", dotted, args, _message_keyword(call))
    return None


def _assertion_from_raise(ctx, node):
    exc = node.exc
    target = exc.func if isinstance(exc, ast.Call) else exc
    dotted = ctx.dotted(target) if target is not None else None
    if dotted in ("AssertionError", "self.failureException"):
        message = exc.args[0] if isinstance(exc, ast.Call) and exc.args else None
        return Assertion(node, "raise", dotted, (), message)
    return None


def assertion_of(ctx, node, aliases=None):
    """The Assertion that `node` is, or None. `aliases` maps local names to assert callables."""
    if isinstance(node, ast.Assert):
        return Assertion(node, "assert", "assert", (node.test,), node.msg)
    if isinstance(node, ast.Call):
        found = _assertion_from_call(ctx, node, aliases)
        if found is not None and isinstance(ctx.parent(node), ast.withitem):
            found = Assertion(found.node, found.kind, found.name, found.operands, found.message, True)
        return found
    if isinstance(node, ast.Raise) and node.exc is not None:
        return _assertion_from_raise(ctx, node)
    return None


def assert_aliases(ctx, func):
    """Local names bound to an assert callable, e.g. `eq = self.assertEqual`."""
    aliases = {}
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, (ast.Attribute, ast.Name))
        ):
            probe = ast.Call(func=node.value, args=[], keywords=[])
            if _assertion_from_call(ctx, probe) is not None:
                aliases[node.targets[0].id] = node.value
    return aliases


def assertions(ctx, func):
    """Assertions anywhere in a function body (nested defs included, as PyNose does), in source order."""
    aliases = assert_aliases(ctx, func)
    found = []
    for stmt in func.body:
        for node in ast.walk(stmt):
            assertion = assertion_of(ctx, node, aliases)
            if assertion is not None:
                found.append(assertion)
    found.sort(key=lambda a: (a.node.lineno, a.node.col_offset))
    return found


def _helper_targets(ctx, test, func):
    """Same-file functions called from `func`: `self.m()` methods and module-level `f()`."""
    module_funcs = {s.name: s for s in ctx.tree.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
    classes = _classes(ctx)
    for stmt in func.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if (
                isinstance(callee, ast.Attribute)
                and isinstance(callee.value, ast.Name)
                and callee.value.id in ("self", "cls")
                and test.cls is not None
            ):
                for cls in classes.bases_in_file(test.cls):
                    method = next((s for s in cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                                   and s.name == callee.attr), None)
                    if method is not None:
                        yield f"self.{callee.attr}", method
                        break
            elif isinstance(callee, ast.Name) and callee.id in module_funcs:
                yield callee.id, module_funcs[callee.id]


def _checker_by_name(test):
    """A decorator or called helper whose name says it checks something, or None."""
    for decorator in test.node.decorator_list:
        name = _final_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
        if name and CHECKER_DECORATOR.search(name):
            return f"@{name}"
    for stmt in test.node.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                name = _final_name(node.func)
                if name and CHECKER_CALL.search(name):
                    return f"{name}()"
    return None


def delegated_assertion(ctx, test):
    """What asserts on the test's behalf, or None: a same-file helper that asserts (followed up
    to 3 calls deep), or a decorator/helper named like a checker (`@image_comparison`,
    `self.check_html()`, `verify()`), whose body may live in another file."""
    named = _checker_by_name(test)
    if named is not None:
        return named
    seen = {id(test.node)}
    frontier = [test.node]
    for _ in range(_HELPER_DEPTH):
        nxt = []
        for func in frontier:
            for label, helper in _helper_targets(ctx, test, func):
                if id(helper) in seen:
                    continue
                seen.add(id(helper))
                if assertions(ctx, helper):
                    return label
                nxt.append(helper)
        frontier = nxt
    return None


_CHECK_FIELDS = ("CHECK_ID", "DETECTOR_VERSION", "NOQA", "REFERENCES", "RECOMMENDATION", "LIMITATION")


def evaluate_tests(payload, check):
    """`static.evaluate_static`, plus an explicit note for each evaluated file without tests.

    A file in scope that contains no recognised test is evaluated (there is nothing for a
    test-smell check to flag), and the limitation says so, so the clean claim is visible.
    """
    without_tests = set()

    def run(ctx):
        if not test_functions(ctx):
            without_tests.add(ctx.path)
            return []
        return check.run(ctx)

    proxy = SimpleNamespace(run=run, **{field: getattr(check, field) for field in _CHECK_FIELDS})
    result = static.evaluate_static(payload, proxy)
    locators = {
        source.get("scope_id"): source.get("locator")
        for source in payload["sources"]
        if isinstance(source, dict) and source.get("kind") == static.SUPPORTED_KIND
    }
    notes = [
        f"{scope_id}: evaluated; no unittest/pytest test functions recognised, so {check.CHECK_ID} has nothing to flag"
        for scope_id in result["coverage"]["evaluated_scope"]
        if locators.get(scope_id) in without_tests
    ]
    limitations = result["coverage"]["limitations"]
    result["coverage"]["limitations"] = limitations[:-1] + notes + limitations[-1:]
    return result

