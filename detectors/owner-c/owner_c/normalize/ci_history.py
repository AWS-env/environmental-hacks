"""Artifact normalizer for `ci_history`: the client's GitHub Actions run-history bundle -> {workflow path: data}.

Plugs the CI category into the shared upload handshake: the presign allow-list is derived from this registry, the
client uploads `ci_history.json` next to `repo.zip` and `manifest.json`, and `normalize_all` returns
`{PROFILER: {workflow path: normalized data}}`, which is exactly what the CI connector reads. The data is parsed
only; nothing in it is ever executed.
"""
from __future__ import annotations

from owner_c.ci.normalize.github_actions import (BUNDLE_SCHEMA, MAX_WORKFLOWS, PROFILER, RawHistoryError,
                                                 normalize as normalize_workflow)

__all__ = ["PROFILER", "normalize"]


def check_repository(raw, repository_id: str) -> None:
    """A bundle names the repository it was collected for; one collected for another repository is rejected, so
    uploaded history cannot be attached to a repository it does not describe. (Who may request upload URLs for a
    repository is decided by the presign Lambda's IAM access, not here.)"""
    if not isinstance(raw, dict) or not repository_id.startswith("github:"):
        return
    named = repository_id[len("github:"):]
    # a bundle must name its repository; a single workflow document is checked when it names one
    if raw.get("schema") == BUNDLE_SCHEMA and raw.get("repository") != named or \
            raw.get("schema") != BUNDLE_SCHEMA and raw.get("repository") not in (None, named):
        raise ValueError("ci_history was collected for a different repository than the upload")


def normalize(raw, files=None) -> dict:
    """`raw` is a bundle (`{"schema": BUNDLE_SCHEMA, "workflows": [doc, ...]}`) or a single workflow document."""
    if isinstance(raw, dict) and raw.get("schema") == BUNDLE_SCHEMA:
        docs = raw.get("workflows")
        if not isinstance(docs, list) or len(docs) > MAX_WORKFLOWS:
            raise ValueError(f"ci_history bundle needs a `workflows` list of at most {MAX_WORKFLOWS} documents")
    else:
        docs = [raw]
    out = {}
    for doc in docs:
        try:
            data = normalize_workflow(doc)
        except RawHistoryError as error:
            raise ValueError(f"ci_history: {error}") from error
        out[data["workflow_path"]] = data
    return out
