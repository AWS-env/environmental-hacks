# Owner A - decisions log

| # | Decision | Why | Status |
| --- | --- | --- | --- |
| D1 | Drop bucket E (C6.5, C8.1, C8.3, C7.2, C7.4, C10.6, C12.1-C12.3, C12.4); record each drop with its reason | Cannot be detected honestly (no tool, no static signal, hardware counters unavailable on AWS VMs, N/A for Python) | Owner said "drop E" |
| D2 | Bucket F (judgement: C7.1, C7.3, C7.5, C3.4): keep only narrow structural patterns, drop the rest | Static complexity is only partly detectable | Owner said "drop e f"; narrow-keep is my suggestion, to confirm |
| D3 | Bucket C (style-only checks): keep, as informational / low severity | Owner answered "keep" | Confirmed |
| D4 | Runtime checks (bucket D): client CI uploads profiler artifacts (memray/cProfile) to S3; we only parse | OQ-1; never execute client code | Confirmed ("they will do this") |
| D5 | Engine for new rules: TypeScript + tree-sitter (extend the existing 7 detectors) | One codebase + contract; arm64 grammar builds on Lambda still to be verified in P3 | Confirmed ("your pick") |
| D6 | Fix existing detectors first (#303, #290, C3.3 severity) | Known defects in built checks | Confirmed ("fix it") |
| D7 | Move C9 to "needs discussion" | Its detection signals are runtime-based | Confirmed ("ok") |
| D8 | AWS-first = use AWS resources wherever the hackathon requires or they genuinely fit; not decoration | Hackathon requirement | Confirmed |

| D9 | Gate 2 signed off. Rows marked "(proposed)" in `owner-a-research.md` are revisited at implementation (P4) | Owner chose to revisit at implementation | Confirmed |

| D10 | One `owner-a-static-scan` Lambda incl. complexity heuristics (no separate complexity Lambda) | Fewer Lambdas, quota limit of 10 concurrent | Confirmed |
| D11 | Own `owner-a-presign` + `owner-a-profile-parser` (no reuse of other owners' artifact path) | Keeps the wall | Confirmed |
| D12 | v1 publishes inline `detector.result.v1` events only; ask owner D for pointer allow-list later if needed | 256 KB event limit accepted for now | Confirmed |
| D13 | Deploy the skeleton after template validation; invoke every Lambda once; explain writes first | P3 gate | Confirmed |

| D14 | Smoke rows removed from the shared `owner-d-findings` table (5 items, repo `github:owner-a-smoke/skeleton`) | Owner asked; listed first, all items belonged to the smoke repo only | Done, 0 remaining |
| D15 | #303 and #290 need no change: already fixed on origin/main (`node.id` comparisons, no object-identity left; owner-a typecheck+tests in CI `build`) | Verified by grep audit + C3.2 test file run 15x, 0 failures | Done |
| D16 | C3.3: `re.compile` -> new signal `cached-compile`, severity low / confidence medium / tier light; `regex.compile`, `compile`, jinja2, XML stay medium/high/heavy | Python `re` docs: recent patterns are cached; `regex` docs say nothing about caching (unverified) | Done, tests added |
| D17 | SRC-01 citation title corrected in all 17 owner-a files (was "An Empirical Study ... Python") | arXiv title is "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells" | Done |

| D18 | Batch 1 implemented (code-reading checks no existing tool covers): C5.1, C10.4, C6.7, C6.6, C11.4 (built by one agent each, reviewed and re-verified by me) | Bucket B/A from `owner-a-research.md`; "(proposed)" verdicts confirmed by real-repo evidence for these five | Done |
| D19 | C5.1 skips loops with a statically small iterable (<= 8 literal elements, `range(<=8)`, `name.items()` of a small dict literal, self-derived filtering comprehensions) | Real-repo hand-check: 5 of 6 first-pass hits were tiny loops | Done |
| D20 | C10.4 skips the finding when the accumulator is read elsewhere in the same loop body (instead of lowering confidence) | Real-repo hand-check: `display += ...; live.update(display)` already consumes the string every iteration, `join` is not a drop-in | Done |
| D21 | `owner-a-static-scan` event accepts `dry_run: true` (evaluate and summarise, never publish) | Live-test new checks without writing more rows into owner D's shared table | Done, deployed |
| D22 | Test and example paths are a connector scope decision, not detector logic | C6.6/C11.4 hits are technically real but 12/15 and 2/4 sit in tests/examples; 48 of 106 C3.2 hits are in tests | OPEN: owner to confirm default exclusions |
| D23 | OPEN DEFECT in existing C3.2 (owned by earlier work): flags expressions on `raise`/`return`/`yield` lines (run at most once) and values depending on state mutated by an unknown call in the same loop (e.g. `reset_mocks()`) | Hand-check of aiohttp sample; 4 of 106 hits on exit lines, 45% in tests | FIXED 2026-10-10: expressions in `return`, or in a `raise` no loop-internal `try` can catch, are skipped; a call finding drops to confidence low when the loop also makes a zero-argument bare statement call (`reset_mocks()`, `await refresh()`) because it can only act through closure/global state. First draft flagged every bare statement call as opaque; the test showed that downgrades nearly every loop, so it was narrowed to zero-argument calls. Real repos: 106 -> 105 hits, 29 medium -> low, remaining exit-keyword hits are `yield` (legitimately per-iteration). 304 tests pass. |
| D24 | Taxonomy reference URLs corrected to real issues (#75 C5.1, #45 C10.4, #51 C11.4; #84, #85 already right) | Agents had guessed; one copied the wrong template URL | Done |

| D25 | Real AWS runtime path = CODE-C6.1 artifact check: client CI uploads `memray stats --json`; `owner-a-profile-parser` evaluates it (allocation churn = total_bytes_allocated / peak_memory plus a client-code hotspot by allocation count) and publishes via the same contract path. Real artifacts generated with memray in a throwaway Linux container from our own demo scripts (`test/fixtures/code-c6-1/`). Key names (`total_num_allocations`, `total_bytes_allocated`, `top_allocations_by_count[{location,count}]`, `metadata.peak_memory`) now verified from a real export, closing the UNVERIFIED from research. | Owner asked for one real AWS path; memray artifact was quicker than building an X-Ray demo workload | Done, live-tested |
| D26 | X-Ray reader (C11.5) and the other artifact parsers (C6.4, C4.6, C9.x confirmation) are NOT built | Time/scope: one real runtime path is enough for the demo | Deferred |

## Verification log
- Gate 1: see `gate1-summary.md` (SRC-01 title/numbers, `re` cache note, Free plan state verified by fetch/CLI).
- Gate 3 (2026-10-10, profile `shrey`, ap-south-1), worktree `<local path>` on branch `wip/owner-a-aws-skeleton` (local, not pushed):
  - Existing owner-a suite 134 tests pass on origin/main (dadddf3); with the new handler tests 149 pass (contract validator required).
  - Template `cdk/owner-a/owner-a-detectors.yaml` passed `validate-template`; stack `owner-a-detectors` created (14 resources); deploy bucket `owner-a-deploy-<account-id>-ap-south-1`.
  - Mistake caught: first uploaded zip was built before `artifact-handler.ts` existed; rebuilt, deleted the stale zip, deployed the rebuilt one `owner-a-static-scan-ef155ed93dc644f4.zip`.
  - Live invocations: `owner-a-static-scan` (CODE-C1.1 smoke input -> completed, 3 findings, published; tree-sitter + tree-sitter-python native arm64 prebuilds load on nodejs22.x), `owner-a-presign` (regional host `<bucket>.s3.ap-south-1.amazonaws.com`, PUT 200, no 307), `owner-a-profile-parser` (S3 trigger fired, published `unavailable` result for CODE-C6.1).
  - Persistence proven by read-only DynamoDB query on `owner-d-findings` (gsi `by-repository`, repo `github:owner-a-smoke/skeleton`): both results stored; `evidence` level = `unverified` (inline events carry no input, so citations are not checked).
  - Drift check: Lambda `CodeSha256` of both functions equals SHA-256 of the local zip. DLQ depth 0, alarm `owner-a-dlq-not-empty` = OK.
  - Smoke rows now exist in the shared findings table (repo `github:owner-a-smoke/skeleton`); they are clearly named but not deleted.

- P4/P5 batch 1 (2026-10-10):
  - Suite: 298 tests pass with the contract validator required; `tsc` clean (was 134 on origin/main).
  - Mutation check (`shrey/.agents/rules/tools/mutate_a.cjs`, run): every check's own tests kill both always-empty and always-flag mutants for C5.1 (33 tests), C10.4 (38), C6.7 (25), C6.6 (24), C11.4 (25).
  - Real repos (`scan_repos_a.mjs`; psf/requests 611c616, pallets/flask d086db8, fastapi/fastapi f5c6e9b, aio-libs/aiohttp 92d2a43, Textualize/rich 9d8f9a3; 1,673 files, 0 detector errors): hand-checked every hit of the five new checks. Counts before -> after the two fixes: C5.1 6 -> 1 (the one left is a true positive, `requests/cookies.py:598`); C10.4 18 -> 10; C6.6 15 (all real unclosed handles, one real library leak `requests/utils.py:313`); C11.4 4 (all real, 2 in tests); C6.7 0.
  - Live AWS: Lambda redeployed (zip `owner-a-static-scan-bb8b1ee1831fa24a.zip`), `CodeSha256` of both functions equals the local zip (drift diff empty). `live_parity_a.py` invoked the deployed Lambda in dry_run for C5.1 (20), C10.4 (9), C6.7 (20), C6.6 (8), C11.4 (15), C3.3 (10): status and finding counts identical to the local run.
  - Smoke rows for `github:owner-a-smoke/skeleton` deleted from `owner-d-findings` (5 items) at the owner's request; no new rows were written in this batch.

- Runtime path (2026-10-10): 325+ tests pass (artifact check 19 + handler 10, validator-backed pair test included). Live on AWS: presign Lambda -> regional PUT (200) -> `owner-a-profile-parser` fired -> `findings-hub` -> owner D writer stored `CODE-C6.1` result `completed` (evidence level `unverified`, inline event) with the finding "score:wasteful.py:7 made 21000 allocations (87% of all 24111) ... 327x" at confidence medium; dead-letter queue 0. Demo rows (1 result + 2 findings) and both uploaded objects removed afterwards; 0 rows remain for the smoke repo. Final zip `owner-a-static-scan-a5fae9ae2ea05a78.zip`; `CodeSha256` of both Lambdas equals the local zip; stale zips deleted from the deploy bucket.

## Open points
- Inline events give evidence=`unverified`; `DetectorResultPointer.v1` (pair in our bucket, allow-listed by owner D) would give `verified`. Revisit D12.
- Profile parser is a skeleton (hash + `unavailable`); real memray/cProfile export parsers come with their checks.
- X-Ray telemetry reader Lambda not built yet (planned for C11.5/C8.4/C9.3).
- `npm audit` reports 3 vulnerabilities (2 critical) in the owner-a dev tree; not triaged.
- Gate 3 MCQ with owner pending.
