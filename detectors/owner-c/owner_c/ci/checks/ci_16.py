"""CI-16: cache misconfiguration (a dependency-cache key that never changes, or collides across operating systems)."""
import re

from owner_c.ci.workflow import Hit

KEY = "CI-16"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows",
]
RECOMMENDATION = (
    "Build the cache key from the lockfile and the OS, for example "
    "`${{ runner.os }}-npm-${{ hashFiles('**/package-lock.json') }}`, with a shorter `restore-keys` prefix.")
LIMITATION = (
    "Release, publish and deploy workflows (and tag-triggered ones) are not evaluated: release workflows should not read from caches (zizmor `cache-poisoning`). "
    "Static pattern only: the cache hit rate is not exposed by the run history and is not measured. Only caches of "
    "PACKAGE-MANAGER content (npm/pnpm/yarn/pip/Maven/Gradle/cargo/go/NuGet stores, node_modules) are judged, because "
    "only their content follows a lockfile: a constant key on them is never refreshed. Tool and data caches (Sonar, "
    "Android AVD, fonts, installed binaries), keys that contain a version number, per-run keys, restore-only steps "
    "(`actions/cache/restore`) and keys built from `hashFiles`, sha, run ids or step/env/input outputs are not flagged. "
    "An OS part is required in the key only for platform-specific content (node_modules, venv, build output); Maven "
    "and Gradle jars are portable.")

_CHANGES_WITH_CONTENT = re.compile(
    r"hashFiles\(|github\.sha|github\.run_id|github\.run_number|github\.run_attempt|github\.event|"
    r"\bsteps\.|\benv\.|\binputs\.|\bneeds\.|\bvars\.")
_VERSIONED = re.compile(r"\bv?\d+\.\d+")
_OS_PART = re.compile(r"runner\.os|matrix\.|runner\.arch")
_PACKAGE_STORE = re.compile(
    r"~/\.npm|~/\.pnpm-store|~/\.yarn|~/\.cache/(?:pip|pypoetry|yarn|pnpm)|~/\.m2|~/\.gradle|~/\.cargo/registry|"
    r"~/go/pkg/mod|~/\.nuget/packages|(?:^|/)node_modules|vendor/bundle|\.venv")
_PLATFORM_SPECIFIC = re.compile(r"(?:^|/)node_modules|\.venv|(?:^|/)venv(?:/|$)|(?:^|/)target(?:/|$)|(?:^|/)build(?:/|$)")
_MATRIX_REF = re.compile(r"matrix\.([\w-]+)")


def _multi_os(job) -> bool:
    """The job fans out over more than one `runs-on` value through the matrix."""
    runs_on = job.data.get("runs-on")
    refs = _MATRIX_REF.findall(runs_on) if isinstance(runs_on, str) else []
    strategy = job.data.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not refs or not isinstance(matrix, dict):
        return False
    return any(isinstance(matrix.get(ref), list) and len(matrix[ref]) > 1 for ref in refs)


def run(wf, settings):
    if wf.deploy_like() or wf.tag_only():  # release workflows should not read caches (zizmor cache-poisoning)
        return []
    hits = []
    for job in wf.jobs():
        for step in job.steps():
            if not step.action.startswith("actions/cache") or step.action.endswith("/restore"):
                continue
            key, path = step.inputs.get("key"), step.inputs.get("path")
            if not isinstance(key, str) or not isinstance(path, str):
                continue
            first_path = next((p.strip() for p in path.splitlines() if p.strip()), "")
            if _PACKAGE_STORE.search(path) and not _CHANGES_WITH_CONTENT.search(key) and not _VERSIONED.search(key):
                hits.append(Hit(
                    f"job:{job.id}:cache:{first_path}:static-key", step.span[0], step.span[1],
                    f"The cache key for '{first_path}' in job '{job.id}' does not change with the dependencies, so "
                    "the first saved cache is never updated.", "medium"))
            if _multi_os(job) and _PLATFORM_SPECIFIC.search(path) and not _OS_PART.search(key):
                hits.append(Hit(
                    f"job:{job.id}:cache:{first_path}:no-os-in-key", step.span[0], step.span[1],
                    f"Job '{job.id}' runs on several operating systems but the cache key has no OS part, so "
                    "the platforms overwrite each other's platform-specific cache.", "medium"))
    return hits
