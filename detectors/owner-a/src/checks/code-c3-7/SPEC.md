# Detection Spec — CODE-C3.7 Inefficient array mutation

- **Taxonomy ID:** `CODE-C3.7`
- **Issue:** #66
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | `for` loop whose body mutates the collection it iterates (`remove/pop/popitem/discard/append/extend/insert/add/update/clear`, `del coll[…]`, `coll += …`, slice store), or front re-indexing (`pop(0)`, `insert(0, …)`, `del x[0]`) on a list inside any loop |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, mutator, loopType}` + `location` (the mutation) |
| **False-positive risk** | Medium — intentional worklist/drain idioms, deque queues, unknown container type for S2 |
| **Detectable** | H (syntactic loop + call pattern) |
| **Measurable** | L statically (per-instance savings high per S01, but frequency unmeasurable without profile) |

## Signals (one finding per mutation site)

| Signal | Kind | Shape | Severity | Confidence |
|---|---|---|---|---|
| **S1 remove/delete from the iterated collection** | `mutate-during-iteration` | `for x in items: items.remove(x)`; `for k in d: del d[k]`; `for k, v in d.items(): d.pop(k)` | high | high |
| **S2 front re-indexing in a loop** | `front-reindex-in-loop` | `while q: q.pop(0)`; `for x in xs: out.insert(0, x)`; `del buf[0]` in a loop | high | high when a list binding is visible (`= []`, `list(…)`, comprehension, `list` annotation), else medium |
| **S3 grow the iterated collection** | `mutate-during-iteration` | `for n in todo: todo.append(child)` (implicit worklist) | low | medium |
| **S4 clear / slice store on the iterated collection** | `mutate-during-iteration` | `for x in a: a[:] = …`; `a.clear()` | medium | medium |

"Iterated collection" means the loop's iterable is the same name chain (`items`, `self.items`),
its `.keys()` / `.values()` / `.items()` view, or `enumerate(<chain>)`. A front mutation on the
iterated collection is reported once, as S1.

## False-positive guards (negatives)

- Iterate over an explicit copy: `items[:]`, `list(items)`, `tuple(items)`, `sorted(items)`,
  `[*items]`, `dict(d)`, `list(d.items())` — these are not the same chain, so they never match.
- Building a new collection (comprehension, `filter()`, append to a different list).
- `collections.deque` bindings (`deque(…)`, `collections.deque(…)`, `deque` / `Deque[…]`
  annotations): S2 is skipped (decision 4: suppress, no Info hint).
- `pop()` / `pop(-1)` from the end → not front re-indexing.
- Reverse-index delete (`for i in range(len(a)-1, -1, -1): del a[i]`) → the loop iterates a
  `range`, not `a`, and `del a[i]` is not a front delete. Suppressed in v1 (decision 3).
- Mutation of a different object with a similar name (`self.items` vs `items` vs
  `other.items`) — full base chains are compared, never substrings.
- Mutation followed in its own block by `break` (owned by the loop), `return` or `raise` —
  remove-one-then-stop is safe and runs at most once.
- Mutations in a nested `def` / `lambda` / `class` inside the loop, or in the loop header.
- Index stores (`a[i] = v`) — not length-changing.
- `# noqa: CODE-C3.7` (or blanket `# noqa`) on the loop header or the mutation line.

## Boundaries with sibling checks

- **C3.6 (#65):** process the whole collection when only part is needed. A copy taken so the
  loop can mutate the original (`for x in items[:]: items.remove(x)`) is required, not waste,
  and is a negative for both checks.
- **C6.2 (#80):** unnecessary copying is the opposite direction (copy when a view suffices).
- **C5.1 (#75):** container choice. C3.7 owns front mutation **inside a loop**; C5.1 owns
  list → `deque` / `set` everywhere else (for example `x in list` membership).

## Fingerprint

`generateFingerprint("CODE-C3.7", kind, path, id)` with
`id = <enclosing def/class qualname>:<base chain>:<mutator>:<ordinal among identical matches in that scope>`.
Line numbers stay out of the hash.

## Known limitations (v1)

- Container type is inferred only from bindings visible in the file; a deque passed in as an
  untyped parameter looks like a list (S2 at medium confidence, with a limitation).
- `while` loops are only checked for S2; grow-while-drain (`while i < len(a): a.append(…)`)
  and manual index-delete loops are not flagged.
- Comprehensions that mutate (`[q.pop(0) for _ in range(n)]`) are not walked as loops.
- Python only; JS/TS `splice`-in-loop coverage is a follow-up.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809) — C3.S7 inefficient array mutation.
- **Taxonomy caveat:** "Rare but costly; highest mean savings per instance in S01." Findings never quantify savings.
