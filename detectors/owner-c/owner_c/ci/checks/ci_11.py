"""CI-11: redundant triggers and no cancellation of superseded runs."""
import re

from owner_c.ci.workflow import Hit, truthy

KEY = "CI-11"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.github.com/en/actions/using-jobs/using-concurrency",
    "https://docs.zizmor.sh/audits/#concurrency-limits",
]
RECOMMENDATION = (
    "Add `concurrency: {group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true}` to pull request "
    "workflows, and limit `push` to the default branch so a pull request commit does not run twice.")
LIMITATION = (
    "The group is judged inside one file: a group made only of context expressions with no workflow name and no literal text "
    "(for example `${{ github.ref }}`) is reported because another workflow with the same group cancels it; a group with literal text "
    "is assumed unique, and clashes between literal groups of different files are not checked. "
    "Static pattern only: run counts and wasted minutes are not measured. Only `pull_request` workflows that run on "
    "new commits are considered. Reusable (`workflow_call`) workflows, `pull_request_target` automation, workflows "
    "whose `pull_request` types exclude `synchronize`, deployment workflows (an `environment` job, or "
    "deploy/release/publish in the name), runs limited by `paths` to workflow files only, workflows whose every step is a "
    "labeling/commenting/closing action (`actions/github-script`, `labeler`, `stale`), an explicit `cancel-in-progress: false` and `cancel-in-progress` "
    "expressions are not flagged. Overlap between a `push` branch filter and pull request branches is not checked.")

_EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.S)
_WORKFLOW_REFS = ("github.workflow", "github.workflow_ref", "github.job")
# Only the generic keys collide across workflows; a group built from inputs, a version or a matrix value is specific.
_GENERIC_KEYS = re.compile(r"github\.(?:ref|head_ref|ref_name|sha|run_id|event\.number|event\.pull_request\.number)\b")
# Steps that only label, comment or close: superseded runs of these cost nothing worth cancelling.
_METADATA_ACTIONS = {"actions/github-script", "actions/labeler", "actions/stale", "actions/first-interaction"}
_PUSH_FILTERS = ("branches", "branches-ignore", "tags", "tags-ignore", "paths", "paths-ignore")


def _cancels(concurrency) -> bool:
    """`cancel-in-progress` is true, or an expression we cannot resolve statically."""
    return isinstance(concurrency, dict) and truthy(concurrency.get("cancel-in-progress"))


def _deliberately_queues(concurrency) -> bool:
    """An explicit `cancel-in-progress: false` is a choice, not an omission."""
    return isinstance(concurrency, dict) and concurrency.get("cancel-in-progress") is False


def _shared_group(concurrency) -> bool:
    """A cancelling group made only of context expressions that do not name the workflow."""
    if not isinstance(concurrency, dict) or not truthy(concurrency.get("cancel-in-progress")):
        return False
    group = concurrency.get("group")
    if not isinstance(group, str) or not group.strip():
        return False
    if re.search(r"[A-Za-z0-9]", _EXPRESSION.sub("", group)):
        return False  # the author named the group: assumed unique
    expressions = _EXPRESSION.findall(group)
    if not all(_GENERIC_KEYS.search(e) for e in expressions):
        return False
    return not any(ref in group for ref in _WORKFLOW_REFS)


def _only_own_workflow_paths(config) -> bool:
    """`pull_request.paths` limited to workflow files: it only runs when the pipeline itself is edited."""
    paths = config.get("paths") if isinstance(config, dict) else None
    return isinstance(paths, list) and bool(paths) and all(
        isinstance(p, str) and p.startswith(".github/workflows/") for p in paths)


def _metadata_only(jobs) -> bool:
    steps = [s for j in jobs for s in j.steps()]
    return bool(steps) and all(s.action in _METADATA_ACTIONS for s in steps)


def _runs_on_new_commits(config) -> bool:
    """`pull_request` fires on new commits unless its `types` filter leaves out `synchronize`."""
    types = config.get("types") if isinstance(config, dict) else None
    return not isinstance(types, list) or "synchronize" in types


def run(wf, settings):
    hits = []
    triggers = wf.triggers()

    push = triggers.get("push", False)
    if push is not False and "pull_request" in triggers:
        unrestricted = push is None or (isinstance(push, dict) and not any(k in push for k in _PUSH_FILTERS))
        if unrestricted:
            start, end = wf.trigger_span("push")
            hits.append(Hit("workflow:redundant-trigger:push+pull_request", start, end,
                            "`push` has no branch or path filter and `pull_request` is also enabled: every commit "
                            "on a pull request branch runs the workflow twice.", "medium"))

    owners = [("workflow:concurrency-group-shared", wf.root)] + [
        (f"job:{j.id}:concurrency-group-shared", j.data) for j in wf.jobs()]
    for anchor, owner in owners:
        if _shared_group(owner.get("concurrency")):
            start, end = owner.spans["concurrency"]
            hits.append(Hit(anchor, start, end,
                            "The cancelling `concurrency.group` has no workflow name or literal text, so another workflow "
                            "using the same group (for example `${{ github.ref }}`) cancels this one.", "low"))

    if "pull_request" not in triggers or not _runs_on_new_commits(triggers["pull_request"]) or wf.deploy_like():
        return hits
    if _only_own_workflow_paths(triggers["pull_request"]) or _metadata_only(wf.jobs()):
        return hits

    jobs = wf.jobs()
    wf_concurrency = wf.root.get("concurrency")
    job_concurrency = [(j, j.data.get("concurrency")) for j in jobs if j.data.get("concurrency") is not None]
    configured = [wf_concurrency] + [c for _, c in job_concurrency]
    if any(_cancels(c) or _deliberately_queues(c) for c in configured):
        return hits
    if wf_concurrency is not None:
        start, end = wf.root.spans["concurrency"]
    elif job_concurrency:
        job, _ = job_concurrency[0]
        start, end = job.data.spans["concurrency"]
    else:
        start, end = wf.root.spans.get(wf.on_key, (1, 1))
        hits.append(Hit("workflow:no-cancel-superseded", start, end,
                        "A pull request workflow has no `concurrency` setting, so superseded runs keep running "
                        "after a new commit is pushed.", "high"))
        return hits
    hits.append(Hit("workflow:concurrency-without-cancel", start, end,
                    "`concurrency` is set without `cancel-in-progress: true`, so a newer run only queues behind the "
                    "superseded one instead of cancelling it.", "medium"))
    return hits
