# Scan API (API Gateway HTTP API + Lambda + S3)

Async HTTP API the frontend uses to scan a public GitHub repository with [`scanner/`](../../scanner).
Scans take 20-150 s, longer than API Gateway's 29 s integration limit, so `POST` queues a scan and the
frontend polls `GET` until the scan is `done` or `error`.

- Code: [`scan_api/`](../../scan_api) (`api.py`, `worker.py`, `store.py`), tests in [`tests/scan_api/`](../../tests/scan_api).
- Template: [`template.yaml`](template.yaml), plain CloudFormation like `cdk/owner-c/python-detectors.yaml`.
  It needs no CDK toolchain or `cdk bootstrap` (which would add a staging bucket and an ECR repository).
- Package: [`scripts/build-scan-api-lambda.sh`](../../scripts/build-scan-api-lambda.sh) builds one zip for both functions.

## Architecture

```
 frontend ──POST /scans {"repo_url"}──▶ API Gateway HTTP API ──▶ api Lambda (256 MB, 10 s)
    │                                   (CORS, throttling)          │ 1. validate https://github.com/<owner>/<repo>
    │                                                               │ 2. PUT scans/<id>/status.json = queued
    │◀───────────── 202 {"scan_id"} ────────────────────────────────┤ 3. lambda:Invoke worker (InvocationType=Event)
    │                                                               ▼
    │                                      worker Lambda (3008 MB, 900 s, 2 GB /tmp, arm64, 2 concurrent)
    │                                        1. status = running
    │                                        2. GET api.github.com/repos/<o>/<r>/commits/HEAD -> SHA
    │                                        3. GET codeload.github.com/<o>/<r>/tar.gz/<SHA> (<= 50 MB, 120 s)
    │                                        4. safe extract to /tmp, run scanner (owners C, D), delete source
    │                                        5. PUT scans/<id>/report.json, status = done (or error)
    │                                                               ▼
    └──GET /scans/{scan_id} (poll every 3-5 s)──▶ api Lambda ──▶ S3 bucket (private, SSE-S3, TLS only,
                                                                  scans/ expires after 7 days)
```

Everything runs in the project Region, **ap-south-1** (the template refuses any other Region). There is
no VPC, NAT gateway or always-on compute; idle cost is only S3 storage for up to 7 days of reports.

## Endpoints

`SCAN_API_URL` is the stack output `ScanApiUrl` (`https://<api-id>.execute-api.ap-south-1.amazonaws.com`).

| Request | Response |
| --- | --- |
| `POST /scans` `{"repo_url": "https://github.com/owner/repo"}` | `202 {"scan_id": "<uuid>", "status": "queued"}` |
| | `200 {"scan_id": "<existing>", "status": "queued" \| "running" \| "done", "reused": true}`: per-repo cooldown, poll that scan (see [Abuse guard](#abuse-guard)) |
| | `400` not a `https://github.com/<owner>/<repo>` URL, `413` body over 4 KB, `503` worker could not be started, `429` throttled or `{"error": "daily scan limit reached; try again after 00:00 UTC"}` |
| `GET /scans/{scan_id}` | `200 {"scan_id", "status", "repo_url", "created_at", "updated_at", ...}` |
| | `status: "queued" \| "running"`: poll again |
| | `status: "done"`: `"report"` is the full `report.json`. Reports over 5 MB (Lambda responses are capped at 6 MB) come as `"report": null` plus `"report_url"`, a presigned S3 URL valid for 15 minutes |
| | `status: "error"`: `"error"` holds a message to show the user |
| | `404` unknown or expired scan id |

Every error response body is `{"error": "<message>"}`. A scan never stays `running`: the worker
records its own failures, and the API reports `error` if a worker died without recording one
(running for more than 960 s, or queued for more than an hour because scans were throttled).

CORS is handled by API Gateway for the `AllowedOrigin` parameter (default `*` for the demo). Set it to
the frontend's origin, e.g. `AllowedOrigin=https://app.example.com`, before sharing the URL.

## Safety and bounds

- Only `https://github.com/<owner>/<repo>` (the scanner's own `GITHUB_URL` pattern, no `.`/`..` names).
  Requests go only to `api.github.com` and `codeload.github.com`.
- The archive is capped at 50 MB downloaded, 1 GB unpacked and 100,000 entries. Absolute or `..`
  paths reject the whole archive. Symlinks, hard links, devices and FIFOs are never extracted.
  Then the scanner's own limits apply (5,000 files, 1 MB per file).
- Nothing from the repository is executed, installed, built or imported. The source lives only in
  the worker's `/tmp` for the duration of the scan and is deleted afterwards. Only status and report
  objects are stored, and they expire after `RetentionDays` (7).
- Cost caps: worker reserved concurrency 2, `POST /scans` throttled to 1 request/s (burst 2) and other
  routes to 10/s (burst 20), no async retries, 900 s worker timeout, plus the per-repo cooldown and the
  daily scan cap below.
- IAM: the api role can only read/write `scans/*`, list the bucket under `scans/` (so a missing key is a
  404, not a 403), invoke the worker and get/put/update items in the `scan-api-guard` table. The worker
  role can only write `scans/*`. Each role writes only to its own log group, and logs are kept 7 days.

## Abuse guard

`POST /scans` has no authentication, so two checks bound what an anonymous caller can make the worker do.
They run in this order: validate the body, cooldown lookup, cap increment, create `status.json`, invoke the worker.

- **Per-repo cooldown** (`ScanCooldownSeconds`, default 600). If the repo's latest scan is `queued` or
  `running`, or finished `done` at most that many seconds ago, the API returns `200` with that scan's
  `scan_id`, its current `status` and `"reused": true` instead of starting a new one. Repos match
  case-insensitively (`Owner/Repo` is `owner/repo`). Scans that ended in `error`, or that `GET` would report
  as stale (running over 960 s, queued over an hour), don't count. A reused scan doesn't use up the daily cap.
- **Daily cap** (`MaxScansPerDay`, default 50). At most that many new scans start per UTC day, across all
  repos. Above it the API returns `429 {"error": "daily scan limit reached; try again after 00:00 UTC"}`.
  Only scans that actually start count: if writing `status.json` or invoking the worker fails, the API
  gives the slot back (best effort) and returns `503`.

Both use the on-demand DynamoDB table `scan-api-guard` (key `pk`, TTL attribute `expires_at`):

| Item | Holds | Expires |
| --- | --- | --- |
| `repo#<owner>/<repo>` (lowercased) | latest `scan_id` and `created_at` for the repo | 1 day after the scan started |
| `day#<YYYY-MM-DD>` (UTC) | `scans`: scans started that day | 2 days after the day starts |

The counter is a single `UpdateItem` with `ADD scans :one` and the condition
`attribute_not_exists(scans) OR scans < :cap`. DynamoDB applies it atomically, so concurrent requests can't
go past the cap, and a failed condition (`ConditionalCheckFailedException`) becomes the `429`. The cooldown
reads the repo item, then the scan's `status.json`, so it never trusts TTL deletion, which can lag by up to
two days. The cooldown lookup and the repo-item write are not one transaction, so two requests for the same
repo inside the POST burst (2) can both start a scan. Each still counts against the cap.
A new scan costs 1 read and 2 writes (a reused scan costs 1 read), billed on demand at well under a cent a
day at the default cap. The table holds at most a few KB.

## Deploy (ap-south-1, not yet run; needs approval)

Follow [`docs/AWS_AGENT_WORKFLOW.md`](../../docs/AWS_AGENT_WORKFLOW.md): use your own local profile and
confirm the identity and Region first.

```bash
aws sts get-caller-identity
aws configure get region                     # expect ap-south-1 (AWS Settings > project > Region)
aws lambda get-account-settings --region ap-south-1 --query AccountLimit.ConcurrentExecutions
#   -> 10 on new projects: Lambda keeps 10 unreserved, so deploy with WorkerReservedConcurrency=0

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CODE_BUCKET="scan-api-code-$ACCOUNT-ap-south-1"

scripts/build-scan-api-lambda.sh             # prints the suggested CodeKey
KEY="scan-api/scan-api-$(shasum -a 256 infra/scan-api/build/scan-api.zip | cut -c1-16).zip"

aws s3 mb "s3://$CODE_BUCKET" --region ap-south-1          # once
aws s3 cp infra/scan-api/build/scan-api.zip "s3://$CODE_BUCKET/$KEY" --region ap-south-1

aws cloudformation deploy --region ap-south-1 --stack-name scan-api \
  --template-file infra/scan-api/template.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides CodeBucket="$CODE_BUCKET" CodeKey="$KEY" AllowedOrigin='*' WorkerReservedConcurrency=2

aws cloudformation describe-stacks --region ap-south-1 --stack-name scan-api \
  --query "Stacks[0].Outputs" --output table
```

Smoke test:

```bash
API=$(aws cloudformation describe-stacks --region ap-south-1 --stack-name scan-api \
  --query "Stacks[0].Outputs[?OutputKey=='ScanApiUrl'].OutputValue" --output text)
curl -s -X POST "$API/scans" -H 'content-type: application/json' \
  -d '{"repo_url": "https://github.com/pallets/itsdangerous"}'
curl -s "$API/scans/<scan_id>" | head -c 400
```

To ship new code, rebuild, upload under the new key and rerun `cloudformation deploy` with that `CodeKey`.
`ScanCooldownSeconds` (600) and `MaxScansPerDay` (50) tune the [abuse guard](#abuse-guard), for example
`--parameter-overrides ... MaxScansPerDay=20`.

## Cost on the Free plan

Per scan: 1 POST, about 30-50 polling GETs, about 5 S3 PUTs and 1-2 GETs, and one worker run of about
3 GB x 20-150 s, which is 60-450 GB-s.

- **Lambda:** the always-free 400,000 GB-s and 1M requests a month cover roughly 900-6,000 scans. arm64
  is about 20% cheaper per GB-s than x86 once usage goes past the free tier. Ephemeral storage above
  512 MB is billed per GB-s at a negligible rate (well under $0.0001 per scan).
- **API Gateway HTTP API:** billed per million requests (about $1/M). 1,000 scans with polling come
  to about 50,000 requests.
- **S3:** reports are KB to a few MB and expire after 7 days. Storage and requests are cents per month.
- **CloudWatch Logs:** a few KB per scan with 7-day retention, inside the free 5 GB of ingestion.
- **Data transfer:** downloading from GitHub is inbound, which is free. Report responses are small.

On the Free plan, any usage that the always-free tiers don't cover is paid from credits. Check usage
in the Billing and Cost Management console, and spend and plan status in AWS Settings > Billing.
Sudden `AccessDenied` errors on calls that used to work can mean a spend limit paused the project.

## Cleanup

```bash
BUCKET=$(aws cloudformation describe-stacks --region ap-south-1 --stack-name scan-api \
  --query "Stacks[0].Outputs[?OutputKey=='ScanBucketName'].OutputValue" --output text)
aws s3 rm "s3://$BUCKET" --recursive --region ap-south-1     # the stack deletes the bucket only when empty
aws cloudformation delete-stack --region ap-south-1 --stack-name scan-api
aws cloudformation wait stack-delete-complete --region ap-south-1 --stack-name scan-api
aws s3 rb "s3://$CODE_BUCKET" --force --region ap-south-1   # the code bucket is not part of the stack
```

## Limitations

- **Owners A and B are reported as `unavailable`.** They run on Node.js, which the `python3.12` Lambda
  runtime does not include. Run the CLI locally for those checks. A container-image Lambda with Node.js
  would cover them, at the cost of an ECR repository and a bigger cold start.
- Only public GitHub repositories, at the default branch HEAD. Submodules are not included, and Git LFS
  files arrive as pointer files.
- GitHub's unauthenticated API allows 60 requests per hour per IP, and Lambda shares its outbound IPs.
  When the API is rate-limited, the worker downloads `HEAD` and takes the commit SHA from the
  archive's `git archive` header (`commit_source: "tarball-header"`), or falls back to a content hash.
- **The API has no authentication.** Anyone with the URL can start scans, within the throttling,
  concurrency, cooldown and daily caps above. One caller can still use up the day's cap for everyone. Add a JWT authorizer, or restrict `AllowedOrigin`, before sharing the URL
  publicly. CORS only restricts browsers.
