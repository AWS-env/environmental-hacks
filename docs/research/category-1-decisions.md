# Category 1 (Python, PY-01..11) - decisions

Owner: C (Medhansh-741). Research: [`category-1-python.md`](category-1-python.md). Implementation and run
instructions: [`detectors/owner-c/README.md`](../../detectors/owner-c/README.md).

## Decided

| # | Decision | Why |
| --- | --- | --- |
| D1 | Engine is Python's built-in `ast` module. | No new dependency; fits a small Lambda; fast to test on real repos. |
| D2 | Runtime evidence only via client-CI artifacts (py-spy speedscope JSON, memray stats JSON), uploaded by presigned PUT to S3 and parsed by a Lambda. We never run user code. | `docs/ARCHITECTURE_FLOWS.md` and the auditor spec. |
| D3 | Artifact tests use real captures recorded locally (py-spy on Windows; memray in a Linux container because it has no Windows wheel). | Avoid invented formats. |
| D4 | Code lives in `detectors/owner-c/owner_c/` (Python package, one module per check), IaC in `cdk/owner-c/`. | Matches the owner-namespaced layout in CODEOWNERS and owner D's detector. |
| D5 | AWS from day one in ap-south-1 (owner D's Region; moved from eu-north-1), CloudFormation YAML. Lambdas `owner-c-static-scan` and `owner-c-profile-parser`, private expiring S3 artifact bucket. `findings-hub` is owned by owner D; we only publish. | Event requirement; `docs/aws_service_mapping_v1.6.md`; AGENTS.md handshake. |
| D6 | PY-01, PY-05 and PY-11 are reported only when an artifact confirms the code pattern. Files without an artifact are left out of coverage (`partial`/`unavailable`), never reported clean. | Avoids overclaiming; matches contract v1 coverage rules. |
| D7 | PY-06 (`re.compile` in loops) is not implemented; issue #250 stays open. | Python caches recent patterns, so the impact is small. |
| D8 | Test on 5 popular Python repos (fastapi, pandas, flask, httpie, rich) plus a real profiled `rich` run. | Real-world noise check. |
| D9 | Parser overlap with owner A is ignored for now; sort out at merge. | Owner C's choice. |
| D10 | Detectors implement **shared detector contract v1** (`docs/DETECTOR_CONTRACT.md`): one result per check, `file:<path>` scope, semantic identity + SHA-256 fingerprint, evidence quoting exact source lines or one artifact data field, explicit coverage. | Contract merged on `main` (#277) while this was being built. |
| D11 | Test files (`tests/`, `test_*.py`, `conftest.py`) are out of scope by default, declared in `context.exclude_tests`; the connector makes that choice, not the detector. | Test-suite waste belongs to the TST checks (owner D). |
| D12 | PRs: one per issue, stacked, raised together; a Verification plan comment is posted on each issue first. | Repo rule: one issue per PR; contract requires the plan before implementation. |
| D13 | One EventBridge event per contract result, `detail-type: detector.result.v1`, `Detail` = the result payload. | The contract defines results but not the bus event shape; owner D's hub consumes it. |

## Build groups

- **Static (`ast`):** PY-09 (#253), PY-04 (#248), PY-02 (#246), PY-03 (#247), PY-08 (#252), PY-07 (#251), PY-10 (#254).
- **Artifact-confirmed:** PY-01 (#245, py-spy), PY-05 (#249, memray), PY-11 (#255, memray).
- **Not implemented:** PY-06 (#250).

## Verification log (2026-10-09)

- 41 unit tests plus 112 committed verification cases (`tests/fixtures/py_nn/cases.json`); every result is validated with `shared.contracts.validation.validate_pair`. The cases fail against always-empty and always-flag implementations.
- Real repos (static): 18 PY-02, 5 PY-03, 24 PY-04, 2 PY-08, 2 PY-09, 0 PY-07, 0 PY-10 across fastapi, pandas, flask, httpie and rich (test files excluded). Hand-checking real hits found and fixed false positives in PY-10, PY-02, PY-08, PY-01 and `noqa` handling.
- Live AWS (ap-south-1, account 768666229343, tagged owner=C): stack `owner-c-python-detectors`. Verified: static scan with test-file exclusion, a real publish of 7 contract results to `findings-hub`, and the S3 artifact flow (repo zip + py-spy capture -> PY-01 completed with 1 finding; PY-05/PY-11 `unavailable` without a memray artifact).
- AWS finding: `PutEvents` to a missing EventBridge bus returns success and drops the events, so the handlers call `DescribeEventBus` first.

## Open

- No `findings-hub` rule/consumer for `detector.result.v1` yet (owner D).
- Local `~/.aws/config` and several docs still say eu-north-1; the project Region in use is ap-south-1.
- The account has no budget alarms and a shared AdministratorAccess group (owner to decide).
- Artifacts come from locally recorded captures; no client CI collector exists yet.
