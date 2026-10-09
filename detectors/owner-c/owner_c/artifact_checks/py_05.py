"""PY-05: temporary list built for a single pass, reported only when memray shows big allocations."""
import ast

from owner_c.common import Confirmation

KEY = "PY-05"
DETECTOR_VERSION = "1.0.0"
PROFILER = "memray"
SETTINGS = {"min_alloc_bytes": (0, None)}  # (exclusive minimum, no maximum)
REFS = ["https://docs.astral.sh/ruff/rules/unnecessary-comprehension-in-call/"]
RECOMMENDATION = "Pass a generator expression (or the lazy map/filter object) instead of building a temporary list."
LIMITATION = ("Needs a client-produced memray stats capture (`memray stats --json`), which lists only the top "
              "allocation sites; files without a recorded allocation site are not evaluated.")
_SINGLE_PASS = {"sum", "min", "max", "any", "all"}
_LAZY = {"map", "filter"}


def find(ctx) -> list:
    out = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        name = ctx.dotted(node.func)
        arg = node.args[0] if node.args else None
        qual = ctx.qualname(node)
        if name in _SINGLE_PASS and isinstance(arg, ast.ListComp):
            out.append(ctx.candidate(node, f"{qual}:{name}(listcomp)",
                                     f"{name}([...]) builds a temporary list for a single pass"))
        elif name in _SINGLE_PASS and isinstance(arg, ast.Call) and ctx.dotted(arg.func) == "list" \
                and arg.args and isinstance(arg.args[0], ast.Call) and ctx.dotted(arg.args[0].func) in _LAZY:
            out.append(ctx.candidate(node, f"{qual}:{name}(list(map))",
                                     f"{name}(list(map/filter(...))) builds a temporary list"))
        elif name == "list" and isinstance(arg, ast.Call) and ctx.dotted(arg.func) in _LAZY:
            parent = ctx.parent(node)
            if isinstance(parent, (ast.For, ast.AsyncFor)) and parent.iter is node:
                out.append(ctx.candidate(node, f"{qual}:for-list(map)",
                                         "list(map/filter(...)) materialized only to be iterated once"))
    return out


def confirm(candidate, data: dict, settings: dict):
    best = max(
        ((data[f"allocated_bytes_line_{n}"], f"allocated_bytes_line_{n}")
         for n in range(candidate.line, candidate.end_line + 1) if f"allocated_bytes_line_{n}" in data),
        default=None)
    if best is None or best[0] < settings["min_alloc_bytes"]:
        return None
    nbytes, field = best
    return Confirmation(
        field, nbytes,
        f"{candidate.detail}; memray attributes {nbytes / (1024 * 1024):.0f} MiB of allocations to this line.",
        "medium")
