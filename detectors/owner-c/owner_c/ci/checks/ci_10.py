"""CI-10: the full test suite runs on every pull request change, with no path filter or test selection."""
from owner_c.ci.patterns import TEST_SELECTION, is_gated, runs_tests
from owner_c.ci.workflow import Hit

KEY = "CI-10"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#onpushpull_requestpull_request_targetpathspaths-ignore",
    "https://github.com/dorny/paths-filter",
]
RECOMMENDATION = (
    "Skip the suite for documentation-only changes, or select tests by changed files (for example `jest --onlyChanged`, "
    "`nx affected`). If this workflow is a REQUIRED status check, do not filter the workflow with `paths`: a skipped required "
    "check stays Pending and blocks the merge; detect changes inside the job instead (for example `dorny/paths-filter` with an `if`).")
LIMITATION = (
    "The taxonomy notes the opposite risk: skipping tests by path can miss tests that a change needs, so the finding is low confidence. "
    "GitHub documents that a required status check whose workflow is skipped by path, branch or commit-message filters stays Pending and blocks merging; whether this workflow is a required check is not visible here. "
    "Static pattern only: the run history has no changed-file data, so docs-only runs are not confirmed. Required "
    "status checks may need the workflow to run on every change (not checked). Only pull request triggers (or a push "
    "trigger with no branch, tag or path filter) are considered: scheduled, manual, tag-only and branch-limited push "
    "workflows, path-filtered workflows, jobs or steps gated by an `if` on changes or other jobs' outputs, and test "
    "selection by changed files are not flagged.")

_FILTER_KEYS = ("paths", "paths-ignore")
_GATING_ACTIONS = {"dorny/paths-filter", "tj-actions/changed-files"}
_PUSH_LIMITS = _FILTER_KEYS + ("branches", "branches-ignore", "tags", "tags-ignore")


def _unfiltered_per_change_trigger(wf) -> bool:
    triggers = wf.triggers()
    if "pull_request" in triggers:
        cfg = triggers["pull_request"]
        if not (isinstance(cfg, dict) and any(k in cfg for k in _FILTER_KEYS)):
            return True
    push = triggers.get("push", False)
    return push is not False and not (isinstance(push, dict) and any(k in push for k in _PUSH_LIMITS))


def run(wf, settings):
    if not _unfiltered_per_change_trigger(wf):
        return []
    hits = []
    for job in wf.jobs():
        steps = job.steps()
        if is_gated(job.data) or any(s.action in _GATING_ACTIONS for s in steps):
            continue
        if any(TEST_SELECTION.search(c) for s in steps for c in s.commands()):
            continue
        for step in steps:
            if not is_gated(step.data) and any(runs_tests(c) for c in step.commands()):
                hits.append(Hit(
                    f"job:{job.id}:tests-unfiltered", step.span[0], step.span[1],
                    f"Job '{job.id}' runs the test suite on every pull request change with no path filter or test "
                    "selection, so documentation-only changes run it too.", "low"))
                break
    return hits
