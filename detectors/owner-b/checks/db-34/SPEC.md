# DB-34 — Unnecessary query construction

Issue #136. Verification plan prepared locally before implementation; not posted to GitHub.

## Detection specification

1. Detection signal: Eager SQL definition precedes a declared cache hit exit; the miss reaches the same definition's actual PDO or Joomla execution.
2. Detection tool: PHP 8.2 AST; declared PDO 8.2/Joomla 4.4 receivers; PSR-16 3.0 null miss or Memcached 3.2 false miss. No receiver-name inference. Deployed owner-b-static-scan; hybrid checks also owner-b-log-analyzer.
3. Telemetry needed: Source only; no runtime impact is quantified.
4. Detectability: M; local proofs are conservative.
5. Measurability: observed requests can be counted with correlated events; no CPU, energy or carbon savings emitted.
6. False-positive risk: declared API/version and ordinary-table assumptions must match the connected project; aliases and unsupported control flow cannot certify clean coverage.
7. Exceptions: purposeful result/SQL use, locking, volatile functions, unknown APIs or semantics suppress findings or make coverage unavailable.

Semantic identity: qualified function + SQL definition ordinal + cache and execution API. No lines, commit or scan ID in fingerprint.
Context: language/version, db_apis/cache_apis, sql_dialect, ordinary_tables, mode, limits and rule_version. See ../../README.md for the exact format.

## Verification plan

| Case | Condition | Expected |
| --- | --- | --- |
| DB-34-01 | cache-hit bypass | Positive finding, exact citations, completed scope |
| DB-34-02 | cache-first | No asserted finding; unsupported paths unavailable |
| DB-34-03 | purposeful SQL use | No asserted finding; unsupported paths unavailable |
| DB-34-04 | unknown cache semantics | Unavailable without required evidence |
| DB-34-05 | malformed PHP | Error/unavailable, no clean claim |
| DB-34-06 | miss-return, overwrite, already-executed, setup-only | No asserted finding; unsupported paths unavailable |

Fixture/test: ../../test/g01.test.js and ../../test/fixtures/g01. Validation: npm test; npm run lint; npm run typecheck; python -m shared.contracts.verify.

Independent reviewer challenge: similarly named receiver with no declared API, by-reference escape, and altered telemetry commit must not produce a confirmed finding.

Cloud gates: deployed invocation and hash; real correlated evidence for hybrids; Owner D ingestion and persisted readback. These are not certified by unit fixtures. Reviewer reproduction remains pending.
