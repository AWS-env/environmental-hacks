# JOB-06: issue #184

1. **Detection signal:** Synchronous non-urgent service interaction plus observed bursts/backpressure and complete deployment inventory.
2. **Detection tool:** Deterministic AST/metadata/telemetry evidence extraction; HEURISTIC Lambda; separate human review. Implementation: `index.js`; shared adapters in `../../core/jobs.js` and `../../normalization/`.
3. **Telemetry needed:** Source callsite/digest, sender/receiver, complete interaction inventory, peak/mean arrivals, downstream/backpressure spans, eventual-completion semantics. Existing client evidence only; no execution or instrumentation of customer code.
4. **Report output field:** shared-contract `findings` with semantic identity/fingerprint, exact citations and recommendations; `coverage` and explicit completed/partial/unavailable outcomes. `measurements` remains empty because supported CPU/environmental measurements are absent.
5. **False-positive risk:** Low-volume apps, existing external queues and transactional immediate responses do not need an automatic queue; OQ-8 remains a review requirement.
6. **Detectable (H/M/L):** L/M; restricted to the documented supported model.
7. **Measurable (H/M/L):** M for observed workload; L for projected benefit. Never convert latency/occupancy/work units to measured CPU.

## Verification plan

Check ID: JOB-06. Required formats, field definitions, context and supported limitations: [JOBS.md](../../JOBS.md). Every runtime source carries a complete bounded acquisition window and matching repository/commit; source-based metadata also matches the exact content digest. These are evidence-provider assertions, not truth supplied by an LLM.

| Case | Condition | Expected result | Test reference |
| --- | --- | --- | --- |
| JOB-06-01 | Supported synthetic positive with exact observations/policy | One finding, completed coverage, exact evidence validated | `test/job-06.test.js` |
| JOB-06-02 | Similar workload/configuration without the condition | Completed coverage, no finding | same |
| JOB-06-03 | Legitimate policy/business exception | Completed coverage, no finding | same |
| JOB-06-04 | Required source/metadata unavailable | Unavailable, no evaluated scope or finding | same |
| JOB-06-05 | Malformed/unsupported evidence | Unavailable with reason; no clean scan claim | same |
| JOB-06-06 | Scan/commit movement, tampered citations and failed follow-up | Stable fingerprint, deterministic output, invalid citations rejected, disappearance remains unknown | same |

Mixed independently evaluable/missing scopes additionally require partial coverage. Threshold/DST/identity and provider failure cases are in `job-edges.test.js` and `jobs-provider.test.js`. Expected evidence is exact full source lines or complete normalized data fields; input/result validation runs inside the deployed handler.

Validation: `npm run lint`, `npm run typecheck`, `npm test`, `python -m shared.contracts.verify`, plus deployed immutable-version invocations and pair readback. Synthetic fixtures are labelled; they are not real client observations. All concrete AWS identity/resource records stay private. Public receipts retain hashes/results and clean committed build provenance.

Required AWS paths: Deterministic AST/metadata/telemetry evidence extraction; HEURISTIC Lambda; separate human review. Deployed functions store immutable validated pairs and publish small pointers to the existing shared findings bus. Acceptance requires actual Owner D ingestion/report readback; EventBridge acceptance alone does not satisfy it. Client runtime evidence is additionally required where listed. JOB-06 requires separate human business confirmation, not an assistant approval. Missing dependencies remain explicit and must not be checked off as complete.

Reviewer challenge: supply the closest legitimate exception plus missing/partially sampled observations, and reproduce the positive/negative cases against the latest committed build. No reviewer confirmation has been claimed.
