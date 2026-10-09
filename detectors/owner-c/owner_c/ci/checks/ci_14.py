"""CI-14: oversized runners for light jobs (lint, format, docs...)."""
import re

from owner_c.ci.patterns import runs_build, runs_tests
from owner_c.ci.workflow import Hit, is_expression

KEY = "CI-14"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {"min_oversized_cores": (0, None)}
DEFAULTS = {"min_oversized_cores": 8}
REFS = [
    "https://docs.github.com/en/actions/using-github-hosted-runners/using-larger-runners/about-larger-runners",
]
RECOMMENDATION = "Run lint, format and docs jobs on a standard runner; keep larger runners for build and test jobs."
LIMITATION = (
    "GitHub names a larger runner label after the runner (official examples: `ubuntu-24.04-16core`, `windows-2022-16core`, `macos-26-xlarge`), so there is no fixed scheme; core counts are read from a `-N-cores` / `-Ncore` suffix. "
    "Static pattern only: runner utilization is not measured (CI-15 is not implemented). Larger-runner label names are "
    "user-defined, so labels are matched by a core count (`-N-cores`) or macOS `-large`/`-xlarge` suffix. Build and "
    "test jobs, expression `runs-on` values and self-hosted runners are not flagged.")

_CORES = re.compile(r"(?:^|[-_.])(\d+)[-_ ]?(?:cores?|cpus?|vcpus?)\b", re.I)
_MAC_LARGE = re.compile(r"macos.*-(?:2?x?large)\b", re.I)
_LIGHT_JOB = re.compile(r"lint|format|fmt|style|docs?\b|typecheck|type-check|spell|label|stale|changelog", re.I)
# A job named "Rust lints (fmt, check, clippy, tests, doc)" is heavy whatever its id says.
_HEAVY_NAME = re.compile(r"test|build|compile|clippy|nextest|bench|e2e|integration", re.I)


def _oversized(label: str, min_cores: float) -> bool:
    cores = _CORES.search(label)
    if cores and int(cores.group(1)) >= min_cores:
        return True
    return bool(_MAC_LARGE.search(label))


def _light(job) -> bool:
    name = job.data.get("name") if isinstance(job.data.get("name"), str) else ""
    if not _LIGHT_JOB.search(f"{job.id} {name}") or _HEAVY_NAME.search(f"{job.id} {name}"):
        return False
    return not any(runs_tests(c) or runs_build(c) for s in job.steps() for c in s.commands())


def run(wf, settings):
    hits = []
    for job in wf.jobs():
        labels = job.runs_on_labels()
        if not labels or any(is_expression(label) for label in labels) or job.self_hosted:
            continue
        big = next((label for label in labels if _oversized(label, settings["min_oversized_cores"])), None)
        if big is None or not _light(job):
            continue
        start, end = job.data.spans["runs-on"]
        hits.append(Hit(f"job:{job.id}:runner:{big}", start, end,
                        f"Light job '{job.id}' runs on the larger runner '{big}'; a standard runner is enough "
                        "for lint, format and docs work.", "low"))
    return hits
