# Architecture Flows

The three finalized views of the Read-Only Software Sustainability Auditor. Each
diagram is followed by a one-to-two line explanation of what it shows.

---

## 1. User POV — end-to-end journey

What the developer experiences: a one-time read-only connect (repo + cloud), then
hands-off operation where pushes refresh findings and a periodic job refreshes
estimates. The user never runs our tool, never grants write access, and never lets
us run their code.

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              USER POV                                       │
└───────────────────────────────────────────────────────────────────────────┘

  ONCE — SETUP (a few minutes, all read-only)
  ───────────────────────────────────────────
   1. Sign in ─────────────────────────────────▶ Dashboard (web)
   2. Connect repo   [OAuth / GitHub App, RO] ─▶ we get code + deps + config
   3. Connect cloud  [read-only IAM role]    ─▶ we get metrics / logs / traces
   4. Enter inputs   (region, instance, workload, functional unit R)
                                                 │
                                                 ▼
   5. FIRST SCAN runs ─▶ Findings (file:line, why, confidence, refs)
                      ─▶ Estimate (a RANGE) + trend
                      ─▶ [Copy prompt] button for their own coding agent

  THEN — STEADY STATE (hands-off)
  ───────────────────────────────
   6. Every `git push`  (automatic via webhook)
         • findings re-scanned automatically
         • estimate re-computed ONLY if it actually moved
           (otherwise shows "no material change")

   7. Nightly / weekly  (periodic)
         • estimate + trend refreshed
         • alert if you cross your carbon budget

   8. OPTIONAL — only for runtime-confirmed findings
         add ONE line to your CI  ─▶ your runner profiles your code
                                 ─▶ uploads artifact ─▶ we parse it

  WHAT THE USER NEVER DOES
  ────────────────────────
   ✗ never installs/runs our tool locally
   ✗ never grants us write access to repo or cloud
   ✗ never lets us run their code
```

---

## 2. Our POV — platform-agnostic technical flow

Shows the trust boundary explicitly: the client side only emits events, artifacts,
and telemetry; our side only ingests, parses, and reads. Findings and estimates run
on two separate clocks (per-push vs trigger + periodic).

```
┌───────────────────────────────────────────────────────────────────────────┐
│                               OUR POV                                       │
└───────────────────────────────────────────────────────────────────────────┘

 CLIENT SIDE (nothing executes here for us)          │      OUR SIDE (read-only)
 ──────────────────────────────────────────         │   ───────────────────────
                                                     │
 GitHub repo ──webhook(push)──────────────────┐      │
                                              │      │
 Client CI ──(optional collector)──artifact──┐│      │
        (their runner runs profiler/EXPLAIN) ││      │
                                             ││      │
 Client AWS ──(read-only role)──────────────┐││      │
        metrics/logs/traces                │││      │
                                            ▼▼▼      │
                                   ┌─────────────────┴──────┐
                                   │  API Gateway (edge)     │
                                   └───────────┬─────────────┘
                                               │ enqueue
                                               ▼
                                   ┌────────────────────────┐
                                   │  SQS scan queue         │
                                   └───────────┬─────────────┘
                                               ▼
                       ┌───────────────────────────────────────────┐
                       │  Step Functions  ingest→detect→score→report│
                       └──┬──────────────┬──────────────┬──────────┘
                          │              │              │
                 ┌────────▼──────┐ ┌─────▼───────┐ ┌────▼─────────────┐
                 │ Static scan   │ │ Artifact    │ │ Telemetry        │
                 │ Lambda        │ │ parser      │ │ analyzer Lambda  │
                 │ semgrep/      │ │ Lambda      │ │ CloudWatch/X-Ray │
                 │ tree-sitter   │ │ (S3)        │ │ (read-only)      │
                 └────────┬──────┘ └─────┬───────┘ └────┬─────────────┘
                          │              │              │
                          └──────────────┼──────────────┘
                                         ▼
                       ┌────────────────────────────────┐
                       │ findings-hub                    │
                       │ EventBridge ─▶ DynamoDB         │
                       └───────────────┬─────────────────┘
                                       │
                    ┌──────────────────┴───────────────────┐
                    ▼                                       ▼
        ┌────────────────────────┐             ┌───────────────────────┐
        │ ESTIMATE ENGINE        │             │ Dashboard API          │
        │ • cached input keys    │             │ Amplify/CF + API GW    │
        │ • model version        │             └──────────┬────────────┘
        │ • materiality gate     │                        ▼
        │ • range + provenance   │                  User dashboard
        └───────────┬────────────┘
                    │  ▲
       trigger ─────┘  └───── periodic
    (input key changed)   ┌────────────────────┐
                          │ EventBridge         │
                          │ Scheduler (night/wk)│
                          └──────────┬──────────┘
                                     ▼
                          ┌────────────────────┐
                          │ SNS  ─▶ budget alert│
                          └────────────────────┘

 LEGEND
 ──────
   RO   = read-only
   R    = functional unit (per request / build / run)
   Two clocks: findings = every push | estimate = trigger + periodic
   Boundary: nothing from the client's side is executed on our infra
```

---

## 3. AWS flow — services in one account (eu-north-1)

The same intent mapped to AWS: edge (CloudFront + Amplify + API Gateway + Cognito),
orchestration (SQS + Step Functions + EventBridge Scheduler), the four detect
Lambdas (static / artifact / telemetry / estimate), the hub (EventBridge →
DynamoDB → SNS), Fargate Spot as parsing-only overflow, and the read-only client
role plus our account guardrails.

```
  TRUST BOUNDARY ──────────────────────────────────────────────────────────────
  CLIENT SIDE  (we NEVER execute here)         │  OUR SIDE (ingest + analyze)
  ──────────────────────────────────────       │  ─────────────────────────────
                                               │
  GitHub repo ──push webhook───────────────────┼──▶ API Gateway (HTTP API)
                                               │      │  + HMAC verify
  Client CI ──OPTIONAL 1-line collector────────┼──▶ API Gateway
    (their runner runs py-spy / memray /       │      │  (presigned handshake)
     EXPLAIN export / Lighthouse)              │      ▼
        │ produces artifact                   │   S3 artifact bucket
        └──upload (presigned PUT)──────────────┼──▶  (client PUTs, we GET-read)
                                               │
  Client cloud ──read-only IAM role────────────┼──▶ Lambda telemetry-reader
    (CloudWatch metrics+Logs Insights,         │      uses STS AssumeRole
     X-Ray, Compute Optimizer,                 │
     Cost Explorer, Resource Explorer)         │
  ─────────────────────────────────────────────┴────────────────────────────────

                         ┌──────────────────────────────────────────┐
   user browser ─HTTPS─▶  │ CloudFront (global) + AWS WAF (optional)  │
                         └───────┬──────────────────────────┬───────┘
                                 │ dashboard (SSR/static)   │ /api/*
                                 ▼                          ▼
                    ┌────────────────────────┐   ┌─────────────────────────────┐
                    │ Amplify Hosting        │   │ API Gateway HTTP API        │
                    │ (Next.js dashboard)    │   │ + Cognito JWT authorizer    │
                    │ eu-north-1             │   │ + GitHub webhook route      │
                    └────────────────────────┘   └───────────────┬─────────────┘
                                                                 │
                    ┌────────────────────────────────────────────┼──────────────────┐
                    │              INGEST LAMBDAS                │                  │
                    │  webhook-receiver · dashboard-api · oauth-connect            │
                    └──────┬───────────────────────┬─────────────┬─────────────────┘
                           │ enqueue               │ presign     │ store creds
                           ▼                       ▼             ▼
                   ┌───────────────┐      ┌──────────────┐  ┌──────────────────┐
                   │ SQS scan-q    │      │ S3 artifacts │  │ Secrets Manager  │
                   │ (+ DLQ)       │      │ (RO after    │  │ (GitHub app key) │
                   └───────┬───────┘      │  upload)     │  └──────────────────┘
                           │              └──────┬───────┘
   EventBridge Scheduler   │                     │
   (nightly / weekly)──────┴──────────┬──────────┘
                                       ▼
             ┌───────────────────────────────────────────────────────┐
             │  Step Functions (Express)  ingest→detect→merge→        │
             │  score→report→notify                                   │
             └──┬────────────┬─────────────┬─────────────┬───────────┘
                │            │             │             │
                ▼            ▼             ▼             ▼
          ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐
          │ STATIC   │ │ ARTIFACT │ │ TELEMETRY  │ │ ESTIMATE     │
          │ SCAN     │ │ PARSER   │ │ READER     │ │ ENGINE       │
          │ Lambda   │ │ Lambda   │ │ Lambda     │ │ Lambda       │
          │ semgrep/ │ │ S3 JSON/ │ │ STS Assume │ │ input-key    │
          │ tree-    │ │ logs;    │ │ Role →     │ │ cache +      │
          │ sitter + │ │ parse    │ │ client acct│ │ materiality  │
          │ config   │ │ only     │ │ (read)     │ │ gate + range │
          └────┬─────┘ └────┬─────┘ └─────┬──────┘ └──────┬───────┘
               └────────────┴──────┬──────┴───────────────┘
                                   ▼
                    ┌──────────────────────────────────┐
                    │ EventBridge bus  "findings-hub"   │
                    └───┬──────────────┬───────────────┘
                        ▼              ▼
                 ┌────────────┐  ┌──────────────┐   ┌───────────────────┐
                 │ DynamoDB   │  │ DynamoDB     │   │ SNS               │
                 │ findings   │  │ estimates    │   │ budget/threshold  │
                 │ (hub)      │  │ (history)    │──▶│ alerts            │
                 └─────┬──────┘  └──────┬───────┘   └─────────┬─────────┘
                       └────────┬───────┘                     ▼
                                ▼                     email / Slack / webhook
                         dashboard-api → CloudFront → user

  OVERFLOW ONLY (scan > Lambda 15 min / 10 GB — parsing, never execution):
      Step Functions ─▶ ECS Fargate Spot  RunTask (on-demand, image in ECR)

  OUR OWN FOOTPRINT (guardrails):
      IAM least-priv roles · KMS · SSM Parameter Store · CloudTrail (audit)
      AWS Budgets (spend cap) · CloudWatch alarms/dashboards · X-Ray (trace)
      CodeBuild (builds OUR detector images only — not user code)
```
