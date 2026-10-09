# Detection Spec — CODE-C3.3 Inefficient per-iteration setup

- **Taxonomy ID:** `CODE-C3.3`
- **Issue:** #62
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | Tier A (`src/core/setup-cost.json`) or Tier B (CapWords, result kept) construction inside a `for`/`while` body whose arguments are loop-invariant; excludes write-mode opens, exceptions, cheap/scratch objects, comprehensions and already-hoisted setup |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan` + `setup-cost.json` tier table + file-local import-alias resolution; no execution |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, factory, costTier, loopType}` + `location` |
| **False-positive risk** | Medium — freshness/state-sharing and thread-safety invisible statically; Tier B noisier than Tier A |
| **Detectable** | H for Tier A (closed callee set); M for Tier B |
| **Measurable** | L statically (gain = setup cost × trip count; trip count needs a profile) |

## Signals

| Signal | Callees | Severity | Confidence | `costTier` |
|---|---|---|---|---|
| **S1 compile** | `re.compile`, `regex.compile`, builtin `compile`, `jinja2.Template` / `Environment`, XML parsers, any `<module>.compile` | medium | high | heavy |
| **S2 connection / session / client / pool** | `requests.Session`, `httpx.Client`, `aiohttp.ClientSession`, `boto3.client` / `resource` / `Session`, DB `connect` (sqlite3, psycopg, pymysql, mysql), `MongoClient`, `redis.Redis`, `create_engine`, `smtplib.SMTP`, gRPC channels, thread/process pools | high | medium | heavy |
| **S3 read-mode file open** | `open`, `io.open`, `codecs.open`, `gzip/bz2/lzma.open`, any `<obj>.open` | medium | medium | heavy |
| **S4 other construction (Tier B)** | CapWords callee whose result is bound to a name | low | medium | unknown |

Setup sites are any call owned by the loop body, including assignment RHS, `with X(...) as v:`
items and calls nested in other calls (`json.load(open(path))`). Tier B additionally requires
the result to be bound (assignment / `with ... as`). Callees resolve through the file's
imports (`import requests as rq`, `from requests import Session`, `from re import compile as c`).

A close per iteration (`with` inside the loop, `.close()` in the body) does **not** lower
severity: it is the repeated setup/teardown being reported. `agentPrompt` carries the
lifecycle advice (create once, `with` around the loop, close once after; thread-safety).

## False-positive guards (negatives)

- Any argument reads a name assigned in the loop (loop target, body assignment, walrus,
  `with … as`), declared `global` / `nonlocal`, or mutated in the body → per-iteration
  construction is required (this also covers per-tenant / per-database isolation).
- `open(..., "w" | "a" | "x" | "+")` (positional or `mode=`) → append-per-item is C9.1.
- Exceptions (`raise X(...)`, `…Error` / `…Exception` / `…Warning`), cheap value/scratch
  CapWords (`Counter`, `OrderedDict`, `StringIO`, `BytesIO`, `Decimal`, `Path`, `Lock`,
  `Queue`, …), file-local CapWords *functions* (`def MakeRecord`), and unbound Tier B
  results → suppress.
- Tier B scratch pattern: the bound object is filled/reset in the loop (`append`, `add`,
  `update`, `write`, attribute/item store, …) → freshness is the point → suppress.
- Comprehensions, generator expressions, lambdas and nested defs inside the body are not
  part of the loop's setup; `[re.compile(r) for r in rules]` never matches.
- Loops that run at most once (unconditional `break` / `return` / `raise`) → suppress.
- Already hoisted (`rx = re.compile(...)` or `with Session() as s:` wrapping the loop) → no match.
- `# noqa: CODE-C3.3` (or blanket `# noqa`) on the loop header or the setup line.

## Boundaries with sibling checks

- **C3.2 (#61):** any recomputed invariant expression. C3.2 skips every callee that
  `setup-cost.json` or the CapWords rule assigns to C3.3 (same import-alias resolution),
  so a `re.compile` in a loop is reported once, by C3.3.
- **C6.6 (#84):** never closing a handle. A handle opened and closed every iteration is C3.3.
- **C9.1 (#95):** unbatched per-item I/O (`requests.get(url)` per item, append-mode writes).
  C3.3 owns the *construction* of the client/session/handle that repeats per iteration.

## Fingerprint

`generateFingerprint("CODE-C3.3", kind, path, id)` with
`id = <enclosing def/class qualname>:<loop header>:<ordinal among identical headers>:<factory>:<ordinal of that factory in the loop>`.
Line numbers stay out of the hash. Nested loops report against the outermost loop whose
iterations all share the invariant arguments.

## Known limitations (v1)

- CapWords is a naming convention, not a type: Tier B can flag cheap classes not in the
  cheap list. If first-repo hit rates show noise, drop Tier B to a follow-up.
- Methods returning sessions (`env.get_template(...)`, `factory.make_client()`) are not
  resolved; only listed callees and CapWords constructors are.
- File re-reads via `Path(p).read_text()` are not matched yet.
- Python only; JS/TS `new Client()` per iteration is a follow-up.

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: An Empirical Study on Energy Smells in Python Software* (arXiv:2604.04809) — C3.S3 "Heavy initialization inside loops that could be pre-computed".
- **Taxonomy caveat:** "Wasteful when setup cost is high (objects, connections, patterns)" — hence the cost tiers; findings never quantify savings.
