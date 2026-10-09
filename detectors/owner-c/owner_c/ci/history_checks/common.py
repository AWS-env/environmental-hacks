"""Shared helpers for checks that read normalized GitHub Actions run history (see ci/normalize/)."""
PROFILER = "github-actions"
RUNS_SETTING = {"min_runs": (0, None)}
RUNS_DEFAULT = {"min_runs": 5}


def is_count(value) -> bool:
    """A non-negative integer (bool is not a count)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_seconds(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def finding(anchor, field, value, summary, confidence) -> dict:
    return {"anchor": anchor, "field": field, "value": value, "summary": summary, "confidence": confidence}
