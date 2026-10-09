"""Normalize a V8 CPU profile (`node --cpu-prof`, a `.cpuprofile` JSON file) into per-file contract data.

Output per repository file that appears in the profile:
    {"profiler": "node-cpu", "sampled_seconds": 2.4,
     "function_time_share_line_<L>": 0.94,   # share of busy samples with the function starting at line L on the stack
     "time_share_line_<N>": 0.07}            # share of busy samples whose self time V8 attributes to line N

Shares use busy samples only (the `(idle)` node is excluded). The module's top-level frame (anonymous, 0:0) is
not a function and is never attributed. A function counts once per sample, so
recursion does not inflate it. Values below MIN_RECORDED_SHARE are omitted. `callFrame.lineNumber` is
0-based (function start); `positionTicks[].line` is 1-based.
"""
import posixpath
import urllib.parse

from owner_c.common import same_file

PROFILER = "node-cpu"
MIN_RECORDED_SHARE = 0.01


def _path(url: str) -> str:
    if url.startswith("file://"):
        url = urllib.parse.unquote(url[len("file://"):])
        if len(url) > 2 and url[0] == "/" and url[2] == ":":  # file:///C:/x -> C:/x
            url = url[1:]
    return url.replace("\\", "/")


def is_module_frame(cf: dict) -> bool:
    """V8's top-level script/module function: anonymous, starting at 0:0. Not an attributable function."""
    return cf["functionName"] == "" and cf["lineNumber"] == 0 and cf["columnNumber"] == 0


def normalize(raw: dict, files) -> dict:
    nodes = {n["id"]: n for n in raw["nodes"]}
    parent = {}
    for n in raw["nodes"]:
        for child in n.get("children", []):
            parent[child] = n["id"]
    samples, deltas = raw.get("samples", []), raw.get("timeDeltas", [])
    if not samples:
        return {}

    def idle(node_id):
        return nodes[node_id]["callFrame"]["functionName"] == "(idle)"

    function_time, line_ticks, busy = {}, {}, 0.0
    for i, node_id in enumerate(samples):
        if idle(node_id):
            continue
        weight = (deltas[i + 1] if i + 1 < len(deltas) else deltas[i]) / 1e6
        busy += weight
        seen, current = set(), node_id
        while current is not None:
            cf = nodes[current]["callFrame"]
            if cf["url"] and not is_module_frame(cf):
                key = (_path(cf["url"]), cf["lineNumber"] + 1)
                if key not in seen:
                    seen.add(key)
                    function_time[key] = function_time.get(key, 0.0) + weight
            current = parent.get(current)
    # self time per line, from V8's per-node line tick counts
    total_ticks = sum(n.get("hitCount", 0) for n in raw["nodes"] if n["callFrame"]["functionName"] != "(idle)")
    for n in raw["nodes"]:
        cf = n["callFrame"]
        if cf["url"] and n.get("positionTicks"):
            for t in n["positionTicks"]:
                key = (_path(cf["url"]), t["line"])
                line_ticks[key] = line_ticks.get(key, 0) + t["ticks"]
    if busy <= 0:
        return {}

    by_name = {}
    for (path, line), secs in function_time.items():
        by_name.setdefault(posixpath.basename(path), []).append((path, "function", line, secs / busy))
    for (path, line), ticks in line_ticks.items():
        if total_ticks:
            by_name.setdefault(posixpath.basename(path), []).append((path, "line", line, ticks / total_ticks))

    out = {}
    for repo_path, _content in files:
        rows = [r for r in by_name.get(posixpath.basename(repo_path), []) if same_file(r[0], repo_path)]
        if not rows:
            continue
        data = {"profiler": PROFILER, "sampled_seconds": round(busy, 3)}
        for _p, kind, line, share in sorted(rows, key=lambda r: (r[1], r[2])):
            if share >= MIN_RECORDED_SHARE:
                data[("function_time_share_line_" if kind == "function" else "time_share_line_") + str(line)] = round(share, 4)
        out[repo_path] = data
    return out
