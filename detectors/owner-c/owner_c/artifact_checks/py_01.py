"""PY-01: `x in <list>` inside a loop, reported only when py-spy shows the line is hot."""
import ast

from owner_c.common import Confirmation

KEY = "PY-01"
DETECTOR_VERSION = "1.0.0"
PROFILER = "py-spy"
SETTINGS = {"min_time_share": (0, 1)}  # (exclusive minimum, inclusive maximum)
REFS = ["https://switowski.com/blog/membership-testing/"]
RECOMMENDATION = "Use a set or dict for repeated membership tests (O(1) instead of O(n) per lookup)."
LIMITATION = ("Needs a client-produced py-spy profile (speedscope) of a representative run. The receiver's type is "
              "not resolved, so a non-list container with a fast `in` can match; only files that appear in the "
              "profile are evaluated.")
_SET_LIKE_CALLS = {"set", "frozenset", "dict"}


def _is_set_like(node) -> bool:
    return isinstance(node, (ast.Set, ast.Dict, ast.SetComp, ast.DictComp)) or (
        isinstance(node, ast.Call) and getattr(node.func, "id", "") in _SET_LIKE_CALLS
    )


def find(ctx) -> list:
    set_names = {
        t.id for n in ast.walk(ctx.tree) if isinstance(n, ast.Assign) and _is_set_like(n.value)
        for t in n.targets if isinstance(t, ast.Name)
    }
    out = []
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Compare) and ctx.in_loop(node)):
            continue
        substring_test = isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
        for op, right in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Name, ast.Attribute)) \
                    and not substring_test \
                    and not (isinstance(right, ast.Name) and right.id in set_names):
                target = ast.unparse(right)
                out.append(ctx.candidate(node, f"{ctx.qualname(node)}:in:{target}",
                                         f"membership test `in {target}` inside a loop"))
    return out


def confirm(candidate, data: dict, settings: dict):
    best = max(
        ((data[f"time_share_line_{n}"], f"time_share_line_{n}")
         for n in range(candidate.line, candidate.end_line + 1) if f"time_share_line_{n}" in data),
        default=None)
    if best is None or best[0] < settings["min_time_share"]:
        return None
    share, field = best
    return Confirmation(
        field, share,
        f"{candidate.detail}; py-spy shows this line on the stack for {share:.0%} of sampled time "
        "(list lookup is O(n) per test).",
        "high" if share >= 0.25 else "medium")
