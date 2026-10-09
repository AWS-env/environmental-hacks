"""LLM-10: unbounded agent/tool loops and repeated tool calls (trace analysis).

Detector semantics version 1.0.0. Two parts:

* `normalize_xray_traces(traces)` turns AWS X-Ray `BatchGetTraces` results into one telemetry summary per
  entrypoint (the X-Ray segment that owns the GenAI calls, e.g. a Lambda function). GenAI calls are found with
  OpenTelemetry GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.tool.name`,
  `gen_ai.request.model`, `gen_ai.tool.call.arguments`, `gen_ai.agent.name`, `error.type`) in subsegment
  metadata (dotted keys, as the ADOT awsxrayexporter writes them) or annotations (`gen_ai_tool_name`).
* `evaluate(payload)` checks contract v1 inputs built from those summaries: an agent run that calls the same
  tool with identical arguments more than `context.max_identical_tool_calls` times, or makes more than
  `context.max_llm_iterations` model calls, is flagged once `context.min_traces` traces were analyzable.

Failed/throttled calls (explicit retries) are not counted; pagination with changing cursors is not identical.
The detector never calls AWS; the trace-analyzer route fetches traces read-only and passes them in.
"""

from __future__ import annotations

import hashlib
import json

from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "LLM-10"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
SCOPE_PREFIX = "entrypoint:"
SETTING_KEYS = ("min_traces", "max_llm_iterations", "max_identical_tool_calls")
IDENTICAL_IDENTITY = "identical-tool-calls"
ITERATION_IDENTITY = "iteration-budget"

# OpenTelemetry GenAI operation names (semantic-conventions-genai, gen-ai-spans.md).
LLM_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})
TOOL_OPERATIONS = frozenset({"execute_tool"})
AGENT_OPERATIONS = frozenset({"invoke_agent", "invoke_workflow"})
TOOL_SPAN_PREFIX = "execute_tool "  # semconv span name: `execute_tool {gen_ai.tool.name}`
ATTRIBUTES = (
    "gen_ai.operation.name",
    "gen_ai.tool.name",
    "gen_ai.request.model",
    "gen_ai.tool.call.arguments",
    "gen_ai.agent.name",
    "error.type",
)
# X-Ray annotation keys only allow letters, digits and `_`; the awsxrayexporter replaces dots with `_`.
_ANNOTATION_KEYS = {key.replace(".", "_"): key for key in ATTRIBUTES}
# Argument keys that page through results; compared lowercase without `_`/`-`.
PAGINATION_KEYS = frozenset({
    "cursor", "nextcursor", "startcursor", "pagetoken", "nextpagetoken", "nexttoken", "continuationtoken",
    "offset", "page", "pagenumber", "marker", "startingafter", "exclusivestartkey", "skip",
})
ENTRYPOINT_RUN = "(entrypoint)"

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md",
    "https://openai.github.io/openai-agents-python/running_agents/",
    "https://docs.aws.amazon.com/xray/latest/devguide/xray-api-segmentdocuments.html",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
IDENTICAL_RECOMMENDATION = (
    "Stop the agent from re-issuing the same tool call: cache or memoize tool results within a run (and share "
    "the cache across agents), give each tool a call budget, and end the loop when a tool returns the same "
    "result again. For polling tools, use backoff or a completion callback instead of identical re-polls."
)
ITERATION_RECOMMENDATION = (
    "Set an explicit turn budget for the agent loop (OpenAI Agents SDK max_turns, LangChain max_iterations, "
    "LangGraph recursion_limit, CrewAI max_iter or an equivalent counter) with a clear stop condition, and "
    "alert when runs hit it."
)
LIMITATION = (
    "Trace analysis only: LLM-10 proves what sampled X-Ray traces recorded, not that the code lacks an "
    "iteration budget; token counts and cost are not measured. GenAI calls are recognised only through "
    "OpenTelemetry GenAI attributes (or `execute_tool <tool>` subsegment names); uninstrumented SDK calls are "
    "not counted. Identical arguments are compared only when gen_ai.tool.call.arguments is recorded (Opt-In)."
)


# --------------------------------------------------------------------------------------------------------
# Normalization: BatchGetTraces -> per-entrypoint summaries
# --------------------------------------------------------------------------------------------------------


class _Malformed(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _short_hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _attributes(node):
    """GenAI attributes of a segment/subsegment; metadata (structured) wins over annotations."""
    found = {}
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        for namespace in sorted(metadata, key=lambda ns: (ns != "default", str(ns))):
            values = metadata[namespace]
            if isinstance(values, dict):
                for key, value in values.items():
                    key = key if key in ATTRIBUTES else _ANNOTATION_KEYS.get(key)
                    if key and value is not None:
                        found.setdefault(key, value)
    annotations = node.get("annotations")
    if isinstance(annotations, dict):
        for key, value in annotations.items():
            key = key if key in ATTRIBUTES else _ANNOTATION_KEYS.get(key)
            if key and value is not None:
                found.setdefault(key, value)
    return found


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _classify(node):
    """(kind, name, attributes) with kind in {"llm", "tool", "agent", None}."""
    attrs = _attributes(node)
    operation = _text(attrs.get("gen_ai.operation.name"))
    operation = operation.lower() if operation else None
    tool = _text(attrs.get("gen_ai.tool.name"))
    model = _text(attrs.get("gen_ai.request.model"))
    if tool is None and isinstance(node.get("name"), str) and node["name"].startswith(TOOL_SPAN_PREFIX):
        tool = _text(node["name"][len(TOOL_SPAN_PREFIX):])
        operation = operation or "execute_tool"
    if operation in AGENT_OPERATIONS:
        return "agent", _text(attrs.get("gen_ai.agent.name")) or _text(node.get("name")) or "agent", attrs
    if operation in TOOL_OPERATIONS or (operation is None and tool):
        return ("tool", tool, attrs) if tool else (None, None, attrs)
    if operation in LLM_OPERATIONS or (operation is None and model):
        return "llm", model, attrs
    return None, None, attrs


def _failed(node, attrs):
    return any(node.get(flag) is True for flag in ("error", "throttle", "fault")) or bool(attrs.get("error.type"))


def _arguments(value):
    """(arguments hash, pagination base hash, cursor) or (None, None, None) when not recorded."""
    if value is None:
        return None, None, None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    full = _short_hash(value)
    if isinstance(value, dict):
        paging = {k: v for k, v in value.items() if k.lower().replace("_", "").replace("-", "") in PAGINATION_KEYS}
        if paging:
            base = {k: v for k, v in value.items() if k not in paging}
            return full, _short_hash(base), _canonical(paging)
    return full, None, None


def _check_node(node, where):
    if not isinstance(node, dict):
        raise _Malformed(f"{where} is not an object")
    if node.get("in_progress") is True:
        raise _Malformed(f"{where} {node.get('id', '?')} is in progress")
    if not isinstance(node.get("id"), str):
        raise _Malformed(f"{where} has no id")
    if not _is_number(node.get("start_time")) or not _is_number(node.get("end_time")):
        raise _Malformed(f"{where} {node['id']} has no numeric start_time/end_time")
    subsegments = node.get("subsegments", [])
    if not isinstance(subsegments, list):
        raise _Malformed(f"{where} {node['id']} has non-list subsegments")
    for child in subsegments:
        _check_node(child, "subsegment")


def _parse_trace(trace):
    """(trace_id, segment docs, children map, names, problem). Names are known even for malformed traces."""
    if not isinstance(trace, dict):
        return None, [], {}, set(), "trace is not an object"
    trace_id = trace.get("Id") if isinstance(trace.get("Id"), str) else None
    segments = trace.get("Segments")
    if trace_id is None or not isinstance(segments, list):
        return trace_id, [], {}, set(), "trace has no Id or Segments list"
    docs, names, problem = [], set(), None
    for segment in segments:
        raw = segment.get("Document") if isinstance(segment, dict) else None
        try:
            doc = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            sid = segment.get("Id") if isinstance(segment, dict) else None
            problem = problem or f"segment {sid or '?'}: Document is not a JSON object"
            continue
        if doc.get("inferred") is True:
            continue
        if doc.get("type") != "subsegment" and isinstance(doc.get("name"), str):
            names.add(doc["name"])
        docs.append(doc)
    if problem:
        return trace_id, [], {}, names, problem
    try:
        for doc in docs:
            _check_node(doc, "subsegment" if doc.get("type") == "subsegment" else "segment")
    except _Malformed as error:
        return trace_id, [], {}, names, str(error)

    # Attach independently sent subsegments (`type: subsegment` + `parent_id`) to their parents.
    known = set()
    stack = [d for d in docs if d.get("type") != "subsegment"]
    while stack:
        node = stack.pop()
        known.add(node["id"])
        stack.extend(node.get("subsegments", []))
    children, pending = {}, [d for d in docs if d.get("type") == "subsegment"]
    while pending:
        progress = False
        for doc in list(pending):
            if doc.get("parent_id") in known:
                children.setdefault(doc["parent_id"], []).append(doc)
                pending.remove(doc)
                inner = [doc]
                while inner:
                    node = inner.pop()
                    known.add(node["id"])
                    inner.extend(node.get("subsegments", []))
                progress = True
        if not progress:
            return trace_id, [], {}, names, f"subsegment {pending[0]['id']} has no parent in the trace"
    roots = [d for d in docs if d.get("type") != "subsegment"]
    return trace_id, roots, children, names, None


def _calls(segment, children):
    """GenAI calls under one segment, with the agent run each belongs to."""
    calls, max_depth = [], 0
    stack = [(segment, None, (segment["id"], ENTRYPOINT_RUN), 0)]
    while stack:
        node, parent, run, depth = stack.pop()
        kind, name, attrs = _classify(node)
        here = parent
        if kind is not None:
            # A span nested directly in one of the same kind (and tool) is another layer of the same call.
            duplicate = parent is not None and parent[0] == kind and (kind == "llm" or parent[1] == name)
            if kind == "agent" and not duplicate:
                run, depth = (node["id"], name), depth + 1
                max_depth = max(max_depth, depth)
            elif kind != "agent" and not duplicate:
                arguments = attrs.get("gen_ai.tool.call.arguments") if kind == "tool" else None
                full, base, cursor = _arguments(arguments)
                calls.append({
                    "kind": kind, "name": name, "run": run, "failed": _failed(node, attrs),
                    "args": full, "base": base, "cursor": cursor,
                    "start": node["start_time"], "seconds": max(0.0, node["end_time"] - node["start_time"]),
                })
            here = (kind, name)
        for child in list(node.get("subsegments", [])) + children.get(node["id"], []):
            stack.append((child, here, run, depth))
    return calls, max_depth


def _trace_summary(trace_id, calls, depth, duration):
    """Per-trace summary for one entrypoint, or None if neither rule can use it."""
    runs = {}
    for call in sorted(calls, key=lambda c: c["start"]):
        runs.setdefault(call["run"], []).append(call)
    worst_run, worst_repeat, paginated = None, None, []
    llm_total = tool_total = failed = without_args = 0
    for (_, label), run_calls in runs.items():
        ok = [c for c in run_calls if not c["failed"]]
        failed += len(run_calls) - len(ok)
        llm = [c for c in ok if c["kind"] == "llm"]
        tools = [c for c in ok if c["kind"] == "tool"]
        llm_total += len(llm)
        tool_total += len(tools)
        without_args += sum(1 for c in tools if c["args"] is None)
        if llm and (worst_run is None or len(llm) > worst_run["llm_calls"]):
            worst_run = {"agent": label, "llm_calls": len(llm), "tool_calls": len(tools)}
        groups, pages = {}, {}
        for call in tools:
            if call["args"] is not None:
                groups.setdefault((call["name"], call["args"]), []).append(call)
            if call["base"] is not None:
                pages.setdefault((call["name"], call["base"]), []).append(call)
        for (tool, args), group in sorted(groups.items()):
            if worst_repeat is None or len(group) > worst_repeat["calls"]:
                worst_repeat = {
                    "agent": label, "tool": tool, "arguments_hash": args, "calls": len(group),
                    "seconds": round(sum(c["seconds"] for c in group), 3),
                }
        for (tool, _), group in sorted(pages.items()):
            cursors = {c["cursor"] for c in group}
            if len(cursors) >= 2:
                paginated.append({"agent": label, "tool": tool, "calls": len(group), "distinct_cursors": len(cursors)})
    summary = {
        "trace_id": trace_id,
        "duration_seconds": round(duration, 3),
        "agent_runs": len(runs),
        "llm_calls": llm_total,
        "tool_calls": tool_total,
        "failed_calls": failed,
        "tool_calls_without_arguments": without_args,
        "max_agent_depth": depth,
        "max_run_iterations": worst_run,
        "max_identical_tool_calls": worst_repeat,
        "paginated_tools": paginated,
    }
    return summary, (worst_run is not None or worst_repeat is not None)


def _worst(traces, field, count):
    best = None
    for trace in sorted(traces, key=lambda t: t["trace_id"]):
        record = trace[field]
        if record is not None and (best is None or record[count] > best[count]):
            best = {"trace_id": trace["trace_id"], **record, "duration_seconds": trace["duration_seconds"]}
    return best


def normalize_xray_traces(traces: list[dict]) -> dict:
    """Summarize BatchGetTraces `Traces` entries per entrypoint (X-Ray segment name owning GenAI calls).

    Returns {"traces_received", "traces_without_genai_calls", "skipped_traces": [{"trace_id", "reason"}],
    "entrypoints": {name: telemetry data}}. Each telemetry data object is the `data` of one LLM-10 source.
    Traces with an unparseable document, an in-progress or orphaned (sub)segment are skipped whole.
    """
    if not isinstance(traces, list):
        raise EvaluationError("traces must be a list of BatchGetTraces trace objects")
    entrypoints, skipped, without_genai = {}, [], 0
    skipped_names = []
    for trace in traces:
        trace_id, roots, children, names, problem = _parse_trace(trace)
        if problem:
            skipped.append({"trace_id": trace_id, "reason": problem})
            skipped_names.append((names, problem))
            continue
        per_name = {}
        for segment in roots:
            calls, depth = _calls(segment, children)
            entry = per_name.setdefault(segment["name"], {"calls": [], "depth": 0, "start": [], "end": []})
            entry["calls"].extend(calls)
            entry["depth"] = max(entry["depth"], depth)
            entry["start"].append(segment["start_time"])
            entry["end"].append(segment["end_time"])
        active = {name: entry for name, entry in per_name.items() if entry["calls"]}
        if not active:
            without_genai += 1
            continue
        for name, entry in active.items():
            data = entrypoints.setdefault(name, {"traces": [], "seen": 0, "without_args": 0, "failed": 0})
            data["seen"] += 1
            duration = max(entry["end"]) - min(entry["start"])
            summary, analyzable = _trace_summary(trace_id, entry["calls"], entry["depth"], duration)
            data["without_args"] += summary["tool_calls_without_arguments"]
            data["failed"] += summary["failed_calls"]
            if analyzable:
                data["traces"].append(summary)

    out = {}
    for name in sorted(entrypoints):
        data = entrypoints[name]
        traces_out = sorted(data["traces"], key=lambda t: t["trace_id"])
        reasons = sorted({problem for names, problem in skipped_names if name in names})
        out[name] = {
            "entrypoint": name,
            "traces_with_genai_calls": data["seen"],
            "traces_analyzed": len(traces_out),
            "traces_skipped": sum(1 for names, _ in skipped_names if name in names),
            "skip_reasons": reasons,
            "failed_calls": data["failed"],
            "tool_calls_without_arguments": data["without_args"],
            "max_llm_iterations": _worst(traces_out, "max_run_iterations", "llm_calls"),
            "max_identical_tool_calls": _worst(traces_out, "max_identical_tool_calls", "calls"),
            "traces": traces_out,
        }
    return {
        "traces_received": len(traces),
        "traces_without_genai_calls": without_genai,
        "skipped_traces": skipped,
        "entrypoints": out,
    }


def telemetry_sources(normalized, locator="aws-xray:BatchGetTraces"):
    """(scope, sources) for a contract v1 LLM-10 input built from `normalize_xray_traces` output."""
    scope, sources = [], []
    for name, data in sorted(normalized["entrypoints"].items()):
        scope_id = SCOPE_PREFIX + name
        scope.append(scope_id)
        sources.append({
            "source_id": f"xray:{name}",
            "scope_id": scope_id,
            "kind": SUPPORTED_KIND,
            "locator": f"{locator}/{name}",
            "data": data,
        })
    return scope, sources


# --------------------------------------------------------------------------------------------------------
# Detection over contract v1 inputs
# --------------------------------------------------------------------------------------------------------


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    for key in SETTING_KEYS:
        if not _count(context[key]) or context[key] < 1:
            return None, f"context.{key} must be a positive integer"
    return {key: context[key] for key in SETTING_KEYS}, None


_NUMBER = "number"
RUN_SPEC = {"agent": str, "llm_calls": int, "tool_calls": int}
REPEAT_SPEC = {"agent": str, "tool": str, "arguments_hash": str, "calls": int, "seconds": _NUMBER}
WORST_SPEC = {"trace_id": str, "duration_seconds": _NUMBER}
TRACE_SPEC = {"trace_id": str, "duration_seconds": _NUMBER, "llm_calls": int, "tool_calls": int, "failed_calls": int,
              "tool_calls_without_arguments": int, "paginated_tools": list}
DATA_COUNTS = ("traces_with_genai_calls", "traces_analyzed", "traces_skipped", "failed_calls",
               "tool_calls_without_arguments")


def _spec_problems(record, name, spec):
    problems = []
    for key, kind in spec.items():
        if key not in record:
            problems.append(f"{name}.{key} is missing")
        elif kind is int and not _count(record[key]):
            problems.append(f"{name}.{key} must be a nonnegative integer")
        elif kind is _NUMBER and not (_is_number(record[key]) and record[key] >= 0):
            problems.append(f"{name}.{key} must be a nonnegative number")
        elif kind is str and not _text(record[key]):
            problems.append(f"{name}.{key} must be a nonempty string")
        elif kind is list and not isinstance(record[key], list):
            problems.append(f"{name}.{key} must be a list")
    return problems


def _record_problems(record, name, spec):
    if record is None:
        return []
    if not isinstance(record, dict):
        return [f"{name} must be an object or null"]
    return _spec_problems(record, name, spec)


def _data_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    required = ("entrypoint", "skip_reasons", "traces", "max_llm_iterations", "max_identical_tool_calls") + DATA_COUNTS
    missing = [k for k in required if k not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    if not _text(data["entrypoint"]):
        problems.append("entrypoint must be a nonempty string")
    elif scope_id != SCOPE_PREFIX + data["entrypoint"]:
        problems.append(f"scope id {scope_id!r} does not match entrypoint (expected '{SCOPE_PREFIX}{data['entrypoint']}')")
    problems += [f"{k} must be a nonnegative integer" for k in DATA_COUNTS if not _count(data[k])]
    if not isinstance(data["skip_reasons"], list) or not all(isinstance(r, str) for r in data["skip_reasons"]):
        problems.append("skip_reasons must be a list of strings")
    traces = data["traces"]
    if not isinstance(traces, list) or not all(isinstance(t, dict) for t in traces):
        return problems + ["traces must be a list of objects"]
    for index, trace in enumerate(traces):
        name = f"traces[{index}]"
        problems += _spec_problems(trace, name, TRACE_SPEC)
        runs, repeats = trace.get("max_run_iterations"), trace.get("max_identical_tool_calls")
        problems += _record_problems(runs, f"{name}.max_run_iterations", RUN_SPEC)
        problems += _record_problems(repeats, f"{name}.max_identical_tool_calls", REPEAT_SPEC)
        if runs is None and repeats is None:
            problems.append(f"{name} has neither model turns nor tool arguments")
    problems += _record_problems(data["max_llm_iterations"], "max_llm_iterations", {**WORST_SPEC, **RUN_SPEC})
    problems += _record_problems(
        data["max_identical_tool_calls"], "max_identical_tool_calls", {**WORST_SPEC, **REPEAT_SPEC})
    if problems:
        return problems
    if data["traces_analyzed"] != len(traces):
        problems.append(f"traces_analyzed {data['traces_analyzed']} does not match {len(traces)} trace summaries")
    for field, per_trace, key in (("max_llm_iterations", "max_run_iterations", "llm_calls"),
                                  ("max_identical_tool_calls", "max_identical_tool_calls", "calls")):
        values = [t[per_trace][key] for t in traces if t[per_trace] is not None]
        top = data[field][key] if data[field] is not None else None
        if (max(values) if values else None) != top:
            problems.append(f"{field} does not match the largest per-trace value")
    return problems


def _evidence(source, *fields):
    return [
        {"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"],
         "field": field, "value": source["data"][field]}
        for field in fields
    ]


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes or omitted reason)."""
    telemetry = [s for s in sources if s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [f"{scope_id}: no telemetry source supplied; LLM-10 requires normalized X-Ray traces"]
    if len(telemetry) > 1:
        return False, [], [f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"]
    source = telemetry[0]
    data = source.get("data")
    problems = _data_problems(data, scope_id)
    if problems:
        return False, [], [f"{scope_id}: " + "; ".join(problems)]
    analyzed = data["traces_analyzed"]
    if analyzed < settings["min_traces"]:
        reason = (f"{scope_id}: {_plural(analyzed, 'analyzable trace')} (with model turns or tool arguments) "
                  f"is below the required min_traces {settings['min_traces']}")
        if data["traces_skipped"]:
            reason += f"; {data['traces_skipped']} skipped: " + "; ".join(data["skip_reasons"])
        return False, [], [reason]

    traces = data["traces"]
    findings, notes = [], []
    limit = settings["max_identical_tool_calls"]
    repeats = [t for t in traces if t["max_identical_tool_calls"] and t["max_identical_tool_calls"]["calls"] > limit]
    if repeats:
        worst = data["max_identical_tool_calls"]
        findings.append({
            "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTICAL_IDENTITY),
            "scope_id": scope_id,
            "identity": IDENTICAL_IDENTITY,
            "summary": (
                f"{len(repeats)} of {analyzed} analyzed traces of {data['entrypoint']} call the same tool with "
                f"identical arguments more than {limit} times in one agent run; worst: {worst['tool']} called "
                f"{worst['calls']} times with arguments hash {worst['arguments_hash']} by {worst['agent']} in "
                f"trace {worst['trace_id']} ({worst['seconds']:g}s of tool time)"
            ),
            "confidence": "high" if len(repeats) >= 2 else "medium",
            "recommendation": IDENTICAL_RECOMMENDATION,
            "references": list(REFERENCES),
            "evidence": _evidence(source, "traces_analyzed", "max_identical_tool_calls"),
        })
    limit = settings["max_llm_iterations"]
    long_runs = [t for t in traces if t["max_run_iterations"] and t["max_run_iterations"]["llm_calls"] > limit]
    if long_runs:
        worst = data["max_llm_iterations"]
        findings.append({
            "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, ITERATION_IDENTITY),
            "scope_id": scope_id,
            "identity": ITERATION_IDENTITY,
            "summary": (
                f"{len(long_runs)} of {analyzed} analyzed traces of {data['entrypoint']} run more than {limit} "
                f"model turns in one agent run; worst: {worst['llm_calls']} model calls and "
                f"{_plural(worst['tool_calls'], 'tool call')} by {worst['agent']} in trace {worst['trace_id']} "
                f"({worst['duration_seconds']:g}s)"
            ),
            "confidence": "medium" if len(long_runs) >= 2 else "low",
            "recommendation": ITERATION_RECOMMENDATION,
            "references": list(REFERENCES),
            "evidence": _evidence(source, "traces_analyzed", "max_llm_iterations"),
        })

    if data["max_llm_iterations"] is None:
        notes.append(f"{scope_id}: no successful model-call spans; the iteration rule was not evaluated")
    if data["max_identical_tool_calls"] is None:
        notes.append(f"{scope_id}: no tool call recorded gen_ai.tool.call.arguments; "
                     "the identical-call rule was not evaluated")
    elif data["tool_calls_without_arguments"]:
        notes.append(f"{scope_id}: {_plural(data['tool_calls_without_arguments'], 'tool call')} without recorded "
                     "arguments could not be compared")
    if data["failed_calls"]:
        notes.append(f"{scope_id}: {_plural(data['failed_calls'], 'failed or throttled call')} excluded as retries")
    series = sum(len(t["paginated_tools"]) for t in traces)
    if series:
        notes.append(f"{scope_id}: {series} pagination series (same tool, changing cursor) not counted as "
                     "identical calls")
    if data["traces_skipped"]:
        notes.append(f"{scope_id}: {data['traces_skipped']} malformed or incomplete traces skipped: "
                     + "; ".join(data["skip_reasons"]))
    return True, findings, notes


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def evaluate(payload):
    """Evaluate a contract v1 LLM-10 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements {DETECTOR_VERSION}",
    )
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    result = {key: payload[key] for key in (
        "schema_version", "repository_id", "scan_id", "commit_sha", "check_id", "detector_version", "context", "scope")}
    result["kind"] = "result"

    settings, reason = _read_settings(payload["context"])
    if settings is None:
        result.update(
            status="unavailable",
            coverage={"evaluated_scope": [], "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION]},
            findings=[],
            measurements=[],
        )
        return result

    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        scope_sources = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id]
        ok, scope_findings, notes = _evaluate_scope(scope_id, scope_sources, settings, payload["repository_id"])
        if ok:
            evaluated.append(scope_id)
            findings.extend(scope_findings)
        limitations.extend(notes)
    limitations.append(LIMITATION)

    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"
    result.update(
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings,
        measurements=[],
    )
    return result
