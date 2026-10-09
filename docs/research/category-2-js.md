# Category 2 research: JS-01..09 + CODE-RT.2 / CODE-RT.6

Retrieved 2026-10-09 with WebFetch (summaries of official pages, so rule text is paraphrase). Anything not read from a
fetched page is marked **UNVERIFIED**. SonarJS (rules.sonarsource.com unreachable), the Semgrep registry and the AWS
CodeGuru Detector Library could not be read; no claim about them is made. CODE-RT.4 and CODE-RT.5 are deferred and not covered.

## Sources cited by the taxonomy itself (read 2026-10-09 from the workbook `Sources` sheet and `docs/taxonomy/checks.json`)

Only the workbook's own entries for these sources were read; the papers were **not** read in full for this category.

| Rows | Cited source | What the workbook says | Consequence for us |
|---|---|---|---|
| JS-01..09 | SRC-01 "Watts This Smell" (Mehditabar, Rajput, Sharma; arXiv 2604.04809, accepted ICSME 2026): 60 papers, 12 smells, 65 root causes, validated on 21,428 **Python** pairs | The rows say "SRC-01 (root cause); idiom derived", confidence Low | The JS idioms are derived by analogy from a Python-validated smell taxonomy, so none of the JS rules has direct empirical energy evidence. Our findings therefore emit no measurements and say so. |
| CODE-RT.2 | SRC-50 Python asyncio docs ("Developing with asyncio") | Official guidance on blocking calls, thread offload | Consistent with our rule (thread offload is for blocking work). |
| CODE-RT.6 | SRC-44 (compiled Python energy, Stoico et al.), SRC-36 ("It's Not Easy Being Green": language choice adds negligible energy once execution time is controlled) | SRC-36 is flagged as a counterpoint | **Anomaly:** neither source measures outdated or end-of-life runtime versions, and SRC-36 argues against language-level energy claims. CODE-RT.6 is a maintenance/support signal, not an energy finding; the finding text and README say an old runtime is not proof of measured inefficiency. |

Taxonomy `notes` that we already follow: JS-01 "check ordering/rate limits" (retry/backoff/throttle loops and dependent results are dropped), JS-03 "semantics differ (dates, undefined)" (recommendation), JS-05 "JITs may fuse some cases" (limitation), CODE-RT.2 "harmless when the wrapped work is genuinely blocking" (unknown functions are never flagged), CODE-RT.6 "upgrade risk must be managed" (recommendation says test first).

**Open questions the taxonomy attaches to these rows:** all nine JS rows carry `needs_discussion: Y (OQ-1)`: "client CI runs the profiler and uploads the output vs skip runtime confirmation". We chose the client-CI artifact route (decision D2); X-Ray is read from the client's account. OQ-11 asks to verify X-Ray against the AWS Free Plan eligible-services list; X-Ray worked in the project during the live test, but the eligibility list itself was not checked.

**Detection methods in the taxonomy that we implemented differently:**

| Row | Taxonomy method | Ours | Why |
|---|---|---|---|
| JS-01 | Async profiler | AWS X-Ray serial subsegments | A CPU profile cannot show waiting; X-Ray is existing telemetry. |
| JS-04 | Async profiler | V8 CPU profile | Sync calls block the event loop and show as CPU time on the main thread. |
| JS-06 | Memory profiler; heap snapshot | Static candidate only | A leak is not visible in a short profile; no cheap reliable runtime signal. Reported as a candidate. |
| JS-08 | Call-count tracing | V8 CPU profile | Sampling profiles give time share, not call counts; this is an approximation. |
| JS-02, JS-03, JS-05, JS-07, JS-09 | Profiler / memory profiler + AST | CPU or heap profile + AST (JS-09 AST only) | As specified, except JS-09 (V8 cannot attribute exception cost). |

Legend for "Our rule": what the detector does today; "Gap" is a difference found while comparing with the precedents.

| Check | Existing tools (fetched) | What they exempt / caveat | Our rule | Gap / action |
|---|---|---|---|---|
| JS-01 await in loop | ESLint `no-await-in-loop` (static, syntactic); typescript-eslint `await-thenable` (tangential); AWS X-Ray docs (subsegment timing). Biome/Oxlint/Klocwork only seen as search titles (UNVERIFIED). | ESLint documents legitimate serial cases: dependent iterations, retries, throttling, ordered or stream writes, bounded resources. Cannot prove independence. | Static candidate drops retry/backoff/sleep loops, `for await`, early exit, result fed into the next iteration; confirmed only by serial X-Ray subsegments. | None. Unbounded `Promise.all` caveat is in the recommendation (bounded helper such as p-limit). X-Ray serial-vs-parallel rendering is not documented on the fetched page, so we proved it on a real trace (`tests/fixtures/real/xray`). |
| JS-02 includes/indexOf in loop | unicorn `prefer-set-has`, `prefer-includes` (static). Node `--cpu-prof` docs (1 ms sampling, no call counts). | `prefer-set-has` is not loop-aware, no size threshold; exempts single-use or mutated arrays. | Loop-nested lookup, reported only when a V8 CPU profile shows the function hot. | None; the profile threshold replaces the missing size threshold. Sampling can miss small hot spots, so it corroborates rather than proves. |
| JS-03 JSON clone | unicorn `prefer-structured-clone` (also flags `_.cloneDeep`), oxc port, MDN `structuredClone`, Node heap profiler docs. | No exemptions documented; semantic differences are not on the rule pages (JSON vs structuredClone behaviour is UNVERIFIED from fetched pages). | Plain `JSON.parse(JSON.stringify(x))` only (not with replacer/space); needs a heap profile with big allocations. | `_.cloneDeep` is out of scope (taxonomy pattern is JSON). Recommendation already warns structuredClone throws on functions. `--heap-prof` default 512 KiB sampling only sees large allocations (fits). |
| JS-04 sync calls | eslint-plugin-n `no-sync`, Node "Don't block the event loop". | Rule flags any `*Sync` name; exempts root level (`allowAtRootLevel`); sync acceptable in CLIs/start-up. Guide list is dated (Node v9). | Resolved module origin (`fs`, `child_process`, `crypto`, `zlib`) not name suffix; module top level ignored; needs hot CPU profile. | **Fixed:** the fs list was narrower than the Node guide; added realpath/mkdtemp/open/read/write/close/fstat/cp/truncate/symlink/link/chmod/chown/utimes/opendir (case JS-04-13). |
| JS-05 chained iteration | react-doctor `js-combine-iterations` (third party), unicorn `no-array-reduce` (opposite stance). No official ESLint/Sonar rule found. | react-doctor: a match proves syntax only, a profile is required; rewrite must keep order, side effects and holes. | Chain of map/filter/flatMap/slice; confirmed by heap profile. | None. State clearly that evidence comes from third-party sources. |
| JS-06 missing cleanup | React docs "Synchronizing with Effects"; `exhaustive-deps` (not a cleanup rule); DeepScan and react-doctor `effect-needs-cleanup` (third party); MDN `addEventListener`; Chrome DevTools memory docs (runtime only). | `{once:true}` and `{signal}` count as cleaned; handler reference must match to remove; helper-based cleanup is a documented false-positive class; idempotent effects need none. | React effects and class components; `{once}`/`{signal}` skipped; inline handlers flagged; cleanup matched by name, returned reference treated as present; static candidate only. | Accepted limitations (documented): no handler-identity or capture-flag matching, `window.setTimeout` one-shot timers not analysed. No official rule exists for class `componentWillUnmount`, so that part has no precedent. |
| JS-07 accumulator spread | Biome `noAccumulatingSpread`, oxc `no-accumulating-spread` (official); react-doctor (third party). | Mutating the accumulator (`push`, `Object.assign(acc, ..)`) is valid; spreading a non-accumulator not flagged. ESLint `prefer-object-spread` pulls the other way. | Tracks the accumulator identity in `reduce` and loop reassignment; mutation not flagged; heap profile threshold. | None. |
| JS-08 Intl / RegExp per call | ESLint `prefer-regex-literals` (readability, not performance), MDN Intl pages (no reuse guidance found), V8 regexp tier-up blog (no cache-cost statement). | Dynamic patterns are exempt. The performance claim is **not** documented officially, so it is lower confidence. | Constant patterns only (literal or substitution-free template); new `Intl.*` formatters; needs hot CPU profile. | None; the profile requirement covers the missing official cost claim, and the limitation text says so. |
| JS-09 throw as control flow | ESLint `no-useless-catch`, `no-throw-literal` (not about control flow); V8 stack-trace API (no cost numbers). No mainstream rule. | n/a | Own static rule: throw caught in the same function; try/catch in a loop whose catch only skips. | Custom criteria, no precedent; false-positive limits come from real-repo hand-checks. V8 profiles cannot attribute exception cost (our own finding). |
| CODE-RT.2 thread hop | Python docs (`to_thread` is for blocking IO; GIL); Ruff ASYNC rules and flake8-async only flag the opposite (blocking calls that need offloading). | No tool flags needless offload, so no precedent. | Immediately awaited hop around a call-free lambda, cheap builtin, or a coroutine function; unknown functions never flagged. | None; deliberately conservative. |
| CODE-RT.6 outdated runtime | Node Release WG `schedule.json` (official), endoflife.date (third party), Python devguide (official), AWS Lambda runtimes page (official). | Lambda deprecation differs from upstream EOL by months in both directions; dates are projections; devguide gives month-only future EOL. | Bundled dated table (`config/runtime_support.json`), compared at a declared `reference_date`; Lambda dates for Lambda config. | Checked all Node 18-26, Python 3.9-3.14 and Lambda rows against the fetched pages: they match. **Fixed:** added Python 3.15 (released 2026-10-09; EOL month-end 2031-10-31, devguide gives only the month). |

## Real-repo hand-checks (2026-10-09, shallow clones of express, axios, fastify, react-redux-realworld-example-app, undici)

Static checks only (artifact-confirmed checks correctly reported `unavailable` without profiles).

| Repo | CODE-RT.6 | JS-06 | JS-09 | Result of hand-check |
|---|---|---|---|---|
| express | 9 | 0 | 0 | All true: Node 16/17 in `legacy.yml`; 18, 19, 20, 21, 23, 25 in the `ci.yml` matrix; `engines >= 18` flagged at low confidence. |
| axios | 20 | 0 | 0 | All true: 4 EOL versions in each `[12,14,16,18]` matrix, 1 in each `[20,22,24,26]`. |
| fastify | 1 | 0 | 0 | True: `node-version: '20'`. |
| react-redux-realworld-example-app | 0 | 0 | 0 | Nothing to flag. |
| undici | 4 | 0 | 9 -> 0 | JS-09 had 9 false positives (below). RT.6 hits true. |

**False positives found and fixed (JS-09, undici):** 6 `throw-in-try` hits were shared error handling (a throw next to awaited
I/O inside one `try`, `catch` calls `socket.destroy(err)`), and 3 `try-skip-in-loop` hits were empty catches that isolate
callbacks. Rule now needs a try body with no other call/await/new, and a catch that explicitly `continue`s. Cases JS-09-03
(flipped to a negative), JS-09-11, JS-09-12 added.

**Parser gap:** `undici/lib/core/request.js` is valid JS (class fields without semicolons: `#abort` then `[kResume] = null`)
that tree-sitter-javascript 0.25 rejects. The file is reported as `partial` coverage, never as clean.

**Judgement point (CODE-RT.6):** libraries such as express and axios test EOL Node versions on purpose to verify
compatibility. The finding is factually correct, but for a library CI matrix it describes a support range, not a runtime
the product runs on. Not changed; listed for the owner.

## Honest limits

- Official documentation for SonarJS, Semgrep and CodeGuru was not reachable, so we do not claim parity with them.
- JS-05, JS-07 and the performance claim in JS-08 rest on Biome, oxc and react-doctor (the last is third party).
- The cost of a throw (JS-09) and of rebuilding Intl formatters/RegExps is not quantified by any fetched official page.
