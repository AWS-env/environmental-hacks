# DB-45 - Sort spilling to disk / large external sort

Issue #147. Verification plan generated from the committed cases (`test/plan-checks.test.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A Sort node with `Sort Space Type` = Disk and `Sort Space Used` of at least `context.min_sort_space_kb` (0: any disk sort).
2. Detection tool: EXPLAIN-plan analysis (`core/plans.js`) of a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) artifact; deployed on `owner-b-static-scan` (stack `owner-b-jobs`), fed by the client collectors in `client-collectors/`.
3. Telemetry needed: A client-produced `explain-plan-v1` artifact with ANALYZE (Sort Method exists only then).
4. Detectability: H (boolean plan field).
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Whether a disk sort matters depends on query frequency and the server's work_mem, which the plan does not carry. Plain EXPLAIN is reported as not evaluated.
7. Exceptions: In-memory sorts (quicksort, top-N heapsort).

Languages: PostgreSQL EXPLAIN JSON. Sources: SRC-05 (the Sort node reports in-memory vs on-disk and the space used); SRC-06 (index order can omit the sort). Context (explicit, no silent defaults): see the check header.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-45-01 | positive | serial external merge sort on disk (8,280 kB) | see test |
| DB-45-02 | positive | parallel Gather Merge with a Disk sort | see test |
| DB-45-03 | negative | in-memory sort | see test |
| DB-45-04 | boundary | floor equal to the spill size is flagged | see test |
| DB-45-05 | boundary | floor one kB above is exempt | see test |
| DB-45-06 | missing | plain EXPLAIN has no Sort Method | see test |
| DB-45-07 | malformed | empty plan array | see test |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
