# Category 1 research: Python checks (PY-01..11), owner C

Status: research draft for discussion. No code yet. Every claim below was read from
the linked source; items marked **UNVERIFIED** were not confirmed.

## Decisions so far

- Engine: Python `ast` module (no new dependency, runs in Lambda).
- Evidence model follows `docs/ARCHITECTURE_FLOWS.md` and `docs/aws_service_mapping_v1.6.md`:
  static first; runtime evidence only through client-CI artifacts (py-spy speedscope
  JSON, memray) uploaded via presigned PUT to S3 and parsed by a Lambda. We never run
  user code.
- Test data: real artifacts generated locally with py-spy and memray.
- AWS: SAM-style CloudFormation YAML (to confirm), eu-north-1. `findings-hub` is owned by
  owner D; our stack takes the bus name as a parameter and only publishes events.
- Findings use the minimal core: check_key, file, line, message, confidence,
  evidence_type (static | artifact | telemetry), refs.

## Per-check findings

| Check | Existing detectors (source) | Static rule for us | Runtime evidence (artifact) | False-positive traps | Start confidence |
|---|---|---|---|---|---|
| PY-09 mutable defaults | Ruff B006 [docs](https://docs.astral.sh/ruff/rules/mutable-argument-default/); Pylint W0102 [docs](https://pylint.readthedocs.io/en/v3.3.9/user_guide/messages/warning/dangerous-default-value.html) | default is list/dict/set literal or call | none needed | Intentional caching via mutable default (Ruff says use `lru_cache`); immutable annotations | high |
| PY-08 open() without `with` | Pylint R1732 [docs](https://pylint.readthedocs.io/en/v3.3.9/user_guide/messages/refactor/consider-using-with.html) | `open()` / connection call not a `with` item | none | Quiet when inside a context manager, result returned, or used directly in `with`; known FP in custom `__enter__` ([#4430](https://github.com/pylint-dev/pylint/issues/4430)) | high |
| PY-04 `str +=` in loop | Pylint R1713 [docs](https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/consider-using-join.html); AWS detector `python/string-concatenation@v1.0` (severity Info, tag efficiency) [docs](http://docs.aws.amazon.com/amazonq/detector-library/python/string-concatenation/) | `AugAssign(+=)` on a name known to be a str, inside a loop | none | Type not always known statically; short loops are harmless | medium |
| PY-10 unused heavy imports | Ruff F401 [docs](https://docs.astral.sh/ruff/rules/unused-import/) | import of numpy/pandas/torch/etc. never referenced | none | Re-exports (`as` alias, `__all__`), `__init__.py`, side-effect imports, TYPE_CHECKING (not documented by Ruff; our own test needed) | high |
| PY-05 `sum([...])` / `list(map())` | Ruff C419 (any/all stable; min/max/sum preview) [docs](https://docs.astral.sh/ruff/rules/unnecessary-comprehension-in-call/) | list comp passed to sum/min/max/any/all | memray peak memory could confirm (OQ-1) | Gain is small without short-circuit; autofix unsafe (side effects) | low-medium |
| PY-03 iterrows / row apply | pandas docs: iterrows builds a Series per row, dtypes not preserved, prefer `itertuples` [docs](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.iterrows.html). No lint rule found (**UNVERIFIED**) | call to `.iterrows()`; `.apply(axis=1)` | py-spy could show time in iterrows | Small frames; the pandas page gives no performance numbers | medium |
| PY-02 `list.pop(0)` / `insert(0)` | Ruff RUF015 covers `list(x).pop(0)` only, not plain `pop(0)` [docs](https://docs.astral.sh/ruff/rules/unnecessary-iterable-allocation-for-first-element/). Cost argument: shifting all items per call ([article](https://www.pythonmastery.io/tips/deque-for-queues/)) | `x.pop(0)` / `x.insert(0, ...)` inside a loop | none | Fine for a handful of items; indexed access needs list | medium |
| PY-01 `x in list` in loop | No linter rule found (**UNVERIFIED**). Cost reasoning: O(n) list lookup vs set ([article](https://switowski.com/blog/membership-testing/)) | `in` against a list-typed name inside a loop | py-spy sample counts at that line | Converting to set costs time; one-off membership is fine ([comment](https://github.com/quantifiedcode/python-anti-patterns/issues/70)) | low |
| PY-06 `re.compile` in loop | Python docs: module caches recent patterns, compile once is more efficient when reused, cache size/eviction not documented [docs](https://docs.python.org/3/library/re.html). Cache lookup still has overhead (forum benchmark, Python 3.6) | `re.compile(...)` / `re.match(str, ...)` with literal pattern inside loop or hot function | py-spy call counts (R2) | The cache makes impact small; do not overclaim | low |
| PY-07 blocking in `async def` | Ruff ASYNC210 http only [docs](https://docs.astral.sh/ruff/rules/blocking-http-call-in-async-function/); Sonar S7493 (file), S7499 (http); pylint-blocking-calls plugin. No Semgrep registry rule found | known blocking names (`time.sleep`, `requests.*`, `open`, `urllib`) inside `async def` | py-spy showing event-loop thread blocked | Misses calls via helpers; taxonomy flags it as semantic (OQ-8) | medium |
| PY-11 needless deepcopy / `.copy()` | Python docs: deepcopy recurses and may over-copy [docs](https://docs.python.org/3/library/copy.html); pandas docs: deep=False plus Copy-on-Write in 3.0 [docs](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.copy.html). No lint rule found (**UNVERIFIED**) | `copy.deepcopy` of immutables, or deepcopy/`.copy()` inside loops, result never mutated | memray allocation volume | A copy is often deliberate; needs "result mutated?" analysis | low |

## Notes

- Noise lesson: Ruff's PERF401 had misleading-advice reports ([#20350](https://github.com/astral-sh/ruff/issues/20350)) and
  the docs call it a micro-optimization. We must test on real repos and record hit/noise.
- Gaps: Ruff/Pylint/Sonar source code was not read; only docs pages and release notes.
- AWS: parser Lambdas stay parse-only. Possible overlap with owner A's profile parsers
  (mapping doc lists A for CODE-C*); confirm before building two parsers.

## Artifact formats

- py-spy: `-f speedscope` JSON (decided). Other formats exist: flame graph SVG, raw.
- memray: `stats --json` writes a JSON summary; key names **UNVERIFIED**, will be read
  from a real capture before the parser is written.
