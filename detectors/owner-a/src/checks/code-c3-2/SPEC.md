# Detection Spec — CODE-C3.2 Recomputing loop-invariant

- **Taxonomy ID:** `CODE-C3.2`
- **Issue:** #61
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | Value-position call / attribute / arithmetic expression inside a `for`/`while` body whose operands are all defined outside the loop and never assigned, mutated, or passed to a call in the body; excludes volatile calls, callee lookups (C10.5), and C3.3 setup callees |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, expr, loopType}` + `location` |
| **False-positive risk** | Medium — callee purity unknown statically; reassignment-through-alias invisible to syntax-only analysis |
| **Detectable** | High (H) for syntactic loop + invariance pattern |
| **Measurable** | Low (L) statically (gain = cost × trip-count; trip-count needs a profile) |

## Scope & Signals

- **S1 Invariant call in loop:**
  - Value-position function or method call whose operands are defined outside the loop and never reassigned or mutated in the loop body.
  - Severity: `medium`, Confidence: `medium` (purity unverified statically).
  - Limitations: `["hoist only if side-effect free — verify callee purity"]`.
- **S2 Invariant attribute / subscript chain:**
  - Attribute read or subscript access chain in value position whose base object and indexing keys are defined outside the loop.
  - Severity: `low`, Confidence: `medium`.
  - Limitations: `["property getters and __getitem__ may perform computation or have side effects"]`.
- **S3 Invariant arithmetic over outer names:**
  - Binary arithmetic computation over variables defined outside the loop.
  - Severity: `low`, Confidence: `high` (pure).
  - Limitations: `[]`.

## Boundaries with Sibling Checks

- **C3.3 (#62, per-iteration setup):** C3.3 owns heavy construction in loops (e.g. `re.compile`, `open`, connection objects, CapWords class constructors). C3.2 skips any callee matching C3.3 tiers to avoid double-reporting.
- **C10.5 (#46, scope lookup):** C10.5 owns hoisting method/name lookups (e.g. `append = out.append`, `sqrt = math.sqrt`). C3.2 S2 only flags value reads, never callee-position lookups.
- **C1.4 (#37, repeated computation):** C1.4 detects repeated computation within the same scope; C3.2 detects invariant recomputation across loop iterations.
- **C3.1 (#60, iteration construct):** C3.1 owns manual while loops like `while i < len(xs)`. Trivial O(1) built-ins (`len()`, `isinstance()`, etc.) are excluded from C3.2.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809).
- **Finding:** Hoisting loop-invariant calls and calculations out of loops reduces iteration overhead and CPU cycles proportionally to the loop trip count.
