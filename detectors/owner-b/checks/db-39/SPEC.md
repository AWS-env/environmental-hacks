# DB-39 - Offset pagination (Skip/Take) on large tables

Issue #141. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: An offset on a read query: SQLAlchemy/Knex/Drizzle `.offset()`, TypeORM/Mongoose `.skip()` (low confidence), Prisma/Sequelize/TypeORM `skip`/`offset` option of find-style methods, a Django queryset slice (also through a variable assigned from a queryset), Django `Paginator`, raw SQL `OFFSET n` / `LIMIT a, b`.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only. No runtime telemetry.
4. Detectability: H (presence of the offset); depth and table size are runtime facts.
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: Wrapper methods and offsets passed through variables are missed (known false negatives, measured on medusa); SQL assembled from fragments is not examined.
7. Exceptions: Literal offsets below `context.min_literal_offset` (100, a configured choice), seek pagination (no offset).

Languages: Python, JavaScript, TypeScript. Sources: SRC-06 (offset counts every row up to the page; seek skips them). Context (explicit, no silent defaults): `{"min_literal_offset":100}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-39-01 | positive | Django queryset slice from a request-derived page | finding: `article_page:django.slice` |
| DB-39-02 | positive | SQLAlchemy .offset() with a variable | finding: `Repo.page:sqlalchemy.offset` |
| DB-39-03 | positive | Prisma findMany skip with a variable | finding: `listUsers:option.skip` |
| DB-39-04 | positive | Sequelize findAll offset and Knex .offset() | findings: `a:option.offset`, `b:builder.offset` |
| DB-39-05 | positive | raw SQL OFFSET placeholder (python) and LIMIT a,b (ts) | finding: `f:raw-sql.offset` |
| DB-39-06 | positive | two raw queries in one function get stable ordinals | findings: `f:raw-sql.offset`, `f:raw-sql.offset#2` |
| DB-39-07 | negative | seek pagination has no offset | no finding |
| DB-39-08 | negative | plain list slice is not a queryset | no finding |
| DB-39-09 | negative | SQL without OFFSET | no finding |
| DB-39-10 | exception | skip: 0 and a small literal offset are exempt | no finding |
| DB-39-11 | boundary | literal offset exactly at the minimum is flagged | finding: `f:builder.offset` |
| DB-39-12 | boundary | literal offset one below the minimum is exempt | no finding |
| DB-39-13 | missing | no source for the requested scope is unavailable, never clean | status unavailable |
| DB-39-14 | malformed | syntax error file is not evaluated | status unavailable |
| DB-39-15 | malformed | unsupported language is not evaluated | status unavailable |
| DB-39-16 | positive | Django queryset held in a variable then sliced | finding: `page:django.slice` |
| DB-39-17 | negative | slicing a materialised list(qs) is not a queryset offset | no finding |
| DB-39-18 | boundary | KNOWN LIMIT: skip inside a wrapper-method options object (listAndCount) is not recognised | no finding |
| DB-39-19 | negative | bare {offset, limit} response metadata object is not a query | no finding |
| DB-39-20 | negative | offset on a non-find callee is ignored | no finding |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
