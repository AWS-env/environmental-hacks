"""Contract v1 runner for Owner D static checks over non-Python text files.

Sibling of `static.py` (which parses Python with `ast`). Here each check brings its own
parser for a configuration format (Dockerfile, Kubernetes/compose YAML, ECS JSON, ...).
Content is only read as text: it is never executed, built, pulled or resolved over the
network. Coverage semantics match `static.py`, so the two can later be merged:

- each scope item is one `file:<path>` with exactly one `static` source;
- a file that is missing, of an unsupported type or fails to parse stays out of
  `evaluated_scope` with a limitation, so it is never reported clean;
- repeated anchors in a file become `anchor#2`, ... and fingerprints use
  `static.fingerprint` (no line numbers);
- evidence is the exact, complete source line(s) of the hit.

A check module provides CHECK_ID, DETECTOR_VERSION, NOQA, REFERENCES, RECOMMENDATION,
LIMITATION, FORMATS (human description for the "unsupported" reason),
`parse(locator, content) -> ctx` (ctx exposes `.lines`; raise `Unsupported` for a file type
the check does not handle, `ParseError` for content it cannot parse and `NotEvaluated` for
content outside the check's claim) and
`run(ctx) -> list[TextHit]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .static import (  # noqa: F401  (EvaluationError is re-exported for the CLI)
    IDENTITY_FIELDS,
    SCHEMA_VERSION,
    SUPPORTED_KIND,
    EvaluationError,
    _require,
    _unique_identities,
    fingerprint,
)

EVIDENCE_MAX_LINES = 8


class Unsupported(ValueError):
    """The file type is not handled by this check."""


class ParseError(ValueError):
    """The file looks like a supported type but its content cannot be parsed."""


class NotEvaluated(ValueError):
    """The file parses but is outside what the check can judge (e.g. a development image)."""


@dataclass(frozen=True)
class TextHit:
    """A pattern match in a text file. `anchor` is the semantic identity (no line numbers).

    `line`..`end_line` (1-based, inclusive) are quoted verbatim as evidence. `block_line` is
    the first line of the enclosing instruction/block; suppression comments directly above
    it (or on `line`) apply. `codes` are extra rule codes such comments may name (e.g. the
    matching hadolint rule). `recommendation` overrides the check-wide recommendation.
    """

    line: int
    anchor: str
    summary: str
    confidence: str  # low | medium | high
    end_line: int | None = None
    block_line: int | None = None
    codes: tuple = ()
    recommendation: str | None = None


# `# noqa`, `# noqa: INF-09`, `# hadolint ignore=DL3009,DL3015`; file-wide `# hadolint global ignore=...`
_SUPPRESS = re.compile(r"#\s*(noqa|hadolint\s+ignore)\b\s*(?:[:=]\s*([A-Za-z0-9_, -]+))?", re.I)
_GLOBAL = re.compile(r"^\s*#\s*hadolint\s+global\s+ignore\s*=\s*([A-Za-z0-9_, -]+)", re.I)


def _suppresses(text, codes):
    for match in _SUPPRESS.finditer(text):
        named = match.group(2)
        if named is None:
            if match.group(1).lower() == "noqa":
                return True
            continue
        listed = {code.strip().upper() for code in re.split(r"[,\s]+", named) if code.strip()}
        if listed & {code.upper() for code in codes}:
            return True
    return False


def _global_codes(lines):
    codes = set()
    for line in lines:
        match = _GLOBAL.match(line)
        if match:
            codes |= {code.strip().upper() for code in re.split(r"[,\s]+", match.group(1)) if code.strip()}
    return codes


def is_suppressed(lines, hit, codes):
    """True if the hit line, the comment lines directly above its block or a file-wide
    `# hadolint global ignore=` comment suppress it."""
    all_codes = tuple(codes) + tuple(hit.codes)
    if _global_codes(lines) & {code.upper() for code in all_codes}:
        return True
    if 1 <= hit.line <= len(lines) and _suppresses(lines[hit.line - 1], all_codes):
        return True
    index = (hit.block_line or hit.line) - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        if _suppresses(lines[index], all_codes):
            return True
        index -= 1
    return False


def _evaluate_file(scope_id, sources, check):
    """Return (finding items, omitted_reason)."""
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
    try:
        ctx = check.parse(locator, content)
    except Unsupported:
        return None, f"{scope_id}: unsupported file type; {check.CHECK_ID} v{check.DETECTOR_VERSION} supports {check.FORMATS} only"
    except ParseError as error:
        return None, f"{scope_id}: could not be parsed ({error}); not evaluated"
    except NotEvaluated as error:
        return None, f"{scope_id}: not evaluated ({error})"
    except Exception as error:  # a parser bug must not become a clean claim for this file
        return None, f"{scope_id}: could not be parsed ({type(error).__name__}); not evaluated"

    items = []
    try:
        for hit in check.run(ctx):
            if is_suppressed(ctx.lines, hit, check.NOQA):
                continue
            end = min(hit.end_line or hit.line, hit.line + EVIDENCE_MAX_LINES - 1)
            items.append({
                "anchor": hit.anchor,
                "line": hit.line,
                "summary": hit.summary,
                "confidence": hit.confidence,
                "recommendation": hit.recommendation or check.RECOMMENDATION,
                "evidence": [{
                    "source_id": source["source_id"],
                    "kind": SUPPORTED_KIND,
                    "locator": locator,
                    "line_start": hit.line,
                    "value": "\n".join(ctx.lines[hit.line - 1:end]),
                }],
            })
    except Exception as error:  # a detector bug must not become a clean claim for this file
        return None, f"{scope_id}: check failed ({type(error).__name__}); not evaluated"
    items.sort(key=lambda item: item["line"])
    return _unique_identities(items), None


def evaluate_text(payload, check):
    """Evaluate a contract v1 input payload with one text-format static check module."""
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
                "recommendation": item["recommendation"],
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
