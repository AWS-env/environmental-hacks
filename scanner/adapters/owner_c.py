"""Owner C (Python, JS/TS and CI workflow checks): the owner's own connectors build the inputs and its runners
evaluate them."""
from __future__ import annotations

from scanner.core import REPO_ROOT, CheckRun, evaluate_each, import_path

OWNER, NAME = "C", "owner-c"

NO_HISTORY = ("needs client-collected GitHub Actions run history (the ci_history artifact uploaded through the "
              "presign -> manifest flow); a repository scan has none")


class OwnerC:
    owner, name = OWNER, NAME

    def run(self, ctx):
        import_path(REPO_ROOT / "detectors" / "owner-c")
        from owner_c.artifact_checks import ARTIFACT_CHECKS
        from owner_c.checks import STATIC_CHECKS
        from owner_c.ci.checks import STATIC_CHECKS as CI_STATIC_CHECKS
        from owner_c.ci.connector import build_inputs as ci_build_inputs
        from owner_c.ci.history_checks import HISTORY_CHECKS as CI_HISTORY_CHECKS
        from owner_c.ci.runner import evaluate as ci_evaluate
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

        # CI: the static checks read .github/workflows/*.y(a)ml; the history checks need run history that a
        # repository scan cannot collect, so they are reported as unavailable, never as clean.
        ci_payloads = ci_build_inputs(repository_id=ctx.repository_id, commit_sha=ctx.commit_sha,
                                      scan_id=ctx.scan_id, files=ctx.files, checks=list(CI_STATIC_CHECKS))
        runs += evaluate_each(OWNER, NAME, ci_payloads, ci_evaluate)
        ci_built = {payload["check_id"] for payload in ci_payloads}
        for check_id in sorted(set(CI_STATIC_CHECKS) - ci_built):
            runs.append(CheckRun(check_id, OWNER, NAME, not_applicable=(
                "the repository has no GitHub Actions workflow files (.github/workflows/*.yml)")))
        for check_id in sorted(CI_HISTORY_CHECKS):
            runs.append(CheckRun(check_id, OWNER, NAME, unavailable=NO_HISTORY))
        return runs
