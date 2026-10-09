# Detection Spec — CODE-C1.3 Redundant control flow

- **Taxonomy ID:** `CODE-C1.3`
- **Issue:** #36
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | A branch whose condition is evaluated but cannot change what runs: an `if`/`elif`/`else` chain whose arms are all token-identical, a conditional expression `v if c else v`, an `if` whose arms are all empty (`pass` / `...`), or empty trailing `elif` arms |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{expr (evaluated conditions), loopType}` + `location` (the statement, or from the first empty trailing `elif` to its end) |
| **False-positive risk** | Low. Medium when a condition runs code (calls, `await`, walrus, attribute/subscript lookups): collapsing must keep it as a bare statement if its side effects are needed. A condition on a user object can also run `__bool__` / `__eq__` |
| **Detectable** | High (H): identical and empty arms are exact syntax matches |
| **Measurable** | Low (L) statically (gain = condition cost × execution count; count needs a profile) |

## Scope & Signals

The rule: flag control flow only when it **evaluates a condition** whose outcome cannot change what runs.
Constructs that evaluate nothing are style, not compute waste, and are left to linters.

- **S1 Identical branches (`identical-branches`):**
  - `if a: X elif b: X else: X`: every arm, including a required `else`, has the same body (compared token by token, so comments and whitespace are ignored). Without an `else`, "nothing runs" is a distinct outcome, so the conditions matter.
  - `v if c else v`: both arms of a conditional expression are the same.
- **S2 Empty branch (`empty-branch`):**
  - `if c: pass` (all arms empty, with or without an empty `else`): every condition is evaluated and nothing depends on it.
  - Trailing empty `elif` arms (`if a: X elif b: pass`): when `b` holds nothing runs, the same as when it does not. Only the trailing run counts: an empty arm followed by a non-empty one (`if a: pass else: X`) keeps the later arm from running and is meaningful.
- Severity `medium` when a skipped condition contains a call or `await` (real work thrown away); otherwise `low`. Confidence `high` for names, literals and operators; `medium` when a condition runs code (call / `await` / `yield` / walrus / attribute / subscript), with the limitation to keep it as a bare statement.
- An `if` whose arms are identical *and* empty is reported once, as S2. Nested redundancy is reported at the innermost redundant statement.
- Module-level code is analysed too; the enclosing `for`/`while` loop or comprehension, if any, is reported in `evidence.loopType` and in `why`, since the waste matters mainly in hot paths.

**Not flagged (zero cost):** a trailing `continue` in a loop body, a bare `return` / `return None` at the end of a function, and an empty `else: pass`. CPython compiles each to the same jump as the implicit one, so there is no executed work to save. `match` statements are not analysed.

## Fingerprint & Suppressions

- `generateFingerprint("CODE-C1.3", kind, path, id)` with
  `id = <enclosing def/class qualname>:<arm headers>:<ordinal>` (for example `f:if x > 0:|else:`), plus the index of the first flagged arm for trailing `elif`s. A ternary uses its normalized text. The ordinal counts identical keys in the file. Line numbers stay out of the hash; the contract identity is `<kind>:<id>`.
- `# noqa: CODE-C1.3` (or blanket `# noqa`) on any flagged arm's header line (`if`, `elif` or `else`), or on the ternary's line.

## Boundaries with Sibling Checks

- **C1.1 (#34, dead code):** C1.1 owns constant-false conditions (`if False:`, `while 0:`), where a branch can never run. C1.3 needs the outcome not to depend on the condition, whatever its value.
- **C4.2 (#68, redundant conditional):** C4.2 owns conditions whose *value* is statically known or unchanged (`if True:`, re-testing a checked condition). C1.3 owns branches whose *outcome* is the same either way.
- **C4.8 (#74, non-idiomatic condition):** `if c: return True else: return False` has different arms; rewriting it as `return bool(c)` is C4.8's.
- **C1.5 (#38, resultless computation):** a bare expression statement left after collapsing an effectful condition is intentional, not C1.5's.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809).
- **Finding:** Branches that do not alter execution still evaluate their conditions; each is negligible alone, but inside hot paths the wasted evaluations scale with the trip count.
- **Sanity scan:** run over the CPython 3.13 standard library (574 non-test modules) it yields one finding, a true positive (`platform.py`: `if release < '6': system = 'Solaris' else: system = 'Solaris'`, marked `XXX`).
