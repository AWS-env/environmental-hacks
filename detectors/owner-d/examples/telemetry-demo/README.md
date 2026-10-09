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
| `LLM-10` | X-Ray `agent_loop` subsegment with `tool_calls` (default 12, max 25) `tool:get_order_status` children. They all have the same `args_hash`, and the loop has `stop_reason=max_iterations`. | `agent_loop` with 2 different tool calls and `stop_reason=answer_ready`. | Trace analysis sees repeated identical tool calls and a loop that ends only at its cap. |

Shared dimensions on every metric: `synthetic`, `check=OBS-06` and `path`.

X-Ray annotations:

- `agent_loop`: `synthetic`, `check`, `path`, `iterations`, `stop_reason`.
- `tool:*`: `synthetic`, `check`, `tool_name`, `args_hash`. The arguments themselves are in the `tool.args` metadata.

The subsegments are sent straight to the Lambda X-Ray daemon over UDP, under the function's trace. This
removes the need to bundle `aws-xray-sdk`, which is not in the Python runtime. The zip contains only
`handler.py`. If the invocation's trace is not sampled, nothing is sent and the response shows
`"xray": "not_sampled"`. Invoke again in a new second.

## Event

```json
{"scenario": "all", "repeat": 20, "series": 40, "tool_calls": 12}
```

`scenario` takes `all` (the default) or one of `OBS-11`, `OBS-17`, `OBS-04`, `OBS-06` or `LLM-10`. Short
forms such as `obs11` are also accepted. Each knob is optional and is clamped to its maximum. The response
summarizes what was emitted, along with a `run_id` that also appears in every log line.

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
    ReservedConcurrency=0 \
  --profile aws-agent --region ap-south-1
```

The project's Lambda concurrency quota is 10, and Lambda refuses any reservation while the quota is 10. Run
`aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions` to check it. On this project,
deploy with `ReservedConcurrency=0`. On projects with a higher quota, keep the default of `1`. The schedule is
created `DISABLED`. To turn it on, pass `ScheduleState=ENABLED` (default `rate(1 day)`), and read the cost
note first.

```bash
aws lambda invoke --function-name owner-d-telemetry-demo --cli-binary-format raw-in-base64-out \
  --payload '{"scenario":"all"}' --profile aws-agent --region ap-south-1 /tmp/telemetry-demo.json
cat /tmp/telemetry-demo.json
```

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
  - One invocation is 42 series for 1 hour, about **$0.02**. Five invocations in different hours cost about
    $0.09.
  - With the optional daily schedule: 42 × 30 h / 730 h × $0.30 ≈ **$0.52/month**.
  - Do not schedule it hourly. That keeps all 42 series active all month, about $12.60/month.
- **PutMetricData**: 1 request per run, at $0.01 per 1,000 requests.
- **Logs**: about 10 KB per full run. Ingestion is about $0.50/GB, and retention is 7 days.
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
