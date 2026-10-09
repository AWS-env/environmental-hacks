# Owner C detectors - Python (Category 1)

Contract v1 detectors for the Python taxonomy checks owned by owner C. The shared input/result
boundary is defined in [`docs/DETECTOR_CONTRACT.md`](../../docs/DETECTOR_CONTRACT.md); JSON Schema is the
source of truth. Detectors are pure functions over contract payloads: they parse source with `ast`,
**never import or execute it**, never call AWS APIs, and source/artifact collection stays outside the
detector (see `owner_c/connector.py` and `owner_c/normalize/`).

| Check | Pattern | Evidence | Issue |
| --- | --- | --- | --- |
| PY-09 | Mutable default arguments | static | #253 |
| PY-04 | String `+=` inside loops | static | #248 |
| PY-02 | `list.pop(0)` / `insert(0, x)` as a queue | static | #246 |
| PY-03 | pandas `iterrows()` / row-wise `apply(axis=1)` | static | #247 |
| PY-08 | `open()` / connections without a context manager | static | #252 |

PY-06 (`re.compile` in loops) is intentionally not implemented: Python caches recent patterns, so the
impact is small. Its issue stays open.

## Layout

```text
detectors/owner-c/
  owner_c/
    common.py            parse context (Ctx), Hit/Candidate, helpers
    contract.py          payload builders + fingerprint (kept in sync with shared/ by a test)
    runner.py            evaluate(input) -> result for one check, with explicit coverage
    connector.py         repo files (+ normalized artifacts) -> contract input payloads
    checks/py_NN.py      static checks; checks/__init__.py is the registry
    artifact_checks/     artifact-confirmed checks (candidate + confirm)
    normalize/           py-spy speedscope / memray stats -> normalized contract `artifact` data
    aws/                 Lambda handlers (static scan, profile parser)
    cli.py               `evaluate` and `scan` commands
  tests/                 unittest suite; fixtures/py_NN/cases.json are the committed verification cases
```

## Run

From the repository root, using Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r shared/contracts/requirements.txt

# scan a local directory with every enabled check; each result is validated against the shared contract
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo --json
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo \
  --artifact speedscope=profile.speedscope.json --artifact memray_stats=memray.stats.json

# evaluate one contract input payload
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c evaluate input.json -o result.json

# tests (verification cases plus unit tests); also runs the shared contract suite
PYTHONPATH=detectors/owner-c .venv/bin/python -m unittest discover -s detectors/owner-c/tests
.venv/bin/python -m shared.contracts.verify
```

## Input, scope and coverage

One input payload per check. Scope IDs are `file:<repo-relative path>`; each carries a `static` source
(the file text) and, for artifact checks, an `artifact` source with normalized data. The connector
declares every exclusion in `context`, so a file left out is a documented choice:

| Context setting | Meaning | Reference value |
| --- | --- | --- |
| `language` | Always `python` | `python` |
| `exclude_tests` | Test files (`tests/`, `test_*.py`, `conftest.py`) are out of scope; test-suite waste belongs to the TST checks | `true` |
| `max_file_bytes` | Larger files are out of scope | `1000000` |
| `excluded_dirs` | Vendored/build directories out of scope | `.git`, `node_modules`, `venv`, ... |
| `min_time_share` (PY-01) | Fraction of sampled time a line must be on the stack | `0.05` |
| `min_alloc_bytes` (PY-05, PY-11) | Bytes allocated at a line that count as significant | `10485760` |

Coverage is never silent. A file that cannot be parsed, or (artifact checks) that no artifact covers,
is left out of `coverage.evaluated_scope` with a limitation, so the result is `partial` or
`unavailable` - never `completed` with an empty findings array. Missing or invalid settings make the
result `unavailable`.

## Findings

`identity` is a semantic anchor without line numbers, e.g. `Cls.method(arg)` (PY-09) or
`function:receiver.pop(0)` (PY-02); a repeated anchor within one file gets `#2`, `#3`, ... so
fingerprints stay unique. Static evidence quotes the exact source line(s); artifact evidence cites one
field of the normalized artifact data (`time_share_line_N`, `allocated_bytes_line_N`,
`allocated_bytes_deepcopy_internals`). No energy or emissions measurements are emitted: static
patterns prove the pattern, not its impact.

## PY-09 - mutable default arguments

Flags `def f(a=[])`, `{}`/`set()`/`dict()`/`defaultdict(...)` etc. in positional, keyword-only and lambda
defaults. Not flagged: `None`, scalars, tuples, `frozenset()`, immutable annotations (`Sequence[...]`),
lines with `# noqa` (bare or naming B006/B008). Identity: `qualname(arg)`. Confidence: high.
Limitation: a deliberate cache is a legitimate exception the code cannot distinguish.

## PY-04 - string `+=` in loops

Flags `x += ...` inside `for`/`while`/comprehension scope when the value is a string expression
(literal, f-string, `str()`, `%`/`+` with a string) or `x` is assigned a string in the same scope.
Identity: `qualname:target`. Confidence: medium. Limitation: loop length unknown; types are inferred.

## PY-02 - `list.pop(0)` / `insert(0, x)` in loops

Flags `x.pop(0)` (one argument) and `x.insert(0, v)` (two) inside loops. Not flagged: `dict.pop(0, d)`,
`DataFrame.insert(0, col, v)` (three arguments), calls outside loops. Identity: `qualname:recv.pop(0)`.
Confidence: medium. Limitation: list size and receiver type are unknown.

## PY-03 - pandas row iteration

Flags `.iterrows()` and, in files that import pandas, `.apply(..., axis=1 | 'columns')`.
Identity: `qualname:recv.iterrows` / `qualname:recv.apply(axis=1)`. Confidence: medium.
Limitation: frame size unknown.

## PY-08 - unmanaged resources

Flags `open()`, `io.open()`, `sqlite3/psycopg2/pymysql/mysql.connector.connect()` not used as a `with`
item. Treated as managed: closed in the same scope, used in `with`, returned (the handle itself),
stored on an object, passed to a constructor or `append/add/enter_context`-style call, `__enter__`.
`return f.read()` does not transfer ownership. Identity: `qualname:callee`. Confidence: high.






## AWS deployment (Free Plan, project Region)

`cdk/owner-c/python-detectors.yaml` (CloudFormation) deploys `owner-c-static-scan` and
`owner-c-profile-parser`, a private expiring artifact bucket and least-privilege roles, all tagged
`owner=C`. Build the code zip with `scripts/build-owner-c-lambda.sh`. Both Lambdas publish **one contract v1
result per check per batch of files** to the shared `findings-hub` bus (owner D) with
`detail-type: detector.result.v1`; they check the bus exists first because EventBridge silently drops
events sent to a missing bus. Only owner D's hub rules decide what is stored.

## Verification

Cases `PY-NN-xx` in `tests/fixtures/py_nn/cases.json` are the committed fixtures referenced by the
Verification plan comment on each issue. Every case validates the result with
`shared.contracts.validation.validate_pair` and asserts status, finding identities, evidence lines and
coverage. Artifact tests use real py-spy and memray captures in `tests/fixtures/real/` (memray was captured
in a Linux container; it has no Windows wheel). The cases fail against always-empty and always-flag
implementations.
