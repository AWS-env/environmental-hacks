# Owner D detectors (runtime ops)

Contract v1 detectors for Owner D taxonomy checks. The shared input/result
boundary is defined in [`docs/DETECTOR_CONTRACT.md`](../../docs/DETECTOR_CONTRACT.md);
JSON Schema is the source of truth. Detectors here are pure functions over
contract payloads: they never call AWS APIs, and source collection stays outside
the detector.

## INF-01 — Over-provisioning for unforeseen demand spikes

Flags compute resources whose provisioned capacity is never approached by
observed demand: a sustained low average CPU utilization **and** a low observed
peak over a sufficiently long telemetry window. A low average with a high
observed peak is treated as legitimate peak-driven provisioning and is not
flagged.

### Input

A contract v1 `input` payload with one `telemetry` source per scope item. Scope
IDs use the form `resource:<id>`. The telemetry `data` object is a normalized
CloudWatch summary supplied by the connector:

| Field | Meaning |
| --- | --- |
| `resource_id` | Resource identifier; must match the scope (`resource:<id>`) |
| `resource_type` | e.g. `aws_ec2_instance`, `aws_ecs_service` |
| `metric` | `cpu_utilization` (v1 supports only this metric) |
| `provisioned_capacity` / `capacity_unit` | e.g. `8` / `vcpu` |
| `average_utilization` / `peak_utilization` | Fractions of capacity in `[0, 1]` |
| `window_days` / `sample_count` | Telemetry coverage; sufficiency is checked |

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_window_days` | Minimum telemetry window | `14` |
| `min_sample_count` | Minimum number of samples | `100` |
| `average_utilization_threshold` | Average below this is suspicious | `0.10` |
| `peak_utilization_threshold` | A peak at/above this is real demand | `0.50` |

Missing or invalid settings make the result `unavailable`.

### Detection rule

A finding is emitted when `window_days >= min_window_days`,
`sample_count >= min_sample_count`, `average_utilization <
average_utilization_threshold` and `peak_utilization <
peak_utilization_threshold`. Both thresholds use strict `<`; equality is not
flagged. Findings cite the exact `average_utilization`, `peak_utilization`,
`provisioned_capacity`, `window_days` and `sample_count` values supplied by the
source, and the fingerprint identity is `cpu-capacity-headroom`. No
energy/emissions measurements are emitted in v1; estimation is deferred to the
estimate engine.

### Run

From the repository root, using Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r shared/contracts/requirements.txt
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/inf01/inf01-01-positive-input.json
```

The CLI validates the input against the shared contract, evaluates it,
validates the result and its evidence against the input, then prints the result
JSON (`-o FILE` writes to a file instead).

## AWS telemetry route

This route implements the read-only telemetry path in
[`docs/ARCHITECTURE_FLOWS.md`](../../docs/ARCHITECTURE_FLOWS.md) §3. Three
Lambdas read existing telemetry. Each can read through a read-only role using
STS AssumeRole (session `owner-d-telemetry-reader`, 15 minutes). They
normalize the data per check, evaluate the contract v1 detectors and publish
one `detector.result.v1` event per result to the `findings-hub` bus. Before
publishing, every input/result pair goes through the shared `validate_pair`.
A pair that fails is refused: it is not published and is listed under
`refused` in the response. The `owner-d-findings-writer`
([`hub/`](../../hub/README.md)) validates each event again and stores it with
`evidence: unverified`. The detectors themselves stay pure and never call AWS.

| Lambda | Code | Reads | Checks |
| --- | --- | --- | --- |
| `owner-d-telemetry-analyzer` | `owner_d/aws/telemetry_handler.py`, `metrics.py` | CloudWatch `ListMetrics`, `GetMetricData` | INF-01, OBS-06 |
| `owner-d-log-analyzer` | `owner_d/aws/log_handler.py` | Logs `DescribeLogGroups`, `ListTagsForResource`, Logs Insights `StartQuery`/`GetQueryResults`/`StopQuery` | OBS-07 |
| `owner-d-trace-analyzer` | `owner_d/aws/trace_handler.py` | X-Ray `GetTraceSummaries`, `BatchGetTraces` (5 ids per call) | LLM-10 |

Infrastructure: [`cdk/owner-d/telemetry.yaml`](../../cdk/owner-d/telemetry.yaml)
(plain CloudFormation). Build: `scripts/build-owner-d-telemetry.sh`. The zip
bundles `owner_d/`, `shared/contracts`, `docs/taxonomy/checks.json` and
jsonschema with its dependencies as python3.12 arm64 wheels. They are pure
Python except `rpds-py`, a compiled manylinux wheel. boto3 comes from the
runtime. If `shared.contracts` cannot be imported, an analyzer fails before it
reads anything; it never publishes unvalidated results.

### Event formats

All analyzers take `repository_id`, a full 40-character `commit_sha`, an
optional `scan_id` (default: a new UUID), `role_arn` and `external_id`
(optional; without them the execution role reads its own project; for this
stack's `owner-d-telemetry-readonly` role the analyzers add the ExternalId
themselves, see [ExternalId](#externalid-for-owner-d-telemetry-readonly)), `checks`
(default: every registered check for that Lambda), `settings` keyed by check
ID, `scope_per_payload` (1-200, default 50) and `dry_run`.

```json
{"repository_id": "github:AWS-env/example", "commit_sha": "<40 hex>", "scan_id": "telemetry-2026-10-10",
 "role_arn": "<ReadOnlyRoleArn stack output>",
 "resources": [{"type": "ec2", "id": "i-0abc12345678def00", "provisioned_capacity": 2, "capacity_unit": "vcpu"},
               {"type": "ecs", "cluster": "web", "service": "api"}, {"type": "lambda", "name": "orders"}],
 "discover": {"types": ["ec2", "ecs", "lambda"], "max_resources": 50, "recently_active": true, "max_pages": 5},
 "window": {"lookback_days": 15, "period_seconds": 3600},
 "settings": {"INF-01": {"min_window_days": 14, "min_sample_count": 100,
                         "average_utilization_threshold": 0.1, "peak_utilization_threshold": 0.5}},
 "dry_run": true}
```

- Telemetry analyzer. INF-01 needs `resources`, `discover`, or both. `window`
  accepts `lookback_days` (default 15, at most 30) or `start`/`end`, plus
  `period_seconds` (default 3600). `list_metrics` (`namespace`,
  `metric_name`, `max_pages` ≤ 20) feeds ListMetrics checks.
- Log analyzer. `log_groups` takes `prefix`, `max_pages` (≤ 20) and
  `include_tags` (≤ 100 lookups). `logs` takes `log_groups` (exact names, at
  most 50, each must match the allowlist), `prefix`, `lookback_hours`
  (default 24, at most 168) or `start`/`end`, `limit` (default 1000) and
  `timeout_seconds` (default 60, at most 240).
- Trace analyzer. `xray` takes `filter_expression`, `lookback_minutes`
  (default 60; X-Ray caps the window at 24 hours) or `start`/`end`,
  `max_traces` (default 50, at most 100, leaving room above LLM-10's
  `min_traces` of 10) and `max_pages`. Point `filter_expression` at the agent
  entrypoint, e.g. `service("agent-fn")`, so the traces read are the ones
  LLM-10 can analyze.
- `{"probe": ["cpu_metrics" | "metrics" | "log_groups" | "logs_insights" | "traces"], "role_arn": ...}`
  only collects. It returns counts and publishes nothing, so you can check
  IAM and the role before running checks.

The response, and one JSON log line per invocation, contains a per-result
summary, collection counts (including Logs Insights `bytes_scanned`),
`published` and `assumed_role`. It never contains the role ARN, the
ExternalId or raw telemetry. A `dry_run` response also includes the full
`result_payloads` (valid results only).

`refused` lists results that failed `validate_pair`: `check_id`, `scope`
size, `status` and the contract error (at most 300 characters). Refused
results are not published, while the valid results of the same invocation
still are. The invocation still succeeds, so a refusal shows up in the
response and the log line, not in the DLQ. A detector that raises is listed
under `errors` and fails the invocation after the valid results are
published.

### INF-01 normalization

`metrics.normalize_cpu_metrics` reads `CPUUtilization` Average and Maximum per
period. `AWS/EC2` uses `InstanceId`. `AWS/ECS` uses `ClusterName` +
`ServiceName`, measured as a percent of the service's CPU reservation. It
produces the INF-01 data:

- `average_utilization` is the mean of the period averages. `peak_utilization`
  is the highest period maximum. Both are fractions, capped at 1.0 with a
  limitation when ECS exceeds its reservation.
- `window_days` is the observed span of datapoints (not the requested
  window). `sample_count` is the number of periods with data.

Scope IDs are `resource:ec2/<instance>`, `resource:ecs/<cluster>/<service>`
and `resource:lambda/<function>`. `provisioned_capacity` defaults to `1`
instance/service unless the event supplies it. The following stay in scope
without a source and get a limitation, so INF-01 reports them as not evaluated
rather than clean:

- Lambda functions, which publish no CPU utilization metric.
- Resources without datapoints.
- Resources whose GetMetricData pages were cut off.

The default 15-day lookback leaves one day of headroom over INF-01's 14-day
minimum.

### Registered checks (one line each)

`owner_d/aws/registry.py` lists the checks each Lambda runs: INF-01
(`cpu_metrics`), OBS-06 (`metrics`), OBS-07 (`log_groups`) and LLM-10
(`traces`). A detector module plugs in through a normalizer, by default
`normalize_<source>(raw, *, settings)`. It returns contract `telemetry`
sources, or `{"scope", "sources", "limitations"}`. Normalizers that take raw
API pages use an adapter. The adapter builds the sources with account-free
locators and drops `log_group_arn`. OBS-06, OBS-07 and LLM-10 use the
`list_metrics`, `describe_log_groups` and `xray_traces` adapters:

```python
TelemetryCheck("OBS-06", "owner_d.obs06", "metrics", normalizer="owner_d.obs06:normalize_list_metrics",
               adapter="list_metrics", defaults=OBS06_DEFAULTS),
```

Without `checks`, the telemetry analyzer runs INF-01 and OBS-06. OBS-06 needs
`list_metrics` (for example `{"namespace": "OwnerD/Demo"}`); without it,
ListMetrics lists every namespace. Pass `"checks": ["INF-01"]` to run only
one of them.

Raw shapes:

- `metrics`: `{"pages": [ListMetrics responses]}`. The last page keeps
  `NextToken` when the listing was truncated.
- `log_groups`: `{"pages": [DescribeLogGroups responses], "logGroups": [...],
  "tags": {name: tags} | None}`.
- `logs_insights`: `{"rows", "statistics", "log_groups", "query"}`. The check
  module defines `LOGS_INSIGHTS_QUERY`.
- `traces`: `{"TraceSummaries", "Traces", "UnprocessedTraceIds"}`. The check
  module may define `XRAY_FILTER_EXPRESSION`. The `xray_traces` adapter calls
  `normalize_xray_traces(Traces)` and then the module's `telemetry_sources`.
  It notes when fewer traces were read than `min_traces`.

Every raw dict also has `window`, `truncated`, `collection` and
`limitations`. `collection` holds the stable collection parameters and is
copied into the contract `context`. `limitations` are appended to each
result.

### Deploy (ap-south-1 only)

The template's `ProjectRegionOnly` rule refuses every Region except
ap-south-1. The handlers also refuse to run anywhere else (`ALLOWED_REGION`).
These commands are a proposal; review them before running them with your own
profile:

```bash
export AWS_PROFILE=<your profile> AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1
./scripts/build-owner-d-telemetry.sh      # prints cdk/owner-d/build/owner-d-telemetry-<sha>.zip (needs pip)
aws s3 cp cdk/owner-d/build/owner-d-telemetry-<sha>.zip s3://owner-d-deploy-<account>-ap-south-1/
# first deploy: ReadOnlyExternalId is required (see "ExternalId" below); later deploys may omit it
aws cloudformation deploy --stack-name owner-d-telemetry \
  --template-file cdk/owner-d/telemetry.yaml --capabilities CAPABILITY_NAMED_IAM \
  --tags owner=D project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-d-deploy-<account>-ap-south-1 CodeKey=owner-d-telemetry-<sha>.zip \
    ReadOnlyExternalId="$(cat ~/.config/owner-d/telemetry-external-id)"
# smoke test through the simulated client role (read only, publishes nothing)
aws lambda invoke --function-name owner-d-log-analyzer --cli-binary-format raw-in-base64-out \
  --payload '{"probe": ["log_groups"], "role_arn": "<ReadOnlyRoleArn output>"}' /tmp/probe.json
# INF-01 against this project's own resources, then prove persistence
aws lambda invoke --function-name owner-d-telemetry-analyzer --cli-binary-format raw-in-base64-out \
  --payload '{"repository_id": "<repo>", "commit_sha": "<sha>", "scan_id": "<scan>", "discover": {}, "role_arn": "<ReadOnlyRoleArn output>"}' /tmp/inf01.json
PYTHONPATH=hub python -m findings_hub.readback --repository-id <repo> --scan-id <scan>
```

`LogQueryPattern` (default `/aws/lambda/owner-d-*`) is the only log group
pattern that Logs Insights may query. It is the `logs:StartQuery` resource in
both roles and the code-level allowlist. `owner-d-telemetry-readonly` trusts
only the three analyzer roles, only with the right `sts:ExternalId`, and
grants read-only actions. A real client role grants the same actions and
requires its own ExternalId.

#### ExternalId for `owner-d-telemetry-readonly`

The role's trust policy requires `sts:ExternalId` to equal the
`ReadOnlyExternalId` parameter (NoEcho, 16-1224 characters from
`[\w+=,.@:/-]`, no default). The template also sets it on the three analyzers
as `READONLY_EXTERNAL_ID`, next to `READONLY_ROLE_ARN`. When an event's
`role_arn` is this stack's role and the event has no `external_id`, the
analyzers send that value. They never send it to any other role, because
another project's CloudTrail would record it. For a client's role, put the
client's ExternalId in the event's `external_id`.

The deployer generates the value once, locally, and keeps it outside the
repository:

```bash
mkdir -p ~/.config/owner-d && chmod 700 ~/.config/owner-d
( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/owner-d/telemetry-external-id )
```

Pass it with `ReadOnlyExternalId="$(cat ~/.config/owner-d/telemetry-external-id)"`,
as in the deploy command above. The value then never appears in the
repository, in shell history or in `describe-stacks` output (NoEcho). `aws
cloudformation deploy` keeps the previous value when a later deploy omits the
override. To rotate it, generate a new file and deploy with the override. The
trust policy and the analyzers' environment change in the same update. Never
commit the file or paste the value into issues, PRs or events in the
repository.

Why a NoEcho parameter and not Secrets Manager or SSM: an ExternalId is not a
secret. It prevents the confused-deputy problem. Anyone who can read the role
can see it in the trust policy (`iam:GetRole`), and anyone who can read the
function configuration can see it in the analyzers' environment. Neither
Secrets Manager nor SSM would hide it from those reads. Secrets Manager also
costs money per secret on this Free-plan project. CloudFormation's
`ssm-secure` dynamic references are not supported in IAM trust policies or
Lambda environment variables. The parameter keeps the value out of the
repository at no cost.

### Cost and limits

- Logs Insights bills per GB of log data scanned. Queries need an allowlisted
  group and a bounded window (24 hours by default, at most 7 days). They run
  one at a time, are stopped (`StopQuery`) on timeout, and report
  `bytes_scanned`. Async invocations are not retried
  (`MaximumRetryAttempts: 0`), so a failure never re-runs a query. Failed
  events go to `owner-d-telemetry-dlq`, and the
  `owner-d-telemetry-dlq-not-empty` alarm goes off.
- GetMetricData bills per metric requested. INF-01 requests 2 metrics per
  EC2/ECS resource, at most 200 resources, at most 10 pages per 250 resources.
  ListMetrics, DescribeLogGroups and X-Ray reads are bounded by page and trace
  limits.
- Lambdas run on demand only: arm64, 256 MB, no VPC, NAT or schedule. Their
  log groups keep 7 days.
- This project's Lambda concurrent-executions quota is 10. Lambda refuses any
  reserved concurrency at that quota, so `ReservedConcurrency` defaults to 0
  (no reservation). Each handler bounds its own work: one Logs Insights query
  in flight, fixed page, trace and resource caps.

## TST-12 — Heavy fixtures, sleeps and real network calls in unit tests

Flags unit tests that do more work than their assertions need: real waits,
real network calls and large in-memory fixtures. The primary mode is static:
it reads Python test source, so it works on any repository whose tests are
available, including public GitHub repositories. Normalized CI test-run
artifacts can be added as optional evidence. The detector never imports or
executes tests and never calls external services.

### Input

A contract v1 `input` payload. The detector chooses a mode from each scope
item's source kinds:

| Scope | Sources | Mode |
| --- | --- | --- |
| `file:<path>` | exactly one `static` source (`content` = file text) | static |
| `file:<path>` | one `static` source plus `artifact` sources for tests in that file | static + artifact |
| `test:<test_id>` | exactly one `artifact` source | artifact |

Static mode supports Python test modules: `test_*.py`, `*_test.py`,
`conftest.py` or files under a `tests/` directory. Other `.py` files, other
languages and files that fail to parse stay out of `evaluated_scope` with a
limitation, so they are never reported clean. A `conftest.py` supplied
anywhere in the payload is also used for the test files below its directory
(for example, an autouse HTTP mock).

The artifact `data` object is a normalized summary from pytest durations,
Jest timing output and optional instrumentation:

| Field | Meaning |
| --- | --- |
| `test_id` | Test identifier; must match the scope (`test:<test_id>`), or start with the file path (`<path>::`) in a `file:` scope |
| `framework` | Test runner family, e.g. `pytest`, `jest` |
| `duration_seconds` | Observed wall-clock test duration |
| `sleep_seconds` | Observed or statically detected explicit sleep time |
| `network_call_count` | Real network calls observed during the unit test |
| `fixture_bytes` | Approximate bytes materialized by fixtures |
| `setup_seconds` | Observed setup/fixture time before the assertion body |

### Context settings

Static mode requires `max_sleep_seconds`, `max_network_calls` and
`max_fixture_bytes`. Artifact mode requires all five settings. If a mode's
settings are missing or invalid, the scope items that need that mode are
omitted with the reason.

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_sleep_seconds` | Explicit sleep allowance per test | `0.1` |
| `max_network_calls` | Real network-call allowance per test | `0` |
| `max_fixture_bytes` | Largest fixture materialization allowance | `10485760` |
| `max_duration_seconds` | Longest acceptable unit-test duration (artifact only) | `10` |
| `max_setup_seconds` | Longest acceptable setup/fixture duration (artifact only) | `2` |

### Static detection rule

Only tests (`test*` functions, and `test*` methods of `Test*`/`*TestCase`
classes), pytest fixtures and setup/teardown hooks are inspected. Helper
functions and code nested in callbacks inside a test are not inspected,
because they may never run. The rule reports one finding per test and signal:

- **Sleep**: `time.sleep`, awaited `asyncio`/`trio`/`anyio.sleep`, or
  `gevent`/`eventlet.sleep` with a statically known constant (literals,
  arithmetic, module/local constants). The longest single execution path is
  summed: `if`/`else` takes the larger branch, a loop body counts once and
  `except` handlers are ignored. A finding is reported when that sum is greater
  than `max_sleep_seconds`. Sleeps are not counted when they are cut short by
  a timeout or cancel scope (`wait_for`, `asyncio.timeout`,
  `move_on_after`, `pytest.raises(TimeoutError)`), when the module patches
  `sleep`, or when it uses a fake clock.
- **Network call**: `requests`/`httpx` calls, sessions and clients
  (`requests.Session`, `httpx.Client`/`AsyncClient`, `aiohttp.ClientSession`,
  `urllib3.PoolManager`), `urlopen` and `socket.create_connection` are
  reported only when the target is a literal external URL or host.
  Localhost, private IPs, `example.*`, `.test`/`.invalid` and dynamic URLs are
  not reported. AWS SDK calls on a `boto3.client`/`resource` with no local
  `endpoint_url` are also reported. A module that imports an HTTP or AWS
  mocking or recording library (`responses`, `requests_mock`, `respx`,
  `httpretty`, `vcr`, `moto`, `botocore.stub`, ...), patches a network target,
  mounts a transport adapter, or has such a `conftest.py` above it is treated
  as mocked. A finding is reported when a test has more call sites than
  `max_network_calls`.
- **Large allocation**: `bytes(n)`, `bytearray(n)`, `os.urandom(n)`,
  `secrets.token_bytes(n)`, `random.randbytes(n)`, a repeated literal
  (`b"x" * n`) or a numpy `zeros`/`ones`/`empty`/`full`/`random` array whose
  constant size is greater than `max_fixture_bytes`.

The following are excluded:

- Tests marked or located as non-unit suites: `pytest.mark.integration`,
  `slow`, `network`, `e2e`, `live`, `vcr` and similar, including through
  `pytestmark`.
- Network/integration `skipif` and `skip` guards.
- `integration/`, `e2e/`, `functional/`, `perf/` and similar directories.
- `*Live*`/`*Integration*` test classes.
- Lines and test definitions with `# noqa` or `# noqa: TST-12`.

Equality is never flagged. The identity is
`<qualified test>:<sleep|network-call|large-allocation>`, for example
`TestUploader.test_upload:network-call`. A redefined test name becomes
`...#2`. Evidence cites the exact call lines.

Confidence is `high` for sleeps. It is also `high` for network calls when an
ancestor `conftest.py` was supplied. Otherwise it is `medium`, because an
unseen `conftest.py`/plugin could block or mock the network. AWS calls and
allocations are `medium`.

Static mode can show that a pattern exists, but not its runtime cost. It does
not follow helpers in other modules. It also does not see markers added by
collection hooks, pytest plugin or ini options (for example `--disable-socket`),
network calls inside libraries, or the size of fixture data files.

### Artifact detection rule

A finding is emitted when any observed field is strictly greater than its
matching context maximum. Equality is not flagged. Findings cite the exact
artifact fields that crossed thresholds, plus `duration_seconds` as the
baseline timing observation. The identity is `heavy-test-work` in a `test:`
scope, or `heavy-test-work:<test_id>` next to a static source in a `file:`
scope. No energy/emissions measurements are emitted. Wall-clock test duration
is not claimed as CPU time.

### Run

Same environment as the INF-01 example above; the CLI routes on `check_id`.
`tst12-a01-positive-input.json` is the artifact-mode example:

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst12/tst12-01-positive-input.json
```


## OBS-02 — Eager log message construction when the level is disabled

Flags Python logging calls whose message is built before the logger checks its
level, so the formatting runs even when production drops the record. The check
is static: source is parsed with `ast` and never imported or executed.
Shared static plumbing lives in `owner_d/static.py` (contract runner) and
`owner_d/logcalls.py` (logger-call recognition, reused by later OBS checks).

### Input

A contract v1 `input` payload with one `static` source per scope item. Scope
IDs use the form `file:<path>`; `content` is the file text. Only `.py` files
are supported in v1. No context settings are required.

### Detection rule

A call is flagged when all of the following hold:

- The receiver is a logger: the `logging` module, a name assigned from
  `getLogger`/`get_logger`/`getChild`/`bind`, loguru's `logger`, or a receiver
  conventionally named `log`/`logger`/`*_logger`.
- The level is `trace`/`debug` (`medium` confidence) or `info` (`low`
  confidence). `.log(logging.DEBUG, ...)` is resolved. WARNING and above are
  not flagged, because they are normally emitted.
- The message is an f-string with placeholders, `%`-formatting,
  `"...".format()` or concatenation of text with a non-constant value.

Exceptions that are not flagged: lazy arguments (`logger.debug("x=%s", x)`),
constant strings, calls inside an `if` that checks the level (directly via
`isEnabledFor`/`getEffectiveLevel`/`.level`, or through a flag assigned from
such a check), and lines with `# noqa` or `# noqa: G004` (or another
G001–G004 / W1201–W1203 / OBS-02 code).

The identity is `<qualified function>:<receiver>.<method>`, e.g.
`charge:logger.debug`. A repeated call in the same function becomes
`charge:logger.debug#2`, so identities survive line movement. Files that are
missing, not Python or fail to parse are left out of `evaluated_scope` with a
limitation. The result is then `partial`/`unavailable`, never clean. No
energy/emissions measurements are emitted: the check proves eager formatting,
not wasted CPU, because it cannot see the production log level or call
frequency.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs02/obs02-01-positive-input.json
```

## OBS-03 — Logging inside hot loops

Flags Python logging calls that run on every iteration of a loop or
comprehension in the same function, so log volume and logging CPU grow with
the number of items. Static only (same runner and logger recognition as
OBS-02); the profiler half of OBS-03 is not implemented in v1.

### Input

Same as OBS-02: one `static` source per `file:<path>` scope item, `.py` only,
no context settings.

### Detection rule

A recognised logger call (see OBS-02) is flagged when it is at
`trace`/`debug`/`info` level (or a `.log(level, ...)` whose level cannot be
resolved) and sits in a per-iteration position of a `for`/`async for`/`while`
body or `while` test, or a comprehension element, without crossing a
function, lambda or class boundary.

Confidence is `medium` for an unconditional call in a bounded loop and `low`
when the call is conditional (`if`, ternary, `and`/`or`, `match`, filtered
comprehension), the level is unresolved, or every enclosing loop is a
`while True` / `async for` loop (request or message loops, where per-event
logging is often intended). Nested loops are named in the summary.

Not flagged:

- WARNING and above, and any call inside an `except` handler within the loop:
  volume follows failures, not items (retry loops are OBS-11).
- Calls in the loop's iterable, `else` clause or a nested function.
- Calls guarded by a level check (`isEnabledFor`, `getEffectiveLevel`,
  `.level`, or a flag assigned from one), by a sampling test using `%`
  (`if i % 1000 == 0`), or by an opt-in verbosity switch (`if self.debug:`,
  `if verbose:`, `if options.log_*:`).
- Loops that are not hot: a literal collection or constant `range()` of at
  most 10 items, a body that ends in `break`/`return`/`raise` at top level, or
  a loop that calls `sleep`/`wait` each pass (polling).
- Calls followed by `return`/`raise` on their path to the loop (a final
  message). A following `break` drops only the innermost loop, so the call is
  still flagged against an enclosing hot loop.
- Lines with `# noqa` or `# noqa: OBS-03`.

The identity is `<qualified function>:<receiver>.<method>` with `#n` for
repeats, as in OBS-02. A call can legitimately match both OBS-02 (eager
formatting) and OBS-03 (per-iteration call); they are separate checks. No
measurements are emitted: the loop's iteration count, the call frequency of
the enclosing function and the production log level are unknown.
## LLM-07 — Unbounded LLM outputs (static proxy: no output-token limit)

Flags LLM API calls in Python source that set no output-token limit. The
taxonomy detects verbose outputs from usage logs; this v1 is a static proxy: it
proves that a call has no explicit cap, not that responses are long or tokens
are wasted, and it emits no token counts. LLM-call recognition lives in
`owner_d/llmcalls.py` so later LLM checks (LLM-01, LLM-15) can reuse it.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

| Call | Flagged when none of these is set |
| --- | --- |
| Bedrock `converse` / `converse_stream` (boto3) | `inferenceConfig.maxTokens`, or a token key in `additionalModelRequestFields` |
| OpenAI `chat.completions.create/parse/stream` (also `beta.`) | `max_completion_tokens`, `max_tokens` (also via `extra_body`) |
| OpenAI `responses.create/parse/stream` | `max_output_tokens` |
| OpenAI v0 `openai.ChatCompletion.create` | `max_tokens` |

Clients are recognised from `.client("bedrock-runtime")` on boto3 or a
session, `OpenAI()`/`AsyncOpenAI()`/`AzureOpenAI()`, the module-level `openai`
client, local factory functions and type annotations; plain client names are
tracked per scope. Confidence is `medium` when the client (or Bedrock's
`modelId=` keyword) establishes the provider, and `low` for a
`chat.completions` call on a client not created in the file (the file must
import `openai`). Clients built from other imported SDK classes (`Groq()`,
`Together()`, ...) are not matched.

Not flagged, because the cap cannot be seen statically: `**kwargs` or config
dicts that are parameters, built by calls or mutated after assignment; Bedrock
Prompt management (`promptVariables`, prompt ARNs) and OpenAI stored prompts
(`prompt=`). Not evaluated in v1: Bedrock `invoke_model` bodies (defaults are
model-specific, e.g. 512 tokens for Llama and Titan), the Anthropic SDK
(`max_tokens` is required), LangChain/LiteLLM and other languages. `# noqa` or
`# noqa: LLM-07` suppresses a call.

The identity is `<qualified function>:<provider>.<api>`, e.g.
`summarise:bedrock.converse`; a repeat in the same function gets `#2`. Missing,
non-Python or unparseable files are left out of `evaluated_scope`, never
reported clean.
## OBS-01 — DEBUG/TRACE logging enabled in production

Flags literal DEBUG/TRACE log levels in production configuration and in Python
code that configures logging. The check is static: config files are scanned
line by line (so evidence is the exact source line), Python is parsed with
`ast`, and nothing is imported or executed. The log-volume telemetry half of the
check (CloudWatch) is out of scope for v1, and the result says so in
`coverage.limitations`.

## INF-09 — Oversized container images / unneeded packages

Flags Dockerfile instructions that put avoidable bytes into the shipped image.
The check is static: the Dockerfile is read as text and is never built, and no
image is pulled. Non-Python text files go through `owner_d/textstatic.py`, a
sibling of `static.py` with the same coverage rules (one `file:` scope per
file; missing, unsupported or unparseable files are omitted with a limitation).
`owner_d/dockerfile.py` is a small stdlib Dockerfile reader. It handles
`# escape=`, continuations, comment lines inside continuations, heredocs,
`FROM ... AS` stages, global `ARG` defaults and exec-form `RUN`.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported: YAML, JSON, TOML, INI/CFG, `.properties`, `.env` files
(`.env`, `.env.*`, `*.env`), Dockerfiles and Python. Optional context:
`environment: "production"` declares that files without an environment marker
are production config. Config support uses the optional `parse` hook in
`owner_d/static.py`.

### Detection rule

A setting is flagged when all of the following hold:

- Its key path is a log-level setting: `LOG_LEVEL`/`*_LOG_LEVEL`/`logLevel`/
  `RUST_LOG` anywhere in the path (`Logging.LogLevel.Default`), `level` under a
  logger key (`logging.level.root`, `loggers.app.level`, `[logger_root] level`)
  or log4j `rootLogger`. Kubernetes/ECS `name: LOG_LEVEL` + `value:` pairs,
  compose `- LOG_LEVEL=debug` items and Dockerfile `ENV` are resolved.
- Its literal value is DEBUG or TRACE (case-insensitive). Lists such as
  `DEBUG, stdout` or `info,app=trace` and `${VAR:-debug}` defaults count.
- In Python: `logging.basicConfig(level=DEBUG)`, `<logger>.setLevel(DEBUG)`,
  `os.getenv("LOG_LEVEL", "DEBUG")` defaults, and `LOGGING`-style dict or
  assignment settings. Calls inside an `if` (other than `__main__`) are skipped,
  because the level is then chosen at runtime.

The environment comes from path tokens, the keys leading to the setting and the
Docker stage name:

| Marker | Example | Outcome |
| --- | --- | --- |
| non-production | `config/development.yaml`, `tests/`, `.env.local`, `staging:` key, `AS dev` stage, `.github/` | not flagged |
| production | `config/production.yaml`, `values-prod.yaml`, `prod:` key, `AS production` stage | config `high`, Python `medium` |
| production template | `.env.production.example` | `medium` |
| none, but `context.environment: production` | `config/settings.yaml` | `medium` (Python `low`) |
| none | `config/settings.yaml` | `low`, summary says production use is not established |

Not flagged: handler/appender levels (they only filter), keys named after a
level (`DEBUG_LOG_LEVEL`, `TRACE_LOG_LEVEL = 5`), framework debug switches
(`DEBUG = True`, `APP_DEBUG`), CLI flags (`--log-level debug`), commented-out
lines and lines with `# noqa: OBS-01` (for example, an acknowledged incident override).

The identity is `log-level:<key path>` for config (for example
`log-level:logging.level.root`) and `<qualified function>:<call or key path>`
for Python. Repeats get `#2`. Unparseable files (invalid JSON/TOML/INI,
tab-indented YAML), flow-style YAML/TOML mappings that contain a level, and
unsupported file types are left out of `evaluated_scope` with a limitation, so
they are never reported clean. No measurements are emitted.
item. Supported files: `Dockerfile`, `Dockerfile.*`, `*.Dockerfile`,
`Containerfile`, `Containerfile.*` and `*.Containerfile`. No context settings
are required.

### Detection rule

Only the shipped stages are checked: the last stage, plus the stages it is
built `FROM`. A builder stage that is only used through `COPY --from` is never
flagged. Rules (identity `<stage>:<rule>`):

| Rule | Flagged when | Not flagged when | Confidence |
| --- | --- | --- | --- |
| `full-base-image` | The base is `python`/`node`/`ruby`/`perl` with a full Debian tag (`3.12`, `lts`, `3.12-bookworm`, no tag) | The tag is `-slim`/`-alpine`/`-windowsservercore`/unknown, or the image ref still contains an unresolved `$VAR` | medium |
| `toolchain-base-image` | The base is `golang`/`rust`/`maven`/`gradle` **and** the shipped stages run `go build`/`cargo build`/`mvn package`/`gradle build` | There is no build step (the toolchain is what the image is for, e.g. codegen images) | medium |
| `apt-install-recommends` (DL3015) | `apt-get install` without `--no-install-recommends` | The same RUN or an earlier RUN sets `APT::Install-Recommends "false"` | medium |
| `apt-lists-kept` (DL3009) | `apt-get update` and the same RUN does not remove `/var/lib/apt/lists` | The same RUN removes the lists, runs `apt-get dist-clean`, or uses a cache/tmpfs mount on `/var/lib/apt` | medium |
| `apk-cache-kept` (DL3019) | `apk add` without `--no-cache` | The same RUN removes `/var/cache/apk`, or uses a cache mount | medium |
| `yum`/`dnf`/`microdnf-cache-kept` (DL3032/DL3040/DL3041) | `install` without `<tool> clean all` in the same RUN | The same RUN cleans the cache, or uses a cache mount | medium |
| `pip-cache-kept` (DL3042) | `pip install` (incl. `pip3`, `python -m pip`) without `--no-cache-dir` | `PIP_NO_CACHE_DIR` is set (ENV/ARG/inline), the same RUN removes `.cache`, a cache mount is used, or `pip config` sets `no-cache-dir` | medium |
| `node-dev-dependencies` | `npm install`/`npm ci`/`yarn install`/`pnpm install` with no package arguments and no production flag | `--omit=dev`/`--production`/`--prod`, `NODE_ENV` or `npm_config_production` is set, `npm prune` runs in the same RUN, or explicit packages are given (`npm install -g pm2`) | low |
| `build-toolchain` | apt/apk/yum/dnf installs `build-essential`, `gcc`, `g++`, `make`, `cmake`, `clang`, `build-base`, `musl-dev`, ... | The same RUN removes packages again (`apk del .build-deps`, `apt-get purge`) | medium |

Cleanup only counts in the **same** `RUN`, because a later layer cannot shrink
an earlier one. Suppressions: `# noqa` / `# noqa: INF-09` on the instruction
or in the comment lines directly above it, and `# hadolint ignore=DL3009`
(plus `# hadolint global ignore=...`) for the rule with that hadolint code.

The following are not evaluated. They are listed as limitations, never
reported clean:

- development/test/debug images: a final stage named `dev`, `dev-envs`,
  `development`, `debug` or `test`, a file such as `Dockerfile.dev` or
  `Dockerfile.debug`, or a file under `.devcontainer/`, `test/`, `tests/` or
  `e2e/`
- unparseable files: no `FROM`, an unknown instruction or an unterminated
  heredoc
- files that are not Dockerfiles

### Limitations

The image size and pull frequency are unknown: nothing is built or pulled, so
no measurements are emitted. The build `--target` is unknown, so the last
stage is assumed. The detector cannot see base-image contents, pip/npm config
files or `.dockerignore`. As a result, `COPY . .` without a `.dockerignore` is
not checked.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs03/obs03-01-positive-input.json
  detectors/owner-d/tests/fixtures/llm07/llm07-01-positive-input.json
  detectors/owner-d/tests/fixtures/obs01/obs01-01-positive-input.json
  detectors/owner-d/tests/fixtures/inf09/inf09-01-positive-input.json
```


## INF-08 — Missing CPU/memory limits (noisy neighbor)

Flags containers in deployment manifests that a shared host cannot bound. The
check is static: manifests are read as text and are never applied, rendered or
sent to a cluster or to AWS. It uses the `textstatic.py` runner. YAML is read
by `owner_d/miniyaml.py`, a stdlib reader with line numbers for the YAML
subset that Kubernetes, Compose and CloudFormation use: block and flow
collections, block scalars, multiple documents, anchors and merge keys, and
tags. PyYAML is not a dependency. Anything outside that subset is reported as
*not parsed*. The same applies to Helm/Go template actions that appear outside
quoted strings.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- **Kubernetes** `.yaml`/`.yml`: `Pod`, `Deployment`, `StatefulSet`,
  `DaemonSet`, `ReplicaSet`, `Job`, `CronJob`, `ReplicationController`,
  `DeploymentConfig` and `Rollout`, including inside `kind: List`
- **Docker Compose** `.yaml`/`.yml`: a top-level `services` mapping
- **Amazon ECS** `.json`: a task definition, or `describe-task-definition`
  output with the `taskDefinition` wrapper
- **CloudFormation** `.json`/`.yaml`: `AWS::ECS::TaskDefinition` resources

No context settings are required.

### Detection rule

| Format | Identity | Flagged when | Confidence |
| --- | --- | --- | --- |
| Kubernetes | `<Kind>/[<ns>/]<name>:<container>` | No requests or limits at all (BestEffort), or no `limits.memory` | medium |
| Kubernetes | same | Memory limit set but no CPU request (and no CPU limit, so no request is defaulted) | low |
| Compose | `service/<name>` | No `mem_limit` and no `deploy.resources.limits.memory` | low |
| ECS / CloudFormation | `task/<family or logical id>:<container>` | Neither the task nor the container sets a hard `memory`. `memoryReservation` is only a soft limit. | medium |
| ECS / CloudFormation | same | Neither the task nor the container sets CPU units (`cpu` 0 counts as unset) | low |

A missing CPU limit on its own is **not** flagged. Under contention, CPU is
shared by request/weight, and many operators leave CPU limits unset on purpose
to avoid throttling. Kubernetes copies a limit into an unset request, so
`limits` alone counts as requested.

The following are not flagged:

- containers covered by a `LimitRange` in the same file and the same
  namespace that sets `default`/`defaultRequest`
- workloads annotated `ignore-check.kube-linter.io/unset-*` or
  `polaris.fairwinds.com/*exempt`
- containers with `# noqa` / `# noqa: INF-08` on the line or directly above it
- Compose services that use `extends`
- Fargate task definitions, because the task-level `cpu`/`memory` they
  require bound every container
- `initContainers`

Evidence is the container's `name` line, or the Compose service key line.

The following are not evaluated. They are listed as limitations, never
reported clean:

- Helm/Go templates
- invalid JSON or YAML outside the subset
- Kubernetes JSON manifests
- YAML/JSON files that contain no Kubernetes objects, Compose services, ECS
  task definitions or CloudFormation resources
- Compose files marked as development/test, such as
  `docker-compose.override.yml`, `compose.dev.yaml`, `*.test.yml` or a file
  under `test/` or `.devcontainer/`

### Limitations

The taxonomy row describes runtime impact ("one container starves others").
v1 is static only and uses no telemetry, so it proves that a container is
unbounded in the manifest, not that it starves anything, and it emits no
measurements. Defaults applied outside the file are not visible: LimitRange
or ResourceQuota objects in other files, Kustomize patches, Helm values,
admission webhooks, EKS Fargate profiles (one pod per micro-VM) and ECS
capacity settings.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/inf08/inf08-01-positive-input.json
```

## TST-06 — Unknown Test (test without assertions)

Flags unittest/pytest tests that contain no assertion, following PyNose's
Unknown Test rule ("a test case does not contain a single assertion
statement"; [Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639),
adopted from [tsDetect](https://testsmells.org/pages/testsmells.html)). Such a
test passes whenever the code it runs does not raise. The check is static:
source is parsed with `ast` and never imported or executed. Test and assertion
recognition shared by the TST checks lives in `owner_d/testsmells.py`.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`; `.py` only). No context settings are required.

### What is a test

Tests follow the default unittest and pytest discovery conventions:

- `test*` methods of classes deriving from a `*TestCase` base (directly or via
  a class in the same file), in any file;
- in pytest modules (`test_*.py`, `*_test.py`): module-level `test*`
  functions, and `test*` methods of `Test*` classes (without `__init__`) or of
  classes with an imported `*Test`/`*Tests`/`*TestBase` base.

`@pytest.fixture` functions and classes with `__test__ = False` are not tests.
Custom `python_files`/`python_classes` settings are not read. A file in scope
with no recognised test is still evaluated. It has no findings and gets a
`<scope>: evaluated; no unittest/pytest test functions recognised` limitation.

### Detection rule

A test is flagged when nothing in its body asserts, including nested
functions. These count as assertions: `assert` statements, `self.assert*` and
`self.fail*` calls, and any call whose name contains `assert`. That last one is
PyNose's rule and covers mock `assert_called*` and `numpy.testing.assert_*`.
Also counted: `pytest.raises`/`warns`/`deprecated_call`/`fail`,
`raise AssertionError`, and local aliases such as `eq = self.assertEqual`. An
empty test body is reported as "has an empty body".

These are not flagged:

- assertions delegated to a helper defined in the same file (`self.m()` or
  `f()`, up to 3 calls deep);
- helpers or decorators named like checkers, whose body may be in another file.
  This covers calls named `check*`/`verify*`/`expect*`/`test*`/`*_test`, and
  decorators containing `assert`/`check`/`compar`/`expect`/`verif` or ending in
  `_test` (e.g. matplotlib's `@image_comparison`);
- tests that never run their body: `@unittest.skip`, `@pytest.mark.skip`, or a
  first statement of `pytest.skip()`/`self.skipTest()`/`raise SkipTest`.
  Conditional `skipIf`/`skipif` tests are still evaluated;
- benchmark tests (a `benchmark` fixture parameter), docstring-only doctest
  containers, and lines with `# noqa: TST-06`.

The identity is the qualified test name, e.g. `CartTest.test_add_item`. A
redefined name becomes `test_retry#2`. Findings are `medium` confidence. Test
names are not judged: the taxonomy's "non-descriptive name" half is out of
scope for v1. No measurements are emitted. The environmental link is indirect:
CI time spent on tests that check nothing.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst06/tst06-01-positive-input.json
```

## TST-05 — Duplicate Assert

Flags an assertion that repeats an earlier assertion in the same test when the
repeat cannot fail on its own. The rule follows PyNose's Duplicate Assert ("a test case contains more than
one assertion statement with the same parameters";
[Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639), adopted from
[tsDetect](https://testsmells.org/pages/testsmells.html)), narrowed to remove
deliberate re-checks. It is static (`ast` only), uses the same input, test
recognition and per-file "nothing to flag" note as TST-06, and needs no
context settings.

### Detection rule

Within one statement block of a test, the check scans each run of consecutive
assertion statements (`assert ...` or a bare assertion call, see TST-06). A
statement is flagged when it repeats an earlier assertion in the same run. Two
assertions are the same when they have the same callee, operands and
non-message keywords. They are compared both by AST and by source tokens, so
whitespace, comments and messages are ignored, but `p[:4]` vs `p[:4:]` or
`f".."` vs `fr".."` still differ.

The run restarts, so nothing is compared across it, at:

- any non-assertion statement, because the test may have changed state;
- an assertion that may run code: an operand with a call other than
  `len`/`isinstance`/`issubclass`/`type`/`id`/`callable`/`abs`/`round` (so
  `list(it)` or `reader.read()` repeated is a re-read, not a duplicate), an
  `await`/walrus, call-form `assertRaises`/`assertWarns`/`assertLogs`/
  `pytest.raises`, or a custom `TestCase` assertion such as
  `assertTemplateUsed`.

Assertions in different branches, loop iterations, nested functions or lambdas
are never compared. `with self.assertRaises(...)` blocks guard different code
and are not compared. `# noqa: TST-05` suppresses a finding.

This is a deliberate narrowing. PyNose flags any textual repeat in the test,
including re-checks after the test mutates state. On about 80k real tests,
that broad rule gave 17,314 matches against 92 for this one. Every one of the
12 removed matches sampled was a deliberate "assert, act, assert again"
re-check. Attribute and property reads are assumed
side-effect free, so deliberate idempotence checks of cached properties are
still reported; mark them with `# noqa: TST-05`.

Each repeat is one finding, with evidence on the repeated line and the first
line named in the summary. The identity is
`<qualified test>:<assertion callee>` (e.g. `CartTest.test_total:self.assertEqual`),
with `#n` for further repeats in the same test. Findings are `medium`
confidence. No measurements are emitted: the waste is one comparison per run
plus maintenance, a weak environmental link.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst05/tst05-01-positive-input.json
```

## TST-10 — Redundant Assertion

Flags assertions that always pass whatever the code under test does, following
PyNose's Redundant Assertion rule. PyNose describes it as "the expected and
actual parameters of equality are the same, e.g. `assertEqual(X, X)`, or the
assertion of truth is carried out on the unchangeable object, e.g.
`assertTrue(True)`" ([Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639),
adopted from [tsDetect](https://testsmells.org/pages/testsmells.html)). It is
static (`ast` only), uses the same input, test recognition and per-file
"nothing to flag" note as TST-06, and needs no context settings.

### Detection rule

An assertion in a test is flagged when its outcome is fixed and true:

- the asserted condition is a literal (`assert True`, `self.assertTrue(1)`,
  `self.assertFalse([])`, `self.assertIsNone(None)`);
- `assert (cond, "msg")`, a non-empty tuple that is always truthy (pyflakes
  F631);
- a comparison of a literal with the same literal (identical source tokens):
  `assertEqual(1, 1)`, `assert "a" == "a"`, `assert None is None`,
  `assertLessEqual("ant", "ant")`, `np.testing.assert_equal([1, 2], [1, 2])`.
  `not` is followed.

These are not flagged:

- always-false assertions (`assert False, "unreachable"`,
  `self.assertTrue(False, msg)`), which are explicit failure markers;
- literals spelled differently (`0o20 == 16`, `'a' in 'abc'`), which exercise
  the language itself;
- `assertEqual(obj, obj)` / `x == x` on non-literals, which tests use to check
  `__eq__`, NaN handling or identity caching. PyNose's textual rule includes
  them. On real suites these were about half of the matches, and every sampled
  one was a deliberate reflexivity test;
- `is` between equal non-singleton literals (interning is an implementation
  detail);
- `# noqa: TST-10` (or `F631`/`PT009`).

The identity is `<qualified test>:<assertion callee>`, with `#n` for repeats.
Findings are `medium` confidence and no measurements are emitted: the cost is a
trivial check per run plus misleading coverage.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst10/tst10-01-positive-input.json
```

## TST-09 — Conditional Test Logic

Flags control structures in a test that decide whether, or how often,
assertions run. This follows PyNose's Conditional Test Logic ("a test case contains one or more
control statements (i.e., if, for, while)";
[Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639), adopted from
[tsDetect](https://testsmells.org/pages/testsmells.html)) in the refined form
of PyNose's current inspection: the structure must contain an assertion. That
is also the intent of eslint-plugin-jest's `no-conditional-in-test`. The
original ASE rule flags any control statement, and the paper reports false
positives for loops that only build data. The check is static (`ast` only),
uses the same input, test recognition and per-file "nothing to flag" note as
TST-06, and needs no context settings.

### Detection rule

One finding per `if` (an `elif` chain counts once), `for`/`async for`,
`while`, `match`, comprehension, or conditional expression in a test body that
contains an assertion (see TST-06). Confidence:

- `medium`: some path runs no assertion. This covers an `if` without an
  asserting `else`, a `match`, a `while`, a conditional expression, and a loop
  or comprehension over a runtime iterable, where an empty iterable checks
  nothing;
- `low`: the assertions always run but vary with the branch or case. This
  covers an `if`/`elif`/`else` where every branch asserts, and a loop over a
  fixed collection: a non-empty literal, `range(<n>)`, `"a b".split()`, a
  module or class constant bound once to a literal, or
  `enumerate`/`zip`/`sorted`/`chain`/`+` of those. These are parametrization
  candidates.

These are not flagged:

- `if` guards whose only check is `self.fail()`/`pytest.fail()`/
  `raise AssertionError`, because that is a hand-written assertion;
- skip guards (`if not HAS_DB: self.skipTest(...)`), which contain no
  assertion;
- loops whose body uses `self.subTest(...)` or pytest-subtests'
  `subtests.test(...)`;
- loops that only build data;
- a conditional inside an assertion's operand;
- control flow inside nested functions or lambdas;
- lines with `# noqa: TST-09` (or `PT018`).

The identity is `<qualified test>:<kind>` (`if`, `for`, `while`, `match`,
`comprehension`, `if-expression`), with `#n` for repeats; nested structures
are separate findings. No measurements are emitted: the environmental link is
weak (CI time on tests that may assert nothing).

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst09/tst09-01-positive-input.json
```

## TST-01 — Assertion Roulette

Flags tests with more than one assertion that has no explanation message,
following PyNose's Assertion Roulette rule ("a test case contains more than
one assertion statement without an explanation/message";
[Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639), adopted from
[tsDetect](https://testsmells.org/pages/testsmells.html)). It is static (`ast`
only), uses the same input, test recognition and per-file "nothing to flag"
note as TST-06, and needs no context settings.

### Detection rule

Only assertions with a message slot are counted:

- `assert` statements (message after the comma);
- `unittest` assert methods with a known signature, where the message is
  positional at the documented index or given as `msg=`;
- numpy-style `assert_*` helpers that take `err_msg=`.

Mock `assert_called*`, `pytest.raises`/`warns`, `assertRaises` contexts and
custom `TestCase` assertions are ignored.

A test is flagged when at least 2 counted assertions have no message. That
includes assertions in nested helpers defined inside the test, as in PyNose.
One finding per test; evidence starts at the `def` line and the summary lists
the undocumented lines. Confidence:

- `medium` when at least two undocumented assertions are truthiness checks
  (`assertTrue`/`assertFalse`/`assert_`), whose failure reads only
  "False is not true";
- `low` otherwise. `assertEqual`-style failures print both operands, and
  pytest rewrites bare `assert` to show values, so the cost is mostly
  readability.

`# noqa: TST-01` on the `def` line suppresses a finding. The identity is the
qualified test name, with `#n` for a redefined name.

The taxonomy cites the strongest energy association for this smell (Kendall
tau 0.615, SRC-15). That evidence comes from JUnit/Maven projects and is not
shown to transfer to Python, so no measurements are emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst01/tst01-01-positive-input.json
```

## LLM-01 — No prompt caching for stable prompt prefixes (static proxy)

Flags LLM API calls in Python source that resend a large, static prompt prefix
without the provider's prompt-caching marker. The taxonomy detects this from
token-usage logs; this v1 is a static proxy: it proves that a statically large
prefix is sent with no cache marker, not that calls repeat within the cache
TTL, that the cache would hit, or any cost. It emits no token counts.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

The prefix follows the providers' cache order, `tools` → `system` →
`messages`: fully static tool definitions (compact JSON length), then the
static leading text of the system prompt, then the static leading text of the
messages (each stops at the first dynamic part, e.g. an f-string placeholder).
Text is resolved from literals, single-assignment names, `+`, f-strings,
`.format`/`%` (up to the first field), `str.join`, `.strip()`,
`textwrap.dedent` and `inspect.cleandoc`. Tokens are estimated at 4
characters per token, which undercounts Claude (about 3.5 characters per
token, and about 30% more tokens on Claude 4.7+).

| Call | Flagged when | Cache marker that is missing |
| --- | --- | --- |
| Anthropic SDK `messages.create/stream/parse` (also `beta.`) | estimated prefix ≥ model minimum | top-level `cache_control` or `cache_control` on a block |
| Bedrock `converse` / `converse_stream` (Claude, Nova) | same | `cachePoint` block in `toolConfig.tools`, `system` or `messages` |
| Bedrock `invoke_model` with `body=json.dumps(<static dict>)` | same | `cache_control` (Claude) / `cachePoint` (Nova) |

Minimum cacheable prefix per model (the larger of the Anthropic API and
Bedrock figures): 512 tokens for Claude 5.x (Opus/Sonnet/Haiku 5.5, Opus 5,
Fable/Mythos 5 and 5.1) except Sonnet 5 (1,024); 1,024 for Opus 4.8,
Opus 4.1/4, Sonnet 4.6/4.5/4, Claude 3.7 Sonnet and Claude 3.5 Sonnet v2;
2,048 for Mythos Preview and Haiku 3.5; 4,096 for Opus 4.7/4.6/4.5 and
Haiku 4.5; 1,024 for Nova Micro/Lite/Pro/2 Lite (whose tool definitions do not
take checkpoints and are not counted). Opus 4.1/4, Sonnet 4, Haiku 3.5 and
Mythos Preview count only on the Anthropic SDK, because Bedrock's table does
not list them. An Anthropic SDK call whose model is not
statically known uses 4,096; a Bedrock call must name a model from Bedrock's
explicit-caching table. Confidence is `medium` for the Anthropic SDK (nothing
is cached without `cache_control`) and `low` for Bedrock (Claude and Nova also
get best-effort implicit caching there).

Not flagged: OpenAI (prompt caching is automatic for prompts of 1,024 tokens
or more); a file that mentions `cache_control`/`cachePoint` anywhere; tools,
system blocks or request bodies that are not statically resolvable (a cache
marker could be inside); `**kwargs`, `extra_body`, Bedrock Prompt management;
prompts loaded from files or imported from other modules; one-shot calls at
module level or in a function named `main` (outside a loop). Cache markers
added to `messages` in another module are not seen. `# noqa` or
`# noqa: LLM-01` suppresses a call.

The identity is `<qualified function>:<provider>.<api>`, e.g.
`answer:anthropic.messages.create`; a repeat in the same function gets `#2`.
Missing, non-Python or unparseable files are left out of `evaluated_scope`,
never reported clean.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm01/llm01-01-positive-input.json
```

## LLM-15 — Tool-definition sprawl (large tool registries in every call)

Flags LLM API calls in Python source that load more tool definitions into the
context up front than the configured limit, or tool definitions whose
estimated size exceeds the configured token budget. This v1 is a static proxy:
it proves the size of a statically known tool list, not that the tools degrade
accuracy or cost a measured amount. It emits no token counts as measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only) and the context settings below.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_tools_per_call` | Most tools a call may load up front | `20` |
| `max_tool_definition_tokens` | Largest estimated size of the loaded tool definitions | `10000` |

The thresholds are judgment calls, so they are required. OpenAI suggests
"fewer than 20 functions available at the start of a turn" (a soft limit).
Anthropic recommends tool search from 10 tools or 10k tokens of definitions,
and reports that tool selection degrades past 30–50 tools. The reference
values are the less aggressive of these. Missing or invalid settings make the
result `unavailable`.

### Detection rule

A finding is emitted when the number of tools loaded up front is strictly
greater than `max_tools_per_call`, or the estimated size of fully static
definitions (compact JSON, 4 characters per token) is strictly greater than
`max_tool_definition_tokens`.

| Call | Tool list |
| --- | --- |
| Anthropic SDK `messages.create/stream/parse` (also `beta.`) | `tools` |
| OpenAI `chat.completions.*`, `responses.*` | `tools`, legacy `functions` |
| Bedrock `converse` / `converse_stream` | `toolConfig.tools` |
| Bedrock `invoke_model` with `body=json.dumps(<static dict>)` | `tools` or `toolConfig.tools` |

Tools are counted from list literals (with `*spread` and `+`),
single-assignment names, `list(...)`, `dict.values()` and unfiltered
comprehensions over a static list or dict. Entries with `defer_loading: true`,
tool-search tools and Bedrock `cachePoint` entries do not count. MCP toolsets
count as one entry, so counts are lower bounds. The summary notes when the
same registry is passed to several calls in the file.

Not flagged: tool lists that are parameters, sliced, filtered, mutated
(`remove`/`insert`) or built from runtime data such as MCP `list_tools()`;
`**kwargs` and `extra_body`; files that use `defer_loading`, tool search or
`allowed_tools` anywhere. Other SDK clients (`Groq()`, ...) are not matched.
`# noqa` or `# noqa: LLM-15` suppresses a call. The identity is
`<qualified function>:<provider>.<api>` with `#2` for repeats. Missing,
non-Python or unparseable files are left out of `evaluated_scope`, never
reported clean.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm15/llm15-01-positive-input.json
```

## LLM-04 — Whole context passed to every pipeline step (static proxy)

Flags LLM steps in Python source that receive the whole conversation history,
or the whole document, again after an earlier step in the same function
already received it. They could receive a slice, a summary or only the
previous step's output instead. This v1 is a static proxy: it shows what is
re-sent, not how many tokens it costs, and it emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

Steps are recognised LLM calls in the same function (or module body). Calls in
nested functions form their own scope. Step B follows step A when it comes
later in the source, the two are not in alternative branches (if/else arms,
`try` body vs `except` handler, `match` cases), and A's result is not returned
or raised.

| Rule | B is flagged when | Confidence |
| --- | --- | --- |
| H — whole history | A and B both send all of the same name `h` as the conversation (`h`, `h + [...]`, `[*h, ...]`), and `h` grew between them (`append`/`extend`/`insert`/`+=`/`h = h + ...`) or B adds messages after it | `medium` (`low` for an unknown OpenAI-compatible client) |
| D — whole document | A and B both embed the same name `d` whole in their prompt text (content, f-string field, `+`, `%`, `.format`, `str.join`, `str()`/`json.dumps()`, through single-assignment prompt variables), B also receives an earlier step's output, and B is not the last step | `low` |

The conversation is `messages` for Anthropic `messages.*`, OpenAI chat
completions and Bedrock `converse`/`converse_stream`, `input` for the OpenAI
Responses API, and `messages` in a static `invoke_model` body
(`json.dumps(<dict>)`). Prompt text also includes `system` / `instructions`.
For rule D, `d` must not come from an earlier step's output. Its name must
contain a context-like word (`doc`, `document`, `context`, `history`,
`transcript`, `conversation`, `corpus`, `article`, `report`, `page`,
`chunk`, `passage`, `source`, `content`, `text`, `memory`, `note`,
`thread`, `email`, `record`, `knowledge`, `file`), so short inputs such as
`question` stay out.

Not flagged:

- a single step;
- slices (`h[-4:]`), summaries or any other rebinding between the steps;
- trimming (`pop`/`remove`/`clear`/`del`/item assignment), or passing the
  history to another function, which might compact it;
- the same unchanged request sent twice (fallback or ensemble);
- the last step for documents (final synthesis);
- files that mention `cache_control`/`cachePoint`/`cache_point` anywhere;
- functions that continue a tool-use turn (`tool_result`, `toolResult`,
  `function_call_output`, `tool_call_id`, role `"tool"`);
- requests with `context_management`, `previous_response_id`,
  `conversation`, `truncation` or `extra_body`, and `**kwargs` that are not
  statically known;
- other SDK clients (`Groq()`, ...).

`# noqa` or `# noqa: LLM-04` suppresses a step.

Not evaluated: LangChain/LangGraph/LlamaIndex/LiteLLM chains and graph state;
steps split across functions or modules; histories held in attributes or
subscripts (`self.messages`, `state["messages"]`); per-turn re-sending in a
loop with one call site (LLM-14). OpenAI's automatic prompt caching may
discount a re-sent prefix, but cached tokens still fill the context window.

The identity is `<qualified function>:<provider>.<api>`, e.g.
`research:anthropic.messages.create`; a repeat in the same function gets
`#2`. Missing, non-Python or unparseable files are left out of
`evaluated_scope`, never reported clean.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm04/llm04-01-positive-input.json
```

## TST-04 — Magic Number Test

Flags tests whose assertions compare against bare numeric literals that
nothing in the test names or explains. This follows PyNose's Magic Number
Test rule ("an assertion method that contains a numeric literal as an
argument"; [Wang et al., ASE 2021](https://arxiv.org/abs/2108.04639), adopted
from [tsDetect](https://testsmells.org/pages/testsmells.html)). It is static
(`ast` only), uses the same input, test recognition and per-file "nothing to
flag" note as TST-06, and needs no context settings.

### Detection rule

A literal is counted when it is a direct operand (optionally signed, or inside
`pytest.approx(...)`) of:

- a `unittest` assert method (the compared operands only, not `msg`,
  `places` or `delta`);
- a numpy.testing comparison such as `assert_equal`/`assert_allclose` (first
  two positional arguments, not `rtol`/`atol`);
- a comparison in an `assert` statement or in `assertTrue`/`assertFalse`
  (`x == 5`, `0 < x < 10`, `a == 2 and b == 3`). PyNose only checks
  unittest method arguments; this extends the same rule to pytest.

These are not counted:

- `-1`, `0`, `1` and booleans;
- literals inside expressions or call arguments (`f(3)`, `3 * 14`, `xs[2]`),
  because the expression shows where the value comes from;
- assertions with a message, a trailing comment, or a comment-only line
  directly above them (`# noqa`, `# type:` and `# pragma` are directives, not
  explanations);
- lines with `# noqa: TST-04` (or ruff's `PLR2004`).

One finding per test; evidence starts at the `def` line and the summary lists
each literal with its line. `# noqa: TST-04` on the `def` line suppresses the
whole test. Confidence:

- `medium` when at least one literal is unexplained;
- `low` when every literal is compared with `len(...)`, compared with a
  `status`/`status_code`/`returncode`/`errno`-like name, or repeats a literal
  from the test's own setup (`make_rows(5)` ... `== 5`).

The identity is the qualified test name, with `#n` for a redefined name. The
taxonomy's energy association (Kendall tau 0.385, SRC-15) comes from
JUnit/Maven projects and is not shown to transfer to Python, so no
measurements are emitted. The JS proxy (`no-magic-numbers`) is out of scope.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst04/tst04-01-positive-input.json
```

## TST-07 — Verbose Test

Flags tests whose body has more statements than a configured limit, following
the size rule of JNose's
[Verbose Test](https://github.com/arieslab/jnose-core/blob/main/src/main/java/io/github/arieslab/core/testsmelldetector/testsmell/smell/VerboseTest.java)
("if a test method contains statements that exceed a certain threshold, the
method is marked as smelly"; `MAX_STATEMENTS = 30`). Meszaros lists Verbose
Test as another name for
[Obscure Test](http://xunitpatterns.com/Obscure%20Test.html). It is static
(`ast` only) and uses the same input, test recognition and per-file "nothing to
flag" note as TST-06.

### Context settings (required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_test_statements` | Most statements a test body may have | `30` |

The limit is a judgment call, so it is required. `30` is JNose's default.
A missing or invalid setting makes the result `unavailable`.

### Detection rule

A test is flagged when its body has strictly more than `max_test_statements`
statements. JNose compares the line span of the body; this check counts
statements instead, so it flags fewer tests than the line rule (only
semicolon-joined statements can make the count exceed the line count):

- statements in nested blocks (`if`, `for`, `with`, `try`) and in nested
  functions and classes count;
- the docstring, comments, blank lines and the extra lines of a multi-line
  statement (a large expected literal, a call with many arguments) do not.

`setUp`, fixtures and helpers called by the test are not counted, and
unconditionally skipped tests are not flagged. One finding per test; evidence
starts at the `def` line. The summary gives the statement count, the code line
count and how many statements come before the first assertion (in-line setup).
Confidence:

- `medium` above twice the limit;
- `low` otherwise.

`# noqa: TST-07` on the `def` line suppresses a finding. The identity is the
qualified test name, with `#n` for a redefined name.

The taxonomy cites a moderate energy association for this smell (Kendall tau
0.246, SRC-15). That evidence comes from JUnit/Maven projects and is not shown
to transfer to Python, so no measurements are emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst07/tst07-01-positive-input.json
```

## OBS-07 — Uniform retention (no tiering) for CloudWatch Logs

Flags CloudWatch Logs log groups that keep a non-trivial amount of data in
CloudWatch Logs storage forever, or for longer than a configured hot horizon,
with no compliance marker. By default, "log data is stored in CloudWatch Logs
indefinitely"
([retention](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html#SettingLogRetention)).
CloudWatch Logs has no storage tier inside the service: "the Standard and
Infrequent Access log classes differ in ingestion costs only. Storage charges
and CloudWatch Logs Insights charges are the same in each log class"
([log classes](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CloudWatch_Logs_Log_Classes.html)).
Tiering means a retention policy plus delivery to Amazon S3, where S3 Lifecycle
archives or deletes the data. This matches Well-Architected
[SUS04-BP03](https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_data_a4.html).
It is a configuration audit: query frequency is not observed, so the detector
cannot prove the data is rarely read.

### Input

One `telemetry` source per `resource:<log-group-name>` scope item, as in
INF-01. The scope is per log group because retention, class and stored bytes
are log-group properties, and the fix is per log group. A retention that is
uniform across an account is not waste by itself: this project's 20 groups
all use 7 days.

`owner_d.obs07.normalize_describe_log_groups(pages, tags=None)` builds the
`data` objects from raw API responses:

- `pages` is a list of `DescribeLogGroups` responses, or the auto-paginated
  `aws logs describe-log-groups` output wrapped in a list.
- `tags` optionally maps a log group name or `logGroupArn` to a
  `ListTagsForResource` response or a plain tag mapping.
- It returns `{logGroupName: data}`.
- It raises `NormalizationError` (a `ValueError`) when a page has no
  `logGroups` list, a group has no name, or a name repeats.

The detector never calls AWS.

| Field | Meaning |
| --- | --- |
| `resource_id` / `resource_type` | `logGroupName` / `aws_cloudwatch_log_group` |
| `log_group_arn` | `logGroupArn` (or `arn` without `:*`); suggested `locator` |
| `retention_in_days` | `retentionInDays`; **`null` = never expire** (no retention policy) |
| `stored_bytes` | `storedBytes` (excludes events already marked for deletion) |
| `log_group_class` | `STANDARD`, `INFREQUENT_ACCESS` or `DELIVERY` |
| `tags` | Tag object, or `null` when tags were not collected |
| `data_protection_status`, `creation_time`, `metric_filter_count`, `deletion_protection_enabled` | Context only; not used by the rule |

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_hot_retention_days` | Longest retention kept in CloudWatch Logs without a finding | `365` |
| `min_stored_bytes` | Groups storing less are not flagged | `1073741824` (1 GiB) |
| `compliance_tag_keys` | Tag keys (case-insensitive) that mark mandated retention; may be empty | `["compliance", "data-retention", "legal-hold"]` |
| `exempt_log_group_prefixes` | Log group name prefixes retained by mandate; may be empty | `["aws-controltower/"]` |

`365` is the default minimum of Security Hub
[CloudWatch.16](https://docs.aws.amazon.com/securityhub/latest/userguide/cloudwatch-controls.html#cloudwatch-16),
so the reference value never flags a retention that this control requires.
1 GiB costs cents per month at the published
[archival price](https://aws.amazon.com/cloudwatch/pricing/). All four values are
judgment calls. Missing or invalid settings make the result `unavailable`.

### Detection rule

A finding is emitted when `retention_in_days` is `null` or strictly greater than
`max_hot_retention_days`, **and** `stored_bytes >= min_stored_bytes`.

Groups that are evaluated but not flagged:

- `DELIVERY`-class groups, whose short retention is fixed by AWS;
- groups whose name starts with an exempt prefix, or that have a compliance tag
  key (a limitation note names them);
- groups below `min_stored_bytes` (a note names hot-retention groups that are
  still small).

The Infrequent Access class is **not** an exception, because it lowers
ingestion cost only. Export and subscription to S3 are not exceptions either:
keeping the CloudWatch copy forever duplicates storage.

Each finding cites `retention_in_days`, `stored_bytes`, `log_group_class` and
`tags`. The identity is `hot-log-retention`, so growth or a retention change
keeps the fingerprint. Confidence:

- `medium` when tags were supplied and have no compliance key;
- `low` when tags were not collected.

Untagged mandates cannot be seen, so the recommendation asks the team to
confirm retention requirements first. Measurements stay absent: `storedBytes`
is a point-in-time stock, not a windowed `log_volume`.

### Limitations

- S3 lifecycle, Firehose delivery streams, subscription filters and export
  tasks are out of scope for v1: they do not change the verdict, and S3-side
  tiering is not audited.
- CloudWatch metrics and X-Ray retention are not covered.
- Groups with missing or malformed data, mismatched scope or several sources
  stay out of `evaluated_scope` with the reason.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs07/obs07-01-positive-input.json
```

`tests/fixtures/obs07/recorded-describe-log-groups.json` is a real response
from the project's selected Region (account ID replaced with `123456789012`).
The `synthetic-*.json` fixtures are synthetic.

## OBS-06 — High-cardinality metric labels (CloudWatch custom metric dimensions)

Flags CloudWatch custom metrics whose dimensions carry many distinct values or
unbounded identifiers (request/trace/session/user IDs, UUIDs, raw URLs).
CloudWatch "treats each unique combination of dimensions as a separate metric"
([concepts](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html#dimension-combinations))
and bills each one as a custom metric
([pricing](https://aws.amazon.com/cloudwatch/pricing/)). The
[EMF specification](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html)
warns that a high-cardinality dimension "such as `requestId`" creates "a custom
metric corresponding to each unique dimension combination". This is a
telemetry detector over a ListMetrics snapshot. It emits no cost or energy
measurements.

### Input

A contract v1 `input` payload with one `telemetry` source per scope item
`resource:metric/<namespace>/<metric_name>`. Scope is per metric because the
metric is CloudWatch's identity and billing unit, cardinality belongs to one
metric's dimension set, and one namespace can mix bounded and unbounded
metrics.

The connector passes the raw ListMetrics pages (boto3 paginator or CLI
responses, in request order) to
`owner_d.obs06.normalize_list_metrics(pages: list[dict]) -> dict`. It returns
`{"window_days", "listing_complete", "page_count", "metrics"}`, where `metrics`
maps each scope ID (`obs06.scope_id_for(namespace, metric_name)`) to the
`data` object for that metric:

| Field | Meaning |
| --- | --- |
| `namespace` / `metric_name` | Must match the scope ID |
| `series_count` | Distinct dimension combinations listed for the metric |
| `dimension_value_counts` | `{dimension key: distinct values}` |
| `dimension_value_samples` | `{dimension key: first 5 values, sorted}` (bounded output) |
| `window_days` | `14`: ListMetrics only lists metrics with datapoints in the past two weeks |
| `listing_complete` | `false` when the last page still had a `NextToken` |

Pages that are not ListMetrics responses raise `ValueError`.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_dimension_values` | Most distinct values one dimension key may have | `100` |
| `min_identifier_values` | Identifier-like keys are flagged above this many values | `10` |

The [Prometheus instrumentation guide](https://prometheus.io/docs/practices/instrumentation/#do-not-overuse-labels)
says to "keep the cardinality of your metrics below 10" and, for "a cardinality
over 100 or the potential to grow that large, investigate alternate solutions".
The [Prometheus naming guide](https://prometheus.io/docs/practices/naming/#labels)
says "Do not use labels to store dimensions with high cardinality ... such as
user IDs, email addresses, or other unbounded sets of values". OpenTelemetry
SDKs cap a metric at 2000 attribute sets by default
([cardinality limits](https://opentelemetry.io/docs/specs/otel/metrics/sdk/#cardinality-limits));
that is a safety cap, not a target. The thresholds are judgment calls, so
missing or invalid settings make the result `unavailable`.

### Detection rule

A dimension key is flagged when its distinct value count is strictly greater
than `max_dimension_values` (count rule), or when it looks like an unbounded
identifier and has strictly more than `min_identifier_values` values
(identifier rule). Identifier-like means either:

- the key name is a per-request/per-user identifier (`RequestId`, `trace_id`,
  `SpanId`, `CorrelationId`, `SessionId`, `UserId`, `CustomerId`, `OrderId`,
  `MessageId`, `Email`, `ClientIp`, `Url`, ...), or
- every sampled value is a UUID, ULID, X-Ray trace ID, hex token (16+), long
  number (8+ digits), email or IPv4 address, or a URL/path with such an ID
  segment.

Confidence:

- `high`: count rule and an identifier-like key;
- `medium`: count rule alone, or identifier-like sample values;
- `low`: identifier-like key name alone.

One finding per key. The identity is `dimension:<key>`, and the evidence cites
`series_count`, `dimension_value_counts`, `dimension_value_samples` and
`window_days`.

Exceptions and coverage:

- `AWS/*` namespaces are evaluated and never flagged. Their dimensions are
  defined by the service (e.g. `AWS/Usage` `Resource`, `AWS/SQS` `QueueName`),
  basic monitoring is not billed as custom metrics, and the team cannot change
  them. The result records how many were excepted. Agent-published namespaces
  such as `ContainerInsights` are billed as custom metrics and are evaluated.
- Bounded identifiers (a `TenantId` with 3 values) stay below
  `min_identifier_values`.
- If `listing_complete` is `false`, counts are lower bounds: flagged metrics
  are still evaluated, but metrics that would be clean are left out of
  `evaluated_scope`.

### Limitations

ListMetrics does not list metrics without datapoints in the past two weeks or
created in the last ~15 minutes, and its counts are not billed metric-hours.
A snapshot shows how many values exist, not that they keep growing. Only the
first five sorted values per key are sampled, so one non-ID value can hide an
ID-shaped key. `RecentlyActive=PT3H` listings, cross-account `OwningAccounts`
(series are merged by dimension set), log/trace attribute cardinality and
Prometheus/AMP series are out of scope.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs06/obs06-01-positive-input.json
```
## LLM-10 — Unbounded agent/tool loops and repeated tool calls (X-Ray traces)

Flags agents that call the same tool with identical arguments over and over,
or run more model turns than a configured budget, using AWS X-Ray traces the
client already records. This is the trace half of LLM-10: it proves what the
sampled traces show, not that the code lacks a budget. A static half (loops
around LLM/tool calls without a max-iteration bound) is a follow-up. No token
or cost measurements are emitted.

### Input

`llm10.normalize_xray_traces(traces)` takes the `Traces` list of
[`BatchGetTraces`](https://docs.aws.amazon.com/xray/latest/api/API_BatchGetTraces.html)
(`{"Id", "Segments": [{"Id", "Document"}]}`) and returns
`{"traces_received", "traces_without_genai_calls", "skipped_traces", "entrypoints"}`.
`llm10.telemetry_sources(normalized)` turns each entrypoint into one
`telemetry` source with scope `entrypoint:<name>`. The entrypoint is the
X-Ray segment that owns the calls, for example the Lambda function. The
trace-analyzer route fetches traces read-only and calls these functions; the
detector never calls AWS.

GenAI calls are recognised by
[OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).
Attributes are read from subsegment `metadata` (any namespace; the ADOT
`awsxrayexporter` writes `metadata.default["gen_ai.tool.name"]`) or
`annotations` (`gen_ai_tool_name`, because annotation keys cannot contain dots).

| Span | Recognised by |
| --- | --- |
| Model turn | `gen_ai.operation.name` `chat`, `text_completion`, `generate_content`, or a `gen_ai.request.model` without a tool name |
| Tool call | `execute_tool`, a `gen_ai.tool.name`, or a subsegment named `execute_tool <tool>` |
| Agent run | `invoke_agent` / `invoke_workflow` (`gen_ai.agent.name`); calls outside any agent span belong to the entrypoint run |
| Arguments | `gen_ai.tool.call.arguments` (JSON string or object), compared by a SHA-256 of canonical JSON |

Embedded and separately sent subsegments (`type: subsegment` + `parent_id`)
are both supported; inferred segments are ignored. A GenAI span nested
directly in a span of the same kind (and tool name) is one call, for example
a framework `chat` span around the instrumented Bedrock client subsegment.
Traces with an unparseable document, an in-progress (sub)segment or an
orphaned subsegment are skipped whole and listed with the reason.

Each entrypoint summary has `traces_with_genai_calls`, `traces_analyzed`
(traces with a model turn or a tool call with arguments), `traces_skipped`,
`skip_reasons`, `failed_calls`, `tool_calls_without_arguments`, the worst
`max_llm_iterations` and `max_identical_tool_calls` records and per-trace
`traces` (calls per run, agent nesting depth, duration, pagination series).

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_traces` | Analyzable traces needed before an entrypoint is evaluated | `10` |
| `max_llm_iterations` | Most successful model calls allowed in one agent run | `10` |
| `max_identical_tool_calls` | Most calls of one tool with identical arguments in one agent run | `3` |

A turn is "one LLM call for the current agent"; the
[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/)
stops at `max_turns` (default 10) and LangChain's `AgentExecutor` at
`max_iterations` (default 15). The OpenHands stuck detector treats 4 repeated
action/observation pairs as a loop. AWS
[AGENTSUS02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html)
notes that "every duplicate model call, tool invocation, and memory lookup is
work the agent fleet has already done once". Missing or invalid settings make
the result `unavailable`.

### Detection rule

For each entrypoint with at least `min_traces` analyzable traces:

- `identical-tool-calls`: some agent run calls one tool with identical
  arguments more than `max_identical_tool_calls` times. Confidence is `high`
  when two or more traces breach, otherwise `medium`.
- `iteration-budget`: some agent run makes more than `max_llm_iterations`
  successful model calls. Confidence is `medium` when two or more traces
  breach, otherwise `low`.

Evidence cites `traces_analyzed` and the worst record. Not counted:

- failed calls (`error`, `throttle` or `fault` true, or `error.type`), treated as explicit retries;
- pagination: arguments that differ only in a cursor/page/offset key are distinct calls, reported as a pagination series;
- separate agent runs in one trace (orchestrator and sub-agents), which are counted per run;
- tool calls without recorded arguments, since `gen_ai.tool.call.arguments` is Opt-In. The limitations say so.

A polling tool called repeatedly with the same job ID is flagged; the trace
cannot show that the results changed. Entrypoints with too few analyzable
traces, or with malformed summaries, are left out of `evaluated_scope`.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm10/llm10-01-positive-input.json
```

The fixtures under `tests/fixtures/llm10/` are synthetic `BatchGetTraces`
responses shaped on the X-Ray segment document format, not production traces.


## OBS-04 — Unstructured logs requiring query-time parsing

Flags Python modules that write log lines with runtime values embedded in free
text while no structured logging is visible in the scanned code. CloudWatch
Logs Insights discovers fields in JSON log events (and can index them), but
free-text values need `parse` over every scanned event. Static only (same
runner and logger recognition as OBS-02). Sampling real CloudWatch log events is
not part of v1; it is a follow-up for the runtime log analyzer.

### Input

Same as OBS-02: one `static` source per `file:<path>` scope item, `.py` only,
no context settings. Structured-logging markers are collected from every
Python source in the payload, because a JSON formatter configured in one module
applies to the whole process.

### Detection rule

Two kinds of free-text lines are counted per module:

- **Logging calls** (any level, recognised as in OBS-02) whose message
  interpolates a runtime value: an f-string, `%`-formatting, `"...".format()`,
  concatenation, or a constant message with `%`-style arguments
  (`logger.info("order %s shipped", oid)`). Unlike OBS-02, the lazy form
  counts too, because the emitted line is still free text.
- **`print()` in Lambda handler modules**: a module-level function taking
  `(event, context)`, or `lambda_handler`/`handler` with a `context` second
  parameter. AWS documents that `print` output reaches CloudWatch Logs as plain
  text even with the JSON log format.

A value passed through `json.dumps(...)` does not count, because Logs Insights
discovers the first JSON fragment in a Lambda log event. Constant messages,
messages passed as a variable and constant arguments do not count either.

One finding per module and kind: identity `module:logging` or `module:print`.
Evidence is the first counted call. The summary gives the count and the
interpolation styles. Per-call findings would flood a report and duplicate
OBS-02.

Logging calls are not flagged in any file when the payload shows structured
logging. A limitation names the file and the marker. The markers are:

- imports of `structlog`, `pythonjsonlogger`, `json_log_formatter`,
  `ecs_logging`, `logstash_formatter`, `logfmter`, the Powertools `Logger` or
  the OpenTelemetry logs SDK;
- a `logging.Formatter` subclass that calls `json.dumps`;
- a JSON-shaped format string, or a string naming a JSON formatter (dictConfig);
- loguru `serialize=True`;
- the Lambda JSON log format in CDK/SDK code (`logging_format=...JSON`,
  `LogFormat: "JSON"`);
- a log call with non-empty `extra=` or structlog-style keyword fields.

Lambda `print` findings are kept even when a marker is present.

Confidence:

- `medium` for `print` findings;
- `medium` for logging findings in a Lambda handler module (Python Lambda's
  default log format is plain text) or when a plain-text format with
  `%(message)s` is configured outside `if __name__ == "__main__":`;
- `low` otherwise.

Not judged (no findings, still evaluated):

- vendored code (`site-packages`, `vendor`, `third_party`, `.aws-sam`,
  `cdk.out`, a Lambda layer's `python/` folder, ...) and samples
  (`examples`, `docs`); these also contribute no markers;
- tests (`tests/`, `test_*.py`, `*_test.py`, `conftest.py`);
- terminal scripts and CLIs (`scripts/`, `bin/`, `setup.py`, `manage.py`, a
  module importing `argparse`/`click`/`typer`/`fire`/`docopt`), unless the
  module is a Lambda handler;
- calls inside `if __name__ == "__main__":`;
- lines with `# noqa` or `# noqa: OBS-04`.

Missing, non-Python or unparseable files are left out of `evaluated_scope` and
contribute no markers. Logging configuration outside Python (`logging.conf`,
YAML dictConfig, SAM/Terraform `LogFormat: JSON`) is not visible, so logging
findings outside Lambda modules stay `low`. JSON records whose `message` still
embeds values (e.g. Powertools with f-strings) are not flagged in v1. No
measurements are emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs04/obs04-01-positive-input.json
```


## OBS-09 — Uncompressed/unbatched telemetry export

Flags OTLP telemetry exports that go out as many small or uncompressed
requests. The check is static and uses the `textstatic.py` runner. Configs are
read as text and are never run, rendered or resolved.

Collector configs are parsed by `owner_d/otelconfig.py`, a small reader that
the later OpenTelemetry config checks (OBS-05/12/13/14) can reuse. It returns
the receivers, processors, exporters, connectors and extensions by component
ID, and `service.pipelines` with line numbers. `${env:...}`/`${...}` references
are left unresolved and treated as unknown.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- **OpenTelemetry Collector** `.yaml`/`.yml`, which also covers AWS Distro for
  OpenTelemetry. The config can be:
  - a plain config with `service.pipelines`
  - an `OpenTelemetryCollector` resource, with `spec.config` as a mapping or a
    `|` string
  - a `ConfigMap` whose `data` entries are `|` strings
- **Python** `.py` files that mention `opentelemetry`

No context settings are required.

### Detection rule

Only the OTLP exporters are checked: `otlp`/`otlp_grpc` and
`otlphttp`/`otlp_http`, including named instances such as `otlp/backend`.

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `pipeline/<id>:unbatched` | A traces, metrics or logs pipeline exports to an OTLP exporter defined in the file. The pipeline has no `batch` processor, or only ones with `timeout: 0` ("data will be sent immediately"). The exporter has no exporter-side batching (`sending_queue.batch`, or the legacy `batcher` without `enabled: false`). | medium; low if the endpoint is unresolved |
| `exporter/<id>:compression-none` | An OTLP exporter used by a pipeline sets `compression: none` (or `""`) | medium; low if the endpoint is unresolved |
| `<qualname>:SimpleSpanProcessor(<Exporter>)` | Python `SimpleSpanProcessor`/`SimpleLogRecordProcessor`, resolved through imports to `opentelemetry.*`, wraps a network exporter: an `opentelemetry.exporter.*` or `azure.monitor.opentelemetry.exporter.*` class, passed directly or through a name assigned only that way | medium |

Identities from embedded configs are prefixed with the resource:
`OpenTelemetryCollector/<name>:` or `ConfigMap/<name>:<key>:`. A repeated
identity gets `#n`.

Defaults, from the official docs:

- The OTLP exporters enable `gzip` by default, so a missing `compression` is
  not a finding.
- `sending_queue.batch` is off by default. The alpha feature gate
  `pkg.exporterhelper.queueBatchEnabled` (v0.158.0) turns it on, but OBS-09
  cannot see feature gates.
- On the SDK side, the OTLP spec leaves the default compression to each
  language ("Default: No value"). An unset `OTEL_EXPORTER_OTLP_COMPRESSION` is
  therefore not a finding: SDKs usually send to a local agent or collector.

Not flagged:

- non-OTLP exporters, such as `debug`, `logging`, `file`, connectors and
  vendor exporters
- exporters whose endpoints are all loopback (`localhost`, `127.*`, `::1`,
  `0.0.0.0`, `unix:`), because that hop costs CPU, not network or ingest
- pipelines fed by a connector, because batching upstream is not visible
- `profiles` pipelines, because the batch processor does not support them
- exporters that are referenced but not defined in the file
- unresolved pipeline lists or queue settings
- `ConsoleSpanExporter`, in-memory exporters, and exporters passed in as
  parameters
- hits with `# noqa` / `# noqa: OBS-09` on the hit line or directly above the
  pipeline key, exporter key or call

The following are not evaluated. They are listed as limitations, never
reported clean:

- Helm/Go templates and YAML outside the `miniyaml` subset, including broken
  embedded configs
- invalid Python
- YAML with `exporters:`/`pipelines:` but no collector config, for example
  Helm chart values, which are merged with chart defaults
- development/test files: path tokens `dev`, `development`, `debug`, `local`,
  `test(s)`, `testing`, `testdata`, `e2e`, `ci`, `devcontainer`, and Python
  test modules

Other files are out of scope (`Unsupported`), so the scan worker passes only
OpenTelemetry inputs to the check.

### Limitations

The check reads one file at a time. It cannot see:

- `--config` merges
- feature gates
- the collector version
- upstream batching behind connectors
- the actual payload sizes

No measurements are emitted. CloudWatch agent JSON is not covered: the agent
always batches (`force_flush_interval`), so there is no unbatched switch to
detect. OpenTelemetry SDK declarative-configuration YAML is not read yet.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs09/obs09-01-positive-input.json
```

## INF-02 — Static replicas without demand-based autoscaling

Flags workloads in deployment manifests that run a fixed replica count at or
above a configured threshold when no autoscaler in the supplied files targets
them. The check is static: manifests are read as text and are never applied,
rendered or sent to a cluster or to AWS. YAML is read with
`owner_d/miniyaml.py` and the contract runner is `textstatic.py`, both as in
INF-08. v1 is a proxy. It proves "fixed replicas, and no autoscaler in these
files". It does not prove that demand varies or that replicas sit idle, so it
emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item and the context setting below. Supported files:

- **Kubernetes** `.yaml`/`.yml`: `Deployment`, `StatefulSet`, `ReplicaSet`,
  `ReplicationController` and Argo `Rollout`, including inside `kind: List`.
  `HorizontalPodAutoscaler` and KEDA `ScaledObject` count as autoscalers.
- **CloudFormation** `.json`/`.yaml`/`.yml`: `AWS::ECS::Service`. An
  `AWS::ApplicationAutoScaling::ScalableTarget` on `ecs:service:DesiredCount`
  counts as its autoscaler.
- **Docker Compose** `.yaml`/`.yml`: `deploy.replicas`, or the legacy `scale`.

### Context settings (required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_static_replicas` | Smallest fixed replica count that is flagged (inclusive); an integer of at least 2 | `2` |

The threshold is a judgment call, so it is required, as in LLM-15/TST-07. A
missing or invalid setting gives `unavailable`. With a single replica there is
no idle replica for HPA or ECS Service Auto Scaling to remove, so 1 is not
allowed. The repository scanner does not supply this setting yet, so repo
scans report INF-02 as `unavailable`, as they do for LLM-15/TST-07.

### Detection rule

| Format | Identity | Flagged when | Confidence |
| --- | --- | --- | --- |
| Kubernetes | `<Kind>/[<ns>/]<name>` | Literal `spec.replicas` >= threshold and no HPA/ScaledObject whose `scaleTargetRef` kind and name match, in a matching namespace | medium; low for `StatefulSet` |
| CloudFormation | `AWS::ECS::Service/<LogicalId>` | Literal `DesiredCount` >= threshold and no ECS scalable target refers to the service | medium |
| Compose | `service/<name>` | Literal `deploy.replicas`/`scale` >= threshold. Compose has no demand-based autoscaler. | low |

**Cross-file matching.** The runner evaluates one file per scope item. INF-02
first indexes autoscalers in **every supplied source**, so an HPA in
`hpa.yaml` covers a Deployment in `deployment.yaml`.

- **Kubernetes namespaces.** An unset namespace on either side matches any
  namespace, because it is chosen at apply time (`kubectl -n`, Kustomize).
  Namespaces that are both set and differ do not match.
- **ECS scalable targets.** A scalable target matches a service in the same
  template when its `ResourceId` names the service:
  - the logical ID through `Ref`, `Fn::GetAtt` (`Service.Name`), `Fn::Join`,
    or `Fn::Sub` with `${Service}`/`${Service.Name}`
  - a literal `ServiceName`

  In another template, it matches only through a literal
  `service/<cluster>/<name>` ResourceId and the service's literal `ServiceName`.

**When findings drop to `low`.**

- A supplied `.yaml`/`.yml`/`.json` file could not be read (a Helm template, a
  YAML construct outside the parser's subset, Kubernetes JSON) and its text
  mentions `HorizontalPodAutoscaler`/`ScaledObject`. This affects Kubernetes
  findings, because an autoscaler there would not be seen.
- An unreadable file mentions `ScalableTarget`/`ecs:service:DesiredCount`, or an
  ECS scalable target could not be tied to any service (e.g. `!ImportValue`).
  This affects ECS findings.

The summary states the reason.

The following are not flagged:

- `replicas`/`DesiredCount` omitted (the default is 1), `null`, or below the
  threshold
- ECS services with `SchedulingStrategy: DAEMON`
- `ReplicaSet`s with `ownerReferences` (managed by a Deployment)
- Compose services with `deploy.mode: global`/`*-job` or `extends`
- `# noqa` / `# noqa: INF-02` on the count line or the comment lines directly
  above it

Evidence is the exact `replicas:` / `DesiredCount:` / `scale:` line.

The following are not evaluated. They are listed as limitations, never
reported clean:

- files where a workload that no autoscaler covers has a count that is not a
  literal integer (`replicas: ${REPLICAS}`, `DesiredCount: !Ref DesiredCount`,
  `{"Ref": ...}`), because the count is set at deploy time
- Helm/Go templates and YAML outside the subset
- invalid JSON and Kubernetes JSON manifests
- YAML/JSON with no Kubernetes objects, Compose services or CloudFormation
  resources (including ECS task definition JSON, which has no service count)
- Compose files marked as development/test, as in INF-08

### Limitations

Static only and no telemetry, so no measurements. The taxonomy's "regardless of
demand" needs replica utilisation or request-rate history, which v1 does not
read.

Not visible:

- autoscalers outside the supplied files: other repositories, `kubectl
  autoscale`, scalable targets registered in the console or CLI
- Kustomize `replicas:` overrides and patches
- Helm values

An HPA pinned at `minReplicas == maxReplicas` still counts as an autoscaler.

Not covered: Copilot manifests, EC2 Auto Scaling groups and ECS services that
are not defined in CloudFormation.

StatefulSets are flagged at `low` because many of them are sized for quorum
(etcd, Kafka, Cassandra). A deliberate fixed count can be kept with
`# noqa: INF-02`.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/inf02/inf02-01-positive-input.json
```

## INF-10 — Storage without lifecycle/retention management

Flags storage resources in CloudFormation/SAM templates that declare no
lifecycle or retention, so their data is kept indefinitely. This v1 is a
static IaC proxy. It proves "no retention/lifecycle declared in this
template", not that data grows, is old or is unused. Templates are read as
text and are never deployed, resolved or sent to AWS. It uses the
`textstatic.py` runner and `owner_d/miniyaml.py`. YAML tags such as `!Ref`,
`!Sub` and `!If` are read, and JSON keeps its line numbers.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- **CloudFormation/SAM YAML**: `.yaml`/`.yml`
- **CloudFormation JSON**: `.json`, including CDK-synthesized
  `cdk.out/*.template.json`
- **`.template`** files in either syntax

A file counts as a template when it has a `Resources` mapping. No context
settings are required.

### Detection rule

| Resource | Identity | Flagged when | Confidence |
| --- | --- | --- | --- |
| `AWS::S3::Bucket` | `<LogicalId>:s3-lifecycle` | No enabled lifecycle rule expires or transitions objects (see the cases below) | low |
| `AWS::S3::Bucket` (versioning `Enabled`) | `<LogicalId>:s3-noncurrent-versions` | Enabled rules expire/transition current objects, but none expires or transitions noncurrent versions | low |
| `AWS::Logs::LogGroup` | `<LogicalId>:log-retention` | No `RetentionInDays` (the CloudWatch Logs default is never expire) | medium |
| `AWS::ECR::Repository` | `<LogicalId>:ecr-lifecycle` | No `LifecyclePolicy`, or one without `LifecyclePolicyText` | medium |

The `s3-lifecycle` rule fires when any of these holds:

- there is no `LifecycleConfiguration`
- every rule is `Status: Disabled`
- the enabled rules only use `AbortIncompleteMultipartUpload` or
  `ExpiredObjectDeleteMarker`
- the enabled rules only act on noncurrent versions and the bucket is not
  versioned

Rules that only abort incomplete uploads or remove delete markers clean up
upload parts and delete markers, but never object data, so they do not count.
A rule with a prefix, tag or size filter counts, because the template does not
show which prefixes hold data. `VersioningConfiguration.Status: Suspended`
still holds earlier versions, so noncurrent rules count on it.

S3 findings are `low` because many buckets keep durable data on purpose:
website assets, release artifacts and compliance archives. Log groups and ECR
repositories are `medium`. Logs rarely need to be kept forever, and ECR keeps
every pushed image, including untagged ones, until a lifecycle policy expires
it. The check does not judge how long a declared retention is; that is OBS-07.

These count as declared and are not flagged:

- values set by intrinsic functions, e.g. `RetentionInDays: !Ref Days` or
  `Status: !Ref RuleStatus`
- `Fn::If` around `Properties`, `LifecycleConfiguration`, `Rules` or a single
  rule. This is conservative: a branch that resolves to `AWS::NoValue` is
  not flagged.
- `LogGroupClass: DELIVERY` log groups, which have a fixed 1-day retention

Legitimate exceptions:

- buckets with S3 Object Lock (`ObjectLockEnabled: true` or
  `ObjectLockConfiguration.ObjectLockEnabled: Enabled`), which signals
  compliance retention
- `# noqa` / `# noqa: INF-10` on the cited line or in the comment lines
  directly above the resource (YAML only; JSON has no comments)

`DeletionPolicy`/`UpdateReplacePolicy` are ignored because they control stack
deletion, not object retention. A resource-level `Condition` does not change
the outcome.

Evidence is the logical-ID line through the `Type` line. Only the logical-ID
line is cited when `Type` is 8 or more lines below it. For
`s3-noncurrent-versions`, evidence is the `VersioningConfiguration` line
through its `Status` line. CDK logical IDs include a hash of the construct
path, so moving a construct changes the identity.

The following are not evaluated. They are listed as limitations, never
reported clean:

- invalid JSON, or YAML outside the miniyaml subset (this includes
  flow-mapping keys with `::` such as `{Fn::Join: ...}`)
- YAML/JSON files without CloudFormation `Resources`
- templates with a macro `Transform` other than
  `AWS::Serverless-2016-10-31`/`AWS::LanguageExtensions`, or with
  `Fn::Transform`/`AWS::Include` or `Fn::ForEach`, because these can rewrite
  resources
- Terraform/HCL (`.tf`). There is no sound HCL parser without a new
  dependency.
- CDK source code (synthesize it first), Pulumi and Serverless Framework files

### Limitations

No inventory or telemetry is read. The taxonomy's AWS Config / Resource
Explorer half needs a client read-only role and is blocked on OQ-7. No
measurements are emitted.

The check cannot see:

- lifecycle applied outside the template: console/CLI, `put-lifecycle-policy`,
  other stacks or Config remediation
- implicit Lambda/SAM function log groups and `Custom::LogRetention`
- DynamoDB TTL, Kinesis retention, EBS/RDS snapshots and S3 directory buckets

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/inf10/inf10-01-positive-input.json
```

## OBS-05 — Tracing without sampling (static proxy: 100% trace sampling)

Flags tracing that is configured to keep every trace. The check is static,
like OBS-01:

- YAML and JSON are read with `owner_d/miniyaml.py`, which keeps line numbers.
- `.env`, `.properties`, INI/CFG, TOML and Dockerfile `ENV` use the OBS-01
  line scanners.
- Python is parsed with `ast`.

Nothing is imported, executed or rendered, and no measurements are emitted.

### Detection rule

| Signal | Flagged when | Identity |
| --- | --- | --- |
| `OTEL_TRACES_SAMPLER` (also `otel.traces.sampler`, `quarkus.otel.traces.sampler`, `QUARKUS_OTEL_TRACES_SAMPLER`), from env files, Compose `environment` (map or `KEY=value` list), Kubernetes/ECS `name`/`value` env, CloudFormation Lambda `Variables` or Dockerfile `ENV` | `always_on` / `parentbased_always_on`; `traceidratio` / `parentbased_traceidratio` with `OTEL_TRACES_SAMPLER_ARG` exactly 1 (`1`, `"1"`, `1.0`) in the same block; the same ratio samplers with no ARG in the block (the specified default ratio is 1.0) | `trace-sampling:<key path>` |
| `OTEL_TRACES_SAMPLER_ARG` with no sampler in the same block | The ARG is ignored and the SDK default `parentbased_always_on` applies. Always `low`, because the sampler may be set elsewhere | `ignored-sampler-arg:<key path>` |
| Spring `management.tracing.sampling.probability` / `spring.sleuth.sampler.probability` (any relaxed-binding form) | equal to 1 | `trace-sampling:<key path>` |
| Python `TracerProvider(sampler=...)` (keyword or first positional, inline or through a variable assigned once in the same scope) | `ALWAYS_ON`, `DEFAULT_ON`, `ParentBased(<one of these>)`, `TraceIdRatioBased(1)` / `(1.0)`, `ParentBasedTraceIdRatio(1.0)`, `StaticSampler(Decision.RECORD_AND_SAMPLE)` from `opentelemetry` | `<function>:TracerProvider.sampler` |
| Python `os.environ["OTEL_TRACES_SAMPLER"] = ...` / `os.environ.setdefault(...)` | `always_on` / `parentbased_always_on` | `<function>:os.environ:OTEL_TRACES_SAMPLER` |
| AWS X-Ray rules: `AWS::XRay::SamplingRule` / API `SamplingRule` (`FixedRate`), and SDK local rules files (`default` and `rules` with `rate`) | rate equal to 1 | `xray-sampling-rule:<RuleName, logical ID, default or service:host:method:path>` |
| Python `aws_xray_sdk` `xray_recorder.configure(sampling=False)` | always (every request is traced) | `<function>:xray_recorder.configure.sampling` |

**Confidence.** The environment comes from path, key and Docker stage names,
as in OBS-01.

| Environment | Config | Python |
| --- | --- | --- |
| Production marker | `high` | `medium` |
| Production template, or `context.environment: "production"` | `medium` | `low` |
| No marker | `low` | `low` |

Some cases drop one tier:

- Parent-based samplers drop one tier, because they keep 100% only of root
  spans and child spans follow the caller.
- A ratio sampler without an ARG drops one tier, and two tiers if it is also
  parent-based.
- X-Ray rules narrowed to a service, host, method, path or attributes drop
  one tier. They may be the deliberate "full tracing for debugging"
  exception.
- A Python setting under an `if` that has no alternative (a guard such as
  `if endpoint:`) drops one tier. It only applies when tracing is switched on.

**Not flagged:**

- dev/test/staging/local/CI/docs/example paths, keys or stages
- `# noqa: OBS-05` on the first evidence line
- a block that also sets `OTEL_SDK_DISABLED=true` or
  `OTEL_TRACES_EXPORTER=none`
- ratios below 1, such as `0.99`
- ratios above 1, which the SDKs reject
- `${VAR}` without a default, or tagged values such as `!Ref`
- Python samplers chosen at runtime: a ternary, an `if`/`else` whose other
  branch configures another sampler, a parameter, or a variable assigned more
  than once or inside an `if`
- `TraceIdRatioBased("1")` or `TraceIdRatioBased(True)`
- remote or other samplers: `xray`, `jaeger_remote`, `always_off`
- Lambda `TracingConfig: Active`, which uses the default X-Ray rule

### Limitations

"No sampler configured" is not flagged, although the OpenTelemetry SDK
default `parentbased_always_on` keeps every root trace. The sampler may come
from another env source, from code, from remote configuration, or from a
Collector that tail-samples. The one exception is the ignored-ARG case above,
which is always `low`.

The taxonomy's "head-only sampling" half (no tail sampling for rare
failures) is not judged.

**OpenTelemetry Collector `probabilistic_sampler` config is not evaluated in
v1.** It will be added on top of the shared `otelconfig.py` after the OBS-09
PR (#233) lands.

The following are not evaluated:

- Terraform `aws_xray_sampling_rule`, CDK code and non-Python SDK code
- X-Ray rules passed inline in code (`LocalSampler({...})`)
- `env_file` / `envFrom` sources
- actual trace volume (X-Ray/CloudWatch)

Unparseable files are listed as limitations and never reported clean. That
covers invalid JSON/TOML/INI, Helm templates, and TOML inline tables holding a
sampler key.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs05/obs05-01-positive-input.json
```
## LLM-13 — LLM responses cached without TTL or invalidation (static proxy)

Flags caches in Python source that store LLM responses (or values computed
from them) with no expiry and no invalidation in the file. AWS
[AGENTSUS02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html)
lists "caching tool results and model responses without invalidation or TTL
policies, producing stale answers that appear fresh" as an anti-pattern. This
v1 is a static proxy: it proves that the cache has no TTL, not that the data
goes stale or how often the cache is hit, and it emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

LLM calls are recognised by `owner_d/llmcalls.py`: Anthropic `messages.*`,
OpenAI chat completions / responses, Bedrock `converse`/`invoke_model`.
Embedding calls do not count. That covers OpenAI `embeddings.create` and
Bedrock `invoke_model` with an `embed` model ID, because an embedding of the
same text does not go stale.

| Cache | Flagged when |
| --- | --- |
| Memoized function: `functools.lru_cache`/`cache`, `cachetools.func.lru_cache`/`lfu_cache`/`fifo_cache`/`rr_cache`/`mru_cache`, `cachetools.cached` with `Cache`/`LRUCache`/`LFUCache`/`FIFOCache`/`RRCache`/`MRUCache` or `{}`, `async_lru.alru_cache` | The function calls an LLM, directly or through a function in the same file. Any `maxsize` counts, because size eviction is not expiry; `maxsize=0` caches nothing. `alru_cache` is flagged without `ttl` or with `ttl=None`. |
| LangChain LLM cache passed to `set_llm_cache(...)`, assigned to `langchain.llm_cache`, or passed as `cache=` | `InMemoryCache`, `SQLiteCache`, `SQLAlchemyCache`, `SQLAlchemyMd5Cache` and community `RedisSemanticCache` (no TTL support). `RedisCache`/`AsyncRedisCache`/`UpstashRedisCache` and `langchain_redis` `RedisCache`/`RedisSemanticCache` without `ttl`. `CassandraCache`/`CassandraSemanticCache` without `ttl_seconds`. |
| Redis/Valkey `set`/`setnx`/`mset`/`msetnx`/`hset`/`hmset` on a client created in the file (`Redis`, `StrictRedis`, `RedisCluster`, `from_url`, `.pipeline()`) | The function also reads from Redis (a cache reads before it writes), and the stored value comes from an LLM call in that function. The write sets no `ex`/`px`/`exat`/`pxat`/`keepttl` or positional expiry, and the function calls no `expire`/`pexpire`/`expireat`/`pexpireat`/`hexpire*`. |
| Module-level `{}`/`dict()`/`OrderedDict()` | A function reads it (`in`, `.get`, `[key]`) and stores an LLM-derived value in it. Nothing in the file evicts or rebinds it, and every use of the name is a read or a write. |

Not flagged:
- `<fn>.cache_clear()` or `<cache>.clear()` anywhere in the file;
- memoized functions with a time-bucket parameter (`ttl_hash`, `version`, `timestamp`, ...);
- dict/Redis caches in a function that reads a clock (`time.time()`, `datetime.now()`), which suggests a manual expiry timestamp;
- TTL caches (`TTLCache`, `TLRUCache`, `ttl_cache`, `setex`, `ex=`, LangChain `ttl=`);
- backends that are not listed (e.g. `MomentoCache`, whose client has a default TTL);
- memoized client/model/prompt factories;
- other SDKs (`Groq()`, ...);
- provider prompt caching (`cache_control`/`cachePoint`, covered by LLM-01);
- Redis clients or cache objects that are parameters or imported;
- `**kwargs`;
- `self._cache` instance caches and `cached_property`;
- code inside `test*` functions or `Test*` classes.

LiteLLM, GPTCache and diskcache are not evaluated. `# noqa` or `# noqa: LLM-13` on
the evidence line (the decorator, `set_llm_cache`, Redis write or dict
assignment) suppresses a finding.

Each finding has one of these identities:
- `<qualified function>:memoize:<decorator>`
- `<qualified scope>:langchain:<Backend>`
- `<qualified function>:redis.<method>`
- `<qualified function>:dict:<NAME>`

A repeat gets `#2`. Confidence is `medium`, and `low` when the only LLM calls are
`chat.completions` chains on a client not created in the file. Missing,
non-Python or unparseable files are left out of `evaluated_scope` and never
reported clean.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm13/llm13-01-positive-input.json
```

## OBS-13 — Noisy health-check/probe telemetry

Flags an OpenTelemetry Collector traces pipeline that keeps the spans of
Kubernetes liveness/readiness probes. The kubelet probes every replica every
`periodSeconds` (default 10s) for as long as the pod runs. When the workload is
OpenTelemetry-instrumented, each probe becomes a server span. Vendor guidance
(SRC-19) drops these spans with the collector `filter` processor; the
opentelemetry-demo does the same for gRPC health checks in its Go services
(`otelgrpc.WithFilter(filters.Not(filters.HealthCheck()))`).

The check is static. Files are read as text with `owner_d/miniyaml.py` and the
shared `owner_d/otelconfig.py`, and are never run, rendered or resolved. It
does not count spans, so no measurements are emitted. The summary estimates the
declared probe rate instead.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. The payload is judged **as a whole**: probes, filters and SDK exclusions
are collected from every source first. Supported files:

- **OpenTelemetry Collector** `.yaml`/`.yml` (also ADOT): a plain config, an
  `OpenTelemetryCollector` resource or a `ConfigMap` `|` entry, as in OBS-09.
- **Kubernetes workloads** `.yaml`/`.yml` with an `httpGet` probe and an
  OpenTelemetry marker: Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
  ReplicationController, Job, CronJob, DeploymentConfig, Rollout, also inside
  `kind: List`.
- **Code with an SDK exclusion hook** (`.py`, `.js`/`.ts`/`.mjs`/`.cjs`,
  `.go`, `.cs`, `.java`, `.kt`) containing `excluded_urls`,
  `ignoreIncomingRequestHook`, `ignoreIncomingPaths`, `WithFilter(`,
  `AddAspNetCoreInstrumentation` or `RuleBasedRoutingSampler`. These files
  only supply exceptions and never produce findings.

No context settings are required.

### Detection rule

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `pipeline/<id>:probe-spans` | A `traces` pipeline receives from `otlp`, `zipkin` or `jaeger` and exports to an exporter other than `debug`/`logging`/`nop`/`file`/a connector, and the payload has an instrumented probed workload whose probe path nothing drops | medium; low if the pipeline has a `probabilistic_sampler` (probe spans are still kept in proportion) |

Embedded configs get the `OpenTelemetryCollector/<name>:` or
`ConfigMap/<name>:<key>:` prefix, and a repeated identity gets `#n`. Evidence
is the pipeline block. The summary lists the probe paths, workloads, files and
periods, and an estimate of spans per day: `spec.replicas` × 86400 /
`periodSeconds`, with DaemonSets counted once.

An **instrumented probed workload** is a container with an HTTP
`livenessProbe` or `readinessProbe` (`httpGet.path`, query stripped) that:

- carries the operator annotation `instrumentation.opentelemetry.io/inject-<lang>`
  (not `"false"`). It applies to the containers in `container-names` /
  `<lang>-container-names`, else to the first container; or
- sets `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or
  `OTEL_TRACES_EXPORTER` (not `none`), an OpenTelemetry `-javaagent` in
  `JAVA_TOOL_OPTIONS`/`JAVA_OPTS`, or runs `opentelemetry-instrument`;
- and does not set `OTEL_SDK_DISABLED=true`, `OTEL_TRACES_EXPORTER=none` or
  `OTEL_TRACES_SAMPLER=always_off`.

A probe path is **dropped** when a `filter` processor of any traces pipeline
in the payload matches it. All filter formats are read: `trace_conditions`,
the deprecated `traces.span` and the legacy `spans.exclude`. A filter matches
when one of its values or quoted OTTL literals contains the path, matches it as
a regex (`IsMatch(span.name, "GET /health.*")`, `health`), or mentions
`kube-probe` (the probe `User-Agent`, which covers every path). `/` is matched
only exactly or by a full regex match. A filter in another collector config
counts, so an agent in front of a filtering gateway is not flagged.

Not flagged:

- payloads where a traces pipeline uses `tail_sampling` (policies such as
  `status_code`/`latency` can drop healthy probe traces, and v1 does not judge
  policies), a `filter` that is not defined in its file or has unresolved
  `${...}` values, or an unresolved processors list. A limitation names it.
- probes excluded in the SDK: container env `OTEL_PYTHON_EXCLUDED_URLS` /
  `OTEL_PYTHON_<FRAMEWORK>_EXCLUDED_URLS` whose comma-separated regexes
  `re.search`-match the path (the Python contrib semantics), the same env set
  with `valueFrom`, or a matching string literal on a code-hook line or the 5
  lines after it
- `startupProbe` (it stops after the first success), `tcpSocket`/`exec`/`grpc`
  probes, probes with no or a templated `path`
- uninstrumented containers and collector images (`opentelemetry-collector`,
  `otelcol`, `aws-otel-collector`)
- `logs`/`metrics`/`profiles` pipelines, pipelines that only export to
  `debug`-like exporters or connectors, and unresolved pipeline lists
- `# noqa` / `# noqa: OBS-13` on the pipeline key or directly above it. On a
  probe's `path:` line or directly above the probe key, it ignores that probe.

The recommended filter drops only **successful** probe spans, for example
`span.attributes["url.path"] == "/healthz" and span.attributes["http.response.status_code"] < 400`.
Failing probes explain restarts and pods taken out of service, so they should
be kept.

The following are not evaluated. They are listed as limitations, never
reported clean:

- every probe and code-hook file when the payload has no collector config
- Helm/Go templates and YAML outside the `miniyaml` subset, Helm values
- YAML that has neither a collector config nor an instrumented probed workload
- development/test files (the OBS-09 path tokens, and test modules)

Other files are out of scope (`Unsupported`), so the scan worker passes only
these inputs to the check.

### Limitations

Not visible: collectors, filters and workloads in other repositories,
namespace-level operator annotations, `Instrumentation` sampler settings, SDK
exclusions other than the ones listed, `--config` merges and HPA replica
counts. Code-hook literals are matched by text, so an unrelated literal next to
a hook can hide a finding (precision over recall).

Out of scope for v1: Kubernetes events (`k8sobjects`/`k8s_events`), probe
access logs in `filelog` pipelines, static-asset 404s and load-balancer health
checks. Telling noise from an audit trail there needs runtime volume data.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs13/obs13-01-positive-input.json
```

## OBS-12 — Unused default integrations enabled (zero-code auto-instrumentation)

Flags OpenTelemetry zero-code auto-instrumentation that is started with its
default "instrument everything" selection while the same deployment unit sets
no instrumentation selection. The check is static and uses the `textstatic.py`
runner. Files are read as text and are never run, rendered or resolved. v1 is a
proxy: it proves that "everything on" is the default selection in this file. It
does not prove that a given instrumentation's telemetry goes unused, so it
emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- **Deployment YAML** (`.yaml`/`.yml`): Kubernetes manifests, Compose files
  and other YAML. Read with `owner_d/miniyaml.py`, one document (`---`) at a
  time.
- **Dockerfiles**: the shipped stages, meaning the final stage and the stages
  it is built `FROM`. Read with `owner_d/dockerfile.py`.
- **`package.json` scripts**. Development scripts (names with `dev`, `test`,
  `debug`, `watch`, `lint`, `e2e`, `local`) are skipped.
- **Node.js setup code** (`.js`/`.mjs`/`.cjs`/`.ts`/`.mts`/`.cts`) that
  imports `@opentelemetry/auto-instrumentations-node`.

No context settings are required.

### Detection rule

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `[<Kind>/<name>:\|service/<name>:]python:opentelemetry-instrument` | A YAML document starts the `opentelemetry-instrument` launcher (also as a path, e.g. `/app/.venv/bin/opentelemetry-instrument`) and does not mention `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` or `--python_disabled_instrumentations` | medium for a Kubernetes object or Compose service; low for other YAML |
| `[...]node:auto-instrumentations-node/register` | A YAML document loads `@opentelemetry/auto-instrumentations-node/register` (via `--require`/`-r`/`--import` or `NODE_OPTIONS`) and sets neither `OTEL_NODE_ENABLED_INSTRUMENTATIONS` nor `OTEL_NODE_DISABLED_INSTRUMENTATIONS` | medium for a Kubernetes object or Compose service; low for other YAML |
| `<CMD\|ENTRYPOINT\|ENV>:python:...` / `...:node:...` | The same launchers appear in an `ENV` of the shipped stages or in the effective (last) `CMD`/`ENTRYPOINT`. No shipped instruction and no global `ARG` mentions the selection | low: `docker run -e` or the orchestrator can still add the selection |
| `scripts/<name>:node:...` / `scripts/<name>:python:...` | A `package.json` script runs one of the launchers and the file does not mention the selection | low |
| `node:getNodeAutoInstrumentations()` | `getNodeAutoInstrumentations()` or `getNodeAutoInstrumentations({})` is called and the file does not mention `OTEL_NODE_*_INSTRUMENTATIONS` | low: the function reads those variables at runtime |

A repeated identity gets `#n`.

Defaults, from the official docs:

- Python: "The Python agent by default will detect a Python program's packages
  and instrument any packages it can. This makes instrumentation easy, but can
  result in too much or unwanted data."
- Node.js: "By default, all supported instrumentation libraries are enabled."
  That is about 45. `getNodeAutoInstrumentations()` leaves out only
  `instrumentation-fs` and `instrumentation-host-metrics`. A per-instrumentation
  `{ enabled: false }` takes precedence over the environment variables.

Not flagged:

- a selection anywhere in the same YAML document, in the shipped Dockerfile
  stages or in the JS file (an unresolved value counts as a selection)
- YAML documents with `envFrom:`, `env_file:` or `environmentFiles:`, because
  the selection may be in that external env source
- `getNodeAutoInstrumentations(<config>)` with any other argument
- package names (`opentelemetry-instrumentation-*`), `opentelemetry-bootstrap`,
  comments, JS strings, `RUN` lines and stages that do not ship
- hits with `# noqa` / `# noqa: OBS-12` (YAML, Dockerfile) or
  `// noqa: OBS-12` (JS) on the hit line or on the comment lines directly
  above it

Researched and deliberately not flagged:

- Collector components that are defined but not referenced by a pipeline.
  "Configuring a receiver does not enable it", and the same holds for the other
  component types, so they are never started.
- The Java agent's `otel.instrumentation.common.default-enabled=false`. The
  docs call it advanced usage, "not recommended for most users".
- CloudWatch agent JSON, which collects only the sections it lists.

The following are not evaluated. They are listed as limitations, never
reported clean:

- Helm/Go templates and YAML outside the `miniyaml` subset
- Dockerfiles `dockerfile.py` cannot read
- a `getNodeAutoInstrumentations(` call with unbalanced parentheses
- development/test/CI files: the OBS-09 path tokens, also inside directory
  names (`contract-tests/`), plus `.github/`

Files without a launcher are out of scope (`Unsupported`), so the scan worker
passes only auto-instrumentation launches to the check.

### Limitations

The check reads one file at a time. It cannot see:

- kustomize overlays
- Helm values merged into templates
- env added by `docker run`
- which libraries are installed
- how much telemetry each instrumentation produces

Not covered in v1:

- Java and vendor agents (`ddtrace-run`, New Relic)
- the ADOT/OpenTelemetry Lambda layer wrappers
- the OpenTelemetry Operator `Instrumentation` resource
- JSON task definitions

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs12/obs12-01-positive-input.json
```

## OBS-14 — Dev/QA/staging telemetry ingested by default

Flags non-production telemetry that is shipped to a managed (paid) backend
with no volume reduction. The check is static and uses the `textstatic.py`
runner. Settings are read with the OBS-05 entry reader and collector configs
with `otelconfig.py`; nothing is run, rendered or resolved. It is the
non-production counterpart of OBS-05 and OBS-01, which skip non-production
files.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- configuration with OpenTelemetry SDK settings (`OTEL_EXPORTER_OTLP_*ENDPOINT`,
  `OTEL_TRACES_SAMPLER[_ARG]`, `OTEL_TRACES_EXPORTER`, `OTEL_LOGS_EXPORTER`,
  `OTEL_SDK_DISABLED`, `OTEL_RESOURCE_ATTRIBUTES`, or their `otel.*` forms):
  YAML/JSON (Kubernetes `env`, Compose `environment`, SAM/serverless
  variables, Helm values), `.env`, `.properties`, TOML, INI and Dockerfile `ENV`
- OpenTelemetry Collector configs (plain, `OpenTelemetryCollector`,
  ConfigMap; ADOT uses the same format)
- AWS X-Ray sampling rules: `FixedRate` (API/CloudFormation) and X-Ray SDK
  local rule files (`default`/`rules` with `rate`)

Optional context: `context.environment`. A non-production value (for example
`"staging"`) marks files with no marker of their own as non-production. Any
other value has no effect. A value that is not a string is rejected.

### Detection rule

A finding needs a non-production environment, a managed destination and no
reduction.

The environment comes from, in order:

1. a literal `deployment.environment.name` (or `deployment.environment`)
   attribute in the same block (`OTEL_RESOURCE_ATTRIBUTES`, or a collector
   `resource`/`attributes` processor in the pipeline). When present, it
   decides, so `production` is never flagged.
2. key names (Compose service, Dockerfile stage, CR/ConfigMap name, X-Ray
   rule/service name)
3. the file path
4. `context.environment`

Non-production tokens: `dev`, `develop`, `development`, `local`, `staging`,
`stage` (path only), `stg`, `qa`, `uat`, `test`, `testing`, `sandbox`,
`preprod`/`pre-prod`, `nonprod`/`non-prod`. Names that also carry `prod`,
`production`, `prd` or `live` are ambiguous and are not used.

Managed destinations:

- OTLP endpoints on known ingest domains: `amazonaws.com`, Honeycomb, New
  Relic, Datadog, Grafana Cloud, Lightstep, Splunk (`signalfx.com`),
  Dynatrace, Elastic Cloud, Google Cloud, Coralogix, Logz.io, Sumo Logic,
  Axiom, Uptrace, SigNoz Cloud, OneUptime
- vendor-only collector exporters: `awsxray`, `awscloudwatchlogs`,
  `datadog`, `googlecloud`, `azuremonitor`, `coralogix`, `logzio`,
  `sumologic`, `sapm`, `alibabacloud_logservice`
- X-Ray itself

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `nonprod-traces:<endpoint key path>` | Traces go over OTLP (`OTEL_TRACES_EXPORTER` unset or containing `otlp`, SDK not disabled) to a managed endpoint (the traces endpoint wins over the generic one), and the sampler keeps every trace. Explicit: `always_on`/`parentbased_always_on`, or `*traceidratio` with arg 1. Default: no sampler (spec default `parentbased_always_on`), or `*traceidratio` without an arg (spec default 1.0). | medium if explicit; low if default |
| `nonprod-logs:<exporter key path>` | `OTEL_LOGS_EXPORTER` contains `otlp`, and the logs (or generic) endpoint is managed. The SDK does not sample logs. | medium if a DEBUG/TRACE level (OBS-01 key rules) is set in the same block; low otherwise |
| `[<resource>:]pipeline/<id>:nonprod-export` | A collector traces/logs pipeline exports to a managed destination with no `probabilistic_sampler`, `tail_sampling`, `filter` or `logdedup` processor | low, because SDK sampling upstream is not visible |
| `xray-sampling-rule:<name>` | An X-Ray rule has `FixedRate`/`rate` = 1, so every matching request beyond the reservoir is traced | medium |

Confidence is always `low` for `dev`, `development` and `local`: a single
developer produces little volume, and full sampling there is a common choice.
Confidence is never `high`, because the environment is inferred from names.
If an env-var hit's block sets no `deployment.environment[.name]`, the
summary says so: the backend then cannot drop or sample the data by
environment, which is the taxonomy's candidate optimization.

Not flagged:

- loopback, in-cluster or Compose service endpoints (`otel-collector:4317`),
  unknown hosts, unresolved `${...}`/`$(...)` values and unset endpoints (the
  SDK default is `localhost`)
- a ratio below 1, `always_off`, and remote/other samplers (`xray`,
  `jaeger_remote`)
- `OTEL_SDK_DISABLED=true`, and `OTEL_TRACES_EXPORTER=none`/`console`
- collector pipelines with a reduction processor, connector-fed pipelines,
  metrics/profiles pipelines, exporters not defined in the file, and vendor
  exporters with a loopback endpoint (LocalStack)
- Lambda/API Gateway `Tracing: Active` with no custom rule, because the X-Ray
  default rule already samples (1 request/second plus 5%)
- hits with `# noqa` / `# noqa: OBS-14` on the hit line or directly above it

The following are not evaluated. They are listed as limitations or as
declined files, never reported clean:

- files with no non-production marker, including production files
- test/docs/example material: directory tokens `tests`, `testdata`,
  `fixture(s)`, `e2e`, `mock(s)`, `doc(s)`, `example(s)`, `sample(s)`,
  `tutorial(s)`, `spec(s)`, file names with `example`/`sample`/`template`/
  `dist`, and CI directories
- Helm templates, YAML outside the `miniyaml` subset, invalid JSON/TOML/INI,
  and TOML inline tables holding OTel keys

Python and other file types are `Unsupported`, so the scan worker sends only
telemetry configs.

### Limitations

The check reads one file at a time. It cannot see:

- runtime overrides, `env_file`/`envFrom`/Secrets
- samplers set in code or remotely
- `--config` merges
- where a self-hosted collector forwards data
- probabilistic sampler percentages

No measurements are emitted, because ingest volume and cost need backend
usage data broken down by `deployment.environment.name`.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs14/obs14-01-positive-input.json
```
