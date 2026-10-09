"""Parse context for plain-text configuration files (line oriented; nothing is executed)."""
from __future__ import annotations

from owner_c.common import Hit


class Line:
    """A position in a config file; plays the role of an AST node for the runner."""

    def __init__(self, lineno: int):
        self.lineno = lineno


class ConfigCtx:
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()

    def line_of(self, node) -> int:
        return node.lineno

    def end_line_of(self, node) -> int:
        return node.lineno

    def evidence_lines(self, node):
        return node.lineno, self.lines[node.lineno - 1]

    def is_suppressed(self, codes: tuple, line: int) -> bool:
        return False

    def hit(self, lineno: int, anchor: str, summary: str, confidence: str) -> Hit:
        return Hit(Line(lineno), anchor, summary, confidence)
