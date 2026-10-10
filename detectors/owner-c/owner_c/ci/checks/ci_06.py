"""CI-06: a job installs dependencies but nothing in it caches them."""
import re

from owner_c.ci.patterns import NOT_A_RUN
from owner_c.ci.workflow import Hit

KEY = "CI-06"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows",
    "https://github.com/actions/setup-node#caching-global-packages-data",
]
RECOMMENDATION = (
    "Cache the package manager's download cache: set the `cache` input on `setup-node`/`setup-python`/`setup-java`, "
    "or add `actions/cache` keyed on the lockfile with `hashFiles(...)`.")
LIMITATION = (
    "Release, publish and deploy workflows (and tag-triggered ones) are not evaluated: release workflows should not read from caches (zizmor `cache-poisoning`). "
    "Static pattern only: install time is not measured. A job that calls a local or composite action, a reusable "
    "workflow or a self-hosted runner is not flagged (the cache may be hidden or persistent). `pip install` counts "
    "only when it installs from a requirements/constraints file or the project itself, not a single tool. Go "
    "modules, conda and caching configured outside the workflow are not evaluated (uv through `astral-sh/setup-uv` caches by default on GitHub-hosted runners, so it counts as cached). `actions/setup-node` v6 and later caches npm "
    "automatically when package.json names npm in `packageManager` (GitHub's setup-node README); package.json is not read, "
    "so an npm hit in a job with such a setup-node step is reported at low confidence. Yarn and pnpm are never cached automatically.")

_INSTALLERS = (
    ("npm", re.compile(r"\bnpm\s+(?:ci|install|i)\b")),
    ("yarn", re.compile(r"^\s*yarn(?:\s+install)?\s*(?:--[\w-]+(?:=\S+)?\s*)*$")),
    ("pnpm", re.compile(r"\bpnpm\s+(?:install|i)\b")),
    # Project dependencies only: `-r file`, `-c file` or the project itself (`.`, `.[extra]`, `-e .`).
    # A single tool install (`pip install build`) is too small to be worth a cache.
    ("pip", re.compile(r"\bpip3?\s+install\b(?=.*(?:\s-[rc]\b|\s--(?:requirement|constraint)\b|\s-e\s|\s\.(?:\[|\s|$)))")),
    ("poetry", re.compile(r"\bpoetry\s+install\b")),
    ("pipenv", re.compile(r"\bpipenv\s+(?:install|sync)\b")),
    ("maven", re.compile(r"\bmvnw?\b|\./mvnw\b")),
    ("gradle", re.compile(r"(?<![.\w-])gradlew?\b|\./gradlew\b")),
    ("bundler", re.compile(r"\bbundle\s+install\b")),
    ("dotnet", re.compile(r"\bdotnet\s+restore\b")),
    ("cargo", re.compile(r"\bcargo\s+(?:build|test|fetch)\b")),
)
_VERSION_TAG = re.compile(r"@v?(\d+)\b")
_GLOBAL_NPM = re.compile(r"\s(?:-g|--global)\b")
_PIP_SELF_UPGRADE = re.compile(r"\bpip3?\s+install\s+(?:--upgrade|-U)\s+pip\s*$")
_CACHING_ACTIONS = {
    "gradle/actions/setup-gradle", "gradle/gradle-build-action", "Swatinem/rust-cache", "astral-sh/setup-uv",
}


def _job_caches(job) -> bool:
    for step in job.steps():
        action = step.action
        if action.startswith("actions/cache"):
            return True
        if action in _CACHING_ACTIONS:
            return True
        if (action.startswith("actions/setup-") and step.inputs.get("cache")) or (
                action == "ruby/setup-ruby" and step.inputs.get("bundler-cache")):
            return True
    return False


def _node_may_cache_itself(job) -> bool:
    """`actions/setup-node` v6+ turns npm caching on by itself when package.json names npm in `packageManager` (not readable
    here); an unparsable (commit-pinned) version may be one of those too."""
    for step in job.steps():
        if step.action == "actions/setup-node" and "cache" not in step.inputs:
            tag = _VERSION_TAG.search(step.uses)
            if tag is None or int(tag.group(1)) >= 6:
                return True
    return False


def _installs(command: str) -> list:
    if NOT_A_RUN.search(command):  # `grep '\.gradle$'`, `echo "mvn ..."` only mention a tool
        return []
    found = []
    for eco, pattern in _INSTALLERS:
        if not pattern.search(command):
            continue
        if eco == "npm" and _GLOBAL_NPM.search(command):
            continue
        if eco == "pip" and _PIP_SELF_UPGRADE.search(command):
            continue
        found.append(eco)
    return found


def run(wf, settings):
    if wf.deploy_like() or wf.tag_only():  # release workflows should not read caches (zizmor cache-poisoning)
        return []
    hits = []
    for job in wf.jobs():
        if job.opaque or job.self_hosted or _job_caches(job):
            continue
        seen = set()
        for step in job.steps():
            for command in step.commands():
                for eco in _installs(command):
                    if eco in seen:
                        continue
                    seen.add(eco)
                    uncertain = eco == "npm" and _node_may_cache_itself(job)
                    hits.append(Hit(
                        f"job:{job.id}:deps:{eco}", step.span[0], step.span[1],
                        f"Job '{job.id}' installs {eco} dependencies but has no cache step or `cache` input, so every "
                        "run downloads them again."
                        + (" setup-node v6+ caches npm by itself when package.json sets `packageManager` to npm "
                           "(package.json is not read here)." if uncertain else ""),
                        "low" if uncertain else "medium"))
    return hits
