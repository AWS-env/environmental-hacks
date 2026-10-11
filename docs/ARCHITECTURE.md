# Architecture

What the system is, what is built today, and how it differs from the target design.
[`ARCHITECTURE_FLOWS.md`](ARCHITECTURE_FLOWS.md) holds the full target diagrams (user view, our view, AWS
view); this page describes the implemented system and links to the details.

## What it does

A read-only auditor for software compute waste. A developer connects a public repository (and optionally
their CI and cloud telemetry), and gets findings with the exact `file:line`, why it matters, how confident
we are, and a copyable prompt for their own coding agent. The user never installs our tool, grants write
access, or lets us run their code.

The checks come from the Software Compute Waste Taxonomy (251 checks, one GitHub issue each, in
[`taxonomy/`](taxonomy)). Four owners split them:

| Owner | Layers | Engine |
| --- | --- | --- |
| A | in-process code efficiency (CODE-C*) | TypeScript + tree-sitter |
| B | database, jobs, network (DB, JOB, NET) | JavaScript + tree-sitter |
| C | Python, JavaScript, CI, frontend (PY, JS, CI, FE, CODE-RT) | Python |
| D | infrastructure, LLM, observability, tests (INF, LLM, OBS, TST) | Python |

Every detector speaks the same [detector contract](DETECTOR_CONTRACT.md) v1: a pure function from an input
payload to a result with status, coverage, limitations and evidence-backed findings. A result never says
"clean" when it could not look: it says `unavailable`, `partial` or `error`.

## Implemented flow (all in ap-south-1)

```
 browser ──▶ Amplify Hosting (Next.js dashboard, web/)
    │  POST /scans {repo_url}                        GET /repos/<o>/<r>/scans/<id>
    ▼                                                          ▲
 scan-api: API Gateway HTTP API ─▶ api Lambda                  │
    │ status.json = queued; async invoke                       │
    ▼                                                          │
 worker Lambda (3008 MB, 900 s)                       hub read API (owner-d-hub-api)
    1. download repo tarball from codeload (<= 50 MB)          ▲
    2. scanner runs owners A-D, text only                      │
    3. report.json + status.json to private S3 (7-day expiry)  │
    4. PutEvents detector.result.v1 ─▶ EventBridge "findings-hub"
                                          │ rule owner-d-findings-ingest
                                          ▼
                                   findings-writer Lambda ─▶ DynamoDB owner-d-findings
                                   (re-validates, DLQ + alarm on failure)
```

1. **Dashboard.** `web/` is a Next.js app on Amplify Hosting. It calls scan-api to start and poll a scan,
   then reads the findings hub. If the S3 report has expired, the hub copy is shown instead.
2. **Scan API.** [`infra/scan-api/`](../infra/scan-api/README.md): `POST /scans` queues a scan, a worker
   Lambda runs it, `GET /scans/{id}` returns the report. A per-repository cooldown and a daily limit guard
   cost.
3. **Scanner.** [`scanner/`](../scanner) collects files as text (skipping vendored directories, binaries and
   files over 1 MB, at most 5,000 files), runs each owner's adapter, validates every result with the
   contract, and builds `report.json`.
4. **Findings hub.** [`hub/`](../hub/README.md): a detector publishes results to the `findings-hub` event
   bus, a writer validates and stores them in DynamoDB, and a read API serves them. A result counts as
   persisted only after readback returns it.

## Runtime evidence from the client's CI (optional)

Static findings only prove a pattern at a line. To confirm runtime cost, the client adds one step to their
own CI (the [one-click GitHub Action](../.github/actions/owner-c-profile-upload)). Their runner runs
py-spy, memray, Lighthouse or Node profiling and uploads the artifact through a presigned PUT to an S3
bucket. Our parser Lambdas only read and parse it, then publish a result like any other detector. We never
run client code. Owner A, B, C and D each have their own presign and parser path.

## Differences from the target design

| Target ([`ARCHITECTURE_FLOWS.md`](ARCHITECTURE_FLOWS.md)) | Implemented today |
| --- | --- |
| Region eu-north-1 | ap-south-1 (the project's assigned Region) |
| GitHub webhook on every push | not built; a scan starts when a user submits a URL (a weekly and on-push scan of this repo runs from `scan.yml`) |
| SQS scan queue and Step Functions | API Lambda invokes the worker Lambda asynchronously |
| Cognito sign-in, CloudFront and WAF in front | dashboard served by Amplify Hosting; no authorizer on the APIs yet |
| Read-only IAM role into the client's cloud | detectors read telemetry only where a stack was set up for it; no client role flow in the UI |
| Estimate engine with a range, trend and budget alerts | not built; impact is reported as `not_quantified` and no energy, CO2 or water figures are estimated |
| Fargate Spot overflow for large scans | not built; scans over the Lambda limits end in `error` |

## Guardrails

- Nothing from the scanned repository is executed, installed, built or imported.
- IAM roles are least-privilege; buckets are private with TLS-only access and 7-day expiry rules.
- Every Lambda that can fail has a dead-letter queue and an alarm.
- All Regional resources are in ap-south-1; templates refuse other Regions.

See [`SETUP.md`](SETUP.md) to run and deploy it, and [`DECISIONS.md`](DECISIONS.md) for the decision log.
