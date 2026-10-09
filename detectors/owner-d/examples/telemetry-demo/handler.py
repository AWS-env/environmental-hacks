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
* LLM-10  X-Ray subsegments of an agent loop calling the same tool with identical arguments until it hits
          its iteration cap, vs a bounded loop that stops once it has an answer. No LLM is called

The event selects what to emit: ``{"scenario": "all" | "OBS-11" | "OBS-17" | "OBS-04" | "OBS-06" | "LLM-10"}``.
Optional knobs, each clamped: ``repeat`` (OBS-11 lines), ``series`` (OBS-06 request ids), ``tool_calls``
(LLM-10 iterations). Subsegments are sent straight to the Lambda X-Ray daemon over UDP, so the function needs
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
SCENARIOS = ("OBS-11", "OBS-17", "OBS-04", "OBS-06", "LLM-10")

# OBS-06 series bound: request_id values come from a fixed pool, endpoint from a fixed list.
REQUEST_ID_POOL = 40
ENDPOINTS = ("/checkout", "/search")
MAX_METRIC_SERIES = REQUEST_ID_POOL + len(ENDPOINTS)

LIMITS = {"repeat": (20, 1, 50), "series": (REQUEST_ID_POOL, 1, REQUEST_ID_POOL), "tool_calls": (12, 1, 25)}
TOOL_CALL_SECONDS = 0.02


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

def _segment_id():
    return uuid.uuid4().hex[:16]


def _tool_call(tool, args):
    start = _now()
    time.sleep(TOOL_CALL_SECONDS)
    digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]
    return {"id": _segment_id(), "name": f"tool:{tool}", "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-10", "tool_name": tool, "args_hash": digest},
            "metadata": {"tool": {"args": args}}}


def _agent_loop(path, calls, stop_reason):
    start = _now()
    children = [_tool_call(tool, args) for tool, args in calls]
    return {"id": _segment_id(), "name": "agent_loop", "start_time": start, "end_time": _now(),
            "annotations": {"synthetic": True, "check": "LLM-10", "path": path, "iterations": len(children),
                            "stop_reason": stop_reason},
            "subsegments": children}


def llm10(emit, tool_calls):
    same = ("get_order_status", {"order_id": "ord-demo-0003"})
    waste = _agent_loop("waste", [same] * tool_calls, "max_iterations")
    control = _agent_loop("control", [same, ("draft_reply", {"status": "shipped"})], "answer_ready")
    sent = [emit.subsegment(waste), emit.subsegment(control)]
    for doc, path in ((waste, "waste"), (control, "control")):
        emit.json("LLM-10", path, level="INFO", message="agent loop finished", iterations=len(doc["subsegments"]),
                  stop_reason=doc["annotations"]["stop_reason"])
    return {"waste_tool_calls": tool_calls, "control_tool_calls": 2, "xray": sent[0]}


# ---- entry points ---------------------------------------------------------------------------------------

def _scenarios(value):
    if value in (None, "", "all"):
        return SCENARIOS
    key = str(value).upper().replace("_", "-")
    if "-" not in key and len(key) > 3:
        key = f"{key[:3]}-{key[3:]}"
    if key not in SCENARIOS:
        raise ValueError(f"unknown scenario {value!r}; use 'all' or one of {', '.join(SCENARIOS)}")
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
        "LLM-10": lambda: llm10(emit, _knob(event, "tool_calls")),
    }
    emitted = {check: steps[check]() for check in selected}
    return {"synthetic": True, "run_id": emit.run_id, "scenarios": list(selected), "emitted": emitted}


def lambda_handler(event, context):
    return run(event if isinstance(event, dict) else {})
