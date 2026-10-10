"""CI-13: redundant matrix jobs (duplicate axis values, include entries that add nothing)."""
import json

from owner_c.ci.workflow import Hit, is_expression

KEY = "CI-13"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {}
DEFAULTS = {}
REFS = [
    "https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs",
    "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idstrategymatrix",
]
RECOMMENDATION = "Remove duplicate axis values and `include` entries that add no new key; they only repeat work."
LIMITATION = (
    "actionlint already reports duplicate matrix values; the no-op `include` rule is derived from GitHub's documented include semantics (an include entry that extends no combination and adds no new key creates nothing). "
    "Static pattern only: whether a combination is needed is not known, so wide matrices are not reported (real "
    "projects keep wide matrices on purpose, e.g. supported versions). Matrices built at runtime (dynamic, with "
    "`fromJSON` or other expressions) are not evaluated.")


def _norm(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _axes(matrix):
    """Axis name -> list of values, or None when any axis is an expression (dynamic)."""
    axes = {}
    for key, value in matrix.items():
        if key in ("include", "exclude"):
            continue
        if is_expression(value):
            return None
        axes[key] = value if isinstance(value, list) else [value]
    return axes


def _includes(matrix):
    value = matrix.get("include")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def _noop_include(axes, include) -> bool:
    """An include entry made only of axis keys with values already in the axes adds no job."""
    return bool(include) and all(
        k in axes and any(_norm(v) == _norm(a) for a in axes[k]) for k, v in include.items())


def run(wf, settings):
    hits = []
    for job in wf.jobs():
        strategy = job.data.get("strategy")
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        if not isinstance(matrix, dict):
            continue
        axes = _axes(matrix)
        if axes is None:
            continue
        start, end = strategy.spans["matrix"]
        for axis, values in axes.items():
            normalised = [_norm(v) for v in values]
            if len(normalised) != len(set(normalised)):
                hits.append(Hit(f"job:{job.id}:matrix:duplicate-axis:{axis}", start, end,
                                f"Matrix axis '{axis}' in job '{job.id}' lists the same value more than once, "
                                "so the same job runs repeatedly.", "high"))
        if any(_noop_include(axes, inc) for inc in _includes(matrix)):
            hits.append(Hit(f"job:{job.id}:matrix:redundant-include", start, end,
                            f"An `include` entry in job '{job.id}' repeats an existing combination and adds no "
                            "new key, so it creates no extra coverage.", "medium"))
    return hits
