# Detection Spec — CODE-C1.2 Redundant assignment

- **Taxonomy ID:** `CODE-C1.2`
- **Issue:** #35
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | A statement that assigns a target to itself (`x = x`, `a, b = a, b`, `self.n = self.n`, `row[i] = row[i]`), or a function-local `x = …` overwritten by a later `x = …` in the same block before any read (dead store) |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, expr, loopType}` + `location` (dead store: from the store to the overwrite) |
| **False-positive risk** | Low for names (module/class re-exports, closures, `global`/`nonlocal`, frame introspection and exception paths are guarded); Medium for attribute/subscript targets (property setters, `__setitem__`) and for stores whose right-hand side runs code |
| **Detectable** | High (H) for self-assignment and straight-line dead stores |
| **Measurable** | Low (L) statically (gain = statement cost × execution count; count needs a profile) |

## Scope & Signals

- **S1 Self-assignment (`self-assignment`):**
  - Name form `x = x` / `a, b = a, b` (pairwise identical, so a swap `a, b = b, a` is not flagged): only inside a function where every name is already bound (parameter or earlier mention). In a module or class body `TimeoutError = TimeoutError` copies a builtin into that namespace (a re-export), and an unbound local raises `UnboundLocalError` (a bug, not waste). Severity `low`, Confidence `high`.
  - Attribute / subscript form `self.n = self.n`, `row[i] = row[i]`: target compared token by token; targets containing a call (`get().x = get().x`, `d[k()] = d[k()]`) are skipped because the call runs twice. Severity `low`, Confidence `medium`; limitation: property setters, `__setattr__` and `__getitem__`/`__setitem__` (e.g. `defaultdict`) can make the statement observable.
- **S2 Dead store (`dead-store`):**
  - A plain or annotated `x = <value>` statement in a function, followed in the **same block** by `x = <value>` whose right-hand side does not read `x`, with no statement between them that mentions `x` (attribute names `obj.x` and keyword names `f(x=…)` are not mentions), returns, raises, `break`s or `continue`s.
  - Severity `medium` when the discarded value is computed by a call / `await` / `yield` / walrus (real work thrown away); otherwise `low`. Confidence `high` for literal-like values (including empty built-in constructors such as `set()`), `medium` when the discarded value runs code (calls, attribute/subscript lookups), with the limitation to keep the expression as a bare statement if its side effects are needed.
  - Guards: names declared `global`/`nonlocal`; names mentioned in a nested function, lambda or class (closures may read them during a call between the stores); functions calling `locals()`, `vars()`, `eval`, `exec`, `sys._getframe` or `inspect.currentframe`; the throwaway name `_`; `x = None` immediately before reloading an already-bound `x` with a call (deliberate release that lowers peak memory).
  - Inside a `try` or `with` (any clause, within the function) an exception between the stores would keep the first value, so only an **adjacent** overwrite by a literal (which cannot raise) is flagged there.
  - Not analysed: module and class bodies (other modules or methods may read the store during intervening calls).

One finding per redundant statement; a chain `x = 1; x = 2; x = 3` reports the first two stores.
The enclosing `for`/`while` loop, if any, is reported in `evidence.loopType` and in `why`,
since the taxonomy notes the waste matters mainly inside hot loops.

## Fingerprint & Suppressions

- `generateFingerprint("CODE-C1.2", kind, path, id)` with
  `id = <enclosing def/class qualname>:<normalized statement>:<ordinal>` (S1) or
  `<enclosing qualname>:<name>:<normalized store statement>:<ordinal>` (S2), where the ordinal
  counts identical keys in the file. Line numbers stay out of the hash; the contract identity is
  `<kind>:<id>`.
- `# noqa: CODE-C1.2` (or blanket `# noqa`) on the statement (S1), or on the store or the
  overwriting line (S2). The shared default list in `core/suppressions.ts` stays
  `["F401", "CODE-C1.1"]`.

## Boundaries with Sibling Checks

- **C1.1 (#34, dead code):** C1.1 owns unreachable statements; S2 stops at `return`/`raise`/`break`/`continue`, so an overwrite after a terminal statement is never C1.2's.
- **C1.5 (#38, resultless computation):** C1.5 owns values that are never read at all (`rows = fetch_all()` falling off the end of a function, discarded expression statements). C1.2 requires a later overwrite.
- **C1.6 (#39, unnecessary initialization):** C1.6 owns stores that die on *some* paths: overwrites in branches (`x = None; if c: x = a else: x = b`) or before early exits. C1.2 only reports straight-line overwrites in one block, which die on every path.
- **C1.7 (#40, unnecessary variable):** C1.7 owns temporaries that are read once (`tmp = f(); return tmp`); those are read, so never C1.2's.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809).
- **Finding:** Redundant statements leave state unchanged yet still execute; each is negligible alone, but inside hot loops the wasted work scales with the trip count.
- **Sanity scan:** run over the CPython 3.13 standard library (633 non-test modules) it yields one finding, a true positive (`unittest/mock.py`: `remove_magics = set()` overwritten on the next line); the module-level `TimeoutError = TimeoutError` re-exports it surfaced before the S1 scope rule are now excluded.
