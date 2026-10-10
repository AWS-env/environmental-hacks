# Detection Spec - CODE-C6.7 Leaking mutable defaults

- **Taxonomy ID:** `CODE-C6.7`
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Status:** Implemented

## Rule

For each `def` / `async def` / `lambda` parameter whose default is a list/dict/set literal, a
list/dict/set comprehension, or a call to `list()` / `dict()` / `set()` / `defaultdict(...)` /
`collections.deque()` / `bytearray()`: flag **only if** the function body mutates that
parameter name:

- method calls `append extend insert add update setdefault pop popitem remove discard clear sort reverse appendleft`
- augmented assignment `p += ...`
- item assignment `p[k] = ...` (also `p[k] += ...`)
- `del p[k]`

This mirrors CodeQL `py/modification-of-default-value` (the default is actually modified) and is
deliberately narrower than Ruff B006 and Pylint W0102, which fire even when the default is never
mutated. A never-mutated default is not reported.

## Output

| Field | Value |
|---|---|
| kind | `mutable-default-mutated` |
| severity | medium |
| confidence | high: list/dict/set literal mutated by a method call; medium: other forms (comprehension, call, `+=`, item assign, `del`); low: memo-style name |
| identity | `mutable-default-mutated:<qualname>:<param>` (`:<n>` appended for repeats of the same function/param) |
| location | the mutation (first one in source order) |
| evidence | `snippet` = the `def` line plus the mutation line; `symbol`, `function`, `mutator`, `defaultLine` |
| agentPrompt | default to `None`, create the collection inside the function (`if p is None: p = []`) |

One finding per (function, parameter), at the first mutation.

## Not flagged

- Default never mutated.
- Parameter re-bound before the mutation, anywhere earlier in the body in source order
  (`p = list(p)`, `p = p or []`, `if p is None: p = []`, `p = copy.copy(p)`, `for p in ...`, `(p := ...)`).
  A mutation in a branch with no earlier re-binding still counts; a mutation after a re-binding does not.
- Defaults `None`, numbers, strings, tuples, `frozenset(...)`.
- Mutation inside a nested `def` / `lambda` that shadows the name (parameter, or local assignment
  without `nonlocal`/`global`). A nested function that does not shadow it mutates the default and is flagged.
- Parameters annotated `Final`, `tuple`/`Tuple`, `frozenset`/`FrozenSet`, `Sequence`, `Mapping`,
  `AbstractSet`, `Iterable`, `Collection`, `ReadOnly` (outermost annotation name, any module prefix).
  Judgement call: the annotation declares read-only intent, so a mutation there is a typing error
  the type checker reports, and the default is not meant to be a shared mutable.
- `# noqa`, `# noqa: CODE-C6.7`, `# noqa: B006` on the `def` line, the parameter line, or the mutation line.

## Deliberate caching

Parameters named `cache`, `_cache`, `memo`, `_memo`, `memoize`, `seen`, `_seen`, `visited`,
`_visited` are still reported but with confidence `low` and a limitation that it may be an
intentional memo (Ruff B006 documents deliberate caching as a reason to ignore the rule).

## Known limitations

- The finding rests on Python's default-argument semantics (evaluated once at definition time)
  and the Ruff B006, Pylint W0102 and CodeQL docs. SRC-01 did not observe this smell in its Python data.
- Static only: call frequency and growth are not measured; impact is not quantified.
- Aliases (`q = p; q.append(x)`) and mutation inside a helper that receives `p` are not seen.
- Re-binding is ordered by source position, not by control flow: `if c: p = []` followed by
  `p.append(x)` is treated as safe even though the `else` path still mutates the default.
- Python only; parse only, client code is never imported or executed.

## Prior art

- Ruff B006: https://docs.astral.sh/ruff/rules/mutable-argument-default/
- Pylint W0102: https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/dangerous-default-value.html
- CodeQL: https://codeql.github.com/codeql-query-help/python/py-modification-of-default-value/
