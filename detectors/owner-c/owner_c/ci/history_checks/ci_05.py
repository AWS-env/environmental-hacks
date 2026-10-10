"""CI-05: brown builds: a workflow run fails on attempt 1 and succeeds on a later attempt of the same commit."""
from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count

KEY = "CI-05"
KIND = "history"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "runs_total"
SETTINGS = {"min_brown_runs": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_brown_runs": 3, **RUNS_DEFAULT}
REFS = [
    "https://docs.github.com/en/rest/actions/workflow-runs",
    "https://docs.trunk.io/flaky-tests/detection/pass-on-retry-monitor",
]
RECOMMENDATION = (
    "Treat a build that fails then passes on re-run as a defect: find the unstable step (network, ordering, "
    "shared state) rather than re-running until green.")
LIMITATION = (
    "Needs a client-collected GitHub Actions run history (normalized). Workflow level: an attempt failed and a later "
    "attempt of the same run succeeded, judged by each attempt's own conclusion; it does not say which job or why "
    "(CI-03 reports the same pattern per job). Approval-gated (`action_required`) and cancelled attempts are not "
    "failures. A failure then pass can also be an infrastructure incident.")


def validate(data):
    runs, brown = data.get("runs_total"), data.get("fail_then_pass_runs")
    if not is_count(runs) or not is_count(brown):
        return "runs_total and fail_then_pass_runs must be non-negative integers"
    if brown > runs:
        return "fail_then_pass_runs cannot exceed runs_total"
    return None


def evaluate(data, settings):
    value, runs, threshold = data["fail_then_pass_runs"], data["runs_total"], settings["min_brown_runs"]
    if value < threshold:
        return []
    return [finding(
        "workflow:brown-builds", "fail_then_pass_runs", value,
        f"{value} of {runs} runs failed on the first attempt and passed on a re-run of the same commit "
        f"(threshold {threshold}).",
        "high" if runs and value / runs >= 0.25 else "medium")]
