# DB-46 - Nested-loop inner child executed per outer row

Issue #148. Verification plan generated from the committed cases (`test/plan-checks.test.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A Nested Loop whose INNER child is itself a Seq Scan executed `Actual Loops` >= `context.min_inner_loops` (100) times and examining at least `context.min_rows_examined` (10000) rows in total.
2. Detection tool: EXPLAIN-plan analysis (`core/plans.js`) of a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) artifact; deployed on `owner-b-static-scan` (stack `owner-b-jobs`), fed by the client collectors in `client-collectors/`.
3. Telemetry needed: A client-produced `explain-plan-v1` artifact with ANALYZE.
4. Detectability: H (structural: the inner node itself).
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Plans from a small CI database misjudge production; the planner often flips join order or adds Materialize/Bitmap nodes, so only the inner Seq Scan itself is reported.
7. Exceptions: Inner Index/Bitmap scans, Materialize/Memoize wrappers (the scan below runs once), small outer sets.

Languages: PostgreSQL EXPLAIN JSON. Sources: SRC-05 (the inner child runs once per outer row; loops = number of executions); SRC-06 (nested loops need an indexed inner side). Context (explicit, no silent defaults): see the check header.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-46-01 | positive | inner Seq Scan executed 300 times over 20,000 rows | see test |
| DB-46-02 | negative | inner Bitmap Heap Scan (indexed) executed 3 times | see test |
| DB-46-03 | boundary | loops floor equal to 300 is flagged | see test |
| DB-46-04 | boundary | loops floor 301 is exempt | see test |
| DB-46-05 | exception | rows-examined floor above the total | see test |
| DB-46-06 | missing | plain EXPLAIN has no actual loops | see test |
| DB-46-07 | exception (synthetic, derived from the real plan) | exception (synthetic, derived from the real plan): a Materialize wrapper means the scan below runs once | see test |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
