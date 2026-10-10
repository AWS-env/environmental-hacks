"""Simulated client workload for owner D's telemetry checks (demo only, not detector code).

Deployed as ``owner-d-telemetry-demo`` (cdk/owner-d/telemetry-demo.yaml). Each invocation emits synthetic
signals that owner D's CloudWatch Logs, CloudWatch metrics and X-Ray analyzers should find. Every scenario has
a ``waste`` path and a clean ``control`` path, and every emitted item is labeled ``synthetic=true``:

* OBS-11  the same log line repeated by a retry loop, vs one summary line
* OBS-17  a log line carrying a full stack trace and the echoed request body, vs a lean structured line
* OBS-04  unstructured text lines, vs the same events as JSON lines
* OBS-06  custom metric ``RequestLatencyMs`` in ``OwnerD/Demo`` keyed by ``request_id`` (high cardinality),
          vs keyed by ``endpoint`` (low cardinality). Values come from fixed pools, so the number of distinct
          series is bounded across all invocations (MAX_METRIC_SERIES)
* LLM-10  X-Ray subsegments with OpenTelemetry GenAI attributes: an agent run that calls the same tool with
          byte-identical arguments until its iteration cap stops it, vs a bounded run that stops once it has
          an answer. No LLM is called; the model name is a placeholder
* LLM-05  (opt-in, not part of ``all``) a two-step pipeline whose second ``chat`` call sends the same model the
          same request (identical ``gen_ai.input.messages.hash``), vs a chain whose second request carries the
          first step's output. No LLM is called
* LLM-12  (opt-in, not part of ``all``) several agents answer the same questions, each through its own
          in-process cache (every agent misses every question once), vs the same agents through one shared cache
          (only the first agent misses). One structured cache-lookup line per lookup; no LLM is called

The event selects what to emit: ``{"scenario": "all" | "OBS-11" | "OBS-17" | "OBS-04" | "OBS-06" | "LLM-10"}``,
or ``{"scenario": "LLM-05" | "LLM-12"}``. Optional knobs, each clamped: ``repeat`` (OBS-11 lines), ``series``
(OBS-06 request ids), ``tool_calls`` (LLM-10 iterations), ``agents`` and ``rounds`` (LLM-12); ``path`` (LLM-10,
LLM-05 and LLM-12: ``both``, ``waste`` or ``control``). Subsegments are sent straight to the Lambda X-Ray daemon over UDP, so the function needs
no dependency beyond the Python runtime (boto3 is used only for PutMetricData).
"""

import hashlib
import json
import os
import socket
import sys
import time
import traceback
import uuid

NAMESPACE = "OwnerD/Demo"
METRIC_NAME = "RequestLatencyMs"
SCENARIOS = ("OBS-11", "OBS-17", "OBS-04", "OBS-06", "LLM-10")  # what "all" runs
OPT_IN_SCENARIOS = ("LLM-05", "LLM-12")  # selected by name only, so "all" keeps its output

# OBS-06 series bound: request_id values come from a fixed pool, endpoint from a fixed list.
REQUEST_ID_POOL = 40
ENDPOINTS = ("/checkout", "/search")
MAX_METRIC_SERIES = REQUEST_ID_POOL + len(ENDPOINTS)

# LLM-12 agents, in order; the `agents` knob takes the first N.
CACHE_AGENTS = ("planner", "researcher", "writer", "reviewer", "support", "triage", "billing", "escalation")

LIMITS = {"repeat": (20, 1, 50), "series": (REQUEST_ID_POOL, 1, REQUEST_ID_POOL), "tool_calls": (12, 1, 25),
          "agents": (5, 2, len(CACHE_AGENTS)), "rounds": (3, 1, 5)}
SPAN_SECONDS = 0.01


def _now():
    return time.time()


class Emitter:
    """Collects everything one invocation emits; the sinks are injectable so tests run offline."""

    def __init__(self, run_id, write=None, cloudwatch=None, xray=None):
        self.run_id = run_id
        self._write = write or _stdout
        self._cloudwatch = cloudwatch
        self._xray = xray
        self.log_lines = []
        self.metric_data = []
        self.subsegments = []

    def text(self, line):
        self.log_lines.append(line)
        self._write(line)

    def json(self, check, path, **fields):
        record = {"synthetic": True, "check": check, "path": path, "run_id": self.run_id, **fields}
        self.text(json.dumps(record, separators=(",", ":"), sort_keys=False))

    def metrics(self, data):
        cloudwatch = self._cloudwatch or _boto3_client("cloudwatch")
        for start in range(0, len(data), 500):
            cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=data[start:start + 500])
        self.metric_data.extend(data)

    def subsegment(self, document):
        self.subsegments.append(document)
        return (self._xray or XRayDaemon.from_env()).send(document)


def _stdout(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _boto3_client(name):
    import boto3  # bundled in the Lambda runtime; imported lazily so tests need no AWS SDK

    return boto3.client(name)


class XRayDaemon:
    """Sends subsegment documents to the X-Ray daemon that Lambda runs when Active tracing is on."""

    def __init__(self, trace_header, address):
        fields = dict(part.split("=", 1) for part in (trace_header or "").split(";") if "=" in part)
        self.trace_id = fields.get("Root")
        self.parent_id = fields.get("Parent")
        self.sampled = fields.get("Sampled") == "1"
        self.address = address

    @classmethod
    def from_env(cls):
        raw = os.environ.get("AWS_XRAY_DAEMON_ADDRESS", "127.0.0.1:2000")
        udp = next((part[4:] for part in raw.split() if part.startswith("udp:")), raw.split()[0])
        host, port = udp.rsplit(":", 1)
        return cls(os.environ.get("_X_AMZN_TRACE_ID"), (host, int(port)))

    def payload(self, document):
        body = {**document, "type": "subsegment", "trace_id": self.trace_id, "parent_id": self.parent_id}
        return b'{"format":"json","version":1}\n' + json.dumps(body, separators=(",", ":")).encode()

    def send(self, document):
        if not (self.sampled and self.trace_id and self.parent_id):
            return "not_sampled"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(self.payload(document), self.address)
        return "sent"


# ---- OBS-11: repeated log lines ------------------------------------------------------------------------

def obs11(emit, repeat):
    message = "upstream inventory-svc unavailable, retrying"
    for _ in range(repeat):
        emit.json("OBS-11", "waste", level="WARN", message=message, upstream="inventory-svc")
    emit.json("OBS-11", "control", level="WARN", message="upstream inventory-svc unavailable after retries",
              upstream="inventory-svc", attempts=repeat)
    return {"waste_lines": repeat, "control_lines": 1}


# ---- OBS-17: verbose fields ----------------------------------------------------------------------------

def _request_body():
    items = [{"sku": f"SKU-{i:04d}", "qty": 1 + i % 3, "note": "synthetic demo item " * 4} for i in range(40)]
    return {"order_id": "ord-demo-0001", "customer": "demo-customer", "items": items, "synthetic": True}


def _parse(body):
    raise ValueError(f"quantity limit exceeded for {body['items'][-1]['sku']}")


def _validate(body):
    try:
        _parse(body)
    except ValueError as exc:
        raise RuntimeError("order validation failed") from exc


def obs17(emit):
    body = _request_body()
    raw = json.dumps(body)
    error, stack = None, ""
    try:
        _validate(body)
    except RuntimeError as exc:
        error, stack = exc, traceback.format_exc()
    emit.json("OBS-17", "waste", level="ERROR", message="order rejected", error=str(error),
              stack_trace=stack, request_body=body)
    emit.json("OBS-17", "control", level="ERROR", message="order rejected", error_type=type(error).__name__,
              error=str(error), order_id=body["order_id"], body_bytes=len(raw),
              body_sha256=hashlib.sha256(raw.encode()).hexdigest()[:16])
    return {"waste_bytes": len(emit.log_lines[-2]), "control_bytes": len(emit.log_lines[-1])}


# ---- OBS-04: unstructured vs structured ----------------------------------------------------------------

EVENTS = (
    ("INFO", "payment authorized", {"order_id": "ord-demo-0002", "amount_cents": 1999, "ms": 84}),
    ("WARN", "inventory lookup slow", {"order_id": "ord-demo-0002", "sku": "SKU-0007", "ms": 2310}),
    ("INFO", "order shipped", {"order_id": "ord-demo-0002", "carrier": "demo-post", "ms": 12}),
)


def obs04(emit):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    for level, message, fields in EVENTS:
        detail = " ".join(f"{key} {value}" for key, value in fields.items())
        emit.text(f"{stamp} {level} [synthetic=true check=OBS-04 path=waste run={emit.run_id}] "
                  f"{message} for {detail}")
    for level, message, fields in EVENTS:
        emit.json("OBS-04", "control", level=level, message=message, **fields)
    return {"waste_lines": len(EVENTS), "control_lines": len(EVENTS)}


# ---- OBS-06: high- vs low-cardinality metric dimensions ------------------------------------------------

def _datum(path, key, value, latency):
    dims = [{"Name": "synthetic", "Value": "true"}, {"Name": "check", "Value": "OBS-06"},
            {"Name": "path", "Value": path}, {"Name": key, "Value": value}]
    return {"MetricName": METRIC_NAME, "Dimensions": dims, "Value": latency, "Unit": "Milliseconds"}


def obs06(emit, series):
    data = [_datum("waste", "request_id", f"req-{i:03d}", 50.0 + i) for i in range(series)]
    data += [_datum("control", "endpoint", endpoint, 50.0 + i) for i, endpoint in enumerate(ENDPOINTS)]
    emit.metrics(data)
    emit.json("OBS-06", "waste", level="INFO", message="metric datapoints published", namespace=NAMESPACE,
              metric=METRIC_NAME, dimension="request_id", series=series)
    emit.json("OBS-06", "control", level="INFO", message="metric datapoints published", namespace=NAMESPACE,
              metric=METRIC_NAME, dimension="endpoint", series=len(ENDPOINTS))
    return {"waste_series": series, "control_series": len(ENDPOINTS)}


# ---- LLM-10: repeated identical tool calls in an agent loop --------------------------------------------

# Spans follow the OpenTelemetry GenAI semantic conventions as the ADOT awsxrayexporter writes them to X-Ray:
# dotted attribute keys under metadata.default, span names `invoke_agent <agent>`, `chat <model>` and
# `execute_tool <tool>`. Owner D's LLM-10 normalizer (llm10.normalize_xray_traces) reads exactly this shape.

DEMO_MODEL = "synthetic-demo-model"  # placeholder, not a real model; no LLM is called
LOOKUP = ("get_order_status", {"order_id": "ord-demo-0003"})
LLM10_PATHS = ("both", "waste", "control")


def _segment_id():
    return uuid.uuid4().hex[:16]


def _span(name, attributes):
    start = _now()
    time.sleep(SPAN_SECONDS)
    return {"id": _segment_id(), "name": name, "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-10"}, "metadata": {"default": attributes}}


def _chat():
    return _span(f"chat {DEMO_MODEL}", {"gen_ai.operation.name": "chat", "gen_ai.request.model": DEMO_MODEL,
                                        "gen_ai.provider.name": "synthetic"})


def _execute_tool(tool, args):
    # Identical calls carry byte-identical argument strings, as an instrumented tool would record them.
    return _span(f"execute_tool {tool}", {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": f"call_{uuid.uuid4().hex[:12]}",
        "gen_ai.tool.call.arguments": json.dumps(args, sort_keys=True)})


def _agent_run(path, agent, tool_calls, stop_reason, final_turn):
    """One `invoke_agent` span: a model turn before each tool call, plus a closing turn if it answers."""
    start = _now()
    children = []
    for tool, args in tool_calls:
        children += [_chat(), _execute_tool(tool, args)]
    if final_turn:
        children.append(_chat())
    turns = sum(1 for child in children if child["name"].startswith("chat "))
    return {"id": _segment_id(), "name": f"invoke_agent {agent}", "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-10", "path": path, "llm_calls": turns,
                            "tool_calls": len(tool_calls), "stop_reason": stop_reason},
            "metadata": {"default": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent}},
            "subsegments": children}


def llm10(emit, tool_calls, path="both"):
    if path not in LLM10_PATHS:
        raise ValueError(f"path must be one of {', '.join(LLM10_PATHS)}")
    runs = []
    if path in ("both", "waste"):
        # Re-issues the same lookup with byte-identical arguments until its iteration cap stops it.
        runs.append(("waste", _agent_run("waste", "demo_unbounded_agent", [LOOKUP] * tool_calls,
                                         "max_iterations", final_turn=False)))
    if path in ("both", "control"):
        # Looks the order up once, drafts a reply from the result, then answers.
        runs.append(("control", _agent_run("control", "demo_bounded_agent",
                                           [LOOKUP, ("draft_reply", {"status": "shipped"})],
                                           "answer_ready", final_turn=True)))
    summary = {}
    for label, doc in runs:
        summary["xray"] = emit.subsegment(doc)
        notes = doc["annotations"]
        emit.json("LLM-10", label, level="INFO", message="agent run finished",
                  agent=doc["metadata"]["default"]["gen_ai.agent.name"], llm_calls=notes["llm_calls"],
                  tool_calls=notes["tool_calls"], stop_reason=notes["stop_reason"])
        summary[f"{label}_llm_calls"] = notes["llm_calls"]
        summary[f"{label}_tool_calls"] = notes["tool_calls"]
    return summary


# ---- LLM-05: redundant chained model calls -------------------------------------------------------------

# Two-step pipeline: step 1 extracts facts, step 2 should build on them. Instead of recording prompt content
# (gen_ai.input.messages is Opt-In), each chat span carries gen_ai.input.messages.hash, a digest of the canonical
# request, which owner D's LLM-05 normalizer (llm05.normalize_xray_traces) compares within one agent run.

PIPELINE_QUESTION = "Extract the order id, status and carrier from ticket T-1007."
STEP_ONE_OUTPUT = "order_id=ord-demo-0005 status=delayed carrier=demo-post"


def _digest(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _pipeline_chat(messages, path, step):
    start = _now()
    time.sleep(SPAN_SECONDS)
    return {"id": _segment_id(), "name": f"chat {DEMO_MODEL}", "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-05", "path": path, "step": step},
            "metadata": {"default": {"gen_ai.operation.name": "chat", "gen_ai.request.model": DEMO_MODEL,
                                     "gen_ai.provider.name": "synthetic",
                                     "gen_ai.input.messages.hash": _digest(messages)}}}


def _pipeline_run(path, agent, second_request):
    start = _now()
    first = [{"role": "user", "content": PIPELINE_QUESTION}]
    children = [_pipeline_chat(first, path, 1), _pipeline_chat(second_request(first), path, 2)]
    return {"id": _segment_id(), "name": f"invoke_agent {agent}", "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-05", "path": path, "llm_calls": len(children)},
            "metadata": {"default": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent}},
            "subsegments": children}


def llm05(emit, path="both"):
    if path not in LLM10_PATHS:
        raise ValueError(f"path must be one of {', '.join(LLM10_PATHS)}")
    runs = []
    if path in ("both", "waste"):
        # Step 2 re-sends step 1's request unchanged, so step 1's output is discarded and recomputed.
        runs.append(("waste", _pipeline_run("waste", "demo_redundant_pipeline", list)))
    if path in ("both", "control"):
        # Step 2 carries step 1's output forward with a new instruction.
        runs.append(("control", _pipeline_run("control", "demo_chained_pipeline", lambda first: first + [
            {"role": "assistant", "content": STEP_ONE_OUTPUT},
            {"role": "user", "content": "Draft a one-line customer update from these facts."}])))
    summary = {}
    for label, doc in runs:
        summary["xray"] = emit.subsegment(doc)
        hashes = [child["metadata"]["default"]["gen_ai.input.messages.hash"] for child in doc["subsegments"]]
        emit.json("LLM-05", label, level="INFO", message="pipeline run finished",
                  agent=doc["metadata"]["default"]["gen_ai.agent.name"], llm_calls=len(hashes),
                  distinct_requests=len(set(hashes)))
        summary[f"{label}_llm_calls"] = len(hashes)
        summary[f"{label}_distinct_requests"] = len(set(hashes))
    return summary


# ---- LLM-12: per-agent isolated caches vs one shared cache ---------------------------------------------

# Every agent answers the same support questions `rounds` times. The cache key is a digest of the model and the
# question, as an LLM response cache would compute it; the run id keeps each run's questions new, so every run
# re-warms the caches the way new traffic does. Lines carry the fields owner D's LLM-12 query reads: cache_result,
# cache_key_hash, agent_id, cache_name and cache_backend. No question text is logged.

CACHE_QUESTIONS = (
    "Where is order ord-demo-0006?", "How do I change my delivery address?", "What is the refund policy?",
    "Which carriers ship to Pune?", "How do I reset my password?", "Can I split a payment across two cards?",
    "Why was my card declined?", "How long does a return take?",
)
CACHE_NAMES = {"waste": "demo-agent-local", "control": "demo-fleet-shared"}
CACHE_BACKENDS = {"waste": "memory", "control": "demo-shared"}


def _cache_key(run_id, question):
    return hashlib.sha256(f"{DEMO_MODEL}\n{run_id}\n{question}".encode()).hexdigest()[:16]


def _cache_path(emit, path, agents, rounds):
    """One lookup line per (round, question, agent). Waste: one dict per agent; control: one dict for all."""
    shared = {}
    caches = {agent: ({} if path == "waste" else shared) for agent in agents}
    misses = 0
    for _ in range(rounds):
        for question in CACHE_QUESTIONS:
            key = _cache_key(emit.run_id, question)
            for agent in agents:
                cache = caches[agent]
                hit = key in cache
                if not hit:
                    misses += 1
                    cache[key] = f"synthetic answer {key}"  # stands in for the model's response
                emit.json("LLM-12", path, level="INFO", message="llm cache lookup", agent_id=agent,
                          cache_name=CACHE_NAMES[path], cache_backend=CACHE_BACKENDS[path],
                          cache_result="hit" if hit else "miss", cache_key_hash=key)
    return len(agents) * rounds * len(CACHE_QUESTIONS), misses


def llm12(emit, agents, rounds, path="both"):
    if path not in LLM10_PATHS:
        raise ValueError(f"path must be one of {', '.join(LLM10_PATHS)}")
    names = CACHE_AGENTS[:agents]
    summary = {}
    for label in ("waste", "control"):
        if path in ("both", label):
            lookups, misses = _cache_path(emit, label, names, rounds)
            summary[f"{label}_lookups"] = lookups
            summary[f"{label}_misses"] = misses
    return summary


# ---- entry points ---------------------------------------------------------------------------------------

def _scenarios(value):
    if value in (None, "", "all"):
        return SCENARIOS
    key = str(value).upper().replace("_", "-")
    if "-" not in key and len(key) > 3:
        key = f"{key[:3]}-{key[3:]}"
    if key not in SCENARIOS + OPT_IN_SCENARIOS:
        raise ValueError(f"unknown scenario {value!r}; use 'all' or one of {', '.join(SCENARIOS + OPT_IN_SCENARIOS)}")
    return (key,)


def _knob(event, name):
    default, low, high = LIMITS[name]
    value = event.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return max(low, min(high, value))


def run(event=None, write=None, cloudwatch=None, xray=None):
    event = event or {}
    selected = _scenarios(event.get("scenario"))
    emit = Emitter(uuid.uuid4().hex[:12], write, cloudwatch, xray)
    steps = {
        "OBS-11": lambda: obs11(emit, _knob(event, "repeat")),
        "OBS-17": lambda: obs17(emit),
        "OBS-04": lambda: obs04(emit),
        "OBS-06": lambda: obs06(emit, _knob(event, "series")),
        "LLM-10": lambda: llm10(emit, _knob(event, "tool_calls"), event.get("path", "both")),
        "LLM-05": lambda: llm05(emit, event.get("path", "both")),
        "LLM-12": lambda: llm12(emit, _knob(event, "agents"), _knob(event, "rounds"), event.get("path", "both")),
    }
    emitted = {check: steps[check]() for check in selected}
    return {"synthetic": True, "run_id": emit.run_id, "scenarios": list(selected), "emitted": emitted}


def lambda_handler(event, context):
    return run(event if isinstance(event, dict) else {})
