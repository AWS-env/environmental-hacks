# Detection Spec — CODE-C1.1 Dead code / unused results

- **Taxonomy ID:** `CODE-C1.1`
- **Issue:** #34
- **Owner:** A
- **Layer:** Code (algo/CPU/mem/GC)
- **Rule:** R1
- **Status:** Implemented

## Detection Specification

| Field | Value |
|---|---|
| **Detection signal** | Import binding with zero references in its module (after false-positive guards); statements after `return`/`raise`/`break`/`continue` or under constant-false conditions (`if False:`, `while 0:`) |
| **Detection tool** | tree-sitter Python grammar in `owner-a-static-scan`; purely static, no code execution |
| **Telemetry needed** | None required for static detection. Optional runtime enhancement: client-CI `python -X importtime` profile artifact |
| **Report output field** | `finding.evidence.{symbol, module, costTier}` + `location` |
| **False-positive risk** | Medium — side-effect imports, dynamic reflection (`globals()`, `getattr()`), test framework injection |
| **Detectable** | High (H) for Python imports and dead blocks |
| **Measurable** | Low (L) statically; Medium (M) with importtime measurement artifact |

## Scope & Signals

- **S1 Unused Import:**
  - Python executes top-level module code on import; unused heavy imports (e.g. `pandas`, `torch`, `scipy`) incur substantial startup latency, CPU cycles, and memory allocation.
  - Guarded against `__future__`, `if TYPE_CHECKING:`, `try/except ImportError`, `__init__.py` re-exports, `__all__` definitions, wildcard imports, string type annotations, and `# noqa: F401`.
- **S2 Unused Result Binding:**
  - Deferred to `CODE-C1.5` (#38) which shares side-effect analysis.
- **S3 Unreachable Code:**
  - Unreachable statements following terminal statements (`return`, `raise`, `break`, `continue`) or within constant-false blocks (`if False:`, `while 0:`).

## Evidence & Citations

- **Source:** SRC-01: *Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells* (arXiv:2604.04809).
- **Finding:** Removing unused heavy imports demonstrated significant measurable reductions in process energy and cold-start execution duration.
