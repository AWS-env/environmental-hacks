"""CI-01: repeated builds of the same pull request commit (re-runs after a failure during code review)."""
from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count

KEY = "CI-01"
KIND = "history"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "runs_total"
SETTINGS = {"min_rechecks": (0, None), "min_reran_passing_jobs": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_rechecks": 2, "min_reran_passing_jobs": 5, **RUNS_DEFAULT}
REFS = [
    "https://arxiv.org/abs/2308.10078",
    "https://docs.github.com/en/rest/actions/workflow-runs",
    "https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/re-running-workflows-and-jobs",
]
RECOMMENDATION = (
    "When a job fails, use \"Re-run failed jobs\" (not \"Re-run all jobs\") so passing jobs are not executed again. "
    "Find why the same commit needed several re-runs after failures (flaky test, unreliable service, missing local "
    "check) and fix that instead of re-running; consider running the slow checks locally or in a pre-merge gate.")
LIMITATION = (
    "Needs a client-collected GitHub Actions run history (normalized). A recheck is a re-run after an attempt that "
    "FAILED (failure or timed_out), as in the OpenStack recheck study (arXiv 2308.10078, \"multiple rechecks\" are "
    "the unjustifiable case). Approval-gated attempts (`action_required`) and cancelled attempts are not rechecks "
    "and are not counted. Only pull request runs whose attempts were collected are counted (the artifact field `runs_without_detail` says how many re-run runs had no attempt detail)."
    " It cannot tell a "
    "code-review recheck from a re-run after an infrastructure outage. Gerrit `recheck` comments are not read.")


def validate(data):
    for field in ("runs_total", "max_rechecks_per_sha", "reran_passing_jobs"):
        if not is_count(data.get(field)):
            return f"{field} must be a non-negative integer"
    return None


def evaluate(data, settings):
    out = []
    value, threshold = data["max_rechecks_per_sha"], settings["min_rechecks"]
    if value >= threshold:
        out.append(finding(
        "workflow:repeated-reruns", "max_rechecks_per_sha", value,
        f"One pull request commit was re-run {value} times after a failed attempt (threshold {threshold}); each "
        "re-run rebuilds the same code.",
        "high" if value >= 2 * threshold else "medium"))
    passing, limit = data["reran_passing_jobs"], settings["min_reran_passing_jobs"]
    if passing >= limit:  # the taxonomy folded CI-04 (rechecking passing jobs) into CI-01
        out.append(finding(
            "workflow:rerun-passing-jobs", "reran_passing_jobs", passing,
            f"{passing} jobs that had already passed were executed again after a failed attempt on pull requests "
            f"(threshold {limit}); \"Re-run failed jobs\" would have re-run only the failed ones.", "low"))
    return out
