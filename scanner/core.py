"""Shared types for adapters: the scan context they read and the per-check runs they return."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from shared.contracts.validation import fingerprint as fingerprint_of  # noqa: F401  (for adapters)

REPO_ROOT = Path(__file__).resolve().parents[1]


class AdapterUnavailable(RuntimeError):
    """The adapter's runtime or build is missing, so none of its checks can run."""


@dataclass
class ScanContext:
    repository_id: str
    commit_sha: str
    scan_id: str
    files: list  # [(repo-relative path, text)]
    collection: dict  # Limits.as_context(): settings that decided which files are in scope

    def context(self, **extra) -> dict:
        """Contract `context`: every setting that affected what the detector was given."""
        return {"collector": "scanner", **self.collection, **extra}

    def input(self, check_id, detector_version, context, sources) -> dict:
        scope = list(dict.fromkeys(source["scope_id"] for source in sources))
        return {"schema_version": "1.0", "kind": "input", "repository_id": self.repository_id,
                "scan_id": self.scan_id, "commit_sha": self.commit_sha, "check_id": check_id,
                "detector_version": detector_version, "context": context, "scope": scope,
                "sources": sources}


def is_test_path(path: str) -> bool:
    """Same rule as owner C's connector: test dirs, test_*.py, *_test.py, conftest.py."""
    parts = path.lower().split("/")
    name = parts[-1]
    return (any(p in {"test", "tests", "testing"} for p in parts[:-1]) or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def static_source(path: str, content: str) -> dict:
    return {"source_id": f"src:{path}", "scope_id": f"file:{path}", "kind": "static",
            "locator": path, "content": content}


@dataclass
class CheckRun:
    """One check's outcome from an adapter, before the orchestrator validates it.

    Exactly one of: `result` (detector ran; validated against `payload`), `error` (crash or
    adapter failure), `unavailable` (evidence the scan cannot collect) or `not_applicable`
    (no files in the repository that this check examines).
    """

    check_id: str
    owner: str
    adapter: str
    payload: dict | None = None
    result: dict | None = None
    error: str | None = None
    unavailable: str | None = None
    not_applicable: str | None = None
    notes: list = field(default_factory=list)


def evaluate_each(owner, adapter, payloads, evaluate) -> list:
    """Run an in-process detector per payload; an exception becomes that check's error, nothing more."""
    runs = []
    for payload in payloads:
        run = CheckRun(payload["check_id"], owner, adapter, payload=payload)
        try:
            run.result = evaluate(payload)
        except Exception as error:  # a crash must never read as a clean result
            run.error = f"detector raised {type(error).__name__}: {error}"
        runs.append(run)
    return runs


def import_path(path: Path) -> None:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
