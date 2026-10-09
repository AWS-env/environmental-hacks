"""A small, dependency-free YAML reader for configuration manifests, with line numbers.

PyYAML is not a project dependency, so this reads the subset of YAML that Kubernetes,
Docker Compose and CloudFormation files use in practice: block mappings and sequences,
plain/quoted scalars (incl. multi-line), block scalars (`|`, `>`), single-line and
multi-line flow collections, comments, multiple documents, anchors/aliases, merge keys
(`<<`) and tags (`!Ref`, `!!str`; the tag is dropped and the value kept).

Every node records the 1-based line where it starts, so findings can quote exact source
lines. Anything outside the subset (tabs used for indentation, complex `?` keys,
directives other than `%YAML`/`%TAG`, unbalanced flow collections, unknown aliases, Go/Helm
template actions outside quoted strings) raises `YamlError`; callers must treat the file as not evaluated, never clean.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field


class YamlError(ValueError):
    pass


@dataclass
class Scalar:
    value: str | None
    line: int


@dataclass
class Sequence:
    items: list
    line: int


@dataclass
class Mapping:
    items: dict
    line: int
    key_lines: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.items.get(key, default)


_KEY = re.compile(r"""^(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s#'"\[\]{},&*!|>%@`-][^#]*?|-[^\s][^#]*?)\s*:(?:\s+|$)""")
_ANCHOR = re.compile(r"^&([^\s,\[\]{}]+)\s*")
_TAG = re.compile(r"^!(?:<[^>]*>|[^\s,\[\]{}]*)\s*")
_ALIAS = re.compile(r"^\*([^\s,\[\]{}]+)\s*$")
# Go/Helm template actions outside quoted strings/block scalars; GitHub `${{ }}` is plain text.
_TEMPLATE = re.compile(r"(?<!\$)\{\{-?\s*[-.$\w(/\"]")
_QUOTED = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'")
_DOC_START = re.compile(r"^---(\s|$)")
_DOC_END = re.compile(r"^\.\.\.(\s|$)")


def strip_comment(text):
    """Drop a trailing `# comment` that is outside quotes."""
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'" and (index == 0 or text[index - 1] in " \t[{,:-?"):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
        index += 1
    return text.rstrip()


_ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f",
                   "v": "\v", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}


def _unescape(match):
    code = match.group(1)
    if len(code) > 1:
        return chr(int(code[1:], 16))
    return _SIMPLE_ESCAPES.get(code, code)


def _unquote(text):
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _ESCAPE.sub(_unescape, text[1:-1])
    return text


def _scalar_value(text):
    text = text.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    return _unquote(text)


class _Parser:
    def __init__(self, text):
        self.raw = text.lstrip("\ufeff").splitlines()
        self.virtual = {}
        self.anchors = {}

    # -- line views -----------------------------------------------------------------------

    def view(self, index):
        """(indent, content) for a line; content is None for blank/comment-only lines."""
        if index in self.virtual:
            return self.virtual[index]
        line = self.raw[index]
        stripped = line.lstrip(" ")
        if stripped.startswith("\t") and stripped.strip():
            raise YamlError(f"tab indentation on line {index + 1}")
        content = strip_comment(stripped)
        if _TEMPLATE.search(_QUOTED.sub("", content)):
            raise YamlError(f"Go/Helm template syntax on line {index + 1}")
        return len(line) - len(stripped), (content if content.strip() else None)

    def next_content(self, index, end):
        while index < end and self.view(index)[1] is None:
            index += 1
        return index

    # -- documents ------------------------------------------------------------------------

    def documents(self):
        docs, start = [], 0
        bounds = []
        for index, line in enumerate(self.raw):
            if line.startswith("%"):
                if not line.startswith(("%YAML", "%TAG")):
                    raise YamlError(f"unsupported directive on line {index + 1}")
                self.virtual[index] = (0, None)
            elif _DOC_START.match(line) or _DOC_END.match(line):
                bounds.append((start, index))
                rest = strip_comment(line[3:]).strip()
                if rest and _DOC_START.match(line):
                    if _TAG.match(rest) and not _TAG.sub("", rest, count=1).strip():
                        rest = ""
                    else:
                        raise YamlError(f"content after '---' on line {index + 1}")
                start = index + 1
        bounds.append((start, len(self.raw)))
        for begin, end in bounds:
            first = self.next_content(begin, end)
            if first >= end:
                continue
            self.anchors = {}
            node, after = self.block(first, end, self.view(first)[0])
            after = self.next_content(after, end)
            if after < end:
                raise YamlError(f"unexpected content on line {after + 1}")
            docs.append(node)
        return docs

    # -- block structure ------------------------------------------------------------------

    def block(self, index, end, indent):
        """Parse the block node that starts at line `index` with the given indent."""
        _, content = self.view(index)
        if content == "-" or content.startswith("- "):
            return self.sequence(index, end, indent)
        if content.startswith("? "):
            raise YamlError(f"complex mapping key on line {index + 1}")
        if _KEY.match(content):
            return self.mapping(index, end, indent)
        return self.value(content, index, end, indent - 1)

    def mapping(self, index, end, indent):
        node = Mapping({}, index + 1)
        merges = []
        while True:
            index = self.next_content(index, end)
            if index >= end:
                break
            ind, content = self.view(index)
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"unexpected indentation on line {index + 1}")
            if content == "-" or content.startswith("- "):
                break
            match = _KEY.match(content)
            if not match:
                raise YamlError(f"expected a mapping key on line {index + 1}")
            key = _unquote(match.group("key").strip())
            key_line = index + 1
            rest = content[match.end():].strip()
            value, index = self.value(rest, index, end, indent, allow_same_indent_seq=True)
            if key == "<<":
                merges.append(value)
                continue
            node.items[key] = value
            node.key_lines[key] = key_line
        for merged in merges:
            for source in merged.items if isinstance(merged, Sequence) else [merged]:
                if not isinstance(source, Mapping):
                    raise YamlError("merge key needs a mapping")
                for key, value in source.items.items():
                    if key not in node.items:
                        node.items[key] = value
                        node.key_lines[key] = source.key_lines.get(key, source.line)
        return node, index

    def sequence(self, index, end, indent):
        node = Sequence([], index + 1)
        while True:
            index = self.next_content(index, end)
            if index >= end:
                break
            ind, content = self.view(index)
            if ind < indent or not (content == "-" or content.startswith("- ")):
                if ind > indent:
                    raise YamlError(f"unexpected indentation on line {index + 1}")
                break
            if ind > indent:
                raise YamlError(f"unexpected indentation on line {index + 1}")
            rest = content[1:]
            offset = len(rest) - len(rest.lstrip(" ")) + 1
            rest = rest.strip()
            if rest and (rest == "-" or rest.startswith("- ") or _KEY.match(rest)) and not rest.startswith(("[", "{")):
                self.virtual[index] = (indent + offset, rest)
                item, index = self.block(index, end, indent + offset)
            else:
                item, index = self.value(rest, index, end, indent)
            node.items.append(item)
        return node, index

    def value(self, rest, index, end, indent, allow_same_indent_seq=False):
        """Parse the value text `rest` found on line `index` (owned by a node at `indent`)."""
        line = index + 1
        anchor = None
        while True:
            match = _ANCHOR.match(rest)
            if match:
                anchor, rest = match.group(1), rest[match.end():]
                continue
            match = _TAG.match(rest)
            if match and rest.startswith("!"):
                rest = rest[match.end():]
                continue
            break
        alias = _ALIAS.match(rest)
        if alias:
            if alias.group(1) not in self.anchors:
                raise YamlError(f"unknown alias on line {line}")
            return self.anchors[alias.group(1)], index + 1
        if rest.startswith(("|", ">")):
            node, index = self.block_scalar(rest, index, end, indent)
        elif rest.startswith(("[", "{")):
            node, index = self.flow_text(rest, index, end, line)
        elif rest.startswith(("\"", "'")) and not self._closed(rest):
            node, index = self.multiline_quoted(rest, index, end, line)
        elif rest:
            node, index = self.plain(rest, index, end, indent, line)
        else:
            nxt = self.next_content(index + 1, end)
            if nxt < end:
                ind, content = self.view(nxt)
                is_seq = content == "-" or content.startswith("- ")
                if ind > indent or (allow_same_indent_seq and ind == indent and is_seq):
                    node, index = self.block(nxt, end, ind)
                    if anchor:
                        self.anchors[anchor] = node
                    return node, index
            node, index = Scalar(None, line), index + 1
        if anchor:
            self.anchors[anchor] = node
        return node, index

    @staticmethod
    def _closed(text):
        quote = text[0]
        body = text[1:]
        if quote == "'":
            body = body.replace("''", "")
            return "'" in body
        return re.search(r'(?<!\\)(?:\\\\)*"', body) is not None

    def plain(self, rest, index, end, indent, line):
        parts = [rest]
        index += 1
        while True:
            nxt = self.next_content(index, end)
            if nxt >= end:
                break
            ind, content = self.view(nxt)
            if ind <= indent or _KEY.match(content) or content.startswith("- "):
                break
            parts.append(content.strip())
            index = nxt + 1
        return Scalar(_scalar_value(" ".join(parts)), line), index

    def multiline_quoted(self, rest, index, end, line):
        parts = [rest]
        index += 1
        while index < end:
            parts.append(self.raw[index].strip())
            index += 1
            if self._closed(" ".join(parts)):
                return Scalar(_unquote(" ".join(parts).strip()), line), index
        raise YamlError(f"unterminated quoted scalar on line {line}")

    def block_scalar(self, header, index, end, indent):
        line = index + 1
        lines = []
        index += 1
        block_indent = None
        while index < end:
            raw = self.raw[index]
            if raw.strip() == "":
                lines.append("")
                index += 1
                continue
            ind = len(raw) - len(raw.lstrip(" "))
            if block_indent is None:
                if ind <= indent:
                    break
                block_indent = ind
            if ind < block_indent:
                break
            lines.append(raw[block_indent:])
            index += 1
        while lines and lines[-1] == "":
            lines.pop()
        joiner = "\n" if header.startswith("|") else " "
        return Scalar(joiner.join(lines), line), index

    # -- flow collections -----------------------------------------------------------------

    def flow_text(self, rest, index, end, line):
        parts = [rest]
        state = _balance(rest, (0, None))
        index += 1
        while not (state[0] <= 0 and state[1] is None):
            if index >= end:
                raise YamlError(f"unbalanced flow collection on line {line}")
            part = strip_comment(self.raw[index].strip())
            parts.append(part)
            state = _balance("\n" + part, state)
            index += 1
        text = "\n".join(parts)
        node, pos = _Flow(text, line, self.anchors).parse(0)
        if text[pos:].strip():
            raise YamlError(f"unexpected text after flow collection on line {line}")
        return node, index


def _balance(text, state):
    """Carry (bracket depth, open quote) across lines so long flow documents stay linear."""
    depth, quote = state
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return depth, quote


class _Flow:
    def __init__(self, text, line, anchors):
        self.text = text
        self.line = line
        self.anchors = anchors
        self.newlines = [i for i, char in enumerate(text) if char == "\n"]

    def _line(self, pos):
        return self.line + bisect.bisect_left(self.newlines, pos)

    def _skip(self, pos):
        while pos < len(self.text) and self.text[pos] in " \t\n\r":
            pos += 1
        return pos

    def parse(self, pos):
        pos = self._skip(pos)
        if pos >= len(self.text):
            raise YamlError(f"unexpected end of flow collection on line {self.line}")
        char = self.text[pos]
        if char == "[":
            return self._seq(pos)
        if char == "{":
            return self._map(pos)
        return self._scalar(pos)

    def _seq(self, pos):
        node = Sequence([], self._line(pos))
        pos = self._skip(pos + 1)
        while True:
            if pos >= len(self.text):
                raise YamlError(f"unbalanced flow sequence on line {node.line}")
            if self.text[pos] == "]":
                return node, pos + 1
            item, pos = self.parse(pos)
            pos = self._skip(pos)
            if pos < len(self.text) and self.text[pos] == ":":  # single-pair mapping inside a sequence
                value, pos = self.parse(pos + 1)
                item = Mapping({item.value: value}, item.line, {item.value: item.line})
                pos = self._skip(pos)
            node.items.append(item)
            if pos < len(self.text) and self.text[pos] == ",":
                pos = self._skip(pos + 1)

    def _map(self, pos):
        node = Mapping({}, self._line(pos))
        pos = self._skip(pos + 1)
        while True:
            if pos >= len(self.text):
                raise YamlError(f"unbalanced flow mapping on line {node.line}")
            if self.text[pos] == "}":
                return node, pos + 1
            key, pos = self._scalar(pos, key=True)
            pos = self._skip(pos)
            value = Scalar(None, key.line)
            if pos < len(self.text) and self.text[pos] == ":":
                value, pos = self.parse(pos + 1)
                pos = self._skip(pos)
            if key.value == "<<" and isinstance(value, Mapping):
                for k, v in value.items.items():
                    node.items.setdefault(k, v)
            else:
                node.items[key.value] = value
                node.key_lines[key.value] = key.line
            if pos < len(self.text) and self.text[pos] == ",":
                pos = self._skip(pos + 1)

    def _scalar(self, pos, key=False):
        line = self._line(pos)
        char = self.text[pos]
        if char in "\"'":
            close = pos + 1
            while close < len(self.text):
                if self.text[close] == "\\" and char == '"':
                    close += 2
                    continue
                if self.text[close] == char:
                    if char == "'" and self.text[close + 1:close + 2] == "'":
                        close += 2
                        continue
                    break
                close += 1
            if close >= len(self.text):
                raise YamlError(f"unterminated quoted scalar on line {line}")
            return Scalar(_unquote(self.text[pos:close + 1]), line), close + 1
        stop = ",]}" if not key else ",]}:"
        end = pos
        while end < len(self.text) and self.text[end] not in stop:
            if self.text[end] == ":" and not key and end + 1 < len(self.text) and self.text[end + 1] in " \n":
                break
            end += 1
        token = self.text[pos:end].strip()
        token = _TAG.sub("", token, count=1) if token.startswith("!") else token
        alias = _ALIAS.match(token)
        if alias:
            if alias.group(1) not in self.anchors:
                raise YamlError(f"unknown alias on line {line}")
            return self.anchors[alias.group(1)], end
        return Scalar(_scalar_value(token), line), end


def load_all(text):
    """Parse every document in `text`; raise YamlError for anything outside the subset."""
    return _Parser(text).documents()


def to_python(node):
    """Plain dict/list/str view of a node tree (for tests and simple lookups)."""
    if isinstance(node, Mapping):
        return {key: to_python(value) for key, value in node.items.items()}
    if isinstance(node, Sequence):
        return [to_python(item) for item in node.items]
    return node.value
