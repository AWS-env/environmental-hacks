"""Normalize a V8 sampling heap profile (`.heapprofile` JSON) into per-file contract data.

By default V8 (and `node --heap-prof`) reports only objects still alive when the profile stops, which
hides churn such as temporary arrays and JSON clones. The collector shipped with this detector
(`collect-heap.js`, run as `node -r ./collect-heap.js app.js`) starts the sampler with
`includeObjectsCollectedByMajorGC/MinorGC`, so freed objects are counted too.

Output per repository file with allocations:
    {"profiler": "node-heap", "allocated_bytes_function_line_<L>": 537802800}
where L is the 1-based start line of the allocating function (`callFrame.lineNumber` is 0-based) and the
value is the function's sampled allocation size, including builtins it called (map, filter, JSON.*). Values under MIN_RECORDED_BYTES are omitted.
"""
import posixpath

from owner_c.common import same_file
from owner_c.normalize.cpuprofile import _path, is_module_frame

PROFILER = "node-heap"
MIN_RECORDED_BYTES = 64 * 1024


def _owned_sizes(head):
    """Yield ((path, 1-based function line), selfSize) crediting builtin frames (no URL) to their JS caller."""
    stack = [(head, None)]
    while stack:
        node, owner = stack.pop()
        cf = node["callFrame"]
        if cf.get("url"):
            owner = None if is_module_frame(cf) else (_path(cf["url"]), cf["lineNumber"] + 1)
        if node.get("selfSize") and owner is not None:
            yield owner, node["selfSize"]
        stack.extend((child, owner) for child in node.get("children", []))


def normalize(raw: dict, files) -> dict:
    sizes = {}
    for key, size in _owned_sizes(raw["head"]):
        sizes[key] = sizes.get(key, 0) + size
    by_name = {}
    for (path, line), nbytes in sizes.items():
        by_name.setdefault(posixpath.basename(path), []).append((path, line, nbytes))
    out = {}
    for repo_path, _content in files:
        rows = [r for r in by_name.get(posixpath.basename(repo_path), []) if same_file(r[0], repo_path)]
        data = {"profiler": PROFILER}
        for _p, line, nbytes in sorted(rows, key=lambda r: r[1]):
            if nbytes >= MIN_RECORDED_BYTES:
                data[f"allocated_bytes_function_line_{line}"] = nbytes
        if len(data) > 1:
            out[repo_path] = data
    return out
