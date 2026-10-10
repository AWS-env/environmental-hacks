# DB-13 - Inefficient updating (row-by-row)

Issue #115. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A database write inside a loop, comprehension or forEach/map callback whose receiver or argument depends on the loop variable: Django save/create/update/delete, SQLAlchemy session.add/merge/delete, raw INSERT/UPDATE/DELETE via execute/query, Prisma/Sequelize/Knex/TypeORM create/update/upsert/delete/save/insert.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only.
4. Detectability: H (loop containment plus a resolved write call).
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Per-row signals or transactions can forbid batching; receivers are recognised by name, so renamed handles are missed; loop size is unknown.
7. Exceptions: Bulk APIs (bulk_create/bulk_update/executemany/createMany/updateMany/insertMany/add_all), literal collections or range() of at most `context.max_literal_iterations` (5), stepped batch loops (`i += n`, `range(a, b, step)`), writes independent of the loop variable, TypeORM/MikroORM `create()` (in-memory). One-off paths (seed, migration, management commands) and `*_or_create` are low confidence.

Languages: Python, JavaScript, TypeScript. Sources: SRC-02, SRC-51 (bulk_create/bulk_update; the caveat about signals and overridden save()). Context (explicit, no silent defaults): `{"max_literal_iterations":5}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-13-01 | positive | Django per-row save() over a queryset | finding: `sync:django.save-in-for` |
| DB-13-02 | positive | Django objects.create per element and filter().update() per element | findings: `load:django.create-in-for`, `load:django.update-in-for` |
| DB-13-03 | positive | SQLAlchemy session.add per row and raw INSERT per row | findings: `f:session.add-in-for`, `f:raw-sql.execute-in-for` |
| DB-13-04 | positive | comprehension that creates one row per element | finding: `f:django.create-in-comprehension` |
| DB-13-05 | positive | Prisma update inside for-of and Promise.all(map) | findings: `a:orm.update-in-for-of`, `b:orm.delete-in-map` |
| DB-13-06 | positive | Knex insert and raw UPDATE per row in forEach | findings: `f:orm.insert-in-for`, `f:raw-sql.query-in-forEach` |
| DB-13-07 | negative | bulk APIs are the fix, not the problem | no finding |
| DB-13-08 | negative | JS createMany / updateMany outside a loop | no finding |
| DB-13-09 | negative | write that does not depend on the loop variable | no finding |
| DB-13-10 | negative | a .save() with a positional argument is not a model save (PIL image) | no finding |
| DB-13-11 | negative | a set .add() and a list .append() in a loop are not DB writes | no finding |
| DB-13-12 | exception | loop over a short literal list is exempt | no finding |
| DB-13-13 | boundary | literal list above the threshold is flagged, at the threshold exempt | finding: `over:django.create-in-for` |
| DB-13-14 | positive | name derived from the loop variable inside the body counts | finding: `f:session.add-in-for` |
| DB-13-17 | negative | TypeORM/MikroORM manager.create() is an in-memory entity, not a write | no finding |
| DB-13-18 | positive | one-off seed/management script keeps the finding at low confidence | finding: `seed:django.create-in-for` |
| DB-13-19 | positive | update_or_create per row is reported at low confidence | finding: `f:django.update_or_create-in-for` |
| DB-13-20 | exception | loop stepping by batchSize already writes one batch per iteration | no finding |
| DB-13-21 | exception | range(0, n, step) batching loop is exempt | no finding |
| DB-13-15 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-13-16 | malformed | syntax error file is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
