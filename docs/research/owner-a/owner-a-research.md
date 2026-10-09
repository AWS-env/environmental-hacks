# Owner A - research (Gate 2 document, merged from the five part files)

Scope: 65 checks CODE-C1.1 .. C12.4, Python-first, AWS profile `shrey`, Region `ap-south-1`.
Detail, URLs and caveats per check: `research/part-1..5-*.md`. Rule: a URL counts only if the research agent fetched it; everything else is UNVERIFIED. I read all five parts and verified two claims myself (SRC-01 title/numbers; Python `re` cache).

## 1. Sources cited by the taxonomy
Every one of the 65 rows cites only **SRC-01**: "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells" (Mehditabar, Rajput, Sharma; arXiv 2604.04809; accepted ICSME 2026 per the workbook's verification note).
- Supports: the 12 smells (C1..C12), 65 root-cause names, and the Table I definitions (they match the workbook "meaning" column).
- Does NOT support: any detection method. The paper has no detection tool (it lists a detector as future work). It profiled 21,000+ Python pairs but an LLM classified only the top 3,000. So every "detection signal / AWS mapping" cell is the workbook authors' own claim.
- Not observed in its Python data: C6.6, C6.7, C8.4, C12.4 (absent), concurrency (C11) barely covered (single-threaded competitive-programming code).
- Numbers are category-level only (e.g. C6 median 3,670 J, C5 3,989 J, C3.S7 N=11). None is a per-fix guarantee, and none justifies energy claims about a client's repo.

## 2. Final verdict per check
Verdicts: **BUILT** = already in `detectors/owner-a` (origin/main); **STATIC** = we write an AST rule; **STATIC+ART** = static candidate, confirmed by a client-CI artifact; **INFO** = ships as informational/low severity (D3); **HEURISTIC** = low-confidence candidate (narrow, D2); **DROP** = dropped (D1) with reason. "(proposed)" = my suggestion, not yet confirmed by the owner.

| Check | Existing tools (fetched by agents) | Our verdict | Why / gap |
|---|---|---|---|
| C1.1 Dead code/unused | Ruff F401/F841, Pylint W0611/W0101, Vulture, CodeQL | BUILT | Add import-cost ranking; plain F401 is style |
| C1.2 Redundant assignment | Ruff PLW0127, CodeQL py/redundant-assignment | INFO | Self-assign covered; dead-store has no fetched tool; negligible energy |
| C1.3 Redundant control flow | Ruff PLR1711/SIM114/PIE790 | INFO | Style-level |
| C1.4 Repeated computation | perflint W8201 (beta) | STATIC (narrow) (proposed) | In-loop overlaps C3.2; straight-line duplication has no tool |
| C1.5 Resultless computation | Ruff B018, CodeQL ineffectual-statement | INFO + STATIC (pure builtin call result discarded) (proposed) | Calls with discarded results not covered |
| C1.6 Unnecessary initialization | none | STATIC (proposed): costly init before early exit | No tool; needs path analysis |
| C1.7 Unnecessary variable | Ruff RET504 | INFO | Taxonomy itself says below noise |
| C1.8 Excessive runtime mgmt | none | STATIC (gc.collect / logging in loop) (proposed) | No tool; honour `isEnabledFor` guards |
| C2.1 Unnecessary delegation | Pylint W0246, Ruff PLW0108 | STATIC (single-call wrapper) (proposed) | Existing rules cover super-delegation/lambdas only |
| C2.2 Missing static declaration | Ruff PLR6301 (preview) | INFO | Style; no profiler needed |
| C2.3 Excessive modularization | none | HEURISTIC / defer (proposed) | Taxonomy says hard to automate safely |
| C3.1 Inefficient iteration construct | Ruff PERF401/402/102/101, PLC0206, Pylint C0200 | BUILT | Add PERF102/101-style shapes (optional); Ruff calls them negligible -> low severity |
| C3.2 Recomputing loop-invariant | perflint W8201 (beta) | BUILT + FIX #303 | Nondeterministic SyntaxNode identity bug |
| C3.3 Per-iteration setup | none (PERF203 py<3.11) | BUILT + FIX severity | `re` caches compiled patterns (verified) -> `re.compile` must not be medium/high |
| C3.4 Nested iteration | none | HEURISTIC (narrow, low confidence) | Not statically decidable (D2) |
| C3.5 Missing early exit | Ruff SIM110, C419, Pylint R1729 | BUILT | Spec stricter than tools, covers flag-store loops |
| C3.6 Unfiltered bulk iteration | Ruff C419, RUF015 | BUILT | Spec not compared by agent (agent read a stale local tree) |
| C3.7 Array mutation | Ruff B909 (preview), Pylint W4701 | BUILT; review mismatch | SRC-01's example is `pop()` in a sieve (algorithm choice), not mutation during iteration |
| C4.1 Short-circuit ordering | none | defer (proposed) | Needs purity model |
| C4.2 Redundant conditional | Pylint W0125/R0124, CodeQL | INFO | Covered; loop-invariant condition is the only gap |
| C4.3 Conditional nesting | Ruff PLR1702/SIM102 | INFO | Readability only |
| C4.4 Missing else-if | none | defer (proposed) | Needs mutual-exclusion proof |
| C4.5 Expensive comparison | Ruff E721, Pylint C0123 (wrong target) | INFO | Tools detect type-check idiom, not reflection cost |
| C4.6 Expensive exception flow | Ruff PERF203 (py<3.11) | STATIC+ART (proposed) | Needs exception frequency evidence |
| C4.7 Missing edge-case guards | none | defer (proposed) | Needs input data |
| C4.8 Non-idiomatic condition | Ruff E712, Pylint C0121 | INFO | No runtime claim |
| C5.1 Structure choice | Ruff PLR6201 (preview), Pylint R6201 (ext) | STATIC | Real gap: list-name membership inside loops |
| C5.2 Missing helper type | none (B019/W1518 point the other way) | HEURISTIC; never advise `@cache` on methods | |
| C5.3 Over-provisioned type | Ruff SLOT000 (narrow) | defer / STATIC+ART (proposed) | Needs heap evidence |
| C5.4 Unnecessary representation | Ruff C414/C413/C416 | STATIC (prefer reusing Ruff findings) | Mostly duplicative |
| C6.1 Unnecessary object | memray temporary-allocations | STATIC+ART | Runtime only (D4) |
| C6.2 Unnecessary copying | Refurb FURB185, perflint W8204 | STATIC (narrow) + ART | FURB145/123 are style, not waste |
| C6.3 Materialization | Ruff C419, Pylint R1729 | STATIC | Good coverage; weight by size |
| C6.4 Oversized retention | Ruff B019; memray --leaks | STATIC (B019-style) + ART | |
| C6.5 Over-allocation | none | **DROP** (D1) | Not detectable: needed size unknowable |
| C6.6 Leaked handles | Ruff SIM115, Pylint R1732, CodeQL file-not-closed | STATIC | Reliability finding, low energy weight |
| C6.7 Mutable defaults | Ruff B006, Pylint W0102, CodeQL | STATIC | Flag only when the default is mutated |
| C7.1 Algorithm choice | bigopy (alpha) | HEURISTIC narrow (D2) | Static big-O is partial |
| C7.2 Decomposition | none | **DROP** (D1) | |
| C7.3 Avoidable recursion | none | HEURISTIC narrow (D2) | |
| C7.4 Operation ordering | none | **DROP** (D1) | Same as C4.1 |
| C7.5 Unsimplified operation | Ruff FURB192 | STATIC (FURB192-type) | Genuine O(n log n)->O(n) |
| C8.1 Memoization | none | **DROP** (D1) | cProfile has no arguments; hit rate unobtainable |
| C8.2 Derived-value reuse | none (perflint W8201 generic) | STATIC low-priority | `re` cache makes regex gain small |
| C8.3 Local lookup caching | perflint W8202/W8205 | **DROP** (D1) | Micro, no energy evidence |
| C8.4 Redundant fetching | none | STATIC+ART (X-Ray repeated downstream calls) | Not observed in SRC-01 |
| C9.1 Fragmented I/O | loophound (early-stage), nplusone (runtime) | STATIC + ART (D7: needs discussion) | Own rule; do not depend on loophound |
| C9.2 Oversized retrieval | none verified | STATIC low-confidence + ART | |
| C9.3 N+1 | loophound, nplusone | STATIC candidate + ART | Highest-value in C9 |
| C9.4 Expensive query | none | HEURISTIC (SQL string patterns only) | Real cost needs EXPLAIN (other owners) |
| C10.1-C10.5 Primitives | Ruff SIM110, PERF401/403/102, FURB113/129/192, C419, perflint | INFO (micro tier); C10.4 STATIC (`+=` str in loop) | Ruff calls most negligible; C10.3 needs an energy-validation gate (SRC-01: NumPy vectorisation raised energy up to +64%) |
| C10.6 Operator overloads | none | **DROP** (D1) | Needs type inference |
| C11.1 Lock contention | none | HEURISTIC / defer | py-spy/Scalene docs do not claim lock detection |
| C11.2 Serial bottleneck | none | HEURISTIC (`max_workers=1`) | Often intentional |
| C11.3 Leaked threads | Ruff RUF006 (different smell) | HEURISTIC; reliability-first | |
| C11.4 Blocking main thread | Ruff ASYNC210/220/230/250/251 | STATIC | Taxonomy said hard; `async def` subset is easy |
| C11.5 Missed parallelism | none | STATIC (low) + ART (X-Ray sibling subsegments) | "Wall-time opportunity", no energy claim |
| C12.1-C12.3 Hardware locality | perf stat (process-level only) | **DROP** (D1) | Counters unavailable on AWS VMs; no code location |
| C12.4 Array declaration | none | **DROP** (D1) | N/A for Python; absent from SRC-01 data |

Dropped: 10 checks (C6.5, C7.2, C7.4, C8.1, C8.3, C10.6, C12.1, C12.2, C12.3, C12.4). 55 remain. Several "(proposed)" rows are my suggestion and still need your confirmation.

## 3. Deviations from the taxonomy
1. SRC-01 gives no detection methods; every detection cell is ours to check.
2. The workbook says "empirically profiled on 21,428 pairs"; true for profiling, but only 3,000 were LLM-classified.
3. C11.4 "hard statically" is wrong for the `async def` subset; C6.7 needs AST not a memory profiler; C8.1's "validate hit rate" cannot be met from cProfile; C11.x "py-spy parser" overstates what py-spy output shows; C4.5 and C11.3 nearest tools detect different smells.
4. C3.3 `re.compile`: severity too high (verified `re` caching).
5. C3.7: the paper's example is algorithm choice, not iteration mutation.
6. C9 marked "N" but its signals are runtime -> moved to needs-discussion (D7).
7. SPEC.md files cite SRC-01 under a wrong title ("An Empirical Study ... Python"); correct title is the taxonomy one.
8. The agents read the stale local tree (no SPEC for C3.6/C3.7); `origin/main` has those specs. Re-compare at P4.

## 4. AWS side (read-only, `shrey`, ap-south-1) - agent-reported unless marked
- Plan FREE, 120 USD credits, expires 2027-04-08 (verified by me). Lambda, S3, EventBridge, SQS, CloudWatch Logs, X-Ray, CloudFormation are Free-Tier-listed. Lambda concurrency limit 10 project-wide -> queue fan-out.
- Account already hosts other owners' stacks and the shared `findings-hub`; nothing for owner A yet (my inventory).
- Runtime evidence from X-Ray: Lambda sampling fixed (~1 req/s + 5%); subsegment-level evidence needs client instrumentation. CodeGuru Profiler (Python) is a possible client-side source (status UNVERIFIED).
- Lambda: 250 MB unzipped, arm64 ok; npm tree-sitter 0.25.1 has a linux-arm64 prebuild; grammar-package arm64 builds UNVERIFIED -> must be proven in P3. Regional presign gives `<bucket>.s3.ap-south-1.amazonaws.com` (local check).
- Artifact advice: CI uploads `memray stats --json` / `memray transform csv` and text exports, not raw `.bin`, tracemalloc dumps or `.prof` (opaque / version-specific; unpickling client files is a risk).

## 5. Honest limits
Sonar rules site unreachable for every agent (all Sonar claims UNVERIFIED). Most Semgrep registry rules and CodeQL queries not opened. Pylint pages needed an offset hack. WebFetch summarises with a small model, so quotes are not byte-verified. Last ~11k chars of SRC-01 HTML unread. memray/Scalene JSON key names, cProfile layout, Lambda PMU availability, Semgrep size on Lambda: UNVERIFIED. No energy was measured anywhere; REAL/MICRO/STYLE labels rest on the tools' own docs.
