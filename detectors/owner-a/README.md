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
- **`CODE-C1.2`**: Redundant assignment (#35)
  - `self-assignment`: Statements that assign a target to itself (`x = x`, `a, b = a, b`, `self.n = self.n`, `row[i] = row[i]`); class bodies are skipped.
  - `dead-store`: A function-local `x = …` overwritten by a later `x = …` in the same block before any read; closures, `global`/`nonlocal`, frame introspection, `break`/`continue` and `try`/`with` exception paths are guarded.
- **`CODE-C3.1`**: Inefficient iteration construct, static half (#60)
  - `inefficient-iteration-construct`: `range(len())` indexing loops, manual-index `while`
    loops, append-accumulation loops (gated or ungated) convertible to comprehensions,
    and dict key loops with `d[k]` lookups convertible to `.items()`. Python only;
    Low severity, unconfirmed without profiler evidence (R2 follow-up).
- **`CODE-C3.2`**: Recomputing loop-invariant (#61)
  - `loop-invariant-recomputation`: Invariant calls (S1), attribute/subscript chains (S2), and binary arithmetic (S3) recomputed inside `for` and `while` loops.
- **`CODE-C3.3`**: Inefficient per-iteration setup (#62)
  - `per-iteration-setup`: Pattern/template compiles, connections/sessions/clients/pools, read-mode file opens (Tier A, `src/core/setup-cost.json`) and CapWords construction (Tier B) with loop-invariant arguments, resolved through the file's imports.
- **`CODE-C1.6`**: Unnecessary initialization (#39)
  - `overwritten-init` / `init-before-early-exit`: A function-local allocation, call or Tier A setup (`src/core/setup-cost.json`) that every `if`/`elif`/`else` arm overwrites before reading (S1), or that runs before a guard which returns/raises/continues/breaks without using it (S2).
- **`CODE-C3.5`**: Missing loop early exit (#64)
  - `missing-early-exit`: Loops that set a sticky flag (S1), store a match (S2) or store-then-return (S3) without `break`/`return`, so they keep scanning after the result is decided.
- **`CODE-C3.6`**: Unfiltered bulk iteration (#65)
  - `eager-then-prefix` / `eager-then-early-exit` / `eager-then-short-circuit`: An eager producer (list comprehension, `list(map/filter/…)`, `readlines()`) whose only consumer reads a prefix (S1), a loop that exits early (S2), or `any`/`all`/`in` (S3).

- **`CODE-C3.7`**: Inefficient array mutation (#66)
  - `mutate-during-iteration`: A `for` loop that removes from (S1), grows (S3, Low) or clears / slice-stores (S4) the collection it iterates.
  - `front-reindex-in-loop`: `pop(0)` / `insert(0, …)` / `del x[0]` on a list inside any loop (S2, O(n²)); `collections.deque` bindings are skipped.
- **`CODE-C1.3`**: Redundant control flow (#36)
  - `identical-branches`: `if`/`elif`/`else` chains whose arms all run the same code, and `v if c else v`.
  - `empty-branch`: `if` statements whose arms are all `pass`/`...`, and empty trailing `elif` arms, so a condition is evaluated with nothing depending on it.
- **`CODE-C5.1`**: Inefficient structure choice (#75)
  - `list-membership-in-loop`: `x in NAME` / `x not in NAME` inside a loop or comprehension where `NAME` is visibly bound to a list or tuple, so every test scans the list (O(n*m)). Skipped when the loop is statically small (8 or fewer items), the collection is mutated in the loop, or the binding is unknown. Medium severity; a set built once before the loop is the usual fix.
- **`CODE-C10.4`**: Inefficient string concatenation (#45)
  - `string-concat-in-loop`: `s += x` / `s = s + x` on a string accumulator that the loop only appends to. Skipped when the loop reads the string (it is consumed every iteration anyway) or runs at most once. Low severity; local-name targets are low confidence because CPython can sometimes resize a string in place (unverified).
- **`CODE-C6.7`**: Leaking mutable defaults (#85)
  - `mutable-default-mutated`: a list/dict/set default argument that the function body mutates, so state persists across calls. Narrower than Ruff B006 / Pylint W0102: a default that is never mutated is not reported.
- **`CODE-C6.6`**: Leaked resource handles (#84)
  - `unclosed-handle`: a file/socket/connection opened into a local name that is neither context-managed, closed in `finally`, nor handed on. Reliability finding with low energy weight.

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

## AWS deployment (ap-south-1)

`src/aws/handler.ts` is the Lambda entry point (`owner-a-static-scan`, Node 22, arm64): it takes a contract v1 `input` (or `{ "input": ..., "dry_run": true }`),
runs `evaluate`, and publishes the result to the `findings-hub` bus as `detector.result.v1` (it describes the bus first and fails the invocation on any publish problem).
The template is `cdk/owner-a/owner-a-detectors.yaml` (names `owner-a-*`, tag `owner=A`, private S3, 7-day logs, DLQ with an alarm).

```bash
./scripts/build-owner-a-lambda.sh      # prints cdk/owner-a/build/owner-a-static-scan-<sha>.zip (linux-arm64 tree-sitter prebuilds)
aws s3 cp <zip> s3://owner-a-deploy-<account>-ap-south-1/
aws cloudformation deploy --stack-name owner-a-detectors --template-file cdk/owner-a/owner-a-detectors.yaml \
  --capabilities CAPABILITY_NAMED_IAM --tags owner=A project=environmental-hacks \
  --parameter-overrides CodeBucket=owner-a-deploy-<account>-ap-south-1 CodeKey=<zip name>
```

Inline `detector.result.v1` events are stored by the owner-d writer with evidence level `unverified` (no input travels with the event); pointer events
(`DetectorResultPointer.v1`) need the results bucket on the writer's allow-list.
