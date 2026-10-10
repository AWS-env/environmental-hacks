# Owner D detectors (runtime ops)

Contract v1 detectors for Owner D taxonomy checks. The shared input/result
boundary is defined in [`docs/DETECTOR_CONTRACT.md`](../../docs/DETECTOR_CONTRACT.md);
JSON Schema is the source of truth. Detectors here are pure functions over
contract payloads: they never call AWS APIs, and source collection stays outside
the detector.

A static check with required context settings exposes a module-level
`REFERENCE_SETTINGS` holding exactly the reference values from its "Context
settings" table below. The repository scanner (`scanner/adapters/owner_d.py`)
passes them in the contract `context` (settings the scanner sets explicitly
win) and lists them in the check's report notes.
`tests/test_reference_settings.py` fails if a module is missing a documented
value. Telemetry checks take theirs from `owner_d/aws/registry.py`.

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
| `owner-d-log-analyzer` | `owner_d/aws/log_handler.py` | Logs `DescribeLogGroups`, `ListTagsForResource`, Logs Insights `StartQuery`/`GetQueryResults`/`StopQuery` | OBS-07, OBS-11, OBS-17 |
| `owner-d-trace-analyzer` | `owner_d/aws/trace_handler.py` | X-Ray `GetTraceSummaries`, `BatchGetTraces` (5 ids per call) | LLM-10, LLM-05 |

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
  LLM-10 and LLM-05 can analyze. Both checks share one collection.
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
(`cpu_metrics`), OBS-06 (`metrics`), OBS-07 (`log_groups`), OBS-11 and
OBS-17 (`logs_insights`), LLM-10 and LLM-05 (`traces`). A detector module plugs in through a normalizer, by default
`normalize_<source>(raw, *, settings)`. It returns contract `telemetry`
sources, or `{"scope", "sources", "limitations"}`. Normalizers that take raw
API pages use an adapter. The adapter builds the sources with account-free
locators and drops `log_group_arn`. OBS-06 and OBS-07 use the `list_metrics`
and `describe_log_groups` adapters; LLM-10 and LLM-05 use `xray_traces`:

```python
TelemetryCheck("OBS-06", "owner_d.obs06", "metrics", normalizer="owner_d.obs06:normalize_list_metrics",
               adapter="list_metrics", defaults=OBS06_DEFAULTS),
```

Without `checks`, the telemetry analyzer runs INF-01 and OBS-06. OBS-06 needs
`list_metrics` (for example `{"namespace": "OwnerD/Demo"}`); without it,
ListMetrics lists every namespace. Pass `"checks": ["INF-01"]` to run only
one of them. The log analyzer runs OBS-07, OBS-11 and OBS-17 by default.
OBS-11 and OBS-17 each run one Logs Insights query over the allowlisted groups
(billed per GB scanned); pass `"checks": ["OBS-07"]` to skip both.

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
- A log-analyzer run without `checks` runs 3 checks: OBS-07 reads
  DescribeLogGroups, and OBS-11 and OBS-17 each run their own Logs Insights
  query over the same window and log groups. A default run therefore scans
  that log data twice. Pass `checks` to run fewer queries. On the demo log
  group, one query over 24 hours scanned about 22 KB.
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

## OBS-11 — Duplicate/repeated log lines (retry loops, flapping checks)

Flags CloudWatch Logs log groups where one log message, once timestamps, IDs and
numbers are replaced, makes up a large share of the application events in the
query window and is not a per-request line (one line per invocation, spread
over time like the invocations). Typical causes are a retry loop that logs every
attempt or a flapping health check that logs every poll. CloudWatch Logs bills ingestion and storage per GB
([pricing](https://aws.amazon.com/cloudwatch/pricing/)), and Logs Insights
[pattern analysis](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData_Patterns.html)
is the console way to find such "frequently occurring or high-cost log lines".
The check runs on the `owner-d-log-analyzer` route (source `logs_insights`).
Without `checks`, the log analyzer runs OBS-07, OBS-11 and OBS-17.

### Input

`owner_d.obs11.LOGS_INSIGHTS_QUERY` runs over the allowlisted log groups and
the bounded window:

```text
fields regexReplace(regexReplace(regexReplace(regexReplace(regexReplace(regexReplace(substr(@message, 0, 400), "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<uuid>"), "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.,][0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?", "<ts>"), "[0-9a-fA-F]{8,}", "<hex>"), "[0-9]+", "<n>"), "[[:space:]]+", " "), "^ | $", "") as normalized, concat(@logStream, " ", toMillis(datefloor(@timestamp, 1s))) as slot
| stats count(*) as n, count_distinct(slot) as n_slots, min(@timestamp) as first, max(@timestamp) as last by @log, normalized
| fields if(n >= 10 or normalized like /^(START|END|REPORT) RequestId: |^(INIT_START|INIT_REPORT|INIT_RUNTIME_DONE|RESTORE_START|RESTORE_REPORT|RESTORE_RUNTIME_DONE|EXTENSION|TELEMETRY)\s|"type" *: *"platform[.]/, normalized, "<other>") as message
| stats sum(n) as occurrences, sum(n_slots) as slots, min(first) as first_seen, max(last) as last_seen by @log, message
| sort @log asc, occurrences desc
```

The first `fields` replaces UUIDs, ISO timestamps, runs of 8+ hex characters
and digit runs in the first 400 characters with `<uuid>`, `<ts>`, `<hex>` and
`<n>`, and collapses whitespace. `regexReplace` uses RE2 syntax. It also builds
a **slot**: the log stream plus the second the line was logged in
(`datefloor(@timestamp, 1s)`). A Lambda log stream belongs to one execution
environment, which runs one invocation at a time, so lines of one retry burst
share a slot while a line logged once per request spreads over as many slots as
the invocations do. `count_distinct(slot)` counts the slots of each message.
Slots stand in for request IDs because application lines written to stdout
(`print`, or `console.log` in text format) carry no `@requestId`: Logs Insights
discovers `@requestId` for Lambda platform lines, and Lambda adds `requestId` only
to lines from the runtime's logging library
([discovered fields](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData-discoverable-fields.html),
[Lambda log formats](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs-logformat.html)).
`count_distinct` is approximate only at high cardinality
([stats](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-Stats.html));
the normalizer caps a slot count at its line count.

Messages seen fewer than 10 times fold into one `<other>` row per log group
(its `slots` are not used), so the row count stays small while the totals stay
exact. Lambda platform lines keep their own rows. It is still one query, and
bytes scanned depend on the window and the log groups, not on the query.

`normalize_logs_insights(raw, *, settings)` turns the rows into one `telemetry`
source per `resource:log-group/<log group name>` scope item. The locator is
`logs-insights://<region>/log-group/<name>`, and the account ID prefix of
`@log` is dropped.

| Field | Meaning |
| --- | --- |
| `log_group`, `window` | Log group name; query window `{start, end}` |
| `events` | Every event the query counted |
| `platform_events` | Lambda platform lines: `START`/`END`/`REPORT RequestId:`, `INIT_START`, `INIT_REPORT`, `INIT_RUNTIME_DONE`, `RESTORE_*`, `EXTENSION`, `TELEMETRY`, JSON `"type":"platform.*"` |
| `application_events` | `events - platform_events` |
| `invocations` | Number of `START RequestId:` lines, or `null` when none were seen (not a Lambda log group) |
| `invocation_slots` | Slots of the `START` lines (at most `invocations`), or `null` with `invocations` |
| `folded_events` | Events in the `<other>` row |
| `messages` | Up to 20 application messages, most frequent first: `message` (redacted, at most 200 characters), `message_sha256`, `occurrences`, `slots` (at most `occurrences`), `share`, `first_seen`, `last_seen` |
| `omitted_messages` | Messages beyond those 20 (still counted in `application_events`) |

Reported text is redacted: emails, IPv4/IPv6 addresses, long tokens, and the
values of `password=`, `token:`, `api_key=`, `Authorization`, `Bearer` and
similar keys. Numbers, UUIDs and hex IDs are already placeholders.

Groups get no source, and stay out of `evaluated_scope` with a limitation,
when:

- the Logs Insights row limit was reached: the last group in the rows and any
  group not seen have incomplete counts;
- a row has a missing message or a non-integer count or slot count (that
  group only);
- a row has no `@log` (every group, since totals are unknown).

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_events` | Application events a group needs in the window before it is evaluated | `100` |
| `min_repeats` | A message is flagged only when it occurs more often than this (at least 9, because the query folds messages seen fewer than 10 times) | `50` |
| `min_share` | ... and when it is more than this fraction of the group's application events | `0.2` |
| `exempt_message_markers` | Case-insensitive substrings that mark heartbeat lines; may be empty | `["heartbeat"]` |

`owner_d.obs11.REFERENCE_SETTINGS` and the registry defaults hold these
values. For scale: the AWS SDKs'
[standard retry mode](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)
makes at most 3 attempts per call, so a message seen more than 50 times that
is also more than 20% of a group's lines is far beyond normal per-call retries.
All four values are judgment calls. Missing or invalid settings make the
result `unavailable`.

### Detection rule

For each group with `application_events >= min_events`, a message is flagged
when `occurrences > min_repeats` **and** `occurrences / application_events >
min_share`. Messages that pass both thresholds but are not flagged (a
limitation names them):

- Lambda platform lines;
- heartbeat lines (`exempt_message_markers`);
- per-request lines: `invocations` is known, `occurrences <= invocations`
  (at most one line per invocation on average), **and** the lines are no more
  than `CLUSTER_FACTOR` (2) times as clustered as the invocations:
  `occurrences / slots <= 2 × invocations / invocation_slots`. This covers
  request summaries and the demo's control path. A burst logged inside one or
  a few invocations shares a few slots, so it is flagged however many other
  invocations the group had. The factor of 2 absorbs lines that cross a second
  boundary and routes somewhat busier than the group as a whole. Comparing
  with the `START` lines keeps busy functions exempt: when each execution
  environment runs several invocations per second, the summary line and the
  `START` lines are clustered alike.

Detector 1.0.0 (issue #235) exempted every message with `occurrences <=
invocations`, so a retry burst in one invocation was missed once the group had
more invocations than burst lines (issue #455). Version 1.1.0 adds the slot
test.

There is one finding per flagged message, with the identity
`repeated-log-line:<first 16 hex characters of message_sha256>`. It cites
`messages`, `application_events`, `invocations`, `invocation_slots` and
`window`, and quotes the redacted text with its lines per invocation and per
slot. Confidence:

- `high` when the text has no `<n>` placeholder (identical apart from
  timestamps and IDs) and `invocations` is known;
- `medium` otherwise, because lines that differ only in numbers were grouped,
  or the per-invocation rate is unknown.

Measurements stay absent: the bytes of the repeated lines are not measured.

### Limitations

- Messages are compared on their first 400 characters after normalisation.
- Invocations are approximated by slots (log stream and second), not request
  IDs. A retry loop that waits a second or more between attempts spreads over
  slots like per-request lines, so it is not flagged while it has no more lines
  than there are invocations.
- A once-per-request line from a route whose invocations are more than twice as
  clustered as the group's invocations as a whole (for example a burst of
  requests to one route in an otherwise quiet function) is flagged.
- Non-Lambda log groups have no invocation count, so the per-request exception
  cannot apply to them.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs11/obs11-01-positive-input.json
```

`tests/fixtures/obs11/recorded-logs-insights.json` is a real response to
`LOGS_INSIGHTS_QUERY` for `/aws/lambda/owner-d-telemetry-demo`, last 24 hours
(recorded 2026-10-10, 33,400 bytes scanned), with the account ID replaced by
`123456789012`. The log lines are the demo's synthetic output: one `all`
invocation, ten `llm10` and twelve `llm05` invocations, so 23 invocations in
23 slots and 77 application events. `obs11-01-positive-input.json` is built
from it with `min_events: 50` and `min_repeats: 10`. The waste line `upstream
inventory-svc unavailable, retrying` (20 lines in one slot, all in the same
millisecond of one invocation) is flagged; detector 1.0.0 exempted it because
20 <= 23 invocations. The control line is logged once and folds into
`<other>`. The four LLM-05/LLM-10 summary lines (11 or 12 lines, one per slot)
are per-request lines, and stay exempt with `min_share: 0.1`. With the
reference settings the group is not evaluated yet (77 < 100 application
events). Within one fresh 24-hour window, four `{"scenario": "all"}`
invocations (132 application events, 80 retry lines) or two with `"repeat":
50` (126 events, 100 retry lines) are enough for the reference settings.

## OBS-17 — Verbose fields retained (full stack traces, request bodies)

Flags CloudWatch Logs log groups that ingest oversized events, such as echoed
request bodies, or that repeat full stack traces. CloudWatch Logs bills
ingestion per GB ingested and storage per GB
([pricing](https://aws.amazon.com/cloudwatch/pricing/)), so a payload copied
into every line is paid for on every line. The OWASP
[Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
suggests "separate files/tables for extended event information such as error
stack traces or a record of HTTP request and response headers and bodies". It
also lists sensitive personal data among the data to exclude from logs. The
check reads existing logs through the log analyzer (`logs_insights` source)
and reports sizes and counts only. Message text, bodies and stack frames never
leave CloudWatch Logs.

### Input

`owner_d.obs17.LOGS_INSIGHTS_QUERY` runs over the allowlisted groups and the
analyzer's window (24 hours by default):

```text
filter @message not like /^(START|END|REPORT) RequestId: |^(INIT_START|INIT_REPORT|INIT_RUNTIME_FAILURE|RESTORE_START|RESTORE_REPORT|EXTENSION|TELEMETRY) /
| filter @message not like /"type"\s*:\s*"platform\./
| parse @message /^\[(?<o17_text_level>[A-Za-z]+)\]/
| parse @message /(?<o17_trace>Traceback \(most recent call last\)|(\n|\r|\\n|\\r)(\t|\\t| {2,})at [^\s(]+[ (]|goroutine \d+ \[)/
| parse @message /"(?<o17_body>[\w.-]{0,60}([Bb]ody|[Pp]ayload))"\s*:/
| fields strlen(@message) as o17_chars, least(ceil(strlen(@message) / 512), 65) as o17_bucket,
    substr(toupper(coalesce(level, levelname, severity, o17_text_level, "")), 0, 16) as o17_level
| stats count(*) as events, sum(o17_chars) as chars, max(o17_chars) as max_chars,
    count(o17_trace) as trace_events, count(o17_body) as body_events by @log, o17_level, o17_bucket
```

- Lambda platform lines (text `START`/`END`/`REPORT`/`INIT_*` and JSON
  `platform.*` records) are excluded.
- Size is `strlen(@message)` ([string functions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-operations-functions.html)),
  in Unicode code points. Events are grouped into 512-character buckets: bucket
  `k` holds `(512(k-1), 512k]` and bucket 65 everything above 32,768. Each
  bucket's sum is exact, so any `max_event_bytes` that is a multiple of 512 is
  evaluated exactly.
- Stack-trace markers: Python `Traceback (most recent call last)`, a frame
  line after a line break (`\tat x.y(` Java, `    at fn (` Node.js and .NET),
  Go `goroutine N [`. They are matched raw or JSON-escaped (`\n\tat`).
- Echoed-body markers: a JSON key that ends in `body` or `payload`
  (`"body":`, `"request_body":`, `"requestBody":`, `"payload":`).
- Level: JSON `level`/`levelname`/`severity`, or the Lambda text prefix
  `[ERROR]`. ERROR/FATAL/CRITICAL (and pino 50/60) are error levels.
  TRACE/DEBUG/INFO/NOTICE/WARN (and pino 10-40) are below ERROR. Anything else
  is unknown.

`normalize_logs_insights(raw, *, settings)` builds one `telemetry` source per
queried log group, with scope `resource:log-group/<name>` and the account ID
removed from `@log`. Every queried group gets a source, with zero counts when
it had no application events. Data fields: `events`, `event_chars`,
`max_event_chars`, `size_histogram` (per bucket: `events`, `chars`,
`trace_events`, `body_events`), `events_by_level`, `trace_events_by_level`
(`error`, `below_error`, `unknown`), `body_field_events`, `bucket_chars`,
`window` and `problems`. The following go into `problems`, which leaves the
group unevaluated:

- the query hit its row limit;
- a row without a queried `@log`;
- a non-numeric count;
- a size outside its bucket's bounds.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_event_bytes` | Events larger than this many characters count as oversized. Must be a multiple of 512, from 512 to 32,768 | `4096` |
| `min_bytes_share` | Oversized events must carry more than this share of the group's application log characters | `0.25` |
| `max_trace_repeats` | Most stack-trace events allowed below ERROR level in the window | `1` |
| `max_error_trace_repeats` | Most ERROR-level stack-trace events allowed in the window | `10` |
| `min_events` | Groups with fewer application events are not evaluated | `20` |

`obs17.REFERENCE_SETTINGS` holds these values, and the registry's
`OBS17_DEFAULTS` copies them; a test keeps the two equal. All five are team
choices. No authoritative per-event size limit exists, and 4 KiB is well above
a structured line. Missing or invalid settings make the result `unavailable`.

### Detection rule

- `oversized-log-events`: the characters in buckets above
  `max_event_bytes / 512` are strictly more than `min_bytes_share` of the
  group's characters. The summary counts the large events that carry a stack
  trace or a body/payload key. Confidence is `medium` when such a marker is
  present, `low` otherwise. Evidence: `size_histogram`, `event_chars`,
  `events`.
- `repeated-stack-traces`: either stack-trace events below ERROR level exceed
  `max_trace_repeats`, or ERROR-level trace events exceed
  `max_error_trace_repeats`. Confidence `medium`. Evidence:
  `trace_events_by_level`, `events`.

Exceptions: Lambda platform lines are excluded by the query. ERROR-level traces
up to `max_error_trace_repeats` are not flagged; their bytes still count for
the size rule. Traces without a recognized level are noted but never flagged.
Large events under the share are noted. Fingerprints use the identity per
scope, so changing sizes keep the finding. Measurements stay absent.

### Limitations

- `strlen` counts code points. It is a lower bound for bytes of non-ASCII text
  and leaves out CloudWatch's per-event overhead.
- Logs Insights sees whole events, so per-field sizes cannot be measured.
- A trace split into one event per line is not recognized as a trace.
- Bodies logged under other key names are missed.
- The rule cannot tell whether the detail is needed, for example in an audit
  log. The recommendation asks the team to confirm that first.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs17/obs17-01-demo-input.json
```

`tests/fixtures/obs17/demo.insights.json` holds real Logs Insights responses
from the synthetic `owner-d-telemetry-demo` log group in ap-south-1. The
account ID is replaced with `123456789012`, and `@ptr` values are removed. It
has three responses: the whole group, the OBS-17 waste path only and the
control path only. The CLI input is normalized from the whole-group response.
The other test inputs are synthetic.

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

## LLM-05 — Redundant chained calls in multi-step pipelines (X-Ray traces)

Flags agent runs and pipelines that send the same model the same request again,
using AWS X-Ray traces the client already records: a step that re-sends an
unchanged request back to back (the later call cannot use the earlier output, so
that output is discarded and recomputed), or a chain that keeps re-asking
requests it already sent. It proves what the sampled traces show. No token or
cost measurements are emitted. LLM-04 (static) covers a different pattern: the
whole context passed to every step.

### Input

`llm05.normalize_xray_traces(traces)` takes the same `BatchGetTraces` `Traces`
list as LLM-10 and builds on `llm10.normalize_xray_traces`. Traces that LLM-10
skips (unparseable document, in-progress or orphaned subsegment) are skipped
with the same reasons. For each accepted trace, LLM-05 walks the same
OpenTelemetry GenAI spans (metadata or annotations, embedded or separately sent
subsegments, `invoke_agent` / `invoke_workflow` runs). It records per model
call (`gen_ai.operation.name` `chat` / `text_completion` / `generate_content`):

| Field | Source |
| --- | --- |
| Run | the enclosing agent span, or the entrypoint when there is none |
| Model | `gen_ai.request.model` (required for comparison) |
| Request hash | SHA-256 of canonical JSON over the model, `gen_ai.input.messages` (or legacy `gen_ai.prompt` / indexed `gen_ai.prompt.<n>.*`), `gen_ai.system_instructions`, `gen_ai.tool.definitions` and the recorded request parameters (`max_tokens`, `temperature`, `top_p`, `top_k`, `seed`, `stop_sequences`, penalties, `choice.count`, `gen_ai.output.type`) |
| Digest basis | when no input content is recorded: `gen_ai.input.messages.hash`, an app-recorded digest of the canonical request (not a semconv attribute; for apps that keep content out of traces) |
| Sampling | `gen_ai.request.temperature` |

Only hashes leave the normalizer, never prompt content. A framework `chat` span
around the instrumented client span is one call; the inner span fills in
missing attributes. `llm05.telemetry_sources` is LLM-10's: one `telemetry`
source per `entrypoint:<name>`.

Each entrypoint summary has `traces_with_model_calls`, `traces_analyzed`,
`traces_without_inputs`, `traces_skipped`, `skip_reasons`,
`failed_model_calls`, `model_calls_without_input`, `uncompared_pairs`,
`retry_repeats_excluded`, `sampling_repeats_excluded`, the worst
`max_consecutive_identical` streak, `repeated_chains` (runs with at least one
repeated request) and per-trace `traces`. A trace is analyzable when it has a
successful model call and either every run makes one model call, or at least
one consecutive pair of calls is comparable.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_traces` | Analyzable traces needed before an entrypoint is evaluated | `10` |
| `max_identical_consecutive_calls` | Most identical requests allowed back to back in one run | `1` |
| `min_chain_calls` | Fewest comparable model calls in a run for the chain rule | `3` |
| `min_repeat_share` | Share of a chain's calls that repeat an earlier request, at or above which it is flagged | `0.5` |

The values are `llm05.REFERENCE_SETTINGS` and the registry defaults. A request
repeated unchanged back to back is already redundant work. The AWS agentic AI
lens
[AGENTCOST02-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html)
says that "agents repeat work constantly: identical prompts, semantically
equivalent requests, the same planning steps", and
[AGENTSUS02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html)
that "every duplicate model call ... is work the agent fleet has already done
once". `min_traces` follows LLM-10. `min_chain_calls` and `min_repeat_share`
are team judgment. Missing or invalid settings make the result `unavailable`.

### Detection rule

For each entrypoint with at least `min_traces` analyzable traces, successful
model calls in each run are ordered by start time:

- `consecutive-identical-calls`: more than `max_identical_consecutive_calls`
  consecutive calls have the same model and request hash (on the same basis).
  Confidence is `high` when two or more traces breach, otherwise `medium`.
- `repeated-chain-requests`: a run with at least `min_chain_calls` comparable
  calls, at least `min_repeat_share` of which repeat an earlier request of the
  same run (A, B, A, B). Confidence is `medium` when two or more traces breach,
  otherwise `low`.

Evidence cites `traces_analyzed` and `max_consecutive_identical` or
`repeated_chains`. Not counted:

- failed calls (`error`, `throttle` or `fault` true, or `error.type`), and an
  identical request right after a failure (a retry), as in LLM-10;
- identical calls whose recorded `gen_ai.request.temperature` is above 0
  (intentional sampling, e.g. self-consistency). An unrecorded temperature is
  not assumed to be above 0;
- calls in different agent runs (orchestrator and sub-agents);
- requests that differ in any recorded request parameter, e.g. a retry with a
  larger `max_tokens` after a truncated answer.

`gen_ai.input.messages` and `gen_ai.system_instructions` are Opt-In in the
[OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md).
When chained calls record neither content nor a digest, the trace is not
analyzable. The limitations say so, and the entrypoint is `unavailable` when too
few traces remain. It is never reported clean. Paraphrased or overlapping
questions, duplicates across runs and dependencies through tools or state are
not detected.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm05/llm05-01-positive-input.json
```

The fixtures under `tests/fixtures/llm05/` are synthetic `BatchGetTraces`
responses shaped on the X-Ray segment document format, not production traces.
The telemetry demo's opt-in `LLM-05` scenario emits a matching waste/control
pair (see `examples/telemetry-demo/README.md`).


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

## OBS-18 — Inconsistent log field names / over-structuring

The counterpart of OBS-04 for code that already logs structured fields. Flags
one field written under several spellings across the scanned project
(`user_id` / `userId` / `usr_id`), single calls that write very many fields, and
whole objects dumped as fields. Backends such as CloudWatch Logs Insights index
each spelling as its own field, so filters, stats and field indexes on one name
miss the others. Static only: real log events are not read.

### Input

One `static` source per `file:<path>` scope item (`.py` only) and the context
settings below. Field spellings are collected from every evaluable Python
source in the payload, because one payload is one project.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_field_spellings` | Most spellings one field concept may have in the project | `1` |
| `max_fields_per_event` | Most literal fields one call may write | `20` |

The thresholds are judgment calls, so they are required; missing or invalid
settings make the result `unavailable`. OpenTelemetry names are lowercase
snake_case and reuse existing conventions; ECS field names are lowercase, use
underscores and avoid abbreviations. SRC-22: "`user_id` in one service and
`userId` in another breaks aggregation" and "Don't create 50 fields when 10 will
do". Hard ceilings: Logs Insights extracts at most 200 fields from a JSON event,
and OpenTelemetry SDKs keep 128 attributes per record by default. Powertools
already adds about 9 keys (14 with `inject_lambda_context`) to every record.

### Detection rule

Field sites (logger recognition as in OBS-02):

- keyword fields on log calls (structlog, Powertools `Logger`, loguru), except
  the stdlib keywords (`exc_info`, `stack_info`, `stacklevel`, `extra`, ...);
- `extra={...}` and `extra=dict(...)` literals;
- `append_keys`, `thread_safe_append_keys`, `append_context_keys`, `bind`, `new`
  on a logger, and structlog `bind_contextvars`;
- `json.dumps({...})` passed to a log call, or to `print()` in a Lambda handler
  module.

Nested dict literals are flattened with dots (`{"user": {"id": u}}` is
`user.id`), as Logs Insights does.

| Identity | Finding | Confidence |
| --- | --- | --- |
| `drift:<concept>` | The concept has more than `max_field_spellings` distinct spellings in the project. One finding per file that uses a spelling other than the most used one (tie: lower snake_case, then alphabetical). Evidence is the first such key. | `medium` for case/separator variants (`userId`, `user.id`), `low` for synonyms only |
| `wide:<qualname>:<receiver>.<method>` | One call writes more than `max_fields_per_event` literal fields (flattened leaves). `**spread` counts 0, so it is a lower bound. | `medium` |
| `dump:<qualname>:<receiver>.<method>` | A field set or value is a whole, schema-less object: `vars(x)`, `x.__dict__`, `locals()`. Typed records (`asdict(x)`, `x._asdict()`, `x.model_dump()`) have declared fields and are not flagged. | `medium`; `low` inside `json.dumps` (a JSON log format keeps it as one `message` string) |

Keys are normalised (camelCase split, `.`/`-`/space to `_`, lowercase) and
mapped through a short synonym list of abbreviations of one concept:
`user_id` (`usr_id`, `userid`), `request_id` (`req_id`), `correlation_id`
(`corr_id`), `session_id` (`sess_id`), `customer_id` (`cust_id`), `account_id`
(`acct_id`, `acc_id`), `status_code` (`http_status`, `http_code`, ...),
`error_message` (`err_msg`, `errmsg`, ...), and `duration_ms`, `duration_s`,
`duration` with `elapsed`/`latency`/`took` in the same unit. Different units,
qualified names (`user_id` vs `user_name`) and ambiguous words (`user`, `id`, `uid`,
`status`, `error`) are never merged.

Not counted or not judged:

- non-literal keys and non-literal `extra=`;
- Embedded Metric Format documents (a dict with an `_aws` key);
- vendored code, samples, tests, scripts and CLIs (same rules as OBS-04), and
  calls inside `if __name__ == "__main__":`;
- a key on a line with `# noqa` or `# noqa: OBS-18` (it leaves the drift count),
  and `wide`/`dump` calls whose first line carries one.

Missing, non-Python or unparseable files are left out of `evaluated_scope` and
contribute no spellings. Fields added by formatters or processors, logging
configuration outside Python and other languages are not visible. Two services
in one repository with different conventions are reported. No measurements are
emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs18/obs18-01-positive-input.json
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
allowed. Repository scans pass the reference value (`REFERENCE_SETTINGS`).

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
## LLM-09 — No token/cost observability (static proxy)

Flags Python modules whose LLM API calls discard the token usage each response
returns, in a payload where nothing records token usage. This v1 is a static
proxy: it proves that usage is dropped and that no token observability is
visible in the scanned files, not that spend is unknown. It emits no
measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`). No context settings are required. Python files are judged.
Some other files are read as text, only for markers, and never get findings:
dependency manifests (`requirements*.txt`, `pyproject.toml`, `Pipfile`,
`setup.cfg`), Dockerfiles, Procfiles, Terraform, YAML, CloudFormation JSON
templates and shell scripts.

### Detection rule

**Markers are payload-wide.** Instrumentation is set up once per process,
invocation logging once per account and Region, and a caller in another file
may read `response.usage`. Any of these markers in a non-vendored file means no
module is flagged, and a limitation names the marker:

- a usage read (`.usage`, `usage_metadata`, `input_tokens`/`output_tokens`,
  `prompt_tokens`/`completion_tokens`, Converse `inputTokens`/`outputTokens`,
  `x-amzn-bedrock-*-token-count`);
- OpenTelemetry GenAI attributes (`gen_ai.usage.*`) or token metrics;
- GenAI instrumentations (`opentelemetry-instrumentation-openai/anthropic/
  bedrock/botocore`, `opentelemetry-instrument`, ADOT) and LLM observability
  SDKs (OpenLLMetry, Langfuse, LangSmith, Helicone, ...);
- Bedrock model invocation logging, `requestMetadata` or application inference
  profiles;
- a whole response passed to a logger, `print` or a usage/metric helper.

**Findings are per module.** A module is flagged when at least one recognised
call (`llmcalls.py`: Anthropic `messages.*`, OpenAI chat/responses, Bedrock
`converse*`/`invoke_model*`) drops its response, and no call lets its
response escape.

- **Dropped:** the module reads only fields that cannot hold usage, such as
  `.content`, `.choices`, `["output"]` or stream deltas. The check follows
  `body.read()` into `json.loads`, stream events and `get_final_message()`.
- **Escaped:** the response is returned, passed to an unknown function,
  stored, serialized or read in a closure. Such a call is not judged, because
  code we cannot see may read its usage.

There is one finding per module, with identity `module:token-usage`. The
evidence is the first dropped call. Confidence is `medium` when the payload
also includes a manifest or IaC file (with no unparseable file); otherwise it
is `low`.

Not flagged: tests, `examples`/`samples`/`docs`/`notebooks`/`demo(s)`,
`scripts`, vendored code and calls under `if __name__ == "__main__"`.
`# noqa` or `# noqa: LLM-09` on a call line removes that call.

Bedrock still publishes `AWS/Bedrock` `InputTokenCount`/`OutputTokenCount` per
model. The finding is about attributing usage to a code path. Invocation
logging enabled in the console and instrumentation configured elsewhere are
not visible.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm09/llm09-01-positive-input.json
```

## TST-03 — Eager Test

Flags tests that call more distinct production methods/functions than a
configured limit, adapting tsDetect's
[Eager Test](https://github.com/TestSmells/TestSmellDetector/blob/master/src/main/java/testsmell/smell/EagerTest.java)
("a test method invokes several methods of the production object"). PyNose
has no Eager Test because Python has no reliable test-to-production class
mapping, so the caller names the production code. It is static (`ast` only)
and uses the same input, test recognition and per-file "nothing to flag" note
as TST-06.

### Context settings (required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_production_methods` | Most distinct production calls a test may make | `4` |
| `production_packages` | Dotted module prefixes that are the code under test | the repo's own packages, e.g. `["shop"]` |

`4` is tsDetect's `SpadiniThresholds` value (Spadini et al., MSR 2020);
`1` reproduces tsDetect's default rule ("more than one"). Without
`production_packages` nothing can be called production, so an empty result
would be a false clean claim. A missing or invalid setting makes the result
`unavailable`. Repository scans derive `production_packages` from the scanned
repository's top-level Python packages and modules (`src/` layout included,
tests excluded).

### Detection rule

Production code is whatever the test file imports (absolute or relative) from
a module under `production_packages`. Module paths with a test-ish segment
(`test`, `tests`, `testing`, `_testing`, `conftest`, `test_*`, `*_test(s)`)
are never production. A call is a production call when it is:

- a production function or class method: `restock(...)`, `pricing.tax(...)`,
  `Cart.from_dict(...)`;
- a method on a production object: a local variable, `with ... as` target,
  same-file pytest fixture parameter or `self.<attr>` (set in `setUp`,
  `setUpClass`, `setup_method`, the class body or the test) bound directly to
  a call of a production callable; or a chain rooted at one (`Cart().add()`,
  `make_cart().add()`).

Calls are counted once per distinct dotted name (`Cart.total` and
`Order.total` are two; `total` on two `Cart` objects is one), across the
whole body including lambdas, nested functions and assertion arguments. Not
counted: constructors (capitalised names such as `Cart()` or `Q()`),
assertion calls, builtins, stdlib/third-party/mock/test-helper imports
(unless listed), calls on `self`, calls on locals not bound to a production
call, attribute reads, and method names the test patches
(`patch.object(X, "m")`, `@patch("pkg.mod.X.m")`,
`monkeypatch.setattr(X, "m", v)`). A name re-bound in the test shadows the
import. There is no type inference: objects returned by production methods
and fixtures from `conftest.py` are not followed, so counts are lower bounds.

A test is flagged when its count is strictly above `max_production_methods`.
Unconditionally skipped tests are not flagged. One finding per test; evidence
starts at the `def` line, and the summary lists the calls in first-call
order. Confidence:

- `medium` above twice the limit;
- `low` otherwise.

`# noqa: TST-03` on the `def` line suppresses a finding. The identity is the
qualified test name, with `#n` for a redefined name.

The taxonomy cites an energy association for this smell (Kendall tau 0.432,
SRC-15). That evidence comes from JUnit/Maven projects and is not shown to
transfer to Python, so no measurements are emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst03/tst03-01-positive-input.json
```

## TST-11 — General Fixture

Flags test-fixture fields that some of the tests running the fixture never
read, following PyNose's
[General Fixture](https://github.com/JetBrains-Research/PyNose/blob/ASE2021/src/main/java/pynose/GeneralFixtureTestSmellDetector.java)
rule, adopted from tsDetect ("not all fields instantiated within the setUp
method of a test class are utilized by all test methods"; Meszaros:
[General Fixture](http://xunitpatterns.com/General%20Fixture.html)). Each test
pays for setup it does not need. It is static (`ast` only) and uses the same
input, test recognition and per-file "nothing to flag" note as TST-06. No
context settings.

### Detection rule

- **Fixtures.** unittest `setUp`/`asyncSetUp` (before every test) and
  `setUpClass` (once per class); pytest test-class `setup_method`,
  `setup_class` and `@pytest.fixture(autouse=True)` methods. Module-level and
  requested pytest fixtures are not judged: a test that does not request one
  does not pay for it.
- **Fields.** Attributes assigned on the fixture's first parameter (or the
  class name) directly in the fixture body.
- **Tests that run the fixture.** Every test class in the file whose
  same-file bases (mixins included) reach the fixture, following `super()` /
  `Base.setUp(self)` chains; an override without `super()` stops it. A
  class's tests are its own and inherited `test*` methods, minus
  unconditionally skipped ones.
- **Use.** Reading `self.<field>` (or `cls.`, `ClassName.`, `type(self).`) in
  the test or in any same-file method or property it reaches through `self`.
  `tearDown` reads are not test use.
- **Flag.** A field that at least one of at least 2 tests running the fixture
  never reads. One finding per fixture field, evidence at its first
  assignment.

Not flagged, to avoid false positives:

- fields the fixture chain reads (to build another field, or to pass to
  something); reading `self.x.close` in an `addCleanup(...)` argument is
  cleanup, not use;
- saved state that teardown passes back (`os.chdir(self.old_cwd)`); a
  resource teardown only closes (`self.conn.close()`) is still flagged;
- fields whose every assignment is call-free (literals, names, attribute
  reads), unittest attributes such as `maxDiff`, and side-effect handles from
  `.start()`, `enterContext()`, `enter_context()` or `__enter__()`;
- dynamic access: a test or helper that passes `self` out, uses
  `getattr`/`vars`/`__dict__`, or calls a `self` method that is neither in the
  file nor a `TestCase` method counts as using every field. The same in the
  fixture after the assignment counts as the fixture using it;
- classes that define `__init__`, `run`, `__call__`, `__getattr__` or similar
  run machinery (for example CPython's `ThreadableTest`, which swaps `setUp`).

Confidence:

- `medium` for a per-test fixture in a class hierarchy defined in the file
  (only unittest's `TestCase` is imported) when at least half of the tests
  skip the field;
- `low` otherwise (fewer skip it, a per-class fixture paid once, or an
  imported base class whose code may read the field).

`# noqa: TST-11` on the assignment line suppresses that field; on the
fixture's `def` line it suppresses the whole fixture. The identity is
`<DefiningClass>.<fixture>:<field>` (e.g. `OrderTest.setUp:cache`), with `#n`
for a redefined class.

The rule does not estimate what a field costs to build; many flagged fields
are cheap. The taxonomy has no General Fixture energy figure, so no
measurements are emitted.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst11/tst11-01-positive-input.json
```

## TST-02 — Lazy Test

Flags several tests of one test class (or the module-level pytest tests of one
file) that call the same production method. It ports tsDetect's
[Lazy Test](https://github.com/TestSmells/TestSmellDetector/blob/master/src/main/java/testsmell/smell/LazyTest.java)
("multiple test methods invoke the same method of the production object");
PyNose has no Lazy Test rule. It is static (`ast` only) and uses the same
input, test recognition and per-file "nothing to flag" note as TST-06.

### Context settings (optional)

| Setting | Meaning | Default |
| --- | --- | --- |
| `production_packages` | Top-level packages that hold the code under test, e.g. `["shop"]` | not set |

Without it, every import that is not stdlib, test tooling (`pytest`, `mock`,
`hypothesis`, ...) or a test-helper module counts as production, including
third-party libraries such as `numpy` or `requests`, and a limitation says so.
An invalid value makes the result `unavailable`.

### Detection rule

tsDetect reads the paired production class. Here only the test file is
supplied, so production calls are recognised from its imports:

- calls through an imported production name: `total(...)`, `cart.total(...)`,
  `Cart.from_dict(...)`;
- method calls on an object built from an imported production class:
  `Cart().add()`, `c = Cart(); c.add()`, `with Cart() as c: c.add()`, and
  `self.cart.add()` when `setUp`/`setUpClass`/`setup_method` or a class
  fixture assigns `self.cart = Cart()`;
- relative imports count when they resolve outside the test directory
  (`from ..shop.cart import total` in `tests/`), not inside it
  (`from .helpers import build`). Module paths with a `test`, `tests`,
  `(_)testing`, `conftest`, `test_*` or `*_test(s)` component are test code.

Not counted: constructors (tsDetect counts method calls, not object creation),
builtins, same-file helpers and classes, calls on `self`/`cls`, assertion calls,
calls in `setUp`/fixtures, names shadowed by a test parameter or local
assignment, and input builders. An input builder is a production call that is
an argument of another production call (`bincount(np.array([1]))`), or whose
result is assigned to a variable used only that way. Helpers are not followed
and there is no type inference.

A target called by at least 2 counted tests of one group is flagged (tsDetect:
"more than one"). Calls shared across groups are not compared. One finding per
group and target; the evidence is the first call to the target in each sharing
test (up to 8). Confidence:

- `medium` when at least 3 tests share the target and all of them make exactly
  the same production calls (they differ only in data, so one parametrized
  test could cover them);
- `low` otherwise.

Unconditionally skipped tests are not counted. `# noqa: TST-02` on a class line
suppresses the class, on a test's `def` line drops that test from the count, and
on a call line drops that call. The identity is `<group>:<target>`, e.g.
`CartTest:shop.cart.Cart.add` or `<module>:shop.cart.total`, with `#n` for a
redefined class.

The taxonomy cites an energy association for this smell (Kendall tau 0.449,
SRC-15). That evidence comes from JUnit/Maven projects and is not shown to
transfer to Python, so no measurements are emitted. Many small tests per
function are often good practice; a finding reports the pattern, not that the
tests are redundant.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst02/tst02-01-positive-input.json
```

## TST-08 — Sensitive Equality

Flags equality assertions that check an object through its `str()`/`repr()`
text, following tsDetect's Sensitive Equality ("test methods verify objects by
invoking the default toString() method of the object and comparing the output
against an specific string"; [tsDetect](https://testsmells.org/pages/testsmells.html),
after van Deursen et al., XP 2001), with Python's `str()`/`repr()` in place of
`toString()`. PyNose does not implement this smell. The check is static (`ast`
only), uses the same input, test recognition and per-file "nothing to flag"
note as TST-06, and needs no context settings.

### Detection rule

An `==`/`!=` assertion or an `assertEqual`/`assertNotEqual`-style method
(`assertEquals`, `assertMultiLineEqual`, `failUnlessEqual`, ...) in a test is
flagged when an operand is `str(x)`, `repr(x)`, `x.__str__()` or
`x.__repr__()` and either:

- the other operand is literal expected text: a string literal, an f-string,
  `"..." + x` / `"..." % x`, or a local name bound once in the test to one of
  those (`expected = "<Cart: 2 items>"`); or
- the other operand is also a representation (`repr(a) == repr(b)`), which
  compares two objects by their text instead of by equality.

tsDetect counts `toString` anywhere in an assertion's arguments, including the
failure message. This rule is narrower. These are not flagged:

- tests whose qualified name, module or directory has a word about the text
  itself: `str`, `repr`, `string*`, `text`, `unicode`, `representation*`,
  `display`, `print*`, `pprint`, `render*`, `format*`, `serializ*`, `dump*`,
  `pretty`, `message*`, `descri*` (`test_repr`, `TestStr`, `test_repr.py`,
  `tests/str/`). Words are matched whole, so `test_stream` is not exempt;
- exception and warning messages: `str(cm.exception)`, `str(excinfo.value)`,
  names bound by `except ... as` or by `with ...raises/warns/catch_warnings(...)
  as`, and exception-like names (`err`, `exc_value`, `last_error`,
  `recwarn[0].message`);
- `str()` compared with a non-literal value (`state == str(expected)`), which
  usually converts an expected value for an actual value that is already text;
- `str()` of a literal or of `len`/`int`/`round`/... (a number), `str(b, enc)`
  decoding, and representations used only as a failure message;
- `in`/`assertIn`/`assertRegex` checks, which tolerate formatting changes, and
  chained comparisons;
- `# noqa: TST-08`.

Confidence:

- `medium`: `repr()`/`__repr__()` text against literal text, or two compared
  representations. `repr` is a debugging aid, so a test that is not about it
  depends on incidental formatting;
- `low`: `str()` against literal text. For URLs, paths and rich text the string
  form is often the intended API.

The identity is `<qualified test>:<assertion callee>`, with `#n` for repeats.
No measurements are emitted. The taxonomy's energy association (Kendall tau
0.177, SRC-15) comes from JUnit/Maven projects and is not shown to transfer to
Python.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/tst08/tst08-01-positive-input.json
```

## INF-07 — Less efficient instance/processor family

Flags compute that a CloudFormation/SAM template declares on an older or less
efficient family when a more efficient equivalent exists. This v1 is a static
IaC proxy. It proves "declared on an older/x86 family that has a newer or
Graviton equivalent", not that the workload runs inefficiently, is compatible
with the successor, or that the successor is offered in the project's Region.
Templates are read as text and are never deployed, resolved or sent to AWS. It
uses the `textstatic.py` runner and `owner_d/miniyaml.py`.

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

| Resource | Property (identity `<LogicalId>:<property>`) |
| --- | --- |
| `AWS::EC2::Instance`, `AWS::AutoScaling::LaunchConfiguration` | `InstanceType` |
| `AWS::EC2::LaunchTemplate` | `LaunchTemplateData.InstanceType` |
| `AWS::AutoScaling::AutoScalingGroup` | `MixedInstancesPolicy.LaunchTemplate.Overrides` (each `InstanceType`) |
| `AWS::EKS::Nodegroup` | `InstanceTypes` |
| `AWS::Batch::ComputeEnvironment` | `ComputeResources.InstanceTypes` (a bare family such as `c4` counts) |
| `AWS::RDS::DBInstance` | `DBInstanceClass` |
| `AWS::ElastiCache::CacheCluster`, `AWS::ElastiCache::ReplicationGroup` | `CacheNodeType` |
| `AWS::OpenSearchService::Domain`, `AWS::Elasticsearch::Domain` | `ClusterConfig`/`ElasticsearchClusterConfig` `.InstanceType` and `.DedicatedMasterType` |
| `AWS::Lambda::Function`, `AWS::Serverless::Function` | `Architectures` (incl. SAM `Globals.Function`) |

The family table is small and explicit (`inf07.py`). Only these families are
judged:

| Tier | EC2 families → suggested successor | Confidence |
| --- | --- | --- |
| Older generation | t1/t2 → t3 or t4g; m1/m3/m4 → m7i or m7g; m2 → r7i or r7g; c1/c3/c4 → c7i or c7g; r3/r4 → r7i or r7g | medium (t1/t2: low) |
| x86 with a Graviton equivalent | t3/t3a → t4g; m5/m5a → m7g; m5d/m5ad → m7gd; c5/c5a → c7g; c5d → c7gd; c5n → c7gn; r5/r5a → r7g; r5d/r5ad → r7gd | low |

Managed services use their own tables:

- **RDS**: `db.t2`, `db.m3`/`m4`, `db.r3`/`r4` are older (successors
  `db.t3`/`db.m6i`/`db.r6i`, or `db.t4g`/`db.m7g`/`db.r7g`). `db.t3`/`m5`/`r5`
  are flagged as Graviton candidates only when `Engine` is a literal MySQL,
  MariaDB, PostgreSQL or Aurora engine. Oracle, SQL Server and Db2 have no
  Graviton classes, and a missing or intrinsic `Engine` is unknown.
- **ElastiCache**: `cache.t1`/`t2`, `m3`/`m4`, `c1`, `r3`/`r4` are older
  (successors `cache.t3`/`m5`/`r5`, or `cache.t4g`/`m7g`/`r7g`); `cache.t3`/`m5`/`r5`
  are Graviton candidates.
- **OpenSearch**: `t2`, `m3`/`m4`, `c4`, `r3`/`r4` (`*.search` or
  `*.elasticsearch`) are older (successors `t3`, `m5`, `c5`, `r5`, or
  `m7g`/`c7g`/`r7g`). `m5`/`c5`/`r5` are Graviton candidates only on
  `AWS::OpenSearchService::Domain` without an `Elasticsearch_*` `EngineVersion`.
  A `DedicatedMasterType` is ignored when `DedicatedMasterEnabled: false`.

Graviton candidates are `low` because AMIs, container images and native
dependencies need arm64 builds, and the template cannot show that. Older
generations are `medium`, because a newer x86 generation of the same family is
usually a drop-in move. t1/t2 are `low`, because they are often chosen for the
free tier or burst credits.

Lambda functions are flagged when `Architectures` is omitted (Lambda's default
is `x86_64`) or is the literal `[x86_64]`:

- `medium`: `Architectures` is omitted on a zip package with a Python, Node.js,
  Ruby, Java or .NET runtime. Such packages usually move unchanged, apart from
  native extensions.
- `low`: an explicit `x86_64`, which may be a deliberate compatibility choice;
  container images (`PackageType: Image`) and `provided.*` runtimes, which
  need an arm64 build; or an unknown runtime.

A `!Ref` to a template parameter is resolved to its literal `Default`. Such
findings are `low` because a deployment can override the default, and the
evidence is the `Default` line. Other intrinsics (`!If`, `!FindInMap`, `!Sub`,
`Fn::If` around `Properties`) and `Architectures` values given by `!Ref` are
not resolved and are not flagged. A list (node group, Batch, ASG overrides)
yields one finding that names every flagged entry; the evidence is the first
flagged entry. Otherwise the evidence is the property line, the
`Architectures` line(s), or the logical-ID..`Type` lines when `Architectures`
is omitted.

Legitimate exceptions (not flagged):

- `# noqa` / `# noqa: INF-07` on the cited line or in the comment lines
  directly above the resource (YAML only). Use it for licensing, certified
  AMIs, drivers or x86-only binaries.
- Lambda runtimes without an arm64 build (`go1.x`, `python3.7` and older,
  `nodejs10.x` and older, `java8`, `dotnetcore2.1`, `ruby2.5`, `provided`).
  Upgrading the runtime comes first.
- Lambda functions synthesized by the CDK framework: custom-resource
  providers, `LogRetention` and bucket-notification handlers.

The following are not evaluated. They are listed as limitations, never
reported clean:

- invalid JSON, or YAML outside the miniyaml subset
- YAML/JSON files without CloudFormation `Resources`, such as Kubernetes
  manifests (a `kubernetes.io/arch: amd64` nodeSelector is not judged)
- templates with a macro `Transform` other than
  `AWS::Serverless-2016-10-31`/`AWS::LanguageExtensions`, or with
  `Fn::Transform`/`AWS::Include` or `Fn::ForEach`
- Terraform/HCL (`.tf`), CDK source code (synthesize it first), Pulumi and
  Serverless Framework files

### Limitations

No inventory, utilisation or Compute Optimizer data is read. The taxonomy's
AWS Config / Compute Optimizer half needs a client read-only role and is
blocked on OQ-7. No measurements are emitted.

The check does not judge 6th/7th-generation x86, GPU, accelerated or
storage-optimised families, attribute-based `InstanceRequirements`, Spot
Fleet/EC2 Fleet, EMR, SageMaker, Batch `optimal`, or whether a function is
used with Lambda@Edge (which requires `x86_64`). It does not check whether a
successor is offered in the project's Region.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/inf07/inf07-01-positive-input.json
```

## OBS-10 — Filtering after ingestion

Flags telemetry that is shipped in full to a later stage which then drops part
of it, while the earlier stage declares no filter. Dropping data after it has
been sent or ingested does not save what was already paid for, such as
serialization, network and ingest (vendor evidence SRC-18). OBS-10 is an OQ-8
judgement row. Findings are candidates for reviewer confirmation, and the check
is pending the OQ-8 decision on what counts as filtering after ingestion.

The check is static. Collector configs are read with `owner_d/miniyaml.py` and
the shared `owner_d/otelconfig.py`. Terraform is read with a small local HCL
block reader in `obs10.py` that evaluates no expressions. Nothing is run,
rendered, planned or resolved. The check proves "dropped downstream, nothing
dropped upstream", not ingested volume, so no measurements are emitted.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. The payload is judged **as a whole**: gateways, Agent rules and archives
are collected from every source first. Supported files:

- **OpenTelemetry Collector** `.yaml`/`.yml` (also ADOT): a plain config, an
  `OpenTelemetryCollector` resource or a `ConfigMap` `|` entry, as in OBS-09.
- **Terraform** `.tf` with `datadog_logs_index`, `datadog_logs_archive` or
  `datadog_logs_metric` resources.
- **Any text file with a Datadog Agent `exclude_at_match` rule**, such as
  `datadog.yaml`, `conf.d`, Kubernetes annotations or
  `DD_LOGS_CONFIG_PROCESSING_RULES`. These files only supply source-side context
  and never produce findings.

No context settings are required.

### Detection rule

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `[<label>]pipeline/<id>:exporter/<exporter>:filtered-downstream` | A `traces`/`metrics`/`logs` pipeline exports through an OTLP exporter to a gateway collector in the payload, and every pipeline of that gateway for the same signal that is fed by `otlp` and exports somewhere real drops data with a `filter` processor. The agent pipeline has no `filter`, `probabilistic_sampler` or `tail_sampling` processor, and none of its receivers has a stanza `filter` operator. | low |
| `datadog_logs_index.<name>:exclusion_filter/<filter name or #n>` | An `exclusion_filter` has `is_enabled = true` and `filter { sample_rate = 1.0 }`. Excluded logs are still ingested and billed for ingestion; they are only not indexed. | medium; low when the payload declares a `datadog_logs_metric` (it can still use excluded logs) |

The **gateway** is matched by the first DNS label of the exporter endpoint
(`<signal>_endpoint`, else `endpoint`):

- `OpenTelemetryCollector/<name>` is reachable as `<name>`, `<name>-collector`
  and `<name>-collector-headless`, the operator Service names.
- A ConfigMap name or a plain file stem is reachable as itself, with a
  `-config`/`-conf`/`-configmap`/`-cm`/`-cfg` suffix stripped, and with
  `-collector` appended.
- For generic stems such as `config.yaml`, the parent directory name also
  matches.

When the gateway filter runs after `k8sattributes`, `resourcedetection`,
`resource`, `attributes`, `transform` or `groupbyattrs`, the summary asks the
reviewer to confirm that its conditions do not need attributes that only the
gateway adds. Evidence is the agent pipeline block or the `exclusion_filter`
block. The summary names the gateway file, the pipelines and the filters.

Not flagged:

- gateways that keep the full stream: another same-signal pipeline fed by
  `otlp` with no `filter`, for example an audit, archive or SIEM pipeline.
  Pipelines that export only to `debug`/`logging`/`nop` do not count.
- gateways that cannot be judged: a filter that is undefined, empty
  (`error_mode` only) or has unresolved `${...}` values; unresolved pipeline
  lists; a pipeline that exports to a connector
- endpoints that name no collector config in the payload, including vendor,
  loopback and unresolved endpoints, and pipelines fed by connectors
- Datadog exclusions with `sample_rate < 1` (OBS-08 downsampling), or with
  `is_enabled = false`, missing or set by a variable
- every Datadog exclusion when the payload declares an Agent `exclude_at_match`
  rule (source-side filtering; queries are not matched, which is conservative)
  or a `datadog_logs_archive` (excluded logs are still archived for audit). A
  limitation names the file and the count of filters not flagged.
- `# noqa` / `# noqa: OBS-10` on the pipeline key or `exclusion_filter` line,
  or directly above it

The following are not evaluated. They are listed as limitations, never
reported clean:

- broken YAML, unbalanced HCL blocks, brackets, heredocs or comments
- YAML with `exporters:`/`pipelines:` but no collector config, and Helm values
- context-only files when the payload has no collector config and no index
  exclusion to judge
- development/test files (the OBS-09 path tokens)

Other files are out of scope (`Unsupported`), so the scan worker passes only
these inputs to the check.

### Limitations

Not visible:

- gateways in other repositories, or behind Services, ingresses or load
  balancers with other names
- whether an Agent rule covers the same logs as an index exclusion
- Datadog index JSON/API exports, Observability Pipelines, Vector, Fluent Bit,
  Fluentd and Splunk stages

The collector signal is a network and serialization hop inside the
customer's estate, so it is `low`. A gateway is also the documented place for
some filtering, for example after enrichment.

Out of scope for v1: CloudWatch Logs subscription filters with an empty
`FilterPattern`. They prove forwarding, not a later drop. CloudWatch metric
filters and Logs Insights queries are also out of scope: they read ingested
data but do not show that the rest was unneeded.

Kept distinct from OBS-08 (downsampling), OBS-09 (batching/compression), OBS-11
(duplicate lines), OBS-13 (probe spans, where a gateway filter counts as
handling) and OBS-14 (non-production ingestion).

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs10/obs10-01-positive-input.json
```

## LLM-08 — Over-sized model for simple tasks (static proxy: fixed top-tier model, no routing)

Flags LLM API calls in Python source that hard-code a top-tier model ID for
every request, with no model selection visible in the file, where the call
looks like a simple task. This v1 is a static proxy: it proves "a fixed large
model at this call site", not that a smaller model would give acceptable
quality (the taxonomy's "quality trade-off" exception), and not any cost. It
emits no measurements. Confidence is `low` or `medium`, never `high`.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only) and the context settings below.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_simple_output_tokens` | Largest output cap that counts as a short answer | `256` |
| `max_simple_prompt_tokens` | Largest estimated static prompt that can count as a simple instruction | `500` |

A label, a yes/no or one extracted field fits well under 256 output tokens.
Anthropic recommends its smallest models for classification and extraction
([choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model)),
and AWS recommends matching the model to the task
([GENCOST01-BP01](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost01-bp01.html)).
Longer static instructions are treated as a real task even with
classification wording. Both values are judgment calls, so they are required
and exported as `REFERENCE_SETTINGS`. Missing or invalid settings make the
result `unavailable`.

### Detection rule

A call is flagged when all of these hold:

1. **Fixed top-tier model.** The model argument (`model`, Bedrock `modelId`)
   resolves to one string literal, inline or through a name bound once, that
   matches this table:

   | Family | Pattern | Smaller tiers named in the summary | Source |
   | --- | --- | --- | --- |
   | Claude Opus / Fable / Mythos | `claude-opus-*`, `claude-fable-*`, `claude-mythos-*`, `claude-3-opus*`, with any `anthropic.`/`us.`/`global.` prefix or Vertex `@` | Haiku, Sonnet | [models overview](https://platform.claude.com/docs/en/about-claude/models/overview) |
   | Amazon Nova Premier | `amazon.nova-premier*` | Nova Micro, Lite, Pro | [Nova user guide](https://docs.aws.amazon.com/nova/latest/userguide/what-is-nova.html) |
   | OpenAI pro | `gpt-5-pro`, `gpt-5.x-pro`, `o1-pro`, `o3-pro` (optionally dated) | base or mini/nano model | [OpenAI models](https://developers.openai.com/api/docs/models) |

   Sonnet, Haiku, Nova Pro/Lite/Micro, plain `gpt-*`, `o*` and `*-mini`/`*-nano`
   models are never flagged.
2. **Looks like a simple task**, at least one of:
   - an output cap (`max_tokens`, `max_completion_tokens`,
     `max_output_tokens`, `inferenceConfig.maxTokens`, invoke body
     `max_tokens`/`max_new_tokens`/`max_gen_len`/...) of at most
     `max_simple_output_tokens`;
   - classification / extraction / yes-no wording (`classify`, `categorize`,
     `sentiment`, `yes or no`, `true or false`, `extract the`, `one word`,
     `which category`, ...) in the static prompt text (`system`,
     `instructions`, `messages`, `input`, `prompt`), whose static part is at
     most `max_simple_prompt_tokens` (4 characters per token).

   `medium` when both signals hold, `low` with one or for an OpenAI-compatible
   chain call on an unknown client.
3. **No sign of routing or tuning.** Not flagged:
   - a model from a parameter (also with a default), the environment, a
     settings object, a function call, a conditional or a name bound more than
     once;
   - files that contain a smaller model ID of a known family (for example
     `claude-haiku-4-5`, `amazon.nova-lite-v1:0`, `gpt-5-mini`) or mention a
     router (`prompt-router`, `ModelRouter`, `litellm.Router`, `RouteLLM`,
     `route_model`, `select_model`, ...) anywhere: the file already picks
     models per task;
   - calls with tools (`tools`, `functions`, `toolConfig`) or with explicit
     reasoning/effort settings (`thinking`, `reasoning`, `reasoning_effort`,
     `output_config`, reasoning fields in `additionalModelRequestFields`):
     agentic work, or cost already tuned on the large model;
   - unresolvable `**kwargs`, `extra_body`, Bedrock `promptVariables` and
     `invoke_model` bodies that are not `json.dumps(<static dict>)`;
   - other SDK clients (`Groq()`, ...).

`# noqa` or `# noqa: LLM-08` records a deliberate choice. The identity is
`<qualified function>:<provider>.<api>` with `#2` for repeats; the model ID is
not part of it. Missing, non-Python or unparseable files are left out of
`evaluated_scope`, never reported clean.

The taxonomy maps LLM-08 to Logs Insights on client logs (usage by task and
model). That route is not implemented in v1. It needs client logs that carry
a task label next to the model ID and token counts; Bedrock model invocation
logs have the model and tokens but no task, and the log analyzer may only
query `/aws/lambda/owner-d-*` groups. It is a follow-up
(`LOGS_INSIGHTS_QUERY` and `normalize_logs_insights` in this module), not
part of the static check.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm08/llm08-01-positive-input.json
```

## LLM-06 — Parallel calls defeating the prompt cache (static proxy)

Flags Python code that launches several LLM calls at the same time over the
same large, static prompt prefix that carries a cache breakpoint, with no
call sending that prefix first. A cache entry becomes readable only after the
first response begins
([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
So N concurrent requests over a cold prefix each pay a cache write and none
reads the cache. The taxonomy's fix is "warm cache with one call, then fan
out".

This is the complement of LLM-01, which flags a large prefix with *no* cache
marker. The taxonomy detects this check from cache hit analysis in client
logs. This v1 is a static proxy: it proves "concurrent fan-out over a shared
cached prefix, with no warm-up in this function", not measured cache misses.
It emits no token counts.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

A finding needs all four conditions below.

1. **Fan-out.** One of these launches 2 or more concurrent units, or an
   unknown number (a comprehension or loop over a non-literal iterable). A
   literal list or `range(n)` with fewer than 2 items is not a fan-out.

   | Fan-out | Units |
   | --- | --- |
   | `asyncio.gather(...)` | explicit arguments, `*[comprehension]`, or `*tasks` where `tasks` is a comprehension or is built with `.append` |
   | `asyncio.wait(...)`, `asyncio.as_completed(...)` | the same sequences |
   | `asyncio.TaskGroup` / `anyio.create_task_group()` / `trio.open_nursery()` | `create_task` / `start_soon` in a loop, or repeated |
   | `ThreadPoolExecutor` / `ProcessPoolExecutor` | `.map(fn, items)`, `.submit(fn, ...)` in a loop or comprehension; not with `max_workers=1` |

   `asyncio.create_task`/`ensure_future` wrappers are unwrapped.
   `asyncio.to_thread(fn, ...)` and `loop.run_in_executor(ex, fn, ...)` units
   run `fn` in a thread.

2. **Same-file LLM call, really concurrent.** A unit is either a direct LLM
   call, or a call or reference to a function in the same file: a plain name,
   a `self.`/`cls.` method, `functools.partial` or a lambda. Helpers are
   followed up to 3 levels deep. Asyncio units must reach an **awaited** call
   through `async def`s; a sync client inside a coroutine blocks the event
   loop, so those calls do not overlap. Thread units must reach a **sync**
   call. LLM calls are recognized by `owner_d/llmcalls.py`: Anthropic SDK
   `messages.create/stream/parse` (also `beta.`), Bedrock
   `converse`/`converse_stream`, and `invoke_model` with
   `body=json.dumps(<static dict>)`.

3. **Large cached prefix.** The call has an **explicit** breakpoint inside
   its static prefix: a `cache_control` block (Anthropic, Bedrock InvokeModel
   Claude) or a `cachePoint` block (Bedrock Converse, Nova). The prefix is read
   in cache order (`tools` -> `system` -> `messages`) and stops at the first
   dynamic piece. Breakpoints after that piece do not count.
   - **Size.** The static text up to the last such breakpoint is estimated at
     4 characters per token. Tool definitions are counted by compact JSON
     length, without the marker itself.
   - **Threshold.** The estimate must reach the model's minimum cacheable
     length. LLM-01's table is reused (`claude_minimum`; 1,024 for Nova, whose
     tool definitions do not count).
   - **Unknown model.** An unknown Anthropic model uses 4,096. An unknown
     Bedrock model is not flagged.

4. **No warm-up.** No call earlier in the fan-out's function sends the same
   model and static prefix. An earlier call to the same target counts (e.g.
   `first = await answer(qs[0])` before
   `gather(*(answer(q) for q in qs[1:]))`), and so does an earlier fan-out.

Not flagged:
- sequential loops;
- calls with no cache marker (LLM-01's territory);
- calls with only top-level automatic `cache_control`. Its breakpoint sits on
  the last, per-item block, so siblings share no entry;
- cached prefixes below the minimum;
- dynamic or unresolvable prefixes (prompts read from files or other modules),
  `**kwargs`, `extra_body`, Bedrock Prompt management;
- OpenAI, whose caching is automatic and has no marker;
- fan-out targets defined in other modules;
- any file that pre-warms (`max_tokens=0` / `maxTokens: 0` anywhere, or a
  function or call whose name contains `warm`) or uses `Semaphore(1)`.

`# noqa` or `# noqa: LLM-06` on the fan-out line or on the LLM call line
suppresses a finding.

Confidence:
- `low` by default. Other traffic within the cache TTL may already have
  warmed the entry, and static analysis cannot see traffic timing.
- `medium` for Anthropic fan-outs at a script entry point (module level or a
  function named `main`), which usually starts cold. Bedrock stays `low`.

The identity is
`<fan-out qualname>:<fan-out api>-><LLM call qualname>:<provider>.<api>`, e.g.
`answer_all:asyncio.gather->answer:anthropic.messages.create`. A repeat gets
`#2`. The evidence is the fan-out expression. Missing, non-Python or
unparseable files are left out of `evaluated_scope`, never reported clean.

Not covered: LangChain/LiteLLM/agent frameworks, `multiprocessing.Pool`,
fan-outs across modules or processes, and warm-ups done by a caller in
another function or file.

**Telemetry follow-up.** The taxonomy maps LLM-06 to CloudWatch Logs Insights
on client usage logs (`owner-d-log-analyzer`): concurrent requests with the
same prefix that each report `cache_creation_input_tokens` /
`cacheWriteInputTokens` and no cache read. That route needs a documented
client log schema (request time, prefix hash, cache write/read tokens) and is
not implemented in v1. `owner_d/llm06.py` exports no `LOGS_INSIGHTS_QUERY` or
normalizer.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm06/llm06-01-positive-input.json
```

## LLM-14 — Unbounded agent memory / history reprocessed every turn (static proxy)

Flags LLM calls in Python source that are sent a whole conversation history
which grows on every turn with no visible bound, so each turn reprocesses all
earlier turns. AWS
[AGENTSUS02-BP01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp01.html)
lists "reprocessing complete historical context on every turn instead of
pulling only the relevant slice" as an anti-pattern. This v1 is a static
proxy: it proves that the history at this call site grows without a bound, not
how many tokens are re-sent or how long conversations last, and it emits no
measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

LLM calls are recognised by `owner_d/llmcalls.py`. The history is the
conversation argument: `messages` for Anthropic `messages.*`, OpenAI chat
completions and Bedrock `converse`/`converse_stream`, `input` for the OpenAI
Responses API, and `messages` in a static `invoke_model` body
(`json.dumps(<dict>)`). It must be sent whole: `h`, `h + [...]`,
`[system, *h]`, `list(h)`, `h.copy()` or `h[:]`.

| Rule | Flagged when | Confidence |
| --- | --- | --- |
| L — loop | The call is in a `while`/`for` loop. `h` is a local or module name created once before that loop (`[]`, a list literal or comprehension, `list()`, `deque()` without `maxlen`, or a parameter) and grows inside the loop (`append`/`extend`/`insert`/`+=`/`h = h + [...]`/`h = [*h, ...]`) | `medium`; `low` for a `for` loop over data, a parameter or an unknown OpenAI-compatible client |
| P — persistent | `self.<attr>` is created once (in `__init__`/`__post_init__`, as a class attribute or `field(default_factory=list)`) and grows in another method; or a module-level list grows inside a function. Every request then re-sends all earlier requests' turns | `medium` (`low` for an unknown OpenAI-compatible client) |

Not flagged (bounded, or data flow this check does not follow):

- slices and summaries as the argument (`h[-10:]`, `[system] + h[-6:]`,
  `summarise(h)`);
- `deque(maxlen=...)` and initial values that are not plain lists (custom
  memory classes, `load_history()`);
- any trimming or rebinding of `h`: `pop`/`popleft`/`remove`/`clear`,
  `del h[...]`, item or slice assignment, `h = h[-n:]`, `h = []`, a
  `reset()` method that rebinds `self.h`, or a subclass in the same file that
  trims it;
- `len(h)` in a comparison (`if len(h) > 40: break`), and `h` passed to any
  function other than the LLM call, logging and plain reads (`trim(h)`,
  `count_tokens(h)`), which might bound it;
- aliases (`other = h`) and use in a nested function or lambda;
- loops with an explicit iteration budget: `range(...)`, `itertools.islice`,
  literal collections, `while n < limit`. LLM-10 checks iteration budgets in
  traces;
- server-side state or truncation (`previous_response_id`, `conversation`,
  `truncation`, `context_management`), `extra_body` and `**kwargs` that are
  not statically known;
- single-turn scripts and histories created per call (no loop, no
  persistence);
- histories on other objects (`state.messages`, `agent.memory`),
  LangChain/LangGraph/LlamaIndex/LiteLLM/Agents SDK memory, and other SDK
  clients (`Groq()`, ...).

`# noqa` or `# noqa: LLM-14` on the call suppresses it.

LLM-04 flags several sequential call sites in one function that re-send the
same history; LLM-14 flags one call site that re-sends a growing history on
every turn. A loop with two call sites can appear in both. A tool-use agent
loop is flagged too: each step needs the latest tool call and result, not
every earlier turn.

The identity is `<qualified function>:<provider>.<api>:<history>`, e.g.
`chat:anthropic.messages.create:messages` or
`Assistant.ask:openai.chat.completions.create:self.history`; a repeat gets
`#2`. Missing, non-Python or unparseable files are left out of
`evaluated_scope`, never reported clean.

Not evaluated: trimming done by a caller or in another file, and object
lifetimes (an instance created per conversation is still flagged, because the
whole conversation is re-sent every turn). The taxonomy's telemetry route
(CloudWatch Logs Insights through `owner-d-log-analyzer`: input tokens per
turn growing within a conversation) is a follow-up; this module defines no
`LOGS_INSIGHTS_QUERY` yet.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm14/llm14-01-positive-input.json
```

## OBS-15 — Duplicate/overlapping observability tools (Frankenstack)

Flags one package or one pod/task that ships two or more observability tools
doing the same job, for example Datadog's `ddtrace` next to `newrelic`, or a
Datadog Agent sidecar next to an OpenTelemetry collector sidecar. The check is
static. Files are read as text and are never installed, executed or resolved.
v1 is a proxy: it proves "overlapping tools declared together". It does not
prove that both tools are active, how much data each ingests or what they
cost, so it emits no measurements.

The taxonomy's runtime half (client account inventory through a read-only
role: AWS Config, Resource Explorer) is blocked on OQ-7 and is not part of v1.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item. Supported files:

- **Python**: `requirements*.txt`/`.in` and `requirements/*.txt`,
  `pyproject.toml` (`[project].dependencies`, `[tool.poetry.dependencies]`
  without `optional = true`) and `Pipfile` (`[packages]`)
- **npm**: `package.json` `dependencies`
- **Go**: `go.mod` direct `require`s
- **Sidecars**: Kubernetes pod templates (`containers`, plus native sidecar
  `initContainers` with `restartPolicy: Always`) and ECS task definitions
  (task-definition JSON, `describe-task-definition` output, CloudFormation
  `AWS::ECS::TaskDefinition` in JSON or YAML). The pod/task readers come from
  INF-08.

No context settings are required.

Every dependency manifest is in scope, and a manifest without overlap is
reported clean. YAML/JSON files are in scope only if they mention a known agent
image.

### Detection rule

Tools come from an explicit table in `owner_d/obs15.py` (`PACKAGES`, `AGENTS`).
A finding needs **two or more distinct tools in the same category**. Packages of
one tool (`newrelic` and `@newrelic/native-metrics`) count once.

| Category | Examples of distinct tools |
| --- | --- |
| `tracing-apm` | Datadog APM (`ddtrace`, `dd-trace`, `dd-trace-go`), New Relic, Elastic APM, AWS X-Ray SDK, OpenTelemetry, AppDynamics, Instana, Dynatrace, Scout, Honeycomb Beeline, SkyWalking |
| `error-tracking` | Sentry, Rollbar, Bugsnag, Airbrake, Honeybadger, Raygun, TrackJS |
| `metrics` | Prometheus client, DogStatsD, StatsD clients, CloudWatch EMF |
| `log-shipping` | in-process shippers: watchtower/winston-cloudwatch, Logstash, Logz.io, Google Cloud Logging, Splunk HEC, Loki, Better Stack, Datadog log transports |

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `<category>` | One manifest declares two or more tools of the category | medium for `tracing-apm` and `error-tracking`, low for `metrics` and `log-shipping` |
| `<category>` | The overlap only appears together with a sibling manifest in the same directory (see below) | low |
| `<Kind>/[<ns>/]<name>:sidecar-agents`, `task/<family>:sidecar-agents`, `<LogicalId>:sidecar-agents` | Two or more agent containers in one pod/task collect the same signal | low |

A repeated identity gets `#n`. Evidence is every declaration line (or agent
`image:` line) of the overlapping tools in the finding's own file.

**Directory grouping.** The canonical manifests `requirements.txt`,
`requirements.in`, `pyproject.toml` and `Pipfile` in one directory, and the
files in a `requirements/` directory, are read as one Python package. Contract
v1 requires evidence from the finding's own scope, so each file gets its own
finding. That finding cites only its own lines, and its summary names the tool
declared in the sibling file. The same tool in two siblings
(`ddtrace` in both) is not an overlap. Variants side by side, such as
`requirements.orders.txt` and `requirements.billing.txt`, often belong to
different deployables, so they are not correlated. If a sibling cannot be
parsed, the limitation says so.

**Sidecar signals** follow each agent's default configuration:

| Agent | Signals |
| --- | --- |
| Datadog Agent | traces, metrics; logs only with `DD_LOGS_ENABLED=true`; no traces with `DD_APM_ENABLED=false` |
| OpenTelemetry collector (contrib, k8s, ADOT, Splunk), Grafana Alloy/Agent | traces, metrics (pipelines live in a separate config; collector logs pipelines are often fed by the log router in the same pod) |
| AWS X-Ray daemon | traces |
| CloudWatch agent, New Relic infrastructure agent | metrics |
| Fluent Bit / aws-for-fluent-bit, Fluentd, Vector, Filebeat, Promtail | logs |
| Elastic Agent | logs, metrics |

The AWS FireLens pattern (Fluent Bit for logs next to ADOT for traces and
metrics) and Container Insights (CloudWatch agent for metrics next to Fluent
Bit for logs) do not overlap. X-Ray daemon next to ADOT does overlap: ADOT's
ECS default config already receives X-Ray segments.

### OpenTelemetry bridges

Bridges are legitimate and are not counted as a second stack:

- **API and per-library instrumentation.** `opentelemetry-api`,
  `@opentelemetry/api` and `go.opentelemetry.io/otel` are not counted, and
  neither are per-library instrumentations (`opentelemetry-instrumentation-*`,
  `@opentelemetry/instrumentation-*`,
  `go.opentelemetry.io/contrib/instrumentation/*`). They only call the API, and
  vendor tracers (ddtrace, dd-trace, dd-trace-go, Elastic APM) implement the API
  as a bridge.
- **What counts as OpenTelemetry.** Only an SDK, distro, exporter or zero-code
  bundle counts: `opentelemetry-sdk`/`-distro`/`-exporter-*`,
  `@opentelemetry/sdk-*`/`auto-instrumentations-*`/`exporter-*`,
  `go.opentelemetry.io/otel/sdk`/`exporters`.
- **Vendor OpenTelemetry distros** count as the same tool, OpenTelemetry:
  ADOT, Splunk, Elastic EDOT, Honeycomb and Azure Monitor.
- **Datadog OTLP ingest.** A Datadog Agent with OTLP ingestion enabled
  (`DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_GRPC_ENDPOINT` or `..._HTTP_ENDPOINT`) next
  to an OpenTelemetry collector is a bridge: the collector forwards to the
  agent.

Not flagged:

- development/test dependencies: `devDependencies`, `peerDependencies`,
  Pipfile `[dev-packages]`, optional extras, Poetry groups, PEP 735
  `dependency-groups`, `tool.uv.dev-dependencies` and requirement files whose
  name has a `dev`, `test`, `lint`, `docs`, `ci`, `typing`, `local`, `e2e`,
  `bench` or `debug` token
- Go modules marked `// indirect`, and `-r`/`-c`/`-e`/URL requirement lines
- regular init containers, which exit before the app starts
- declarations or agent images marked `# noqa` / `# noqa: OBS-15` (Go:
  `// noqa: OBS-15`) on the line or on the comment lines directly above it. A
  marked declaration does not count toward an overlap. `package.json` has no
  comments, so a top-level `"//"` string that contains `noqa: OBS-15` marks the
  whole file.

The following are not evaluated. They are listed as limitations or as
declined files, never reported clean:

- test, example and docs paths (directory tokens `test(s)`, `testing`,
  `testdata`, `e2e`, `example(s)`, `sample(s)`, `doc(s)`, `fixture(s)`,
  `mock(s)`, `benchmark(s)`)
- vendored or tooling directories (`node_modules`, `vendor`, `third_party`,
  `site-packages`, `.venv`, `venv`, `.github`)
- YAML/JSON without a pod template or task definition (Helm values)
- invalid JSON/TOML, Helm/Go templates, YAML outside the `miniyaml` subset
- a `go.mod` without a `module` directive or with an unterminated block
- a TOML declaration that cannot be located (dotted keys)

### Limitations

- No measurements: ingest volume, licensing and cost need backend or billing
  data.
- Not seen:
  - lockfiles and `-r` includes
  - Maven/Gradle and .NET/Ruby (Java agents are usually attached with
    `-javaagent`, not declared)
  - agents attached at runtime without a dependency
  - Compose stacks
  - agents deployed as separate DaemonSets or in other files
  - agent configs outside the file
- When this is not necessarily wasteful (taxonomy): migrations and
  organizational or vendor constraints. Mark those with `noqa`.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs15/obs15-01-positive-input.json
```

## LLM-16 — No streaming (full response buffered in memory)

Flags LLM API calls in Python request handlers that do not stream, so the
whole reply is generated and held in memory before anything reaches the
client. The taxonomy detects this by memory profiling. This v1 is a static
proxy: it proves that a handler makes a non-streaming call whose output is not
capped small. It does not measure response length, peak memory or latency, and
emits no measurements. AWS lists "streaming responses are used for
user-facing interactions to reduce memory footprint and improve
time-to-first-token" as a desired outcome
([AGENTSUS02-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html)).

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only) and the context setting below.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_buffered_output_tokens` | Largest output cap that may still be returned in one piece | `256` |

No provider documents a size below which streaming stops paying off, so the
value is required. At about 4 characters per token, 256 tokens is about 1 KB of
text. Missing or invalid settings make the result `unavailable`.

### Detection rule

A call is checked when it runs while serving a request:

- inside a function decorated as a route of a FastAPI, Starlette, Flask, Quart,
  Sanic, Litestar, aiohttp, Chalice or Django Ninja app/router created in the
  same file (`@app.post`, `@router.get`, `@bp.route`, ...), a Litestar `@post`
  or DRF `@api_view`, or an AgentCore `@app.entrypoint`; or
- in a module-level function of the same file that such a handler calls
  directly (one hop, confidence `low`).

It is flagged when it does not stream and its output cap is absent or strictly
greater than `max_buffered_output_tokens`:

| Call | Output cap | Streaming form |
| --- | --- | --- |
| Anthropic SDK `messages.create` (also `beta.`) | `max_tokens` | `messages.stream()` or `stream=True` |
| OpenAI `chat.completions.create`, `responses.create`, v0 `ChatCompletion.create` | `max_completion_tokens`/`max_tokens`, `max_output_tokens` | `stream=True` |
| Bedrock `converse` | `inferenceConfig.maxTokens` | `converse_stream` |
| Bedrock `invoke_model` with `body=json.dumps(<static dict with messages>)` | `max_tokens` (or `inferenceConfig.max_new_tokens`) | `invoke_model_with_response_stream` |

Not flagged: calls that stream (`stream=True` or a non-literal `stream`,
`.stream()`, `with_streaming_response`, `converse_stream`,
`invoke_model_with_response_stream`); structured outputs (`.parse`,
`response_format`, `output_format`, `output_config.format`, `text.format`,
Bedrock `outputConfig`, instructor `response_model`), forced tool calls
(`tool_choice` other than `auto`/`none`) and replies parsed as JSON later in
the same function (`json.loads`, `model_validate_json`, ...; not the raw
`body.read()` of `invoke_model`), whose reply must be complete before use;
output caps, request
bodies or `**kwargs` that are not statically known, `extra_body`, Bedrock
Prompt management; embedding or prompt-style `invoke_model` bodies; legacy
`completions.create` (16-token default). Calls outside handlers (scripts,
batch jobs, workers, Lambda handlers, startup hooks), handlers whose app or
router is imported from another module and helpers reached through more than
one call are not checked. `# noqa` or `# noqa: LLM-16` suppresses a call.
Confidence is `medium` in the handler itself and `low` for one-hop helpers
and clients known only from the `chat.completions` chain.

The identity is `<qualified function>:<provider>.<api>` with `#2` for repeats.
Missing, non-Python or unparseable files are left out of `evaluated_scope`,
never reported clean.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm16/llm16-01-positive-input.json
```
## LLM-03 — Bloated system prompts / redundant instructions (static proxy)

Flags LLM API calls in Python source whose statically resolvable system
prompt is larger than the configured token budget, or repeats the same
instruction. This v1 is a static proxy: it proves the size or the repetition
of the literal prompt text, not that a shorter prompt would behave the same
or what the prompt costs. It emits no token counts as measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only) and the context settings below.

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `max_system_prompt_tokens` | Largest estimated size of the literal system prompt | `4000` |
| `min_repeated_instruction_chars` | Shortest normalised sentence that counts as a repeated instruction | `40` |

The thresholds are judgment calls, so they are required; missing or invalid
settings make the result `unavailable`. `llm03.REFERENCE_SETTINGS` holds the
reference values. No provider documents a size above which a system prompt is
bloated: 4,000 tokens (about 16,000 characters) favours precision and sits at
the largest minimum cacheable prompt documented by Anthropic. 40 characters
is about 7–8 words; shorter repeats ("Be concise.") are often deliberate
emphasis. AWS [AGENTCOST02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp02.html)
names "verbose system prompts with lengthy persona descriptions and redundant
explanations" as an anti-pattern, and
[GENCOST03-BP01](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost03-bp01.html)
asks for prompts "as short as possible while meeting performance
requirements".

### Detection rule

| Call | System prompt |
| --- | --- |
| Anthropic SDK `messages.create/stream/parse` (also `beta.`) | `system` (string or text blocks) |
| OpenAI `chat.completions.*` | `messages` entries with role `system` or `developer` |
| OpenAI `responses.*` | `instructions`, and `input` entries with role `system` or `developer` |
| Bedrock `converse` / `converse_stream` | `system` text blocks |
| Bedrock `invoke_model` with `body=json.dumps(<static dict>)` | `system` |

Text is resolved from literals, single-assignment names, `+`, f-strings,
`.format`/`%` templates, `str.join`, `.strip()`, `textwrap.dedent` and
`inspect.cleandoc`. Each dynamic part (f-string or format field, Jinja
`{{ }}`/`{% %}`, unknown name or call) becomes a placeholder, so the literal
parts of templates are still read. A finding is emitted when:

- **Size:** the literal characters, estimated at 4 characters per token, are
  strictly greater than `max_system_prompt_tokens`. Placeholders are not
  counted, so the estimate is a lower bound. Files that configure prompt
  caching (`cache_control`, `cachePoint`, `cache_point`, `prompt_cache_key`)
  are exempt from this rule.
- **Redundancy:** a sentence occurs more than once within one stretch of the
  prompt that has no placeholder, and its normalised form (no bullets,
  numbering, Markdown emphasis or lead-ins such as `IMPORTANT:`, `Note:`,
  `Remember,`, `Again,`; all-caps words and the first letter in lower case,
  other capitals kept, so a Title Case module list does not match a topic
  list) has at least
  `min_repeated_instruction_chars` characters. Prompt caching does not exempt
  this rule.

Not counted as repeats: few-shot examples in tags whose names contain
`example`, `sample`, `shot` or `demo`, and everything after an `Examples` /
`Few-shot` / `Sample` heading up to the next Markdown heading; lines starting
with `Example`, `For example` or `e.g.` and the lines indented under them;
labelled lines (`Input:`, `Output:`, `Q:`, `User:`, `Assistant:`,
`Thought:`, ...); fenced code blocks, `"key":` lines and sentences with `{`,
`}` or `|`; sentences that contain a
placeholder; and copies separated by a placeholder (OpenAI recommends placing
instructions before and after long context). Not flagged: prompts that are
parameters, attributes, read from files or built by calls; `**kwargs` and
`extra_body`; other SDK clients. `# noqa` or `# noqa: LLM-03` suppresses a
call.

One finding per call, with both reasons when both apply; the summary quotes
the first repeated sentence. Confidence is `medium` for a repeat on a known
client and `low` for size-only findings (trimming can change behaviour) and
for clients known only from the `chat.completions` chain. The identity is
`<qualified function>:<provider>.<api>` with `#2` for repeats. Missing,
non-Python or unparseable files are left out of `evaluated_scope`, never
reported clean.

### Run

The committed input uses a smaller fixture budget (`200` tokens):

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm03/llm03-01-positive-input.json
```

## LLM-11 — Re-embedding or re-running inference on unchanged inputs (static proxy)

Flags Python code that embeds the same document corpus again on every run,
with no change detection, and LLM requests repeated unchanged once per loop
element. LangChain's [indexing API](https://blog.langchain.dev/syncing-data-sources-to-vector-stores/)
exists to "avoid re-computing embeddings over unchanged content". LlamaIndex's
[ingestion pipeline](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/)
skips a document when "the hash is unchanged". Amazon Bedrock Knowledge Bases
[sync incrementally](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html),
processing "only added, modified, or deleted documents since the last sync".
AWS [AGENTSUS02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html)
says: "Every duplicate model call ... is work the agent fleet has already done
once".

This v1 is a static proxy. It shows that the code re-embeds or re-asks, not
that the inputs really are unchanged between runs. It emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required, so there is no
`REFERENCE_SETTINGS`.

### Detection rule

**1. Whole corpus re-embedded on every run** (`<scope>:reembed:<api>`).

Embedding calls:
- LangChain vector store `from_documents`/`from_texts` (and the `a*` variants);
- `add_documents`/`add_texts` on a LangChain store created in the file;
- `embed_documents` on LangChain embeddings created in the file;
- LlamaIndex `VectorStoreIndex.from_documents`;
- OpenAI `embeddings.create`;
- Bedrock `invoke_model` with an `embed` model ID (from `owner_d/llmcalls.py`).

`BM25Retriever`/`TFIDFRetriever` and the non-vector LlamaIndex indexes
(summary, keyword, tree) do not embed and are ignored.

A call is flagged when all of the following hold:

- **Corpus input.** Its input comes, through assignments, loop targets,
  `append`/`extend` and splitter calls in the same scope (or module-level
  names), from a bulk read of a location fixed in the code. Bulk reads:
  - `*Loader(...).load()`/`load_and_split()`/`lazy_load()`;
  - `*Reader(...).load_data()`;
  - `glob.glob`, `os.listdir`/`walk`/`scandir`, `Path.glob`/`rglob`/`iterdir`;
  - `open(...)`;
  - `pandas.read_*`, `datasets.load_dataset`;
  - S3 `list_objects*`.

  A fixed location is a literal, a module constant, an environment variable,
  an `os.path.join`/`Path` of those, or `__file__`. Uploads, parameters,
  request data and `self.*` paths are new input, not unchanged input.
- **Unconditional.** It is not under an `if`/`else`/`match`/`except`/
  conditional expression, and no earlier `if` in its scope returns, raises,
  continues or breaks. Emptiness tests on the corpus itself (`if not docs:`,
  `if len(chunks) == 0:`) and `if __name__ == "__main__":` are not guards.
  When the call is in a function, it is not flagged if every call of that
  function in the file sits behind an existence check (`os.path.exists`,
  `isdir`, `*exist*()`, `count()`, ...) or in an `except` fallback.
- **Runs repeatedly.** Either the file also queries the index
  (`as_retriever`, `as_query_engine`, `as_chat_engine`, `similarity_search*`,
  `max_marginal_relevance_search*`, `embed_query`, `get_relevant_documents`,
  or an embedding of a non-corpus input), or the call is inside a Lambda
  `handler(event, context)`, a function decorated with
  `route`/`get`/`post`/.../`on_event`/`task`/`shared_task`/`scheduled_job`, or
  a `while True:` loop.

**2. The same request re-sent in a loop** (`<scope>:loop-invariant:<api>`).

A recognised LLM call (Anthropic `messages.*`, OpenAI chat completions /
responses, Bedrock `converse`/`invoke_model`) or OpenAI `embeddings.create` in
a `for` loop over a collection is flagged when no part of the request depends
on the loop. That means no name in the request is:
- a loop target;
- assigned or mutated in the loop (method-call receiver, subscript/attribute
  store);
- passed to another call in the loop, other than `print`/`len`.

The request may contain no calls except `json.dumps`, and no `**kwargs`.

### Not flagged

- Files with any change detection: a `hashlib`/`xxhash`/`mmh3`/`blake3`
  import or `md5`/`sha*`/`blake2*`/`crc32` call, `getmtime`/`st_mtime`, S3
  `ETag`/`LastModified`, the LangChain indexing API
  (`index`/`aindex`, `*RecordManager`), `CacheBackedEmbeddings`, a LlamaIndex
  `IngestionPipeline` or `refresh_ref_docs`.
- Persist-and-load patterns: `if os.path.exists(DIR): load ... else: build`,
  `try: FAISS.load_local(...) except: FAISS.from_documents(...)`, and per-item
  `if id in seen: continue`.
- Ingest-only scripts that embed and exit, with no query in the file and no
  recurring entry point, because they may run once.
- Query-time embedding of a single user query.
- Repeated requests in `range(...)`/`itertools.count`/`repeat` loops
  (sampling), inside `try`/`with`/`if` in the loop (retries such as tenacity
  `with attempt:`), or in loops with `break`/`return` (first success).
- Comprehensions and `while` loops.
- Test files (`test_*.py`, `*_test.py`, `conftest.py`, `tests/`), `test*`
  functions and `Test*` classes.
- `# noqa` or `# noqa: LLM-11` on the evidence line.

Notebooks are not Python files. Like other unsupported, missing or
unparseable files, they are left out of `evaluated_scope` and never reported
clean.

A repeat gets `#2`. Confidence is `medium`. It is `low` for repeated
`chat.completions` requests on a client not created in the file.

**Reviewer challenge case:** an app that builds
`VectorStoreIndex.from_documents(SimpleDirectoryReader("data").load_data())`
inside `@st.cache_resource` and queries it is flagged. The cache embeds once
per process, but every restart, deploy or new replica embeds the unchanged
corpus again.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm11/llm11-01-positive-input.json
```


## OBS-08 — Raw high-resolution metrics kept long without downsampling

Flags metric configs that scrape at a high resolution and keep those raw
samples for a long time with no downsampling, and Thanos compactors that run
with downsampling switched off. The check is static: configs are read as text
with `owner_d/miniyaml.py` (collector configs through `owner_d/otelconfig.py`)
and are never applied, rendered or resolved. It pairs a scrape interval only
with a store visible in the same file. It does not observe sample volumes or
the alerts that read the series, so it emits no measurements.

Prometheus local storage and Amazon Managed Service for Prometheus (AMP) keep
every raw sample until retention; only the Thanos compactor downsamples (5m
after 40h, 1h after 10d). CloudWatch high-resolution metrics are out of scope:
CloudWatch keeps data points under 60 s for 3 hours and then rolls them up by
itself.

### Input

A contract v1 `input` payload with one `static` source per `file:<path>` scope
item and the context settings below. Supported `.yaml`/`.yml` content:

- Prometheus Operator `Prometheus`/`PrometheusAgent` resources
  (`monitoring.coreos.com/*`)
- kube-prometheus-stack values (`prometheus.prometheusSpec`)
- Prometheus server configs (`global`, `scrape_configs`, `remote_write`), as a
  plain file or a `|` string in a `ConfigMap` `data` entry, together with a
  Prometheus server container in a Kubernetes workload or Compose service in
  the same file
- OpenTelemetry Collector configs with a `prometheus` receiver (plain,
  `OpenTelemetryCollector`, `ConfigMap`)
- Thanos compactor (`thanos compact`) containers in Kubernetes workloads and
  Compose services

### Context settings (all required)

| Setting | Meaning | Reference value |
| --- | --- | --- |
| `min_scrape_interval_seconds` | Scrape intervals strictly below this are high-resolution | `15` |
| `max_raw_retention_days` | Raw retention strictly above this is long | `15` |

The module exposes the reference values as `REFERENCE_SETTINGS`. 15 s is the
interval of the Prometheus example config (the documented default is 1m); the
issue names 1s/10s scrapes. 15 days is Prometheus' default local retention and
how long CloudWatch keeps 1-minute data. Missing or invalid settings (absent,
not a positive number) make the result `unavailable`.

### Detection rule

Durations use the Prometheus `<duration>` format (`1y2w3d4h5m6s7ms`, `0`).

| Identity | Flagged when | Confidence |
| --- | --- | --- |
| `Prometheus/[<ns>/]<name>:scrapeInterval` | The effective `spec.scrapeInterval` (default `30s`) is high-resolution and a store is long: `spec.retention` (operator default `24h` when retention, retentionSize and retentionPercentage are all unset) or an AMP `remoteWrite` | medium; low when only AMP is long |
| `values/prometheus.prometheusSpec:scrapeInterval` | The same for kube-prometheus-stack values; retention must be explicit, because the chart default depends on the chart version | medium; low when only AMP is long |
| `[ConfigMap/<name>:<key>:]global:scrape_interval`, `[…]job/<job_name>:scrape_interval` | A global interval that at least one job inherits, or a job's own interval, is high-resolution and a store is long: the Prometheus server in the same file (`--storage.tsdb.retention.time`, deprecated `--storage.tsdb.retention`, default `15d` without retention flags), or a `remote_write` to an AMP workspace (`aps-workspaces.<region>.amazonaws.com/workspaces/...`) | medium; low when only AMP is long |
| `[<collector label>]receiver/<id>:global:scrape_interval`, `…:job/<name>:scrape_interval` | A collector `prometheus` receiver scrapes at high resolution and its metrics pipeline exports to a `prometheusremotewrite` exporter with an AMP endpoint | low |
| `<Kind>/[<ns>/]<name>:<container>:downsampling.disable`, `service/<name>:downsampling.disable` | `thanos compact` runs with `--downsampling.disable` and `--retention.resolution-raw` is unset or `0d` (kept forever) or long | medium |

AMP stores samples for 150 days by default (configurable up to 1095). The
workspace retention is not visible in a scraper config, so AMP-only findings
are `low`. A repeated identity gets `#n`.

Not flagged:

- intervals at or above the threshold, and global intervals that every job
  overrides
- high-resolution scrapes whose stores are all short (for example
  `retention: 7d`, or the operator default `24h`)
- Thanos compactors with downsampling on, or with downsampling off and a short
  raw retention
- hits with `# noqa` / `# noqa: OBS-08` on the hit line or directly above the
  job item or container

Not judged (a file with nothing else to report is listed as a limitation,
never reported clean):

- Prometheus servers in agent mode (`--agent`, `--enable-feature=agent`), which
  have no local TSDB
- size-only retention (`retentionSize`, `--storage.tsdb.retention.size`), whose
  time bound is unknown
- non-AMP remote-write targets and collector exporters, whose retention and
  downsampling are not visible
- unresolved values (`${VAR}`, `$(VAR)`) and several Prometheus servers in one
  file with different retention
- a Prometheus server with long retention whose scrape config is in another
  file, and scrape configs with no store in the file

Not evaluated: development/test paths (tokens `dev`, `development`, `debug`,
`local`, `test(s)`, `testing`, `testdata`, `e2e`, `ci`, `devcontainer`), Helm/Go
templates and YAML outside the `miniyaml` subset. Other files are out of scope
(`Unsupported`), so the scan worker passes only matching inputs to the check.

### Limitations

The check reads one file at a time. It cannot see ServiceMonitor/PodMonitor
`interval` overrides, `scrape_config_files`, `additionalScrapeConfigs`, stores
in other files or the AMP workspace retention. A sub-minute interval can be a
real need (fast SLO burn alerts, autoscaling on Prometheus metrics); the rule
cannot see which series those read, so keep the high resolution for those jobs
only and record the decision with `# noqa: OBS-08`. Mimir/Cortex,
VictoriaMetrics, Thanos Receive, Grafana Agent/Alloy and CloudWatch agent
configs are not covered.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/obs08/obs08-01-positive-input.json
```

## LLM-02 — No response cache for repeated requests (static proxy)

Flags LLM calls in Python source that send a repeatable request on a code path
that runs repeatedly, with no response cache visible in front of them. AWS
[AGENTCOST02-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html)
recommends caching responses so that repeated requests are not processed
again. This v1 is a static proxy. It proves "a repeatable call with no visible
cache"; it does not prove that identical requests actually recur, how often,
or what a cache would hit. It emits no measurements.

### Input

A contract v1 `input` payload with one `static` source per scope item
(`file:<path>`, `.py` only). No context settings are required.

### Detection rule

LLM calls are recognised by `owner_d/llmcalls.py`: Anthropic `messages.*`,
OpenAI chat completions / responses / legacy completions, Bedrock
`converse`/`invoke_model`. Streaming calls (`*.stream`, `converse_stream`,
`invoke_model_with_response_stream`, `stream=True`) and Bedrock embedding
models are not evaluated. A call is flagged when all three hold:

| Condition | Meaning |
| --- | --- |
| Repeatable request | No positional arguments and no unknown `**kwargs`, and at least one of `messages`/`system`/`input`/`instructions`/`prompt`/`body` is passed. Every argument except deployment knobs (`model`, `modelId`, token caps, `top_p`, `stop`, `timeout`, `metadata`, `user`, `prompt_cache_key`, ...) is built only from literals, module constants and **small keys**. Allowed operations are f-strings, `+`/`%`, `.format()`, `.join()`, `.strip()`/`.lower()`/..., `json.dumps`, `textwrap.dedent`/`inspect.cleandoc` and `CONSTANTS[key]`. A small key is a parameter of the enclosing function that is a route path parameter (`{topic}` in `@app.get("/faq/{topic}")`, `<topic>` in Flask) or that is annotated `int`, `bool`, `Literal[...]` or an `Enum` defined in the file. Other parameters, `event[...]`, `request.*`, `self.*`, `datetime.now()`, other calls and names that are mutated anywhere in the file (`history.append(...)`) make the request dynamic. |
| Repeated path | The call is in a request handler, or in a function that a handler reaches through calls in the same file. Request handlers are route decorators (`.get/.post/.put/.patch/.delete/.head/.options/.route/.api_route/.websocket("/...")`), Lambda handlers (`lambda_handler`, or first parameters `event, context`), Chainlit `@cl.on_message`, and Django views (first parameter `request` or `self, request` in a file that imports `django`). A `while True:` loop with no `break` also counts, as does module scope of a Streamlit script (it reruns on every interaction), and so do the functions these call. |
| No visible cache | The file imports no cache library (`cachetools`, `redis`, `valkey`, `aioredis`, `pymemcache`, `memcache`, `pylibmc`, `aiocache`, `diskcache`, `gptcache`, `joblib`, `requests_cache`, `hishel`, `flask_caching`, `fastapi_cache`, `cashews`, `dogpile`, `beaker`, `redisvl`, `momento`). It imports no other path with `cache` in it outside `functools`/`async_lru`/`streamlit`, e.g. `django.core.cache` or `langchain_community.cache`. It does not call `set_llm_cache` or assign a `*cache*` attribute (`langchain.llm_cache = ...`, `litellm.cache = ...`). No function on the path is memoized: no decorator name contains `cache`/`memo` (`lru_cache`, `cache`, `alru_cache`, `st.cache_data`, `memory.cache`, `memoize`, ...). A memoized function fronts everything it calls. No identifier on the path contains `cache`, `memo` or `session_state`. Provider prompt-caching names (`cache_control`, `cachePoint`, `prompt_cache_key`, ...) do not count, because that is LLM-01. There is also no lookup-then-return guard (`if key in d: return ...`, or `hit = r.get(k)` then `if hit: return hit`). |

Legitimate exceptions (not flagged):
- **Intended variety.** An explicit `temperature` > 0 is treated as a request
  for varied answers. That covers OpenAI/Anthropic `temperature`, Bedrock
  `inferenceConfig.temperature`, and the `invoke_model` body's `temperature`
  or `textGenerationConfig`/`inferenceConfig` temperature. So are `n` other
  than 1 and a temperature that cannot be resolved statically. With
  `temperature=0`, confidence is `medium`. With temperature unset, the
  provider samples at its default, so whether varied answers are intended is
  unknown, and confidence is `low`.
- **Calls directly in a `for` loop or comprehension.** Re-sending the same
  request per loop element is LLM-11.
- Caches of any kind in front, with or without a TTL. LLM-13 judges caches
  without a TTL.
- Dynamic prompts.
- One-shot module-level scripts, `main()` and functions that no entry point
  in the file reaches.
- Health and readiness probes: a route whose path or function name mentions
  `health`, `liveness`, `readiness`, `livez`/`readyz`, or the word `ping`,
  `ready` or `probe`. A probe calls the model to check that it answers, so a
  cached response would defeat it.
- Lambdas and class bodies.
- `test*` functions and `Test*` classes.

`# noqa` or `# noqa: LLM-02` on the call line suppresses a finding.

Evidence is the call (up to 8 lines). The identity is
`<qualified scope>:<provider>.<api>` (e.g. `faq:anthropic.messages.create`,
`<module>:openai.chat.completions.create`), with `#2` for repeats. Confidence
is `medium` with `temperature=0`. It is `low` with temperature unset or when
the only provider evidence is an OpenAI-compatible `chat.completions` chain on
a client not created in the file. A static prompt with no cache also has no
prompt caching, so a large one can be reported by LLM-01 too. The remedies
differ: prompt caching makes the call cheaper, while a response cache avoids
the call. Missing, non-Python or unparseable files are left out of
`evaluated_scope` and never reported clean.

Not evaluated:
- caches in other modules, middleware, API Gateway or a CDN;
- prompts read from files or other modules;
- keys passed through helper parameters;
- Gradio callbacks;
- LangChain/LlamaIndex/LiteLLM model wrappers;
- other languages.

The taxonomy's telemetry route (Logs Insights through
`owner-d-log-analyzer`) is a follow-up. It needs client request logs with a
prompt hash and cache hit/miss fields, and there is no standard format for
those yet, so no `LOGS_INSIGHTS_QUERY` or normalizer is exported.

### Run

```bash
PYTHONPATH=detectors/owner-d .venv/bin/python -m owner_d.cli \
  detectors/owner-d/tests/fixtures/llm02/llm02-01-positive-input.json
```
