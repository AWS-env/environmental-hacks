"""Shared building blocks for the owner C Python detectors: parse context, hits, helpers."""
from __future__ import annotations

import ast
import re
import warnings
from dataclasses import dataclass

MAX_FILE_BYTES = 1_000_000
EVIDENCE_MAX_LINES = 8

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
SCOPE_NODES = FUNC_NODES + (ast.ClassDef,)
LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)
COMP_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


@dataclass(frozen=True)
class Hit:
    """A static pattern match. `anchor` is the semantic identity (no line numbers)."""
    node: ast.AST
    anchor: str
    summary: str
    confidence: str  # low | medium | high


@dataclass(frozen=True)
class Candidate:
    """A pattern that is only reported when a runtime artifact confirms it."""
    node: ast.AST
    anchor: str
    detail: str
    line: int
    end_line: int
    meta: object = None  # check-specific data carried to confirm() (JS: enclosing function start line)


@dataclass(frozen=True)
class Confirmation:
    """Runtime evidence for a candidate: one field of the normalized artifact data."""
    field: str
    value: object
    summary: str
    confidence: str  # low | medium | high
    extra: tuple = ()  # more (field, value) pairs cited as evidence


def is_test_path(path: str) -> bool:
    """Test code is excluded by the connector: test-suite waste belongs to the TST checks."""
    parts = path.replace("\\", "/").lower().split("/")
    name = parts[-1]
    return (
        any(p in {"test", "tests", "testing", "__tests__", "__mocks__", "e2e", "cypress"} for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or any(marker in name for marker in (".test.", ".spec.", ".stories."))
    )


_NOQA = re.compile(r"#\s*noqa(?::\s*([A-Za-z0-9, ]+))?", re.I)


def is_noqa(codes: tuple, line: str) -> bool:
    """True if the line has a bare `# noqa` or one naming any of this check's lint codes."""
    m = _NOQA.search(line)
    if not m:
        return False
    named = m.group(1)
    return named is None or any(c in named for c in codes)


def same_file(artifact_path: str, repo_path: str) -> bool:
    """Match a path recorded on the client's machine to a repo-relative path."""
    a, r = artifact_path.replace("\\", "/"), repo_path.replace("\\", "/")
    return a == r or a.endswith("/" + r)


def scope_walk(scope):
    """Yield nodes in `scope` without descending into nested functions or classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(node))


def is_str_like(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return is_str_like(node.left) or (isinstance(node.op, ast.Add) and is_str_like(node.right))
    return False


class Ctx:
    """One parsed file: tree, parent links, import aliases and structural helpers."""

    def __init__(self, path: str, source: str):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # user code may contain invalid escapes etc.
            self.tree = ast.parse(source, filename=path)
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self._parent = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self._parent[id(child)] = node
        self.aliases = self._import_aliases()

    def _import_aliases(self) -> dict:
        """Map local names to dotted import paths, e.g. {'sleep': 'time.sleep'}."""
        aliases = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        aliases[a.asname] = a.name
                    else:
                        root = a.name.split(".")[0]
                        aliases[root] = root
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    if a.name != "*":
                        aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        return aliases

    def parent(self, node):
        return self._parent.get(id(node))

    def ancestors(self, node):
        node = self.parent(node)
        while node is not None:
            yield node
            node = self.parent(node)

    def dotted(self, node):
        """Resolve `a.b.c` / imported names to a dotted path, or None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(self.aliases.get(node.id, node.id))
        return ".".join(reversed(parts))

    def enclosing_function(self, node):
        for a in self.ancestors(node):
            if isinstance(a, FUNC_NODES):
                return a
        return None

    def enclosing_scope(self, node):
        for a in self.ancestors(node):
            if isinstance(a, SCOPE_NODES):
                return a
        return self.tree

    def qualname(self, node) -> str:
        """Stable dotted name of the enclosing function/class chain, or '<module>'."""
        names = []
        for a in self.ancestors(node):
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(a.name)
            elif isinstance(a, ast.Lambda):
                names.append("<lambda>")
        return ".".join(reversed(names)) or "<module>"

    def in_loop(self, node) -> bool:
        for a in self.ancestors(node):
            if isinstance(a, LOOP_NODES + COMP_NODES):
                return True
            if isinstance(a, SCOPE_NODES):
                return False
        return False

    def in_async(self, node) -> bool:
        for a in self.ancestors(node):
            if isinstance(a, ast.AsyncFunctionDef):
                return True
            if isinstance(a, (ast.FunctionDef, ast.Lambda, ast.ClassDef)):
                return False
        return False

    def line_of(self, node) -> int:
        return node.lineno

    def is_suppressed(self, codes: tuple, line: int) -> bool:
        return 0 < line <= len(self.lines) and is_noqa(codes, self.lines[line - 1])

    def hit(self, node, anchor, summary, confidence) -> Hit:
        return Hit(node, anchor, summary, confidence)

    def candidate(self, node, anchor, detail) -> Candidate:
        return Candidate(node, anchor, detail, node.lineno, getattr(node, "end_lineno", node.lineno))

    def evidence_lines(self, node):
        """(line_start, exact complete source line(s)) for a node, capped to a few lines."""
        start = node.lineno
        end = min(getattr(node, "end_lineno", start), start + EVIDENCE_MAX_LINES - 1)
        return start, "\n".join(self.lines[start - 1:end])
