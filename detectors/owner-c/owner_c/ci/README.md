# Owner C detectors - CI (Category 3)

Detectors for the CI/CD waste checks (CI-01 .. CI-19) on **GitHub Actions**. They implement the shared detector
contract v1 ([`docs/DETECTOR_CONTRACT.md`](../../../../docs/DETECTOR_CONTRACT.md)): one result per check, explicit
coverage, evidence that quotes exact source lines or one field of normalized run history. Workflow YAML and run
history are only **parsed**; nothing from the client is ever executed (`docs/ARCHITECTURE_FLOWS.md`).

Research: [`docs/research/category-3-ci.md`](../../../../docs/research/category-3-ci.md). Decisions:
[`docs/research/category-3-decisions.md`](../../../../docs/research/category-3-decisions.md).

## Two evidence routes

| Route | Source | Checks |
| --- | --- | --- |
| **static** | `.github/workflows/*.yml\|yaml` in the repo (PyYAML, line spans kept for evidence) | CI-06, 07, 08, 10, 11, 13, 14, 16, 19 |
| **history** | GitHub Actions run history collected by the **client** with `python -m owner_c.ci.collector`, uploaded to S3 by presigned PUT, normalized here | CI-01, 02, 03, 05 |
| **static candidate + history** | a workflow pattern, reported only when history confirms it (files without history are not evaluated) | CI-12, CI-18 |

Not implemented: **CI-15** (needs live runner utilization) and **CI-17** (cannot be proven from YAML or run history);
their issues stay open. CI-04 and CI-09 have no taxonomy row or issue.

## Layout

```
owner_c/ci/
  yamlio.py          PyYAML loader that records the (start, end) line span of every entry
  workflow.py        Workflow / Job / Step model, triggers, evidence quoting
  patterns.py        command patterns shared by checks (test, build, installers, gating conditions)
  checks/            static checks, one module per check (+ registry in __init__.py)
  history_checks/    history and history-confirmed checks, one module per check (+ registry)
  normalize/         raw collector JSON -> contract artifact data (github_actions.py)
  collector.py       CLIENT-side collector (one file, stdlib only); never run by us
  connector.py       workflow files + history -> contract inputs; declares limits in `context`
  runner.py          evaluate(input) -> result; pure, no AWS, no execution
  cli.py             scan a local checkout / evaluate an input
```

## Run

```bash
pip install pyyaml jsonschema rfc3339-validator          # from the repository root
# scan a checkout's workflows (+ optional run history); every result is validated against the shared contract
PYTHONPATH=detectors/owner-c python -m owner_c.ci scan path/to/repo
PYTHONPATH=detectors/owner-c python -m owner_c.ci scan path/to/repo --history history.json --json
# client side: collect run history for one workflow (read-only token with `actions: read`)
GITHUB_TOKEN=... PYTHONPATH=detectors/owner-c python -m owner_c.ci.collector --repo owner/name --workflow ci.yml > history.json
# tests: verification cases + unit tests + real-capture tests, then the shared contract suite
PYTHONPATH=detectors/owner-c python -m unittest discover -s detectors/owner-c/tests
python -m shared.contracts.verify
```

## Input, scope and coverage

- Scope is one item per workflow file: `file:.github/workflows/<name>.yml`. History checks cite the same scope id.
- A workflow that cannot be parsed, or has no `jobs` mapping, is **not evaluated** (limitation, never "clean").
- Run-history checks need an artifact for the workflow and at least `min_runs` runs; fewer is `unavailable`.
- Thresholds live in `context` (validated; missing/invalid makes the check `unavailable`).
- Identity is a semantic anchor without line numbers (`job:<id>:deps:npm`, `workflow:no-cancel-superseded`, ...);
  a repeated anchor in one file gets `#2`. Fingerprint is the shared SHA-256 of `[repo, check, scope, identity]`.
- No environmental values are invented: results carry no measurements.

## Static checks

- **CI-06** a job installs dependencies (npm/yarn/pnpm, pip from a requirements file or the project, poetry, pipenv,
  maven, gradle, bundler, dotnet, cargo) with no cache in the job. Not flagged: local/composite actions, self-hosted
  runners, single-tool installs (`pip install build`), `npm install -g`.
- **CI-07** `docker/build-push-action` or `docker build` without layer cache flags; `docker compose build`/`up --build`
  and `docker/bake-action` without a cache in the step (low: the compose and bake files are not read). Not flagged:
  `no-cache`, cache flags via a variable, a second build of the same context/file in one job.
- **CI-08** a build that runs `clean` right after restoring a build-output cache (target/build/dist/out). Explicit
  `--no-build-cache`/`--rerun-tasks` are not reported (deliberate on every real file checked); a cache keyed per run is a
  hand-off between jobs; `.gradle` is not removed by `clean`; release/deploy jobs and tag workflows are exempt.
- **CI-10** tests on every pull request (or unfiltered push) with no path filter or test selection. Not flagged:
  path filters, `--onlyChanged`/`nx affected`, jobs or steps gated by an `if` on changes/outputs, installing a test tool.
- **CI-11** push+pull_request double runs; pull request workflows with no `concurrency`; `concurrency` without
  `cancel-in-progress`; a cancelling group made only of context expressions without the workflow name (for example
  `${{ github.ref }}`). Not flagged: `workflow_call`, `pull_request_target`, `pull_request` types without
  `synchronize`, deployments, explicit `cancel-in-progress: false`, expressions.
- **CI-13** duplicate matrix axis values and no-op `include` entries. Wide matrices are deliberately not reported.
- **CI-14** larger runner (`-N-cores`, macOS `-large`/`-xlarge`) on a light job (lint/format/docs...; a job named with test/build/clippy is not light). Low confidence:
  larger-runner labels are user-defined.
- **CI-16** a package-manager cache (npm/pnpm/pip/Maven/Gradle/cargo/go stores, node_modules) whose `actions/cache` key never
  changes (no `hashFiles`, sha, run id, version number), or that lacks an OS part in an OS matrix for platform-specific
  content. Tool and data caches (Sonar, AVD, fonts, binaries) and portable stores (`~/.m2`) are not judged.
- **CI-19** `actions/upload-artifact` with `retention-days` above `max_retention_days` (30). An unset value is not
  reported (D20: the 90-day default is not "forever" and was pure noise on real repositories).

## History checks

Cited fields come from the normalizer (`normalize/github_actions.py`); a field is only produced from data the
collector fetched (schema v2: every attempt with its own conclusion and all its jobs, with `created_at`). GitHub facts the
rules rely on were measured in a controlled lab, because the docs do not state them: every attempt lists ALL jobs; a job
that was not re-executed keeps the earlier attempt's timestamps (`started_at < created_at`); `action_required` is the
first-time-contributor approval gate and `cancelled` is a cancellation (neither is a failure).

- **CI-01** `max_rechecks_per_sha >= min_rechecks` (2): a pull request commit was re-run several times after FAILED attempts. Approval-gated (`action_required`) and cancelled attempts are not rechecks. Also `reran_passing_jobs >= min_reran_passing_jobs` (5): passing jobs executed again after a failure (CI-04, absorbed into CI-01 by the taxonomy).
- **CI-02** `repeat_failed_reruns >= min_repeat_failures` (2): a re-run after a failure in which a failed job was re-executed and failed again (a failure only carried over does not count).
- **CI-03** per job, `fail_then_pass:<job> >= min_flaky_occurrences` (2): failed then passed on the same commit.
- **CI-05** `fail_then_pass_runs >= min_brown_runs` (3): an attempt of a run failed and a later attempt of the same run succeeded.

CI-01, CI-02 and CI-05 follow the repeated-build ("recheck") study SRC-14 (arXiv 2308.10078).

## AWS: part of the shared owner-C system (decision D15)

CI has no AWS resources of its own. It plugs into what the other owner-C categories deployed
(`cdk/owner-c/python-detectors.yaml`, project Region ap-south-1, owner D's `findings-hub`):

- **Static CI checks** run inside the shared `owner-c-static-scan` Lambda (`owner_c/aws/handler.py`), which already loads
  every file of the repository zip; the handler also calls the CI connector for `.github/workflows/*.y(a)ml`.
- **Run history** is the artifact type `ci_history` in the normalizer registry (`owner_c/normalize/ci_history.py`).
  The client runs the collector, then uploads `ci_history.json` next to `repo.zip` and `manifest.json` with the shared
  presign -> manifest -> `owner-c-profile-parser` flow (regional presign URLs, S3 trigger, DLQ and alarm included). The
  parser evaluates CI-01/02/03/05 and, with `repo.zip`, CI-12/18.
- **Unified scanner** (`scanner/adapters/owner_c.py`) runs the CI static checks too and reports the history checks as
  `unavailable` (a repository scan has no run history).
- One code package and one zip (`scripts/build-owner-c-lambda.sh`, PyYAML pinned in `detectors/owner-c/requirements.txt`).
  Every contract result is published as one event (`detail-type: detector.result.v1`, source `owner-c.detectors`); a
  result too large for one event is published as an explicit `error` result for that check.

Client side, proven as a real CI step in a private lab repository with only the built-in `GITHUB_TOKEN`
(`permissions: actions: read`):

```yaml
permissions: {actions: read, contents: read}
steps:
  - uses: actions/checkout@v4
  - env: {GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}"}
    run: python collector.py --repo "$GITHUB_REPOSITORY" --workflow ci.yml --workflow release.yml > ci_history.json
```

`collector.py` is one file with no dependencies besides the standard library (download it from this repository, pinned to
a commit). It follows pagination, waits out short rate limits and explains auth and not-found errors. Upload with
`examples/client-ci/upload-ci-history.sh <repository_id> <sha> <repo_dir> ci.yml release.yml` (collect, zip, presign, upload
`repo.zip` and `ci_history.json`, manifest last; presigned URLs are never printed).

## For consumers of the results (owner D's hub, dashboards)

- **Events:** one `detector.result.v1` event per check and batch of files, `source` `owner-c.detectors` (the hub rule matches the
  `owner-` prefix), `detail` = a full contract v1 result, at most 240 KB (a larger result is published as an explicit `error`
  result for that check, never dropped).
- **Scope:** one item per workflow file, `file:.github/workflows/<name>.yml`. Identity is `job:<id>:...` or `workflow:...` (no line numbers);
  a repeated identity in one file gets `#2`. Findings carry `static` evidence (exact quoted lines) or `artifact` evidence (one field
  of the normalized run history). The hub stores inline owner-C evidence as `unverified` because the input is not sent with the event.
- **Status meaning:** `completed` = every requested workflow file was evaluated. `unavailable` = nothing was evaluated: a history
  check without a `ci_history` upload, with fewer than `min_runs` runs, an unparsable or too-large workflow file. `error` = the result
  was too large to publish. **`unavailable` and `error` are never "clean".** History checks appear only when the client uploaded
  `ci_history`; the unified scanner reports them as `unavailable`.
- **Declared limits:** every payload's `context.not_scanned` lists composite actions, reusable workflows in other repositories and
  CI providers other than GitHub Actions. Thresholds are in `context` (`min_runs`, `min_rechecks`, ...).

## Security and privacy

- Workflow YAML and run history are **parsed only**. Parsing is bounded (lines, line length, nesting, node count, command length) so a
  hostile file is `unavailable`, not a stall. Nothing a client uploads is executed.
- The collector needs only `permissions: actions: read` (the job's own `GITHUB_TOKEN` is enough); the token never leaves the client's
  job and is never uploaded or stored by us.
- **What is collected:** per run its id, event, head SHA, conclusion and timestamps; per attempt its conclusion; per job its name,
  conclusion and timestamps. No logs, no step output, no secrets, no user names or emails. Uploaded objects expire after 7 days
  (S3 lifecycle) and are encrypted at rest.
- An uploaded `ci_history` bundle must name the repository it was collected for; one for another repository is rejected. Who may
  request upload URLs for a repository is decided by IAM access to the presign Lambda (owner D's API layer should bind this to the
  authenticated user).
- Dependency: PyYAML pinned to one version in `detectors/owner-c/requirements.txt` (no hash pinning).

## Scope limits (stated, not hidden)

GitHub Actions only. `.github/workflows/*.y(a)ml` in the repository; composite actions and reusable workflows from other repositories
are not read. Static findings prove a pattern in the file, not measured waste. CI-15 and CI-17 are not implemented; there are no
issues for CI-04 and CI-09 (the taxonomy folded them into CI-01 and CI-08).

## Versioning

Every check has `DETECTOR_VERSION` (now `1.0.0`, nothing released yet). Once a version is merged, any change to what a check reports
(rule, threshold default, identity) bumps it, because the contract only treats findings as comparable between scans with the same
detector version; cosmetic changes (summary wording, limitations) do not.

## Verification

`tests/fixtures/ci_NN/ci_cases.json` hold the committed verification cases that each issue's *Verification plan* comment
is generated from (`tests/ci_plans.py`); `tests/test_ci_cases.py` runs them through the shared contract validator.
`tests/test_ci_real.py` checks the normalizer and history checks against real GitHub Actions captures in
`tests/fixtures/real_ci/` (psf/black, numpy/numpy, tiangolo/sqlmodel, fastapi/fastapi). The cases fail against both
an always-empty and an always-flag implementation. The static checks were also run on 82 real workflow files (pandas,
flask, fastapi, next.js, home-assistant) and hits were hand-checked; the false positives found are now cases.
