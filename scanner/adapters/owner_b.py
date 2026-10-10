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
from scanner.core import REPO_ROOT, CheckRun, fingerprint_of, static_source

OWNER, NAME = "B", "owner-b-node-legacy"
ENTRY = REPO_ROOT / "detectors" / "owner-b" / "index.js"
CHECK_ID, VERSION = "DB-34", "legacy-scanSource"
REFERENCES = ["https://github.com/AWS-env/environmental-hacks/issues/136",
              "https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf"]
LIMITATION = ("Translated by the scanner from owner B's pre-contract scanSource output; PHP only, "
              "bounded to local function/method scope. Static pattern: avoided CPU is not measured.")
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
        files = [(p, c) for p, c in ctx.files if p.endswith(".php")]
        if not files:
            return [CheckRun(CHECK_ID, OWNER, NAME, not_applicable="no PHP (.php) files collected"), *network_runs]
        payload = ctx.input(CHECK_ID, VERSION, ctx.context(language="php", adapter=NAME),
                            [static_source(p, c) for p, c in files])
        run = CheckRun(CHECK_ID, OWNER, NAME, payload=payload)
        try:
            response = node.call("owner-b", self.entry, "scan", {"files": [{"path": p, "content": c} for p, c in files]})
            run.result = translate(payload, response["files"])
        except (RuntimeError, KeyError, TypeError, ValueError) as error:
            run.error = f"owner B scan failed: {error}"
        return [run, *network_runs]
