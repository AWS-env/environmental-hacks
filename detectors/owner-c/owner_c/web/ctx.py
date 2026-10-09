"""Parse contexts for HTML, CSS and package.json (tree-sitter / json). Source is parsed only, never rendered or executed."""
from __future__ import annotations

import json
import re

import tree_sitter as ts
import tree_sitter_css as tscss
import tree_sitter_html as tshtml

from owner_c.common import EVIDENCE_MAX_LINES, Hit
from owner_c.config.ctx import ConfigCtx
from owner_c.langs import ParseError

_LANGUAGES = {}


def _language(kind: str):
    if kind not in _LANGUAGES:
        _LANGUAGES[kind] = ts.Language({"html": tshtml.language, "css": tscss.language}[kind]())
    return _LANGUAGES[kind]


class _TreeCtx:
    kind = ""

    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self._bytes = getattr(self, "_parse_source", source).encode("utf-8")
        tree = ts.Parser(_language(self.kind)).parse(self._bytes)
        if tree.root_node.has_error:
            raise ParseError("syntax errors in file")
        self.tree = tree
        self.root = tree.root_node

    def text(self, node) -> str:
        return self._bytes[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def walk(self, node=None):
        stack = [node or self.root]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.children))

    def line_of(self, node) -> int:
        return node.start_point[0] + 1

    def end_line_of(self, node) -> int:
        return node.end_point[0] + 1

    def evidence_lines(self, node):
        start = self.line_of(node)
        end = min(self.end_line_of(node), start + EVIDENCE_MAX_LINES - 1)
        return start, "\n".join(self.lines[start - 1:end])

    def is_suppressed(self, codes: tuple, line: int) -> bool:
        return False  # no suppression syntax is defined for FE checks yet

    def hit(self, node, anchor, summary, confidence) -> Hit:
        return Hit(node, anchor, summary, confidence)


class HtmlCtx(_TreeCtx):
    kind = "html"

    def attributes(self, tag) -> list:
        """[(lower-cased name, value text or None)] for a start_tag / self_closing_tag node."""
        out = []
        for attr in tag.children:
            if attr.type != "attribute":
                continue
            name = next((c for c in attr.children if c.type == "attribute_name"), None)
            if name is None:
                continue
            value = None
            for c in attr.children:
                if c.type == "attribute_value":
                    value = self.text(c)
                elif c.type == "quoted_attribute_value":
                    inner = next((v for v in c.children if v.type == "attribute_value"), None)
                    value = self.text(inner) if inner is not None else ""
            out.append((self.text(name).lower(), value))
        return out


_AT_PRELUDE = re.compile(r"@(?:media|container|custom-media)([^{;]*)")
_RANGE_CONDITION = re.compile(r"\([^()\n]*[<>][^()\n]*\)")


def _neutralize_range_queries(source: str) -> str:
    """Rewrite range media-query conditions, `(width < 576px)`, to a neutral `(color)`.

    tree-sitter-css cannot parse the range syntax (found on bootstrap.css: 206 errors, whole file unavailable). The
    condition never matters to FE checks, and the rewrite is confined to one line of the at-rule prelude, so line
    numbers and every declaration are unchanged. Evidence still quotes the original source lines."""
    return _AT_PRELUDE.sub(lambda m: m.group(0).replace(m.group(1), _RANGE_CONDITION.sub("(color)", m.group(1))), source)


class CssCtx(_TreeCtx):
    kind = "css"

    def __init__(self, path: str, source: str):
        self._parse_source = _neutralize_range_queries(source)
        super().__init__(path, source)


class PackageCtx(ConfigCtx):
    """package.json: validated with the JSON parser, then read line by line for evidence."""
    kind = "package"

    def __init__(self, path: str, source: str):
        super().__init__(path, source)
        try:
            self.data = json.loads(source)
        except ValueError as error:
            raise ParseError("invalid JSON") from error
        if not isinstance(self.data, dict):
            raise ParseError("package.json is not an object")

    def dependency_line(self, section: str, name: str) -> int:
        """1-based line of `"name":` inside the named top-level section (first match from the section start)."""
        in_section = False
        for number, line in enumerate(self.lines, 1):
            if re.match(rf'\s*"{re.escape(section)}"\s*:', line):
                in_section = True
            elif in_section and re.match(rf'\s*"{re.escape(name)}"\s*:', line):
                return number
        return 1
