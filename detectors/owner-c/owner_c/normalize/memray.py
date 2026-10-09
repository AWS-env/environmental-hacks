"""Normalize `memray stats --json` into per-file contract artifact data.

memray lists only its top allocation sites (`top_allocations_by_size`, location `func:file:line`).
Output per repository file with a recorded site:
    {"profiler": "memray", "allocated_bytes_line_5": 592115200, ...}
Allocations made inside `copy.deepcopy` are attributed by memray to the stdlib copy.py, not to the
caller, so files that mention deepcopy also get `allocated_bytes_deepcopy_internals`.
"""
import re

from owner_c.common import same_file

PROFILER = "memray"
_LOCATION = re.compile(r"^(?P<func>[^:]*):(?P<file>.+):(?P<line>\d+)$")


def _sites(raw: dict):
    for item in raw.get("top_allocations_by_size", []):
        m = _LOCATION.match(item.get("location", ""))
        if m:
            yield m["func"], m["file"], int(m["line"]), item["size"]


def normalize(raw: dict, files) -> dict:
    sites = list(_sites(raw))
    deepcopy_bytes = max((b for func, f, _l, b in sites
                          if f.replace("\\", "/").endswith("/copy.py") and func.startswith("_deepcopy")), default=None)
    out = {}
    for path, content in files:
        data = {"profiler": PROFILER}
        for _func, frame_file, line, nbytes in sites:
            if same_file(frame_file, path):
                key = f"allocated_bytes_line_{line}"
                data[key] = data.get(key, 0) + nbytes
        if deepcopy_bytes is not None and "deepcopy" in content:
            data["allocated_bytes_deepcopy_internals"] = deepcopy_bytes
        if len(data) > 1:
            out[path] = data
    return out
