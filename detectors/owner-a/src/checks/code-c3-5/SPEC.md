# Detection Spec — CODE-C3.5 Missing loop early exit

- **Taxonomy ID:** `CODE-C3.5`
- **Issue:** #64
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | Loop whose whole body is one `if` (no `elif`/`else`) that only assigns names initialised before the loop, with no `break`/`return`/`raise`, no read of those names inside the loop, and no volatile call on the match path |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, loopType}` + `location` (the loop) |
| **False-positive risk** | Medium — first-vs-last-match semantics (S2/S3); lazily produced iterables with side effects |
| **Detectable** | H (syntactic flag/store + missing-exit pattern) |
| **Measurable** | L statically (savings depend on match position, invisible without a profile) |

## Signals (one finding per loop, mutually exclusive)

| Signal | Shape | Severity | Confidence | Rewrite in `agentPrompt` |
|---|---|---|---|---|
| **S1 sticky flag / constant** | every assignment in the arm stores a literal (`found = True`, `status = "degraded"`) | medium | high | `break`, or `found = any(<condition> for x in xs)` |
| **S2 match store** | the arm stores a non-literal (`result = x`) | medium | medium | `break` (first match), or `next((… if …), default)` |
| **S3 store then return** | S2, and the statement right after the loop is `return <target>` | medium | medium | `return <value>` inside the loop |

S1 is High confidence because a sticky literal is position-independent: stopping at the
first match leaves the result unchanged. S2/S3 change *which* match is kept (the loop keeps
the last one today), so they carry a first-vs-last limitation.

## False-positive guards (negatives)

- `break` / `return` / `raise` on the match path, or a `for … else` → already exits.
- The body has anything besides the single `if`: per-element calls (`audit(x)`),
  accumulation (`out.append`, `n += 1`), or other statements → an early exit would skip them.
- The arm contains anything besides plain name assignments (a call, augmented assignment, …).
- `else` / `elif` on the `if` → a reset or dispatch, not a one-way decision.
- The flag/result is read inside the loop: in the match test (`if not found and …`), in a
  stored value (`best = merge(best, x)`), or in a `while` condition (`while not found:` is
  already an exit).
- A target not initialised before the loop in the enclosing scope (parameter or earlier
  assignment) → suppress (cannot tell what "decided" means).
- Volatile calls on the match path (`time()`, `random()`, `next()`, `input()`, …).
- Already idiomatic: `any(<generator>)`, `next((…), None)`.
- `# noqa: CODE-C3.5` (or blanket `# noqa`) on the loop header or the flag/store line.

## Boundaries with sibling checks

- **C3.6 (#65):** an eager producer with a subset consumer — including `any([...])` /
  `all([...])` over a list comprehension and loops that *do* exit early but iterate a fully
  built list. C3.5 owns hand-written loops that have no exit at all.
- **C3.4 (#63):** an inner loop with a flag and no `break` is a C3.5 finding on the inner
  loop; C3.4 may separately propose an index for the nest.
- **C4.7 (#73):** skipping the whole call on trivial input. **C4.1 (#67):** operand order
  inside one expression. C3.5 is control flow across iterations.
- **C3.1 (#60):** accumulation loops are a C3.5 negative.

## Fingerprint

`generateFingerprint("CODE-C3.5", kind, path, id)` with
`id = <enclosing def/class qualname>:<loop header>:<ordinal among identical headers>:<flag/store target(s)>`.
Line numbers stay out of the hash.

## Known limitations (v1)

- The body must be exactly the match arm, so `while` loops (which also carry their own
  increment) and loops with extra pure statements are not matched — conservative by design.
- Purity of the match test is not verified beyond the volatile-call list.
- Python only; JS/TS `.some()` / `.find()` equivalents are a follow-up.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809) — C3.S5 "Failing to exit early after determining the required result".
- **Taxonomy caveat:** "Matters when match is common/early" — findings never quantify savings.
