"""LLM-05: redundant chained calls in multi-step pipelines (X-Ray trace analysis).

Detector semantics version 1.0.0. Two parts:

* `normalize_xray_traces(traces)` builds on LLM-10's public `llm10.normalize_xray_traces`, which skips
  malformed, in-progress and orphaned traces whole and gives the reason. For the traces it accepts, this module
  walks the same OpenTelemetry GenAI spans (metadata with dotted keys, as the ADOT awsxrayexporter writes them,
  or annotations with `_`) and adds what LLM-05 needs per model call: the agent run, `gen_ai.request.model`,
  a hash of the request (`gen_ai.input.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions` and
  the request parameters, or an app-recorded `gen_ai.input.messages.hash` digest) and the sampling temperature.
  Only hashes leave the normalizer, never prompt content. `telemetry_sources` is LLM-10's.
* `evaluate(payload)` checks contract v1 inputs built from those summaries. Within one agent run, successful
  model calls are ordered by start time. It flags an entrypoint, once `context.min_traces` traces were
  analyzable, when a run sends the same model an unchanged request more than
  `context.max_identical_consecutive_calls` times in a row (the later call cannot use the earlier output, so
  that output is discarded and recomputed), or when a chain of at least `context.min_chain_calls` comparable
  calls repeats earlier requests of the same run for at least `context.min_repeat_share` of its calls.

Not counted: failed/throttled calls and identical calls right after a failure (retries); identical calls whose
recorded `gen_ai.request.temperature` is > 0 (sampling); calls in different agent runs (sub-agents). Inputs are
Opt-In in the OTel GenAI conventions: chained calls without a comparable input make the trace unanalyzable and
are reported as a limitation, never as clean. The detector never calls AWS.
"""

from __future__ import annotations

import hashlib
import json

from . import llm10
from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, fingerprint

CHECK_ID = "LLM-05"
DETECTOR_VERSION = "1.0.0"
SUPPORTED_KIND = "telemetry"
SCOPE_PREFIX = llm10.SCOPE_PREFIX
SETTING_KEYS = ("min_traces", "max_identical_consecutive_calls", "min_chain_calls", "min_repeat_share")
# Reference values (README "LLM-05"); the registry supplies them as defaults. Settings stay required here.
REFERENCE_SETTINGS = {
    "min_traces": 10,
    "max_identical_consecutive_calls": 1,
    "min_chain_calls": 3,
    "min_repeat_share": 0.5,
}
CONSECUTIVE_IDENTITY = "consecutive-identical-calls"
CHAIN_IDENTITY = "repeated-chain-requests"
ENTRYPOINT_RUN = llm10.ENTRYPOINT_RUN

# OpenTelemetry GenAI client inference span attributes (semantic-conventions-genai, client-inference.md).
CONTENT_KEYS = ("gen_ai.input.messages", "gen_ai.system_instructions", "gen_ai.tool.definitions")  # Opt-In
INPUT_KEYS = ("gen_ai.input.messages", "gen_ai.prompt")  # gen_ai.prompt: deprecated/legacy instrumentations
LEGACY_PROMPT = "gen_ai.prompt"
LEGACY_PROMPT_PREFIX = "gen_ai.prompt."  # indexed gen_ai.prompt.<n>.role/content (OpenLLMetry)
# Not a semconv attribute: an app-recorded digest of the canonical input, for apps that keep content out of traces.
DIGEST_KEY = "gen_ai.input.messages.hash"
PARAMETER_KEYS = (
    "gen_ai.request.max_tokens", "gen_ai.request.temperature", "gen_ai.request.top_p", "gen_ai.request.top_k",
    "gen_ai.request.seed", "gen_ai.request.stop_sequences", "gen_ai.request.frequency_penalty",
    "gen_ai.request.presence_penalty", "gen_ai.request.choice.count", "gen_ai.output.type",
)
TEMPERATURE = "gen_ai.request.temperature"
ATTRIBUTES = (
    "gen_ai.operation.name", "gen_ai.request.model", "gen_ai.tool.name", "gen_ai.agent.name", "error.type",
    *CONTENT_KEYS, LEGACY_PROMPT, DIGEST_KEY, *PARAMETER_KEYS,
)
_ANNOTATION_KEYS = {key.replace(".", "_"): key for key in ATTRIBUTES}

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
    "https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md",
    "https://docs.aws.amazon.com/xray/latest/devguide/xray-api-segmentdocuments.html",
    "https://coralogix.com/ai-blog/token-efficiency/",
)
CONSECUTIVE_RECOMMENDATION = (
    "Do not send the same model the same request again within a run: reuse the previous step's result (memoize "
    "model responses per run, keyed by model and canonical request), pass each step's output to the next step, "
    "and fix the loop or chain step that re-issues an unchanged request. If you sample on purpose, record "
    "gen_ai.request.temperature, or ask for several candidates in one request (n / choice count) where supported."
)
CHAIN_RECOMMENDATION = (
    "Deduplicate the pipeline's steps: cache answers to sub-questions that were already asked in this run "
    "(AGENTCOST02-BP03), merge steps that re-ask the same question, and share intermediate results between steps "
    "instead of recomputing them."
)
LIMITATION = (
    "Trace analysis only: LLM-05 proves what sampled X-Ray traces recorded, not what the code does; token counts "
    "and cost are not measured. Requests are compared only when gen_ai.input.messages (Opt-In in the OpenTelemetry "
    "GenAI conventions) or an app-recorded gen_ai.input.messages.hash is present, by exact hash: paraphrased or "
    "overlapping questions, duplicates across separate agent runs and dependencies through tools or state are not "
    "detected. Uninstrumented SDK calls are not counted, and an unrecorded temperature is not assumed to be > 0."
)


# --------------------------------------------------------------------------------------------------------
# Normalization: BatchGetTraces -> per-entrypoint summaries (on top of llm10.normalize_xray_traces)
# --------------------------------------------------------------------------------------------------------


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _short_hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parsed(value):
    """JSON strings and structured values hash alike (the exporter may write either)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _attributes(node):
    """GenAI attributes of a (sub)segment; metadata (structured) wins over annotations, like LLM-10."""
    found, indexed = {}, {}
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        for namespace in sorted(metadata, key=lambda ns: (ns != "default", str(ns))):
            values = metadata[namespace]
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                if value is None or not isinstance(key, str):
                    continue
                if key.startswith(LEGACY_PROMPT_PREFIX):
                    indexed.setdefault(key, value)
                    continue
                key = key if key in ATTRIBUTES else _ANNOTATION_KEYS.get(key)
                if key:
                    found.setdefault(key, value)
    annotations = node.get("annotations")
    if isinstance(annotations, dict):
        for key, value in annotations.items():
            key = key if key in ATTRIBUTES else _ANNOTATION_KEYS.get(key)
            if key and value is not None:
                found.setdefault(key, value)
    if indexed and LEGACY_PROMPT not in found:
        found[LEGACY_PROMPT] = indexed
    return found


def _kind(node, attrs):
    """"llm", "tool", "agent" or None, with the same OTel GenAI rules as LLM-10."""
    operation = _text(attrs.get("gen_ai.operation.name"))
    operation = operation.lower() if operation else None
    tool = _text(attrs.get("gen_ai.tool.name"))
    if tool is None and isinstance(node.get("name"), str) and node["name"].startswith(llm10.TOOL_SPAN_PREFIX):
        tool = _text(node["name"][len(llm10.TOOL_SPAN_PREFIX):])
        operation = operation or "execute_tool"
    if operation in llm10.AGENT_OPERATIONS:
        return "agent"
    if operation in llm10.TOOL_OPERATIONS or (operation is None and tool):
        return "tool" if tool else None
    if operation in llm10.LLM_OPERATIONS or (operation is None and _text(attrs.get("gen_ai.request.model"))):
        return "llm"
    return None


def _failed(node, attrs):
    return any(node.get(flag) is True for flag in ("error", "throttle", "fault")) or bool(attrs.get("error.type"))


def _request(attrs):
    """(model, basis, request hash) — basis "content" or "digest" — or (model, None, None) when not comparable."""
    model = _text(attrs.get("gen_ai.request.model"))
    content = {key: _parsed(attrs[key]) for key in CONTENT_KEYS + (LEGACY_PROMPT,) if key in attrs}
    params = {key: attrs[key] for key in PARAMETER_KEYS if key in attrs}
    if model is None:
        return None, None, None
    if any(key in content for key in INPUT_KEYS):
        return model, "content", _short_hash({"model": model, "content": content, "params": params})
    digest = attrs.get(DIGEST_KEY)
    if isinstance(digest, (str, int)) and not isinstance(digest, bool) and str(digest).strip():
        return model, "digest", _short_hash({"model": model, "digest": str(digest).strip(), "content": content,
                                             "params": params})
    return model, None, None


def _sampling(call):
    return _is_number(call["temperature"]) and call["temperature"] > 0


def _documents(trace):
    """(roots, children by parent id) of a trace that llm10.normalize_xray_traces accepted."""
    docs = []
    for segment in trace["Segments"]:
        raw = segment.get("Document")
        doc = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(doc, dict) and doc.get("inferred") is not True:
            docs.append(doc)
    children = {}
    for doc in docs:
        if doc.get("type") == "subsegment":
            children.setdefault(doc.get("parent_id"), []).append(doc)
    return [d for d in docs if d.get("type") != "subsegment"], children


def _model_calls(segment, children):
    """Model calls under one segment, each with its agent run. A model span nested directly in another model
    span (a framework span around the instrumented client span), directly or through unclassified subsegments,
    is one call, as in LLM-10; the inner span's attributes fill in missing ones."""
    calls = []
    stack = [(segment, None, (segment["id"], ENTRYPOINT_RUN))]
    while stack:
        node, parent_call, run = stack.pop()
        attrs = _attributes(node)
        kind = _kind(node, attrs)
        here = parent_call if kind is None else None
        if kind == "agent":
            run = (node["id"], _text(attrs.get("gen_ai.agent.name")) or _text(node.get("name")) or "agent")
        elif kind == "llm" and parent_call is not None:
            for key, value in attrs.items():
                parent_call["attrs"].setdefault(key, value)
            parent_call["failed"] = parent_call["failed"] or _failed(node, attrs)
            here = parent_call
        elif kind == "llm":
            here = {"run": run, "attrs": attrs, "failed": _failed(node, attrs), "start": node["start_time"],
                    "seconds": max(0.0, node["end_time"] - node["start_time"])}
            calls.append(here)
        for child in list(node.get("subsegments", [])) + children.get(node["id"], []):
            stack.append((child, here, run))
    for call in calls:
        call["model"], call["basis"], call["key"] = _request(call["attrs"])
        temperature = call["attrs"].get(TEMPERATURE)
        call["temperature"] = temperature if _is_number(temperature) else None
    return calls


def _run_summary(label, calls, totals, chains):
    """Scan one run's model calls in time order; returns its longest identical streak (or None)."""
    previous, previous_streak, failed_since, worst = None, [], False, None
    seen, comparable, repeated, ok_calls = set(), 0, 0, 0
    for call in calls:
        if call["failed"]:
            totals["failed_model_calls"] += 1
            failed_since = True
            continue
        ok_calls += 1
        if call["key"] is None:
            totals["model_calls_without_input"] += 1
        else:
            comparable += 1
            if call["key"] in seen and not failed_since and not _sampling(call):
                repeated += 1
            seen.add(call["key"])
        streak = [call]
        if previous is not None:
            totals["consecutive_pairs"] += 1
            if previous["key"] is None or call["key"] is None:
                totals["uncompared_pairs"] += 1
            else:
                totals["comparable_pairs"] += 1
                if previous["key"] == call["key"]:
                    if failed_since:
                        totals["retry_repeats_excluded"] += 1
                    elif _sampling(previous) and _sampling(call):
                        totals["sampling_repeats_excluded"] += 1
                    else:
                        streak = previous_streak + [call]
        previous, previous_streak, failed_since = call, streak, False
        if len(streak) >= 2 and (worst is None or len(streak) > worst["calls"]):
            worst = {"agent": label, "model": call["model"], "input_basis": call["basis"], "input_hash": call["key"],
                     "calls": len(streak), "seconds": round(sum(c["seconds"] for c in streak), 3)}
    totals["model_calls"] += ok_calls
    if comparable >= 2:
        chains.append({"agent": label, "model_calls": ok_calls, "comparable_calls": comparable,
                       "repeated_calls": repeated})
    return worst


TRACE_COUNTS = ("model_calls", "failed_model_calls", "model_calls_without_input", "consecutive_pairs",
                "comparable_pairs", "uncompared_pairs", "retry_repeats_excluded", "sampling_repeats_excluded")


def _trace_summary(trace_id, calls, duration):
    """(summary, chains, analyzable) for one entrypoint in one trace."""
    runs = {}
    for call in sorted(calls, key=lambda c: c["start"]):
        runs.setdefault(call["run"], []).append(call)
    totals = dict.fromkeys(TRACE_COUNTS, 0)
    chains, worst, agent_runs = [], None, 0
    for (_, label), run_calls in runs.items():
        agent_runs += 1
        record = _run_summary(label, run_calls, totals, chains)
        if record and (worst is None or record["calls"] > worst["calls"]):
            worst = record
    summary = {"trace_id": trace_id, "duration_seconds": round(duration, 3), "agent_runs": agent_runs, **totals,
               "max_consecutive_identical": worst}
    analyzable = totals["model_calls"] > 0 and (totals["consecutive_pairs"] == 0 or totals["comparable_pairs"] > 0)
    return summary, [{"trace_id": trace_id, **chain} for chain in chains], analyzable


DATA_SUMS = ("failed_model_calls", "model_calls_without_input", "uncompared_pairs", "retry_repeats_excluded",
             "sampling_repeats_excluded")


def normalize_xray_traces(traces: list[dict]) -> dict:
    """Summarize BatchGetTraces `Traces` entries per entrypoint for LLM-05.

    Returns {"traces_received", "traces_without_model_calls", "skipped_traces": [{"trace_id", "reason"}],
    "entrypoints": {name: telemetry data}}. Traces that llm10.normalize_xray_traces skips are skipped here too,
    with its reason. Entrypoints appear when some trace has a successful model call under them.
    """
    base = llm10.normalize_xray_traces(traces)
    skipped_ids = {item["trace_id"] for item in base["skipped_traces"]}
    entrypoints, without_model = {}, 0
    for trace in traces:
        if not isinstance(trace, dict) or not isinstance(trace.get("Id"), str) or trace["Id"] in skipped_ids \
                or not isinstance(trace.get("Segments"), list):
            continue
        roots, children = _documents(trace)
        per_name = {}
        for segment in roots:
            entry = per_name.setdefault(segment["name"], {"calls": [], "start": [], "end": []})
            entry["calls"].extend(_model_calls(segment, children))
            entry["start"].append(segment["start_time"])
            entry["end"].append(segment["end_time"])
        active = {name: e for name, e in per_name.items() if any(not c["failed"] for c in e["calls"])}
        if not active:
            without_model += 1
        for name, entry in active.items():
            data = entrypoints.setdefault(name, {"seen": 0, "without_inputs": 0, "traces": [], "chains": [],
                                                 **dict.fromkeys(DATA_SUMS, 0)})
            data["seen"] += 1
            summary, chains, analyzable = _trace_summary(trace["Id"], entry["calls"],
                                                         max(entry["end"]) - min(entry["start"]))
            for key in DATA_SUMS:
                data[key] += summary[key]
            if analyzable:
                data["traces"].append(summary)
                data["chains"].extend(chains)
            else:
                data["without_inputs"] += 1

    out = {}
    for name in sorted(entrypoints):
        data = entrypoints[name]
        prior = base["entrypoints"].get(name, {})
        traces_out = sorted(data["traces"], key=lambda t: t["trace_id"])
        worst = None
        for trace in traces_out:
            record = trace["max_consecutive_identical"]
            if record is not None and (worst is None or record["calls"] > worst["calls"]):
                worst = {"trace_id": trace["trace_id"], **record, "duration_seconds": trace["duration_seconds"]}
        out[name] = {
            "entrypoint": name,
            "traces_with_model_calls": data["seen"],
            "traces_analyzed": len(traces_out),
            "traces_without_inputs": data["without_inputs"],
            "traces_skipped": prior.get("traces_skipped", 0),
            "skip_reasons": list(prior.get("skip_reasons", [])),
            **{key: data[key] for key in DATA_SUMS},
            "max_consecutive_identical": worst,
            "repeated_chains": sorted((c for c in data["chains"] if c["repeated_calls"] > 0),
                                      key=lambda c: (c["trace_id"], c["agent"])),
            "traces": traces_out,
        }
    return {
        "traces_received": base["traces_received"],
        "traces_without_model_calls": without_model,
        "skipped_traces": base["skipped_traces"],
        "entrypoints": out,
    }


def telemetry_sources(normalized, locator="aws-xray:BatchGetTraces"):
    """(scope, sources) for a contract v1 LLM-05 input; the same scope/source shape as LLM-10."""
    return llm10.telemetry_sources(normalized, locator=locator)


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
    for key in ("min_traces", "max_identical_consecutive_calls"):
        if not _count(context[key]) or context[key] < 1:
            return None, f"context.{key} must be a positive integer"
    if not _count(context["min_chain_calls"]) or context["min_chain_calls"] < 2:
        return None, "context.min_chain_calls must be an integer of at least 2"
    share = context["min_repeat_share"]
    if not _is_number(share) or not 0 < share <= 1:
        return None, "context.min_repeat_share must be a number in (0, 1]"
    return {key: context[key] for key in SETTING_KEYS}, None


_NUMBER = "number"
STREAK_SPEC = {"agent": str, "model": str, "input_basis": str, "input_hash": str, "calls": int, "seconds": _NUMBER}
WORST_SPEC = {"trace_id": str, "duration_seconds": _NUMBER, **STREAK_SPEC}
CHAIN_SPEC = {"trace_id": str, "agent": str, "model_calls": int, "comparable_calls": int, "repeated_calls": int}
TRACE_SPEC = {"trace_id": str, "duration_seconds": _NUMBER, "agent_runs": int, **dict.fromkeys(TRACE_COUNTS, int)}
DATA_COUNTS = ("traces_with_model_calls", "traces_analyzed", "traces_without_inputs", "traces_skipped") + DATA_SUMS


def _spec_problems(record, name, spec):
    if not isinstance(record, dict):
        return [f"{name} must be an object"]
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
    return problems


def _data_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    required = ("entrypoint", "skip_reasons", "traces", "max_consecutive_identical", "repeated_chains") + DATA_COUNTS
    missing = [key for key in required if key not in data]
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    problems = []
    if not _text(data["entrypoint"]):
        problems.append("entrypoint must be a nonempty string")
    elif scope_id != SCOPE_PREFIX + data["entrypoint"]:
        problems.append(f"scope id {scope_id!r} does not match entrypoint (expected '{SCOPE_PREFIX}{data['entrypoint']}')")
    problems += [f"{key} must be a nonnegative integer" for key in DATA_COUNTS if not _count(data[key])]
    if not isinstance(data["skip_reasons"], list) or not all(isinstance(r, str) for r in data["skip_reasons"]):
        problems.append("skip_reasons must be a list of strings")
    traces, chains = data["traces"], data["repeated_chains"]
    if not isinstance(traces, list) or not isinstance(chains, list):
        return problems + ["traces and repeated_chains must be lists"]
    for index, trace in enumerate(traces):
        problems += _spec_problems(trace, f"traces[{index}]", TRACE_SPEC)
        if isinstance(trace, dict) and trace.get("max_consecutive_identical") is not None:
            problems += _spec_problems(trace["max_consecutive_identical"],
                                       f"traces[{index}].max_consecutive_identical", STREAK_SPEC)
    for index, chain in enumerate(chains):
        problems += _spec_problems(chain, f"repeated_chains[{index}]", CHAIN_SPEC)
    if data["max_consecutive_identical"] is not None:
        problems += _spec_problems(data["max_consecutive_identical"], "max_consecutive_identical", WORST_SPEC)
    if problems:
        return problems
    if data["traces_analyzed"] != len(traces):
        problems.append(f"traces_analyzed {data['traces_analyzed']} does not match {len(traces)} trace summaries")
    if data["traces_analyzed"] + data["traces_without_inputs"] > data["traces_with_model_calls"]:
        problems.append("traces_analyzed + traces_without_inputs exceeds traces_with_model_calls")
    for trace in traces:
        if trace["model_calls"] == 0 or (trace["consecutive_pairs"] and not trace["comparable_pairs"]):
            problems.append(f"trace {trace['trace_id']} is not analyzable")
        if trace["max_consecutive_identical"] is not None and trace["max_consecutive_identical"]["calls"] < 2:
            problems.append(f"trace {trace['trace_id']}: an identical streak needs at least 2 calls")
    analyzed_ids = {t["trace_id"] for t in traces}
    for chain in chains:
        if chain["trace_id"] not in analyzed_ids:
            problems.append(f"repeated chain refers to trace {chain['trace_id']}, which is not analyzed")
        if not 0 < chain["repeated_calls"] < chain["comparable_calls"] <= chain["model_calls"]:
            problems.append(f"repeated chain in trace {chain['trace_id']} has inconsistent call counts")
    streaks = [t["max_consecutive_identical"]["calls"] for t in traces if t["max_consecutive_identical"]]
    top = data["max_consecutive_identical"]["calls"] if data["max_consecutive_identical"] else None
    if (max(streaks) if streaks else None) != top:
        problems.append("max_consecutive_identical does not match the largest per-trace streak")
    return problems


def _evidence(source, *fields):
    return [
        {"source_id": source["source_id"], "kind": SUPPORTED_KIND, "locator": source["locator"],
         "field": field, "value": source["data"][field]}
        for field in fields
    ]


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _inputs_note(scope_id, data):
    return (f"{scope_id}: {_plural(data['traces_without_inputs'], 'trace')} with chained model calls recorded no "
            "comparable input (gen_ai.input.messages is Opt-In in the OpenTelemetry GenAI conventions; record it or "
            "a gen_ai.input.messages.hash digest) and could not be analyzed")


def _evaluate_scope(scope_id, sources, settings, repository_id):
    """(evaluated, findings, notes or omitted reason)."""
    telemetry = [s for s in sources if s.get("kind") == SUPPORTED_KIND]
    if not telemetry:
        return False, [], [f"{scope_id}: no telemetry source supplied; LLM-05 requires normalized X-Ray traces"]
    if len(telemetry) > 1:
        return False, [], [f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"]
    source = telemetry[0]
    data = source.get("data")
    problems = _data_problems(data, scope_id)
    if problems:
        return False, [], [f"{scope_id}: " + "; ".join(problems)]
    analyzed = data["traces_analyzed"]
    if analyzed < settings["min_traces"]:
        reasons = [f"{scope_id}: {_plural(analyzed, 'analyzable trace')} is below the required min_traces "
                   f"{settings['min_traces']}"]
        if data["traces_without_inputs"]:
            reasons.append(_inputs_note(scope_id, data))
        if data["traces_skipped"]:
            reasons.append(f"{scope_id}: {data['traces_skipped']} malformed or incomplete traces skipped: "
                           + "; ".join(data["skip_reasons"]))
        return False, [], reasons

    traces, findings, notes = data["traces"], [], []
    limit = settings["max_identical_consecutive_calls"]
    streaks = [t for t in traces if t["max_consecutive_identical"] and t["max_consecutive_identical"]["calls"] > limit]
    if streaks:
        worst = data["max_consecutive_identical"]
        findings.append({
            "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, CONSECUTIVE_IDENTITY),
            "scope_id": scope_id,
            "identity": CONSECUTIVE_IDENTITY,
            "summary": (
                f"{len(streaks)} of {analyzed} analyzed traces of {data['entrypoint']} send the same model an "
                f"unchanged request {limit + 1} or more times in a row in one agent run, so earlier outputs are "
                f"discarded and recomputed; worst: {worst['calls']} consecutive identical {worst['model']} calls "
                f"(request hash {worst['input_hash']}, from {worst['input_basis']}) by {worst['agent']} in trace "
                f"{worst['trace_id']} ({worst['seconds']:g}s of model time)"
            ),
            "confidence": "high" if len(streaks) >= 2 else "medium",
            "recommendation": CONSECUTIVE_RECOMMENDATION,
            "references": list(REFERENCES),
            "evidence": _evidence(source, "traces_analyzed", "max_consecutive_identical"),
        })

    minimum, share = settings["min_chain_calls"], settings["min_repeat_share"]
    breaching = [c for c in data["repeated_chains"]
                 if c["comparable_calls"] >= minimum and c["repeated_calls"] / c["comparable_calls"] >= share]
    if breaching:
        worst = max(sorted(breaching, key=lambda c: (c["trace_id"], c["agent"])),
                    key=lambda c: (c["repeated_calls"] / c["comparable_calls"], c["repeated_calls"]))
        hit = sorted({c["trace_id"] for c in breaching})
        findings.append({
            "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, CHAIN_IDENTITY),
            "scope_id": scope_id,
            "identity": CHAIN_IDENTITY,
            "summary": (
                f"{len(hit)} of {analyzed} analyzed traces of {data['entrypoint']} run a chain of at least {minimum} "
                f"model calls in one agent run where at least {share:.0%} of the calls repeat an earlier request; "
                f"worst: {worst['repeated_calls']} of {worst['comparable_calls']} calls repeated by {worst['agent']} "
                f"in trace {worst['trace_id']}"
            ),
            "confidence": "medium" if len(hit) >= 2 else "low",
            "recommendation": CHAIN_RECOMMENDATION,
            "references": list(REFERENCES),
            "evidence": _evidence(source, "traces_analyzed", "repeated_chains"),
        })

    if data["traces_without_inputs"]:
        notes.append(_inputs_note(scope_id, data))
    if data["uncompared_pairs"]:
        notes.append(f"{scope_id}: {_plural(data['uncompared_pairs'], 'consecutive call pair')} without a recorded "
                     "model or input could not be compared")
    if not any(t["comparable_pairs"] for t in traces):
        notes.append(f"{scope_id}: no agent run had two comparable model calls; analyzed traces make one model call "
                     "per run")
    if data["failed_model_calls"]:
        notes.append(f"{scope_id}: {_plural(data['failed_model_calls'], 'failed or throttled model call')} excluded, "
                     f"and {_plural(data['retry_repeats_excluded'], 'identical repeat')} after a failure excluded as "
                     "retries")
    if data["sampling_repeats_excluded"]:
        notes.append(f"{scope_id}: {_plural(data['sampling_repeats_excluded'], 'identical consecutive call')} with "
                     "gen_ai.request.temperature > 0 treated as intentional sampling")
    if data["traces_skipped"]:
        notes.append(f"{scope_id}: {data['traces_skipped']} malformed or incomplete traces skipped: "
                     + "; ".join(data["skip_reasons"]))
    return True, findings, notes


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def evaluate(payload):
    """Evaluate a contract v1 LLM-05 input payload and return a contract v1 result payload."""
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
