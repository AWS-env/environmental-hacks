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

