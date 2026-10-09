# Local-pair import and hub readback verification

Verified 2026-10-10 (Asia/Calcutta) against the approved ap-south-1 hub.
The writer/readback foundation landed in #347; the import increment is #351,
linked to issue #350. No infrastructure deployment was needed for this command.

With the user's own local profile and explicit read/write approval, the actual
`python -m findings_hub.import --pair <transfer.json>` command stored 14
sanitized input/result pairs from the Owner B DB verification receipts:

| Check | Imported and read back |
| --- | --- |
| DB-34 | 3 |
| DB-06 | 3 |
| DB-05 | 4 |
| DB-16 | 4 |

Each transfer passed shared pair, checksum and independent identity validation.
The actual `python -m findings_hub.readback --repository-id <repo> --scan-id <scan>`
command returned a matching stored result for every pair. Result hashes, status,
commit identity, finding counts and fingerprints matched the supplied pairs.
All stored results carried `evidence=verified` and `source=owner-b.detectors`.
Repeating one import returned `duplicate`. No cross-Region S3 or EventBridge
calls were made. Local imports used `canonical_local_pair_sha256`; they do not
claim byte equality with the original S3 artifacts.

Separately, 18 existing ap-south-1 Owner B jobs test reports were republished via
same-Region EventBridge pointers. Actual consistent DynamoDB queries returned
all 18 committed reports; the hub readback function reconstructed them from
those responses. Stored result JSON, canonical hashes, artifact checksums and
finding fingerprints matched the original verified outputs. These tests remain
synthetic or explicitly limited auditor-provider acquisition cases, not proof
of real client workload waste.

Raw AWS identities, resource names and provider receipts are private ignored
files. This report contains no credentials or concrete deployment metadata.
The supplied-pair integrity checks do not authenticate authorship. Runtime-only
DB checks and jobs checks retain their documented client-evidence requirements.

Initial full local validation at base `f8df650` passed: hub 25 tests, shared
contracts 20, Owner D 53, Owner C 49, Owner A 112 with types, and Owner B 12
with lint/types.
The jobs development stack separately passed Owner B 112 tests with lint/types.

After rebasing on `90c3f25`, hub 25 and Owner C 49 tests passed locally; GitHub's
Linux CI build, metadata and labeling checks also passed. The newly merged
Owner D suite has four Windows failures among 145 tests: OBS-01 fixtures use
literal slash paths while Windows fixture preparation returns backslashes.
Owner D detector files are unchanged in this PR; no tests were skipped or
weakened to hide those platform failures.
