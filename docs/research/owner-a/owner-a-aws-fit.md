# Owner A - AWS fit (Gate 3 design, DRAFT for discussion; nothing deployed)

Project `shrey`, account <account-id>, Region `ap-south-1`, plan FREE (120 USD credits to 2027-04-08), Lambda concurrency limit 10.
Names `owner-a-*`, tag `owner=A`. `docs/ARCHITECTURE_FLOWS.md` still says eu-north-1: stale, ignore (use ap-south-1).

## What already exists (read-only inventory + origin/main)
- Bus `findings-hub`; rule `owner-d-findings-ingest` matches `source` prefix `owner-` and detail-type `detector.result.v1` or `DetectorResultPointer.v1`; writer Lambda validates contract v1 and stores to DynamoDB; failures -> DLQ. Pointer events may only reference `results/` objects in **allow-listed buckets** (stack parameter `PointerBuckets`, owned by owner D).
- `detectors/owner-a` (TypeScript, tree-sitter, arm64-friendly): 8 registered checks (C1.1, C1.3, C3.1, C3.2, C3.3, C3.5, C3.6, C3.7) and `evaluate(input)` for contract v1. No Lambda wrapper, no CFN, no build script yet (#290 covers contract + CI).
- No owner-a stack, Lambda, bucket, queue or role in the account.

## Fit per bucket (what carries the evidence)
| Evidence | Checks (after D1/D2) | AWS piece |
|---|---|---|
| Static scan of repo source | C1.x, C2.x, C3.x, C4.x, C5.x, C6.2/3/4/6/7, C7.1/3/5, C8.2, C9.x, C10.x, C11.1-4 | Lambda `owner-a-static-scan` (Node 22, arm64, tree-sitter) |
| Client-CI artifact (memray/cProfile text/JSON exports) | C6.1, C6.4, C4.6, C9.x confirmation, C11.x confirmation | S3 `owner-a-artifacts-<acct>-ap-south-1` (private, SSE, TLS-only, lifecycle) + Lambda `owner-a-presign` + Lambda `owner-a-profile-parser` (manifest/S3 event trigger) |
| Existing telemetry | C11.5, C8.4, C9.3 (repeated/sequential downstream calls) | Lambda `owner-a-telemetry-reader` (X-Ray `BatchGetTraces`, read-only role; assume-role to client; demo simulated in this project) |
| Result transport | all | `PutEvents` to `findings-hub` (source `owner-a.<lambda>`, detail-type `detector.result.v1`); large results via `results/` in our bucket + `DetectorResultPointer.v1` (needs owner D allow-list) |
| Reliability | all | one DLQ with CloudWatch alarm, 7-day log groups, SQS in front of the parser to respect the 10-concurrency limit |

The old workbook plan also named `owner-a-complexity-scan` and `owner-a-mem-parser`. Proposal: fold complexity heuristics into `static-scan` (D2 narrowed them) and use one `profile-parser` for memory and CPU exports. Fewer Lambdas = less quota pressure.

## Skeleton to prove first (P3 gate)
1. Build zip with the **linux-arm64** tree-sitter + tree-sitter-python prebuilds (core 0.25.1 arm64 prebuild verified by agent; the Python grammar arm64 build is UNVERIFIED -> this is the main risk).
2. CFN `cdk/owner-a/owner-a-detectors.yaml`, `ValidateTemplate` (free), then deploy.
3. Invoke every Lambda once with a real contract input (smoke repo name clearly marked), publish to `findings-hub`, confirm the owner-d writer stores it (read-only query of `owner-d-findings`).
4. Regional presign (`<bucket>.s3.ap-south-1.amazonaws.com`) PUT of a sample artifact -> parser fires -> result published.
5. Diff the deployed zip against the code before any report (drift check).
Gotchas: `DescribeEventBus` before `PutEvents` (missing bus silently drops); IAM role names global per account; presigned URLs are credentials (never print); the account is shared with other owners (names/tags keep us apart; do not touch their resources).

## Writes this needs (explained before I do any)
Create: 1 CFN stack, 3-4 Lambdas, 1 S3 bucket, 1 SQS DLQ + alarm, IAM roles, log groups, uploaded zips in an `owner-a` deploy bucket. All small, inside Free Plan. Cleanup question at the end: keep or delete.

## Open questions for the owner
1. One static-scan Lambda (incl. complexity heuristics) or keep the workbook's separate complexity Lambda?
2. Artifact path: build our own `owner-a-presign` + parser, or reuse another owner's presign/profile parser? (Reuse would couple us to their stack; own path keeps the wall.)
3. Large results: ask owner D to allow-list our results bucket (cross-owner request), or v1 publishes inline `detector.result.v1` events only (256 KB EventBridge limit)?
4. Go-ahead to deploy the skeleton after template validation?
