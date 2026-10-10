"""TST-02: Lazy Test, several tests in one test class or module calling the same production method.

Detector semantics version 1.0.0. Ports tsDetect's Lazy Test ("multiple test methods invoke the
same method of the production object", LazyTest.java) to unittest and pytest. PyNose has no Lazy
Test rule. tsDetect reads the paired production class to know its methods; here only the test file
is supplied, so production calls are recognised from its imports: calls through an imported
production name and method calls on objects built from an imported production class. Python only;
static only; analysed code is never executed.
"""

from __future__ import annotations

import ast
import re
import sys
from types import SimpleNamespace

from . import static, testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-02"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-02", "TST02")
SETTING_KEY = "production_packages"
MIN_TESTS = 2  # tsDetect: a production method called from more than one test method
MEDIUM_TESTS = 3  # medium: at least 3 tests with exactly the same production calls
MAX_EVIDENCE = 8
_SHOWN_TESTS = 5
MODULE_GROUP = "<module>"

# Test frameworks, doubles and test-data libraries: calls into them are test tooling, not production.
TEST_TOOLING = frozenset({
    "pytest", "_pytest", "unittest", "mock", "hypothesis", "nose", "nose2", "testtools", "testfixtures",
    "parameterized", "ddt", "freezegun", "time_machine", "responses", "respx", "requests_mock", "httpretty",
    "aioresponses", "vcr", "betamax", "moto", "pyfakefs", "faker", "factory", "model_bakery", "syrupy",
    "snapshottest", "asynctest", "flexmock", "hamcrest", "assertpy", "approvaltests", "sure", "expects",
})
# A module path component that marks test code: tests/, (_)testing/, conftest, test_x, x_test(s), testutils.
_TEST_PART = re.compile(r"^_*(?:tests?|testing|conftest|test_.*|.*_tests?|testutils?|test_?support)$", re.I)
_SETUP_METHODS = frozenset({"setUp", "setUpClass", "asyncSetUp", "setup_method", "setup_class", "setup"})
_RECEIVERS = ("self", "cls")

REFERENCES = (
    "https://github.com/TestSmells/TestSmellDetector/blob/master/src/main/java/testsmell/smell/LazyTest.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "If the tests differ only in input data, merge them into one parametrized test "
    "(pytest.mark.parametrize, self.subTest) so the production setup and call path are written once; "
    "if each checks a distinct behaviour, keep them. Mark a deliberate per-behaviour split with "
    "`# noqa: TST-02` on the class line, a test's def line or the call line."
)
LIMITATION = (
    "Static call-map rule only (tsDetect Lazy Test): TST-02 proves several tests of one class (or the "
    "module-level pytest tests of one file) call the same production method, not that they are redundant "
    "or that merging them saves CI time; many small tests per function are often good practice. The "
    "taxonomy's energy association (SRC-15, Kendall tau 0.449) comes from JUnit/Maven projects and is not "
    "shown to transfer to Python, and no impact is measured. Production calls are recognised from the test "
    "file's imports only: calls through an imported name and method calls on objects built from an imported "
    "class in the test or in setUp/setUpClass/setup_method/class fixtures. There is no type inference, so "
    "objects from factories, pytest fixtures or helpers are not tracked; constructors, stdlib, test tooling, "
    "test-helper modules, assertion calls, calls on self/cls, calls in setUp/fixtures and input builders "
    "(a production call passed, directly or through a variable, to another production call) are not "
    "counted, and helpers are not followed. Setup-like production calls in the test body (e.g. os.mkdir "
    "when the stdlib is the code under test) are still counted. Findings are medium confidence only when "
    "at least 3 tests share the target and all of them make exactly the same production calls. "
    "Unconditionally skipped tests are not counted. Tests are recognised by default unittest/pytest "
    "discovery; files without recognised tests are evaluated with no findings."
)
NO_PACKAGES_NOTE = (
    "context.production_packages not supplied: every import that is not stdlib, test tooling or a "
    "test-helper module counts as production, including third-party libraries."
)


def _is_test_part(part):
    return bool(_TEST_PART.match(part))


def _class_like(name):
    """CamelCase final name: a class, so calling it constructs an object."""
    return name.lstrip("_")[:1].isupper()


def _absolute_production(path, packages):
    """True if an absolute import path names production code."""
    parts = path.split(".")
    if any(_is_test_part(part) for part in parts):
        return False
    if packages is not None:
        return parts[0] in packages
    root = parts[0]
    return not (root in sys.stdlib_module_names or root in TEST_TOOLING or root.startswith("pytest_"))


def _production_imports(ctx, packages):
    """Local name -> dotted path, for names imported from production code.

    Relative imports count when they resolve outside the test file's test directories
    (`from ..shop.cart import total` in tests/), not inside them (`from .helpers import build`).
    """
    test_dir = [part for part in ctx.path.replace("\\", "/").split("/")[:-1] if part not in ("", ".")]
    names = {}
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else local
                if _absolute_production(alias.name, packages):
                    names[local] = target
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if node.level == 0:
                    if node.module and _absolute_production(f"{node.module}.{alias.name}", packages):
                        names[local] = f"{node.module}.{alias.name}"
                    continue
                up = node.level - 1
                if up > len(test_dir):
                    continue  # resolves above the supplied path: cannot tell production from test code
                parts = test_dir[:len(test_dir) - up] + (node.module.split(".") if node.module else []) + [alias.name]
                if not any(_is_test_part(part) for part in parts):
                    names[local] = ".".join(parts)
    for stmt in ctx.tree.body:  # a same-file definition shadows the import (a local helper or class)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.pop(stmt.name, None)
    return names


class _Resolver:
    """Resolves call expressions in one test to dotted production paths."""

    def __init__(self, imports, attrs, objects=None):
        self.imports = imports  # local name -> production path
        self.attrs = attrs  # self.<attr> -> production class path
        self.objects = objects or {}  # local variable -> production class path

    def path(self, func):
        """Dotted production path of a callee expression, or None."""
        parts = []
        node = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        parts.reverse()
        if isinstance(node, ast.Call):
            base = self.instance_of(node)
            if base is None or not parts:
                return None
        elif isinstance(node, ast.Name) and node.id in _RECEIVERS:
            if not parts or parts[0] not in self.attrs or len(parts) < 2:
                return None
            base, parts = self.attrs[parts[0]], parts[1:]
        elif isinstance(node, ast.Name) and node.id in self.objects:
            if not parts:
                return None
            base = self.objects[node.id]
        elif isinstance(node, ast.Name) and node.id in self.imports:
            base = self.imports[node.id]
            if any(_is_test_part(part) for part in parts[:-1]):
                return None  # `import shop.cart` also binds `shop`, but shop.tests.helpers is test code
        else:
            return None
        return ".".join([base, *parts])

    def instance_of(self, call):
        """The production class path if `call` constructs an object of an imported class."""
        path = self.path(call.func)
        if path is not None and _class_like(path.rsplit(".", 1)[-1]):
            return path
        return None

    def target(self, call):
        """The production method/function `call` invokes, or None (constructors are not counted)."""
        path = self.path(call.func)
        if path is None or _class_like(path.rsplit(".", 1)[-1]):
            return None
        return path


def _is_fixture(func):
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "fixture":
            return True
    return False


def _bind(resolver, func, objects, attrs):
    """Record variables and self attributes assigned a production object in `func`."""
    for node in ast.walk(func):
        pairs = []
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            pairs = [(node.target, node.value)]
        elif isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            pairs = [(node.optional_vars, node.context_expr)]
        for target, call in pairs:
            cls = resolver.instance_of(call)
            if cls is None:
                continue
            if isinstance(target, ast.Name) and objects is not None:
                objects[target.id] = cls
            elif (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in _RECEIVERS
                and attrs is not None
            ):
                attrs[target.attr] = cls


def _setup_attrs(imports, cls):
    """self.<attr> -> production class for objects built in setUp-style methods and class fixtures."""
    attrs = {}
    resolver = _Resolver(imports, {})
    for stmt in cls.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            stmt.name in _SETUP_METHODS or _is_fixture(stmt)
        ):
            _bind(resolver, stmt, None, attrs)
    return attrs


def _shadowed(func):
    """Parameters (pytest fixtures) and plain local assignments that hide imported names."""
    args = func.args
    names = {arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs}
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _in_production_args(ctx, node, resolver):
    """True if `node` sits in the arguments of a production call or constructor."""
    child = node
    for ancestor in ctx.ancestors(node):
        if isinstance(ancestor, static.SCOPE_NODES):
            return False
        if isinstance(ancestor, ast.Call) and child is not ancestor.func and resolver.path(ancestor.func) is not None:
            return True
        child = ancestor
    return False


def _input_variables(ctx, func, resolver):
    """Local names whose every use is as an argument of a production call or constructor."""
    uses = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            uses.setdefault(node.id, []).append(node)
    return {name for name, loads in uses.items() if all(_in_production_args(ctx, load, resolver) for load in loads)}


def _builds_input(ctx, call, resolver, inputs):
    """True if `call` only builds input for the code under test: it is an argument of another
    production call or constructor (`bincount(np.array([1]))`), or its result is assigned to a
    variable used only that way (`g = path_graph(5); assert connectivity(g) == 1`)."""
    if _in_production_args(ctx, call, resolver):
        return True
    parent = ctx.parent(call)
    return (
        isinstance(parent, ast.Assign)
        and len(parent.targets) == 1
        and isinstance(parent.targets[0], ast.Name)
        and parent.targets[0].id in inputs
    )


def _production_calls(ctx, test, imports, attrs):
    """Production target -> first call node in the test body."""
    shadowed = _shadowed(test.node)
    visible = {name: path for name, path in imports.items() if name not in shadowed}
    objects, own_attrs = {}, dict(attrs)
    _bind(_Resolver(visible, attrs), test.node, objects, own_attrs)
    resolver = _Resolver(visible, own_attrs, objects)
    inputs = _input_variables(ctx, test.node, resolver)
    found = {}
    for stmt in test.node.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if static.is_noqa(NOQA, ctx.lines[node.lineno - 1]):
                continue
            if testsmells.assertion_of(ctx, node) is not None:
                continue
            target = resolver.target(node)
            if target is None or _builds_input(ctx, node, resolver, inputs):
                continue
            first = found.get(target)
            if first is None or (node.lineno, node.col_offset) < (first.lineno, first.col_offset):
                found[target] = node
    return found


def _noqa_at(ctx, line):
    return static.is_noqa(NOQA, ctx.lines[line - 1])


def _groups(ctx):
    """Counted tests by group: each test class, and the module-level pytest functions."""
    groups = {}
    for test in testsmells.test_functions(ctx):
        if testsmells.is_skipped(ctx, test) or _noqa_at(ctx, test.node.lineno):
            continue
        if test.cls is not None and _noqa_at(ctx, test.cls.lineno):
            continue
        groups.setdefault(id(test.cls) if test.cls is not None else None, []).append(test)
    return groups.values()


def _summary(target, users, where, profiles):
    names = [test.qualname.rsplit(".", 1)[-1] for test, _ in users]
    shown = ", ".join(names[:_SHOWN_TESTS]) + (", ..." if len(names) > _SHOWN_TESTS else "")
    text = f"{len(users)} {where} call the production method {target} ({shown}). "
    if len(profiles) == 1:
        count = len(next(iter(profiles)))
        same = (
            f"Each makes exactly the same production calls ({count} {'target' if count == 1 else 'targets'})"
        )
        if len(users) >= MEDIUM_TESTS:
            return text + same + ", so they differ only in data and one parametrized test could cover them."
        return text + same + ", but only two tests share it."
    return text + "Their other production calls differ, so they may check distinct behaviours."


def run(ctx, settings, evidence=None):
    """Lazy Test hits for one file. `evidence` collects the other tests' call sites per hit."""
    imports = _production_imports(ctx, settings.get(SETTING_KEY))
    if not imports:
        return []
    hits = []
    for tests in _groups(ctx):
        cls = tests[0].cls
        attrs = _setup_attrs(imports, cls) if cls is not None else {}
        group = tests[0].qualname.rsplit(".", 1)[0] if cls is not None else MODULE_GROUP
        where = f"tests in test class {group}" if cls is not None else "module-level tests"
        calls = [_production_calls(ctx, test, imports, attrs) for test in tests]
        users_by_target = {}
        for test, found in zip(tests, calls):
            for target, node in found.items():
                users_by_target.setdefault(target, []).append((test, node, found))
        for target, users in users_by_target.items():
            if len(users) < MIN_TESTS:
                continue
            profiles = {frozenset(found) for _, _, found in users}
            pairs = [(test, node) for test, node, _ in users]
            anchor = f"{group}:{target}"
            hits.append(Hit(
                node=pairs[0][1],
                anchor=anchor,
                summary=_summary(target, pairs, where, profiles),
                confidence="medium" if len(users) >= MEDIUM_TESTS and len(profiles) == 1 else "low",
            ))
            if evidence is not None:
                evidence[(ctx.path, anchor, pairs[0][1].lineno)] = [
                    ctx.evidence_lines(node) for _, node in pairs[1:MAX_EVIDENCE]
                ]
    return hits


def _attach_evidence(result, evidence):
    """Add the call site in each further sharing test as its own static evidence item."""
    for finding in result["findings"]:
        first = finding["evidence"][0]
        anchor = re.sub(r"#\d+$", "", finding["identity"])
        for start, text in evidence.get((first["locator"], anchor, first["line_start"]), []):
            finding["evidence"].append({**first, "line_start": start, "value": text})


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    if SETTING_KEY not in context:
        return {SETTING_KEY: None}, None
    value = context[SETTING_KEY]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.isidentifier() for item in value)
    ):
        return None, f"context.{SETTING_KEY} must be a nonempty list of top-level package names"
    return {SETTING_KEY: frozenset(value)}, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    evidence = {}
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION,
        run=lambda ctx: module.run(ctx, settings or {}, evidence),
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
    _attach_evidence(result, evidence)
    if settings[SETTING_KEY] is None and result["coverage"]["evaluated_scope"]:
        limitations = result["coverage"]["limitations"]
        limitations.insert(len(limitations) - 1, NO_PACKAGES_NOTE)
    return result
