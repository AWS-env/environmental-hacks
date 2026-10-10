# Detection Spec - CODE-C10.4 Inefficient string concatenation

- **Taxonomy ID:** `CODE-C10.4`
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented (v0.1.0)

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | `NAME += EXPR` or `NAME = NAME + EXPR` in the body of a `for` / `while` loop, where the target is known to be a `str` |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution, client code is only parsed |
| **Telemetry needed** | None (R1) |
| **Report output field** | `finding.evidence.{symbol, mutator, loopType, suggested}` + `location` (the accumulating statement) |
| **False-positive risk** | Medium - CPython may make local `+=` cheap; the accumulator may be read inside the loop |
| **Measurable** | L statically (trip count and piece sizes are unknown) |

## Rule (one finding per accumulating statement)

Kind `string-concat-in-loop`, severity `low`. The statement must be directly in the body of a
loop (not crossing a nested `def` / `lambda` / `class`).

**Target is a bare name** - flagged only when it is bound to a string in the same function
before the loop: the last assignment before the loop is a string literal (including `''`),
f-string, `str(...)`, `"...".join(...)` / `"...".format(...)`, `"..." % x`, or there is no such
assignment and it is a parameter annotated `str` (an annotation `: str` on the assignment also
counts). A local with unknown binding is NOT flagged. Confidence `low`.

**Target is an attribute / subscript** (`self.buf`, `d[k]`) - flagged when an assignment of a
string literal to the same chain is visible in the file (and no non-string assignment to it), OR
when EXPR itself is clearly a string (string literal, f-string, `str(...)`, `.format(...)`, `%`
format). Confidence `medium` (no in-place resize optimisation for these).

**Global names** (declared `global`, or a loop at module level) are treated like attribute
targets: binding from file-wide assignments, confidence `medium`.

A read of the accumulator anywhere else in the same loop body (any load that is not the
accumulating statement itself: `len(s)`, passing it to a call, comparing it, using it in an
f-string) suppresses the finding: the value is consumed every iteration, so `"".join` after the
loop is not a drop-in and the `+=` adds no extra asymptotic cost. Append-only loops are still flagged.

## False-positive guards (negatives)

- Numeric accumulation (binding is an int/float literal, or EXPR is a number).
- list / tuple / bytes concatenation (`acc += [x]`, `b""`, `()`).
- Loops that run at most once: the statement is followed in its block by `break` (owned by that
  loop) / `return` / `raise`, or the loop body has a top-level break / return / raise. The next
  enclosing loop is then considered instead.
- `for` over a literal collection of constant items, or `range(<int literals>)`, of 8 or fewer
  items. 9 items is flagged.
- The accumulator is re-assigned (reset) elsewhere in the loop body, or is the loop variable.
- An attribute/subscript accumulator whose base or index is the loop variable or is re-bound in
  the loop body (`node.text += s`, `lines[i] += s`, `out[k] += s`): a different string each iteration.
- The accumulator is read elsewhere in the same loop body (see above).
- Building with a list and `"".join(parts)` (no `+=` on a string).
- `# noqa` or `# noqa: CODE-C10.4` on the loop header or the statement line.

## Identity and fingerprint

`identity = string-concat-in-loop:<qualname>:<name>:<ordinal>` where `qualname` is
`getEnclosingQualname` and the ordinal counts accumulations of the same name in the same
qualname. Line numbers stay out of identity and fingerprint.

## Known limitations (v1)

- CPython may resize a local `str` in place when it holds the only reference, making local
  `+=` close to linear. This is UNVERIFIED from an official page; hence low confidence for locals.
- Trip count is not measured; the saving is bounded (it grows with the number and size of
  pieces). Findings never quantify savings.
- Binding is inferred from assignments visible in the file only; names bound by `for`, `with`,
  imports, or unpacking are unknown and not flagged.
- Python only; comprehension / generator accumulation, `str.__add__`, and `reduce(operator.add)`
  are not covered. Prepending (`s = x + s`) is not covered.
- Boundary: the assignment need not be on every path; the last textual assignment before the
  loop wins.

## Evidence & Citations

- **SRC-01:** *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809).
- **taxonomy-c10.4:** Software Compute Waste Taxonomy - C10.4 Inefficient string concatenation.
- Precedent: AWS Amazon Q detector `python/string-concatenation`
  (http://docs.aws.amazon.com/amazonq/detector-library/python/string-concatenation/) and
  Sourcery `use-join` (https://docs.sourcery.ai/References/Sourcery-Rules/Python/Default-Rules/use-join/).
  No Ruff / Pylint rule covers this.
