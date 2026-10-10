# Owner D telemetry demo workload (simulated client)

This is a demo workload, not detector code. `owner-d-telemetry-demo` is a small Python 3.12 Lambda function.
Each time it is invoked, it emits labeled synthetic CloudWatch Logs lines, `OwnerD/Demo` custom metrics and
X-Ray subsegments. Owner D's telemetry analyzers then have real evidence to read through the read-only
"client" role. It follows the pattern of Owner C's `owner-c-xray-demo`.

- Every scenario emits a **waste** path and a clean **control** path. An analyzer should flag the first and
  leave the second alone.
- Every emitted item is labeled synthetic:
  - JSON log lines have `"synthetic": true`, `check` and `path` fields.
  - Text log lines contain `synthetic=true check=... path=...`.
  - Metrics carry a `synthetic=true` dimension.
  - X-Ray subsegments carry a `synthetic=true` annotation.
- No LLM, Bedrock or downstream service is called. The data is invented.

| Scenario | Waste path | Control path | What it proves |
| --- | --- | --- | --- |
| `OBS-11` | A retry loop logs the identical line `upstream inventory-svc unavailable, retrying` `repeat` times (default 20, max 50). | One summary line with `attempts=N`. | A dedup analyzer sees N identical messages in one run and one line on the clean path. |
| `OBS-17` | One ERROR line with the full `stack_trace` (chained exceptions) and the whole echoed `request_body` (40 items, about 6 KB). | `error_type`, `error`, `order_id`, `body_bytes`, `body_sha256` (about 0.25 KB). | Field-level analysis finds large verbose fields and the size ratio between the two paths. |
| `OBS-04` | Three free-text lines (`<time> WARN [...] inventory lookup slow for order_id ... ms 2310`). | The same three events as JSON lines. | Unstructured lines need parsing at query time; the structured ones need none. |
| `OBS-06` | `RequestLatencyMs` keyed by `request_id` (`req-000`..`req-039`, one series per request). | `RequestLatencyMs` keyed by `endpoint` (2 values). | `ListMetrics` shows a dimension whose value count grows with traffic, next to a bounded one. |
| `LLM-10` | `invoke_agent demo_unbounded_agent` runs `tool_calls` turns (default 12, max 25). Each turn is a `chat` span followed by `execute_tool get_order_status` with byte-identical arguments. It ends with `stop_reason=max_iterations`. | `invoke_agent demo_bounded_agent`: 3 `chat` turns and 2 different tools (`get_order_status`, then `draft_reply`), ending with `stop_reason=answer_ready`. | Owner D's LLM-10 detector flags the waste run for `identical-tool-calls` (12 > 3) and `iteration-budget` (12 > 10). The control run stays under both limits. |
| `LLM-05` (opt-in) | `invoke_agent demo_redundant_pipeline`: 2 `chat` calls with the same model and the same `gen_ai.input.messages.hash`; step 2 re-sends step 1's request unchanged. | `invoke_agent demo_chained_pipeline`: the same first call, then a second request that carries step 1's output (2 distinct digests). | Owner D's LLM-05 detector flags the waste run for `consecutive-identical-calls` (2 > 1) and leaves the control run alone. |
| `LLM-12` (opt-in) | `agents` agents (default 5, from 2 to 8) each answer the same 8 questions `rounds` times (default 3, at most 5), each through its own in-process dict. Every agent misses every question once. Lines carry `cache_name=demo-agent-local` and `cache_backend=memory`. | The same agents through one shared dict: only the first agent misses each question. Lines carry `cache_name=demo-fleet-shared` and `cache_backend=demo-shared`. | Owner D's LLM-12 detector flags `demo-agent-local` for `isolated-agent-caches` (32 of 40 misses in 120 lookups would have been hits in a shared cache) and leaves `demo-fleet-shared` alone (8 misses, one per question). |

Shared dimensions on every metric: `synthetic`, `check=OBS-06` and `path`.

### LLM-10 trace shape

The LLM-10 spans follow the
[OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
in the form the ADOT `awsxrayexporter` writes to X-Ray. Owner D's LLM-10 normalizer,
`llm10.normalize_xray_traces`, reads exactly this shape.

- **Span names:** `invoke_agent <agent>`, `chat <model>` and `execute_tool <tool>`.
- **Attributes:** dotted keys under `metadata.default`:
  - `gen_ai.operation.name` (`invoke_agent`, `chat` or `execute_tool`)
  - `gen_ai.agent.name`
  - `gen_ai.request.model`, set to the placeholder `synthetic-demo-model`
  - `gen_ai.tool.name`
  - `gen_ai.tool.call.arguments`, a JSON string that is identical byte for byte on repeated calls
  - `gen_ai.tool.call.id`
- **Annotations:**
  - Every span has `synthetic=true` and `check=LLM-10`.
  - The agent span also has `path`, `llm_calls`, `tool_calls` and `stop_reason`.

The entrypoint the detector reports is the function segment, `owner-d-telemetry-demo`. Each invocation
produces one analyzable trace.

### LLM-05 trace shape

The opt-in `LLM-05` scenario uses the same span names and `metadata.default` layout, with `check=LLM-05`,
`path` and `step` annotations. Its `chat` spans record no prompt content. Each carries
`gen_ai.input.messages.hash`, a 16-hex SHA-256 digest of the canonical synthetic request, which Owner D's
LLM-05 normalizer (`llm05.normalize_xray_traces`) compares within one agent run. This attribute is not part of
the OpenTelemetry GenAI conventions: `gen_ai.input.messages` itself is Opt-In, and this digest stands in for
it. The LLM-10 scenario records neither, so LLM-05 reports its traces as a limitation, not as clean.

The subsegments are sent straight to the Lambda X-Ray daemon over UDP, under the function's trace. This
removes the need to bundle `aws-xray-sdk`, which is not in the Python runtime. The zip contains only
`handler.py`. If the invocation's trace is not sampled, nothing is sent and the response shows
`"xray": "not_sampled"`. Invoke again in a new second.

### LLM-12 log shape

The opt-in `LLM-12` scenario writes one JSON line per cache lookup with `agent_id`, `cache_name`,
`cache_backend`, `cache_result` (`hit` or `miss`) and `cache_key_hash`. The hash is a 16-hex SHA-256 digest of
the placeholder model, the run ID and the question. No question text or answer is logged. Including the run ID
gives every run new keys, so every run re-warms the caches the way new traffic does. One run with the
defaults writes 240 lines (120 per path), about 50 KB, and is enough to exceed LLM-12's `min_lookups` of 100.
Run it once, then invoke `owner-d-log-analyzer` with `"checks": ["LLM-12"]` over the demo log group.

## Event

```json
{"scenario": "all", "repeat": 20, "series": 40, "tool_calls": 12, "path": "both"}
```

`scenario` takes `all` (the default) or one of `OBS-11`, `OBS-17`, `OBS-04`, `OBS-06` or `LLM-10`. Short
forms such as `obs11` are also accepted. Each numeric knob is optional and is clamped to its maximum.
`LLM-05` (or `llm05`) and `LLM-12` (or `llm12`) are opt-in: `all` does not run them, so the output of `all` is
unchanged. `agents` and `rounds` apply to LLM-12 only.

`path` applies to LLM-10, LLM-05 and LLM-12 and takes `both` (the default), `waste` or `control`. The LLM-10
and LLM-05 runs share one entrypoint, so a clean-only result needs a time window that contains only
`"path": "control"` traces. The LLM-12 paths use separate cache names, so one run gives both results.

The response summarizes what was emitted, along with a `run_id` that also appears in every log line.

## Build, deploy, invoke

Deploy only in the project Region, ap-south-1. The template rejects any other Region. These commands change
AWS state, so run them only after someone has reviewed them.

```bash
./scripts/build-owner-d-telemetry-demo.sh   # prints cdk/owner-d/build/owner-d-telemetry-demo-<sha>.zip
aws s3 cp <zip> s3://owner-d-deploy-<account>-ap-south-1/ --profile aws-agent --region ap-south-1
aws cloudformation deploy --stack-name owner-d-telemetry-demo \
  --template-file cdk/owner-d/telemetry-demo.yaml --capabilities CAPABILITY_NAMED_IAM \
  --tags owner=D project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-d-deploy-<account>-ap-south-1 CodeKey=<zip name> \
  --profile aws-agent --region ap-south-1
```

`ReservedConcurrency` defaults to `0` (no reservation). The project's Lambda concurrency quota is 10, and
Lambda refuses any reservation while the quota is 10, so the stack deploys as-is. Run
`aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions` to check the quota. On projects
with a higher quota, pass `ReservedConcurrency=1` to cap the demo at one concurrent run. The schedule is
created `DISABLED`. To turn it on, pass `ScheduleState=ENABLED` (default `rate(1 day)`), and read the cost
note first.

```bash
aws lambda invoke --function-name owner-d-telemetry-demo --cli-binary-format raw-in-base64-out \
  --payload '{"scenario":"all"}' --profile aws-agent --region ap-south-1 /tmp/telemetry-demo.json
cat /tmp/telemetry-demo.json
```

LLM-10 evaluates an entrypoint only once it has `min_traces` analyzable traces, with a reference value of 10.
Run the LLM-10 scenario about 10 times. Pause one second between calls: X-Ray samples the first request in
each second, and later ones only at 5%.

```bash
for i in $(seq 10); do
  aws lambda invoke --function-name owner-d-telemetry-demo --cli-binary-format raw-in-base64-out \
    --payload '{"scenario":"llm10"}' --profile aws-agent --region ap-south-1 /tmp/telemetry-demo-llm10.json
  grep -o '"xray": "[a-z_]*"' /tmp/telemetry-demo-llm10.json
  sleep 1
done
```

The `llm10` scenario sends no custom metrics. Ten runs cost almost nothing: 10 X-Ray traces, which fall
within the 100,000 free traces each month, and a few log lines. Re-run any call that reports
`"xray": "not_sampled"`.

Read-only checks that the evidence landed. Metrics can take a few minutes to appear, and traces about a
minute:

```bash
aws logs filter-log-events --log-group-name /aws/lambda/owner-d-telemetry-demo \
  --filter-pattern '"synthetic"' --max-items 50 --profile aws-agent --region ap-south-1
aws cloudwatch list-metrics --namespace OwnerD/Demo --profile aws-agent --region ap-south-1
aws xray get-trace-summaries --start-time $(date -u -v-15M +%s) --end-time $(date -u +%s) \
  --filter-expression 'annotation.check = "LLM-10"' --profile aws-agent --region ap-south-1
```

## Cost

The cost is tiny as long as the demo is invoked by hand. Prices below are list prices, and most of these
fall within free tiers or plan credits.

- **Custom metrics** dominate. Each distinct metric name plus dimension set is billed as one custom
  metric, at $0.30 per metric-month for the first 10,000. The charge is prorated by the hour and accrues only
  in hours that receive data.
  - The `request_id` and `endpoint` values come from fixed pools, so at most **42 series** ever exist, however
    often the demo runs. A unit test enforces this bound.
  - One `all` or `OBS-06` invocation is 42 series for 1 hour, about **$0.02**. Five such invocations in
    different hours cost about $0.09.
  - Other scenarios, including the 10 `llm10` runs, send no metrics.
  - With the optional daily schedule: 42 × 30 h / 730 h × $0.30 ≈ **$0.52/month**.
  - Do not schedule it hourly. That keeps all 42 series active all month, about $12.60/month.
- **PutMetricData**: 1 request per run, at $0.01 per 1,000 requests.
- **Logs**: about 10 KB per full run, and about 50 KB per opt-in `LLM-12` run. Ingestion is about $0.50/GB, and retention is 7 days.
- **X-Ray**: 1 trace per run. The first 100,000 traces each month are free.
- **Lambda**: 128 MB, arm64, under 1 s per run.
- **EventBridge Scheduler**: free while disabled.

There is no VPC, NAT gateway or always-on compute.

## Cleanup

```bash
aws cloudformation delete-stack --stack-name owner-d-telemetry-demo --profile aws-agent --region ap-south-1
aws cloudformation wait stack-delete-complete --stack-name owner-d-telemetry-demo --profile aws-agent --region ap-south-1
aws s3 rm s3://owner-d-deploy-<account>-ap-south-1/<zip name> --profile aws-agent --region ap-south-1
```

Deleting the stack removes the function, its log group, both roles and the schedule. Custom metrics cannot be
deleted. They stop costing anything once no data is sent, and `ListMetrics` drops them after about two weeks.
X-Ray traces expire after 30 days.

## Tests

```bash
python -m unittest discover -s detectors/owner-d/tests -t detectors/owner-d -p test_telemetry_demo.py
```

boto3 and the X-Ray daemon are stubbed, so the tests make no network calls.
