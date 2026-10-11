# DB-14 - Unnecessary column retrieval

Issue #116. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: Raw SQL whose select list is `*` or `t.*` passed to execute/query/raw/fetch*, and an explicit query-builder `.select('*')`. First pass only: ORM default-all-columns forms need use analysis and are not reported.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only.
4. Detectability: H for raw SQL; M for ORM forms (not implemented).
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: SQL held in a variable and executed elsewhere is not examined; whether the extra columns matter depends on the caller.
7. Exceptions: COUNT(*), EXISTS (SELECT *), INSERT / CREATE ... SELECT *, `SELECT * FROM (subquery)` wrappers, tables in `context.select_star_allowed_tables`.

Languages: Python, JavaScript, TypeScript. Sources: SRC-02, SRC-51 (values()/only()/defer()); sqlfluff AM04 as reference. Context (explicit, no silent defaults): `{"select_star_allowed_tables":[]}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-14-01 | positive | raw SELECT * in python and node drivers | findings: `a:raw-sql.select-star`, `b:raw-sql.select-star` |
| DB-14-02 | positive | qualified star, DISTINCT star and star with extra columns | findings: `a:raw-sql.select-star`, `a:raw-sql.select-star#2`, `a:raw-sql.select-star#3` |
| DB-14-03 | positive | explicit Knex select('*') | finding: `f:builder.select-star` |
| DB-14-04 | negative | COUNT(*), EXISTS (SELECT *), INSERT ... SELECT * and explicit columns are not flagged | no finding |
| DB-14-05 | negative | a star over a derived table with explicit inner columns is a wrapper | no finding |
| DB-14-06 | negative | SQL that is not passed to an execute/query call is not examined | no finding |
| DB-14-07 | exception | a table the project allows (small config table) is exempt | no finding |
| DB-14-08 | positive | one-off script keeps the finding at low confidence | finding: `a:raw-sql.select-star` |
| DB-14-09 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-14-10 | malformed | syntax error file is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
