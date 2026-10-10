# Owner A - Gate 1 summary (research) - DRAFT for discussion

Scope: 65 checks (CODE-C1.1 .. C12.4), Python-first, AWS profile `shrey`, Region `ap-south-1`.
Detail per check: `research/part-1..5-*.md` (written by five research agents; each URL there was fetched by the agent, everything else is marked UNVERIFIED). I spot-checked two claims myself (below); I have NOT re-read every row.

## Verified by me (fetched)
- SRC-01 is "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells" (Mehditabar, Rajput, Sharma). 60 papers, 12 smells, 65 root causes (55 mapped to real code). 21,000+ Python pairs were **profiled**, but only the top **3,000** were **classified by an LLM pipeline**. It names **no detection tool**. So the workbook's "empirically profiled on 21,428 pairs" is true for profiling only, and every per-row "detection signal" is the taxonomy's own claim.
- Python docs: the `re` module caches compiled patterns (https://docs.python.org/3/library/re.html). So CODE-C3.3's `re.compile`-in-loop is a weak waste claim; owner A's existing C3.3 rates it medium/high-confidence, which is too strong -> candidate fix.
- AWS: plan FREE, ACTIVE, 120 USD credits, expires 2027-04-08 (`aws freetier get-account-plan-state --profile shrey`).

## Agent-reported (not re-verified by me)
Lambda, S3, EventBridge, SQS, CloudWatch Logs, X-Ray, CloudFormation are Free-Tier-listed; account Lambda concurrency is 10; zip 50 MB / 250 MB unzipped; arm64 ok; tree-sitter Python wheel has cp313 aarch64; regional presign gives `<bucket>.s3.ap-south-1.amazonaws.com`; X-Ray sampling on Lambda is fixed (~1 req/s + 5%).

## Proposed buckets (from the five reports)
| Bucket | Checks | Note |
| --- | --- | --- |
| A. Static, existing tools overlap (we add precision/energy framing) | C1.1, C3.1, C3.5, C3.6, C5.1, C5.4, C6.3, C6.6, C6.7, C11.4, C10.1-C10.5 (partly) | Ruff/Pylint/CodeQL cover parts; Ruff itself calls many PERF rules negligible -> severity must stay low |
| B. Static, we must build the rule (no tool found) | C1.4, C1.6, C1.8, C2.1, C2.3, C3.2 (beta perflint only), C3.3, C3.4, C3.7 (partial), C4.1, C4.4, C4.7, C5.2, C5.3, C8.2, C8.4, C9.x (N+1 static is early-stage only) | highest false-positive risk; each needs not_wasteful_when |
| C. Mostly style, low energy evidence | C1.2, C1.3, C1.5, C1.7, C2.2, C4.2, C4.3, C4.5, C4.8, C10.x | candidates for "informational" severity or drop |
| D. Runtime artifact or telemetry needed (OQ-1) | C6.1, C6.4, C11.1-C11.3, C11.5 (X-Ray can show sequential subsegments), C2.x/C3.x confirmation | client CI uploads `memray stats --json` / csv, not raw dumps |
| E. Not detectable / drop or mark unavailable | C6.5, C8.1, C8.3, C7.2, C7.4, C10.6, C12.1-C12.3 (perf counters, OQ-4), C12.4 (N/A for Python) | say "unavailable", never "clean" |
| F. Judgement (OQ-8) | C7.1, C7.3, C7.5, C3.4 | only structural patterns (e.g. Ruff FURB192-type) are honest |

## Existing owner-A code (7 checks: C1.1, C3.1, C3.2, C3.3, C3.5, C3.6, C3.7)
Specs are stricter than existing tools (good). Known issues: #303 nondeterministic C3.2; #290 contract v1 + CI; C3.3 `re.compile` severity too high; specs exist only for C1.1/C3.1/C3.2/C3.3/C3.5 (the agent could not compare C3.6/C3.7).

## Honest limits
Sonar rules site unreachable (DNS) for every agent -> all Sonar rules UNVERIFIED. Semgrep registry rules mostly UNVERIFIED. WebFetch summarises pages with a small model, so quotes are not byte-verified. Last ~11k chars of the SRC-01 HTML unread. memray JSON keys, Scalene schema, cProfile layout UNVERIFIED. Semgrep size/arm64 on Lambda not researched.

## Questions for the owner (MCQ at Gate 1)
1. Style-only checks (bucket C): ship as informational, or drop?
2. Not-detectable checks (bucket E): mark "unavailable" in the report, or drop from v1?
3. Runtime checks (bucket D, OQ-1): client-CI artifact upload (memray/cProfile) or static candidates only?
4. Engine for new static rules: keep owner A's TypeScript + tree-sitter, or add Semgrep/Ruff-based rules?
