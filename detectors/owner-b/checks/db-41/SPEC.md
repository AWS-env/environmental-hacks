# DB-41 - Synchronous DB APIs in async code

Issue #143. Verification plan generated from the committed cases (`test/orm-cases.js`) by the owner-B spec generator, so the plan and the tests cannot drift.

## Detection specification

1. Detection signal: Inside `async def`: a blocking driver `connect()` of a configured module and execute/fetch*/commit/... on names bound from it or from a sync SQLAlchemy Session; Django ORM terminal calls (get/first/create/save/...), or a plain `for`/list()/len() over a manager queryset.
2. Detection tool: Static syntax-tree analysis (WASM tree-sitter, no client code executed) via `core/orm.js`; deployed on `owner-b-static-scan` (stack `owner-b-jobs`) and in the scan-api worker through the scanner adapter.
3. Telemetry needed: Source files only.
4. Detectability: H (import-resolved callee inside a coroutine).
5. Measurability: No: candidate only. Static candidate: the table size and runtime cost are unknown, so findings are candidates and no energy or carbon figure is emitted.
6. False-positive risk: A connection created in another module and passed in is not recognised. Positive precision is validated on the committed cases and Django's own async tests; production repositories rarely contain this bug (Django raises at runtime).
7. Exceptions: Calls inside sync_to_async / database_sync_to_async / run_in_executor / to_thread / run_sync arguments, nested sync defs, `a`-variants and `async for`, AsyncSession and async drivers.

Languages: Python only (JS/TS database drivers are Promise based). Sources: The cited SRC-50 and SRC-52 do not support the row; Django async docs and SQLAlchemy asyncio docs do (decision D9). Context (explicit, no silent defaults): `{"sync_driver_modules":["psycopg2","pymysql","MySQLdb","sqlite3","mysql.connector","pyodbc","pg8000","psycopg"]}`.

Report output: contract-v1 findings with a semantic identity (qualified function, API, ordinal; no line numbers), exact source or artifact evidence, confidence and references. Coverage reports files that could not be parsed or were not evaluated; those are never "clean". Measurements stay empty.

## Verification plan

| Case | Kind | Condition | Expected |
| --- | --- | --- | --- |
| DB-41-01 | positive | sync psycopg2 connection used inside async def | findings: `handler:sync-driver.connect`, `handler:sync-driver.cursor`, `handler:sync-driver.execute`, `handler:sync-driver.fetchall` |
| DB-41-02 | positive | module-level sqlite3 connection used in a coroutine | findings: `load:sync-driver.execute`, `load:sync-driver.fetchone` |
| DB-41-03 | positive | sync SQLAlchemy Session in a coroutine | findings: `f:sync-driver.execute`, `f:sync-driver.commit` |
| DB-41-04 | positive | Django ORM terminal calls, list() and a plain for in a coroutine | findings: `a:django.get`, `a:django.list`, `a:django.for` |
| DB-41-05 | negative | wrapped in sync_to_async / to_thread / run_in_executor is the documented fix | no finding |
| DB-41-06 | negative | async variants and async for are not blocking | no finding |
| DB-41-07 | negative | the same calls in a plain def are not a coroutine problem | no finding |
| DB-41-08 | negative | a nested sync def inside an async def is not part of the coroutine | no finding |
| DB-41-09 | negative | async drivers and AsyncSession are fine | no finding |
| DB-41-10 | missing | no source for the requested scope is unavailable | status unavailable |
| DB-41-11 | malformed | syntax error file is not evaluated | status unavailable |
| DB-41-12 | malformed | a TypeScript file is outside the Python-only scope and is not evaluated | status unavailable |

Run: `npm test`, `npm run lint`, `npm run typecheck`, `python -m shared.contracts.verify`, `python -m unittest discover -s tests/scanner`. Mutation: always-empty and always-flag detectors must fail committed cases (a test per plan check; `mutate_b` for the static checks).
Cloud gates (not certified by unit tests): deployed invocation and code hash on `owner-b-static-scan`, hub persistence readback, real-repository hand-check. See the PR description for the receipts.
