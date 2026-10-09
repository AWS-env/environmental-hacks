"""TST-03: Eager Test, a test that calls more distinct production methods than the configured limit.

Detector semantics version 1.0.0. Adapts tsDetect's Eager Test ("a test method invokes several
methods of the production object"; smelly when the production-call count exceeds a threshold)
to unittest and pytest. PyNose has no Eager Test because Python has no reliable test-to-production
class mapping, so the caller names the production code (`context.production_packages`) and the
limit (`context.max_production_methods`); both are required. Python only; static only; analysed
code is never imported or executed.
"""

from __future__ import annotations

import ast
import re
import sys
from types import SimpleNamespace

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-03"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-03", "TST03")
LIMIT_KEY = "max_production_methods"
PACKAGES_KEY = "production_packages"
MEDIUM_FACTOR = 2  # more than twice the limit is medium confidence
_SHOWN = 8  # production calls listed in a summary

REFERENCES = (
    "https://github.com/TestSmells/TestSmellDetector/blob/master/src/main/java/testsmell/smell/EagerTest.java",
    "https://testsmells.org/pages/testsmells.html",
    "https://doi.org/10.1145/3379597.3387453",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Split the test so each test exercises one production behaviour and is named after it; move shared "
    "arrangement into setUp or a fixture, and use pytest.mark.parametrize or self.subTest for repeated "
    "cases. Mark a deliberate workflow/scenario test with `# noqa: TST-03` on its def line."
)
LIMITATION = (
    "Static call-count rule only (adapted from tsDetect Eager Test): TST-03 proves a test calls more distinct "
    "production methods/functions than context.max_production_methods, not that it runs longer or costs more "
    "CI energy. The taxonomy's energy association (SRC-15, Kendall tau 0.432) comes from JUnit/Maven projects "
    "and is not shown to transfer to Python, and no impact is measured. Production code is whatever is imported "
    "from context.production_packages (module paths with a test/tests/testing/conftest segment excluded). There "
    "is no type inference: a production object is a local, `with` target, same-file pytest fixture parameter or "
    "self attribute bound directly to a call of a production callable (or a chain rooted at one); objects "
    "returned by production methods, fixtures from conftest.py or other files and helpers called by the test "
    "are not followed, so counts are lower bounds. Constructors (CapWords calls), assertion calls, mocks, "
    "builtins, non-production imports, attribute reads and method names patched in the test are not counted; "
    "production calls inside assertion arguments are. Same-named methods of one class count once. Findings are "
    "medium confidence only above twice the limit. Unconditionally skipped tests are not flagged. Tests are "
    "recognised by default unittest/pytest discovery; files without recognised tests are evaluated with no "
    "findings."
)

SETUP_METHODS = frozenset({
    "setUp", "asyncSetUp", "setUpClass", "setUpTestData", "setup_method", "setup_class", "setup",
})
_TESTISH_SEGMENT = re.compile(r"^_*(?:tests?|testing|conftest)$|^_*tests?_|_tests?$")
_DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_PATCH_NAMES = frozenset({"patch", "object", "setattr", "delattr"})


def _is_class_name(name):
    """Capitalised names are classes by PEP 8 (`Cart`, `UUID`, Django's `Q`): calling one constructs."""
    stripped = name.lstrip("_")
    return bool(stripped) and stripped[0].isupper()


def _testish(parts):
    return any(_TESTISH_SEGMENT.search(part) for part in parts)


class _Production:
    """Which imported names in one file are production code, and how to resolve them."""

    def __init__(self, ctx, packages):
        self.ctx = ctx
        self.packages = packages
        self.aliases = dict(ctx.aliases)
        self.aliases.update(self._relative_aliases())

    def _relative_aliases(self):
        """`from ..cart import Cart` resolved against the locator's directories."""
        directories = self.ctx.path.replace("\\", "/").split("/")[:-1]
        tops = {package.split(".")[0] for package in self.packages}
        aliases = {}
        for node in ast.walk(self.ctx.tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            up = node.level - 1
            if up > len(directories):
                continue
            base = directories[:len(directories) - up] + (node.module.split(".") if node.module else [])
            start = next((i for i, part in enumerate(base) if part in tops), None)
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                if start is None or _testish(base):
                    aliases[name] = f"<relative>.{alias.name}"  # known import, never production
                else:
                    aliases[name] = ".".join(base[start:] + [alias.name])
        return aliases

    def is_production(self, dotted):
        if not dotted or _testish(dotted.split(".")):
            return False
        return any(dotted == package or dotted.startswith(package + ".") for package in self.packages)

    def path(self, node, local):
        """Dotted import path of a Name/Attribute chain rooted at an import not re-bound locally."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id in local or node.id not in self.aliases:
            return None
        parts.append(self.aliases[node.id])
        return ".".join(reversed(parts))


class _Scope:
    """Production objects visible in one function: locals, fixture parameters and self attributes."""

    def __init__(self, prod, func, objects=None, attrs=None):
        self.prod = prod
        self.objects = dict(objects or {})  # local name -> origin
        self.attrs = attrs if attrs is not None else {}  # self/cls attribute -> origin
        self.local = _bound_names(func)
        self.bind(func)

    def bind(self, func):
        """Record names bound directly to a production call, flow-insensitively."""
        for node in ast.walk(func):
            targets, value = (), None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = (node.target,), node.value
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                targets, value = (node.optional_vars,), node.context_expr
            origin = self.created(value)
            if origin is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    self.objects[target.id] = origin
                elif _self_attr(target):
                    self.attrs[target.attr] = origin

    def created(self, node):
        """Origin of the object returned by a direct call of a production class or function."""
        if not isinstance(node, ast.Call):
            return None
        path = self.prod.path(node.func, self.local)
        if path is None or not self.prod.is_production(path):
            return None
        return path if _is_class_name(path.rsplit(".", 1)[-1]) else f"{path}()"

    def receiver_origin(self, node):
        if isinstance(node, ast.Name) and node.id in self.objects:
            return self.objects[node.id]
        if _self_attr(node):
            return self.attrs.get(node.attr)
        if isinstance(node, ast.Call):
            origin = self.created(node)
            if origin is None and isinstance(node.func, ast.Attribute):
                origin = self.receiver_origin(node.func.value)  # chain rooted at a production object
            return origin
        path = self.prod.path(node, self.local)
        if path is not None and self.prod.is_production(path):
            return path  # module (`pricing.tax`) or class (`Cart.from_dict`)
        return None

    def production_call(self, call):
        """The distinct-call key for a production function/method call, or None."""
        func = call.func
        if isinstance(func, ast.Name):
            path = self.prod.path(func, self.local)
            if path is not None and self.prod.is_production(path) and not _is_class_name(func.id):
                return path
            return None
        if not isinstance(func, ast.Attribute) or _is_class_name(func.attr):
            return None
        origin = self.receiver_origin(func.value)
        return f"{origin}.{func.attr}" if origin is not None else None


def _self_attr(node):
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")


def _final_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _bound_names(func):
    """Parameters (including nested functions and lambdas) and every name assigned in the function."""
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return names


def _is_fixture(ctx, func):
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (ctx.dotted(target) or "").split(".")[-1] == "fixture":
            return True
    return False


def _fixture_name(func):
    for decorator in func.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    return keyword.value.value
    return func.name


class _File:
    """Per-file state: production imports, fixtures and setUp attributes (cached per class)."""

    def __init__(self, ctx, packages):
        self.ctx = ctx
        self.prod = _Production(ctx, packages)
        self.module_fixtures = self._fixture_objects(ctx.tree)
        self.classes = {}
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef):
                self.classes.setdefault(node.name, node)
        self._class_state = {}

    def _fixture_objects(self, container):
        """Same-file pytest fixtures (in `container`'s body) that return or yield a production object."""
        found = {}
        for stmt in container.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _is_fixture(self.ctx, stmt):
                continue
            scope = _Scope(self.prod, stmt)
            for node in ast.walk(stmt):
                value = node.value if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) else None
                origin = scope.objects.get(value.id) if isinstance(value, ast.Name) else scope.created(value)
                if origin is not None:
                    found[_fixture_name(stmt)] = origin
                    break
        return found

    def _chain(self, cls):
        """The class and its same-file bases, nearest first."""
        order, queue, seen = [], [cls], {id(cls)}
        while queue:
            current = queue.pop(0)
            order.append(current)
            for base in current.bases:
                local = self.classes.get(_final_name(base))
                if local is not None and id(local) not in seen:
                    seen.add(id(local))
                    queue.append(local)
        return order

    def class_state(self, cls):
        """(fixtures, self attributes) a test method of `cls` can see as production objects."""
        key = id(cls)
        if key not in self._class_state:
            fixtures, attrs = dict(self.module_fixtures), {}
            for current in reversed(self._chain(cls)):  # nearest class wins
                fixtures.update(self._fixture_objects(current))
                for stmt in current.body:
                    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                        # a class attribute `cart = Cart()` is read as self.cart
                        attrs.update(_Scope(self.prod, ast.Module(body=[stmt], type_ignores=[])).objects)
                    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name in SETUP_METHODS:
                        _Scope(self.prod, stmt, attrs=attrs)
            self._class_state[key] = (fixtures, attrs)
        return self._class_state[key]


def _patched_names(test):
    """Names a test replaces with mocks: patch.object(X, "m"), @patch("a.b.m"), monkeypatch.setattr(X, "m", v)."""
    nodes = list(ast.walk(test.node))
    for root in [test.node] + ([test.cls] if test.cls is not None else []):
        for decorator in root.decorator_list:
            nodes.extend(ast.walk(decorator))
    names = set()
    for node in nodes:
        if not isinstance(node, ast.Call) or _final_name(node.func) not in _PATCH_NAMES:
            continue
        args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None for a in node.args[:2]]
        if _final_name(node.func) == "patch" and args and args[0]:
            names.add(args[0].rsplit(".", 1)[-1])  # patch("pkg.mod.Cart.save")
        elif len(args) == 2 and args[1]:
            names.add(args[1])  # patch.object(Cart, "save"), monkeypatch.setattr(Cart, "save", v)
        elif args and args[0]:
            names.add(args[0].rsplit(".", 1)[-1])  # monkeypatch.setattr("pkg.mod.Cart.save", v)
    return names


def production_calls(state, test):
    """Distinct production calls in a test, in first-call order."""
    ctx = state.ctx
    fixtures, attrs = state.class_state(test.cls) if test.cls is not None else (state.module_fixtures, {})
    args = test.node.args
    params = {arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs}
    objects = {name: origin for name, origin in fixtures.items() if name in params}
    scope = _Scope(state.prod, test.node, objects=objects, attrs=dict(attrs))
    patched = _patched_names(test)
    aliases = testsmells.assert_aliases(ctx, test.node)
    calls = {}
    for stmt in test.node.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call) or testsmells.assertion_of(ctx, node, aliases) is not None:
                continue
            key = scope.production_call(node)
            if key is None or key.rsplit(".", 1)[-1] in patched:
                continue
            calls.setdefault(key, (node.lineno, node.col_offset))
    return sorted(calls, key=calls.get)


def _display(key):
    """`shop.cart.Cart.add` -> `Cart.add`; `shop.make_cart().add` -> `make_cart().add`."""
    return ".".join(key.split(".")[-2:])


def run(ctx, settings):
    limit = settings[LIMIT_KEY]
    state = _File(ctx, settings[PACKAGES_KEY])
    hits = []
    for test in testsmells.test_functions(ctx):
        if testsmells.is_skipped(ctx, test):
            continue
        calls = production_calls(state, test)
        if len(calls) <= limit:
            continue
        shown = ", ".join(_display(key) for key in calls[:_SHOWN]) + (", ..." if len(calls) > _SHOWN else "")
        hits.append(Hit(
            node=test.node,
            anchor=test.qualname,
            summary=(
                f"{test.qualname} calls {len(calls)} distinct production methods/functions ({shown}), more "
                f"than the configured {limit} (context.{LIMIT_KEY}), so one test exercises several behaviours."
            ),
            confidence="medium" if len(calls) > MEDIUM_FACTOR * limit else "low",
        ))
    return hits


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    for key in (LIMIT_KEY, PACKAGES_KEY):
        if key not in context:
            return None, f"missing required context setting: {key}"
    limit = context[LIMIT_KEY]
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return None, f"context.{LIMIT_KEY} must be a positive integer"
    packages = context[PACKAGES_KEY]
    if (
        not isinstance(packages, list)
        or not packages
        or not all(isinstance(package, str) and _DOTTED.match(package) for package in packages)
    ):
        return None, f"context.{PACKAGES_KEY} must be a nonempty list of dotted module names"
    return {LIMIT_KEY: limit, PACKAGES_KEY: tuple(packages)}, None


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
