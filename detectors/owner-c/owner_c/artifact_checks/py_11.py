"""PY-11: deepcopy / .copy() of data, reported only when memray shows big allocations."""
import ast

from owner_c.common import Confirmation

KEY = "PY-11"
DETECTOR_VERSION = "1.0.0"
PROFILER = "memray"
SETTINGS = {"min_alloc_bytes": (0, None)}  # (exclusive minimum, no maximum)
REFS = ["https://docs.python.org/3/library/copy.html"]
RECOMMENDATION = ("Avoid the copy when the result is never mutated, or use a shallow copy / copy-on-write "
                  "(e.g. DataFrame.copy(deep=False) on pandas 3).")
LIMITATION = ("Needs a client-produced memray stats capture (`memray stats --json`), which lists only the top "
              "allocation sites. memray attributes deepcopy allocations to copy.py internals, so for deepcopy the "
              "call site cannot be pinned exactly; whether the copy is necessary is a judgement the code cannot prove.")


def find(ctx) -> list:
    out = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        is_deepcopy = ctx.dotted(node.func) in {"copy.deepcopy", "deepcopy"}
        is_loop_copy = (isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
                        and not node.args and ctx.in_loop(node))
        if is_deepcopy or is_loop_copy:
            callee = ast.unparse(node.func)
            out.append(ctx.candidate(node, f"{ctx.qualname(node)}:{callee}", f"{callee}() copies data"))
    return out


def confirm(candidate, data: dict, settings: dict):
    best = max(
        ((data[f"allocated_bytes_line_{n}"], f"allocated_bytes_line_{n}")
         for n in range(candidate.line, candidate.end_line + 1) if f"allocated_bytes_line_{n}" in data),
        default=None)
    how = "memray attributes this to the line"
    if (best is None or best[0] < settings["min_alloc_bytes"]) and "deepcopy" in candidate.detail:
        internals = data.get("allocated_bytes_deepcopy_internals")
        if internals is not None:
            best, how = (internals, "allocated_bytes_deepcopy_internals"), "memray attributes it to copy.deepcopy internals"
    if best is None or best[0] < settings["min_alloc_bytes"]:
        return None
    nbytes, field = best
    return Confirmation(
        field, nbytes,
        f"{candidate.detail}; possibly unnecessary copy: {nbytes / (1024 * 1024):.0f} MiB allocated ({how}).",
        "medium")
