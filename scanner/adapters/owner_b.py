"""Owner B (JavaScript, php-parser): DB-34 via the legacy `scanSource` entry point, via Node.

Owner B on main does not emit contract v1 yet (its contract `evaluate` is in review), so this
adapter translates: a file counts as evaluated only if it also passes a strict PHP parse (the
legacy scanner hides parse errors), and every finding cites exact lines of the supplied source.
The translated result goes through the same `validate_pair` gate as every other detector.
"""
from __future__ import annotations

import re
from collections import Counter

from scanner.adapters import node
from scanner.core import REPO_ROOT, CheckRun, fingerprint_of, is_test_path, static_source

OWNER, NAME = "B", "owner-b-node-legacy"
ORM_NAME = "owner-b-orm-static"
ENTRY = REPO_ROOT / "detectors" / "owner-b" / "index.js"
CHECK_ID, VERSION = "DB-34", "legacy-scanSource"
REFERENCES = ["https://github.com/AWS-env/environmental-hacks/issues/136",
              "https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf"]
LIMITATION = ("Translated by the scanner from owner B's pre-contract scanSource output; PHP only, "
              "bounded to local function/method scope. Static pattern: avoided CPU is not measured.")
# Static ORM checks for Python / JavaScript / TypeScript sources (contract v1, evaluated in Node).
# check_id -> extra context; every finding is a candidate (table size and runtime cost are unknown).
ORM_DETECTOR_VERSION, ORM_RULE_VERSION = "1.0.0", "orm-1"
ORM_CHECKS = {
    "DB-13": {"max_literal_iterations": 5},
    "DB-23": {"unique_key_fields": ["id", "pk"]},
    "DB-39": {"min_literal_offset": 100},
}
ORM_SUFFIXES = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# Plan checks (DB-43/45/46) evaluate client-produced EXPLAIN artifacts, never repository source.
PLAN_CHECKS = set()
# Checks that apply to fewer languages than the default (DB-41: JS/TS database drivers are Promise based).
ORM_CHECK_SUFFIXES = {}
# JavaScript/TypeScript test code (shared is_test_path only knows Python and tests/ directories).
JS_TEST_PATH = re.compile(r"(^|/)(__tests__|__mocks__|__fixtures__|e2e|integration-tests?|cypress)(/|$)|\.(test|spec|stories)\.[cm]?[jt]sx?$", re.I)
# Lowest confidence a scan reports per check; lower-confidence findings stay out of the scan result (and are counted in its limitations).
# DB-23's low tier is mostly scoped queries (where: { userId }), which is noise on a public scan.
ORM_MIN_CONFIDENCE = {"DB-23": "medium"}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def surface(check_id, result):
    """Drop findings below the check's minimum scan confidence; the result stays valid (fewer findings, one more limitation)."""
    floor = ORM_MIN_CONFIDENCE.get(check_id)
    if not floor or not result.get("findings"):
        return result
    kept = [f for f in result["findings"] if CONFIDENCE_ORDER[f["confidence"]] >= CONFIDENCE_ORDER[floor]]
    omitted = len(result["findings"]) - len(kept)
    if not omitted:
        return result
    note = f"{omitted} {check_id} finding(s) below {floor} confidence are not shown in scan output (filtered, mostly scoped queries); run the detector directly to list them."
    return {**result, "findings": kept, "coverage": {**result["coverage"], "limitations": [*result["coverage"]["limitations"], note]}}
AMBIGUOUS_BREAKS = re.compile(r"\r(?!\n)|[\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


def translate(payload, files_out):
    """Build a contract v1 result from the legacy per-file output."""
    sources = {s["locator"]: s for s in payload["sources"]}
    evaluated, limitations, findings, seen = [], [], [], Counter()
    for item in files_out:
        scope_id, source = f"file:{item['path']}", sources[item["path"]]
        if not item["parsed"]:
            limitations.append(f"{scope_id}: {item['reason']}; not evaluated")
            continue
        if AMBIGUOUS_BREAKS.search(source["content"]):
            limitations.append(f"{scope_id}: non-LF line separators; evidence lines cannot be cited exactly")
            continue
        lines = source["content"].splitlines()
        evaluated.append(scope_id)
        for legacy in item["findings"]:
            ev = legacy["evidence"]
            anchor = f"{ev['query_variable']}:{ev['cache_api']}->{ev['db_api']}"
            seen[(scope_id, anchor)] += 1
            identity = anchor if seen[(scope_id, anchor)] == 1 else f"{anchor}#{seen[(scope_id, anchor)]}"
            wanted = sorted({loc["line"] for loc in legacy["locations"].values() if loc and loc.get("line")})
            evidence = [{"source_id": source["source_id"], "kind": "static", "locator": source["locator"],
                         "line_start": n, "value": lines[n - 1]}
                        for n in wanted if 1 <= n <= len(lines) and lines[n - 1].strip()]
            findings.append({
                "fingerprint": fingerprint_of(payload["repository_id"], CHECK_ID, scope_id, identity),
                "scope_id": scope_id, "identity": identity, "summary": legacy["bypass_explanation"],
                "confidence": legacy["confidence"].lower(), "recommendation": legacy["recommendation"],
                "references": REFERENCES, "evidence": evidence,
            })
    status = ("completed" if len(evaluated) == len(payload["scope"]) else "partial" if evaluated else "unavailable")
    result = {k: payload[k] for k in ("schema_version", "repository_id", "scan_id", "commit_sha", "check_id",
                                      "detector_version", "context", "scope")}
    result.update(kind="result", status=status, findings=findings if evaluated else [], measurements=[],
                  coverage={"evaluated_scope": evaluated, "limitations": limitations + [LIMITATION]})
    return result


class OwnerB:
    owner, name = OWNER, NAME

    def __init__(self, entry=ENTRY):
        self.entry = entry

    def run(self, ctx):
        # A public repository scan has no route/capture/policy mapping. Keep registered
        # NET checks visible as unavailable rather than omitting them or inventing telemetry.
        registered = node.call("owner-b", self.entry, "list")["checks"]
        network_runs = [CheckRun(check_id, OWNER, "owner-b-network-contract", unavailable=(
            "Source-only repository scan has no snapshot-correlated network capture, route mapping "
            "or reviewed policy metadata. Supply contract v1 input through the network artifact "
            "connector and verify the hub report; no runtime network waste is confirmed."))
            for check_id in registered if re.fullmatch(r"NET-\d{2}", check_id)]
        # Plan checks read client-produced EXPLAIN artifacts; a source-only scan has none, so they stay visible as unavailable.
        plan_runs = [CheckRun(check_id, OWNER, "owner-b-explain-artifact", unavailable=(
            "Source-only repository scan has no EXPLAIN (ANALYZE, FORMAT JSON) artifact. Run the Owner B explain collector in "
            "your CI and submit the contract v1 input; nothing about query plans is confirmed or ruled out by this scan."))
            for check_id in sorted(PLAN_CHECKS) if check_id in registered]
        network_runs = [*plan_runs, *network_runs]
        orm_runs = self.run_orm(ctx, registered)
        files = [(p, c) for p, c in ctx.files if p.endswith(".php")]
        if not files:
            return [CheckRun(CHECK_ID, OWNER, NAME, not_applicable="no PHP (.php) files collected"), *orm_runs, *network_runs]
        payload = ctx.input(CHECK_ID, VERSION, ctx.context(language="php", adapter=NAME),
                            [static_source(p, c) for p, c in files])
        run = CheckRun(CHECK_ID, OWNER, NAME, payload=payload)
        try:
            response = node.call("owner-b", self.entry, "scan", {"files": [{"path": p, "content": c} for p, c in files]})
            run.result = translate(payload, response["files"])
        except (RuntimeError, KeyError, TypeError, ValueError) as error:
            run.error = f"owner B scan failed: {error}"
        return [run, *orm_runs, *network_runs]

    def run_orm(self, ctx, registered):
        """Static ORM checks: one contract v1 input per check over every Python/JS/TS file, run in Node."""
        checks = sorted(c for c in ORM_CHECKS if c in registered)
        files = [(p, c) for p, c in ctx.files
                 if p.lower().endswith(ORM_SUFFIXES) and not is_test_path(p) and not JS_TEST_PATH.search(p)]
        if not checks:
            return []
        if not files:
            return [CheckRun(c, OWNER, ORM_NAME, not_applicable="no Python, JavaScript or TypeScript files collected") for c in checks]
        runs, skipped = [], []
        for check_id in checks:
            own = [(p, c) for p, c in files if p.lower().endswith(ORM_CHECK_SUFFIXES.get(check_id, ORM_SUFFIXES))]
            if not own:
                skipped.append(CheckRun(check_id, OWNER, ORM_NAME, not_applicable=f"no files of the languages {check_id} applies to were collected"))
                continue
            context = ctx.context(adapter=ORM_NAME, rule_version=ORM_RULE_VERSION, mode="candidate", **ORM_CHECKS[check_id])
            payload = ctx.input(check_id, ORM_DETECTOR_VERSION, context, [static_source(p, c) for p, c in own])
            runs.append(CheckRun(check_id, OWNER, ORM_NAME, payload=payload))
        if not runs:
            return skipped
        try:
            response = node.call("owner-b", self.entry, "evaluate", {"inputs": [r.payload for r in runs]})
            by_check = {item["check_id"]: item for item in response["results"]}
            for run in runs:
                item = by_check.get(run.check_id, {})
                if "result" in item:
                    run.result = surface(run.check_id, item["result"])
                else:
                    run.error = f"owner B ORM check failed: {item.get('error', 'no result returned')}"
        except (RuntimeError, KeyError, TypeError, ValueError) as error:
            for run in runs:
                run.error = f"owner B ORM evaluation failed: {error}"
        return [*runs, *skipped]
