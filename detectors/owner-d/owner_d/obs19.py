"""OBS-19: CPU-heavy / leaky application code found via continuous profiling (artifact only).

Detector semantics version 1.0.0. The evidence is a client continuous-profiler export uploaded as
`obs-19.json` through the artifact upload endpoint and routed by owner_d/aws/artifact_handler.py, which
calls `artifact_inputs` here to normalize it:

- CPU: folded/collapsed stacks (`frame;frame;frame count`, root first; written by py-spy
  `--format raw`, Pyroscope collapsed exports, async-profiler `collapsed`, stackcollapse-perf), either
  as raw text or as JSON `{"stack": [...], "count": n}` objects. The format is detected by content.
- memory (optional): in-use bytes per function over a series of snapshots, or one collapsed in-use
  export per snapshot.

Each profile becomes `cpu-profile:<name>` and/or `memory-profile:<name>` scope items with one `artifact`
source each. Hot spots are functions whose self or inclusive share of the CPU samples exceeds the
configured maximum; leak candidates are functions whose in-use memory grows in (nearly) every interval.

Repository scans never call this check (SUPPORTED_KIND "artifact"): without an artifact it is
`unavailable`. Missing, malformed or too-small input is never reported as clean.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from itertools import pairwise

CHECK_ID = "OBS-19"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "artifact"  # scanner/adapters/owner_d.py reports artifact checks unavailable in repo scans
ARTIFACT_KIND = "artifact"
ARTIFACT_NAME = "obs-19.json"
CPU_SCOPE, MEMORY_SCOPE = "cpu-profile:", "memory-profile:"
CPU_FIELD, MEMORY_FIELD = "cpu:", "memory:"

CPU_SETTING_KEYS = ("min_total_samples", "max_self_cpu_share", "max_inclusive_cpu_share")
MEMORY_SETTING_KEYS = ("min_leak_snapshots", "min_monotonic_fraction", "min_leak_growth_bytes")
SETTING_KEYS = CPU_SETTING_KEYS + MEMORY_SETTING_KEYS
# Reference values from "OBS-19 > Context settings" in detectors/owner-d/README.md. The artifact route
# applies them unless the artifact's `settings` override them.
REFERENCE_SETTINGS = {
    "min_total_samples": 1000,  # README OBS-19: fewest CPU samples in a profile worth judging
    "max_self_cpu_share": 0.1,  # README OBS-19: largest acceptable self-time share of one function
    "max_inclusive_cpu_share": 0.5,  # README OBS-19: largest acceptable share of one function plus callees
    "min_leak_snapshots": 5,  # README OBS-19: fewest memory snapshots needed for a trend
    "min_monotonic_fraction": 0.8,  # README OBS-19: share of intervals in which memory must grow
    "min_leak_growth_bytes": 1048576,  # README OBS-19: smallest first-to-last growth reported as a leak
}

ENTRY_SHARE = 0.9  # functions in more than this share of samples are entry points/dispatch loops
MAX_FINDINGS_PER_SCOPE = 5  # keeps one published result under the EventBridge entry limit
MAX_PROFILES = 5
MAX_SNAPSHOTS = 60
MAX_KEY_CHARS = 120
MAX_COUNT_DIGITS = 18
SHARE_DIGITS = 4
SHARE_TOLERANCE = 1e-4

IDENTITY_FIELDS = (
    "schema_version", "repository_id", "scan_id", "commit_sha", "check_id", "detector_version", "context", "scope",
)
CPU_FIXED = {"profile", "profiler", "input_format", "sample_type", "total_samples", "stack_count", "function_count"}
CPU_RECORD = {"function", "file", "location", "self_samples", "inclusive_samples", "self_share", "inclusive_share",
              "top_callee_samples", "root_samples"}
MEMORY_FIXED = {"profile", "profiler", "input_format", "unit", "snapshot_count", "function_count"}
MEMORY_RECORD = {"function", "file", "location", "in_use_bytes"}
PROFILE_KEYS = {"name", "sample_type", "cpu", "memory"}
ARTIFACT_KEYS = {"profiler", "settings", "profiles"}

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/243",
    "https://grafana.com/docs/pyroscope/latest/introduction/what-is-profiling/",
    "https://www.brendangregg.com/flamegraphs.html",
    "https://github.com/benfred/py-spy",
)
CPU_RECOMMENDATION = (
    "Open the flagged function in a flame graph of the same profile and remove the repeated work: cache or "
    "memoize results, batch or move it off the hot path, use a cheaper algorithm, data structure or library, "
    "then re-profile to confirm its share of CPU samples dropped."
)
LEAK_RECOMMENDATION = (
    "Check what the flagged function keeps alive (unbounded caches, growing module-level collections, listeners "
    "or references held after a request) and bound or release it; confirm with a longer in-use memory profile "
    "or a heap snapshot diff that the growth stops."
)
GENERAL_LIMITATION = (
    "OBS-19 reads one client continuous-profiler export: shares are of the sampled CPU time in that profile's "
    "window, not of the service's total cost, and memory growth within one window can be warm-up or a bounded "
    "cache filling. No energy or cost measurements are emitted."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an OBS-19 contract input."""


class ArtifactError(ValueError):
    """The uploaded artifact cannot be used at all; the artifact parser refuses it."""


class _Invalid(ValueError):
    """One profile part (cpu or memory) is malformed."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    return hashlib.sha256(_canonical([repository_id, check_id, scope_id, identity]).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _fmt(value):
    return str(value) if isinstance(value, int) else f"{value:g}"


def _pct(part, whole):
    return f"{100 * part / whole:.1f}%"


# --------------------------------------------------------------------------- frames and stacks

_PYSPY = re.compile(r"^(?P<name>.*?\S) \((?P<file>[^()]+?)(?::(?P<line>\d{1,9}))?\)$")  # name (file:line)
_PYROSCOPE = re.compile(r"^(?P<file>\S+?):(?P<line>\d{1,9}) - (?P<name>\S.*)$")  # file:line - name
_SUFFIX = re.compile(r"(?:_\[[a-z0-9]\]|\+0x[0-9a-fA-F]+)$")  # async-profiler frame type, perf offset
_PSEUDO_ROOT = re.compile(r"^(?:process \d+:.*|thread \((?:0x[0-9a-fA-F]+|\d+)\)|thread \d+)$")  # py-spy
_STACK_LINE = re.compile(rf"^(?P<stack>.*?\S)[ \t]+(?P<count>\d{{1,{MAX_COUNT_DIGITS}}})$")


def parse_frame(frame):
    """(key, function, file or None, line or None). The key leaves out the line so it is stable."""
    text = frame.strip()
    match = _PYSPY.match(text) or _PYROSCOPE.match(text)
    if match:
        name, file = match["name"].strip(), match["file"].strip()
        line = int(match["line"]) if match["line"] else None
    else:
        name, file, line = _SUFFIX.sub("", text) or text, None, None
    key = f"{name} ({file})" if file else name
    if len(key) > MAX_KEY_CHARS:
        key = key[:MAX_KEY_CHARS - 11] + "~" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return key, name[:MAX_KEY_CHARS], file[:MAX_KEY_CHARS] if file else None, line


def parse_collapsed(text, what="cpu"):
    """[(frames, count)] from folded stacks, one `frame;frame;... count` per line (root first)."""
    stacks = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        match = _STACK_LINE.match(line)
        if not match:
            raise _Invalid(f"{what} line {number} is not 'frame;frame;... count'")
        frames = match["stack"].split(";")
        if any(not frame.strip() for frame in frames):
            raise _Invalid(f"{what} line {number} has an empty frame")
        stacks.append((frames, int(match["count"])))
    if not stacks:
        raise _Invalid(f"{what} has no stacks")
    return stacks


def _parse_stack_objects(items, what):
    if not isinstance(items, list) or not items:
        raise _Invalid(f'{what} must be collapsed text or a nonempty list of {{"stack", "count"}} objects')
    stacks = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"stack", "count"}:
            raise _Invalid(f'{what}[{index}] must be an object with exactly "stack" and "count"')
        stack, count = item["stack"], item["count"]
        frames = stack.split(";") if isinstance(stack, str) else stack
        if not isinstance(frames, list) or not frames or any(not isinstance(f, str) or not f.strip() for f in frames):
            raise _Invalid(f"{what}[{index}].stack must be a nonempty list of frames (or 'a;b;c')")
        if not _is_count(count) or count >= 10 ** MAX_COUNT_DIGITS:
            raise _Invalid(f"{what}[{index}].count must be a nonnegative integer")
        stacks.append((list(frames), count))
    return stacks


def _stacks(value, what):
    """Detect the format by content: a string is collapsed text, a list holds stack objects."""
    if isinstance(value, str):
        return parse_collapsed(value, what), "collapsed"
    return _parse_stack_objects(value, what), "stacks"


def _strip_pseudo_roots(frames):
    """Drop py-spy `process ...`/`thread (...)` pseudo frames from the root of a stack."""
    start = 0
    while start < len(frames) - 1 and _PSEUDO_ROOT.match(frames[start].strip()):
        start += 1
    return frames[start:]


class _Frames:
    def __init__(self):
        self.cache, self.meta, self.lines = {}, {}, defaultdict(Counter)

    def __call__(self, frame):
        parsed = self.cache.get(frame)
        if parsed is None:
            parsed = self.cache[frame] = parse_frame(frame)
            self.meta.setdefault(parsed[0], parsed[1:3])
        return parsed

    def location(self, key, preferred=None):
        _, file = self.meta[key]
        lines = (preferred or {}).get(key) or self.lines.get(key)
        if not file or not lines:
            return None
        line = min(lines.items(), key=lambda item: (-item[1], item[0]))[0]
        return f"{file}:{line}"


# --------------------------------------------------------------------------- normalization

def cpu_profile_data(stacks, *, profile, profiler, input_format):
    """Contract `data` for one CPU profile: per-function self/inclusive samples as `cpu:<function>` fields."""
    frames = _Frames()
    self_samples, inclusive, roots, edges = Counter(), Counter(), Counter(), Counter()
    self_lines = defaultdict(Counter)
    total = 0
    for raw_frames, count in stacks:
        parsed = [frames(frame) for frame in _strip_pseudo_roots(raw_frames)]
        total += count
        keys = [key for key, _, _, _ in parsed]
        leaf_key, _, _, leaf_line = parsed[-1]
        self_samples[leaf_key] += count
        if leaf_line is not None:
            self_lines[leaf_key][leaf_line] += count
        for key, _, _, line in parsed:
            if line is not None:
                frames.lines[key][line] += count
        for key in set(keys):
            inclusive[key] += count
        roots[keys[0]] += count
        for pair in {(caller, callee) for caller, callee in pairwise(keys) if caller != callee}:
            edges[pair] += count
    top_callee = Counter()
    for (caller, _), count in edges.items():
        top_callee[caller] = max(top_callee[caller], count)
    data = {"profile": profile, "profiler": profiler, "input_format": input_format, "sample_type": "cpu",
            "total_samples": total, "stack_count": len(stacks), "function_count": len(frames.meta)}
    for key in sorted(frames.meta):
        function, file = frames.meta[key]
        data[CPU_FIELD + key] = {
            "function": function,
            "file": file,
            "location": frames.location(key, self_lines),
            "self_samples": self_samples[key],
            "inclusive_samples": inclusive[key],
            "self_share": round(self_samples[key] / total, SHARE_DIGITS) if total else 0.0,
            "inclusive_share": round(inclusive[key] / total, SHARE_DIGITS) if total else 0.0,
            "top_callee_samples": top_callee[key],
            "root_samples": roots[key],
        }
    return data


def memory_profile_data(memory, *, profile, profiler):
    """Contract `data` for one in-use memory series: `memory:<function>` fields with bytes per snapshot."""
    if not isinstance(memory, dict):
        raise _Invalid('memory must be an object with "series" or "snapshots"')
    unknown = set(memory) - {"unit", "timestamps", "series", "snapshots"}
    if unknown:
        raise _Invalid("memory has unknown fields: " + ", ".join(sorted(unknown)))
    if memory.get("unit", "bytes") != "bytes":
        raise _Invalid('memory.unit must be "bytes"')
    if ("series" in memory) == ("snapshots" in memory):
        raise _Invalid('memory needs exactly one of "series" or "snapshots"')
    frames = _Frames()
    totals = defaultdict(Counter)  # key -> snapshot index -> bytes
    if "series" in memory:
        series, input_format = memory["series"], "series"
        if not isinstance(series, dict) or not series:
            raise _Invalid("memory.series must be a nonempty object of function -> [bytes per snapshot]")
        lengths = set()
        for frame, values in series.items():
            if not frame.strip() or not isinstance(values, list) or not all(_is_count(v) for v in values):
                raise _Invalid(f"memory.series[{frame[:80]!r}] must be a list of nonnegative integer bytes")
            lengths.add(len(values))
            key, _, _, line = frames(frame)
            for index, value in enumerate(values):
                totals[key][index] += value
            if line is not None:
                frames.lines[key][line] += values[-1] if values else 0
        if len(lengths) != 1:
            raise _Invalid("memory.series lists must all have one value per snapshot")
        count = lengths.pop()
    else:
        snapshots, input_format = memory["snapshots"], "snapshots"
        if not isinstance(snapshots, list) or not snapshots:
            raise _Invalid("memory.snapshots must be a nonempty list of collapsed in-use exports")
        count = len(snapshots)
        for index, snapshot in enumerate(snapshots):
            for raw_frames, value in _stacks(snapshot, f"memory.snapshots[{index}]")[0]:
                key, _, _, line = frames(_strip_pseudo_roots(raw_frames)[-1])
                totals[key][index] += value
                if line is not None:
                    frames.lines[key][line] += value
    if not 1 <= count <= MAX_SNAPSHOTS:
        raise _Invalid(f"memory needs 1 to {MAX_SNAPSHOTS} snapshots (got {count}); downsample before upload")
    data = {"profile": profile, "profiler": profiler, "input_format": input_format, "unit": "bytes",
            "snapshot_count": count, "function_count": len(frames.meta)}
    if "timestamps" in memory:
        stamps = memory["timestamps"]
        if not isinstance(stamps, list) or len(stamps) != count or not all(isinstance(s, str) and s for s in stamps):
            raise _Invalid("memory.timestamps must be one nonempty string per snapshot")
        data["timestamps"] = stamps
    for key in sorted(frames.meta):
        function, file = frames.meta[key]
        data[MEMORY_FIELD + key] = {"function": function, "file": file, "location": frames.location(key),
                                    "in_use_bytes": [totals[key][index] for index in range(count)]}
    return data


def load_artifact(body):
    """An uploaded file (bytes or text) -> its JSON value, or the text itself when it is collapsed stacks."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            raise ArtifactError("artifact is not UTF-8 text") from None
    if body.lstrip("﻿ \t\r\n")[:1] in ('{', '[', '"'):
        try:
            return json.loads(body.lstrip("﻿"))
        except ValueError:
            raise ArtifactError("artifact looks like JSON but does not parse") from None
    return body


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}")


def _profiles(data):
    """(profiler, settings overrides, [profile objects]) from the documented artifact shape."""
    if isinstance(data, str):
        data = {"cpu": data}  # raw collapsed text: one CPU profile
    if not isinstance(data, dict):
        raise ArtifactError(f"{ARTIFACT_NAME} needs a JSON object or collapsed stack text")
    if "profiles" in data:
        unknown = set(data) - ARTIFACT_KEYS
        profiles = data["profiles"]
        if not isinstance(profiles, list) or not profiles:
            raise ArtifactError('"profiles" must be a nonempty list')
    else:
        unknown = set(data) - (ARTIFACT_KEYS | PROFILE_KEYS)
        profiles = [{"name": "default"} | {key: data[key] for key in PROFILE_KEYS if key in data}]
        if "cpu" not in data and "memory" not in data:
            raise ArtifactError(f'{ARTIFACT_NAME} needs "profiles", or "cpu"/"memory" for one profile')
    if unknown:
        raise ArtifactError("unknown top-level fields: " + ", ".join(sorted(unknown)))
    profiler = data.get("profiler", "unknown")
    if not isinstance(profiler, str) or not profiler.strip() or len(profiler) > 100:
        raise ArtifactError('"profiler" must be a nonempty string of at most 100 characters')
    overrides = data.get("settings") or {}
    if not isinstance(overrides, dict) or set(overrides) - set(SETTING_KEYS):
        raise ArtifactError(f"settings may only contain {', '.join(SETTING_KEYS)}")
    return profiler.strip(), overrides, profiles


def artifact_inputs(data, *, name=ARTIFACT_NAME, run=None):
    """The `obs-19.json` artifact route: (module, context, scope, sources, notes).

    Malformed profile parts still become scope items, with a `normalization_error`, so the result lists
    them as not evaluated instead of dropping them. Raises ArtifactError when nothing is usable.
    """
    profiler, overrides, profiles = _profiles(data)
    origin = f"{name} from GitHub Actions run {run}" if run else name
    notes, scope, sources, seen = [], [], [], set()
    if len(profiles) > MAX_PROFILES:
        notes.append(f"only the first {MAX_PROFILES} of {len(profiles)} profiles in {name} were evaluated")
        profiles = profiles[:MAX_PROFILES]
    for index, profile in enumerate(profiles):
        label = profile.get("name") if isinstance(profile, dict) else None
        if not isinstance(label, str) or not _NAME.fullmatch(label):
            notes.append(f"profiles[{index}] has no usable name (letters, digits, '._:@/-', at most 100); skipped")
            continue
        if label in seen:
            notes.append(f"duplicate profile name {label!r} in profiles[{index}]; only the first was evaluated")
            continue
        unknown = set(profile) - PROFILE_KEYS
        if unknown or ("cpu" not in profile and "memory" not in profile):
            reason = ("unknown fields " + ", ".join(sorted(unknown))) if unknown else 'neither "cpu" nor "memory"'
            notes.append(f"profiles[{index}] ({label}) has {reason}; skipped")
            continue
        seen.add(label)
        for part, prefix in (("cpu", CPU_SCOPE), ("memory", MEMORY_SCOPE)):
            if part not in profile:
                continue
            try:
                if part == "cpu":
                    if profile.get("sample_type", "cpu") != "cpu":
                        raise _Invalid('sample_type must be "cpu" (on-CPU samples)')
                    stacks, input_format = _stacks(profile["cpu"], "cpu")
                    payload = cpu_profile_data(stacks, profile=label, profiler=profiler, input_format=input_format)
                else:
                    payload = memory_profile_data(profile["memory"], profile=label, profiler=profiler)
            except _Invalid as error:
                payload = {"profile": label, "profiler": profiler, "normalization_error": str(error)[:300]}
            scope_id = prefix + label
            sources.append({"source_id": f"profile-{index}-{part}", "scope_id": scope_id, "kind": ARTIFACT_KIND,
                            "locator": f"{origin}: profile {label} ({part})", "data": payload})
            scope.append(scope_id)
    if not scope:
        raise ArtifactError("no usable profiles in the artifact: " + "; ".join(notes[:5]))
    return sys.modules[__name__], dict(REFERENCE_SETTINGS) | overrides, scope, sources, notes


# --------------------------------------------------------------------------- validation

def _read_settings(context, keys):
    missing = [key for key in keys if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in keys:
        value = context[key]
        if not _is_number(value):
            return None, f"context.{key} must be a number"
        if key in ("max_self_cpu_share", "max_inclusive_cpu_share", "min_monotonic_fraction") and not 0 < value <= 1:
            return None, f"context.{key} must be greater than 0 and at most 1"
        if key == "min_total_samples" and (not isinstance(value, int) or value < 1):
            return None, f"context.{key} must be a positive integer"
        if key == "min_leak_snapshots" and (not isinstance(value, int) or value < 3):
            return None, f"context.{key} must be an integer of at least 3"
        if key == "min_leak_growth_bytes" and value < 0:
            return None, f"context.{key} must be nonnegative"
        settings[key] = value
    return settings, None


def _records(data, prefix):
    return {field: value for field, value in data.items() if field.startswith(prefix)}


def _common_problems(data, profile, fixed, prefix, record_keys):
    if not isinstance(data, dict):
        return ["artifact data must be an object"], {}
    if "normalization_error" in data:
        return [f"profile could not be normalized: {data['normalization_error']}"], {}
    problems = []
    missing = sorted(fixed - set(data))
    if missing:
        problems.append("missing fields: " + ", ".join(missing) + " (normalize the export with owner_d.obs19)")
    unknown = sorted(f for f in set(data) - fixed - {"timestamps"} if not f.startswith(prefix))
    if unknown:
        problems.append("unknown fields: " + ", ".join(unknown[:5]))
    if "profile" in data and data["profile"] != profile:
        problems.append(f"profile {data['profile']!r} does not match the scope (expected {profile!r})")
    for field in ("profiler", "input_format"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            problems.append(f"{field} must be a nonempty string")
    records = _records(data, prefix)
    for field, record in records.items():
        if not isinstance(record, dict) or set(record) != record_keys:
            problems.append(f"{field} must be an object with exactly {', '.join(sorted(record_keys))}")
        elif not isinstance(record["function"], str) or not record["function"]:
            problems.append(f"{field}.function must be a nonempty string")
        elif not all(record[k] is None or isinstance(record[k], str) for k in ("file", "location")):
            problems.append(f"{field}.file and .location must be strings or null")
        if len(problems) > 5:
            break
    if not problems and (not _is_count(data["function_count"]) or data["function_count"] != len(records)):
        problems.append(f"function_count must equal the number of {prefix}<function> fields ({len(records)})")
    return problems, records


def _cpu_problems(data, profile):
    problems, records = _common_problems(data, profile, CPU_FIXED, CPU_FIELD, CPU_RECORD)
    if problems:
        return problems
    if data["sample_type"] != "cpu":
        return ['sample_type must be "cpu"']
    if data["input_format"] not in ("collapsed", "stacks"):
        return ['input_format must be "collapsed" or "stacks"']
    total = data["total_samples"]
    if not _is_count(total) or not _is_count(data["stack_count"]):
        return ["total_samples and stack_count must be nonnegative integers"]
    self_sum = 0
    for field, record in records.items():
        counts = [record[k] for k in ("self_samples", "inclusive_samples", "top_callee_samples", "root_samples")]
        if not all(_is_count(c) for c in counts):
            return [f"{field} sample counts must be nonnegative integers"]
        own, inclusive, callee, root = counts
        if not own <= inclusive <= total or callee > inclusive or root > inclusive:
            return [f"{field} counts are inconsistent (self <= inclusive <= total_samples; callee, root <= inclusive)"]
        for share_key, count in (("self_share", own), ("inclusive_share", inclusive)):
            share = record[share_key]
            if not _is_number(share) or abs(share - (count / total if total else 0)) > SHARE_TOLERANCE:
                return [f"{field}.{share_key} does not match its samples / total_samples"]
        self_sum += own
    if self_sum != total:
        return [f"self samples sum to {self_sum}, not total_samples {total}; every sample has exactly one leaf"]
    return []


def _memory_problems(data, profile):
    problems, records = _common_problems(data, profile, MEMORY_FIXED, MEMORY_FIELD, MEMORY_RECORD)
    if problems:
        return problems
    count = data["snapshot_count"]
    if data["unit"] != "bytes":
        return ['unit must be "bytes"']
    if data["input_format"] not in ("series", "snapshots"):
        return ['input_format must be "series" or "snapshots"']
    if not _is_count(count) or count < 1:
        return ["snapshot_count must be a positive integer"]
    stamps = data.get("timestamps")
    if stamps is not None and (not isinstance(stamps, list) or len(stamps) != count):
        return ["timestamps must have one entry per snapshot"]
    for field, record in records.items():
        series = record["in_use_bytes"]
        if not isinstance(series, list) or len(series) != count or not all(_is_count(v) for v in series):
            return [f"{field}.in_use_bytes must be {count} nonnegative integers (one per snapshot)"]
    return []


# --------------------------------------------------------------------------- detection

def _where(record):
    return record["location"] or record["file"] or "an unknown location"


def _cpu_findings(data, settings, source):
    total = data["total_samples"]
    self_max, inclusive_max = settings["max_self_cpu_share"], settings["max_inclusive_cpu_share"]
    items = []
    for field, record in _records(data, CPU_FIELD).items():
        own, inclusive = record["self_samples"] / total, record["inclusive_samples"] / total
        self_hot = own > self_max
        inclusive_hot = (inclusive > inclusive_max and record["top_callee_samples"] / total <= inclusive_max
                         and record["root_samples"] == 0 and inclusive <= ENTRY_SHARE)
        if not (self_hot or inclusive_hot):
            continue
        parts = []
        if self_hot:
            parts.append(f"{_pct(record['self_samples'], total)} of CPU samples in its own code "
                         f"({record['self_samples']} of {total}; max_self_cpu_share {_fmt(self_max)})")
        if inclusive_hot:
            parts.append(f"{_pct(record['inclusive_samples'], total)} including its callees "
                         f"({record['inclusive_samples']} of {total}; max_inclusive_cpu_share {_fmt(inclusive_max)}) "
                         "with no single callee above that share")
        items.append((max(own if self_hot else 0, inclusive if inclusive_hot else 0), {
            "identity": "hot-spot:" + field[len(CPU_FIELD):],
            "summary": (f"Profile {data['profile']} ({data['profiler']}): {record['function']} at "
                        f"{_where(record)} is a CPU hot spot: " + "; ".join(parts) + "."),
            "confidence": "high" if self_hot else "medium",
            "recommendation": CPU_RECOMMENDATION,
            "evidence": [_evidence(source, "total_samples", total), _evidence(source, field, record)],
        }))
    return items


def _memory_findings(data, settings, source):
    count = data["snapshot_count"]
    steps = count - 1
    items = []
    for field, record in _records(data, MEMORY_FIELD).items():
        series = record["in_use_bytes"]
        growing = sum(later > earlier for earlier, later in pairwise(series))
        growth = series[-1] - series[0]
        if growth <= 0 or growth < settings["min_leak_growth_bytes"]:
            continue
        if growing / steps < settings["min_monotonic_fraction"]:
            continue
        items.append((growth, {
            "identity": "leak-candidate:" + field[len(MEMORY_FIELD):],
            "summary": (f"Profile {data['profile']} ({data['profiler']}): in-use memory attributed to "
                        f"{record['function']} at {_where(record)} grew from {series[0]} to {series[-1]} bytes "
                        f"(+{growth}) across {count} snapshots and increased in {growing} of {steps} intervals (min_monotonic_fraction "
                        f"{_fmt(settings['min_monotonic_fraction'])}); a leak candidate."),
            "confidence": "medium" if growing == steps else "low",
            "recommendation": LEAK_RECOMMENDATION,
            "evidence": [_evidence(source, field, record), _evidence(source, "snapshot_count", count)],
        }))
    return items


def _evidence(source, field, value):
    return {"source_id": source["source_id"], "kind": ARTIFACT_KIND, "locator": source["locator"],
            "field": field, "value": value}


def _evaluate_scope(scope_id, sources, context):
    """(evaluated, items, limitations)."""

    def skip(reason):
        return False, [], [f"{scope_id}: {reason}"]

    if scope_id.startswith(CPU_SCOPE):
        part, keys, problems_of, findings_of = "cpu", CPU_SETTING_KEYS, _cpu_problems, _cpu_findings
    elif scope_id.startswith(MEMORY_SCOPE):
        part, keys, problems_of, findings_of = "memory", MEMORY_SETTING_KEYS, _memory_problems, _memory_findings
    else:
        return skip("unsupported scope; OBS-19 evaluates cpu-profile:<name> and memory-profile:<name> items "
                    "from a profiler artifact")
    profile = scope_id.split(":", 1)[1]
    artifacts = [s for s in sources if s.get("kind") == ARTIFACT_KIND]
    if not artifacts:
        return skip("no continuous-profiler artifact supplied; OBS-19 needs a client profile export "
                    f"(upload {ARTIFACT_NAME})")
    if len(artifacts) > 1:
        return skip("multiple profiler artifacts supplied; evaluation requires exactly one")
    settings, reason = _read_settings(context, keys)
    if settings is None:
        return skip(f"missing or invalid context settings: {reason}")
    source = artifacts[0]
    data = source.get("data")
    problems = problems_of(data, profile)
    if problems:
        return skip("; ".join(problems[:5]))
    if part == "cpu" and data["total_samples"] < settings["min_total_samples"]:
        return skip(f"{data['total_samples']} CPU samples is below min_total_samples "
                    f"{_fmt(settings['min_total_samples'])}; too few to judge, not evaluated")
    if part == "memory" and data["snapshot_count"] < settings["min_leak_snapshots"]:
        return skip(f"{data['snapshot_count']} memory snapshots is below min_leak_snapshots "
                    f"{_fmt(settings['min_leak_snapshots'])}; no trend can be judged, not evaluated")
    ranked = sorted(findings_of(data, settings, source), key=lambda pair: (-pair[0], pair[1]["identity"]))
    limitations = []
    if len(ranked) > MAX_FINDINGS_PER_SCOPE:
        limitations.append(f"{scope_id}: {len(ranked) - MAX_FINDINGS_PER_SCOPE} more {part} findings not reported "
                           f"(the {MAX_FINDINGS_PER_SCOPE} largest are)")
    return True, [item for _, item in ranked[:MAX_FINDINGS_PER_SCOPE]], limitations


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == "1.0", "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; "
        f"this detector implements {DETECTOR_VERSION}",
    )
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")
    sources = [s for s in sources if isinstance(s, dict)]

    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        ok, items, notes = _evaluate_scope(scope_id, [s for s in sources if s.get("scope_id") == scope_id],
                                           payload["context"])
        limitations.extend(notes)
        if not ok:
            continue
        evaluated.append(scope_id)
        for item in items:
            findings.append({
                "fingerprint": fingerprint(payload["repository_id"], CHECK_ID, scope_id, item["identity"]),
                "scope_id": scope_id,
                "identity": item["identity"],
                "summary": item["summary"],
                "confidence": item["confidence"],
                "recommendation": item["recommendation"],
                "references": list(REFERENCES),
                "evidence": item["evidence"],
            })
    limitations.append(GENERAL_LIMITATION)
    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result = {field: payload[field] for field in IDENTITY_FIELDS}
    result.update(kind="result", status=status, coverage={"evaluated_scope": evaluated, "limitations": limitations},
                  findings=findings if evaluated else [], measurements=[])
    return result
