# Setup

How to run, test and deploy the Read-Only Software Sustainability Auditor. Everything here is read
from the repository and its CI, so the commands match what the `build` job runs. For how the pieces fit
together see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| Python | 3.12 or newer | contract validation, scanner, owner C and D detectors, scan API, findings hub |
| Node.js | the version in [`.nvmrc`](../.nvmrc) (22) | owner A and owner B detectors, the `web/` dashboard |
| Git | any recent | cloning scanned repositories, the contribution workflow |
| AWS CLI v2 | any recent | only for deploying or inspecting AWS state |

The scanner only reads files as text. It never executes, installs, builds or imports the scanned code.

## 1. Run the checks locally

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r shared/contracts/requirements.txt
.venv/bin/python -m shared.contracts.verify                      # contract suite (CI always runs it)

.venv/bin/pip install -r detectors/owner-c/requirements.txt
PYTHONPATH=detectors/owner-c .venv/bin/python -m unittest discover -s detectors/owner-c/tests
PYTHONPATH=detectors/owner-d .venv/bin/python -m unittest discover -s detectors/owner-d/tests -t detectors/owner-d
.venv/bin/python -m unittest discover -s tests/scanner
.venv/bin/python -m unittest discover -s tests/scan_api

npm ci                                                           # owner B (php-parser) and root tooling
npm run lint && npm run typecheck && npm test
npm --prefix detectors/owner-a ci
npm --prefix detectors/owner-a run typecheck && npm --prefix detectors/owner-a test
```

On Windows use `.venv\Scripts\python` and `.venv\Scripts\pip` instead of `.venv/bin/...`.

## 2. Scan a repository from the command line

```bash
npm --prefix detectors/owner-a run build                         # owner A needs its tree-sitter build once

.venv/bin/python -m scanner scan https://github.com/aws/aws-sam-cli -o report.json
.venv/bin/python -m scanner scan path/to/local/repo -o report.json --owners C,D
```

The input is a public `https://github.com/<owner>/<repo>` URL or a local directory. If Node.js or the
owner A build is missing, that owner is reported as `unavailable`, never as clean. The README describes the
report format and the collection limits.

## 3. Run the web dashboard

```bash
cd web
npm ci
cp .env.example .env.local        # then fill in the two URLs below
npm run dev                       # http://localhost:3000
```

| Variable | Value | When unset |
| --- | --- | --- |
| `NEXT_PUBLIC_SCAN_API_URL` | stack output `ScanApiUrl` of the `scan-api` stack | the Analyze flow says scanning is not configured |
| `NEXT_PUBLIC_HUB_API_URL` | stack output `HubApiUrl` of the `owner-d-hub-api` stack | results come from the scan report only |

Use the base URL with no trailing path. Next.js inlines these at build time, so set them before
`npm run build`. On Amplify Hosting, set them under App settings > Environment variables (the app root is
`web`). Never put secrets in a `NEXT_PUBLIC_*` variable.

There is also a static, no-build dashboard: open `dashboard/index.html` in a browser, or load your own
`report.json` with its **Load report.json** button.

## 4. AWS setup

All resources live in one AWS project and one Region, **ap-south-1**. Confirm your Region under AWS
Settings > View all projects > Overview > Additional Info > Region, or in `~/.aws/config`. Several
templates refuse any other Region.

Each team member uses their own local AWS login and profile; never share credentials or access keys.
Agents must follow [`AWS_AGENT_WORKFLOW.md`](AWS_AGENT_WORKFLOW.md): ask before using AWS tools, verify
the identity and Region, inspect read-only first, and explain any write before doing it.

```bash
aws sts get-caller-identity --region ap-south-1     # check the identity before anything else
```

| Piece | Template or source | Build and deploy notes |
| --- | --- | --- |
| Scan API | [`infra/scan-api/template.yaml`](../infra/scan-api/template.yaml) | [`infra/scan-api/README.md`](../infra/scan-api/README.md); zip from `scripts/build-scan-api-lambda.sh` |
| Findings hub and read API | [`cdk/owner-d/`](../cdk/owner-d) | [`hub/README.md`](../hub/README.md) |
| Owner A detectors | [`cdk/owner-a/owner-a-detectors.yaml`](../cdk/owner-a/owner-a-detectors.yaml) | `scripts/build-owner-a-lambda.sh`, `scripts/check-owner-a-deploy.sh` |
| Owner B detectors | [`infra/owner-b/`](../infra/owner-b) | [`infra/owner-b/README.md`](../infra/owner-b/README.md), `scripts/build-owner-b.cjs` |
| Owner C detectors | [`cdk/owner-c/python-detectors.yaml`](../cdk/owner-c/python-detectors.yaml) | `scripts/build-owner-c-lambda.sh`, [`detectors/owner-c/README.md`](../detectors/owner-c/README.md) |
| Owner D detectors | [`cdk/owner-d/`](../cdk/owner-d) | `scripts/build-owner-d-*.sh` |

Deploy order for a fresh project: findings hub, then the detector stacks, then the scan API, then the
hub read API, then the dashboard with the two URLs set. Templates are plain CloudFormation, so no CDK
bootstrap is needed. Check a change set before executing it, and invoke each Lambda once afterwards.

Cost and cleanup: the stacks use Lambda, S3 with expiry rules, SQS dead-letter queues and DynamoDB, with no
VPC, NAT gateway or always-on compute. Scan reports and uploaded artifacts expire after 7 days. Delete a
stack with `aws cloudformation delete-stack` when you no longer need it, after emptying any versioned
bucket it owns. If resources suddenly return Access Denied, check for a spend limit in AWS Settings >
Billing.

## 5. Contribute

Issue first, one issue per PR, squash-merge only. Branch names are `<issue>-<type>-<slug>` or
`owner-<x>/<issue>-<type>-<slug>`, commits read `#<issue> | type(scope): summary`, and a PR needs a linked
issue. See [`WORKFLOW.md`](WORKFLOW.md), [`BRANCHING.md`](BRANCHING.md) and
[`../CONTRIBUTING.md`](../CONTRIBUTING.md). A detector must follow
[`DETECTOR_CONTRACT.md`](DETECTOR_CONTRACT.md) and the [`VERIFICATION_PLAN.md`](VERIFICATION_PLAN.md).
