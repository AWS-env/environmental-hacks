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

## Running Tests

```bash
cd detectors/owner-a
npm install
npm test
```
