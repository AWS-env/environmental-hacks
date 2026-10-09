# Detection Spec — CODE-C3.6 Unfiltered bulk iteration

- **Taxonomy ID:** `CODE-C3.6`
- **Issue:** #65
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | Eager producer (list comprehension; `list`/`tuple` of a generator or of `map`/`filter`/`zip`/`enumerate`/`reversed`; `[*map(…)]`; `readlines()` / `read().splitlines()` / `read().split(sep)`) whose only consumer uses a subset: prefix index/slice or `next(iter(…))`, a `for` loop that exits early, or `any`/`all`/`in` |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.kind` (consumer type) + `finding.evidence.{snippet, symbol}` + `location` (the producer) |
| **False-positive risk** | Low–Medium — the shape is syntactic; residual risks are skipped side effects in the element expression and one-hop reads the resolver misses |
| **Detectable** | H (producer and consumer are both syntactic; one-hop dataflow is file-local) |
| **Measurable** | L statically (saved work = unconsumed elements × per-element cost; both runtime-dependent) |

## Reading of the source

SRC-01 (Table I) defines C3.S6 as "Processing entire collections when only a subset is
needed" (360 / 3,000 samples, 12%) and attributes it to "a bias toward eager execution
rather than energy-efficient lazy evaluation or generator pipelines". The paper gives no
code example, so the signal below — an eager producer feeding a subset consumer — is our
reading of that framing, not a before/after pair from the paper.

## Signals (one finding per producer)

| Kind | Consumer | Rewrite in `agentPrompt` | Confidence |
|---|---|---|---|
| `eager-then-prefix` (S1) | `P[0]`, `P[:k]` / `P[0:k]`, `next(iter(P))` | `next(<gen>)`, `list(itertools.islice(<gen>, k))`, `f.readline()` | high |
| `eager-then-early-exit` (S2) | `for … in P:` with a `break` owned by that loop, a `return`, or a `raise` outside a `try` body | iterate the generator / the file object | medium |
| `eager-then-short-circuit` (S3) | `any(P)`, `all(P)`, `v in P`, `v not in P` | pass the generator instead of the list | high |

**One hop:** `name = P` inside a function, where `name` occurs exactly twice in that function
(the binding and one later read), is followed to that read. The read must not sit inside a
loop or comprehension that does not also contain the binding (it would run repeatedly and
exhaust a generator). Module-level names are never followed (other modules may import them).

**Severity:** Medium when the producer reads a file or does per-element calls (element
expression, `if` clause, or a mapped callee). Low for a trivial projection
(`[x.id for x in xs][0]`, `tuple(zip(a, b))[0]`), where only allocation is saved. Never High:
the saved share (k / n, match position) is not visible statically.

## False-positive guards (negatives)

- Consumer reads every element (`sum`, `len`, a loop without an exit, …) → C6.3, not C3.6.
- A bound name read more than once, reassigned, returned, or read in a repeating context.
- Non-prefix index/slice: `[-1]`, `[-k:]`, `[::2]`, `[i]`, `[2:5]`, `[:]`.
- `break` that belongs to a nested loop; `raise` inside a `try` body.
- S2 loop body mutates a source of the producer (copy-to-mutate idiom, C3.7).
- Element expression / `if` clause / mapped callee is volatile or mutating (I/O, `random`,
  `next`, `.append`, `.pop`, `print`, logging methods).
- Source is a small literal: a tuple/list/set of ≤ 8 elements, or `range` spanning ≤ 8.
- Already lazy: generator expressions, bare `map`/`filter`, `islice`, `next(gen)`, iterating the
  file object, and `read().split()` without a separator (whitespace split, not a line read).
- The in-loop filter (`for x in xs: if p(x): …`) is already filter-first and never matches.
- `# noqa: CODE-C3.6` (or blanket `# noqa`) on the producer or the consumer line.

## Boundaries with sibling checks

`test/fixtures/code-c3-6/boundaries.py` holds one example per row; C3.6 emits nothing there.

| Shape | Owner |
|---|---|
| eager producer consumed in full | C6.3 (#81) |
| work done before a cheaper guard in the loop body | C7.4 (#89) |
| hand-written loop with no exit | C3.5 (#64) |
| append-accumulation loop → comprehension | C3.1 (#60) |
| `sorted(xs)[0]` / `sorted(xs)[:k]` | C10.2 (#43) |
| `v in stored_list` | C5.1 (#75) |
| `items[:]` / `list(d)` copy so the loop can mutate the original | C3.7 (#66) |
| fetch everything from a DB/API, then use a few rows | C9.2 (#96) / Owner B |

## Fingerprint

`generateFingerprint("CODE-C3.6", kind, path, id)` with
`id = <enclosing def/class qualname>:<kind>:<whitespace-normalised producer text>[:<ordinal>]`.
Line numbers stay out of the hash.

## Known limitations (v1)

- Purity of per-element calls is not verified beyond the volatile/mutating list; findings with
  calls carry a skipped-side-effects limitation.
- `return` inside a nested loop counts as an exit (it leaves the outer loop too).
- Python only; JS/TS `.map(f).slice(0, k)` / `.map(f)[0]` / `.map(f).some(p)` and pandas
  producers are follow-ups.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809) — C3.S6 "Processing entire collections when only a subset is needed".
- **Taxonomy caveat:** "Filter-first helps if the filter is selective" — findings never quantify savings.
