# NET-01 / #213: Chatty I/O

1. Detection signal: at least context.min_calls independent outbound calls for one correlated request, destination, operation and authorization identity; every payload is <= context.max_small_bytes and recorded setup work is positive. A documented batching alternative must exist.
2. Detection tool: deterministic trace analyzer; AWS X-Ray GetTraceSummaries/BatchGetTraces and deployed owner-b-trace-analyzer. Normalized network-evidence-v1 spans and batch_policy are cited exactly.
3. Telemetry needed: complete parent topology, request correlation, timestamps in milliseconds, payload bytes, setup_ms, dependencies, authorization identity and supported batch API reference. Segment metadata.owner_b supplies application semantics; missing ordinary X-Ray metadata is unavailable.
4. Recommendation: use the evidenced bounded batch API with authorization and failure semantics preserved; do not infer API support or HTTP/2 savings.
5. Confidence: medium for supplied capture; no total workload extrapolation or environmental estimates.
6. False positives: streaming protocols, dependent operations, separate users/tenants, large useful payloads, sampled incomplete trees, existing batches. Required streaming or any dependencies suppress the candidate.
7. Limitations: connector metadata is asserted; client production traces remain required for runtime confirmation. Identity is route:destination:operation:batch, independent of line/scan/count. No customer code executes.

## Verification plan

Fixtures test six cases: -01 independent small calls (one finding); -02 one batch (clean); -03 streaming required (clean); -04 missing spans (unavailable); -05 cyclic tree (unavailable); -06 below min_calls (clean). All use explicit synthetic provenance. Executable tests: test/net-01.test.js and test/network-contract.test.js, fixtures/network/net-01-*. Additional tenant/dependency cases challenge false positives. Context includes mode, rule_version, min_calls and max_small_bytes.

Run npm run lint, npm run typecheck, npm test and python -m shared.contracts.verify. AWS transport, actual acquisition and exact hub readback are separate acceptance evidence; test success is not independent reviewer approval.

## Taxonomy owner fields

- Detection signal: the bounded rule above, corroborated only by its declared capture requirements.
- Detection tool: the check module and shared network adapters; actual deployed family and immutable version are recorded in verification receipts.
- Telemetry needed: the field-level requirements above; missing provenance, snapshot identity or capture semantics is unavailable.
- Report field: contract-v1 findings with scope_id, semantic identity, fingerprint, confidence, recommendation, references and exact evidence; status/coverage exposes incomplete checks.
- False positive risk: the legitimate exceptions and boundary cases above; connector policy assertions require independent review.
- Detectable: medium for supported complete evidence; architectural superiority for NET-06 remains low and review-required.
- Measurable: supplied observed quantities are cited as evidence, without inferred environmental or remediation savings.
