# Owner B G01 deployment

CDK v2 synthesizes two narrow CloudFormation stacks in the verified eu-north-1 project. Deploy the synthesized templates through AWS MCP change sets. BootstraplessSynthesizer is intentional: code is an externally uploaded immutable S3 object, not a CDK-published asset; no bootstrap/admin execution roles are needed for this path.

1. `npx cdk synth --app "node infra/owner-b/app.cjs" --strict -c project=<verified-project>` produces owner-b-artifacts (private, encrypted, versioned, retained).
2. `node scripts/build-owner-b.cjs`; zip the two emitted JS files at archive root. SHA256-address the zip under `code/<digest>.zip`; upload using AWS MCP presigned PUT.
3. Synthesize with `-c artifactBucket=<actual-output> -c codeKey=code/<digest>.zip -c codeVersion=<HeadObject-VersionId>`. Inspect a CloudFormation change set before ExecuteChangeSet; wait for CREATE/UPDATE_COMPLETE. Check deployed CodeSha256 and invoke both published immutable versions.
4. Optional `-c hubArn=<verified-owner-d-bus-arn>` and `-c logGroups='["<authorized-group>"]'` add narrowly scoped publication/query permission. Resolve Owner D pointer ingestion agreement first. Results contain `delivery.status=blocked` when hub is absent, and unavailable telemetry when logs cannot be acquired. Never equate S3 artifact storage with hub report persistence.
5. `-c reservedConcurrency=2` only when GetAccountSettings shows sufficient capacity to retain Lambda's minimum unreserved concurrency. Otherwise the project service quota bounds invocation concurrency; disclose that no per-function reservation was applied.

No function URL, public bucket, client writes, database, customer execution, or invented hub is created. Functions use 512 MiB, ARM64, 90 seconds, 7-day logs and failure queues. Artifact inputs expire after 7 days; results after 30 days. Code and retained buckets/log groups require explicit cleanup decisions.

The initial deployment's only LOG allowlist entry is `/aws/lambda/owner-b-static-scan` for an operational smoke query. This contains auditor runtime logs, not client query telemetry. It does not satisfy DB-05/DB-16 real-data acceptance. The deployed hub ARN is empty because no Owner D ingestion route was discovered.

This work does not import or change the fetched Owner D INF-01 detector. The separate findings hub is a missing shared dependency.
