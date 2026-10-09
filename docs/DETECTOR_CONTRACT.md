# Shared Detector Contract

Contract v1 is the common boundary for every owner's detector. JSON Schema is
the language-neutral source of truth; Python is a reference validator, not a
requirement that all detectors or the application use Python. This contract is
proposed for team review in issue #277. Adoption requires reviewing and merging
the contract PR; it does not retroactively certify existing detectors.

## Files and Quick Start

- Schema: [`shared/contracts/detector.schema.json`](../shared/contracts/detector.schema.json)
- Reference validation and comparison: [`validation.py`](../shared/contracts/validation.py)
- Example input/result pairs: [`examples/`](../shared/contracts/examples/)
- Contract regression cases: [`tests/contracts/`](../tests/contracts/)

From the repository root, using Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r shared/contracts/requirements.txt
.venv/bin/python -m shared.contracts.verify
.venv/bin/python -m shared.contracts.validation shared/contracts/examples/obs01-detected.json --input shared/contracts/examples/obs01-input.json
```

CI's existing `build` job always runs the contract suite, even without a Node
application manifest. Zero discovered contract tests is a failure. This suite
verifies the contract; each owner's detector still needs its own behavioral
tests and integration into CI as its runtime is introduced.

Repository settings must require `build` and `meta` on `main` and an independent
review before merge. Adding CI steps does not configure those settings. At the
time of issue #277, GitHub returned no classic protection or effective branch
rules for `main`; enforcement remains a maintainer setup task.

## Pipeline

```text
Connector supplies files / existing telemetry / client-produced artifacts
  -> validate input
  -> detector evaluates the declared check and scope
  -> validate result AND its citations against that input
  -> store accepted findings and measurement provenance
  -> compare compatible scans / pass measurements to estimation logic
```

Use `validate_pair(input_payload, result_payload)` at the ingestion boundary.
Standalone `validate(payload)` checks shape and invariants but cannot establish
that cited evidence exists. Reject invalid output before storage; record a
separate orchestration error and do not count it as a clean detector run.

Neither validation nor comparison executes the connected project's code.
The orchestrator is responsible for bounded input sizes, timeouts and access.

## Input

Every payload includes `schema_version`, `kind`, `repository_id`, `scan_id`,
`commit_sha`, `check_id`, `detector_version`, `context`, and `scope`.

| Field | Meaning |
| --- | --- |
| `schema_version` | Exactly `1.0`; incompatible shapes require a new version |
| `repository_id` | Stable connector-qualified repository identity |
| `commit_sha` | Full lowercase Git SHA, not a branch name or abbreviated SHA |
| `check_id` | Existing key in `docs/taxonomy/checks.json` |
| `detector_version` | Version of this check's detection semantics |
| `context` | Every evaluation-affecting setting: environment, thresholds, exclusions, language support, and model/prompt versions if used |
| `scope` | Explicit nonempty set of requested scope IDs, e.g. `file:config/production.yaml` |
| `sources` | Supplied evidence, possibly empty when requested evidence is unavailable |

A source has a unique `source_id`, a requested `scope_id`, a `locator`, and a kind:

- `static`: `content` is source/configuration text. Never import or execute it.
- `telemetry`: `data` is a normalized object of existing readings.
- `artifact`: `data` is a normalized object from a client-produced artifact.

Structured evidence cites a top-level `data` field. Nested values can be stored
under that field; the cited value must equal it in full. Connectors normalize
raw provider data before supplying it. Do not put credentials in context or
payloads. Source collection and provider-specific formats are outside v1.

## Result and Coverage

Result identity/context/scope must match the input exactly. Each result includes
`status`, `coverage`, `findings`, and `measurements`; use empty arrays when there
are no findings or supported measurements. A result represents one check.

| Status | Required meaning |
| --- | --- |
| `completed` | All requested scope was evaluated under this check's declared evidence requirements |
| `partial` | A nonempty proper subset of scope was evaluated; explain omissions |
| `unavailable` | Required evidence or support was missing; nothing evaluated |
| `error` | Evaluation failed; nothing certified as evaluated |

`coverage.evaluated_scope` contains only successfully evaluated scope IDs.
`coverage.limitations` explains uncertainty and omissions; it is mandatory and
nonempty for incomplete results. Unavailable/error results contain no findings
or measurements. Findings may appear in partial results only for evaluated scope.

A missing environment, unsupported syntax, failed parse or missing telemetry
must never become `completed` with an empty findings array. If one scope item
was only partly evaluable, omit it from evaluated scope; split scopes more finely
when independent subunits need separate coverage.

A static-only check can complete without runtime metrics if that limitation is
explicit in its spec. It proves the static pattern, not runtime impact.

## Findings and Evidence

Each finding requires `scope_id`, `identity`, `fingerprint`, `summary`,
`confidence` (`low`, `medium`, or `high`), `recommendation`, nonempty technical
`references` (HTTP/HTTPS URLs), and nonempty `evidence`.

- Static evidence cites `source_id`, `kind`, `locator`, one-based `line_start`,
  and `value` containing the exact complete source line(s), without trailing newline.
- Telemetry/artifact evidence cites `source_id`, `kind`, `locator`, `field`, and
  the observed `value`. The value must match that supplied data field.
- Evidence sources must belong to the finding's scope. Evidence kind expresses
  where the observation came from; confidence describes the detection, not savings.

An LLM's raw prose is not a result. Require structured output, validate it and
verify citations against supplied input. Validation can reject invented lines
or values; it cannot prove that the reasoning or recommendation is correct.
Behavioral cases and independent review are still necessary.

The fingerprint is lowercase SHA-256 of the UTF-8 compact JSON array:

```text
[repository_id, check_id, scope_id, identity]
```

Encode Unicode directly, use standard JSON escaping, and include no whitespace.
`fingerprint()` provides the reference implementation and the OBS-01 example a
known test vector. `identity` is a detector-defined semantic anchor such as
`production-log-level` or a function-qualified setting. Specify this anchor in
the issue. Exclude line numbers, values, prose, scan IDs and commit IDs so line
movement does not create a new finding. Renames or changed semantic anchors may
produce a new identity; v1 does not infer continuity across renames.

## Measurements and Repository Metrics

Measurements belong to the detector result rather than individual findings.
One shared observation may support multiple findings; do not duplicate it for
each finding. Each measurement has:

- `metric`, nonnegative finite `value`, and the prescribed `unit`.
- `basis`: `measured`, `user_provided`, or `modelled`.
- `boundary`: the service, workload or other allocation boundary.
- `allocation_key`: a shared identity for the underlying observation/quantity,
  not a detector-specific ID.
- `window.start` and `window.end`: timezone-qualified timestamps, start before end.
- `provenance.source_ids`, a named/versioned `method`, and explicit `assumptions`.

| Metric | Unit |
| --- | --- |
| `energy` | `kWh` |
| `emissions` | `kgCO2e` |
| `cpu_time` | `seconds` |
| `data_transfer`, `log_volume` | `bytes` |
| `requests`, `tokens` | `count` |

Do not invent environmental values from a finding count. Unsupported metrics
remain absent. Modelled values must name their methodology/version, factors
and assumptions; acceptance of the schema does not validate the model.

An aggregator must reconcile allocation keys across checks, reject conflicting
observations and avoid overlapping boundaries/time windows before summing.
Different allocation keys alone do not prove independence. Keep measured,
user-provided and modelled values distinct; compare only compatible workloads,
boundaries, windows and methods. A repository report must also expose checks
that were partial, unavailable or failed, instead of describing them as passing.
This change defines inputs for that work; it does not implement an estimator or
certify sums, causal attribution, cost savings, or carbon savings.

## Comparison and Resolution

Compare only evidence-validated results stored by the orchestrator. The caller
selects chronological before/after scans; commit hashes do not establish order.

`compare(previous, current)` requires the same repository and check. A finding
can become `no_longer_detected` only when both evaluations completed, detector
and schema versions match, context is unchanged, and requested scopes match.
Scope order is immaterial for comparison. Compatible fingerprints present in
both results are `persisting`; a previously absent fingerprint is `new` only
under comparable complete coverage. Other disappearances are `unknown`.

Changing detector/model versions, narrowing scan scope, losing access or failing
a scan cannot establish a fix. File deletion/rename needs explicit lifecycle
handling later; v1 conservatively treats changed scope as incomparable.
`no_longer_detected` is not a claim of measured environmental improvement.

## Verification for Every Issue

Before implementation, add a **Verification plan** comment to the existing
taxonomy issue using [`VERIFICATION_PLAN.md`](VERIFICATION_PLAN.md). Generated
issue bodies may be replaced by taxonomy sync, so keep owner plans in comments
and durable fixtures/tests in the PR.

Specify supported inputs, evidence requirements, semantic identity and expected
outputs. Select cases relevant to the check: positive, similar negative,
legitimate exception, missing evidence, malformed input, and a boundary case.
Give each case an ID and map it to a committed fixture and executable test.

Tests must assert findings AND evidence/coverage, not merely non-crashing code.
At least one positive and one negative case should fail an always-empty or
always-flag implementation. The reviewer checks expectations against the issue,
runs tests on the latest PR revision and challenges at least one likely false
positive or unsupported conclusion. Missing required dependencies remain visible.

## References

- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
- [Python jsonschema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/)
