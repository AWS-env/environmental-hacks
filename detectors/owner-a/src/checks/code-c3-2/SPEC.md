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
  - Not flagged: statement-level calls whose result is discarded (`print(header)`, `notify(cfg)` — they run for their side effects) and awaited calls.
  - Severity: `medium`, Confidence: `medium` (purity unverified statically).
  - Limitations: `["hoist only if side-effect free — verify callee purity"]`.
- **S2 Invariant attribute / subscript chain:**
  - Attribute read or subscript access chain in value position, **two or more hops deep** (`settings.limits["max"]`, `cfg.rates["eu"]`), whose base object and indexing keys are defined outside the loop. Single lookups (`rate.value`, `cfg["k"]`) are C10.5's lookup-overhead territory and too cheap for the "value is costly" caveat.
  - Severity: `low`, Confidence: `medium`.
  - Limitations: `["property getters and __getitem__ may perform computation or have side effects"]`.
- **S3 Invariant arithmetic over outer names:**
  - Binary arithmetic computation over variables defined outside the loop.
  - Severity: `low`, Confidence: `high` (pure).
  - Limitations: `[]`.

One finding per (loop, expression): repeated occurrences of the same invariant in one
loop are reported once.

## Fingerprint & Suppressions

- `generateFingerprint("CODE-C3.2", kind, path, id)` with
  `id = <enclosing def/class qualname>:<normalized loop header>:<ordinal among identical
  headers in that scope>:<normalized expr>`. Line numbers stay out of the hash.
- `# noqa: CODE-C3.2` (or blanket `# noqa`) on the loop header or the expression's line.
  The check passes its own code; the shared default list in `core/suppressions.ts` stays
  `["F401", "CODE-C1.1"]` so a C3.2 noqa never hides another check's finding.

## Boundaries with Sibling Checks

- **C3.3 (#62, per-iteration setup):** C3.3 owns heavy construction in loops (e.g. `re.compile`, `open`, connection objects, CapWords class constructors). C3.2 skips any callee matching C3.3 tiers to avoid double-reporting.
- **C10.5 (#46, scope lookup):** C10.5 owns hoisting method/name lookups (e.g. `append = out.append`, `sqrt = math.sqrt`). C3.2 S2 only flags value reads, never callee-position lookups.
- **C1.4 (#37, repeated computation):** C1.4 detects repeated computation within the same scope; C3.2 detects invariant recomputation across loop iterations.
- **C3.1 (#60, iteration construct):** C3.1 owns manual while loops like `while i < len(xs)`. Trivial O(1) built-ins (`len()`, `isinstance()`, etc.) are excluded from C3.2.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809).
- **Finding:** Hoisting loop-invariant calls and calculations out of loops reduces iteration overhead and CPU cycles proportionally to the loop trip count.
