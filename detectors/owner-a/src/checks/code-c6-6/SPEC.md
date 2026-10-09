# Detection Spec — CODE-C6.6 Leaked resource handles

- **Taxonomy ID:** `CODE-C6.6`
- **Issue:** #84
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | A handle-producing call bound to a plain local name inside a function, with no `with` ownership, no close in a `finally:`, and no escape; or the chained form `open(p).read()` |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; no execution, no imports of client code |
| **Telemetry needed** | None (R1). No profiler artifact, no OQ gate |
| **Report output field** | `finding.evidence.{symbol, factory, suggested}` + `location` (the assignment or chained statement) |
| **False-positive risk** | Low-medium — ownership transfer through helper calls is treated as escape, but a helper that never closes is not followed |
| **Detectable** | H (syntactic) |
| **Measurable** | L statically — descriptor lifetime is not observable without runtime data |

## Handle-producing callees

Resolved through import aliases (`collectImportAliases` / `resolveCallee`):
`open`, `io.open`, `codecs.open`, `gzip.open`, `bz2.open`, `lzma.open`, `tarfile.open`,
`zipfile.ZipFile`, `tempfile.TemporaryFile`, `tempfile.NamedTemporaryFile`,
`tempfile.SpooledTemporaryFile`, `socket.socket`, `socket.create_connection`, `sqlite3.connect`,
`urllib.request.urlopen`, `subprocess.Popen`. `from os import open` resolves to `os.open` and is not flagged.

## Signals (one finding per site, kind `unclosed-handle`, severity low)

| Shape | Confidence |
|---|---|
| `f = open(p)` in a function; never closed, not escaping | medium |
| same, but `f.close()` exists only outside `finally:` (not exception-safe) | low |
| chained `open(p).read()` / `.write(...)` with no `with` (not for Popen, not `.close()`) | low |

`Popen` accepts `close/terminate/kill/wait/communicate` as release.

## False-positive guards (negatives)

- The call is a `with` item, including `with closing(...)` and `ExitStack().enter_context(...)`
  (the call is not assigned directly), and `f = open(p)` followed by `with f:`.
- Some `NAME.close()` (or Popen release call) sits inside a `finally:` clause of the function.
- The handle escapes: returned, yielded, assigned to an attribute / subscript / another name,
  stored in a tuple/list/dict, passed as an argument to another call (ownership transferred),
  or referenced from a nested `def` / `lambda`.
- Module-level or class-level handles (functions only), `sys.stdin` / `sys.stdout` (not calls).
- `# noqa`, `# noqa: CODE-C6.6`, `# noqa: SIM115` on the statement line or the call line.

## Fingerprint

`generateFingerprint("CODE-C6.6", "unclosed-handle", path, id)` with
`id = <enclosing qualname>:<name or "(chained)">:<resolved callee>:<ordinal per key>`;
contract identity is `unclosed-handle:<id>`. Line numbers stay out of the hash.

## Known limitations (v1)

- Name-based, flow-insensitive: any close in a `finally` in the function clears the name, even
  one belonging to a different `try`; all uses of the same name in the function are pooled.
- Escape via any call argument is assumed to transfer ownership, even when the callee does not close.
- Only plain-identifier targets are tracked (no tuple unpacking, walrus, chained assignment).
- SRC-01 did **not** observe this smell in its Python data. This is a reliability finding with low
  energy weight, never a quantified saving. CPython reference counting usually closes a dropped
  file object immediately (the author's reasoning, not a cited source).
- Memory profilers do not track file descriptors, so no profile artifact corroborates it.

## Evidence & Citations

- **Source:** SRC-01 *Watts This Smell* (arXiv:2604.04809); taxonomy C6.6.
- **Prior art (fetched during research):** Ruff SIM115, Pylint R1732 `consider-using-with`,
  CodeQL `py/file-not-closed`.
