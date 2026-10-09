"""Turn repository workflow files (and normalized run history) into contract v1 input payloads.

The connector decides scope and declares every limit in `context`, so a file left out here is a documented
choice rather than a silent skip. Workflow files are `.github/workflows/*.yml|yaml`.
"""
from __future__ import annotations

from owner_c.ci.registry import CHECKS, PROFILER, kind
from owner_c.ci.workflow import WORKFLOW_PATH
from owner_c.contract import artifact_source, build_input, static_source

MAX_FILE_BYTES = 1_000_000
PROVIDER = "github-actions"


def select_workflows(files):
    """Keep workflow files that are not oversized."""
    out = []
    for path, content in files:
        path = path.replace("\\", "/")
        if WORKFLOW_PATH.match(path) and len(content.encode("utf-8", "replace")) <= MAX_FILE_BYTES:
            out.append((path, content))
    return out


def base_context() -> dict:
    return {"provider": PROVIDER, "workflow_glob": ".github/workflows/*.{yml,yaml}", "max_file_bytes": MAX_FILE_BYTES,
            "not_scanned": ["composite actions (.github/actions/**/action.yml)",
                            "reusable workflows defined in other repositories",
                            "CI providers other than GitHub Actions"]}


def build_inputs(*, repository_id, commit_sha, scan_id, files, artifacts=None, settings=None, checks=None) -> list:
    """One input payload per check. `artifacts` maps profiler -> {workflow path: normalized history data}."""
    workflows = select_workflows(files)
    history = (artifacts or {}).get(PROFILER, {})
    wanted = set(checks) if checks else None
    payloads = []
    for key, module in CHECKS.items():
        if wanted is not None and key not in wanted:
            continue
        style = kind(module)
        sources = []
        if style in ("static", "confirmed"):
            sources = [static_source(path, content) for path, content in workflows]
        if style in ("history", "confirmed"):
            known = {path for path, _ in workflows}
            for path, data in sorted(history.items()):
                if style == "history" or path in known:
                    sources.append(artifact_source(PROFILER, path, data))
        if not sources:
            continue
        context = base_context()
        context.update({name: {**module.DEFAULTS, **(settings or {})}[name] for name in module.SETTINGS})
        payloads.append(build_input(
            repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id, check_id=key,
            detector_version=module.DETECTOR_VERSION, context=context, sources=sources))
    return payloads
