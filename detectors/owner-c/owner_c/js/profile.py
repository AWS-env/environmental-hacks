"""Shared confirmation logic for JS checks backed by V8 profiles (CPU or heap).

Both profile types attribute work to *functions* (their start line), so a candidate is matched through
its nearest enclosing function. If V8 inlined that function its time is credited to the caller and the
candidate is not confirmed; clients should profile with `node --no-opt` (see detectors/owner-c/README.md).
"""
from __future__ import annotations

from owner_c.common import Confirmation

CPU_FUNCTION = "function_time_share_line_"
CPU_LINE = "time_share_line_"
HEAP_FUNCTION = "allocated_bytes_function_line_"


def confirm_cpu(candidate, data, settings, what):
    """Confirm when the enclosing function holds >= min_time_share of busy CPU samples."""
    line = candidate.meta  # start line of the nearest enclosing function (None at module level)
    if line is None:
        return None
    share = data.get(f"{CPU_FUNCTION}{line}")
    if share is None or share < settings["min_time_share"]:
        return None
    extra, confidence = (), "medium"
    own = max(((data[f"{CPU_LINE}{n}"], n) for n in range(candidate.line, candidate.end_line + 1)
               if f"{CPU_LINE}{n}" in data), default=None)
    if own is not None:
        extra, confidence = ((f"{CPU_LINE}{own[1]}", own[0]),), "high"
    return Confirmation(
        f"{CPU_FUNCTION}{line}", share,
        f"{candidate.detail}; the CPU profile shows its enclosing function (line {line}) on the stack for "
        f"{share:.0%} of busy samples ({what}).",
        confidence, extra)


def confirm_heap(candidate, data, settings, what):
    """Confirm when the enclosing function allocated >= min_alloc_bytes (including objects freed by GC)."""
    line = candidate.meta
    if line is None:
        return None
    nbytes = data.get(f"{HEAP_FUNCTION}{line}")
    if nbytes is None or nbytes < settings["min_alloc_bytes"]:
        return None
    return Confirmation(
        f"{HEAP_FUNCTION}{line}", nbytes,
        f"{candidate.detail}; the heap profile attributes {nbytes / (1024 * 1024):.0f} MiB of allocations to its "
        f"enclosing function (line {line}) ({what}).",
        "medium")
