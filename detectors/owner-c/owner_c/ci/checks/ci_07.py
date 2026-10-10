"""CI-07: Docker images built in CI with no layer cache."""
import re

from owner_c.ci.workflow import Hit, truthy

KEY = "CI-07"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.docker.com/build/ci/github-actions/cache/",
    "https://github.com/docker/build-push-action",
]
RECOMMENDATION = (
    "Add `cache-from: type=gha` and `cache-to: type=gha,mode=max` to `docker/build-push-action` "
    "(or `--cache-from`/`--cache-to` to `docker buildx build`) so unchanged layers are reused.")
LIMITATION = (
    "Release, publish and deploy workflows (and tag-triggered ones) are not evaluated: release workflows should not read from caches (zizmor `cache-poisoning`). "
    "Static pattern only: build time is not measured, and the compose file or bake file is not read, so `docker compose build` "
    "and `docker/bake-action` are reported at low confidence (a `cache_from` in the compose file or a cache set in the bake "
    "file would already cache the layers). Deliberate clean builds (`no-cache`), "
    "caches set in the builder or Dockerfile (`RUN --mount=type=cache`), scripts that pass cache flags through a "
    "variable, non-build docker commands and a second build of the same context and file in one job (it reuses "
    "the first build's local cache) are not flagged.")

_BUILD = re.compile(r"\bdocker\s+(buildx\s+)?build\b")
_COMPOSE = re.compile(r"\bdocker[\s-]compose\b([^;&|]*)")
_COMPOSE_VALUE_FLAGS = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory"}


def _compose_builds(command: str) -> bool:
    """`docker compose [-f x] build` or `... up --build`: the sub-command, not any word, must be the build."""
    match = _COMPOSE.search(command)
    if not match:
        return False
    tokens = match.group(1).split()
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in _COMPOSE_VALUE_FLAGS else 1
    if i >= len(tokens):
        return False
    return tokens[i] == "build" or (tokens[i] == "up" and "--build" in tokens[i + 1:])
_CACHE_HINT = re.compile(r"--cache-(?:from|to)|--no-cache|cache[-_]?args", re.I)


def run(wf, settings):
    if wf.deploy_like() or wf.tag_only():  # release workflows should not read caches (zizmor cache-poisoning)
        return []
    hits = []
    for job in wf.jobs():
        built = set()  # (context, file) of builds already seen in this job
        for step in job.steps():
            label = step.name
            if step.action == "docker/build-push-action":
                inputs = step.inputs
                target = (inputs.get("context"), inputs.get("file"))
                if target in built:
                    continue
                built.add(target)
                if "cache-from" in inputs or "cache-to" in inputs or truthy(inputs.get("no-cache")):
                    continue
                hits.append(Hit(
                    f"job:{job.id}:docker-build:{label or step.action}", step.span[0], step.span[1],
                    f"Job '{job.id}' builds an image with docker/build-push-action and no `cache-from`, so every run "
                    "rebuilds all layers.", "medium"))
                continue
            if step.action == "docker/bake-action":
                inputs = step.inputs
                targets = str(inputs.get("set", ""))
                if "cache-from" in targets or "cache-to" in targets or "no-cache" in targets or truthy(inputs.get("no-cache")):
                    continue
                hits.append(Hit(
                    f"job:{job.id}:docker-build:{label or step.action}", step.span[0], step.span[1],
                    f"Job '{job.id}' builds images with docker/bake-action and no cache set in the step "
                    "(a cache in the bake file is not visible here).", "low"))
                continue
            script = step.data.get("run")
            if isinstance(script, str) and not _CACHE_HINT.search(script) and any(_compose_builds(c) for c in step.commands()):
                hits.append(Hit(
                    f"job:{job.id}:docker-build:{label or 'docker compose build'}", step.span[0], step.span[1],
                    f"Job '{job.id}' runs `docker compose build` and the layer cache is not restored in the step "
                    "(a `cache_from` in the compose file is not visible here).", "low"))
                continue
            match = _BUILD.search(script) if isinstance(script, str) else None
            if not match or _CACHE_HINT.search(script):
                continue
            cmd = "docker buildx build" if match.group(1) else "docker build"
            hits.append(Hit(
                f"job:{job.id}:docker-build:{label or cmd}", step.span[0], step.span[1],
                f"Job '{job.id}' runs `{cmd}` with no layer cache flags, so every run rebuilds all layers.", "low"))
    return hits
