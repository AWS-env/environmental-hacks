"""All CI checks by key: static (workflow YAML), history (run history) and confirmed (both)."""
from owner_c.ci.checks import STATIC_CHECKS
from owner_c.ci.history_checks import HISTORY_CHECKS

CHECKS = {**STATIC_CHECKS, **HISTORY_CHECKS}
PROFILER = "github-actions"


def kind(module) -> str:
    return getattr(module, "KIND", "static")
