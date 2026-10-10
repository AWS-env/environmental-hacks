# Category 3 (CI, CI-01..19) - decisions

Owner: C (Medhansh-741). Research: `category-3-ci.md` (in progress). Follows `.agents/rules/solve-issues-by-category.md` and
`docs/ARCHITECTURE_FLOWS.md` (client side only emits artifacts; our side only parses).

## Decided

| # | Decision | Why |
| --- | --- | --- |
| D1 | CI provider is **GitHub Actions** (OQ-6). Static checks read workflow YAML from the repo. History checks use a **client collector**: one client-CI step uploads GitHub Actions run/job JSON to S3 by presigned PUT; an owner-c Lambda parses it. We never run their CI and never hold a GitHub token for history. | `ARCHITECTURE_FLOWS.md` trust boundary (flow 1 step 8, flow 3 "Client CI -> presigned PUT"); same route as py-spy/memray in Category 1 (Python D2). |
| D2 | CI-14 (oversized runners) and CI-19 (artifacts retained forever) are implemented **static-only** (runner labels, `retention-days`, lifecycle config in YAML/IaC). CI-15 (static runner pools) stays **unavailable**: it needs live runner utilization. Issues stay open where coverage is partial. | Needs client cloud metrics (OQ-7); no invented formats. |
| D3 | Region ap-south-1 (owner D's `findings-hub`), account 768666229343, CloudFormation YAML, `owner-c-` names, `owner=C` tags. A **new stack** `owner-c-ci-detectors`; the Python stack is not touched. | Preflight 2026-10-09; avoids two chats updating one stack. |
| D4 | Work happens in worktree `../environmental-hacks-ci` on branch `wip/category-3-ci`, off `origin/main`. | The JS chat uses the main working copy. |

| D5 | YAML engine is **PyYAML**, bundled in the Lambda zip and installed in the CI test step. `on:` parses as boolean `True`; the parser handles both keys. | Real workflows use anchors, multiline and flow syntax; a hand-written parser would be fragile. |
| D6 | Real run-history is captured read-only with `gh api` from 4-5 popular public repos (runs, attempts, jobs). The collector payload mirrors that real JSON; nothing is invented. | Category 1 D3 (real captures only). |
| D7 | **Superseded by D15.** AWS shape (first design): two Lambdas in the new stack `owner-c-ci-detectors` (ap-south-1): `owner-c-ci-static-scan` (workflow YAML) and `owner-c-ci-history-parser` (S3 run/job JSON); own private expiring S3 bucket; arm64; 7-day log retention; one `detector.result.v1` event per result to `findings-hub`. | Same pattern as Category 1; keeps the Python and JS stacks untouched. |
| D8 | Scope: all checks **except CI-15**. Static: CI-06/07/11/13/14/16/19. Static + history: CI-08/10/12/17/18. History: CI-01/02/03/05. Low-confidence checks report with explicit coverage limits. | User choice 2026-10-09. |

| D9 | Refinement of D8 after reading the real GitHub objects. **Static-reported** (workflow YAML only): CI-06, 07, 08, 10, 11, 13, 14, 16, 19. **History-only** (normalized client artifact): CI-01, 02, 03, 05. **Static candidate + history confirms** (artifact-confirmed, like PY-01): CI-12 (schedule trigger + repeated `head_sha`), CI-18 (chained test jobs + measured serial seconds). **Not implemented**: CI-15 (D2) and **CI-17**; their issues stay open. | CI-17 "no observability" cannot be proven from YAML or run objects. The run/job objects carry no changed-file lists, so CI-10 and CI-08 cannot be history-confirmed; they stay static with low/medium confidence. CI-08 is narrowed to explicit forced full builds (`--no-build-cache`, `--rerun-tasks`, `clean` next to a restored build-output cache) because a fresh runner has nothing to clean. |
| D10 | CI code lives in its own subpackage `detectors/owner-c/owner_c/ci/` (checks, history checks, normalizer, connector, runner). It imports only the stable helpers from `owner_c/contract.py` and does not edit shared Python files. Cases live in `tests/fixtures/ci_NN/ci_cases.json`, run by `tests/test_ci_cases.py`. | The JS chat is editing `common.py`, `connector.py`, `runner.py`, `contract.py` and both registries; separate files avoid merge conflicts. |

| D11 | CI-01/02/05 are grounded in SRC-14 (OpenStack recheck study): same commit re-run = recheck; one re-run that flips failure to success is "justifiable" (CI-05 only), several re-runs or a re-run that still fails is "unjustifiable" (CI-01, CI-02). | Taxonomy `Sources` sheet; research file section "Sources the taxonomy itself cites". |
| D12 | History format **v2** and rules redone on measured GitHub behaviour (tracker CD-4, CD-5, CD-6). A recheck is a re-run after an attempt that FAILED (`failure`/`timed_out`): `action_required` (first-time-contributor approval gate) and `cancelled` attempts are not rechecks. CI-01 field is `max_rechecks_per_sha` with `min_rechecks` 2 (was attempts, 3). Each attempt carries its own conclusion (`GET .../attempts/{n}`). A job counts as re-executed in an attempt only when `started_at >= created_at` (GitHub lists every job in every attempt and keeps old timestamps for jobs that were not re-run): CI-02 and CI-03 use this, so a failure only carried over is not a repeated failure and a carried-over success is not a pass. CI-18 uses one stage-name function for workflow and history (trailing `(leg)` removed, `${{ }}` in a name matches any text). The collector is a standalone single file. | The docs do not state what an attempt contains. A private lab repo with scenarios built to fail in known ways (re-run failed jobs twice, single job, all jobs, cancel, clean, two pull requests) gave the ground truth; psf/black's `action_required` attempts showed the first version over-counted. Expected values in `test_ci_real.py` follow from how the scenarios were built. |
| D13 | Issue-closing policy (tracker SP-1): each PR `Closes #N` (the repo CI requires exactly one) and states a **Not covered** line in its body; the posted Verification plan is the agreed scope of the issue. No follow-up issues are opened. Narrowed checks: CI-08 (forced full builds only), CI-10, CI-14, CI-19 (static only), CI-12 and CI-18 (detector done; the client collector is a documented client-side step). CI-15 and CI-17 stay open (not implemented); CI-04 and CI-09 have no issue. | Owner answer 2026-10-10. The run objects carry no changed-file or utilization data, so the dropped halves cannot be built, and extra issues would only add clutter in the shared repo. |
| D14 | CI-08, CI-14, CI-16 narrowed after a real-data search (tracker DQ-1): 336 real workflow files (GitHub code search for the patterns, hand-checked). CI-08 no longer reports explicit `--no-build-cache`/`--rerun-tasks` (every sampled hit was deliberate: CodeQL, Infer, forced tests, dependency warm-up) and keeps only clean-after-restored-build-cache, ignoring per-run hand-off caches and `.gradle`. CI-16 judges only package-manager caches (a constant key there is never refreshed), skips versioned keys, tool/data caches (Sonar's documented snippet, AVD, fonts) and requires an OS part only for platform-specific content (not `~/.m2`). CI-14: a job whose name mentions test/build/clippy is not light, and `cargo check|clippy|doc|nextest` count as builds. | Precision before: CI-16 34 hits, almost all legitimate; CI-08 ~15, all deliberate or hand-offs; CI-14 2 (one false: rerun-io `Rust lints (...tests...)`). After: 1 hit each, all hand-verified true (CrockPot clean after cached build dir; open-telemetry pnpm store constant key; nats.go lint on 8 cores). |
| D15 | **One tied owner-C system, no CI-specific AWS resources.** CI plugs into what Category 1/2 already deployed: (1) CI static checks run inside the shared `owner-c-static-scan` (it already loads every file of the repo zip; the handler also calls the CI connector); (2) CI run history is a new artifact type `ci_history` in the normalizer registry (`owner_c/normalize`), so the existing presign -> `manifest.json` -> `owner-c-profile-parser` flow carries it with its regional presign endpoint, S3 trigger, DLQ and alarm (the presign allow-list is derived from the registry, so presign needs no change); (3) the unified scanner's owner-C adapter also runs the CI static checks and reports the history checks as `unavailable` (no history in a repo scan); (4) the oversize-result handling (publish an explicit `error` result instead of failing the batch) moves into the shared `publish_results` and benefits every category; (5) one code package, one zip version and one build script for all owner-C categories (PyYAML is pinned in the shared `requirements.txt`). Deleted: my separate stack `owner-c-ci-detectors`, its bucket, Lambdas, template, `build-owner-c-ci-lambda.sh`, `owner_c/ci/aws/`. The collector writes a **bundle** (`{workflows: [...]}`) uploaded as the single artifact `ci_history`. | Owner direction 2026-10-10: no redundancy, nothing stale, all owner-C categories logically tied. Read-only inspection of `main` showed the shared handler, presign, parser, DLQ/alarm, scanner adapter and owner D's hub already cover what a CI-specific stack would duplicate; a second stack means a second zip to keep in sync. |
| D16 | Event source for owner-C results is renamed from `owner-c.python-detectors` to `owner-c.detectors` (it carries Python, JS and CI). | Owner D's hub rule `owner-d-findings-ingest` matches any `source` with prefix `owner-` and stores it, so nothing depends on the old string; done once, now, before consumers rely on it. |
| D17 | **AWS deployment of the integrated CI waits until the Frontend category is merged to `main`.** No AWS write happens before that: no zip upload, no stack update, no live handshake test, and my old stack `owner-c-ci-detectors` is deleted together with the rest of the batch, not earlier. The deployed zip is then built once as main + CI. Rejected: overlaying CI on the live zip (keeps unmerged Frontend files alive but bakes another session's unreviewed code into my artifact) and deploying main + CI now (would silently remove the live Frontend check). Zip numbering: `v12` is the Frontend/JS deployment, never overwritten; the next free number is used. | Read-only drift check (2026-10-10): the live `owner-c-detectors-v12.zip` equals `main` except `checks/fe_03.py`, `web/__init__.py`, `web/ctx.py` (only in the zip) and edits to `checks/__init__.py` and `langs.py`; the three shared files CI edits (`handler.py`, `profile_handler.py`, `common.py`) are identical to `main`. Owner direction: if we logically need to wait for Frontend to merge, wait rather than continue. |
| D18 | Rules changed by the official-page research (tracker RS-2/RS-3/DQ-5): CI-06, CI-07 and CI-16 skip release/publish/deploy and tag workflows (zizmor: release workflows should not read caches); CI-10 recommends job-level change detection when the workflow may be a required check (GitHub: a skipped required check stays Pending and blocks merging); CI-14 recognises the official label form `ubuntu-24.04-16core` and states there is no official scheme; CI-19 and CI-13 limitations cite org retention policy and actionlint's overlapping duplicate-value check. | Fetched official pages, see the research file section "Verified against official pages". Effect on 82 real files: CI-06 16 -> 12, CI-07 1 -> 0; on 336 files the CI-08/14/16 hits are unchanged. |
| D19 | Collector and conclusions (tracker CD-3, CD-12, CD-13, DT-2): the collector follows pagination, waits out rate limits up to 90 s (`Retry-After` / `X-RateLimit-Reset`) and explains 401/403/404/429; it runs as a real CI step with the built-in `GITHUB_TOKEN` and `permissions: actions: read` (proven in the lab, artifact identical to the laptop capture); distribution is the single file pinned to a commit of this repository, uploaded with `examples/client-ci/upload-ci-history.sh`. A conclusion GitHub's docs do not list (`startup_failure`, seen only in third-party reports) is never counted as a failure. Composite actions and other CI providers are declared as not scanned in each payload's `context`. | The docs list the values; unknown must stay unknown. A client can only adopt what is one downloadable file and one step. |
| D20 | CI-19 keeps only explicit long retention (`retention-days` above `max_retention_days`, 30). An unset value is **not reported** any more. | Owner direction 2026-10-10 (tracker DQ-3): the default is 90 days, not "forever", an organization may already shorten it, and the unset rule produced 50 low-confidence hits on 5 real repositories, none actionable. Taxonomy row says "retained forever": only an explicit long value approaches that. Issue #33 stays closed by the PR with the Not covered note. |
| D21 | Small limits closed with code (tracker DQ-8, DQ-10): CI-11 reports a cancelling `concurrency.group` made only of context expressions with no `github.workflow` and no literal text (`workflow:concurrency-group-shared`, `job:<id>:concurrency-group-shared`, medium); literal text is assumed unique and cross-file clashes are not checked. CI-07 reports `docker compose build` / `up --build` and `docker/bake-action` without a cache in the step, at low confidence (the compose and bake files are not read). CI-13 (DQ-9) is kept with its documented recall limit. | Owner direction 2026-10-10. Cases CI-11-15..18, CI-07-12..16 (positive, similar negative, literal-text exception). |
| D22 | Real-repo breadth (tracker DQ-2, DT-1) stays inside the decided project aim: **Python, JavaScript and TypeScript repositories** only. No Java/Go/Rust/.NET repos are sampled; their ecosystems are declared as not evaluated, not claimed clean. | Owner correction 2026-10-10: the aim was decided earlier; sampling other ecosystems would widen the scope without a decision. |
| D23 | Process choices 2026-10-10: the foundation PR (CI-11, about 2.7k lines) ships as one PR and the PR notes say so (SP-2); the private lab repo `Medhansh-741/owner-c-ci-lab` is kept (DT-4); CI is rebased on the newest `origin/main` now and once more after the Frontend merge (CT-1). | Owner answers 2026-10-10. |

## Anomalies

- 17 CI issues, not 19: CI-04 and CI-09 have no issue and no taxonomy row.
- CI-14/15/19 are mapped to R4/R13 (client cloud metrics), not CI history.

## Open

- Evidence/false-positive research per check (see `category-3-ci.md`).
- Detector engine for YAML (stdlib has no YAML parser; decide dependency vs minimal parser).
- Scope policy (which workflow files, test fixtures) and real repos to test on.

## Verification log (2026-10-09)

- CI tests (135 committed verification cases in `tests/fixtures/ci_NN/ci_cases.json`, plus AWS-handler, collector,
  real-capture and robustness tests); the whole owner-c suite (88 tests on main 5ddb515 + CI, Python 3.12 and 3.14) and the shared contract suite pass. Every result is validated with `shared.contracts.validation.validate_pair`. The cases fail
  against an always-empty and an always-flag implementation of every check. Passes on Python 3.12 (repo CI) and 3.14.
- Real workflow files: 82 files from pandas, flask, fastapi, next.js and home-assistant (static checks only). Hits
  after refinement: CI-06 16, CI-07 1, CI-10 1, CI-11 7, CI-19 50 (all low confidence, retention unset), CI-08/13/14/16 0.
  Hand-checking found and fixed false positives: `pull_request_target` automation and `types: [closed]` flagged by
  CI-11, explicit `cancel-in-progress: false`, `pip install pytest` counted as running tests, `pip install build` as a
  dependency install, two builds of one Docker context, cache flags passed through a variable, push-to-main runs and
  `if`-gated jobs flagged by CI-10. The CI-13 wide-matrix rule was dropped because all 5 hits were intentional.
  CI-08, CI-14 and CI-16 found nothing on these repos: their positives are proven on the cases only.
- Real run history: psf/black, numpy/numpy, tiangolo/sqlmodel, fastapi/fastapi captured with the client collector
  (`tests/fixtures/real_ci/`). **Corrected 2026-10-10 (tracker CD-4):** the first pass reported "black has a commit with
  3 attempts" and counted it as repeated builds; on inspection the empty attempt-1 job lists were GitHub's
  first-time-contributor approval gate (`action_required`), so that commit has one recheck, not two. The history
  format and rules were redone on measured GitHub behaviour (decision D12). `test (3.15, windows-11-arm)` failed then
  passed on 2 commits (still true). GitHub fails on some very large runs (next.js), so the collector retries and
  records `jobs_unavailable` instead of aborting.
- Live AWS (ap-south-1, account 768666229343, tagged owner=C): stack `owner-c-ci-detectors`. Static Lambda: dry run
  and a real publish of 9 contract results to `findings-hub` (smoke repo `github:owner-c-smoke/ci-detectors-smoke`).
  History Lambda: black history uploaded by presigned PUT to the stack's bucket and parsed from S3 (CI-01 1 finding,
  CI-03 1, CI-02 and CI-05 clean, same as the local test).

## Open

- No `findings-hub` rule/consumer for `detector.result.v1` yet (owner D): published events cannot be read back.
- CI-15 and CI-17 are not implemented (issues stay open); CI-04 and CI-09 have no issue.
- No client CI step that runs the collector exists in any client repository; the collector and its flow are documented.
- CI-19 reports every unset retention (50 on the 5 repos); low confidence by design.
- PyYAML is pinned in `detectors/owner-c/requirements.txt` (appended to the file the JS category added); the repo CI step already installs it and `.github/` is not touched.

## Review findings fixed before raising the PRs (2026-10-10)

An adversarial review of the finished work (not just the happy-path tests) found and fixed:

- **Stale base**: `main` gained JS PR #323 while the stack was being built; it added its own
  `detectors/owner-c/requirements.txt` (add/add conflict) and a shared `tests/test_cases.py` that globs
  `*/cases.json`, which ran the CI cases through the Python runner (135 failures). CI cases are now
  `ci_cases.json`, PyYAML is appended to main's requirements file, and the `ci.yml` edit was dropped (main's CI
  already installs that file).
- **YAML alias bomb**: the span builder re-expanded aliases without a bound (hang). Parsing now has bounds (lines,
  line length, indentation, flow depth, node budget); a file beyond them is `unavailable` with a limitation.
- **Oversize results**: a result over the EventBridge limit raised and dropped the whole batch; it is now published
  as an explicit `error` result for that check only.
- **Repeated parsing and unbounded stage pairs**: one parse per file per invocation; run-history stage pairs are capped.
- Tests: `tests/test_ci_robustness.py` (alias bomb, long line, deep nesting, CRLF, BOM, wrong shapes, many jobs,
  oversize result, hostile history).

- **CD-4/CD-5/CD-6 closed (2026-10-10):** lab `Medhansh-741/owner-c-ci-lab` (private scratch repo; deletable): now 9 runs, normalized values equal the
  by-construction expectations (runs 9, rechecks 2, repeat failures 2, fail-then-pass runs 4, `flaky` 7, `matrix (b)` 6,
  passing jobs re-run 2). CI-18 naming cases
  CI-18-10..12 (parentheses, expressions, no over-matching). Public histories recaptured with the v2 collector; black now has 1 recheck and CI-01 no
  longer flags it (it was a false positive caused by the approval gate).
- **DQ-1 closed with an honest limit (2026-10-10):** 336 real workflow files; positives for these patterns are rare (1 verified true positive each
  after the fixes). Base rates and the 10 new cases (CI-08-08..10, CI-14-09..10, CI-16-10..13) are recorded; more breadth is tracked as DQ-2.

## Real-repo breadth, Python/JS/TS only (2026-10-10, tracker DQ-1, DQ-2, DQ-4, DQ-6, DQ-7, DT-1; decision D22)

21 public repositories, 513 workflow files, static checks run with the shared runner and hand-checked against the source.
Python: django, scikit-learn, pytest, pydantic, apache/airflow, full-stack-fastapi-template, cookiecutter-django, plus the
earlier fastapi, flask, pandas, home-assistant. JS/TS: react, TypeScript, vite, vue core, eslint, n8n, cal.com, dify, immich,
plus the earlier next.js. Hits before and after the fixes below (15 repos first, then all 21 after the second fetch):

| Check | First run | Final (21 repos) | Hand-check |
| --- | --- | --- | --- |
| CI-06 | 46 | 51 | Every hit is a job that installs project dependencies with no cache step. 1 false positive fixed (airflow codeql: a `grep '\.gradle$'` line was read as running gradle). npm hits after `setup-node` v6+ are now low confidence (TypeScript: `packageManager` is npm, so setup-node caches by itself: confirmed in the setup-node README). |
| CI-07 | 0 | 8 | Real positives: `docker compose build` (full-stack-fastapi-template x2), `docker compose up --build` (immich x2), `build-push-action` with no cache (dify x2, n8n x2). All verified in the source. |
| CI-10 | 43 | 21 | 22 hits removed as false positives: jobs gated by an opt-in pull request label (pydantic third-party tests), `uv pip uninstall pytest-speed` read as running pytest (pydantic codspeed), `python -c "... pytest ..."` (scikit-learn lint); pydantic alone went from 30 to 10 (the split of the 22 by cause was not counted separately). Remaining 21 are low-confidence full suites on every pull request. |
| CI-11 | 25 | 49 | 46 pull request workflows with no `concurrency` (every file read; 1 mentions "concurrency" only as a build flag), 2 push+pull_request without filters, 1 shared group (cookiecutter `github.head_ref || github.run_id`, low confidence: no other workflow of that repository uses it). 3 false positives fixed: a release workflow whose group is an input (airflow), a pull request run limited by `paths` to its own workflow file (vite), a workflow whose only step is `github-script` (react). |
| CI-19 | 50 (unset rule) | 1 | Unset retention dropped (D20). The one hit is n8n `jscpd.yml`: 31 days (limit 30) on 20 upload steps, now reported once per job instead of 21 times. The limit of 30 is our own default and n8n sits one day above it: configurable. |
| CI-08, CI-13, CI-14, CI-16 | 0 | 0 | No hits on these repositories; their positives stay proven on cases and the earlier 336-file search (D14). Honest limit: Python/JS/TS CI rarely triggers them. |

History (DT-1, DQ-7): four more captures with the standalone collector, 60 runs each: scikit-learn `unit-tests.yml` (schedule + 16-job matrix), pytest `test.yml` (32 jobs per attempt, one attempt without job detail), vite `ci.yml` (three re-run runs), TypeScript `nightly.yaml` (59 scheduled runs on 21 commits: 38 repeat a commit, hand-counted). The vite capture found a counting bug: a result-gate job that ended `skipped` in the re-run was counted as a passing job run again (`reran_passing_jobs` 2 -> 1); fixed and covered by `test_vite_reruns_hand_checked`. Fixtures in `tests/fixtures/real_ci/` (no logins, e-mails or URLs: grep 0).

| ID | Decision |
| --- | --- |
| D24 | Rules changed by this sweep (cases added for each): CI-10 treats an opt-in label `if` as gated and no longer reads a printing/uninstalling/inspecting command as a test run; CI-06 ignores commands that only mention a tool and rates npm after `setup-node` v6+ low; CI-11 skips pull request runs limited to workflow files and metadata-only workflows (`github-script`, labeler, stale) and judges the shared group only for generic keys (`github.ref`, `head_ref`, `sha`, `run_id`, PR number), low confidence; CI-19 reports once per job; the normalizer does not count a `skipped` job as a passing job run again. |
| D25 | Defaults (DQ-6): thresholds stay configurable and are our choices, stated per check: `min_runs` 5 and CI-02/03/05/12 counts 2-3 (small counts so a few real re-runs are visible; CI-01 is 2 rechecks from SRC-14), CI-18 120 s, CI-19 30 days (n8n at 31 shows how arbitrary a limit is), CI-14 8 cores. No default is claimed to come from a measurement. |
| D26 | Process honesty (tracker PH-1): the gates were not followed in order for CI. The AWS skeleton was deployed after the detectors, research was thin until challenged and not every gate ended with an MCQ. From 2026-10-10 the remaining work was done in gate order with the owner's MCQ answers recorded here (D20-D23). |
| D27 | The private tools (`mutate_c3.py`, `run_real_c3.py`, `make_stack_c3.py`) are kept in `.agents/rules/tools/` next to the Category 2 and 4 tools (that folder is git-ignored, like theirs): mutation table (always-empty and always-flag per check), real-repo runner, per-state stack generator. Plan comments are generated from the committed cases by `tests/ci_plans.py` (tracked). |

## Live AWS pass (2026-10-10, after the Frontend merge; project 768666229343, ap-south-1, identity medhansh; tracker AW-1/2/3/4/6/7/10/12)

Owner approved the writes: zip upload, stack update (only `CodeKey`), live invocations, forced DLQ failure. Read-only first: the live zip was v14; it equalled `main` (62 files) except the ordering of two registry files, so deploying main + CI removed nothing. The repo template equals the live template (change set: code-only changes; the bucket and policy lines are dynamic re-evaluations that depend on the parser's ARN).

| What | Result |
| --- | --- |
| Deploy | `lambda/owner-c-detectors-v15.zip` (2.1 MB, arm64, PyYAML `_yaml` aarch64 wheel, 30 CI modules, no tests) uploaded; change set `ci-v15` executed; stack `owner-c-python-detectors` UPDATE_COMPLETE with `CodeKey` v15. All five Lambdas python3.13/nodejs22, arm64. |
| Drift diff (AW-1) | The deployed zip is byte-identical to the local build and all 94 `owner_c/` files equal the branch `wip/category-3-ci` (empty diff). |
| Static scan (Lambda `owner-c-static-scan`) | Dry run and real publish with the smoke repo `github:owner-c-smoke/ci-detectors-smoke`: 18 results (Python and CI). **Hub readback (AW-6):** owner D's table `owner-d-findings` holds the 5 findings and 9 CI results of that publish. On the 513 real workflow files the live Lambda gave the same counts as the local run (CI-11 49, CI-06 51, CI-07 8, CI-10 21, CI-19 1). |
| Upload path (AW-2, AW-3) | `examples/client-ci/upload-ci-history.sh` against `owner-c-presign`: regional presign host, 3 PUTs HTTP 200 (no 307), manifest last; the S3 event ran `owner-c-profile-parser`, which published 6 CI results (CI-01/02/03/05/18 completed, CI-12 unavailable because the sample history has no schedule runs). |
| Other Lambdas | `owner-c-presign`, `owner-c-xray-parser` (JS-01 unavailable: no traces in the window) and `owner-c-xray-demo` invoked live. |
| DLQ (AW-4) | A forced invalid asynchronous event to `owner-c-profile-parser` ran 3 times and landed in `owner-c-parse-dlq`; the alarm `owner-c-parse-dlq-not-empty` went to ALARM; I deleted the test message afterwards. |
| Worst case (AW-7) | 20 workflow files at the 30k-line bound (15.6 MB of YAML, 1,661 jobs each): 68 s and 652 MB (limits 120 s and 1,024 MB). The 513 real files (2.8 MB, 21 repositories in one scan): 47 s, 652 MB. A 25 MB history bundle (200 workflows): 1.7 s, 205 MB. A normal scan (one repository, up to about 60 workflows) takes seconds. Limit: a corpus about twice the worst case would hit the 120 s timeout (a timeout is a failed invocation, not a clean result). |
| Runtime (AW-10) | Every path above ran on python3.13. |

| ID | Decision |
| --- | --- |
| D28 | **Bug found by the live test and fixed:** the parser drops the first path component of every repo zip member (GitHub archives have `<repo>-<sha>/`), but both client example scripts zipped from inside the repository, so `.github/workflows/ci.yml` became `workflows/ci.yml` and CI-12/CI-18 silently got no workflow (only 4 of 6 history results came back; local tests had passed files directly). Both example scripts now write `repo/<path>` and a test loads their zip through `iter_zip`; the README states the layout. `upload-profiles.sh` is Category 2 code: its flat zip lost `src/` the same way. The deployed Lambda code was not affected, only the client scripts. |
| D29 | Worst-case limits are documented, not changed: no per-invocation time guard was added (a guard would be a design change for a corpus no real repository reaches); Lambda timeout and memory stay at 120 s and 1,024 MB. |
| D30 | Cleanup done with the owner's approval (2026-10-10): the old provisional stack `owner-c-ci-detectors` was deleted (its only object, my black history smoke upload, deleted first), then its retained history bucket, `lambda/owner-c-ci-detectors-v1.zip` and `cfn/owner-c-ci-detectors.yaml`. Only `owner-c-python-detectors` (v15) carries owner C now. The smoke objects under `uploads/github%3Aowner-c-smoke...` in the shared artifact bucket expire by the 7-day lifecycle rule. |
