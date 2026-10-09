"""Owner C (Python checks): the owner's own connector builds the inputs and its runner evaluates them."""
from __future__ import annotations

from scanner.core import REPO_ROOT, CheckRun, evaluate_each, import_path

OWNER, NAME = "C", "owner-c-python"


class OwnerC:
    owner, name = OWNER, NAME

    def run(self, ctx):
        import_path(REPO_ROOT / "detectors" / "owner-c")
        from owner_c.artifact_checks import ARTIFACT_CHECKS
        from owner_c.checks import STATIC_CHECKS
        from owner_c.connector import build_inputs
        from owner_c.runner import evaluate

        # The connector picks its files (non-test .py within its size limit) and declares the
        # exclusions in each payload's context; artifact checks get no artifacts from a repo scan.
        payloads = build_inputs(repository_id=ctx.repository_id, commit_sha=ctx.commit_sha,
                                scan_id=ctx.scan_id, files=ctx.files)
        runs = evaluate_each(OWNER, NAME, payloads, evaluate)
        built = {payload["check_id"] for payload in payloads}
        for check_id in sorted((set(STATIC_CHECKS) | set(ARTIFACT_CHECKS)) - built):
            runs.append(CheckRun(check_id, OWNER, NAME, not_applicable=(
                "the owner C connector selected no files for this check (non-test Python files)")))
        return runs
