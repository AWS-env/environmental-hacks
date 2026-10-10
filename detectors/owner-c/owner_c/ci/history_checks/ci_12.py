"""CI-12: a scheduled workflow keeps running on a commit that has not changed."""
from dataclasses import dataclass

from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count

KEY = "CI-12"
KIND = "confirmed"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "scheduled_runs_total"
SETTINGS = {"min_repeat_runs": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_repeat_runs": 3, **RUNS_DEFAULT}
REFS = [
    "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#onschedule",
    "https://docs.github.com/en/rest/actions/workflow-runs",
]
RECOMMENDATION = (
    "Skip the scheduled run when the default branch has not changed since the last scheduled run (compare "
    "`github.sha` with the last successful run), or run it on push instead of on a timer.")
LIMITATION = (
    "Needs the workflow file and a client-collected GitHub Actions run history for it; a workflow without history is "
    "not evaluated. A schedule is legitimate for dependency or security scans that re-check fresh advisories on an "
    "unchanged commit; this check only reports repeated identical commits.")


@dataclass(frozen=True)
class Candidate:
    anchor: str
    start: int
    end: int


def validate(data):
    total, same = data.get("scheduled_runs_total"), data.get("scheduled_runs_same_sha")
    if not is_count(total) or not is_count(same):
        return "scheduled_runs_total and scheduled_runs_same_sha must be non-negative integers"
    if same > total:
        return "scheduled_runs_same_sha cannot exceed scheduled_runs_total"
    return None


def find(wf):
    if "schedule" not in wf.triggers():
        return []
    start, end = wf.trigger_span("schedule")
    return [Candidate("workflow:schedule:unchanged-commit", start, end)]


def confirm(candidate, data, settings):
    same, total = data["scheduled_runs_same_sha"], data["scheduled_runs_total"]
    if same < settings["min_repeat_runs"]:
        return None
    return finding(
        candidate.anchor, "scheduled_runs_same_sha", same,
        f"{same} of {total} scheduled runs used the same commit as the previous scheduled run, so they re-tested "
        "unchanged code.", "medium" if total and same / total >= 0.5 else "low")
