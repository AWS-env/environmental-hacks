"""Owner A (TypeScript, tree-sitter): contract v1 `evaluate` from the built package, via Node.

Each check runs in its own short-lived Node process (two at a time): tree-sitter's native
memory is only reclaimed when the process exits, and one process for every check grew past 3 GB
on a 1,700-file repository.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from scanner.adapters import node
from scanner.core import REPO_ROOT, AdapterUnavailable, CheckRun, is_test_path, static_source

OWNER, NAME = "A", "owner-a-node"
DIST = REPO_ROOT / "detectors" / "owner-a" / "dist" / "index.js"
BUILD_HINT = "build it with `npm --prefix detectors/owner-a ci && npm --prefix detectors/owner-a run build`"
PROCESSES = 2


class OwnerA:
    owner, name = OWNER, NAME

    def __init__(self, entry=DIST):
        self.entry = entry

    def _evaluate(self, payload):
        run = CheckRun(payload["check_id"], OWNER, NAME, payload=payload)
        base = {k: v for k, v in payload.items() if k not in ("check_id", "detector_version", "scope", "sources")}
        request = {"base": base, "sources": payload["sources"],
                   "checks": [{"check_id": payload["check_id"], "detector_version": payload["detector_version"]}]}
        try:
            (item,) = node.call("owner-a", self.entry, "evaluate", request)["results"]
        except (RuntimeError, ValueError, KeyError) as error:
            run.error = f"owner A run failed: {error}"
            return run
        if "error" in item:
            run.error = f"detector raised {item['error']}"
        else:
            run.result = item["result"]
        return run

    def run(self, ctx):
        if not self.entry.exists():
            raise AdapterUnavailable(f"owner A is not built ({BUILD_HINT})")
        checks = node.call("owner-a", self.entry, "list")["checks"]
        # CODE-layer checks target shipped code; test files belong to the TST checks (as for owner C).
        files = [(p, c) for p, c in ctx.files if p.endswith((".py", ".pyi")) and not is_test_path(p)]
        if not files:
            return [CheckRun(c["check_id"], OWNER, NAME, not_applicable="no non-test Python (.py/.pyi) files collected")
                    for c in checks]
        sources = [static_source(p, c) for p, c in files]
        context = ctx.context(language="python", exclude_tests=True)
        payloads = [ctx.input(c["check_id"], c["version"], context, sources) for c in checks]
        with ThreadPoolExecutor(max_workers=PROCESSES) as pool:
            return list(pool.map(self._evaluate, payloads))
