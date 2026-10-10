# Category 3 (CI, CI-01..19) - research

Owner C. Decisions: [`category-3-decisions.md`](category-3-decisions.md). Date: 2026-10-09.
Provider: GitHub Actions (D1). Two evidence routes: **static** (workflow YAML in the repo) and **history** (client collector
uploads run/job JSON to S3, parse-only Lambda; `docs/ARCHITECTURE_FLOWS.md`). We never run CI and never execute user code.

Source quality: GitHub/Docker docs below are official. Flake methodology and the concurrency-lint comparison come from vendor
docs and search results (Trunk, Tuist, Tenki, Datadog, one arXiv paper), so treat them as supporting, not authoritative.
UNVERIFIED = not confirmed in a primary source.

## Sources the taxonomy itself cites (added after review: first pass missed them)

The workbook's `Sources` sheet names the evidence behind the CI rows. Read 2026-10-09:

| Source | Used by | What it says | What it changes for us |
| --- | --- | --- | --- |
| SRC-14, "Repeated Builds During Code Review: An Empirical Study of OpenStack" (arXiv 2308.10078, academic, ar5iv HTML read in full) | CI-01, CI-02, CI-10 | A recheck is a re-run after a failing build **without changing the change set** (found by regex on Gerrit CI-bot comments). 66,932 reviews: 55% invoke recheck after a failure; 42% of rechecks change the outcome. **Justifiable** = failure turns to success after a single recheck; **unjustifiable** = changed only after multiple rechecks, or never changed ("could be avoided if developers figure out the causes"). Waste = accumulated runtime of all test jobs across rechecks (187.4 compute-years, 170.6 unjustifiable). Recommends: recheck only failed jobs, timeouts/delays, require an explanation. Threats: one community (OpenStack), Gerrit only, regex false positives, waste metric is an approximation. | Confirms CI-01/02/05 are one family on the same observable (same `head_sha`, later attempt). Our thresholds now have a basis: `min_rerun_attempts` 3 means at least two re-runs, the paper's "multiple rechecks" (unjustifiable) case; CI-05 (fail then pass on a re-run) is the paper's *justifiable* case and stays a separate, weaker signal; CI-02 is the paper's "outcome never changed" case. Gerrit comments are not read; the GitHub equivalent is `run_attempt` (mapping now supported, not just assumed). Runner-seconds of the repeated attempts could later be reported as a measured `cpu_time` (contract metric) using the paper's metric; not built yet. |
| SRC-16, Gradle/Develocity "CI without the wait" (vendor blog; URL moved to develocity.ai) | CI-06, CI-07, CI-08, CI-11, CI-12, CI-17 | Ephemeral CI builds start with an empty cache; repeated dependency downloads (7 TB/week at one organisation), setup overhead, rebuilding unchanged work (a Maven build spent 6 of 8 minutes on unchanged inputs). Remedies: dependency/artifact cache, setup cache, build cache, observability. Vendor claims (50% setup, 95% dependency speed-up) are not verified. It does **not** discuss Docker layer caching or cache-key design. | Supports CI-06/CI-08 in principle. It is not a source for CI-07, CI-11, CI-12 or CI-16 even though the taxonomy cites it there; those rest on the GitHub/Docker docs above. Its detection is proprietary build telemetry (Build Scans), not static analysis: no off-the-shelf static detector exists for these rows. |
| SRC-17, Harness "Reduce CI costs without slowing development" (vendor blog) | CI-03, CI-06, CI-11, CI-12, CI-13, CI-19 | Names caching, test selection (vendor claim 50-80% fewer tests), parallelism (mostly wait time, not compute), flaky-test detection (target under 2% flaky), runner right-sizing (30-50% infra claim, unsourced), auto-scaling. **Does not name** scheduled builds, artifact retention, matrix builds or cancelling redundant builds. | CI-12, CI-13, CI-19 and CI-11's cancellation have no support in this source despite being cited. They rest on GitHub docs and on our real-repo checks. Its figures are vendor claims and are not used as evidence. |

Source codes `OS`, `GR`, `HA` in the taxonomy `source` column have no legend in the workbook sheets searched; their meaning is unresolved, not assumed. The workbook has no per-check detection tool for the CI rows (`detection_tool` is empty).

Real-world tool landscape (what exists, honestly): static workflow linters (zizmor, Datadog IaC rules; actionlint not verified) cover only concurrency, caching security and runner type; flaky-test detection (Trunk, Tuist, Tenki) and test-intelligence / build-scan products (Harness, Develocity) are SaaS on proprietary CI telemetry. For most rows we are defining the static or history rule ourselves, grounded in GitHub's own docs, SRC-14 for the rerun family, and checks on real repositories.

## What existing tools already do

| Tool | Relevant coverage | Gap for us |
| --- | --- | --- |
| zizmor (docs.zizmor.sh/audits) | `concurrency-limits` (missing concurrency, pedantic), `cache-poisoning`, `artipacked`, `self-hosted-runner` (pedantic), `superfluous-actions` | Security-focused; no cache-missing, retention, matrix-redundancy, schedule or history checks. |
| Datadog IaC rule `concurrency_limits` | Flags missing `concurrency` or a bare-string value without `cancel-in-progress` | Single check; no history. |
| Trunk / Tuist / Tenki (flaky tests) | Fail-then-pass on one commit = flaky; recovery window 7 d (Trunk) / 14 d (Tuist) | Need test-level results; GitHub job conclusions only give job-level signal. |
| actionlint | UNVERIFIED: rules list not fetched (search returned no page). Known as a syntax/expression linter, not a waste linter. | Not a source for our rules. |

## GitHub facts the rules rely on (official)

- `concurrency` at workflow or job level; `cancel-in-progress` defaults to not cancelling; default queue keeps one pending run and replaces older pending ones. Group should include `github.workflow`; `github.head_ref` exists only for `pull_request`, so mixed triggers need a fallback (`github.head_ref || github.run_id`). Cancel can be an expression.
- Caching: `actions/cache` keys max 512 chars; `hashFiles('lockfile')` in the key; caches are immutable; unused 7 days -> evicted; 10 GB per repo default. `setup-node` `cache:` input is off for yarn/pnpm unless set, automatic for npm only when `packageManager` says npm; it caches the package store, not `node_modules`. Similar `cache` inputs exist in setup-python/java/go/dotnet/ruby.
- Docker layers: `docker/build-push-action` `cache-from`/`cache-to` (`type=gha` or `type=registry`, `mode=max`); read-only events should keep `cache-from` and omit `cache-to`.
- `on.schedule`: UTC cron, minimum 5 min, always runs the default branch's latest commit. `paths`/`paths-ignore` apply to push and pull_request; use one or the other.
- `upload-artifact`: `retention-days` default is the repo setting (90 days), range 1-90.
- Larger runners are billed per minute and not covered by included minutes on private repos.
- REST: run object has `run_attempt`, `conclusion`, `event`, `head_sha`, `created_at`, `run_started_at`, `updated_at`, `previous_attempt_url`, `triggering_actor`. Job object has `labels`, `runner_name`, `started_at`, `completed_at`, `conclusion`, `run_attempt`, `steps[]` with timings. Jobs per attempt: `GET /repos/{o}/{r}/actions/runs/{id}/attempts/{n}/jobs`. No endpoint lists all attempts; fetch by attempt number. Artifacts expose `expires_at`, `size_in_bytes`, `expired`.

## Per-check table

Route: S = static YAML, H = history artifact. Starting confidence is what we would report, not the taxonomy's.

| Check | Our rule | Evidence | False-positive traps | Route | Start confidence |
| --- | --- | --- | --- | --- | --- |
| CI-01 repeated builds in review | Same `head_sha` on `pull_request` needed 3 or more attempts (more than one re-run = SRC-14's "multiple rechecks") | run list + attempts | SRC-14 studies Gerrit comments; the GitHub equivalent is a re-run attempt of the same commit. Re-runs after an infra outage are legitimate and not separable. | H | Medium |
| CI-02 blind rechecking | Re-run with **no new commit** between attempts and the failed job's step/log set unchanged | attempts + job steps | Cannot see whether a human diagnosed; only "same sha, same failure, re-run" is observable. | H | Low |
| CI-03 flaky tests | Same (workflow, job, matrix leg, `head_sha`) with failure then success; job-level only | job conclusions per attempt | Infra failures also fail-then-pass; a pass in a different matrix leg is not recovery. Job-level, not test-level. | H | Medium |
| CI-05 brown builds | Run attempt 1 `failure`, later attempt `success`, same sha (workflow level) | run attempts | Overlaps CI-03; report at workflow level, CI-03 at job level. | H | Medium |
| CI-06 no dependency caching | Job installs deps (`npm ci`, `pip install`, `mvn`, `go build`...) with no `actions/cache` and no `cache:` on its `setup-*` step | workflow YAML | Cache may live in a composite/reusable action we cannot see; self-hosted runners with a persistent disk; trivial installs. | S | Medium |
| CI-07 Docker layers not cached | `docker build`/`build-push-action` with no `cache-from`/`cache-to` | workflow YAML (+ history for rebuild time) | Cache configured in a Dockerfile `RUN --mount=type=cache` or builder settings; one-off image builds. | S (H optional) | Low |
| CI-08 full builds where incremental possible | Build/test step with no build-cache or path filter AND history shows near-constant duration | YAML + durations | Hard to prove "incremental possible" from YAML alone; low evidence without history. | S+H | Low |
| CI-10 full test suite on every change | `pull_request`/`push` workflow runs tests with no `paths`/`paths-ignore`, and history shows docs-only changes still run tests | YAML + changed paths (history) | Required status checks need workflows to run; skipped workflows can block merges (UNVERIFIED: branch-protection interaction not fetched). | S+H | Low |
| CI-11 redundant triggers / no cancellation | Workflow triggered by `pull_request` and `push` on the same branch, or PR workflow with no `concurrency` + `cancel-in-progress: true` | YAML | Deploy/release workflows must NOT cancel in progress; `cancel-in-progress` may be an expression; group missing `github.workflow` can cancel other workflows. | S | High |
| CI-12 scheduled pipelines with no change | `on.schedule` workflow with no guard (no check that the default branch changed since the last run) | YAML (+ history: run sha repeats) | Schedules are legitimate for dependency/security scans and flaky detection; only flag when history shows identical `head_sha` repeating. | S+H | Low |
| CI-13 redundant matrix jobs | Matrix with overlapping/duplicate combos, `include` entries that duplicate base combos, or a very wide cross product with no `exclude` | YAML | Intentional compatibility matrices (supported OS/runtime versions) are not waste; can only flag duplicates and obvious over-width, not "unneeded". | S | Medium |
| CI-14 oversized runners | `runs-on` uses a larger-runner label (e.g. `-8-core`, `-16-core`) for jobs with only lint/docs/light steps | YAML labels | Label naming is org-specific for larger runners (UNVERIFIED: no official label scheme found); build/test jobs may need the CPU. D2: static only. | S | Low |
| CI-15 static runner pools | Needs live utilization | none | Unavailable by D2. | - | n/a |
| CI-16 cache misconfiguration | `actions/cache` with a static `key` (no `hashFiles`/lockfile), key over 512 chars, `restore-keys` less specific than `key`, or cache path `node_modules` with a `setup-node` store cache duplicate | YAML (+ hit rate via history) | Static keys are valid for immutable toolchain caches; cache hit rate is not exposed by the run/job objects (UNVERIFIED where it comes from). | S | Medium |
| CI-17 no CI observability | No timing/summary step, no duration export, no `GITHUB_STEP_SUMMARY`/annotation use; history artifact itself is absent | YAML + collector absent | "No observability" cannot be proven from YAML alone; very likely noisy, so only meaningful when collector data is missing. | S | Low |
| CI-18 sequential test execution | Several test jobs chained with `needs:` where nothing requires order; or one job runs multiple independent test commands serially; history shows summed durations | YAML + step timings | Real dependencies (build artifacts, shared DB) justify sequence; test-level parallelism (`pytest -n`, `jest --shard`) is invisible in YAML. | S+H | Low |
| CI-19 artifacts retained forever | `upload-artifact` with no `retention-days` (uses the 90-day repo default) or a large value; none set for large paths | YAML | Default is a deliberate repo setting; compliance artifacts need long retention. D2: static only. | S | Medium |

Missing issues: CI-04 and CI-09 have no taxonomy row or issue.

## Implementation notes (for the discussion step)

- Static checks (CI-06, 07, 11, 13, 14, 16, 19 and the static halves of 10/12/17/18) need a YAML parser. Python stdlib has none; options are PyYAML (new dependency) or a minimal parser restricted to the keys we use. Decide in discussion.
- History checks (CI-01, 02, 03, 05 and the history halves) need a defined collector payload: run list + per-attempt jobs, no logs, no secrets. Define it as a normalizer output like Category 1 (`normalize/`), recorded from a **real** GitHub Actions run (D3 of Category 1: no invented formats).
- Reporting identity: static = `file:.github/workflows/<name>.yml`, identity anchored on job id / step name, not line numbers.
- Overlap to settle: CI-03 vs CI-05 (job vs workflow level), CI-01 vs CI-02 (count vs "no new commit").

## GitHub re-run and attempt semantics (official pages fetched 2026-10-10; tracker CD-4, RS-3)

Pages: `docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/re-running-workflows-and-jobs`,
`docs.github.com/en/rest/actions/workflow-runs`, `docs.github.com/en/rest/actions/workflow-jobs`.

- Three re-run modes exist: all jobs, failed jobs only (`POST .../runs/{id}/rerun-failed-jobs`, "re-runs failed jobs and their
  dependent jobs") and a single job (`POST .../jobs/{id}/rerun`, "re-runs the job and its dependent jobs"). All reuse the original
  `GITHUB_SHA` and `GITHUB_REF`; limit 50 re-runs per run and 30 days.
- `run_attempt` numbers the attempt; `GET .../runs/{id}/attempts/{n}` returns that attempt with the run schema (so it has its own
  `conclusion`); `GET .../attempts/{n}/jobs` returns the jobs of attempt n; `jobs?filter=latest|all` filters on `completed_at`.
- Run `status`/`conclusion` values listed: completed, action_required, cancelled, failure, neutral, skipped, stale, success,
  timed_out, in_progress, queued, requested, waiting, pending. Job `conclusion`: success, failure, neutral, cancelled, skipped,
  timed_out, action_required, null.
- **Not documented (so UNVERIFIED until measured in the lab):** what a re-run attempt's job list contains (only the re-run jobs, or all
  jobs with earlier results carried over); whether `needs` dependents of a re-run job appear; how outputs/artifacts carry over;
  whether `startup_failure` is a possible conclusion; why many earlier attempts return an empty job list (seen in psf/black).
- Consequence for our rules: inferring an attempt's outcome from its job list is unsafe when an attempt can hold only a subset of the
  jobs. The authoritative outcome of attempt n is the `conclusion` of `GET .../attempts/{n}`; the collector should fetch it, and
  CI-02/CI-05 should use it, with job lists used only for per-job flake counting (CI-03).

## Verified against official pages (fetched 2026-10-10; tracker RS-2, RS-3, DQ-5)

| Claim | Source fetched | Result |
| --- | --- | --- |
| A required status check from a workflow skipped by `paths`/branch/commit-message filters | docs.github.com ... troubleshooting-required-status-checks | Stays **Pending and blocks merging**; GitHub's workaround is not to require workflows that can be skipped by filters. CI-10 now says so and recommends job-level change detection when the workflow may be required. |
| Larger-runner labels | docs.github.com ... running-jobs-on-larger-runners | `runs-on` selects by group and/or label; "Larger runners are automatically assigned a workflow label that matches the runner name", so labels are the runner's own name. Official examples `ubuntu-24.04-16core`, `windows-2022-16core`, `macos-26-xlarge`; **no official core-count scheme**. CI-14 reads `-N-cores` / `-Ncore` suffixes and macOS `-large`/`-xlarge` (case CI-14-11). |
| `setup-go` caching | github.com/actions/setup-go | `cache` defaults to true (go.mod hash by default; `cache-dependency-path` for go.sum). CI-06 does not evaluate Go: its default is already cached. |
| Matrix `include` semantics | docs.github.com ... using-a-matrix-for-your-jobs | An include entry extends every original combination it can without overwriting a value; one that cannot becomes a new job; includes do not extend combinations made by earlier includes. Not stated: whether an include that only repeats existing values adds anything, or exclude-before-include order. CI-13's no-op-include rule is **derived** from the documented rules (UNVERIFIED as an explicit statement). |
| Artifact retention | docs.github.com ... configuring the retention period | Default 90 days; public repositories 1-90, private up to 400; set in the organization's Actions settings; a repository cannot exceed the managing organization's maximum. |
| actionlint (rhysd/actionlint docs/checks.md, headings read) | github.com/rhysd/actionlint | Has a "Matrix values" check (duplicate values, exclude entries matching nothing: overlaps CI-13's duplicate-axis rule) and runner-label validity. Has **no** check for missing concurrency, caching, artifact retention or runner size. |
| zizmor `concurrency-limits` (pedantic, "pretty noisy") | docs.zizmor.sh/audits | Flags missing `concurrency`; no exemptions named. CI-11 is stricter about what it exempts (pull_request types without synchronize, deployments, deliberate `cancel-in-progress: false`). |
| zizmor `cache-poisoning` | docs.zizmor.sh/audits | "Generally speaking, release workflows should not read from the GitHub Actions cache." CI-06, CI-07 and CI-16 therefore **skip release, publish, deploy and tag workflows** (before this, they would have told release workflows to add caches). |

Honest limits: the zizmor page was read to 100,000 of 129,809 characters; actionlint's headings were read, not every section.
Still not found in a fetched page: whether `startup_failure` is a run conclusion (tracked in CD-12).
Real-tool landscape: no off-the-shelf static detector exists for CI-06, 07, 08, 10, 14, 16 or 19; actionlint covers only duplicate matrix values (part of CI-13) and zizmor only missing concurrency (pedantic) and cache-poisoning.

## Workbook rows read (tracker RS-1, RS-4, RS-5; 2026-10-10)

- **Source-code legend (RS-1):** the codes `OS`, `GR`, `HA` in the taxonomy `source` column are not defined on any workbook sheet searched (Sources, Read Me, Method & Gaps, Summary and the rest). Recorded as unknown, not assumed. Nothing in our rules depends on them.
- **Row fields (RS-4):** `not_wasteful_when` read for every CI row and mapped: CI-01 (justifiable single recheck) -> SRC-14 threshold; CI-02 (confirmed flaky) -> CI-03 is a separate signal; CI-10 (risk of missing tests) -> low confidence and limitation text; CI-18 (parallelism trade-offs) -> wall-clock, not compute, in the recommendation; CI-19 (compliance retention) -> limitation text; CI-08 (needs correct keys) -> CI-16. `notes` explain the CI-04/CI-09 anomaly: **CI-04 ("rechecking passing jobs") was absorbed into CI-01 and CI-09 ("rebuild/retest unchanged code") into CI-08 in taxonomy v1.3**, so those issues do not exist. CI-01 now also reports passing jobs executed again after a failure (`reran_passing_jobs`); CI-08's unchanged-code rebuild is covered by clean-after-cached-build-output.
- **Open Questions (RS-5):** OQ-6 (CI provider connector and where run history comes from) is answered by decisions D1 and D12. OQ-7 lists **CI-14, CI-15 and CI-19** for a client read-only AWS role (CloudWatch / Resource Explorer / Compute Optimizer); we deviate (D2): GitHub-hosted runners expose no CloudWatch metrics, so CI-15 is unavailable and CI-14/CI-19 are static.
