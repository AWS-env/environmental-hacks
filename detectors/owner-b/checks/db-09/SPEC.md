# DB-09 - Inefficient lazy loading (N+1), explicit form

Issue #111. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A database read inside a loop, comprehension or forEach/map callback whose receiver or arguments depend on the loop variable: Django manager get/first/count/exists/aggregate (or a queryset as a nested loop iterable), SQLAlchemy query().first/all, session.get, execute(select), Prisma/Sequelize/TypeORM find*/count, awaited Knex/Drizzle select, raw SELECT via execute/query. Medium when the loop itself iterates a query result, low otherwise.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only.
4. Detectability: H for the explicit form; the implicit lazy-attribute form (`book.author.name`) needs model definitions and is not covered.
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Loop size is unknown; a loop over a few ids is cheap. Cache and dict lookups are excluded by receiver name only.
7. Exceptions: Short literal loops (`context.max_literal_iterations`), stepped batch loops, reads independent of the loop variable (DB-04), Prisma `findUnique` inside map/forEach (batched by the client), non-database receivers such as cache.get and dict.get.

Languages: Python, JavaScript, TypeScript. Sources: SRC-02, SRC-51, SRC-52; Prisma docs. Context (explicit, no silent defaults): `{"max_literal_iterations":5}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-09-01 | positive | Django: one query per object of a queryset, and a nested queryset loop | findings: `totals:django.count-in-for`, `nested:django.queryset-in-for` |
| DB-09-02 | positive | SQLAlchemy session.query(...).first() and session.get per item | findings: `f:sqlalchemy.first-in-for`, `f:sqlalchemy.get-in-for` |
| DB-09-03 | positive | raw SELECT per item with a parameter from the loop | finding: `f:raw-sql.execute-in-for` |
| DB-09-04 | positive | Prisma findMany per user in for-of, medium confidence over a query result | finding: `f:orm.findMany-in-for-of` |
| DB-09-05 | positive | Sequelize findAll per id in a loop over request ids is low confidence | finding: `f:orm.findAll-in-for-of` |
| DB-09-06 | positive | awaited Knex select inside a for loop | finding: `f:builder.select-in-for-of` |
| DB-09-07 | negative | a read that does not depend on the loop variable is DB-04, not N+1 | no finding |
| DB-09-08 | negative | one query with select_related / in-memory lookups has no per-item query | no finding |
| DB-09-09 | exception | Prisma findUnique inside a map callback is batched by the client | no finding |
| DB-09-10 | exception | loop over a short literal list is exempt | no finding |
| DB-09-11 | negative | a dict/cache get in a loop is not a database read | no finding |
| DB-09-14 | negative | a plain dict that happens to be named objects is not a Django manager (saleor resolvers.py) | no finding |
| DB-09-12 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-09-13 | malformed | syntax error file is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
