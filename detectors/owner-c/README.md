# Owner C detectors - Python and JavaScript (Categories 1 and 2)

Contract v1 detectors for the Python and JavaScript/TypeScript taxonomy checks owned by owner C. The shared input/result
boundary is defined in [`docs/DETECTOR_CONTRACT.md`](../../docs/DETECTOR_CONTRACT.md); JSON Schema is the
source of truth. Detectors are pure functions over contract payloads: they parse source (Python with `ast`,
JS/TS with tree-sitter), **never import or execute it**, never call AWS APIs, and source/artifact collection
stays outside the detector (see `owner_c/connector.py` and `owner_c/normalize/`). Research and decisions:
[`category-2-js.md`](../../docs/research/category-2-js.md), [`category-2-decisions.md`](../../docs/research/category-2-decisions.md).

| Check | Pattern | Evidence | Issue |
| --- | --- | --- | --- |
| PY-09 | Mutable default arguments | static | #253 |
| PY-04 | String `+=` inside loops | static | #248 |
| PY-02 | `list.pop(0)` / `insert(0, x)` as a queue | static | #246 |
| PY-03 | pandas `iterrows()` / row-wise `apply(axis=1)` | static | #247 |
| PY-08 | `open()` / connections without a context manager | static | #252 |
| PY-07 | Blocking calls inside `async def` | static | #251 |
| PY-10 | Unused heavy imports (numpy, pandas, torch, ...) | static | #254 |
| PY-01 | `x in <list>` inside a loop | static candidate + py-spy artifact | #245 |
| PY-05 | Temporary list for a single pass (`sum([...])`) | static candidate + memray artifact | #249 |
| PY-11 | Needless `deepcopy` / `.copy()` | static candidate + memray artifact | #255 |
| CODE-RT.6 | Outdated runtime / interpreter version | static + dated support table | #102 |
| CODE-RT.2 | Thread/executor hop awaited around trivial work (Python asyncio) | static | #99 |
| JS-06 | Listeners, timers, subscriptions without cleanup | static | #190 |
| JS-02 | `includes`/`indexOf`/`find`/`some` inside loops | static candidate + V8 CPU profile | #186 |
| JS-04 | Synchronous `fs` / `child_process` / `crypto` / `zlib` calls | static candidate + V8 CPU profile | #188 |
| JS-08 | `new Intl.*` / constant `new RegExp` built per call | static candidate + V8 CPU profile | #192 |
| JS-03 | `JSON.parse(JSON.stringify(x))` deep clone | static candidate + heap profile | #187 |
| JS-05 | Chained `map`/`filter`/... building intermediate arrays | static candidate + heap profile | #189 |
| JS-07 | Spreading the accumulator in `reduce`/loops | static candidate + heap profile | #191 |
| JS-09 | `throw`/`catch` used as local control flow | static | #193 |
| JS-01 | `await` inside loops (independent calls run serially) | static candidate + AWS X-Ray traces | #185 |

PY-06 (`re.compile` in loops) is intentionally not implemented: Python caches recent patterns, so the
impact is small. Its issue stays open. CODE-RT.4 (#100) and CODE-RT.5 (#101) are deferred.

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
    normalize/           one module per artifact type (profiles, traces) -> normalized `artifact` data
    js/                  tree-sitter parse context (ctx.py) and V8 profile confirmation helpers
    config/              dated runtime support table for CODE-RT.6
    aws/                 Lambda handlers (static scan, profile parser, X-Ray parser, presign)
    cli.py               `evaluate` and `scan` commands
  collectors/            collect-heap.js, the heap-profile collector clients run in their CI
  examples/xray-demo/    traced demo Lambda used to test the JS-01 X-Ray route
  examples/client-ci/    tested client-side script: profile, presign, upload, manifest last
  requirements.txt       tree-sitter parsers (JS/TS detectors only)
  tests/                 unittest suite; fixtures/<check>/cases.json are the committed verification cases
```

## Run

From the repository root, using Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r shared/contracts/requirements.txt
.venv/bin/python -m pip install -r detectors/owner-c/requirements.txt   # tree-sitter, JS/TS checks only

# scan a local directory with every enabled check; each result is validated against the shared contract
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo --json
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo \
  --artifact speedscope=profile.speedscope.json --artifact memray_stats=memray.stats.json
PYTHONPATH=detectors/owner-c .venv/bin/python -m owner_c scan path/to/repo \
  --artifact cpuprofile=app.cpuprofile --artifact heapprofile=collected.heapprofile

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
| `language` | `python` or `javascript` (one input per check and language) | `python` |
| `exclude_tests` | Test files (`tests/`, `test_*.py`, `conftest.py`) are out of scope; test-suite waste belongs to the TST checks | `true` |
| `max_file_bytes` | Larger files are out of scope | `1000000` |
| `excluded_dirs` | Vendored/build directories out of scope | `.git`, `node_modules`, `venv`, ... |
| `min_time_share` (PY-01, JS-02, JS-04, JS-08) | Fraction of sampled time a line/function must be on the stack | `0.05` |
| `min_alloc_bytes` (PY-05, PY-11, JS-03, JS-05, JS-07) | Bytes allocated at a line/function that count as significant | `10485760` |
| `min_serial_calls`, `min_serial_seconds` (JS-01) | Back-to-back traced calls and serial wall time that count as serial waiting | `3`, `0.05` |
| `reference_date` (CODE-RT.6) | "As of" date for end-of-life comparisons | today (ISO date) |

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

## PY-07 - blocking calls in `async def`

Flags `time.sleep`, `requests.*`, `httpx.<verb>`, `urllib.request.urlopen`, `subprocess.run/call/...`,
`os.system`, `sqlite3/psycopg2/pymysql.connect` (medium) and plain `open()` (low) directly inside
`async def`. Not flagged: nested sync functions, `asyncio.to_thread(time.sleep, 1)`.
Identity: `qualname:callee`. Limitation: blocking calls made through helpers are not detected.

## PY-10 - unused heavy imports

Flags imports from numpy, pandas, torch, tensorflow, scipy, sklearn, matplotlib, seaborn, cv2,
transformers, jax, keras, plotly whose bound name is never used. Not flagged: `__init__.py`, the library's
own package, `import x as x` re-exports, names in `__all__`, string annotations or type strings
(`cast("pd.DataFrame", x)`), `TYPE_CHECKING` blocks, `try/except ImportError`, `# noqa` (bare/F401).
Identity: `qualname:import:name`. Confidence: high.

## PY-01 - list membership in loops (py-spy)

Candidate: `x in <name|attr>` inside a loop, not against known sets/dicts, not a string-literal substring
test. Reported only when the client's py-spy capture (`py-spy record -f speedscope`) shows that line on the
stack for at least `min_time_share` of sampled time. Identity: `qualname:in:target`. Confidence: high
at >= 25%, otherwise medium. Limitation: container type is not resolved; only files that appear in the
profile are evaluated.

## PY-05 - temporary list for a single pass (memray)

Candidate: `sum|min|max|any|all([...])`, `sum(list(map(...)))`, `for x in list(map(...))`. Reported only when
memray (`memray stats --json`) attributes at least `min_alloc_bytes` to that line. Identity:
`qualname:sum(listcomp)`. Confidence: medium. Limitation: memray lists only the top allocation sites.

## PY-11 - needless copies (memray)

Candidate: `copy.deepcopy(...)` anywhere, `x.copy()` inside loops. Reported only when memray attributes at
least `min_alloc_bytes` to the line, or - for deepcopy, which memray attributes to `copy.py` internals - to
`copy.deepcopy`. Identity: `qualname:callee`. Confidence: medium. Limitation: whether the copy is needed
is a judgement the artifact cannot prove.

## CODE-RT.6 - outdated runtime / interpreter

Reads `.nvmrc`, `.node-version`, `.python-version`, `runtime.txt`, `Pipfile`, `.tool-versions`, `package.json` (`engines`),
Dockerfiles, `serverless.yml`, SAM/CloudFormation templates, Terraform `.tf` and `.github/workflows/*.yml` version
declarations and compares them, as of `reference_date`, with `owner_c/config/runtime_support.json` (retrieved 2026-10-09
from the AWS Lambda runtimes page and endoflife.date; Lambda config uses the Lambda deprecation date, everything else
upstream end of life). Identity: `runtime:source` (`node:setup-node`, `node:engines.node`). Flags only versions past end
of life or within `warn_days`. Limitation: aliases (`lts/*`, `latest`) and variables are not resolved; a library's CI matrix
can list old versions on purpose.

## CODE-RT.2 - thread hop around trivial work (Python asyncio)

Flags `await asyncio.to_thread(f, ...)` / `await loop.run_in_executor(ex, f, ...)` when `f` is a call-free lambda, a cheap
builtin (`len`, `str`, `int`, ...) or a coroutine function defined in the same file. Unknown functions are never flagged.
Identity: `qualname:to_thread(len)`. Confidence: low (medium for the coroutine case). Limitation: static only; there is no
precedent rule in Ruff or flake8-async (they flag the opposite).

## JavaScript / TypeScript checks

Files ending `.js .jsx .mjs .cjs .ts .tsx` (not minified/bundled files or `.d.ts`) are parsed with tree-sitter (Python bindings; install
`requirements.txt`). A file with syntax errors, or one the grammar rejects, is left out of coverage and the result is
`partial`, never clean (known case: class fields without semicolons, e.g. `#a` followed by `[k] = 1`, which Node accepts but
tree-sitter-javascript 0.25 does not). `// eslint-disable[-next-line] <rule>` comments naming the equivalent ESLint/Biome/oxc
rule suppress a hit. JS candidates are matched to profiles through their nearest enclosing **function** (V8 attributes work
to functions, not lines); module top-level code is never matched.

### Collecting the evidence (client CI)

| Artifact | How the client produces it | Normalized as |
| --- | --- | --- |
| `cpuprofile` | `node --no-opt --cpu-prof app.js` writes `*.cpuprofile` | per-function and per-line time share (`function_time_share_line_N`, `time_share_line_N`) |
| `heapprofile` | `node -r ./collectors/collect-heap.js app.js` writes `collected.heapprofile` | allocated bytes per function (`allocated_bytes_function_line_N`), freed objects included |
| `xray` | not uploaded: read from the client's AWS X-Ray traces (see JS-01) | longest run of back-to-back same-named subsegments |

**Why `--no-opt`:** V8 inlines small hot functions into their callers, so the profile credits the caller and a hot callee
is not confirmed. `--no-opt` keeps function boundaries in the profile at the cost of speed; it is only for the profiled run.

**Why a collector for heap:** V8's default heap profile (`node --heap-prof`) reports only objects still alive when it stops,
which hides temporary arrays, clones and accumulator copies; `collect-heap.js` starts the sampler with freed objects
included. It only observes the process.

### JS-06 - listeners, timers and subscriptions without cleanup

In `useEffect`/`useLayoutEffect`/`useInsertionEffect` bodies and `componentDidMount`: `addEventListener`, `setInterval`,
`.subscribe`, `.addListener`, `.on` without a returned cleanup (or `componentWillUnmount`) that undoes them by name.
Not flagged: `{once}`/`{signal}` options, cleanup returned by reference. Inline handler functions are flagged because they
can never be removed. Un-stored `setInterval` is flagged at low confidence. Identity: `qualname:effect:kind`.
Limitation: handler identity and capture flags are not compared; one-shot `setTimeout` is not analysed.

### JS-02 - `includes`/`indexOf`/`find`/`findIndex`/`some` inside loops

Loop-nested (including `forEach`/`map`/... callbacks) lookups on non-string receivers, confirmed when the enclosing function
holds at least `min_time_share` of busy CPU samples. Identity: `qualname:method`. Confidence: medium, high when a profiled line
inside the call is itself on the stack.

### JS-04 - synchronous `fs` / `child_process` / `crypto` / `zlib` calls

Calls resolved through `require`/`import` to a known sync API (`readFileSync`, `execSync`, `pbkdf2Sync`, `gzipSync`, ...)
inside a function, confirmed by the CPU profile. Module top level (start-up) is ignored. Identity: `qualname:module.function`.

### JS-08 - formatters and RegExps rebuilt per call

`new Intl.NumberFormat/DateTimeFormat/...` and `new RegExp(<constant>)` inside functions, confirmed by the CPU profile.
Dynamic patterns are not flagged (they cannot be hoisted). Identity: `qualname:new Ctor`.

### JS-03 - `JSON.parse(JSON.stringify(x))` clone

Plain clones (`JSON.stringify` without replacer or spacing), confirmed when the enclosing function allocated at least
`min_alloc_bytes` in the heap profile. The recommendation notes that `structuredClone` differs (it throws on functions).
Identity: `qualname:JSON.parse(JSON.stringify)`.

### JS-05 - chained iteration building intermediate arrays

`map`/`filter`/`flatMap`/`slice`/`concat`/`flat`/... chains with at least two array-allocating steps, confirmed by the heap
profile. Identity: `qualname:map.filter` (the chain's method names).

### JS-07 - spreading the accumulator

`[...acc, x]` / `{...acc, k: v}` / `acc.concat` / `Object.assign({}, acc, ...)` inside `reduce` callbacks, and
`name = [...name, x]` inside loops; mutating the accumulator (`acc.push`) is fine. Confirmed by the heap profile.
Identity: `qualname:reduce-spread` / `reduce-copy` / `loop-spread:name`.

### JS-09 - `throw`/`catch` as local control flow

Static only (V8 profiles cannot attribute exception cost). Flags a `throw` caught in the same function when the try body has
no other call, `await` or `new` (otherwise the catch is shared error handling), and a loop `try` whose catch only
`continue`s (an empty catch isolating callbacks is not flagged). Identity: `qualname:throw-in-try` / `try-skip-in-loop`.

### JS-01 - `await` inside loops (X-Ray)

Static candidate: an `await` in a loop body. Dropped as serial-on-purpose: `for await`, retry/backoff/sleep/throttle loops,
loops with `break`/`return`, and `x = await f(x)` where the next iteration depends on the result. Confirmed only by the
client's AWS X-Ray traces: the `owner-c-xray-parser` Lambda reads `GetTraceSummaries`/`BatchGetTraces` (read-only,
optionally through a role in the client's account), maps traced function names to repo files (`xray.function_files`) and
reports when at least `min_serial_calls` same-named sibling subsegments ran back to back (`min_serial_seconds` of waiting).
It never invokes the client's functions. Real traces of the deployed demo Lambda are in `tests/fixtures/real/xray`
(parallel: clean; serial: 8 back-to-back `Inventory` calls, out of time order in the document). Identity:
`qualname:await:callee`. Limitation: a trace has no source lines, so all loop-await candidates in the mapped file are
confirmed together.

## AWS deployment (Free Plan, project Region)

`cdk/owner-c/python-detectors.yaml` (CloudFormation) deploys, all tagged `owner=C` with least-privilege roles and a private
expiring artifact bucket (`owner-c-artifacts-<account>-<region>`):

| Lambda | Role |
| --- | --- |
| `owner-c-static-scan` | static checks over a repo zip |
| `owner-c-profile-parser` | confirms candidates with uploaded profiles; also runs from the S3 `manifest.json` trigger |
| `owner-c-presign` | 15-minute presigned PUT URLs for the client's uploads (regional S3 endpoint) |
| `owner-c-xray-parser` | JS-01: reads the client's X-Ray traces |
| `owner-c-xray-demo` | traced demo workload (Active tracing) for testing the X-Ray route |

Build the code zips with `scripts/build-owner-c-lambda.sh` (needs Python, pip and npm). All parsers publish **one contract
v1 result per check per batch of files** to the shared `findings-hub` bus (owner D) with `detail-type:
detector.result.v1`; they check the bus exists first because EventBridge silently drops events sent to a missing bus. Only
owner D's hub rules decide what is stored. Failed asynchronous invocations go to a dead-letter queue; the alarm `owner-c-parse-dlq-not-empty` is in ALARM while that queue holds a message (no notification target is attached).

### Upload flow (client CI)

1. Invoke `owner-c-presign` with `{"repository_id", "commit_sha", "artifacts": ["<artifact type>", ...]}`; it returns
   short-lived PUT URLs under `uploads/<repository>/<sha>/`. The client never gets credentials.
2. PUT `repo.zip`, then each artifact (`<artifact type>.json`).
3. PUT `manifest.json` **last** (`{"repository_id", "commit_sha", "artifacts": [...]}`): the S3 event triggers
   `owner-c-profile-parser`, which reads the zip and artifacts and publishes the results. The manifest must match its prefix.
   Nothing the client uploads is ever executed.

`examples/client-ci/upload-profiles.sh <repository_id> <commit_sha> <repo_dir> <entry.js>` performs the client side of this
flow (CPU profile with `--no-opt`, heap profile with the collector, presign, uploads, manifest last). It was run against the
deployed stack: the parser completed JS-02, 03, 04, 05, 07 and 08 on the freshly profiled run.

## Verification

Cases `<CHECK>-xx` in `tests/fixtures/<check>/cases.json` are the committed fixtures referenced by the
Verification plan comment on each issue. Every case validates the result with
`shared.contracts.validation.validate_pair` and asserts status, finding identities, evidence lines and
coverage. Artifact tests use real captures in `tests/fixtures/real/`: py-spy and memray (memray was captured
in a Linux container; it has no Windows wheel), Node `--cpu-prof` and heap profiles, and X-Ray traces of the deployed
demo Lambda (account id scrubbed). The cases fail against always-empty and always-flag
implementations. Real-repo hand-checks for the JS category are recorded in `docs/research/category-2-js.md`.
