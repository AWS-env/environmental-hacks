"""Shared runner for Owner D static source checks under contract v1.

Source is parsed with `ast`; it is never imported or executed. Each scope item is one
`file:<path>` with exactly one static source. A file that is missing, not Python or fails
to parse stays out of `evaluated_scope` with a limitation, so it is never reported clean.

A check module provides CHECK_ID, DETECTOR_VERSION, NOQA, REFERENCES, RECOMMENDATION,
LIMITATION and `run(ctx) -> list[Hit]` (the same shape as owner C's static checks).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import warnings
from collections import Counter
from dataclasses import dataclass

SCHEMA_VERSION = "1.0"
SUPPORTED_KIND = "static"
EVIDENCE_MAX_LINES = 8

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
SCOPE_NODES = FUNC_NODES + (ast.ClassDef,)
LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)
COMP_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

IDENTITY_FIELDS = (
    "schema_version",
    "repository_id",
    "scan_id",
    "commit_sha",
    "check_id",
    "detector_version",
    "context",
    "scope",
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as a contract input for this check."""


@dataclass(frozen=True)
class Hit:
    """A static pattern match. `anchor` is the semantic identity (no line numbers)."""

    node: ast.AST
    anchor: str
    summary: str
    confidence: str  # low | medium | high


def _canonical(value):
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


_NOQA = re.compile(r"#\s*noqa(?::\s*([A-Za-z0-9, -]+))?", re.I)


def is_noqa(codes, line):
    """True if the line has a bare `# noqa` or one naming any of this check's codes."""
    match = _NOQA.search(line)
    if not match:
        return False
    named = match.group(1)
    return named is None or any(code in named for code in codes)


class Ctx:
    """One parsed file: tree, parent links, import aliases and structural helpers."""

    def __init__(self, path, source):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # user code may contain invalid escapes etc.
            self.tree = ast.parse(source, filename=path)
        self.path = path
        self.lines = source.splitlines()
        self._parent = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self._parent[id(child)] = node
        self.aliases = self._import_aliases()

    def _import_aliases(self):
        """Map local names to dotted import paths, e.g. {'log': 'logging'}."""
        aliases = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".")[0]
                        aliases[root] = root
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
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

    def qualname(self, node):
        """Stable dotted name of the enclosing function/class chain, or '<module>'."""
        names = []
        for ancestor in self.ancestors(node):
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(ancestor.name)
            elif isinstance(ancestor, ast.Lambda):
                names.append("<lambda>")
        return ".".join(reversed(names)) or "<module>"

    def enclosing_loop(self, node):
        """The nearest loop/comprehension in the same function scope, or None."""
        for ancestor in self.ancestors(node):
            if isinstance(ancestor, LOOP_NODES + COMP_NODES):
                return ancestor
            if isinstance(ancestor, SCOPE_NODES):
                return None
        return None

    def evidence_lines(self, node):
        """(line_start, exact complete source line(s)) for a node, capped to a few lines."""
        start = node.lineno
        end = min(getattr(node, "end_lineno", start), start + EVIDENCE_MAX_LINES - 1)
        return start, "\n".join(self.lines[start - 1:end])


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _unique_identities(items):
    """Disambiguate repeated anchors within a file: the 2nd occurrence becomes `anchor#2`."""
    seen = Counter()
    for item in items:
        seen[item["anchor"]] += 1
        count = seen[item["anchor"]]
        item["identity"] = item["anchor"] if count == 1 else f"{item['anchor']}#{count}"
    return items


def _evaluate_file(scope_id, sources, check):
    """Return (findings-items, omitted_reason). Items carry anchor, line, summary, evidence."""
    statics = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not statics:
        return None, f"{scope_id}: no static source supplied for this scope item"
    if len(statics) > 1:
        return None, f"{scope_id}: multiple static sources supplied; evaluation requires exactly one"
    source = statics[0]
    locator = source.get("locator")
    content = source.get("content")
    if not isinstance(locator, str) or not isinstance(content, str):
        return None, f"{scope_id}: static source needs a string locator and content"
    if not locator.endswith(".py"):
        return None, f"{scope_id}: unsupported language; {check.CHECK_ID} v{check.DETECTOR_VERSION} supports Python (.py) only"
    try:
        ctx = Ctx(locator, content)
    except (SyntaxError, ValueError) as error:
        return None, f"{scope_id}: could not be parsed ({type(error).__name__}); not evaluated"

    items = []
    try:
        for hit in check.run(ctx):
            line = hit.node.lineno
            if line <= len(ctx.lines) and is_noqa(check.NOQA, ctx.lines[line - 1]):
                continue
            start, text = ctx.evidence_lines(hit.node)
            items.append({
                "anchor": hit.anchor,
                "line": line,
                "summary": hit.summary,
                "confidence": hit.confidence,
                "evidence": [{
                    "source_id": source["source_id"],
                    "kind": SUPPORTED_KIND,
                    "locator": locator,
                    "line_start": start,
                    "value": text,
                }],
            })
    except Exception as error:  # a detector bug must not become a clean claim for this file
        return None, f"{scope_id}: check failed ({type(error).__name__}); not evaluated"
    items.sort(key=lambda item: item["line"])
    return _unique_identities(items), None


def evaluate_static(payload, check):
    """Evaluate a contract v1 input payload with one static check module."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == check.CHECK_ID, f"detector only evaluates {check.CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == check.DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; "
        f"this detector implements {check.DETECTOR_VERSION}",
    )
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope = payload["scope"]
    sources = payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        scope_sources = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        items, omitted = _evaluate_file(scope_id, scope_sources, check)
        if items is None:
            limitations.append(omitted)
            continue
        evaluated.append(scope_id)
        for item in items:
            findings.append({
                "fingerprint": fingerprint(payload["repository_id"], check.CHECK_ID, scope_id, item["identity"]),
                "scope_id": scope_id,
                "identity": item["identity"],
                "summary": item["summary"],
                "confidence": item["confidence"],
                "recommendation": check.RECOMMENDATION,
                "references": list(check.REFERENCES),
                "evidence": item["evidence"],
            })
    limitations.append(check.LIMITATION)

    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"

    result = {field: payload[field] for field in IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result
