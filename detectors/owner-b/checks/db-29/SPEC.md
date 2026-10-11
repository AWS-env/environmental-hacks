# DB-29 - Joining unused tables

Issue #131. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: In a single SELECT passed to execute/query/raw/fetch*, a LEFT JOIN whose table or alias is never referenced outside its own ON clause (select list, WHERE, GROUP BY, HAVING, ORDER BY, another join's ON).
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter. Uses `node-sql-parser` for the SQL AST.
3. Telemetry needed: Source files only.
4. Detectability: H to flag; removal safety needs review (a join can change the row count).
5. Measurability: No: flag only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: An unused LEFT JOIN that matches several rows changes the result when removed. Tagged-template SQL (`Prisma.sql`) and SQL in variables are not examined.
7. Exceptions: Inner/RIGHT/FULL joins, any unqualified column or bare `*`, subqueries/CTEs/UNION, SQL that does not parse, SQL longer than 8 KB. Placeholders are normalised before parsing.

Languages: Python, JavaScript, TypeScript (raw SQL strings). Sources: SRC-02 AP-29; sqlfluff ST11 as reference. Context (explicit, no silent defaults): `{"sql_dialects":["postgresql","mysql"]}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-29-01 | positive | LEFT JOIN whose table is never used outside its ON clause | finding: `f:raw-sql.unused-join:c` |
| DB-29-02 | positive | a chain: the second LEFT JOIN is unused, the first is used by it | finding: `f:raw-sql.unused-join:c` |
| DB-29-03 | positive | placeholders and an interpolated table still parse | finding: `f:raw-sql.unused-join:u` |
| DB-29-04 | negative | a joined table used in the select list, WHERE or ORDER BY is used | no finding |
| DB-29-05 | negative | inner joins filter rows and are never flagged | no finding |
| DB-29-06 | negative | an unqualified column or SELECT * could belong to the joined table: not provable | no finding |
| DB-29-07 | negative | subqueries are not analysed (correlated references) | no finding |
| DB-29-08 | negative | SQL that does not parse is skipped, never reported | no finding |
| DB-29-09 | positive | one-off script keeps the finding at low confidence | finding: `f:raw-sql.unused-join:c` |
| DB-29-10 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-29-11 | malformed | syntax error file is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
