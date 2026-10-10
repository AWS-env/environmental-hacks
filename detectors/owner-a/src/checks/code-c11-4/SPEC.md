# Detection Spec - CODE-C11.4 Blocking the main thread

- **Taxonomy ID:** `CODE-C11.4`
- **Owner:** A
- **Status:** Implemented (v0.1.0)

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | A call made directly in an `async def` body whose callee (resolved through import aliases) is a known synchronous blocking API |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution, no imports of client code |
| **Telemetry needed** | None |
| **Report output field** | `finding.evidence.{snippet, symbol}` + `location` (the call) |
| **False-positive risk** | Medium - short uncontended I/O can be fine synchronously; `open()` is the weakest signal |

## Signals (kind `blocking-call-in-async`, severity medium)

| Callee | Confidence |
|---|---|
| `time.sleep` | high |
| `requests.get/post/put/patch/delete/head/options/request` | high |
| method call on a name bound to `requests.Session()` in the same async def (reported as `requests.<method>`) | medium |
| `urllib.request.urlopen` | medium |
| `httpx.get/post/put/patch/delete/head/options/request` (module-level sync API) | medium |
| `subprocess.run/call/check_call/check_output` | medium |
| `os.system`, `os.popen` | medium |
| builtin `input()` | medium |
| builtin `open()` | low |

Aliases are resolved: `import time as t`, `from time import sleep`, `from urllib import request`.
Builtins `input`/`open` match only when not rebound by an import.

## Not flagged

- The same calls in a plain `def`.
- Calls inside a nested `def`, `class` or `lambda` within the async def (for example a lambda passed to
  `asyncio.to_thread` / `run_in_executor`). A nested `async def` is analysed as its own scope.
- A call that is the direct operand of `await`.
- Passing the function without calling it (`asyncio.to_thread(time.sleep, 1)`).
- `asyncio.sleep`, `httpx.AsyncClient`, `aiohttp`.
- `# noqa`, `# noqa: CODE-C11.4`, or any `# noqa: ASYNC2xx` on the call's first line.

## Fingerprint / identity

`identity = blocking-call-in-async:<qualname>:<callee>:<ordinal>` (ordinal per qualname+callee);
fingerprint = `generateFingerprint("CODE-C11.4", kind, path, "<qualname>:<callee>:<ordinal>")`. Line numbers are not hashed.

## Known limitations

- The impact is latency/throughput (event-loop stall), NOT a demonstrated energy cost. No source read shows an energy effect.
- SRC-01 barely covers concurrency (single-threaded dataset); the finding rests on tool documentation (Ruff ASYNC210/220/230/250/251, Python `asyncio.to_thread` docs).
- Wrapping blocking work in `to_thread` is not automatically a saving (thread hand-off cost; the Azure synchronous-I/O antipattern guidance notes short uncontended I/O can be fine synchronously).
- Session detection is limited to `x = requests.Session()` / `with requests.Session() as x` in the same async def; sessions from parameters, attributes or other functions are missed.
- Only direct calls are seen: a sync helper that itself blocks, called from an async def, is not traced.
- Python only; no cross-file analysis.

## Prior art / citations

- SRC-01: *Watts This Smell* (arXiv:2604.04809) and taxonomy C11.4.
- Ruff ASYNC210, ASYNC220, ASYNC230, ASYNC250, ASYNC251; Python asyncio-task docs (`asyncio.to_thread`).