# DB-05 — Dead-store queries

Issue #107. Verification plan prepared locally before implementation; not posted to GitHub.

## Detection specification

1. Detection signal: A read result is overwritten by another executed read before use, in supported straight-line local flow; paired events share request, transaction and immutable snapshot.
2. Detection tool: Same PHP/SQL API models; query-event-v1 CloudWatch message format. Deployed owner-b-static-scan; hybrid checks also owner-b-log-analyzer.
3. Telemetry needed: Source and complete query events; candidate mode is explicit and never runtime confirmation.
4. Detectability: M; local proofs are conservative.
5. Measurability: observed requests can be counted with correlated events; no CPU, energy or carbon savings emitted.
6. False-positive risk: declared API/version and ordinary-table assumptions must match the connected project; aliases and unsupported control flow cannot certify clean coverage.
7. Exceptions: purposeful result/SQL use, locking, volatile functions, unknown APIs or semantics suppress findings or make coverage unavailable.

Report output: contract-v1 findings carry scope_id, identity/fingerprint, summary, confidence, recommendation, references and exact source evidence. Runtime hybrid findings also cite correlated query events. Coverage reports unsupported or missing evidence; measurements remain empty without measured impact.

Semantic identity: qualified function + overwritten binding + execution ordinal + reload ordinal. No lines, commit or scan ID in fingerprint.
Context: language/version, db_apis/cache_apis, sql_dialect, ordinary_tables, mode, limits and rule_version. See ../../README.md for the exact format.

## Verification plan

| Case | Condition | Expected |
| --- | --- | --- |
| DB-05-01 | overwrite | Positive finding, exact citations, completed scope |
| DB-05-02 | use before overwrite | No asserted finding; unsupported paths unavailable |
| DB-05-03 | locking | No asserted finding; unsupported paths unavailable |
| DB-05-04 | missing correlation | Unavailable without required evidence |
| DB-05-05 | malformed events | Error/unavailable, no clean claim |
| DB-05-06 | branch/alias | No asserted finding; unsupported paths unavailable |

Fixture/test: ../../test/g01.test.js and ../../test/fixtures/g01. Validation: npm test; npm run lint; npm run typecheck; python -m shared.contracts.verify.

Independent reviewer challenge: similarly named receiver with no declared API, by-reference escape, and altered telemetry commit must not produce a confirmed finding.

Cloud gates: deployed invocation and hash; real correlated evidence for hybrids; Owner D ingestion and persisted readback. These are not certified by unit fixtures. Reviewer reproduction remains pending.
