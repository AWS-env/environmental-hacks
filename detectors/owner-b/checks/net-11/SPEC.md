# NET-11 / #223: Duplicate or serial client fetching

1. Detection signal: Compare request/body/auth/cache identities within one browser interaction and separate retries, redirects, preflights and polling. Serial findings require reviewed dependency metadata; timing alone never proves independence.
2. Detection tool: deterministic net-11 analyzer, network-evidence-v1 normalization and deployed owner-b-trace-analyzer / owner-b-profile-parser. Source and artifact inputs are never executed.
3. Telemetry needed: complete snapshot-correlated acquisition, capture tool/version/origin, route and browser. Required field formats are defined and validated in index.js; committed fixtures show exact supported shapes.
4. Recommendation: use only the tested/evidenced alternative with policy, semantic and interoperability constraints preserved. Architecture decisions require independent human review.
5. Confidence: medium for bounded supplied observations (NET-06 is low/review-required); detectability is limited to declared formats. Observations remain evidence; no fabricated CPU, energy, emissions or projected savings.
6. False positives: the committed similar negative, legitimate exception and boundary suppress findings. Missing/unsupported evidence produces unavailable rather than a passing runtime scan.
7. Limitations: connectors assert source provenance and policy semantics; synthetic fixtures prove algorithms/plumbing only. Real client evidence and independent review are distinct acceptance gates. Semantic identity excludes line numbers, values, thresholds, scans and commits.

## Verification plan

Cases NET-11-01 through -06 are committed under test/fixtures/network/net-11-*. Positive: one finding with exact field/source citations. Similar negative and legitimate exception: completed, zero findings. Missing and malformed evidence: unavailable, no certified coverage. Boundary: completed, zero findings. test/network-contract.test.js reproduces all fixtures, validates each pair with the Python reference, checks fabricated citation rejection and prohibits claiming an unavailable disappearance fixed.

Context includes rule_version=network-1, mode and all materiality/sample/objective/noise thresholds. Run npm run lint, npm run typecheck, npm test and python -m shared.contracts.verify. Client-runtime acceptance, real AWS transport/acquisition and persisted hub readback must be recorded separately. Unit success is not reviewer approval.

## Taxonomy owner fields

- Detection signal: the bounded rule above, corroborated only by its declared capture requirements.
- Detection tool: the check module and shared network adapters; actual deployed family and immutable version are recorded in verification receipts.
- Telemetry needed: the field-level requirements above; missing provenance, snapshot identity or capture semantics is unavailable.
- Report field: contract-v1 findings with scope_id, semantic identity, fingerprint, confidence, recommendation, references and exact evidence; status/coverage exposes incomplete checks.
- False positive risk: the legitimate exceptions and boundary cases above; connector policy assertions require independent review.
- Detectable: medium for supported complete evidence; architectural superiority for NET-06 remains low and review-required.
- Measurable: supplied observed quantities are cited as evidence, without inferred environmental or remediation savings.
