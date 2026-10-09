# DB-06 — Queries with known results

Issue #108. Verification plan prepared locally before implementation; not posted to GitHub.

## Detection specification

1. Detection signal: Literal side-effect-free SELECT over ordinary declared tables, with a predicate proven FALSE or UNKNOWN under SQL three-valued logic.
2. Detection tool: PHP 8.2, PDO 8.2/Joomla 4.4; MySQL 8.0/PostgreSQL 16. Bounded SQL AST with integer/null/boolean comparisons and boolean conjunction/disjunction. Deployed owner-b-static-scan; hybrid checks also owner-b-log-analyzer.
3. Telemetry needed: Source plus declared dialect and ordinary-table metadata; no runtime needed.
4. Detectability: M; local proofs are conservative.
5. Measurability: observed requests can be counted with correlated events; no CPU, energy or carbon savings emitted.
6. False-positive risk: declared API/version and ordinary-table assumptions must match the connected project; aliases and unsupported control flow cannot certify clean coverage.
7. Exceptions: purposeful result/SQL use, locking, volatile functions, unknown APIs or semantics suppress findings or make coverage unavailable.

Report output: contract-v1 findings carry scope_id, identity/fingerprint, summary, confidence, recommendation, references and exact source evidence. Runtime hybrid findings also cite correlated query events. Coverage reports unsupported or missing evidence; measurements remain empty without measured impact.

Semantic identity: qualified function + execution ordinal + API. No lines, commit or scan ID in fingerprint.
Context: language/version, db_apis/cache_apis, sql_dialect, ordinary_tables, mode, limits and rule_version. See ../../README.md for the exact format.

## Verification plan

| Case | Condition | Expected |
| --- | --- | --- |
| DB-06-01 | false predicate | Positive finding, exact citations, completed scope |
| DB-06-02 | runtime predicate | No asserted finding; unsupported paths unavailable |
| DB-06-03 | locking/volatile | No asserted finding; unsupported paths unavailable |
| DB-06-04 | missing metadata | Unavailable without required evidence |
| DB-06-05 | unsupported SQL | Error/unavailable, no clean claim |
| DB-06-06 | NULL, coercion, rebinding | NULL comparison yields a known-empty finding; coercion-sensitive expressions never prove emptiness; rebound SQL is unavailable |

Fixture/test: ../../test/g01.test.js and ../../test/fixtures/g01. Validation: npm test; npm run lint; npm run typecheck; python -m shared.contracts.verify.

Independent reviewer challenge: similarly named receiver with no declared API, by-reference escape, and altered telemetry commit must not produce a confirmed finding.

Cloud gates: deployed invocation and hash; real correlated evidence for hybrids; Owner D ingestion and persisted readback. These are not certified by unit fixtures. Reviewer reproduction remains pending.
