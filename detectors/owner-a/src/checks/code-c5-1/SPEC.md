# Detection Spec - CODE-C5.1 Inefficient structure choice (list membership in a loop)

- **Taxonomy ID:** `CODE-C5.1`
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Status:** Implemented (v0.1.0)

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | `x in NAME` / `x not in NAME` inside the body of a `for`/`while` loop, or in the element or condition of a comprehension/generator, where `NAME` resolves to a list or tuple |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution, no imports |
| **Telemetry needed** | None. No profiler artifact |
| **Kind** | `list-membership-in-loop` |
| **Severity / confidence** | medium / medium; confidence high when every binding is a list literal with more than 8 known elements or a list comprehension |
| **Identity** | `list-membership-in-loop:<qualname>:<name>:<ordinal>` (line-free; ordinal per qualname + name) |
| **Report output field** | `finding.evidence.{symbol, expr, loopType, suggested}` + `location` (the membership test) |

## What is flagged

`NAME` is a plain identifier or a `self.attr` chain. Its binding must be visible in the same
function (identifiers; module level if the function never binds the name), or in the enclosing
class / module (`self` chains), and every visible binding must be a list or tuple:

- list literal, `list(...)`, list comprehension, tuple literal, `tuple(...)`
- `.split()` / `.splitlines()` result, `sorted(...)`
- a parameter or variable annotated `list` / `List` / `tuple` / `Tuple` / `Sequence`

## Not flagged

- `NAME` bound to a set / frozenset / dict / `dict.keys()` / `range` / `str`, or any other
  non-list binding (one non-list binding among several makes the name unknown).
- Unknown binding: imported names, unannotated parameters, names never assigned in this file.
- Membership against an inline literal (`x in [1, 2, 3]`, `x in (a, b)`): style, covered by Ruff PLR6201.
- A list/tuple literal with 8 or fewer elements (every binding known and small).
- The collection is mutated or rebound (`append/extend/insert/remove/pop/clear`, `+=`, item
  assignment, `del`, reassignment) anywhere inside the same loop(s).
- Loops whose body unconditionally exits (`break`/`return`/`raise` at top level of the body).
- Statically small trip count: every enclosing for loop / comprehension iterates (1) a literal
  list/tuple/set/dict with 8 or fewer elements, (2) a name whose every visible binding is such a
  literal, (3) `<name>.items()/.keys()/.values()` where `<name>` is bound only to a dict literal with
  8 or fewer entries, or (4) `range(<integer literals>)` with 8 or fewer iterations. A `*splat`,
  comprehension element, or mutation of the iterated name (append/update/item-assign/...) makes the
  size unknown, so those stay flagged. While loops are never "small"; a nested large outer loop keeps
  the finding. A rebinding of the iterated name to a single-clause comprehension or generator whose
  only iterable is that same name (`name`, `name.items()`, `name.keys()`, `name.values()`) can only
  filter or map, so it still counts as small, provided the name also has at least one real small base
  binding (real-repo case: `rich/jupyter.py`, `data = {..2 keys..}` then `data = {k: v for k, v in
  data.items() if k in include}`). A comprehension over any other name, or with two `for` clauses,
  keeps the name unknown.
- The iterable of a comprehension's first `for ... in` (evaluated once).
- Chained comparisons (`a in b in c`).
- `# noqa` or `# noqa: CODE-C5.1` on the loop header line or the test line.

## Limitations

- Binding resolved only inside this file.
- List size and trip count are not measured.
- A set requires hashable elements, and equality-vs-hash semantics must match: elements of an
  unhashable type or with a custom `__eq__` cannot go in a set.
