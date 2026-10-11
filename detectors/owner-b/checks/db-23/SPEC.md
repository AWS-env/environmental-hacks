# DB-23 - Unbounded queries

Issue #125. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: A materialised read with no limiter: an iterated or list()-ed Django queryset without slice or `.iterator()`, SQLAlchemy `.all()` without limit/first/one, Prisma `findMany`, Sequelize `findAll`, TypeORM-style `find`/`getMany`, an awaited Knex/Drizzle select without `limit`, a raw SELECT passed to execute/query/raw without LIMIT/FETCH/TOP.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only.
4. Detectability: H for the syntactic test; "for display" intent and table size are not visible, so candidate.
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Scoped queries (`where: { userId }`) are bounded in practice: they are low confidence and the scan output shows medium only. Lazy querysets that are returned or stored are not flagged. Wrapper methods are not followed.
7. Exceptions: Slices, first/get/count/exists/aggregate, aggregate-only selects, INSERT ... SELECT, FOR UPDATE, unique-key lookups (`context.unique_key_fields`), options objects with a spread or a variable argument, SQL longer than 8 KB (not analysed).

Languages: Python, JavaScript, TypeScript. Sources: SRC-02 (pagination); the paper's "for display" intent is not statically visible. Context (explicit, no silent defaults): `{"unique_key_fields":["id","pk"]}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-23-01 | positive | Django queryset iterated and list()-ed without a slice | findings: `a:django.queryset`, `b:django.queryset` |
| DB-23-16 | positive | a read with a filter is low confidence (scoped to a parent), a whole-table read is medium | finding: `a:option.findMany` |
| DB-23-18 | negative | findMany(query) with a variable argument has an unknown limiter and is not flagged | no finding |
| DB-23-19 | exception | unique id compared with a quoted literal is exempt | no finding |
| DB-23-20 | positive | one-off integration-test helper keeps the finding at low confidence | finding: `a:option.findMany` |
| DB-23-17 | positive | a whole-table read without any filter is medium confidence | finding: `a:option.findMany` |
| DB-23-02 | positive | SQLAlchemy .all() without limit, query() and select() styles | findings: `a:sqlalchemy.all`, `b:sqlalchemy.all` |
| DB-23-03 | positive | Prisma findMany without take, Sequelize findAll without limit | findings: `a:option.findMany`, `b:option.findAll`, `c:option.findMany` |
| DB-23-04 | positive | TypeORM find() and QueryBuilder getMany() without take | findings: `a:option.find`, `b:querybuilder.getMany` |
| DB-23-05 | positive | raw SELECT without LIMIT in python and node | findings: `a:raw-sql.select`, `b:raw-sql.select` |
| DB-23-06 | positive | awaited Knex select without limit | finding: `f:builder.select` |
| DB-23-07 | negative | limited / sliced / streamed / terminal reads are bounded | no finding |
| DB-23-08 | negative | take / limit present and aggregates are bounded in JS | no finding |
| DB-23-09 | negative | options with a spread have an unknown limiter and are not flagged | no finding |
| DB-23-10 | exception | unique-key lookups and aggregate-only selects are exempt | no finding |
| DB-23-11 | exception | Prisma findMany filtered by unique id is exempt | no finding |
| DB-23-12 | negative | a lazy queryset that is only stored or returned is not flagged | no finding |
| DB-23-13 | negative | Array.find and a non-DB receiver are not repository reads | no finding |
| DB-23-14 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-23-15 | malformed | syntax error file is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
