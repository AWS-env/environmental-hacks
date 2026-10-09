"""CI-08: a build that wipes the build-output cache it just restored (full builds where a cached build is possible)."""
import re

from owner_c.ci.workflow import Hit

KEY = "CI-08"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.gradle.org/current/userguide/build_cache.html",
    "https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows",
]
RECOMMENDATION = (
    "Do not `clean` right after restoring a build-output cache: it throws away the cached result. Drop `clean`, "
    "or stop caching the build directory.")
LIMITATION = (
    "Static pattern only: build duration is not measured, and the run history carries no changed-file data to prove "
    "an incremental build was possible. Explicit flags such as `--no-build-cache` or `--rerun-tasks` are NOT reported: "
    "on real repositories they are almost always deliberate (CodeQL and Infer need a fresh compile, tests are forced "
    "to re-run, dependency warm-ups). A cache keyed per run (`github.run_id`, `github.sha`) is a hand-off between jobs, "
    "not an incremental cache, and is not flagged. Release/publish/deploy jobs and tag-triggered workflows build clean "
    "on purpose; remote-cache tools (Turborepo, Nx Cloud, Bazel) are not evaluated.")

_CLEAN = re.compile(r"\bmvnw?\b.*\bclean\b|\bgradlew?\b.*\bclean\b|\bmake\s+clean\b|\bcargo\s+clean\b|"
                    r"\bnpm\s+run\s+clean\b|\brm\s+-rf\s+(?:\./)?(?:build|dist|target|out)\b")
# `gradlew clean` removes `build/`, not the project's `.gradle/` (task history), so `.gradle` is not listed.
_BUILD_OUTPUT = re.compile(r"^(?:\./)?(?:target|build|dist|out|\.next|\.nx|\.turbo)(?:/\*{0,2})?$")
_PER_RUN_KEY = re.compile(r"github\.run_id|github\.run_number|github\.run_attempt|github\.sha")
_RELEASE_JOB = re.compile(r"release|publish|deploy", re.I)


def _caches_build_output(step) -> bool:
    """A build-output cache that is meant to be reused incrementally (not keyed per run)."""
    if not step.action.startswith("actions/cache"):
        return False
    path, key = step.inputs.get("path"), step.inputs.get("key")
    if not isinstance(path, str) or (isinstance(key, str) and _PER_RUN_KEY.search(key)):
        return False
    tokens = [t.strip() for line in path.splitlines() for t in line.split(",") if t.strip()]
    return any(_BUILD_OUTPUT.match(t) for t in tokens)


def run(wf, settings):
    if wf.tag_only():
        return []
    hits = []
    for job in wf.jobs():
        name = job.data.get("name") if isinstance(job.data.get("name"), str) else ""
        if job.data.get("environment") or _RELEASE_JOB.search(f"{job.id} {name}"):
            continue
        cache_seen = reported = False
        for step in job.steps():
            if _caches_build_output(step):
                cache_seen = True
                continue
            if cache_seen and not reported and any(_CLEAN.search(c) for c in step.commands()):
                reported = True
                hits.append(Hit(
                    f"job:{job.id}:full-build:clean-with-build-cache", step.span[0], step.span[1],
                    f"Job '{job.id}' restores a build-output cache and then cleans, so the cached build "
                    "result is discarded.", "medium"))
    return hits
