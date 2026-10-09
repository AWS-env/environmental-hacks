# AWS Service & Detector Mapping - Compute Waste Taxonomy v1.6

**Date:** 2026-10-09  |  **Base:** Software_Compute_Waste_Taxonomy_v1.5_draft.xlsx (not modified)  |  **Output:** Software_Compute_Waste_Taxonomy_v1.6_aws_mapping.xlsx

## 1. Purpose

Map every one of the 251 taxonomy rows to the AWS service/detector that produces its evidence, under the hackathon constraints: **one shared AWS Free Plan account** ($200 credits, no Organizations), **4 owner identities** (Owner A/B/C/D), namespaced resources, Owner-D hub. No row is mapped without a rule (R1-R20) and an AWS official source.

## 2. Architecture (single account, 4 owners)

```
AWS account (Free Plan, $200) - root MFA-locked
  IAM users: owner-a, owner-b, owner-c, owner-d (scoped policies, MFA)
  Shared hub (Owner D): EventBridge findings-hub | DynamoDB findings | API GW | Cognito | CloudFront
  owner-a-*: static scan Lambda, profile parsers, S3 artifacts        (CODE-C*)
  owner-b-*: log/telemetry analyzers, X-Ray, CloudWatch Logs Insights (DB/NET/JOB)
  owner-c-*: CodeBuild Lighthouse/bundle, CI connectors, static scan  (PY/JS/FE/CI)
  owner-d-*: test-smell scan, config scans, hub, Cost Explorer        (TST/OBS/LLM/INF)
```

Guardrails: every resource prefixed `owner-x-` and tagged `owner=A|B|C|D`; IAM policies scoped by ARN prefix + tag conditions; shared resources are append-only for A/B/C (PutEvents / PutItem); only Owner D edits shared resources; budget alarms at $50/$100.

## 3. Mapping rubric (R1-R21)

| Rule | Signal | AWS pattern | Confidence | Discussion |
|---|---|---|---|---|
| R1 | Detection = linters/AST/static/config scan | Lambda static scanner (semgrep/tree-sitter/config parsers) | High | No |
| R1+R2 | Static rule + profiler signal | Lambda static scan + S3 profile-artifact parser | Medium | Yes - OQ-1 (path default) |
| R1+R3 | Static rule + memory-profiler signal | Lambda static scan + S3 memory-artifact parser | Medium | Yes - OQ-1 (path default) |
| R1+R4 | Static rule + utilization telemetry | Lambda static scan + CloudWatch metrics read | Medium | No |
| R2 | Profiler/call-count/tracing signal | S3 artifact + Lambda parser | Medium | Yes - OQ-1 (path default) |
| R3 | Memory profiler signal | S3 artifact + Lambda parser | Medium | Yes - OQ-1 (path default) |
| R4 | Utilization/metrics/cardinality signal | CloudWatch APIs + Lambda analyzer | Medium | No |
| R5 | Query logs / job logs / usage logs | CloudWatch Logs Insights + Lambda analyzer | High | No |
| R6 | EXPLAIN plan signal | S3 plan artifact + Lambda parser | Medium | Yes - OQ-2 |
| R7 | APM/span/trace signal | AWS X-Ray + Lambda analyzer | Medium | No |
| R8 | Lighthouse/DevTools/browser signal | CodeBuild headless Chrome + S3 + Lambda | Medium | Yes - OQ-5 (path default) |
| R9 | Bundle analyzer signal | CodeBuild on existing build output + S3 | Medium | Yes - OQ-5 (path default) |
| R10 | CI run-history/flake/timing signal | CI provider logs -> S3 + Lambda | Medium | Yes - OQ-6 |
| R11 | Benchmark signal | Client-side A/B benchmark -> S3 artifact | Low | Yes - OQ-3 |
| R12 | Hardware counter signal | Client perf artifact -> S3 (or unavailable) | Low | Yes - OQ-4 |
| R13 | Inventory/recommendation signal | Config/Resource Explorer/Compute Optimizer (client) | Medium | Yes - OQ-7 |
| R14 | Semantic/judgement signal | Lambda heuristic + reviewer confirmation | Low | Yes - OQ-8 |
| R17 | Test duration signal | S3 timing artifact + Lambda parser | Medium | No |
| R18 | Billing/tag-only signal | Cost Explorer API (client) + Lambda | Low | Yes - OQ-14 |
| R19 | No off-the-shelf detector | Lambda custom AST/call-graph rule | Low | Yes - OQ-9 |
| R20 | Continuous/GPU profiling signal | Client profiler data -> S3 (or unavailable) | Low | Yes - OQ-10 |
| R21 | Scanner-family overflow (R1/R1R2/R1R3/R2/R3) when a scan exceeds Lambda limits | Same detector image as a Fargate Spot task (on-demand only, never always-on) | Medium | Yes - OQ-15 |

Core principle applied throughout: **static first, existing telemetry second, client-side artifacts third, our sandbox never** - no execution of user code on our AWS.

## 4. Coverage

- Rows mapped: **251/251**
- Per owner: **A** 65, **B** 63, **C** 61, **D** 62
- Confidence: High 85, Low 31, Medium 135
- Rows flagged for discussion: **140** (grouped into OQ-1..OQ-14)
- Rule usage: R1 65, R1+R10 3, R1+R4 3, R1+R5 1, R10 9, R11 3, R12 3, R13 5, R14 10, R14+R2 6, R17 1, R18 1, R19 5, R1R2 14, R1R3 6, R1R4 1, R2 20, R20 2, R3 9, R4 12, R5 23, R5+R1 1, R5+R14 1, R6 21, R7 5, R8 18, R9 3

## 5. Discussion register

| # | Decision needed | Affected rows |
|---|---|---|
| OQ-1 | Profiler artifact path: client CI runs py-spy/cProfile/memray and uploads output vs skip runtime confirmation | All R2/R3 rows (CODE-C2/C3/C6/C8/C11, PY-01/05/06/07/11, JS-01..09, CODE-RT.4/.5, DB-41, NET-12, OBS-03, TST-01) |
| OQ-2 | EXPLAIN acquisition: parse existing auto_explain/query logs vs client CI plan export vs live EXPLAIN (execution on client DB) | All DB EXPLAIN rows (DB-01/02/03/07/08/09/10/11/12/13/14/15/19/20/21/22/23/25/26/27/28/29/31/35/39/42/43/44/45/46) |
| OQ-3 | Benchmarks (A/B) run client-side vs drop from v1 | DB-17, DB-24, DB-37, NET-08, NET-10 |
| OQ-4 | Hardware counters: ingest client perf artifact vs mark unavailable | CODE-C12.1, CODE-C12.2, CODE-C12.3 |
| OQ-5 | Frontend execution location: our CodeBuild vs client CI; React interaction replay for FE-20 | All FE-*; FE-20 |
| OQ-6 | CI provider connector: GitHub Actions vs CodePipeline/CodeBuild; where run-history data comes from | CI-01, CI-02, CI-03, CI-05, CI-06, CI-07, CI-08, CI-10, CI-12, CI-16, CI-17, CI-18 |
| OQ-7 | Client read-only role for CloudWatch/Resource Explorer/Compute Optimizer; simulate in demo | CI-19, OBS-15, INF-04, INF-07, INF-10 |
| OQ-8 | Semantic/judgement rows: complexity, architecture, SLA, schema/query review, alert coverage | CODE-C3.4, C7.1..5, C11.4, PY-07, DB-18/21/32/33, NET-06, JOB-06, OBS-10/20, LLM-18, INF-05/06/14 |
| OQ-9 | Custom test-smell detectors build vs drop (taxonomy v1.5 marks candidates to drop) | TST-02, TST-03, TST-07, TST-08, TST-11 |
| OQ-10 | Continuous profiler / GPU / LLM memory profiling: ingest client data vs mark unavailable; Bedrock token cap + eligibility | OBS-19, LLM-19 |
| OQ-11 | Verify each service against AWS Free Plan eligible-services list before build | CodeBuild, X-Ray, Step Functions, EventBridge, API Gateway, CloudFront, ECR, ECS Fargate, EventBridge Scheduler, Amplify Hosting, Bedrock |
| OQ-12 | SCI boundary/inventory rows: client SCI inventory access and boundary definition | INF-12 |
| OQ-13 | Naming/tagging guardrails sign-off: owner-x- prefix + owner tag enforced by IAM conditions | Cross-cutting (all rows) |
| OQ-14 | Cost Explorer/tag-only billing rows: keep as visibility-only or drop | OBS-16 |
| OQ-15 | Fargate overflow threshold: at what repo size / scan duration does a detector move from Lambda to Fargate Spot | R1/R1R2/R1R3/R2/R3 families (no specific rows reassigned yet) |

## 6. IAM roles needed

- **Internal scanner roles** (owner-a..owner-d): scoped to `owner-x-*` ARNs; no cross-owner access.
- **Client read-only role (simulated in demo)**: assume-role from hub for CloudWatch Logs/Metrics, X-Ray, Resource Explorer/Compute Optimizer.
- **Client CI upload role (simulated)**: writes profiler/test/bundle artifacts to an `owner-x-artifacts` S3 bucket via presigned URLs or STS.
- **Hub role (Owner D)**: writes findings table, reads the event bus, serves the dashboard.
- Root user: billing only, MFA, no daily use. No long-lived access keys; CI uses OIDC where available.

## 7. Free Plan checklist (OQ-11)

Always-free (no credits consumed): Lambda (1M req + 400k GB-s/mo), DynamoDB (25 GB), SQS (1M), SNS (1M), Cognito (10k MAU), IAM/STS/Budgets.

Verify against the AWS Free Plan eligible-services list before building: EventBridge, API Gateway, CloudFront, CodeBuild, X-Ray, ECR, ECS Fargate, Step Functions, EventBridge Scheduler, Amplify Hosting, Bedrock.

Avoid on Free Plan: NAT Gateway, ALB, always-on RDS/Aurora, OpenSearch (paid, no always-free), Config (paid), Athena (paid per scan), EC2/Lightsail profiler hosts, Cost Explorer API calls beyond budget review.

## 8. Service catalog

| AWS service | Why our app uses it | Owner(s) | Free Plan note |
|---|---|---|---|
| AWS Lambda | Static scanners, artifact parsers, telemetry analyzers, hub API | A/B/C/D | Always-free 1M req + 400k GB-s/mo |
| Amazon S3 | Artifact buckets, reports, client profile uploads | A/B/C/D | Verify on Free Tier list (OQ-11) |
| Amazon DynamoDB | Shared findings table (hub) | D | Always-free 25 GB |
| Amazon EventBridge | findings-hub cross-detector bus; schedules | D (bus), all (publish) | Verify (OQ-11) |
| Amazon SQS | Scan queue, backpressure, retries | D | Always-free 1M req/mo |
| Amazon API Gateway | Dashboard/API for findings | D | Verify (OQ-11) |
| Amazon Cognito | Dashboard login | D | Always-free 10k MAU |
| Amazon CloudFront | Dashboard delivery | D | Always-free allowance (verify) |
| Amazon CloudWatch (Logs, Metrics, Logs Insights, Alarms) | Read client logs/metrics; service logs; dashboards | B/D | Limited always-free; cap retention |
| AWS X-Ray | Analyze client APM spans/traces | B/D | Verify (OQ-11) |
| AWS CodeBuild | Lighthouse, bundle analysis, browser traces | C | Verify (OQ-11); fallback client CI |
| Amazon ECR | Scanner container images | A | Verify (OQ-11) |
| Amazon ECS Fargate | Heavy detector container / scan overflow (on-demand Spot tasks only) | A/C | Verify (OQ-11); on-demand only - never always-on |
| AWS Step Functions | Multi-step scan pipelines | D | Verify (OQ-11) |
| AWS IAM | 4 owner users/roles, least privilege, namespacing | All | Free |
| AWS STS | Client read-only role assumption (simulated) | D | Free |
| AWS Systems Manager Parameter Store | Scanner configuration, model constants | D | Standard params free |
| AWS Budgets | $50/$100 alarms on the shared $200 | D | Free (2 budgets) |
| Amazon SNS | Optional finding notifications | D | Always-free 1M publishes |
| Amazon EventBridge Scheduler | Weekly deep-scan schedule; schedule inventory | B/D | Verify (OQ-11) |
| AWS Config / Resource Explorer | Client inventory checks (INF-04/10, CI-19) | D | Config is paid - use Resource Explorer/manual (OQ-7) |
| AWS Compute Optimizer | Client instance-family checks (INF-07) | D | Free service; client opt-in (OQ-7) |
| AWS Cost Explorer | Tag-only billing rows (OBS-16); own budget review | D | Console free; API ~$0.01/request (OQ-14) |
| Amazon Athena | Optional large log/plan analysis | B | Paid per TB scanned - fallback Logs Insights |
| Amazon RDS | Client-side log source only (we do not run DBs) | B | N/A - client resource |
| AWS CodePipeline | Optional client CI connector | C | Verify (OQ-11); client-owned |
| Amazon Bedrock | Optional narrative summaries (not required) | D | Paid-plan exclusive risk (OQ-10); use templates |
| AWS CloudTrail | Audit scanner actions in shared account | D | First trail free |
| Amazon Amplify Hosting | Dashboard hosting alternative to S3+CloudFront (Next.js/SPA) | D | Verify on Free Plan (OQ-11); Route 53 skipped (cost) |
| Amazon OpenSearch Service | Deferred: full-text finding search not needed for MVP | D | Deferred - paid, no always-free tier; verify pricing; revisit post-hackathon |
| Amazon Aurora (RDS) | Deferred: DynamoDB covers findings + catalog; avoids duplication | D | Deferred - credit burn if always-on; not deployed |
| AWS Verified Permissions (Cedar) | Deferred: IAM namespacing covers owner isolation for MVP | D | Deferred - eligibility/pricing not assumed; Cedar usable locally (free) |
| AWS App Runner | Optional alt engine host / PR previews | A/C | Optional - no always-free tier (verify); skip MVP |
| Amazon SageMaker AI | Optional ML scoring/ranking of findings | D | Optional - usage consumes credits; skip MVP |
| Amazon EKS | Optional multi-runner scaling demo | A | Optional - skip (cost); serverless-first |

## 9. Sources

- AWS official documentation (per-row 'Assessment / source' column): https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/welcome.html, https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html, https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html, https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/WhatIsCloudWatchLogs.html, https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/WhatIsCloudWatch.html, https://docs.aws.amazon.com/AmazonECR/latest/userguide/what-is-ecr.html, https://docs.aws.amazon.com/AmazonECS/latest/developerguide/Welcome.html, https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/CHAP_AuroraOverview.html, https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html, https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html, ...
- Taxonomy v1.5: detection methods, categories, evidence rules (unchanged; see workbook).
- AWS Free Tier terms and plan comparison (aws.amazon.com/free, docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).

## 10. Build & Deploy (corrected)

Capability map as adopted, with the four scope/budget corrections applied (no untrusted-code sandbox, no profiler hosts, no OpenSearch/Aurora, Fargate on-demand overflow only) and operational guardrails added (IAM/STS/Budgets/SSM).

| Capability / Step | AWS service(s) / Tool | Where it fits | Tier | v1.6 note |
|---|---|---|---|---|
| Frontend hosting | Amplify Hosting + CloudFront (Route 53 optional) | Next.js/SPA dashboard, global CDN | Core | Route 53 skipped (cost) - use CloudFront default URL; S3+CloudFront is the $0 fallback; verify Amplify on Free Plan (OQ-11) |
| Edge / API | API Gateway + Lambda | Upload/status/webhook APIs | Core | Verify API Gateway on Free Plan (OQ-11) |
| Engine runtime | ECS Fargate (heavy detector container) + Lambda | Detectors and artifact parsers | Core | Fargate Spot on-demand tasks ONLY; light detectors stay on Lambda; never an always-on service (R21/OQ-15) |
| Pipeline orchestration | Step Functions | ingest -> detect -> score -> report state machine | Core | Verify on Free Plan (OQ-11) |
| Async jobs | SQS + SNS + EventBridge | Scan queue, notifications, scheduled rescans | Core | SQS/SNS always-free; EventBridge verify (OQ-11) |
| Object store | S3 | Uploaded repos/artifacts/reports/catalog | Core | Prefer Git read-only connection over repo upload where possible |
| Findings store | DynamoDB | Runs + findings (fast, serverless) | Core | Always-free 25 GB |
| Catalog / relational | DynamoDB (+ S3) | Taxonomy catalog + finding lookup | Core | Aurora rejected: duplication + credit burn |
| Search | DynamoDB GSIs (+ S3/Athena later) | Finding lookup for MVP | Core | OpenSearch rejected: paid, no always-free, tens of $/mo risk; defer post-hackathon |
| LLM detection + summary | Bedrock (optional) | LLM-* layer detection + report summarization | Core (optional) | Token cap + template fallback; eligibility not assumed (OQ-10/OQ-11) |
| Auth + policy | Cognito + IAM | User auth + per-owner isolation | Core | Cedar/Verified Permissions deferred (eligibility not assumed); IAM namespacing for MVP |
| Observability | CloudWatch (+ X-Ray) | Logs/metrics/dashboards; dogfoods the OBS-* layer | Core | Cap log retention 3-7 days; verify X-Ray (OQ-11) |
| Operational guardrails | IAM + STS + Budgets + SSM | 4 owner identities, temp creds, $50/$100 alarms, config | Core | Added - missing from the original draft table |
| Sandbox for untrusted repos | - (removed) | No execution of untrusted repo code | Removed | Violates read-only scope; Tier-3 evidence via client CI artifacts (OQ-1) |
| Long-running profilers | - (removed) | Client CI runs py-spy/--prof; we parse artifacts | Removed | EC2/Lightsail profiler host dropped (scope + credit burn) |
| DEPLOY ORDER - Step 1 (Day 0) | IAM + STS + Budgets + SSM | Root MFA, owner-a..d users, $50/$100 budget alarms, config params | Step | Before anything else; protects the $200 |
| DEPLOY ORDER - Step 2 | HubStack (CDK) | EventBridge findings-hub, DynamoDB findings, S3 reports, Cognito, API GW, dashboard | Step | Owner D deploys; produces the demo URL |
| DEPLOY ORDER - Step 3 | OwnerA/B/C/D stacks (CDK) | Detector Lambdas, artifact buckets, bus publish rules | Step | Each owner deploys their own stack; no collisions |
| DEPLOY ORDER - Step 4 | Demo target stack | App Runner or EC2 t3.micro + RDS db.t3.micro demo app (deliberately wasteful) | Step | Generates real telemetry for the demo (DB/NET/OBS/INF evidence) |
| DEPLOY ORDER - Step 5 | GitHub OIDC roles | Per-owner deploy roles, path-filtered workflows | Step | No long-lived keys; PR = synth + tests, main = deploy |
| TOOLCHAIN - IaC | AWS CDK v2 | 5 stacks (Hub + OwnerA..D); guardrail constructs enforce owner-x- prefix + tags | Dev | Recommended; SAM is an acceptable alternative - not both |
| TOOLCHAIN - local dev | LocalStack + SAM CLI + Finch | Local Lambda/S3/SQS/EventBridge tests + container image builds | Dev | No cloud waste; dev only, never deployed |
| TOOLCHAIN - CI/CD | GitHub Actions + OIDC | Path-filtered deploys per owner; synth on PR | Dev | Free minutes; no stored keys |
| UNCERTAIN - Free Plan eligibility | Amplify Hosting, CodeBuild, X-Ray, Step Functions, EventBridge, API Gateway, CloudFront, ECR, Fargate, Scheduler, Bedrock | Verify against the AWS Free Plan eligible-services list before build | Verify | OQ-11 - do not assume; if ineligible, use the stated fallback |
| UNCERTAIN - Bedrock | Bedrock | LLM-* layer + summaries are optional | Verify | OQ-10 - token cost + plan eligibility unverified; template fallback ready |
| UNCERTAIN - Verified Permissions | Cedar / Verified Permissions | Fine-grained authZ deferred; IAM namespacing for MVP | Verify | Pricing/eligibility not assumed |
| UNCERTAIN - Fargate overflow threshold | Lambda -> Fargate Spot | When a scan exceeds Lambda 15 min / 10 GB | Verify | OQ-15 - threshold to be set; no specific rows reassigned |

Explicit uncertainties (not assumed): Free Plan eligibility of Amplify Hosting / CodeBuild / X-Ray / Step Functions / EventBridge / API Gateway / CloudFront / ECR / Fargate / Scheduler / Bedrock (OQ-11); Bedrock token cost + eligibility (OQ-10); Verified Permissions pricing/eligibility (deferred); Fargate overflow threshold not set, so no taxonomy rows were reassigned (OQ-15).

## 11. Changelog

- 2026-10-09 - v1.6 AWS mapping created from v1.5. 6 new columns in MASTER + Owner views; 5 new sheets; 251/251 rows mapped; discussion flags grouped OQ-1..OQ-15; no source row altered.
- 2026-10-09 - v1.6a Build & Deploy adopted (corrected): removed untrusted-code sandbox, EC2/Lightsail profiler hosts, OpenSearch, Aurora; Fargate = on-demand overflow (R21); added IAM/STS/Budgets/SSM; toolchain CDK + GitHub OIDC + LocalStack/SAM/Finch.
