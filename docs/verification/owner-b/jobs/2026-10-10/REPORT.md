# Group 3 implementation and AWS verification

Prepared 2026-10-10 (Asia/Calcutta). All five checks are implemented on separate local issue branches. Deployment in **ap-south-1** is real: four immutable Lambda versions, a private encrypted/versioned artifact bucket, separate execution roles, operational logs and failure queues. Nothing was pushed, opened as a PR or closed.

The clean build at commit `f1f6255a8c2cfd87f22e4566500acb8f7c31b986` reproduces the exact deployed archive SHA256 `33f6245f5946c3c3d6bae51d817d7fd482e641ca7b39977a96442f129d5b79a1`. Source commit, lockfile and bundle hashes are in [receipts.json](receipts.json). Reproduce it with `npm ci`, `npm run build:owner-b`, and `python scripts/package-owner-b.py`. Later receipt-only commits do not change deployed code. Raw AWS identity/resource/provider records remain private and ignored; exported pairs and hashes are explicitly labelled.

| Issue | Check | Implemented behavior | Verification limit |
| --- | --- | --- | --- |
| #182 | JOB-04 | Supported schedule parsing, deterministic DST/rate expansion and constrained-pool collision candidates | Real Scheduler inventory was empty; a load spike is not proven |
| #181 | JOB-03 | Complete aligned worker occupancy versus reviewed required reserve | Real metric acquisition returned no samples; runtime waste remains unavailable |
| #183 | JOB-05 | Repeated successful full work on unchanged complete inputs, with exceptions | Real log query found no client runs; runtime waste remains unavailable |
| #180 | JOB-01 | Bounded handler AST and reviewed optional heavy work before response; matching runtime spans when supplied | Source scans are candidates; real client span correlation remains unverified |
| #184 | JOB-06 | Reviewed interaction/source plus burst/backpressure evidence; conditional messaging advice | Real client spans, complete business semantics and separate human confirmation required |

**Passed:** Owner B 110 tests on the latest #313/#314 main foundation, Owner A 112, Owner C 44, shared contract 20, lint and types. The pre-publication revisions were independently validated; publication revisions are tested again after excluding the three pending DB registrations. The clean rebased build reproduces the identical deployed archive. Fifteen labelled synthetic positive/negative/unavailable cases passed on actual Lambda version 1. Inputs were uploaded to S3 with exact checksums, outputs read back, byte hashes and expected/local results compared, and all 15 pairs validated by both Node and Python. Three real provider acquisition cases were pair-validated and read back separately.

**Hub acceptance passed:** Owner D deployed its allowlisted same-Region writer. All 18 existing reports were republished, queried from DynamoDB and reconstructed through the hub readback function; stored JSON, hashes and findings matched. Event acceptance alone was not used as proof.

**Still blocking full runtime acceptance:** existing client job logs, complete worker occupancy metrics and correlated request captures are missing. JOB-06 also needs Owner D's separately stored human business review (OQ-8). No production finding, projected CPU/energy saving, reviewer approval or issue closure has been fabricated.

The branches form one linear dependency chain on the merged #313 scanner foundation: #182 → #181 → #183 → #180 → #184. Every increment retains previous registry entries and has its own check/spec/tests and safe evidence pairs; the shared foundation is inherited once. Rebase the next dependency after each upstream squash merge. Commit messages omit assistant co-author trailers.

The new resources remain available for missing-evidence/hub validation. Logs and failure queues retain seven days; inputs seven days; results thirty days. Retained buckets/code need deliberate cleanup. Historical eu-north-1 resources were not moved or deleted.
