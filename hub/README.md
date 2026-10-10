# Owner D findings hub (writer + readback)

The detectors publish to the `findings-hub` EventBridge bus in ap-south-1. The
`owner-d-findings-ingest` rule sends every result event to
`owner-d-findings-writer`, which validates it against contract v1 and saves it to
the `owner-d-findings` DynamoDB table. A result counts as persisted only after
readback returns it. A successful `PutEvents` does not prove persistence.

| Detail type | Sender | Validation | `evidence` |
| --- | --- | --- | --- |
| `detector.result.v1` | Owner C (full result inline) | `validate(result)`: shape, coverage and fingerprints only. No input is sent with the event, so citations cannot be checked | `unverified` |
| `DetectorResultPointer.v1` | Owner B (pointer to an `{input, result}` object) | Bucket must be allowlisted, key must be a content-addressed `results/` key, SHA-256 must match, then `validate_pair` runs and the pointer identity must equal the result | `verified` |

The rule matches any `source` that starts with `owner-`.

Invalid events raise an error and are never stored. After the retries run out,
the event lands in `owner-d-findings-writer-dlq`, and the
`owner-d-findings-writer-dlq-not-empty` alarm goes off. Redelivering the same
result is a no-op (`duplicate`).

## Table layout

One partition per scan, so a single Query returns the whole report:

| pk | sk | item |
| --- | --- | --- |
| `SCAN#<sha256([repository_id, scan_id])[:32]>` | `RESULT#<check_id>#<result_sha256>` | status, coverage counts, limitations, evidence level, source, event id, `result_json` (or `artifact` pointer) |
| same | `FINDING#<check_id>#<result_sha256>#<fingerprint>` | scope, identity, summary, confidence, recommendation, references, `evidence_json` |

Findings are written before their result item. Readback ignores findings whose
result item is missing. GSI `by-repository` (`gsi1pk = REPO#<repository_id>`,
`gsi1sk = <received_at>#<scan_id>#<check_id>`) lists scans for a repository.

## Readback (persistence proof)

```bash
export AWS_PROFILE=<your profile> AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1
PYTHONPATH=hub python -m findings_hub.readback --repository-id <repo> --scan-id <scan> [--check-id <id>]
PYTHONPATH=hub python -m findings_hub.readback --repository-id <repo>   # recent scans
```

The command exits with status 1 when nothing is persisted for that scan. This
needs `boto3`, which is not part of the contract requirements.

## Local pair import (no cross-Region acquisition)

Owner B's older eu-north-1 deployment cannot send events or expose S3 objects to
this ap-south-1 hub under project restrictions. Transfer sanitized pair files
locally and import them using your own local profile. This command makes no S3
or EventBridge calls; persistence still writes to DynamoDB in ap-south-1 and
requires approval for live AWS access and write permissions on the hub table.
Do not run the earlier deployment's AWS reads from the hub's Region.

The transfer JSON has three fields: `pair` (the exact `{input, result}` object),
`sha256` (SHA-256 of its UTF-8 `store.canonical(pair)` representation), and
`identity` (independent `repository_id`, `scan_id`, `check_id`,
`detector_version`, `commit_sha` from the receipt you intend to accept). Review
the identity against that receipt before import. To package a local pair:

```python
import json
from pathlib import Path
from findings_hub.store import canonical, sha256
from findings_hub.writer import POINTER_FIELDS
pair = json.loads(Path("sanitized-pair.json").read_text(encoding="utf-8"))
identity = {field: pair["result"][field] for field in POINTER_FIELDS}
# Compare identity with the independently reviewed receipt before proceeding.
Path("transfer.json").write_text(json.dumps({"pair": pair,
    "sha256": sha256(canonical(pair)), "identity": identity}), encoding="utf-8")
```

```bash
export AWS_PROFILE=<your-profile> AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1
PYTHONPATH=hub python -m findings_hub.import --pair transfer.json --verify-only
PYTHONPATH=hub python -m findings_hub.import --pair transfer.json --profile <your-profile> --region ap-south-1
PYTHONPATH=hub python -m findings_hub.readback --repository-id <repo> --scan-id <scan>
```

Both acquisition paths share checksum, `validate_pair` and identity validation.
The envelope is limited to 4 MiB; imported results must fit the existing 300 KB
inline-result limit because there is no remote artifact fallback. Validation
finishes before creating AWS clients. Duplicate imports use the existing
conditional result write and deterministic finding keys. No deploy is required
to use the command locally with the existing table and `boto3` installed.
Profiles created with `aws login` also require `pip install 'botocore[crt]'`
for the SDK login credential provider. Use your existing local sign-in; do not
copy credentials into the import files.

Imports store `evidence=verified`, `source=owner-b.detectors`, and a
`canonical_local_pair_sha256` provenance marker. Verified means the supplied
pair passes the contract and citation checks. It does not authenticate its
author, establish a real customer workload, or prove these sanitized bytes
equal the original S3 object. Keep raw AWS identities/resource names private;
never reuse an original object's checksum for altered public bytes.

Same-Region Owner B deployments can continue publishing allowlisted pointers
normally. Confirm each path through report readback, not `PutEvents` alone.

## Build and deploy

```bash
./scripts/build-owner-d-hub.sh            # prints cdk/owner-d/build/owner-d-findings-hub-<sha>.zip
aws s3 cp <zip> s3://owner-d-deploy-<account>-ap-south-1/
aws cloudformation deploy --stack-name owner-d-findings-hub \
  --template-file cdk/owner-d/findings-hub.yaml --capabilities CAPABILITY_NAMED_IAM \
  --tags owner=D project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-d-deploy-<account>-ap-south-1 CodeKey=<zip name> \
    PointerBuckets=<owner-b artifact bucket>
```

The `findings-hub` bus already exists outside this stack. The stack only
references it by name.

## Read API (public scans)

Stack `owner-d-hub-api` ([`cdk/owner-d/hub-api.yaml`](../cdk/owner-d/hub-api.yaml), code
[`findings_hub/api.py`](findings_hub/api.py), deployed from the same zip as the writer) serves persisted scans
to the website and the dashboard:

```
GET {HubApiUrl}/repos/<owner>/<repo>/scans/<scan_id>
200 {"repository_id": "github:<owner>/<repo>", "scan_id", "commit_sha", "results": [...], "findings": [...],
     "findings_total", "truncated"}
404 {"error": "scan not found"}
```

`scan_id` is the UUID that scan-api returned from `POST /scans`. After saving `report.json`, the scan-api
worker publishes every validated result to `findings-hub` with source `owner-<c|d>.scan-api`, so a finished
scan becomes readable here a few seconds later and stays after the S3 report expires. Use the repository
name exactly as it was scanned, because `repository_id` is case-sensitive.

Only results whose source is `owner-<x>.scan-api` are served. Those are scans of public repositories,
looked up by a random ID, which matches scan-api's own exposure. Client-CI upload results
(`owner-d.artifact-parser`) and telemetry results (`owner-d.*-analyzer`) are never returned. The function
can only run `dynamodb:Query` on the findings table. CORS allows `GET` from `AllowedOrigin`.

```bash
./scripts/build-owner-d-hub.sh               # same zip as the writer; contains findings_hub/api.py
aws s3 cp <zip> s3://owner-d-deploy-<account>-ap-south-1/
aws cloudformation deploy --stack-name owner-d-hub-api --template-file cdk/owner-d/hub-api.yaml \
  --capabilities CAPABILITY_NAMED_IAM --tags owner=D project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-d-deploy-<account>-ap-south-1 CodeKey=<zip name>
```

## Artifact upload endpoint

Client CI uploads artifacts such as profiler output, test timings, coverage and bundle stats through
`POST /artifacts/presign` (stack `owner-d-artifact-upload`, template
[`cdk/owner-d/artifact-upload.yaml`](../cdk/owner-d/artifact-upload.yaml), code
[`artifact_upload/api.py`](artifact_upload/api.py)).

1. The GitHub Actions job requests an OIDC token with the audience `owner-d-artifact-upload` and sends it as
   `Authorization: Bearer <token>`, with the body `{"artifacts": ["cpuprofile.json", "junit.xml"]}`.
2. `owner-d-artifact-presign` checks the token:
   - its signature against GitHub's JWKS, with RS256 only;
   - its issuer, audience and expiry;
   - its `repository`, which must match `AllowedRepositories` (default `AWS-env/*`).
3. It returns one presigned POST per name. Each is valid for 15 minutes and is pinned to one exact key and
   to the `MaxArtifactMiB` size limit (default 25). The keys come from the token's claims, never from the
   body: `uploads/github%3A<owner>%2F<repo>/<sha>/<run_id>-<run_attempt>/<name>`.
4. The job uploads each file directly to the private `owner-d-artifacts-<account>-ap-south-1` bucket. Uploads
   are kept for `ArtifactRetentionDays` (default 90). S3 sends `Object Created` events to the default
   EventBridge bus, so a parser subscribes with its own rule on `detail.bucket.name`.
   Owner D's `owner-d-artifact-parser` already does this for `tst-12.json`; see
   [the artifact route](../detectors/owner-d/README.md#artifact-route).

```yaml
# client workflow
permissions:
  id-token: write
  contents: read
steps:
  - run: |
      token=$(curl -sfH "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
        "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=owner-d-artifact-upload" | jq -r .value)
      curl -sf -X POST "$ARTIFACT_UPLOAD_URL" -H "Authorization: Bearer $token" \
        -d '{"artifacts":["junit.xml"]}' > presign.json
      args=()  # --form-string: field values are sent literally (no @file or <file expansion)
      while IFS= read -r kv; do args+=(--form-string "$kv"); done \
        < <(jq -r '.uploads["junit.xml"].fields | to_entries[] | "\(.key)=\(.value)"' presign.json)
      curl -sf "${args[@]}" -F file=@junit.xml "$(jq -r '.uploads["junit.xml"].url' presign.json)"
```

Responses: `401` for a missing, expired or invalid token; `403` for a repository not on the allowlist;
`400` for a bad body; `503` when GitHub's keys can't be fetched. S3 answers the upload itself with `204`, or
`400 EntityTooLarge` above the size cap. Tokens and presigned fields are never logged.

```bash
./scripts/build-owner-d-artifact-upload.sh  # prints cdk/owner-d/build/owner-d-artifact-upload-<sha>.zip
aws s3 cp <zip> s3://owner-d-deploy-<account>-ap-south-1/
aws cloudformation deploy --stack-name owner-d-artifact-upload \
  --template-file cdk/owner-d/artifact-upload.yaml --capabilities CAPABILITY_NAMED_IAM \
  --tags owner=D project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-d-deploy-<account>-ap-south-1 CodeKey=<zip name>
```

The bucket is retained when the stack is deleted. To clean up, empty and delete
`owner-d-artifacts-<account>-ap-south-1` by hand after `aws cloudformation delete-stack`.

## Tests

```bash
python -m pip install -r hub/artifact_upload/requirements.txt  # PyJWT[crypto] for the upload endpoint
PYTHONPATH=hub python -m unittest discover -s hub/tests -t hub
```
