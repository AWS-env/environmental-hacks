"""TST-11: General Fixture, a test fixture that sets fields some of its tests never read.

Detector semantics version 1.0.0. Implements PyNose's General Fixture rule (Wang et al., ASE
2021, adopted from tsDetect: "not all fields instantiated within the setUp method of a test
class are utilized by all test methods in the same test class") for unittest and pytest test
classes. Python only; static only; analysed code is never executed.

Fixtures: unittest `setUp`/`asyncSetUp` (before every test) and `setUpClass` (once per class);
pytest test-class `setup_method` (every test), `setup_class` (once) and autouse fixture
methods. A fixture field is an attribute assigned on the fixture's first parameter (or the
class name) directly in its body. A test uses a field if it, or any same-file method or
property it reaches through `self`, reads it. Dynamic access counts as using every field.
"""

from __future__ import annotations

import ast
import sys
import unittest

from . import testsmells
from .static import EvaluationError, Hit, fingerprint, is_noqa  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-11"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-11", "TST11")
MIN_TESTS = 2  # a fixture shared by fewer than two tests is not a general fixture
_SHOWN_TESTS = 5

# Hooks per framework: (name, runs before every test).
HOOKS = {
    "unittest": (("setUp", True), ("asyncSetUp", True), ("setUpClass", False)),
    "pytest": (("setup_method", True), ("setup_class", False)),
}
TEARDOWNS = {
    "unittest": ("tearDown", "asyncTearDown", "tearDownClass"),
    "pytest": ("teardown_method", "teardown_class"),
}
# Attributes and methods unittest itself provides or reads (maxDiff, longMessage, assert*, ...).
TESTCASE_NAMES = frozenset(dir(unittest.TestCase)) | frozenset(dir(unittest.IsolatedAsyncioTestCase)) | frozenset(
    {"_testMethodName", "_testMethodDoc", "_outcome", "_cleanups", "_subtest", "_type_equality_funcs"}
)
_STDLIB_BASES = frozenset({
    "object", "unittest.TestCase", "unittest.case.TestCase", "unittest.IsolatedAsyncioTestCase",
    "unittest.async_case.IsolatedAsyncioTestCase",
})
# Values whose point is a side effect active for every test (a started patch, an entered context).
_HANDLE_CALLS = frozenset({
    "start", "__enter__", "__aenter__", "enter_context", "enter_async_context", "enterContext",
    "enterClassContext", "enterAsyncContext",
})
# Calls that take `self` without reading arbitrary attributes from it.
_SAFE_SELF_CALLS = frozenset({"type", "isinstance", "issubclass", "id", "super", "hash", "repr", "str"})
# Methods that change how a test case is constructed or run (ThreadableTest-style setUp swapping):
# a class defining one of these is not judged.
_MACHINERY = frozenset({
    "__init__", "__new__", "__init_subclass__", "run", "__call__", "debug", "_callSetUp", "_callTestMethod",
    "__getattr__", "__getattribute__",
})
# Calls that register cleanup: a field passed only as `self.x.close` is closed, not used.
_CLEANUP_CALLS = frozenset({"addCleanup", "addClassCleanup", "addAsyncCleanup", "callback", "push", "register"})
_DYNAMIC_ATTRS = frozenset({"__dict__", "__getattribute__", "__getattr__", "__setattr__", "__delattr__"})
_LOCAL_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_BUILD_NODES = (ast.Call, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Await)

REFERENCES = (
    "https://arxiv.org/abs/2108.04639",
    "https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/GeneralFixtureTestSmellDetector.java",
    "https://testsmells.org/pages/testsmells.html",
    "http://xunitpatterns.com/General%20Fixture.html",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Build only what every test needs in setUp; move the rest into the tests that use it, a helper they "
    "call, a lazily created property, or a separate test class (or a pytest fixture the tests request). "
    "Mark a field that must exist for every test with `# noqa: TST-11` on its assignment line, or on the "
    "fixture's def line for the whole fixture."
)
LIMITATION = (
    "Static pattern only (PyNose General Fixture rule): TST-11 proves a fixture sets a field that some of the "
    "tests running it never read, not that building the field is expensive or measurable in CI energy; the "
    "taxonomy has no General Fixture energy figure and no impact is measured. Covered fixtures: unittest "
    "setUp/asyncSetUp/setUpClass and pytest test-class setup_method/setup_class/autouse fixture methods; "
    "module-level and requested pytest fixtures are not judged. Only fields assigned directly in the fixture "
    "body are judged, and only when built by a call or comprehension; started patches and entered contexts, "
    "unittest configuration attributes, fields the fixture chain itself reads (to build other fields), and "
    "saved state that teardown passes back are not flagged. tearDown reads never count as test use, and a "
    "resource that teardown or addCleanup only closes is still flagged. Base classes, mixins and helpers are "
    "followed within the file only: a test that passes `self` out, uses getattr/vars/__dict__ or calls a self "
    "method defined elsewhere counts as using every field, findings in classes with an imported base other "
    "than unittest's TestCase are low confidence, and classes that define __init__/run/__call__/__getattr__ "
    "are not judged. Classes with fewer than 2 runnable tests and unconditionally skipped tests are not "
    "counted. Tests are recognised by default unittest/pytest discovery; files without recognised tests are "
    "evaluated with no findings."
)


def _final_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _walk_local(node):
    """Nodes of `node` that execute with it: nested functions, lambdas and classes are not entered."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if not isinstance(child, _LOCAL_SCOPES):
                stack.append(child)


def _first_param(func):
    args = func.args.posonlyargs + func.args.args
    if not args:
        return None
    for decorator in func.decorator_list:
        if _final_name(decorator) == "staticmethod":
            return None
    return args[0].arg


def _fixture_decorator(func):
    """The `@pytest.fixture(...)` / `@fixture` decorator, or None."""
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _final_name(target) == "fixture":
            return decorator
    return None


def _autouse_scope(func):
    """None unless the method is an autouse fixture; else True if it runs before every test."""
    decorator = _fixture_decorator(func)
    if not isinstance(decorator, ast.Call):
        return None
    keywords = {k.arg: k.value for k in decorator.keywords if k.arg}
    autouse = keywords.get("autouse")
    if not (isinstance(autouse, ast.Constant) and autouse.value is True):
        return None
    scope = keywords.get("scope")
    return not (isinstance(scope, ast.Constant) and scope.value in ("class", "module", "package", "session"))


class _Hierarchy:
    """Same-file classes: method resolution order, attribute lookup and runnable tests."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.by_name = {}
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef):
                self.by_name.setdefault(node.name, node)
        self._mro = {}

    def local_bases(self, cls):
        bases = []
        for base in cls.bases:
            local = self.by_name.get(_final_name(base.value if isinstance(base, ast.Subscript) else base))
            if local is not None and local is not cls:
                bases.append(local)
        return bases

    def mro(self, cls):
        """C3 linearisation over same-file bases (breadth-first if C3 fails)."""
        key = id(cls)
        if key not in self._mro:
            self._mro[key] = [cls]  # cycle guard
            self._mro[key] = self._c3(cls)
        return self._mro[key]

    def _c3(self, cls):
        bases = self.local_bases(cls)
        seqs = [list(self.mro(base)) for base in bases] + [list(bases)]
        result = [cls]
        while True:
            seqs = [seq for seq in seqs if seq]
            if not seqs:
                return result
            for seq in seqs:
                head = seq[0]
                if not any(head in other[1:] for other in seqs):
                    break
            else:
                return self._breadth_first(cls)
            result.append(head)
            for seq in seqs:
                if seq and seq[0] is head:
                    del seq[0]

    def _breadth_first(self, cls):
        order, queue, seen = [], [cls], {id(cls)}
        while queue:
            current = queue.pop(0)
            order.append(current)
            for base in self.local_bases(current):
                if id(base) not in seen:
                    seen.add(id(base))
                    queue.append(base)
        return order

    def resolved(self, cls):
        """True if every base in the hierarchy is defined in this file or is unittest's TestCase/object."""
        for current in self.mro(cls):
            for base in current.bases:
                target = base.value if isinstance(base, ast.Subscript) else base
                name = _final_name(target)
                if name in self.by_name or (self.ctx.dotted(target) or "") in _STDLIB_BASES:
                    continue
                return False
        return True

    @staticmethod
    def methods(cls):
        """Methods defined in the class body; the last definition of a name wins, as at runtime."""
        found = {}
        for stmt in cls.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[stmt.name] = stmt
        return found

    def lookup(self, mro, name, start=0):
        """(index into mro, method) of the first class from `start` defining `name`, or None."""
        for index in range(start, len(mro)):
            method = self.methods(mro[index]).get(name)
            if method is not None:
                return index, method
        return None

    def class_attributes(self, mro):
        names = set()
        for cls in mro:
            for stmt in cls.body:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else (
                    [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
                for target in targets:
                    names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
                if isinstance(stmt, ast.ClassDef):
                    names.add(stmt.name)
        return names

    def tests(self, mro):
        """Runnable test methods of the class: own and inherited, nearest definition first."""
        found = {}
        for cls in mro:
            for name, method in self.methods(cls).items():
                if name.startswith("test") and name not in found and _fixture_decorator(method) is None:
                    found[name] = method
        return sorted(found.items(), key=lambda item: item[1].lineno)


class _Reader:
    """Which fields a function reads through `self`, following same-file methods and properties.

    With `restoring=True` (teardown code) a field only counts as read when its value is used, as in
    `os.chdir(self.old_cwd)`; calling a method on it (`self.conn.close()`) is cleanup, not use.
    The same applies everywhere to arguments of addCleanup-style calls.
    """

    def __init__(self, hierarchy, mro, restoring=False):
        self.h = hierarchy
        self.ctx = hierarchy.ctx
        self.mro = mro
        self.restoring = restoring
        self.class_names = {cls.name for cls in mro}
        known = set(TESTCASE_NAMES) | hierarchy.class_attributes(mro)
        for cls in mro:
            for name, method in hierarchy.methods(cls).items():
                known.add(name)
                receiver = _first_param(method)
                for node in ast.walk(method):
                    if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                            and isinstance(node.value, ast.Name) and node.value.id == receiver):
                        known.add(node.attr)
        self.known = frozenset(known)
        self._cache = {}

    def _cleanup_only(self, node, root):
        """`self.x.close`-style access in teardown code or in an addCleanup argument."""
        parent = self.ctx.parent(node)
        if not (isinstance(parent, ast.Attribute) and parent.value is node):
            return False
        if self.restoring:
            return True
        for ancestor in self.ctx.ancestors(node):
            if isinstance(ancestor, ast.Call) and _final_name(ancestor.func) in _CLEANUP_CALLS:
                return True
            if ancestor is root:
                return False
        return False

    def owner_index(self, func):
        for index, cls in enumerate(self.mro):
            if any(stmt is func for stmt in cls.body):
                return index
        return 0

    def function(self, func):
        """(fields read, dynamic) for a whole method, helpers included (memoised; cycles cut)."""
        key = id(func)
        if key not in self._cache:
            self._cache[key] = (frozenset(), False)
            self._cache[key] = self.node(func, _first_param(func), self.owner_index(func))
        return self._cache[key]

    def _is_receiver(self, node, receiver):
        if isinstance(node, ast.Name):
            return node.id == receiver or node.id in self.class_names
        if isinstance(node, ast.Attribute) and node.attr == "__class__":
            return self._is_receiver(node.value, receiver)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "type"
                and len(node.args) == 1):
            return self._is_receiver(node.args[0], receiver)
        return False

    def node(self, root, receiver, owner):
        """(fields read, dynamic) for the statements under `root`; nested functions included (closures)."""
        reads, dynamic = set(), False
        for node in ast.walk(root):
            if isinstance(node, ast.Attribute):
                parent = self.ctx.parent(node)
                if isinstance(node.value, ast.Call) and _final_name(node.value.func) == "super":
                    found = self.h.lookup(self.mro, node.attr, owner + 1)
                    if found is not None:
                        sub_reads, sub_dynamic = self.function(found[1])
                        reads |= sub_reads
                        dynamic = dynamic or sub_dynamic
                    elif node.attr not in TESTCASE_NAMES:
                        dynamic = True
                    continue
                if not self._is_receiver(node.value, receiver) or node.attr == "__class__":
                    continue
                if node.attr in _DYNAMIC_ATTRS:
                    dynamic = True
                    continue
                if isinstance(node.ctx, ast.Store) and not isinstance(parent, ast.AugAssign):
                    continue  # overwriting a field is not reading it
                if not self._cleanup_only(node, root):
                    reads.add(node.attr)
                start = 0
                if isinstance(node.value, ast.Name) and node.value.id != receiver:
                    start = next(i for i, cls in enumerate(self.mro) if cls.name == node.value.id)
                found = self.h.lookup(self.mro, node.attr, start)
                if found is not None:
                    sub_reads, sub_dynamic = self.function(found[1])
                    reads |= sub_reads
                    dynamic = dynamic or sub_dynamic
                elif isinstance(parent, ast.Call) and parent.func is node and node.attr not in self.known:
                    dynamic = True  # a method from an imported base or mixin may read any field
            elif isinstance(node, ast.Name) and node.id == receiver and isinstance(node.ctx, ast.Load):
                parent = self.ctx.parent(node)
                if isinstance(parent, ast.Attribute) and parent.value is node:
                    continue
                if isinstance(parent, ast.Call) and _final_name(parent.func) in _SAFE_SELF_CALLS:
                    continue
                if (isinstance(parent, ast.Call) and parent.args and parent.args[0] is node
                        and isinstance(parent.func, ast.Attribute) and isinstance(parent.func.value, ast.Name)
                        and parent.func.value.id in self.class_names):
                    continue  # `Base.method(self, ...)`: followed as a method call above
                dynamic = True  # `self` escapes: getattr(self, ...), vars(self), helper(self), ...
        return frozenset(reads), dynamic


def _assignments(func, receivers):
    """(field, statement index, evidence node, value) for attributes assigned in the fixture body."""
    for index, stmt in enumerate(func.body):
        for node in _walk_local(stmt):
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(target, node.value, node) for target in node.targets]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs = [(node.target, node.value, node)]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs = [(item.optional_vars, item.context_expr, item.optional_vars)
                         for item in node.items if item.optional_vars is not None]
            for target, value, evidence in pairs:
                for leaf in ast.walk(target):
                    if (isinstance(leaf, ast.Attribute) and isinstance(leaf.value, ast.Name)
                            and leaf.value.id in receivers and isinstance(leaf.ctx, ast.Store)):
                        yield leaf.attr, index, evidence, value


def _builds(value):
    return any(isinstance(node, _BUILD_NODES) for node in _walk_local(value))


def _is_handle(value):
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and _final_name(value.func) in _HANDLE_CALLS


def _candidates(func, receivers):
    """Fields worth judging: {field: (statement index, evidence node)} in assignment order."""
    seen = {}
    for field, index, evidence, value in _assignments(func, receivers):
        seen.setdefault(field, []).append((index, evidence, value))
    out = {}
    for field, assigned in seen.items():
        if field in TESTCASE_NAMES or (field.startswith("__") and field.endswith("__")):
            continue
        values = [value for _, _, value in assigned]
        if not any(_builds(value) for value in values) or any(_is_handle(value) for value in values):
            continue
        out[field] = assigned[0][:2]
    return out


def _chain(hierarchy, mro, hook):
    """[(class index, method)] of the hook implementations that run, following super() calls."""
    out, start = [], 0
    while True:
        found = hierarchy.lookup(mro, hook, start)
        if found is None:
            return out
        index, method = found
        out.append((index, method))
        if not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == hook
            and (isinstance(node.func.value, ast.Call) and _final_name(node.func.value.func) == "super"
                 or isinstance(node.func.value, ast.Name) and node.func.value.id in hierarchy.by_name)
            for node in ast.walk(method)
        ):
            return out
        start = index + 1


def _fixtures(hierarchy, mro, framework):
    """[(defining class, method, label, per_test)] for every fixture that runs for the class."""
    entries = []
    for hook, per_test in HOOKS[framework]:
        for index, method in _chain(hierarchy, mro, hook):
            entries.append((mro[index], method, hook, per_test))
    if framework == "pytest":
        seen = set()
        for cls in mro:
            for name, method in hierarchy.methods(cls).items():
                per_test = _autouse_scope(method)
                if per_test is not None and name not in seen:
                    seen.add(name)
                    entries.append((cls, method, name, per_test))
    return entries


def _qualname(ctx, cls):
    outer = ctx.qualname(cls)
    return cls.name if outer == "<module>" else f"{outer}.{cls.name}"


def run(ctx):
    classes = testsmells._classes(ctx)  # shared unittest/pytest class recognition
    hierarchy = _Hierarchy(ctx)
    found = {}  # (id(fixture method), field) -> aggregated record
    for cls in [node for node in ast.walk(ctx.tree) if isinstance(node, ast.ClassDef)]:
        framework = classes.framework(cls)
        if framework is None:
            continue
        mro = hierarchy.mro(cls)
        if any(_MACHINERY & set(hierarchy.methods(current)) for current in mro):
            continue  # custom construction or run logic may call fixtures and helpers dynamically
        cls_name = _qualname(ctx, cls)
        tests = [
            (name, method) for name, method in hierarchy.tests(mro)
            if not testsmells.is_skipped(ctx, testsmells.TestFunction(method, f"{cls_name}.{name}", framework, cls))
        ]
        if len(tests) < MIN_TESTS:
            continue
        reader = _Reader(hierarchy, mro)
        usage = [(f"{cls_name}.{name}", reader.function(method)) for name, method in tests]
        entries = _fixtures(hierarchy, mro, framework)
        whole = {id(method): reader.function(method) for _, method, _, _ in entries}
        restorer = _Reader(hierarchy, mro, restoring=True)
        for hook in TEARDOWNS[framework]:
            for _, method in _chain(hierarchy, mro, hook):
                whole[id(method)] = restorer.function(method)
        resolved = hierarchy.resolved(cls)
        for owner, method, label, per_test in entries:
            if is_noqa(NOQA, ctx.lines[method.lineno - 1]):
                continue
            receivers = {_first_param(method)} | reader.class_names
            fields = _candidates(method, receivers)
            if not fields:
                continue
            owner_index = mro.index(owner)
            steps = [reader.node(stmt, _first_param(method), owner_index) for stmt in method.body]
            others = [whole[key] for key in whole if key != id(method)]
            for field, (index, evidence) in fields.items():
                if any(field in step_reads for step_reads, _ in steps):
                    continue  # the fixture itself reads it
                if any(step_dynamic for _, step_dynamic in steps[index:]):
                    continue  # the fixture hands `self` out after assigning it
                if any(field in other_reads or other_dynamic for other_reads, other_dynamic in others):
                    continue  # another fixture reads it, or teardown restores it (`os.chdir(self.old_cwd)`)
                record = found.setdefault((id(method), field), {
                    "owner": _qualname(ctx, owner), "label": label, "field": field, "evidence": evidence,
                    "per_test": per_test, "receiver": _first_param(method) or "self", "resolved": True,
                    "total": 0, "unused": [], "classes": set(),
                })
                record["resolved"] = record["resolved"] and resolved
                record["classes"].add(cls_name)
                for test_name, (reads, dynamic) in usage:
                    record["total"] += 1
                    if not dynamic and field not in reads:
                        record["unused"].append(test_name)
    hits = []
    for record in found.values():
        unused, total = record["unused"], record["total"]
        if not unused:
            continue
        medium = record["per_test"] and record["resolved"] and 2 * len(unused) >= total
        shown = ", ".join(unused[:_SHOWN_TESTS]) + (f" and {len(unused) - _SHOWN_TESTS} more"
                                                    if len(unused) > _SHOWN_TESTS else "")
        cost = ("each of them still pays for building it" if record["per_test"]
                else "it is built once per class for tests that do not need it")
        where = "" if record["classes"] == {record["owner"]} else (
            f" in {', '.join(sorted(record['classes']))}")
        if len(unused) == total:
            usage = f"none of the {total} tests that run it{where} reads it, directly or through a same-file helper"
        else:
            usage = (f"{len(unused)} of the {total} tests that run it{where} never read it, directly or through a "
                     f"same-file helper")
        hits.append(Hit(
            node=record["evidence"],
            anchor=f"{record['owner']}.{record['label']}:{record['field']}",
            summary=(f"{record['owner']}.{record['label']} builds {record['receiver']}.{record['field']}, but "
                     f"{usage}: {shown}; {cost}."),
            confidence="medium" if medium else "low",
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
