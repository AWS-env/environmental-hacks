# Detection Spec — CODE-C6.1 Unnecessary object (runtime half, memray artifact)

- **Taxonomy ID:** `CODE-C6.1`
- **Issue:** #79
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R2 (runtime artifact)
- **Status:** Implemented (artifact check; there is no static half)

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | The run allocated far more bytes in total than its peak (`total_bytes_allocated / metadata.peak_memory`, churn), and a location in the client's own code accounts for a large share of the allocation count |
| **Detection tool** | `memray stats --json` run by the client's CI; `owner-a-profile-parser` only reads the uploaded JSON, never executes client code |
| **Telemetry needed** | The memray stats JSON export, uploaded through a presigned URL from `owner-a-presign` to `artifacts/<repo>/<scan>/<sha>/<name>` |
| **Report output field** | `finding.summary` (location, count, share, total vs peak, churn) + evidence fields `top_allocations_by_count`, `total_bytes_allocated`, `metadata` from the artifact |
| **False-positive risk** | Medium — churn shows short-lived allocations, not that an object is avoidable; a finding is an indicator, not proof |
| **Detectable** | M (needs a representative workload) |
| **Measurable** | M — allocation counts and bytes are measured; energy is not |

## Input

Real `memray stats --json` export (memray 1.x, Python 3.13, fixtures in `test/fixtures/code-c6-1/`):

- `total_num_allocations` (count), `total_bytes_allocated` (bytes), `metadata.peak_memory` (bytes)
- `top_allocations_by_count`: `[{ "location": "func:file.py:LINE", "count": N }, ...]` (memray lists only the top few)

Anything else (not JSON, a JSON array, missing or wrongly typed fields) is an honest `unavailable`
result with the reason, never "clean". The upload parser (`owner-a-profile-parser`) answers an artifact above 5 MB with `unavailable` without reading it.

## Thresholds (overridable per input through `context`)

| Setting | Default | Meaning |
|---|---|---|
| `min_total_bytes` | 1,048,576 | a run that allocated less in total is too small to judge |
| `churn_ratio_min` | 20 | total / peak at or above this counts as churn |
| `min_hotspot_count` | 1,000 | a location needs at least this many allocations |
| `min_hotspot_share` | 0.05 | ...and at least this share of all allocations |

## Signals (one finding per qualifying location, identity `allocation-churn:<func:file>#n`)

| Shape | Confidence |
|---|---|
| hotspot share >= 50% and churn >= 5 x `churn_ratio_min` | medium |
| any other qualifying hotspot | low |

Locations in the standard library, `site-packages`/`dist-packages`, `<frozen ...>`, `<string>` or `<unknown>`
are ignored; line numbers stay out of the identity so it survives edits above the hotspot.

## Limitations

- One client-run workload: churn depends on what was run; a quiet workload hides a hotspot.
- The export lists only the top allocation sites, and memray does not attribute object lifetimes.
- Static half (C6.1 "unnecessary object" patterns in source) is not implemented.

## Evidence & Citations

- **Source:** SRC-01 *Watts This Smell* (arXiv:2604.04809); taxonomy C6.1 (issue #79).
- **Tool docs:** memray `stats` and *temporary allocations* (bloomberg.github.io/memray).
