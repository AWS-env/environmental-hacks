# Category 4 (Frontend, FE-01..21) - research

Owner: C. Decisions: [`category-4-decisions.md`](category-4-decisions.md). Status: first pass. Sources are official
Chrome/web.dev pages fetched on 2026-10-09; items marked UNVERIFIED were not confirmed from a primary page.
FE-05 does not exist in the taxonomy (19 issues, not 21).

## Sources cited by the taxonomy (playbook P1a; workbook sheet `Sources`, read 2026-10-10)

Every FE row cites one of SRC-09..13 or SRC-28 (or none). All are marked "No device-energy evidence" in the workbook itself: they measure bytes, CPU and latency, not energy. The workbook's own verification (2026-10-08) marked SRC-09 "URL MOVED" (canonical slug `/articles/browser-level-image-lazy-loading`).

| Source | Checks citing it | Read by us | Supports the row as worded? |
| --- | --- | --- | --- |
| SRC-09 web.dev image lazy loading | FE-01, 02, 03, 04, 06 | Yes (canonical URL fetched) | Yes: native `loading=lazy`, don't lazy-load LCP, width/height, `opacity:0` still loads, libraries only for old browsers. |
| SRC-10 web.dev code splitting | FE-07, 08, 09, **FE-10** | Yes | FE-07/08/09 yes (route/component splitting, dynamic `import()`, vendor splitting). **FE-10 (CSR delays LCP discovery) is not about code splitting; the source does not support that row.** |
| SRC-11 web.dev defer non-critical CSS | FE-11, 12, 13, 14 | Yes | FE-11, 13 yes (inline critical CSS via Coverage, defer the rest). FE-12 (large stylesheets) and FE-14 (moving the link to the bottom) are not stated by the page. |
| SRC-12 web.dev layout thrashing | FE-15, 16 | Yes | FE-15 yes (batch reads then writes). FE-16 only indirectly (layout cost rises with DOM size; no DOM-size threshold). |
| SRC-13 web.dev compositor-only properties | FE-17, 18, **FE-19** | Yes | FE-17, 18 yes. **FE-19 (list virtualization) cites it as "(DOM size)", but the page does not mention DOM size or lists.** Workbook notes the page was last updated 2015. |
| SRC-28 AWS Well-Architected Sustainability | **FE-21** | Yes (fetched two BP pages) | **No.** SRC-28 names SUS02-BP02 and SUS03-BP01; SUS03-BP01 is "Optimize software and architecture for asynchronous and scheduled jobs" and says nothing about end-user devices. The device-efficiency practice is **SUS03-BP04** "Optimize impact on devices and equipment" (fetched): run efficient apps on devices, do computationally intense work server-side, segment and paginate output. |
| none | FE-20 | n/a | No source; taxonomy marks it a hypothesis (React ecosystem guidance). |

Open Questions sheet: OQ-5 "Frontend execution location: our CodeBuild vs client CI; React interaction replay for FE-20" (affects all FE-*, FE-20). Our answer: client CI, never our execution (D2).

### Deviations from the taxonomy (each is a decision row)
- Execution: taxonomy names CodeBuild `owner-c-lighthouse` / `owner-c-bundle`; we use client-CI artifacts (D2).
- Static detectors for FE-03, 04, 17 where the taxonomy only lists Lighthouse/bundle (D1, D7).
- FE-21 evidence basis is SUS03-BP04, not the cited SUS03-BP01 (D13).
- FE-10 and FE-19 rest on Lighthouse (`lcp-discovery-insight`) and Chrome DevTools/framework guidance, not on their cited web.dev pages (D14).
- Lighthouse 13 audit ids differ from the taxonomy's era (anomaly 1 below).

### Verification of research-agent claims (playbook: verify before relying)
Spot-checked against fetched primary pages on 2026-10-10:
- Lighthouse `unsized-images.js`: exemptions and the "sized" rule **confirmed** (fixed/absolute position, non-network SVG, 0x0 rect, CSS-background and shadow-DOM images excluded; sized = width+height, width+aspect-ratio, or height+aspect-ratio).
- Lighthouse `unused-css-rules.js`: `IGNORE_THRESHOLD_IN_BYTES = 10 * 1024`, strict `>`; **confirmed**.
- html-eslint `require-explicit-size`: checks `img`/`iframe` width and height, options `allowClass`/`allowId`; **confirmed**.
- Creedengo GCI12 `no-multiple-style-changes.js`: fires at 2 or more `.style.<prop>` statements on the same element in the same block; message says "more than two"; **confirmed. Correction to the agent's report:** non-literal right-hand sides are NOT exempt for the group count (they still count toward it); only nested blocks, `setProperty`/`Object.assign`, and different elements are skipped.
- Not independently re-fetched (rely on the agents' report, treat as UNVERIFIED): GCI11, GCI29, GCI31 details, stylelint-high-performance-animation issues #227/#295, react-doctor rules, npm download counts, EcoIndex/GreenFrame/sitespeed/GSF/WSG descriptions, bundle-format docs (the bundle formats themselves were confirmed by our own real captures).

### Honest limits
- Sonar, Semgrep and AWS CodeGuru/Q detector libraries were not reached (search only); no parity claim is made against them.
- Creedengo `RULES.md` was only partly read by the agent; EcoIndex practice ids and GSF pattern thresholds were not retrieved.
- Several Chrome docs pages (unsized-images, lcp-lazy-loaded) returned 404; Lighthouse behaviour comes from its source on `main` and our own captures of 13.5.0, not from a pinned tag.
- AWS side not yet researched: Free Plan eligibility of anything new for FE (we reuse Lambda/S3/EventBridge already in the stack), regional availability (ap-south-1 hosts the existing stack).

### Process note (honest order)
Decisions D1-D11 were discussed and Lighthouse/bundle captures were done before Gate 1 was complete (the workbook sources, the verification of agent claims and the AWS-side read happened afterwards, on 2026-10-10). No detector code or AWS change exists yet, so the Gate 2 / Gate 3 order is intact.

## Anomalies found (report to owner / discuss)

1. **Lighthouse 13 (Oct 2025) changed the audits the taxonomy relies on.** Old audit ids no longer appear in the
   JSON report. Parsers must read the new insight ids, and must support older Lighthouse JSON if clients pin it.
   - `offscreen-images`: **removed with no replacement** (browsers already deprioritize offscreen images). FE-01 has no Lighthouse signal any more.
   - `unsized-images`, `layout-shifts`, `non-composited-animations` -> `cls-culprits-insight` (FE-03, FE-17).
   - `lcp-lazy-loaded` -> `lcp-discovery-insight` (FE-02, FE-10).
   - `render-blocking-resources` -> `render-blocking-insight` (FE-11, FE-14).
   - `dom-size` -> `dom-size-insight` (FE-16).
   - `uses-responsive-images` -> `image-delivery-insight`.
   - `unused-css-rules`: replacement not stated on the blog. UNVERIFIED whether it still exists (FE-13).
   - Insight JSON shape (details type) is not documented on the blog; capture a real report before writing parsers (playbook step 6.2).
   - **Verified by a real capture (Lighthouse 13.5.0 on https://react.dev, performance category, 2026-10-09):** the blog's "consolidated" wording is misleading. The old audits **`unsized-images`, `layout-shifts`, `non-composited-animations`, `unused-css-rules`, `unused-javascript` are still present** next to the insights. `render-blocking-resources`, `dom-size`, `offscreen-images`, `lcp-lazy-loaded` are gone. `lcp-discovery-insight` was `notApplicable` on this page (no LCP image to discover). So FE-03, FE-13, FE-17 keep their old audit ids; FE-11, FE-14, FE-16 use the insights.
   - Shapes seen: `render-blocking-insight` = `details.type: table`, items `{url, totalBytes, wastedMs}`, `metricSavings {FCP, LCP}`. `dom-size-insight` = table, items `{statistic: "Total elements" | "DOM depth" | ..., value | node}`, total elements 1785 here. `forced-reflow-insight` = `list` of tables with `source-location` + `reflowTime`. `unused-css-rules` = `opportunity`, `overallSavingsBytes`. `unsized-images` = table `{node, url}`. `cls-culprits-insight` = `list`. Raw capture is outside the repo for now; a trimmed fixture will be committed with the cases.
   - Capture limits: react.dev had no unsized images, no non-composited animations and no unused CSS, so those shapes were only seen empty.
   - **Second capture (Lighthouse 13.5.0, local test page we wrote, served on localhost, 2026-10-09)** with an unsized lazy hero image, 500 unsized thumbnails, 2 large unused stylesheets, a `left`/`width` animation, a read-after-write loop in JS, and about 2,000 DOM nodes. Real hits seen:
     - `unsized-images`: table, items `{url, node{selector, snippet, boundingRect}}` (FE-03).
     - `non-composited-animations`: table, items `{node, subItems[{failureReason, animation}]}` (FE-17). Its `scoreDisplayMode` is `informative`.
     - `unused-css-rules`: `opportunity`, items `{url, wastedBytes, wastedPercent, totalBytes}`, `overallSavingsBytes` 69,411 (FE-13).
     - `render-blocking-insight`: items `{url, totalBytes, wastedMs}` for both stylesheets (FE-11).
     - `dom-size-insight`: "Total elements" 2007 plus "DOM depth" and max children rows with node selectors (FE-16).
     - `forced-reflow-insight`: list of tables, `source-location` url/line/column + `reflowTime` ms; pointed exactly at `app.js` (FE-15).
     - `cls-culprits-insight` and `layout-shifts`: per-node layout shift scores, total 0.81 (FE-03 context).
     - `lcp-discovery-insight` was still `notApplicable` (LCP was text, not the lazy image), so **FE-02 and FE-10 still need a capture where the LCP element is an image**.
     - `image-delivery-insight` returned no items.
2. The taxonomy `detection_signal`, `false_positive_risk` and `not_wasteful_when` are empty for most FE rows; the table below fills them from sources.
3. The taxonomy maps FE-01/02/03/06/07/10..21 to Lighthouse and FE-04/08/09 to a bundle analyzer. D1 (hybrid) adds static detectors as an extension.
4. FE-04 is mapped to the bundle analyzer, but its source (web.dev lazy loading) is about `<img>` lazy-load libraries. A static dependency/markup check fits better.
5. FE-19 and FE-20 are Derived/Low confidence in the taxonomy; no primary source reports them as audits.

## Per-check table

Evidence class: **S** = static source detector, **A** = client-CI artifact, **S+A** = static finding, artifact-confirmed when available.

| Check | Class | Existing detectors / sources | Our rule | False-positive traps | Start confidence |
| --- | --- | --- | --- | --- | --- |
| FE-01 eager offscreen images | S (weak) | web.dev: use `loading="lazy"` for offscreen images. Lighthouse audit removed in 13. | `<img>` without `loading` in a list/grid/below-fold position (repeated items, after the first N images). | Source cannot know the viewport. LCP/above-fold images must stay eager. Chrome already deprioritizes offscreen images. | Low |
| FE-02 lazy-loaded LCP image | S+A | web.dev: don't lazy-load LCP images. Lighthouse `lcp-discovery-insight`. | `<img loading="lazy">` that is the first image or has `fetchpriority="high"` / hero class. Artifact confirms the LCP element is lazy. | First image is not always the LCP element. | Medium (A), Low (S) |
| FE-03 missing width/height | S | web.dev: set both so space is reserved. Lighthouse `cls-culprits-insight`. | `<img>` with no `width`+`height` and no CSS `aspect-ratio`/sized wrapper. | CSS can size the image (aspect-ratio, class-based sizes); SVG/inline data URIs; JSX spread props hide attributes. | Medium-High |
| FE-04 lazy-load JS library instead of native | S | web.dev: native `loading` needs no library; libraries like lazysizes only for old browsers. | package.json dependency (lazysizes, lozad, vanilla-lazyload, react-lazyload...) or `data-src` + `.lazyload` markup. | Library may serve legacy browsers, or finer thresholds, or background images (`loading` only works on `<img>`/`<iframe>`). | Medium |
| FE-06 hidden images still load | S+A | web.dev: `display:none` images don't load, `opacity:0` ones do; carousels load earlier in Chrome 121+. | `<img>` inside element styled `opacity:0`/`visibility:hidden` or off-screen carousel slide, without `loading="lazy"`. | Intentional preloading; fade-in effects that start at opacity 0. | Low-Medium |
| FE-07 no code splitting | S+A | web.dev code splitting: route/component splitting via `import()`. | Static: bundler entry with a router and no `import()`/`React.lazy`. Artifact: one large initial chunk in bundle stats. | Small apps; SSR frameworks that split automatically (Next.js). | Medium |
| FE-08 static import of non-initial modules | S+A | web.dev: dynamic `import()` for modules needed after an event. | Top-level `import` of a heavy module used only inside an event handler or conditional. | "Heavy" needs a size source (bundle stats); without it, use a curated list. Side-effect imports. | Medium (A), Low (S) |
| FE-09 no cacheable vendor bundle | A (+S config) | web.dev: vendor splitting with SplitChunksPlugin. | Artifact: third-party modules inside the app chunk. Static: bundler config without `splitChunks`/`manualChunks`. | Bundler defaults may already split vendor code (webpack 5 splitChunks defaults, Vite). | Medium |
| FE-10 CSR delays LCP discovery | A | Lighthouse `lcp-discovery-insight`. | Artifact: LCP resource not discoverable in initial HTML. | Static can only guess (empty `<div id=root>`). | Medium (A) |
| FE-11 render-blocking CSS | S+A | Lighthouse `render-blocking-insight`: `<link rel=stylesheet>` without `disabled` and non-matching `media` is blocking; `media="all"` counts as blocking. | Static: multiple blocking stylesheet links in `<head>`. Artifact: insight savings. | One small critical stylesheet is fine. Savings threshold is not documented (UNVERIFIED). | Medium |
| FE-12 large style sheets | S+A | Lighthouse unused-CSS flags savings of 2 KiB or more (old audit). | CSS file size over a threshold in `context` (needs our own number; no primary threshold). | Minified vs source size; compressed transfer size. | Low |
| FE-13 unused CSS | A | DevTools Coverage; old `unused-css-rules` (2 KiB threshold). Detection method not documented. | Artifact only. | Unused rules on one page may be used on another; state-dependent rules. | Low-Medium |
| FE-14 CSS link moved to bottom still blocks | S+A | Lighthouse render-blocking: blocking is per stylesheet, position does not remove it. web.dev defers CSS via the preload pattern, not by moving it. | `<link rel=stylesheet>` in `<body>` end without `media`/preload pattern. | Late-body stylesheets for below-fold widgets can be intentional. | Low-Medium |
| FE-15 forced synchronous layout | S+A | web.dev: batch reads then writes; DevTools Forced Reflow insight (`forced-reflow`). | Static: layout read (`offsetHeight`, `getBoundingClientRect`...) after a style write in the same loop/function. Artifact: forced-reflow insight. | Read-after-write once is cheap; thrashing needs a loop. | Medium (A), Low (S) |
| FE-16 large DOM | A | Lighthouse `dom-size-insight`; old audit: warn above ~800 nodes, error above ~1,400 on body. No numeric depth/children limits. | Artifact only. | Virtualized pages; heavy but legitimate dashboards. | Medium |
| FE-17 animating layout/paint properties | S+A | web.dev: only `transform` and `opacity` are compositor-only. Lighthouse `cls-culprits-insight` lists non-composited animations. | CSS `transition`/`@keyframes`/`animation` touching width, height, top, left, margin, box-shadow, etc. | One-off transitions on rarely used elements; reduced-motion variants. | Medium-High |
| FE-18 layer explosion | S+A | web.dev: `will-change` on `*` causes layer explosion; no layer count limit given. DevTools Layers. | Static: `will-change`/`translateZ(0)` on `*` or on very many selectors. Artifact: layer count. | Promotion is useful for elements that are animating. | Low-Medium |
| FE-19 long lists without virtualization | S (weak) | web.dev: virtualize large lists; no tool flags them automatically. | `.map()` rendering without a virtualization library (react-window, react-virtual...) over data known to be large. | List size is unknown from source. Matters only for large lists. | Low |
| FE-20 unnecessary re-renders | A | React Profiler; no primary source. Taxonomy calls it a hypothesis. | Artifact only (React Profiler trace). | Memoization has its own cost. | Low |
| FE-21 heavy client computation / large payloads | A | AWS SUS03 device efficiency; Lighthouse long tasks / transfer size. | Artifact only. | Device mix matters. | Low |

### Third capture: image LCP (Lighthouse 13.5.0, local pages, 2026-10-09)
Two pages with a 1.2 MB PNG hero as the LCP element, identical except `loading="lazy"`. `lcp-discovery-insight` is now applicable and the checklist shape is **confirmed**:
- `details.type: list`; `items[0] = {type: "checklist", items: {priorityHinted, requestDiscoverable, eagerlyLoaded}}`, each `{label, value: boolean}`; `items[1]` is the LCP `node` (selector, snippet, boundingRect).
- Lazy page: `eagerlyLoaded.value = false` (label "LCP resources should not use loading=lazy"). Eager page: `true`.
- **Trap for parsers:** the audit `score` is 0 on BOTH pages, because `priorityHinted` is false on both (no `fetchpriority=high`). So FE-02 must read `eagerlyLoaded.value === false`, never the audit score. FE-10 reads `requestDiscoverable.value === false` (not discoverable in the initial document); that key was `true` on those pages.
- **CSR capture (confirmed):** a page with an empty `<div id=root>` and a script that inserts the same PNG after 300 ms gives `requestDiscoverable.value = false` (with `eagerlyLoaded = true`, `priorityHinted = false`) and the LCP node path `body > div#root > img#hero`. So FE-10 fires on `requestDiscoverable === false`. Score is again 0, so the same rule applies: read the checklist, not the score.

## Precedents: how existing tools detect these (second research pass, 2026-10-09)

Source: Lighthouse `main` raw source and linter docs, gathered by two research agents; not pinned to a 13.x tag.
"Original work" means no existing tool was found. Sonar and Semgrep registries were searched but not fetched directly, so "none found" there is UNVERIFIED.

### Corrections to the table above
- **FE-13 threshold:** source says an `unused-css-rules` sheet is reported only if `wastedBytes > 10 KiB` (strict); the docs page said 2 KiB. Use the source. Unused = CSS coverage ranges; no merging of ranges.
- **FE-16 thresholds:** the old 800/1400-node limits are gone in 13. `dom-size-insight` never fails; it is `informative` when a Layout event has `dur >= 40 ms` and `dirtyObjects > 100`, or a RecalcStyle event has `dur >= 40 ms` and `elementCount > 300`. Our rule needs our own node-count threshold in `context`, reported from the rows (`Total elements`, `DOM depth`, max children) or `debugData`.
- **FE-11 threshold:** `render-blocking-insight` counts a request only if it finishes before first paint and its wasted time is at least 50 ms. State is `fail` if any request qualifies.
- **FE-02 / FE-10:** no separate lazy-LCP audit. Lazy LCP = `lcp-discovery-insight` checklist key `eagerlyLoaded` is false. Other keys: `priorityHinted` (fetchpriority high), `requestDiscoverable` (preload or parser-discovered from the document). Text LCP gives `notApplicable`. Checklist per-key shape `{label,value}` is UNVERIFIED; capture needed.
- **FE-01 / FE-06:** no Lighthouse signal for offscreen or hidden (opacity:0) images in 13. A signal would need our own collector (DOM/trace), so both stay artifact-dependent on something we do not have yet. Candidate: propose a small client-CI DOM-snapshot artifact (open question).

### Lighthouse detection logic (source)
| Check | Audit | Logic and exemptions |
| --- | --- | --- |
| FE-03 | `unsized-images` | Non-background `<img>` outside shadow DOM. Sized = width+height, width+aspect-ratio, or height+aspect-ratio (HTML attr `parseInt >= 0`, or CSS value not auto/initial/unset/inherit). Exempt: `position: fixed/absolute`, non-network SVG, 0x0 rect, sized images. No threshold: any hit is score 0. Items `{url, node}`. |
| FE-17 | `non-composited-animations` | Reads trace failure-reason bits. Only 6 reasons reported: unsupported CSS property, transform depends on box size, filter may move pixels, non-replace composite mode, incompatible animations, unsupported timing parameters. `notApplicable` if none. Items `{node, subItems[{failureReason, animation}]}`. |
| FE-13 | `unused-css-rules` | `wastedBytes = round(unused ratio x estimated compressed size)`, reported if over 10 KiB. |
| FE-15 | `forced-reflow-insight` | `fail` if any `FORCED_REFLOW` trace warning exists. No duration threshold. List of tables: `source-location` + `reflowTime`; unattributed rows are text. |
| FE-03 context | `cls-culprits-insight` | Culprits: web font, injected iframe, animation, unsized image. Per cluster a total row then up to 5 shifts. |

### Static linter precedents (for static-only detectors FE-03, FE-04, FE-17)
| Check | Precedent | Notes and false-positive reports |
| --- | --- | --- |
| FE-03 | html-eslint `require-explicit-size`; react-doctor `no-img-without-dimensions` (JSX) | Flags missing `width`/`height`; ignores CSS sizing and `aspect-ratio`; no value validation. Open issue #568: fixed pixel sizes conflict with responsive design. Our rule should follow Lighthouse's "sized" definition (width+height, or one + aspect-ratio) rather than the linters'. |
| FE-04 | none found | Original work. Library popularity (npm weekly downloads, Oct 2026): react-lazyload 280k, lazysizes 207k, vanilla-lazyload 59k, lozad 40k. Detect imports + `data-src`/`.lazyload` markers. |
| FE-17 | `stylelint-high-performance-animation` (`no-low-performance-animation-properties`) | Blacklist; checks `transition`, `transition-property`, `@keyframes`; ignores `:hover`/`:focus` changes (issue #227, false negative); not published for stylelint 16.13+ (issue #295). react-doctor has JSX inline equivalents (`no-layout-transition-inline`, `no-transition-all`). |
| FE-18 | react-doctor `no-permanent-will-change` (JSX inline only) | No stylelint rule found. UNVERIFIED. |
| FE-15 | none found | Original work. Read list: offsetWidth/Height/Top/Left, clientWidth/Height, scrollWidth/Height, getBoundingClientRect, getComputedStyle, innerText. Trigger: read after `.style` write, `classList` change or `setAttribute` in the same loop/function. Cannot know the layout was actually dirty. react-doctor `js-batch-dom-css` only flags adjacent style writes. |
| FE-08 | react-doctor `prefer-dynamic-import` | Fixed list of heavy libraries (monaco, recharts, chart.js, codemirror, draft-js...); no usage analysis; does not skip type-only imports. Usage analysis ("binding used only inside a handler") would be original. |
| FE-19 | none found for web | Closest: react-doctor `rn-no-scrollview-mapped-list` (React Native). Static rule cannot know list length. Supports D5 (artifact-confirmed only). |

### Bundle evidence format (FE-07, FE-08, FE-09)
| Format | Initial vs async | Third-party | Verdict |
| --- | --- | --- | --- |
| webpack `--json` stats | `chunks[].initial`, entrypoints, import `reasons` | module `name` contains `node_modules/` | **Best single format to require.** Richest: initial flag, module-to-chunk map, import reasons. `reasons[].type` value for dynamic imports UNVERIFIED. |
| rollup-plugin-visualizer `raw-data` (Vite/rollup) | derive from `isEntry` + non-`dynamic` `imported` edges | `node_modules` in path | Best equivalent for Vite clients. |
| source-map-explorer `--json` | none | `node_modules/` in file keys | Sizes only; cannot answer FE-07/09. |
| Lighthouse `script-treemap-data` / `unused-javascript` | none | by path | `unused-javascript` reports over 20 KiB wasted per script; shows unused, not split. |

**Real captures (2026-10-09, tiny app: static imports of date-fns, lodash, a local module used only in a click handler, plus one `import()`; webpack 5 production build and Vite build):**
- **webpack `--json`** (top keys: `chunks`, `modules`, `entrypoints`, `assets`, ...): chunk `main` has `initial: true`, `entry: true`, 11 modules; chunk 899 has `initial: false` and holds the `import()` module. The dynamic module's `reasons` entry has `type: "import()"` (**confirmed**; previously UNVERIFIED). 305 `modules` entries have `node_modules` in `name` (third-party = path match, confirmed). **Trap:** in a production build, modules concatenated into another module show `chunks: []` (our `heavy.js` did), so per-module chunk membership must be read through the parent module or `chunks[].modules`, not `modules[].chunks` alone. Statically imported `date-fns`/`lodash` and our handler-only `heavy.js` all land in the initial `main` chunk, which is the FE-07/08/09 signal.
- **Vite + rollup-plugin-visualizer `raw-data`** (`version 2`; keys `tree, nodeParts, nodeMetas, env, options`): output chunks are the keys of `moduleParts` (`assets/index-*.js`, `assets/lazy-*.js`). `main.js` has `imported` entries with `dynamic: true` for the `import()` edge; `index.html` has `isEntry: true`. Initial set = follow non-`dynamic` edges from entries (confirmed workable). Ids are absolute local paths, so connectors must normalize them to repo-relative paths.
- Raw captures live outside the repo (`C:\Users\medha\bundle-capture\app\`); trimmed fixtures will be committed with the cases.

UNVERIFIED overall: CLS good/needs-improvement thresholds, exact render-blocking classification helper, checklist per-key shape, Lighthouse 13 changelog, Sonar/Semgrep coverage.

## Precedents: green-software tools in our own category (third research pass, 2026-10-09)

Sources: Creedengo (formerly ecoCode) repos, EcoIndex/GreenFrame/sitespeed.io docs, Sustainable Web Design, Green Software Foundation (GSF), W3C Web Sustainability Guidelines (WSG), AWS Well-Architected. Several pages gave no rule-level detail through the fetch tool (marked UNVERIFIED).

**Headline:** green tools mostly *measure* (bytes, DOM count, whole-scenario energy) or give *guidance*. Few have per-pattern detection rules, and none cites measured impact per rule. Our evidence therefore comes from web.dev/Lighthouse, with green tools as category precedent and vocabulary.

### Creedengo (ESLint plugin `creedengo-javascript`, rules in `creedengo-rules-specifications`)
Renamed from ecoCode; ids are GCI###. JS/TS/React/Vue only; **no HTML or CSS analyzer** (CSS rules run as JS/JSX/Vue checks). Implemented rules (16): GCI9 no-import-all, GCI11 repeated DOM access, GCI12 batch style changes, GCI13 paginated collections, GCI24 limit DB results, GCI25 empty image src, GCI26 CSS shorthand, GCI29 avoid CSS animations, GCI30 print CSS, GCI31 lighter image formats, GCI36 autoplay, plus device-API rules. Evidence basis: short good-practice text, no measurements. GCI12 cites CNUMR guideline RWEB_0040 (UNVERIFIED, repo 404).

| Our check | Creedengo rule | Real behaviour (from source) and complaints |
| --- | --- | --- |
| FE-15 | GCI12 `no-multiple-style-changes`; GCI11 `no-multiple-access-dom-element` | GCI12: 2+ `.style.prop` assignments to the same element in one block (message says "more than two", logic fires at 2; verified from source 2026-10-10; skips different elements, nested blocks, `setProperty`/`Object.assign`; non-literal values still count toward the group). FP: issue #72 (two elements in one object, fixed). GCI11: repeated `document.getElementById/querySelector...(literal)` in one function scope; can miss A,B,A and throw on no-arg calls; FP issue #59. **Neither detects read-after-write, so neither is a real forced-reflow detector.** |
| FE-17 / FE-18 | GCI29 `avoid-css-animations` | Flags any inline `transition`/`animation` in a JSX/Vue `style`; does not check which property, does not read stylesheets. Doc says use only opacity/transform and `will-change`, but logic does not enforce it. Coarser than ours. |
| FE-04 adjacent | GCI31 `prefer-lighter-formats-for-image-files` | Static `<img src>` not webp/avif/svg/jxl; exempts `<picture>`, empty or non-literal src; known gap: query strings not stripped. Not about lazy-load libraries. |
| FE-03 adjacent | GCI25 `no-empty-image-src-attribute` | Empty `src` only; no width/height check (source not read, UNVERIFIED). |
| FE-07/08/09 adjacent | GCI9 `no-import-all-from-library` | Tree-shaking, not code splitting. |

**Creedengo has no rule for:** FE-01, 02, 03, 04, 06, 07, 08, 09, 11, 12, 13, 16, 19, 20, 21, true layout thrashing. Also none for other-category JS items (await in loop, includes in loop, JSON clone, sync fs, listener cleanup, `new RegExp` in loop); GCI35 try/catch and loop-invariant ideas (GCI3, GCI69, GCI72) are "planned for JS" only. RULES.md was read only partly, so "absent" is UNVERIFIED. Open proposal: polling without visibility check (issue #109).

### Measuring tools (not detectors)
| Tool | What it does | Relevance |
| --- | --- | --- |
| EcoIndex / GreenIT-Analysis (cnumr) | Score from 3 inputs: DOM element count, page size, request count; headless browser. RWEB = 115 web eco-design practices (practice ids UNVERIFIED), mapped to the French RGESN standard. | Supports FE-16 (DOM size is a score input). |
| GreenFrame (marmelab) | Playwright scenario; samples CPU, memory, network, disk with `docker stats`; model converts to Wh; `--threshold` fails CI. No per-pattern rules. | Closest to our artifact route (client-CI run). Runs the app, so we would only consume its output. |
| sitespeed.io sustainable plugin | Transfer bytes + green-host check via CO2.js; no CPU/DOM (snippets only, UNVERIFIED). | Byte-based only. |
| Sustainable Web Design model | Byte-based emissions; operational 0.055 / 0.059 / 0.080 kWh/GB (data center / network / user device); 494 gCO2e/kWh. | Indirect support for payload checks only; no CPU or rendering cost. |

### Guidance catalogues (no testable thresholds)
- GSF front-end patterns: avoid excessive DOM size, defer offscreen images, remove unused CSS, minimize main-thread work, avoid chaining critical requests, optimize image size/format, deprecate GIFs (pattern pages not opened, thresholds UNVERIFIED).
- W3C WSG: lazy loading, code splitting ("don't over-split"), remove redundancy (unused CSS/JS), efficient animation, sustainable JavaScript, data-processing efficiency. Width/height appears only as an example.
- AWS SUS03-BP04 (optimize impact on devices): render heavy work server-side, paginate, test on device farms. Guidance only (page not fetched). No CodeGuru / Amazon Q frontend detectors found (UNVERIFIED, not searched deeply).

### Coverage of our checks by green tools
| Check | Green precedent | Status |
| --- | --- | --- |
| FE-01 | GSF/WSG "defer offscreen images" | guidance only |
| FE-02, FE-03, FE-18, FE-20 | none | cite web.dev/Lighthouse |
| FE-04 | none (GCI31 is image format) | original work |
| FE-07 | WSG "code splitting"; GCI9 partial | guidance + partial |
| FE-11 | GSF "avoid chaining critical requests" partial | none direct |
| FE-13 | GSF/WSG remove unused CSS | guidance only |
| FE-15 | GCI11, GCI12 (partial, no reads) | weak |
| FE-16 | EcoIndex, GSF | measure/guidance |
| FE-17 | GCI29 (coarse), WSG efficient animation | weak |
| FE-19 | GCI13 (data pagination), AWS SUS03 | no virtualization rule |
| FE-21 | GSF, WSG, SUS03, GreenFrame (measures) | guidance/measure |

### Consequences for our design
- Our detectors would be **stricter and more specific than any green precedent** (property-level for FE-17, Lighthouse-style "sized" definition for FE-03, read-after-write for FE-15). That is fine, but each needs its own verification cases because no existing tool validates them.
- GCI12/GCI29 false-positive reports (#72, #59) are reusable negative cases: different elements in one object; different function scopes.
- FE-15 static must not be sold as a Creedengo-equivalent; GCI11/12 are not forced-reflow detectors.

## Proposed split (for discussion)

- **Static first wave (strong):** FE-03, FE-04, FE-17.
- **Static + artifact:** FE-02, FE-06, FE-07, FE-08, FE-11, FE-14, FE-15, FE-18.
- **Artifact-only:** FE-09 (config static optional), FE-10, FE-13, FE-16, FE-20, FE-21.
- **Weak / consider dropping or low confidence:** FE-01 (Lighthouse removed it), FE-12 (no threshold), FE-19.

## AWS notes (from `docs/aws_service_mapping_v1.6.md`)

- R8/R9 rows name CodeBuild headless Chrome; D2 replaces that with client-CI artifacts -> S3 -> parse-only Lambda (OQ-5 decided as client CI).
- Reuse stack `owner-c-python-detectors` (ap-south-1). Free Plan eligibility for any new service: UNVERIFIED, check before adding anything beyond Lambda/S3/EventBridge.

## Not yet done

- Fetch pages that returned 404 (Lighthouse unsized-images, lcp-lazy-loaded; new URLs after the insights move).
- Second Lighthouse capture on a page with real hits (unsized images, non-composited animations, unused CSS, lazy LCP image).
- Check ESLint/Sonar/Semgrep/stylelint rules for FE-03, FE-04, FE-17, FE-18 (e.g. `jsx-a11y`, `stylelint-plugin-performance`: UNVERIFIED).
