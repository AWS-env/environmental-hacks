"""Parse a GitHub Actions workflow with PyYAML and keep the source line span of every mapping entry.

The shared contract wants evidence that quotes exact source lines, so each mapping entry and sequence
item records (start, end) as 1-based inclusive line numbers. Spans come from the start of the next
sibling, not from PyYAML end marks (those can run into the next token). The YAML is only parsed with
`yaml.SafeLoader`; nothing in it is ever executed.
"""
from __future__ import annotations

import yaml


# Bounds for untrusted workflow files (the connector already caps a file at 1 MB). A file beyond them is reported
# as not evaluated, never silently skipped and never allowed to stall the whole scan.
MAX_LINES = 30_000
MAX_LINE_CHARS = 20_000
MAX_FLOW_DEPTH = 64       # brackets open at once on one line
MAX_INDENT = 400          # leading spaces of any line
MAX_NODES = 200_000       # nodes built; an alias is re-expanded each time, so a YAML alias bomb hits this fast
MAX_DEPTH = 100


class WorkflowParseError(ValueError):
    """The text is not valid YAML, not a YAML mapping, or exceeds the input bounds."""


class LMap(dict):
    """A YAML mapping; `spans[key]` is the (start, end) line span of that entry, `line` its first line."""
    line = 0
    spans: dict


class LSeq(list):
    """A YAML sequence; `spans[i]` is the (start, end) line span of item i."""
    line = 0
    spans: list


def _trim(lines, start, end):
    """Last line of a region (1-based, inclusive) after dropping trailing blank and comment-only lines."""
    end = max(min(end, len(lines)), start)
    while end > start and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return end


def _check_bounds(source: str, lines: list) -> None:
    if len(lines) > MAX_LINES:
        raise WorkflowParseError(f"more than {MAX_LINES} lines")
    for line in lines:
        if len(line) > MAX_LINE_CHARS:
            raise WorkflowParseError(f"a line is longer than {MAX_LINE_CHARS} characters")
        if len(line) - len(line.lstrip(" ")) > MAX_INDENT:
            raise WorkflowParseError("indentation is too deep")
        depth = peak = 0
        for ch in line:
            if ch in "[{":
                depth += 1
                peak = max(peak, depth)
            elif ch in "]}":
                depth = max(depth - 1, 0)
        if peak > MAX_FLOW_DEPTH:
            raise WorkflowParseError("flow collections are nested too deeply")


def _build(loader, node, end_exclusive, lines, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0:
        raise WorkflowParseError("too many nodes (alias expansion)")
    if depth > MAX_DEPTH:
        raise WorkflowParseError("nesting is too deep")
    if isinstance(node, yaml.MappingNode):
        loader.flatten_mapping(node)
        out = LMap()
        out.line = node.start_mark.line + 1
        out.spans = {}
        pairs = node.value
        for i, (key_node, value_node) in enumerate(pairs):
            start = key_node.start_mark.line
            nxt = pairs[i + 1][0].start_mark.line if i + 1 < len(pairs) else end_exclusive
            region_end = nxt if nxt > start else start + 1
            if i + 1 == len(pairs):
                region_end = max(region_end, end_exclusive)
            key = loader.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError:
                continue
            out[key] = _build(loader, value_node, region_end, lines, budget, depth + 1)
            out.spans[key] = (start + 1, _trim(lines, start + 1, region_end))
        return out
    if isinstance(node, yaml.SequenceNode):
        out = LSeq()
        out.line = node.start_mark.line + 1
        out.spans = []
        items = node.value
        for i, item in enumerate(items):
            start = item.start_mark.line
            nxt = items[i + 1].start_mark.line if i + 1 < len(items) else end_exclusive
            region_end = nxt if nxt > start else start + 1
            if i + 1 == len(items):
                region_end = max(region_end, end_exclusive)
            out.append(_build(loader, item, region_end, lines, budget, depth + 1))
            out.spans.append((start + 1, _trim(lines, start + 1, region_end)))
        return out
    return loader.construct_object(node, deep=True)


def load(source: str):
    """Return the root LMap of a YAML document, or raise WorkflowParseError."""
    lines = source.splitlines()
    _check_bounds(source, lines)
    loader = yaml.SafeLoader(source)
    try:
        node = loader.get_single_node()
        if node is None:
            raise WorkflowParseError("empty document")
        if not isinstance(node, yaml.MappingNode):
            raise WorkflowParseError("top level is not a mapping")
        return _build(loader, node, len(lines), lines, [MAX_NODES])
    except yaml.YAMLError as error:
        raise WorkflowParseError(type(error).__name__) from error
    except RecursionError as error:
        raise WorkflowParseError("nesting is too deep") from error
    finally:
        loader.dispose()
