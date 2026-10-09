# Detection Spec — CODE-C1.6 Unnecessary initialization

- **Taxonomy ID:** `CODE-C1.6`
- **Issue:** #39
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | A function-local `x = <allocation or call>` whose value is unused on some paths: every `if`/`elif`/`else` arm assigns `x` again (or leaves) before reading it, or a guard right after it returns / `continue`s / `break`s without using it |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution. Tier A setup callees come from the shared `src/core/setup-cost.json`, resolved through the file's imports |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, expr, factory, loopType}` + `location` (from the setup to the end of the `if` chain or the first guard) |
| **False-positive risk** | Low for allocations. Medium when the setup runs code: dropping it (S1) loses its side effects, and moving it (S2) changes when they happen. Mutating calls, volatile calls and order-sensitive moves are guarded (see below) |
| **Detectable** | High (H) for straight-line setup followed by a full overwrite or a guard |
| **Measurable** | Low (L) statically (gain = setup cost × how often the discarding path runs; both need a profile) |

## Scope & Signals

The rule: flag setup only when it **costs something** and its value is **discarded on a path that is
visible in the same block**. The taxonomy note ("matters when setup is costly and early exits are
common") sets both limits.

**Costly setup** (the right-hand side of a plain or annotated `x = …` in a function):

| Cost | Examples | Severity |
|---|---|---|
| Tier A setup (`setup-cost.json`) | `open(p)`, `requests.Session()`, `re.compile(…)` | `high` |
| Call or `await` | `parse(path)`, `await fetch()`, `r.split(",")` | `medium` |
| Allocation | `[]`, `{…}`, `set()`, comprehensions | `low` |

Free values are never flagged: constants (`x = None`, `0`, `""`, `()`), names, attribute/subscript
lookups and O(1) built-ins (`len`, `isinstance`, `int`). Pre-declaring `x = None` costs one constant
store, so it is style, not compute waste. Confidence is `high` for allocations and `medium` when the
setup runs code.

- **S1 Overwritten on every arm (`overwritten-init`):** `rows = []` followed (before any other mention of `rows`) by an `if` chain **with an `else`** whose every arm either assigns `rows = …` (not reading `rows`) before reading it, or leaves via `return` / `raise` / `continue` / `break` without reading it, with at least one assigning arm. The conditions must not read `rows`. Fix: delete the setup; for a call, keep it as a bare statement if its side effects are needed.
- **S2 Setup before an early exit (`init-before-early-exit`):** the setup is followed by one or more guards `if c: …; return|continue|break` (no `elif`/`else`, no walrus) that do not mention `x`, and `x` is read later in the block. Fix: move the setup below the guards. Several guards are one finding, anchored at the first.
  - `raise` guards are not counted: they are error handling that rarely fires, so the setup is rarely wasted.
  - A setup that runs code stays put when a guard reads one of its inputs (`do.decompress(d)` then `if not do.eof`), unless every call is a known pure built-in or string method (`r.split(",")` then `if r.startswith("#")`).
  - An intervening statement that mentions the setup's inputs (it may rebind them) stops the move, and so does one that makes a call when the setup is impure (the two side effects would swap order).
  - Setups that call a state-changing method (`cache.pop(k)`, `kwargs.pop("x")`, `map(kwds.pop, …)`) are not moved: the call is the point.
- **Both signals skip:** volatile calls (`time.time()`, `random()`, `next()`, `readline()`), `yield`, walrus and `lambda` values; the throwaway name `_`; names declared `global`/`nonlocal` or captured by a closure; functions using `locals()`, `vars()`, `eval`, `exec` or frame introspection; and blocks inside `try` / `with` (a handler or `__exit__` may still see the setup). Module and class bodies are not analysed.
- **`continue` / `break` exits:** the old value survives into the next iteration or the code after the loop, so those exits count only when every mention of `x` in the function sits in the setup's own block, after the setup.
- A guard run followed by a full S1 overwrite is reported once, as S1. The enclosing `for`/`while` loop, if any, is reported in `evidence.loopType` and in `why`.

**Not flagged (v1):** partial overwrites (`x = default(); if c: x = other()`): without an `else` the default is used on the fall-through path. `match` statements and `try`/`except` overwrites are not analysed.

## Fingerprint & Suppressions

- `generateFingerprint("CODE-C1.6", kind, path, id)` with
  `id = <enclosing def/class qualname>:<name>:<normalized setup statement>:<ordinal>`, where the
  ordinal counts identical keys in the file. Line numbers stay out of the hash; the contract
  identity is `<kind>:<id>`.
- `# noqa: CODE-C1.6` (or blanket `# noqa`) on the setup line or on the `if` / first guard line.

## Boundaries with Sibling Checks

- **C1.2 (#35, redundant assignment):** C1.2 owns straight-line overwrites in one block (dead on every path), including `x = f(); if c: return; x = g()`, so S2 skips a guard run whose next mention is a plain overwrite. C1.6 owns stores that die on branches or before early exits, as C1.2's spec reserves.
- **C1.5 (#38, resultless computation):** a setup never read on any path is C1.5's; S2 requires a later read.
- **C3.3 (#62, per-iteration setup):** C3.3 owns heavy setup repeated across loop iterations (hoist it out). C1.6 flags setup wasted on a path, inside or outside a loop; both share `setup-cost.json`.
- **C7.4 (#89, inefficient operation ordering):** C7.4 owns expensive work done before a cheaper filter in general. C1.6 covers only the syntactic case of a binding followed by an exiting guard.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809).
- **Finding:** Setup that is overwritten or abandoned on common paths still allocates and runs; the cost matters when the setup is heavy (allocation, I/O) and the discarding path is frequent.
- **Sanity scan:** run over the CPython 3.13 standard library (559 non-test modules) it yields 7 findings, all S2 and all true positives (e.g. `turtle.py`: a speeds `dict` built before `if speed is None: return self._speed`; `http/cookiejar.py`: `cookies = []` before `return []`). An earlier draft yielded 38; the `raise`-guard, mutator, guard-reads-input and call-order rules above removed the 31 that were error-path validation or reordered side effects.
