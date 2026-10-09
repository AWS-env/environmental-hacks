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
