# Owner A Static Detectors

This package contains lightweight, read-only static code detectors for **Owner A** in the **Software Compute Waste Taxonomy** (Layer: Code — algorithmic efficiency, CPU, memory, garbage collection, unnecessary imports).

## Guiding Principles

1. **Do not create compute waste to detect compute waste:** Fast, single-pass AST traversal using tree-sitter.
2. **Never execute user code:** All checks run statically without loading modules or invoking scripts.
3. **Evidence-backed findings:** Every finding includes exact line coordinates, offending code snippet, rationale, limitations, and copyable context for coding agents.

## Implemented Checks

- **`CODE-C1.1`**: Dead code / unused results (#34)
  - `unused-import`: Heavy and light unused module and binding imports in Python.
  - `unreachable-code`: Dead code after terminal statements (`return`, `raise`, `break`, `continue`) and constant-false conditionals (`if False:`, `while 0:`).
- **`CODE-C3.1`**: Inefficient iteration construct, static half (#60)
  - `inefficient-iteration-construct`: `range(len())` indexing loops, manual-index `while`
    loops, append-accumulation loops (gated or ungated) convertible to comprehensions,
    and dict key loops with `d[k]` lookups convertible to `.items()`. Python only;
    Low severity, unconfirmed without profiler evidence (R2 follow-up).
- **`CODE-C3.2`**: Recomputing loop-invariant (#61)
  - `loop-invariant-recomputation`: Invariant calls (S1), attribute/subscript chains (S2), and binary arithmetic (S3) recomputed inside `for` and `while` loops.
- **`CODE-C3.3`**: Inefficient per-iteration setup (#62)
  - `per-iteration-setup`: Pattern/template compiles, connections/sessions/clients/pools, read-mode file opens (Tier A, `src/core/setup-cost.json`) and CapWords construction (Tier B) with loop-invariant arguments, resolved through the file's imports.

## Shared contract v1

`evaluate(input)` (`src/contract.ts`) is the entry point for the shared detector contract
(`docs/DETECTOR_CONTRACT.md`). It takes a contract v1 `input` payload, runs the check named by
`check_id` (see `src/registry.ts`) over each scope's static Python sources and returns a
contract `result`:

- `completed` only when every requested scope was parsed and evaluated. Syntax errors,
  missing or non-Python sources, ambiguous line separators, unsupported checks and
  `detector_version` mismatches yield `partial` / `unavailable` with a reason in
  `coverage.limitations`; a throwing check yields `error`.
- Findings carry the contract fingerprint `sha256([repository_id, check_id, scope_id, identity])`,
  a line-free semantic `identity` supplied by the check, HTTP references, and static evidence
  quoting the exact source lines (up to 10).
- No measurements: owner-a checks are static and never report runtime or environmental values.

Contract pairs are checked against the shared Python validator in `test/contract.test.ts`
(needs Python 3.12+ with `shared/contracts/requirements.txt`; set `CONTRACT_PYTHON` to pick the
interpreter). Locally the pair tests skip when the validator is missing; CI sets
`REQUIRE_CONTRACT_VALIDATOR=1` so they fail instead.

## Running Tests

```bash
cd detectors/owner-a
npm install
npm test
```
