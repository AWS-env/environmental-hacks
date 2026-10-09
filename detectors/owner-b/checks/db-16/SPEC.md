# DB-16 — Unnecessary whole queries

Issue #118. Verification plan prepared locally before implementation; not posted to GitHub.

## Detection specification

1. Detection signal: An actual read query result is discarded or locally unused, excluding writes, locks, volatile expressions, streaming escapes and unknown side effects.
2. Detection tool: Same PHP/SQL API models; query-event-v1 CloudWatch message format. Deployed owner-b-static-scan; hybrid checks also owner-b-log-analyzer.
3. Telemetry needed: Source and complete executed query events; candidate mode is explicit.
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
| DB-16-01 | discarded read | Positive finding, exact citations, completed scope |
| DB-16-02 | returned/iterated | No asserted finding; unsupported paths unavailable |
| DB-16-03 | locking/volatile | No asserted finding; unsupported paths unavailable |
| DB-16-04 | missing telemetry | Unavailable without required evidence |
| DB-16-05 | malformed PHP/event | Error/unavailable, no clean claim |
| DB-16-06 | closure/iterator/lazy builder | No asserted finding; unsupported paths unavailable |

Fixture/test: ../../test/g01.test.js and ../../test/fixtures/g01. Validation: npm test; npm run lint; npm run typecheck; python -m shared.contracts.verify.

Independent reviewer challenge: similarly named receiver with no declared API, by-reference escape, and altered telemetry commit must not produce a confirmed finding.

Cloud gates: deployed invocation and hash; real correlated evidence for hybrids; Owner D ingestion and persisted readback. These are not certified by unit fixtures. Reviewer reproduction remains pending.
