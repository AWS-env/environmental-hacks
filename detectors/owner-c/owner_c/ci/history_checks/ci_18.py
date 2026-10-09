"""CI-18: test jobs chained with `needs` that have no data dependency, so they run one after the other."""
import json
import re
from dataclasses import dataclass

from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count, is_seconds
from owner_c.ci.normalize.github_actions import stage_name
from owner_c.ci.patterns import runs_tests

KEY = "CI-18"
KIND = "confirmed"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "runs_total"
SETTINGS = {"min_chain_seconds": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_chain_seconds": 120, **RUNS_DEFAULT}
PREFIX = "chain_seconds:"
REFS = [
    "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idneeds",
    "https://docs.github.com/en/rest/actions/workflow-jobs",
]
RECOMMENDATION = (
    "Run independent test jobs in parallel to cut waiting time: drop the `needs` between them (or make the later job "
    "depend only on what it really consumes). Compute is unchanged; this is a wall-clock saving.")
LIMITATION = (
    "Running the jobs in parallel shortens the wall-clock time; compute stays about the same because each job repeats its set-up (the taxonomy and the Harness article both note this trade-off), so the saving is waiting time, not necessarily energy. "
    "Needs the workflow file and a client-collected GitHub Actions run history; a workflow without history is not "
    "evaluated, and a chain with no timing in the history is not confirmed. A job is matched to history by its "
    "stage name: its `name` (or id) without a trailing matrix leg, with `${{ }}` expressions matching any text. "
    "Parallelism inside a test command (`pytest -n`, `jest --shard`) and shared state not expressed in `needs` "
    "are not visible.")


@dataclass(frozen=True)
class Candidate:
    anchor: str
    start: int
    end: int
    up_pattern: "re.Pattern"
    down_pattern: "re.Pattern"
    upstream: str
    downstream: str


def validate(data):
    if not is_count(data.get("runs_total")):
        return "runs_total must be a non-negative integer"
    for key, value in data.items():
        if key.startswith(PREFIX) and not is_seconds(value):
            return f"{key} must be a non-negative number"
    return None


def _stage_pattern(job) -> "re.Pattern":
    """Matches the stage names run history uses for this job: `name` (or id) minus a trailing matrix leg;
    a `${{ ... }}` expression in the name matches any text."""
    name = job.data.get("name")
    base = stage_name(name.strip()) if isinstance(name, str) and name.strip() else job.id
    parts = re.split(r"\$\{\{.*?\}\}", base)
    return re.compile(".+".join(re.escape(p) for p in parts), re.S)


def _runs_tests(job) -> bool:
    return any(runs_tests(c) for s in job.steps() for c in s.commands())


def _depends_on_data(job, upstream: str) -> bool:
    if any(s.action == "actions/download-artifact" for s in job.steps()):
        return True
    return f"needs.{upstream}.outputs" in json.dumps(job.data, default=str)


def find(wf):
    jobs = {j.id: j for j in wf.jobs()}
    out = []
    for job in jobs.values():
        needs = job.data.get("needs")
        needs = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
        for upstream in (n for n in needs if isinstance(n, str) and n in jobs):
            if not (_runs_tests(job) and _runs_tests(jobs[upstream])) or _depends_on_data(job, upstream):
                continue
            start, end = job.data.spans["needs"]
            out.append(Candidate(f"job:{job.id}:serial-after:{upstream}", start, end,
                                 _stage_pattern(jobs[upstream]), _stage_pattern(job), upstream, job.id))
    return out


def _best_match(candidate, data):
    """The history pair `chain_seconds:<A>-><B>` whose stage names match the candidate (longest chain wins)."""
    best = None
    for key, seconds in data.items():
        if not key.startswith(PREFIX) or not is_seconds(seconds):
            continue
        pair = key[len(PREFIX):]
        for arrow in (m.start() for m in re.finditer("->", pair)):
            if candidate.up_pattern.fullmatch(pair[:arrow]) and candidate.down_pattern.fullmatch(pair[arrow + 2:]):
                if best is None or seconds > best[1]:
                    best = (key, seconds)
    return best


def confirm(candidate, data, settings):
    best = _best_match(candidate, data)
    if best is None or best[1] < settings["min_chain_seconds"]:
        return None
    field, seconds = best
    return finding(
        candidate.anchor, field, seconds,
        f"Test job '{candidate.downstream}' waits for test job '{candidate.upstream}' with no data dependency; "
        f"the two ran back to back for {seconds:g} s (median) and could run in parallel.", "medium")
