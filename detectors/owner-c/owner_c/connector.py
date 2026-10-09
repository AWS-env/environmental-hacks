"""Turn repository files (and normalized artifacts) into contract v1 input payloads.

The connector decides scope and declares every exclusion in `context`, so a file left out here
is a documented choice rather than a silent skip. Each check only receives the files it accepts
(see `owner_c.langs.accepts`).
"""
from __future__ import annotations

import datetime

from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.checks import STATIC_CHECKS
from owner_c.common import MAX_FILE_BYTES, is_test_path
from owner_c.contract import artifact_source, build_input, static_source
from owner_c.langs import accepts

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "site-packages",
    "__pycache__", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache", ".next", "coverage",
}
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf",
                   ".eot", ".so", ".dll", ".exe", ".pyc", ".whl", ".jar", ".mp4", ".mp3", ".lock", ".map")
DEFAULT_SETTINGS = {"min_time_share": 0.05, "min_alloc_bytes": 10 * 1024 * 1024, "warn_days": 180,
                    "min_serial_calls": 3, "min_serial_seconds": 0.05}


def select_files(files, include_tests=False):
    """Keep scannable files: not vendored, not a test file (unless asked), not oversized."""
    out = []
    for path, content in files:
        path = path.replace("\\", "/")
        if SKIP_DIRS & set(path.split("/")) or path.lower().endswith(BINARY_SUFFIXES):
            continue
        if not include_tests and is_test_path(path):
            continue
        if len(content.encode("utf-8", "replace")) > MAX_FILE_BYTES:
            continue
        out.append((path, content))
    return out


def base_context(language, include_tests=False):
    return {"language": language, "exclude_tests": not include_tests,
            "max_file_bytes": MAX_FILE_BYTES, "excluded_dirs": sorted(SKIP_DIRS)}


def build_inputs(*, repository_id, commit_sha, scan_id, files, artifacts=None, include_tests=False,
                 settings=None, checks=None) -> list:
    """One input payload per check. `artifacts` maps profiler -> {path: normalized data}."""
    files = select_files(files, include_tests)
    artifacts = artifacts or {}
    wanted = set(checks) if checks else None
    merged = {"reference_date": datetime.date.today().isoformat(), **DEFAULT_SETTINGS, **(settings or {})}
    payloads = []
    for key, module in list(STATIC_CHECKS.items()) + list(ARTIFACT_CHECKS.items()):
        if wanted is not None and key not in wanted:
            continue
        mine = [(path, content) for path, content in files if accepts(module, path)]
        sources = [static_source(path, content) for path, content in mine]
        context = base_context(getattr(module, "LANGUAGE", "python"), include_tests)
        if hasattr(module, "SETTINGS"):
            context.update({name: merged[name] for name in module.SETTINGS})
        if key in ARTIFACT_CHECKS:
            kind = getattr(module, "SOURCE_KIND", "artifact")
            paths = {path for path, _ in mine}
            for path, data in sorted(artifacts.get(module.PROFILER, {}).items()):
                if path in paths:
                    sources.append(artifact_source(module.PROFILER, path, data, kind))
        if not sources:
            continue
        payloads.append(build_input(
            repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id, check_id=key,
            detector_version=module.DETECTOR_VERSION, context=context, sources=sources))
    return payloads
