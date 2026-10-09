# Detection Spec — CODE-C3.1 Inefficient iteration construct

- **Taxonomy ID:** `CODE-C3.1`
- **Issue:** #60
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1R2 (static half in this check; profiler confirmation is a follow-up)
- **Status:** Implemented (static half only — findings are unconfirmed)

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal (static half)** | Syntactic loop form: `range(len())` + subscript, manual-index `while`, single (optionally `if`-gated, no `else`) append accumulation, or dict key loop with a `d[k]` lookup |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution |
| **Telemetry needed** | None for the static half. **R2 confirmation (OQ-1 follow-up):** client-CI profile artifact showing the loop is hot, parsed by `owner-a-profile-parser` |
| **Report output field** | `finding.evidence.{symbol, loopType, suggested}` + `location` (+ enclosing function/class scope folded into the `fingerprint` join key) |
| **False-positive risk** | Low on shape (syntactic), Medium on value (engine-dependent payoff; convertible ≠ profitable) |
| **Detectable** | High (H) — loop form is syntactic |
| **Measurable** | Low (L) statically; Medium (M) after R2 hotness confirmation |

## Signals (Python only; JS/TS loop idioms are a follow-up)

- **S1 `range(len())` indexing loop** (`range-len-indexing`): `for i in range(len(xs))` with an
  `xs[i]` read in the body → direct iteration / `enumerate`. Low severity, high confidence.
- **S2 manual-index `while` loop** (`manual-index-while`): `i = 0` (immediately preceding
  statement, same block) … `while i < len(xs)` … exactly one `i += 1` (or `i = i + 1`) as a
  top-level body statement, no `continue` owned by the loop, with an `xs[i]` read → `for` /
  `enumerate`. Low severity, high confidence.
- **S3 append-accumulation loop** (`append-accumulation`, `append-accumulation-gated`):
  `out = []` (immediately preceding statement, same block; annotated `out: list = []` counts)
  plus a loop whose body is a single `out.append(E)` statement, optionally wrapped in one
  `if <cond>:` with no `else` → list comprehension (`async for` loops get an `async for`
  comprehension). Low severity, high confidence.
- **S4 dict key loop with lookup** (`dict-key-lookup`): `for k in d` / `for k in d.keys()`
  with a `d[k]` read in the body → `for k, v in d.items()`. Low severity. Confidence is
  high for `.keys()` and for a name bound in-file to a dict literal / comprehension /
  `dict()` / `defaultdict()` / `Counter()` / `OrderedDict()` or annotated as a mapping;
  **medium** (with a "not provably a dict" limitation) when the binding is unknown; a name
  bound to a list / tuple / set / `range` / `sorted(...)` is **suppressed**, because
  `for i in perm: perm[i]` on a list is legitimate indexing and `.items()` would be wrong.
  Only when `d` is not mutated in the body.

One finding per loop at most. **S3 takes precedence over S1/S4 on the same loop**: the
comprehension rewrite subsumes the header-form question (e.g. `out.append(xs[i])` inside a
`range(len())` loop is reported once, as a comprehension candidate whose template prefers
direct iteration inside).

## False-positive guards (negatives)

- The index is needed for non-access purposes — index arithmetic (`xs[i+1]`), parallel
  indexing into a second list (`ys[i]`), slices, or any other occurrence of the index
  variable outside a plain `X[i]` read → suppress (S1, S2).
- S3 only: body contains `break` / `continue` / `return` / `yield` / `await` / `try-except`,
  has any statement besides the (optionally gated) single append, or the `if` has an `else`
  → not mechanically convertible to a comprehension → suppress. S1, S2 and S4 stay valid
  with control flow present, since only the header changes.
- S3: `out` is read or re-bound anywhere inside the loop, is not a fresh `[]` bound just
  before the loop, the append takes ≠1 argument, or the receiver is a different list
  → suppress.
- The loop mutates the iterated collection (`xs[i] = …`, `d[k] = …`, `del`, `pop`,
  `append`/`update`/… on it) → C3.7 owns it → suppress.
- The loop reduces to a builtin (`total += xs[i]` → `sum`, and by extension the C10
  family) → C10 owns it → suppress.
- Already-idiomatic forms: `enumerate`, direct `for x in xs`, comprehensions, `.items()`,
  `while` with a non-index condition, counting `range(n)` with a non-`len` bound → no match.
- `while` without a same-block `i = 0` init or without an `i += 1` / `i = i + 1`
  increment → suppress (manual indexing not established).
- `while` whose increment is conditional (inside an `if`), repeated, or skippable by a
  `continue` → not a plain traversal (a `for` rewrite would change which elements are
  visited) → suppress.

## Boundaries with sibling checks

- **C3.6** no longer claims gated accumulation; C3.1 S3 owns append-accumulation →
  comprehension, gated or ungated, because the rewrite changes the construct, not the set
  of elements processed.
- **C3.5** owns flag/store loops with no `break`; accumulation loops are a C3.5 negative.
- **C10.1 / C10.3 / C10.4** (built-ins, bulk primitives) cover *which* builtin (`sum`,
  `max`, `join`, vectorize). C3.1 covers *loop form* (indexing vs iterator, append vs
  comprehension).
- **C3.2** does not flag `len()` in the S2 `while` condition; that loop is C3.1's.
- **C3.7** owns loops that mutate the iterated collection.

## Fingerprint / R2 join

`generateFingerprint("CODE-C3.1", kind, path, id)` with
`id = <enclosing def/class qualname or <module>>:<signal>:<normalized loop header>:<ordinal
among identical headers in that scope>`. Line numbers stay out of the hash, so the key
survives edits above the loop and forms the join key the future `owner-a-profile-parser`
matches against. Confirmation upgrades confidence without re-emitting.

## Suppressions

`isLineSuppressed(line, ["CODE-C3.1"])` — honouring `# noqa: CODE-C3.1` (and blanket
`# noqa`) on the loop header line, and for S3 also on the `out.append(...)` line. Path skips
belong to the scan Lambda's file walker.

## Known limitations (v1, static half)

- Non-augmented accumulation (`total = total + xs[i]`) is not recognised as a C10
  builtin reduction; only `+=` is.
- `range(0, len(xs))` and flipped/arithmetic `while` bounds (`i <= len(xs) - 1`) are not
  matched — conservative v1 scope.
- The `i = 0` init and `out = []` binding must be the immediately preceding statement
  (comments ignored); hoisted or conditional bindings are missed.
- `evidence.suggested` is a rewrite template with `<placeholders>`, not a finished patch.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809) — Python evidence across profiled pairs.
- **Taxonomy caveat:** "Engine-dependent; measure before changing." A `range(len())` →
  `enumerate` rewrite is faster on CPython but the gain varies by engine and loop body.
  Static findings never claim measured savings — that is what the R2 half exists to confirm.
