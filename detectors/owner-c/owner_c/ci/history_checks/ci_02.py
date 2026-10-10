"""CI-02: blind rechecking: a failed run is re-run without a new commit and fails the same way."""
from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count

KEY = "CI-02"
KIND = "history"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "runs_total"
SETTINGS = {"min_repeat_failures": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_repeat_failures": 2, **RUNS_DEFAULT}
REFS = [
    "https://docs.github.com/en/rest/actions/workflow-runs",
    "https://docs.github.com/en/rest/actions/workflow-jobs",
]
RECOMMENDATION = (
    "Read the failed job's log before re-running: if the same jobs fail again on the same commit, fix the cause "
    "instead of re-running.")
LIMITATION = (
    "Needs a client-collected GitHub Actions run history (normalized). Whether a person diagnosed the failure is not "
    "observable; only \"re-run after a failed attempt, and a job that failed was re-executed and failed again\" is. "
    "A failure carried over from the previous attempt without being re-run (a single-job re-run) does not count. "
    "Failure logs and test-level results are not collected.")


def validate(data):
    if not is_count(data.get("runs_total")) or not is_count(data.get("repeat_failed_reruns")):
        return "runs_total and repeat_failed_reruns must be non-negative integers"
    return None


def evaluate(data, settings):
    value, threshold = data["repeat_failed_reruns"], settings["min_repeat_failures"]
    if value < threshold:
        return []
    return [finding(
        "workflow:blind-reruns", "repeat_failed_reruns", value,
        f"{value} commits were re-run after a failure and failed again with the same jobs and no new commit "
        f"(threshold {threshold}).", "low")]
