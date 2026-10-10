"""TST-08: Sensitive Equality, an equality assertion on an object's str()/repr() text.

Detector semantics version 1.0.0. Implements tsDetect's Sensitive Equality rule ("test methods
verify objects by invoking the default toString() method of the object and comparing the output
against an specific string", testsmells.org; van Deursen et al., XP 2001) for unittest and pytest,
with Python's str()/repr() in place of toString(). Python only; static only; analysed code is
never executed.
"""

from __future__ import annotations

import ast
import re
import sys

from . import testsmells
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "TST-08"
DETECTOR_VERSION = "1.0.0"
NOQA = ("TST-08", "TST08")

REFERENCES = (
    "https://testsmells.org/pages/testsmells.html",
    "https://github.com/TestSmells/TestSmellDetector/blob/master/src/main/java/testsmell/smell/SensitiveEquality.java",
    "https://arxiv.org/abs/2108.04639",
    "https://arxiv.org/abs/2310.14548",
)
RECOMMENDATION = (
    "Compare the objects themselves (define __eq__ and assertEqual(actual, expected)) or the specific "
    "attributes the test is about, instead of their str()/repr() text. If the test really checks the "
    "text representation, name it after it (e.g. test_repr) or mark the line with `# noqa: TST-08`."
)
LIMITATION = (
    "Static pattern only (tsDetect Sensitive Equality rule, toString() mapped to str()/repr()): TST-08 "
    "finds equality assertions that compare the str()/repr() text of a value with literal expected text, or "
    "two such texts with each other; it does not measure CI time. The taxonomy's energy association (SRC-15, "
    "Kendall tau 0.177) comes from JUnit/Maven projects and is not shown to transfer to Python; the waste "
    "mechanism is indirect (tests that break and are re-run or rewritten when unrelated formatting changes) "
    "and no impact is measured. Static analysis cannot see the operand's type or whether the text is the "
    "behaviour under test, so these are not reported: tests whose name, module or directory is about the "
    "text (test_str, test_repr.py, tests/str/, ...format..., ...render..., ...message...), exception and "
    "warning messages (str(cm.exception), str(excinfo.value), names bound by `except ... as`), str() of "
    "literals and numeric builtins, and str() compared with a non-literal value (usually an expected value "
    "converted for a text-typed actual). "
    "Findings are medium confidence for repr()/__repr__() text or two compared representations and low for "
    "str() against literal text, where the string form is often the intended API (URLs, paths, rich text). "
    "Only ==/!= and assertEqual/assertNotEqual-style methods are read; substring and regex checks (in, "
    "assertIn, assertRegex) tolerate formatting changes and are not reported. Tests are recognised by default "
    "unittest/pytest discovery; files without recognised tests are evaluated with no findings."
)

# unittest equality methods (positional `first`, `second`).
EQUALITY_METHODS = frozenset({
    "assertEqual", "assertEquals", "failUnlessEqual", "assertMultiLineEqual",
    "assertNotEqual", "assertNotEquals", "failIfEqual",
})
_SHOWN_CHARS = 60
_REPR_BUILTINS = frozenset({"str", "repr"})
_REPR_DUNDERS = frozenset({"__str__", "__repr__"})
# str() of these is a number or text already: a conversion, not an object's representation.
_PLAIN_BUILTINS = frozenset({"len", "int", "float", "round", "sum", "abs", "min", "max", "hash", "id", "bool",
                             "ord", "chr", "hex", "oct", "bin"})
# A test about the text itself: a word of its qualified name says so (test_repr, TestStr, test_format_...).
_TEXT_TEST_WORD = re.compile(
    r"^(?:str|strs|string\w*|tostring|text|unicode|repr|reprs|representation\w*|display|pprint|print\w*"
    r"|render\w*|format\w*|seriali[sz]\w*|dump\w*|pretty|message\w*|descri\w*)$"
)
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
# Context managers that capture an exception or warning: `with pytest.raises(E) as excinfo`.
_CAPTURE_CALL = re.compile(r"raises|warns|catch_warnings|deprecated_call", re.I)
# Exception/warning-like names when the binding is not visible: `str(err)`, `str(self.last_error)`,
# `str(recwarn[0].message)` (pytest's recwarn fixture).
_EXCEPTION_NAME = re.compile(r"^(?:e|ex|exc|err|error|exception|excinfo|exc_info|warning|message|recwarn)$"
                             r"|^exc_\w+$|_(?:error|exception|exc|err)$"
                             r"|(?:Error|Exception|Warning)$")


def is_text_test(test, path=""):
    """True if the test's qualified name, its module (`test_repr.py`) or that module's directory
    (`tests/str/`) says it checks the text representation."""
    parts = path.replace("\\", "/").rsplit("/", 2)[-2:]
    names = [test.qualname] + [part.rsplit(".", 1)[0] for part in parts]
    return any(_TEXT_TEST_WORD.match(word.lower()) for name in names for word in _WORD.findall(name))


def captured_names(func):
    """Names bound to exceptions or warnings in the test: `except E as err`, `with ... raises(E) as cm`."""
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            call = node.context_expr
            target = call.func if isinstance(call, ast.Call) else call
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if _CAPTURE_CALL.search(name):
                names.add(node.optional_vars.id)
    return names


def _root(node):
    """Root name and final identifier of `a.b[0].c` -> ("a", "c")."""
    final = None
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        if isinstance(node, ast.Attribute) and final is None:
            final = node.attr
        node = node.func if isinstance(node, ast.Call) else node.value
    root = node.id if isinstance(node, ast.Name) else None
    return root, final or root


def representation(ctx, node, captured):
    """(label, subject) if `node` is the str()/repr() text of a value under test, else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and ctx.dotted(func) in _REPR_BUILTINS:
        if len(node.args) != 1 or node.keywords or isinstance(node.args[0], ast.Starred):
            return None  # str(b, "utf-8") decodes; str() is empty
        label, subject = f"{func.id}()", node.args[0]
    elif isinstance(func, ast.Attribute) and func.attr in _REPR_DUNDERS and not node.args and not node.keywords:
        label, subject = f"{func.attr}()", func.value
    else:
        return None
    if isinstance(subject, (ast.Constant, ast.JoinedStr)):
        return None
    if isinstance(subject, ast.Call) and isinstance(subject.func, ast.Name) and subject.func.id in _PLAIN_BUILTINS:
        return None
    root, final = _root(subject)
    if root in captured or any(name and _EXCEPTION_NAME.search(name) for name in (root, final)):
        return None  # exception/warning messages are the interface under test
    return label, subject


def _equality_operands(assertion):
    """(left, right) of an equality assertion, or None."""
    name = assertion.name.split(".")[-1]
    if assertion.kind == "assert":
        test = assertion.operands[0]
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.NotEq)):
            return test.left, test.comparators[0]
        return None
    if assertion.kind == "unittest" and name in EQUALITY_METHODS and len(assertion.operands) >= 2:
        return assertion.operands[0], assertion.operands[1]
    return None


def _is_text(node, names=None):
    """True for literal expected text: a string literal, f-string, `"..." + x` / `"..." % x`, or a
    local name bound once in the test to one of those."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _is_text(node.left)
    return bool(names) and isinstance(node, ast.Name) and node.id in names


def text_names(func):
    """Local names bound exactly once in the test, to literal text: `expected = "<Cart: 2 items>"`."""
    stores, text = {}, set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and _is_text(node.value)):
            text.add(node.targets[0].id)
    return {name for name in text if stores.get(name) == 1}


def _finding(found, other_is_text):
    """(detail, confidence) for one equality assertion, or None if it is not Sensitive Equality."""
    reps = [f for f in found if f is not None]
    if len(reps) == 2:
        labels = " and ".join(dict.fromkeys(label for label, _ in reps))
        return (f"compares two values through their {labels} text instead of comparing the values, so it depends "
                f"on formatting rather than equality"), "medium"
    if not reps or not other_is_text:
        return None  # `actual == str(expected)` converts an expected value for a text-typed actual
    label, subject = reps[0]
    shown = ast.unparse(subject)
    if len(shown) > _SHOWN_CHARS:
        shown = shown[:_SHOWN_CHARS - 3] + "..."
    confidence = "medium" if label in ("repr()", "__repr__()") else "low"
    return (f"compares the {label} text of `{shown}` with literal expected text, so a change to its string "
            f"formatting fails the test even when the value is still correct"), confidence


def run(ctx):
    hits = []
    for test in testsmells.test_functions(ctx):
        if is_text_test(test, ctx.path):
            continue
        captured = names = None
        for assertion in testsmells.assertions(ctx, test.node):
            operands = _equality_operands(assertion)
            if operands is None:
                continue
            if captured is None:
                captured, names = captured_names(test.node), text_names(test.node)
            found = [representation(ctx, operand, captured) for operand in operands]
            other = operands[1] if found[0] is not None else operands[0]
            result = _finding(found, _is_text(other, names))
            if result is None:
                continue
            detail, confidence = result
            hits.append(Hit(
                node=assertion.node,
                anchor=f"{test.qualname}:{assertion.name}",
                summary=f"{assertion.name} in {test.qualname} {detail}.",
                confidence=confidence,
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return testsmells.evaluate_tests(payload, sys.modules[__name__])
