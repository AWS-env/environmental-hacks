"""Normalize AWS X-Ray traces into per-file contract telemetry data (read-only; nothing is executed).

Raw input (built by the X-Ray reader Lambda or `aws xray batch-get-traces`):
    {"traces": [{"Id": "1-...", "Segments": [{"Id": "...", "Document": "<segment JSON string>"}]}],
     "function_files": {"my-fn": "src/handler.js"}}      # traced function/service name -> repo file

For every traced function that maps to a repo file, siblings under the same parent are grouped into
*serial runs*: consecutive children with the same `name` (and namespace) where each starts no earlier than the
previous one ended (small epsilon). Overlapping children (Promise.all) never form a run.

Output per mapped file:
    {"profiler": "xray", "function": "my-fn", "traces_analyzed": 5, "max_serial_calls": 8,
     "serial_wall_seconds": 0.91, "serial_services": ["Inventory"]}
`max_serial_calls` is the longest run seen; `serial_wall_seconds` is that run's wall time.
"""
import json

PROFILER = "xray"
EPSILON = 0.001  # seconds of allowed overlap/clock jitter between consecutive calls


def _runs(children):
    """Serial runs (lists of >= 2 children) among a parent's subsegments."""
    kids = sorted((c for c in children if "start_time" in c and "end_time" in c), key=lambda c: c["start_time"])
    runs, current = [], []
    for kid in kids:
        same = current and (kid.get("name"), kid.get("namespace")) == (current[-1].get("name"), current[-1].get("namespace"))
        if same and kid["start_time"] >= current[-1]["end_time"] - EPSILON:
            current.append(kid)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = [kid]
    if len(current) >= 2:
        runs.append(current)
    return runs


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get("subsegments", []))


def normalize(raw: dict, files) -> dict:
    mapping = raw.get("function_files", {})
    repo_paths = {p for p, _ in files}
    per_function = {}
    for trace in raw.get("traces", []):
        for segment in trace.get("Segments", []):
            doc = segment.get("Document")
            doc = json.loads(doc) if isinstance(doc, str) else doc
            name = doc.get("name")
            if name not in mapping or mapping[name] not in repo_paths:
                continue
            entry = per_function.setdefault(name, {"traces": set(), "best": None, "services": set()})
            entry["traces"].add(trace.get("Id"))
            for node in _walk(doc):
                for run in _runs(node.get("subsegments", [])):
                    entry["services"].add(run[0].get("name", ""))
                    wall = run[-1]["end_time"] - run[0]["start_time"]
                    if entry["best"] is None or (len(run), wall) > entry["best"]:
                        entry["best"] = (len(run), wall)
    out = {}
    for name, entry in per_function.items():
        calls, wall = entry["best"] or (0, 0.0)
        out[mapping[name]] = {
            "profiler": PROFILER, "function": name, "traces_analyzed": len(entry["traces"]),
            "max_serial_calls": calls, "serial_wall_seconds": round(wall, 3),
            "serial_services": sorted(s for s in entry["services"] if s),
        }
    return out
