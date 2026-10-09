# Category 2 (JS + CODE-RT, 11 checks) - decisions

Owner: C (Medhansh-741). Research: [`category-2-js.md`](category-2-js.md). Implementation and run
instructions: [`detectors/owner-c/README.md`](../../detectors/owner-c/README.md). Follows
[`category-1-decisions.md`](category-1-decisions.md) (D2, D4, D5, D10, D11, D13 carry over unchanged).

Checks: JS-01..09, CODE-RT.2, CODE-RT.6. **CODE-RT.4 (#100) and CODE-RT.5 (#101) are deliberately deferred.**

## Decided

| # | Decision | Why |
| --- | --- | --- |
| D1 | JS/TS is parsed with tree-sitter (Python bindings, JS/TS/TSX grammars); wheels are bundled for the Lambda (python3.13, arm64). Source is only parsed, never executed. | One engine for the owner-c package; needs no Node on the Lambda. |
| D2 | JS-02, 03, 04, 05, 07, 08 are reported only when a client-produced V8 profile confirms them (`node --no-opt --cpu-prof`; heap via the shipped `collectors/collect-heap.js`). Files without a profile stay out of coverage (`partial`/`unavailable`). | Same rule as D6 of Category 1. `--no-opt` is required because V8 inlining hides small functions from the profile. |
| D3 | The heap collector is a shipped script because V8's default heap profile omits freed objects, which hides clone/copy churn. | Found while capturing real heap profiles. |
| D4 | JS-01 is a static candidate confirmed by AWS X-Ray telemetry (runs of same-named sibling subsegments with no overlap), read-only from the client's account. | A CPU profile cannot show waiting. Static candidates already drop retry/backoff/sleep loops, `for await`, early exits and dependent results, following ESLint's documented legitimate cases. |
| D5 | JS-06 and CODE-RT.2 are static candidates (low/medium confidence, documented limits). | No runtime signal is cheap and reliable: a leak is not visible in a short profile. |
| D6 | JS-09 is static. | Real V8 CPU and heap profiles charge stack-trace capture and GC to native frames, so they cannot attribute exception cost to the throwing function. |
| D7 | CODE-RT.6 compares declared versions (`engines`, `.nvmrc`, workflow `node-version`, runtimes, Dockerfiles, ...) with a dated table (`config/runtime_support.json`) at a declared `reference_date`. Lambda dates are used for Lambda config, upstream dates elsewhere. | Lambda deprecation differs from upstream EOL by months; the table records its retrieval date and sources. |
| D8 | Real traces and profiles are captured, not invented (`tests/fixtures/real/node`, `tests/fixtures/real/xray` from the deployed demo Lambda, account id scrubbed). | Same as D3 of Category 1. |
| D9 | AWS: the same stack `owner-c-python-detectors` (ap-south-1) gains `owner-c-presign` (15-minute PUT URLs), `owner-c-xray-parser`, `owner-c-xray-demo` (Active tracing), an S3 `manifest.json` trigger (upload manifest last) and a DLQ. | Event requirement; keeps one deployable unit per owner. |
| D10 | PRs: 11 stacked, one per issue, CODE-RT.6 (#102) first with the shared foundation. Order: #102, #99, #190, #186, #188, #192, #187, #189, #191, #193, #185. | One issue per PR; the foundation must land once. |

| D11 | Where our detection method differs from the taxonomy's (JS-01, JS-04, JS-06, JS-08, JS-09), the difference is recorded in the research file with the reason. | The taxonomy rows say "Async profiler", "Memory profiler; heap snapshot" and "Call-count tracing"; we use X-Ray, V8 CPU/heap profiles and static candidates. OQ-1 (client-CI artifacts) is answered by D2. |

## Changes made because of the research and hand-checks (2026-10-09)

| # | Change | Evidence |
| --- | --- | --- |
| C1 | JS-04 sync-call list widened (realpath, mkdtemp, open/read/write/close, fstat, cp, truncate, symlink, link, chmod, chown, utimes, opendir). | Node's "Don't block the event loop" list is broader than our first list. Case JS-04-13. |
| C2 | JS-09 `throw-in-try` now requires a try body with no other call, await or `new`; `try-skip-in-loop` requires an explicit `continue`. | undici: 9 false positives (shared error handling; callback isolation). Cases JS-09-03 (flipped), -11, -12. |
| C3 | CODE-RT.6 table gains Python 3.15 (EOL 2031-10-31, month-end of the devguide's "2031-10"). | Python 3.15 released 2026-10-09. |
| C4 | Presign URLs now use the regional S3 endpoint. | Live test: the default client signed `bucket.s3.amazonaws.com`, which answered a PUT with 307. |

## Verification log (2026-10-09)

- 71 unit tests plus the committed verification cases; the shared contract suite passes (20 tests).
- Live AWS: demo traces -> `owner-c-xray-parser` (parallel window: JS-01 completed, 0 findings; serial window: 1 finding); presign -> PUT repo zip + cpuprofile + heapprofile + manifest last -> parser run triggered by S3 (6 JS checks, 1 finding each, published, partial coverage for the file with no profile).
- Old retained artifact bucket from before the rename emptied and deleted.
- 2026-10-10 follow-up: the deployed Lambda zip (v10) was found to be behind the PR code (4 of 49 files differed: JS-04, JS-09, cli, runtime table). Rebuilt as v11 and diffed against the top PR branch: 49 identical, 0 different.
- `owner-c-static-scan` invoked live for the first time with a mixed repo: CODE-RT.6, CODE-RT.2, JS-06 and JS-09 each found their positive; the undici-shaped JS-09 negative was not flagged.
- The `role_arn` path of `owner-c-xray-parser` (assume-role into `owner-c-xray-reader`) was tested against real STS with a temporary same-account role (deleted afterwards): JS-01 completed with 1 finding. A real second account was not available.
- Added an alarm on the dead-letter queue (`owner-c-parse-dlq-not-empty`, no notification target) and a tested client-side example (`examples/client-ci/upload-profiles.sh`); live run: fresh profiles -> JS-02/03/04/05/07/08 completed with 1 finding each.
- Real repos: see the hand-check table in the research file.

## Open

- Client CI example is a shell script, not a GitHub Actions workflow; it was run locally against the stack, not from a CI runner.
- The DLQ alarm has no notification target (SNS/email); someone must decide where alerts go.
- tree-sitter-javascript 0.25 rejects valid JS in some files (class fields without semicolons); reported as `partial`.
- For library repos, CODE-RT.6 flags EOL Node versions in test matrices that exist to check compatibility (factually true; owner to decide whether to downgrade matrix entries).
- No official documentation was reachable for SonarJS, Semgrep or CodeGuru; no parity claim is made.
- The taxonomy's cited sources were checked only through the workbook's `Sources` entries; SRC-01 (the JS root-cause paper, Python-validated) was not read in full.
- The sources cited for CODE-RT.6 (SRC-44, SRC-36) do not support an "outdated version = wasteful" claim; the check is a support signal.
- OQ-11 (X-Ray vs the AWS Free Plan eligible-services list) is unverified beyond the live test working.
