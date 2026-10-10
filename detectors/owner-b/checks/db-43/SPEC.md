# DB-43 - Full sequential scan (no usable filter/index)

Issue #145. Verification plan generated from the committed cases (`test/plan-checks.test.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A Seq Scan / Parallel Seq Scan with a Filter whose rows examined ((Actual Rows + Rows Removed by Filter) x Actual Loops) reach `context.min_rows_examined` (10000) and whose removed share reaches `context.min_removed_ratio` (0.9).
2. Detection tool: EXPLAIN-plan analysis (`core/plans.js`) of a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) artifact; deployed on `owner-b-static-scan` (stack `owner-b-jobs`), fed by the client collectors in `client-collectors/`.
3. Telemetry needed: A client-produced `explain-plan-v1` artifact (EXPLAIN (ANALYZE, FORMAT JSON)), uploaded to `inputs/` and invoked by key and checksum; no client IAM role.
4. Detectability: H (plan facts); severity needs production table sizes.
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Plans from a small CI database misjudge production. Plain EXPLAIN (no ANALYZE) has no Rows Removed by Filter and is reported as not evaluated.
7. Exceptions: Small scans (below `min_rows_examined`; SRC-05 backs the one-page-table exemption, rows examined is the plan-only proxy), scans keeping most rows, scans without a filter.

Languages: PostgreSQL EXPLAIN JSON (collectors for Python SQLAlchemy/Django and JS/TS Prisma/node-postgres). Sources: SRC-05 PostgreSQL "Using EXPLAIN". Context (explicit, no silent defaults): see the check header.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-43-01 | positive | seq scan discarding 199,960 of 200,000 rows | see test |
| DB-43-02 | negative | primary-key index lookup | see test |
| DB-43-03 | exception | tiny 3-row table seq scan | see test |
| DB-43-04 | boundary | rows examined exactly at the floor is flagged | see test |
| DB-43-05 | boundary | one row above the floor is exempt | see test |
| DB-43-06 | exception | a scan that keeps most rows (ratio floor above the removed share) | see test |
| DB-43-07 | missing | plain EXPLAIN without ANALYZE is not evaluated | see test |
| DB-43-08 | missing | no artifact for the scope | see test |
| DB-43-09 | malformed | plan is not an EXPLAIN JSON array | see test |
| DB-43-10 | malformed | unsupported dialect | see test |
| DB-43-11 | missing | acquisition incomplete | see test |
| DB-43-12 | partial | one good and one malformed query | see test |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
