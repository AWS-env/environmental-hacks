"""Normalize a py-spy capture (`py-spy record -f speedscope`) into per-file contract artifact data.

Output per repository file that appears in the profile:
    {"profiler": "py-spy", "sampled_seconds": 3.66, "time_share_line_5": 0.6366, ...}
where `time_share_line_N` is the fraction of sampled time that line N was on the stack (counted
once per sample). Lines under MIN_RECORDED_SHARE are omitted to keep the payload small.
"""
import posixpath

from owner_c.common import same_file

PROFILER = "py-spy"
MIN_RECORDED_SHARE = 0.01


def _line_seconds(raw: dict):
    frames = raw["shared"]["frames"]
    seconds, total = {}, 0.0
    for prof in raw["profiles"]:
        if prof.get("type") != "sampled":
            continue
        for stack, weight in zip(prof["samples"], prof["weights"]):
            total += weight
            for key in {(frames[i].get("file", ""), frames[i].get("line")) for i in stack}:
                if key[1] is not None:
                    seconds[key] = seconds.get(key, 0.0) + weight
    return seconds, total


def normalize(raw: dict, files) -> dict:
    seconds, total = _line_seconds(raw)
    if total <= 0:
        return {}
    by_name = {}
    for (frame_file, line), secs in seconds.items():
        by_name.setdefault(posixpath.basename(frame_file.replace("\\", "/")), []).append((frame_file, line, secs))
    out = {}
    for path, _content in files:
        rows = [r for r in by_name.get(posixpath.basename(path), []) if same_file(r[0], path)]
        if not rows:
            continue
        data = {"profiler": PROFILER, "sampled_seconds": round(total, 3)}
        for _frame_file, line, secs in sorted(rows, key=lambda r: r[1]):
            share = secs / total
            if share >= MIN_RECORDED_SHARE:
                data[f"time_share_line_{line}"] = round(share, 4)
        out[path] = data
    return out
