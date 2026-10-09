# Owner B: G01 checks

`evaluate(input)` from index.js is the validated contract-v1 API. This issue branch registers DB-06 (#108). The shared G01 engine and its regression tests are common prerequisites; other detector registrations are reviewed in their own PRs. `cli.js` accepts an input JSON path. The original `scanSource`/`scanFile` interface remains a legacy compatibility API; its heuristic findings must never be published as shared-contract results.

## Context and scope

Use one PHP source per file scope. `detector_version` is `1.0.0`; a full lowercase commit SHA is mandatory. Context requires:

```json
{
  "language": "php", "php_version": "8.2", "rule_version": "g01-1",
  "mode": "runtime", "sql_dialect": "mysql-8.0",
  "ordinary_tables": ["users", "articles"],
  "db_apis": {"pdo": {"family": "pdo", "version": "8.2"}},
  "cache_apis": {"cache": {"family": "psr16", "version": "3.0"}},
  "limits": {"max_source_bytes":262144,"max_ast_nodes":20000,"max_ast_depth":128,"max_sql_bytes":8192}
}
```

The authenticated connector supplies API/table identities from the project's configuration. Variable names alone are not type proof. Models support PDO 8.2 `query`; Joomla 4.4 `setQuery` plus `loadObjectList/loadAssocList/loadResult/loadObject/loadAssoc`; PSR-16 3.0 (`!== null`), Memcached 3.2 (`!== false`), and PSR-6 3.0 (`getItem` followed by `isHit`). Truthiness, APCu, prepared execution, other versions/languages and dynamic dispatch are not certified. PostgreSQL 16 uses `sql_dialect=postgresql-16`.

All scope units are checked conservatively. Unsupported branches/loops, closures, dynamic variables, receiver reassignment/escapes and nonlocal/reference assignments make that file unavailable. Malformed/over-bound PHP is error; independent files may produce partial results. SQL must be a single bounded statement. Ordinary tables must exclude views/foreign tables and other hidden effects. SQL functions, aggregates, CTEs, locks, output/session operations and unknown table semantics do not produce removal findings. DB-06 proves only integer/null comparisons, literal booleans and supported AND/OR three-valued predicates; it does not infer results from repeated queries or evaluate string/numeric coercion. SQL definitions must be immutable and cannot escape to helpers.

DB-34 proves only a pure cache-hit return before actual execution on the miss path. SQL used in a key/audit, already executed, overwritten or set up without execution does not qualify. Construction identity uses the named function, declaration ordinal/binding and API anchors; execution checks use function/operation ordinal. Blank lines preserve fingerprints; adding an earlier semantic operation may create a new anchor. PDO prepare is never treated as execution.

## Hybrid evidence

DB-05 and DB-16 require telemetry in runtime mode even for a clean scan. `static_candidate` is a separate context: results and limitations explicitly state that execution is unverified. No numeric savings or request measurements are emitted, preventing double counting across DB-05/16. Both may cite the same exact event bundle; downstream measurement logic must allocate each event once if counts are later introduced.

Telemetry data is `query-event-v1` with `acquisition.status=complete`, timezone-qualified start/end and an events array. Each event requires event_id, request_id, transaction_id (use an explicit autocommit request identity where appropriate), repository_id, full commit_sha, SHA256 of the exact UTF-8 source, timestamp, function, operation_id (1-based execution ordinal), locator, exact SQL, status=success and dialect. DB-05 correlates both events in temporal order within the same request/transaction. Empty, malformed, mismatched or uncorrelated data cannot confirm a candidate. Timestamps are inside the acquisition's half-open window. Complete acquisition is a connector assertion; the detector does not independently trust an AWS-looking URI.

CloudWatch messages use `{"format":"query-event-v1","event":{...}}`. The real collector applies immutable snapshot filters, bounded polling, cancellation, time-window splitting/deduplication and explicit truncation failures. It does not alter client logging. Logs must already contain these fields; RDS SQL text alone cannot establish local consumption or a source callsite.

## Handlers and verification

STATIC accepts an input envelope or `{input_key,sha256}` for a protected `inputs/` object. LOG accepts `{input,log_window:{group,start,end}}` and acquires evidence from its configured group allowlist. Result pairs are evidence-validated in the deployed handler, stored immutably under content-addressed `results/` keys, and optionally published as `DetectorResultPointer.v1` to the actual Owner D bus. Missing hub produces `delivery.status=blocked`. Successful PutEvents is not proof of report persistence.

Limits: 1 MiB envelope, 20 file scopes, 40 sources, 256 KiB PHP/file, 8 KiB SQL, <=1-hour LOG window, <=15 split queries/5,000 events, <=45-second overall acquisition budget. The Lambda runtime also bounds execution. No customer code is imported, executed or benchmarked; only auditor code and synthetic fixtures are run.

Run `npm ci`, create `.venv-g01` with the shared contract requirements, then `npm run lint`, `npm run typecheck`, `npm test`, and the Python contract suite. CI already runs these commands and retains other owners' contract checks. Tests compare results with the Python reference and reject fabricated citations, changed scope/version comparisons and always-empty implementations. Six committed synthetic input/result pairs per issue plus boundary tests are under test/fixtures/g01 and test/g01.test.js. These fixtures prove behavior, not customer-data acquisition.

See checks/*/SPEC.md for the seven detection fields and local verification-plan comments. See infra/owner-b and docs/verification/owner-b for actual deployment receipts and remaining acceptance gates.
