"""INF-06: one-size-fits-all design pattern across workloads — static IaC heuristic.

Detector semantics version 1.0.1. Reads CloudFormation / SAM templates (YAML or JSON, including
CDK-synthesized `*.template.json`) and Serverless Framework `serverless.yml` files as text — never
deployed, resolved or sent to AWS — and flags a template whose Lambda functions cover all three
clearly different workload kinds, identified by their triggers:

- `api`: synchronous request/response (API Gateway REST/HTTP/WebSocket, function URL, ALB);
- `event`: asynchronous queue, stream or event consumer (SQS, Kinesis, DynamoDB streams, Kafka/MQ,
  S3, SNS, EventBridge patterns, CloudWatch Logs, IoT);
- `schedule`: scheduled job (SAM/Serverless `schedule`, EventBridge rule with a ScheduleExpression,
  EventBridge Scheduler);

and every such function has the identical declared sizing (MemorySize, Timeout, Architectures),
including sizing that comes only from a shared SAM `Globals.Function` or Serverless `provider` block.

It proves "uniform declared sizing across heterogeneous workload kinds", not that any function is
mis-sized: nothing is executed or measured and no measurements are emitted. INF-06 is an OQ-8
judgement row; findings are candidates for reviewer confirmation.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace

from . import inf07, miniyaml, textstatic
from .inf07 import _get, _text
from .miniyaml import Mapping, Scalar, Sequence
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-06"
DETECTOR_VERSION = "1.0.1"
NOQA = ("INF-06", "INF06")
FORMATS = (
    "CloudFormation/SAM templates (.yaml/.yml/.json/.template, incl. CDK-synthesized *.template.json) and "
    "Serverless Framework serverless.yml files"
)

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_user_a3.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_software_a2.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/configuration-memory.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html",
    "https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-specification-template-anatomy-globals.html",
)
RECOMMENDATION = (
    "Size each workload kind for its own profile instead of one shared default: keep latency-sensitive API "
    "handlers at the memory their p99 needs with a short timeout, give queue/stream consumers memory and timeouts "
    "that fit their batch size, and give scheduled jobs the duration they really need (or move long batch work to "
    "a cheaper compute model). Measure per function (e.g. AWS Lambda Power Tuning or Compute Optimizer) before "
    "changing values, and keep shared Globals/provider settings only for what is genuinely common. If the uniform "
    "sizing is deliberate, document why and add `# noqa: INF-06`."
)
LIMITATION = (
    "Static IaC heuristic only: INF-06 proves that one template gives Lambda functions of all three workload kinds "
    "(API request/response, asynchronous event/queue consumer, scheduled job) identical declared MemorySize, "
    "Timeout and Architectures, not that any function is over- or under-sized; no invocation, duration or memory "
    "telemetry is read, so no measurements are emitted. It is an OQ-8 judgement row: findings are candidates for "
    "reviewer confirmation. A template is not judged when a function of a counted kind has sizing given by an "
    "intrinsic other than a Ref to a parameter, when no counted function declares MemorySize or Timeout (pure "
    "platform defaults), or when a function has triggers of more than one kind. Functions split across templates, "
    "stacks or nested applications are not compared. Not evaluated: EC2/ECS/EKS sizing, Multi-AZ and other "
    "service-level choices, Terraform/HCL, CDK source code (only synthesized templates), Pulumi and templates with "
    "a macro transform, Fn::Transform/AWS::Include or Fn::ForEach."
)

KINDS = ("api", "event", "schedule")
MIN_WORKLOADS = 3  # one function of each kind at least
KIND_LABELS = {
    "api": "API request/response",
    "event": "asynchronous event/queue consumer",
    "schedule": "scheduled job",
}
NAMES_SHOWN = 4

LAMBDA_TYPES = {"AWS::Lambda::Function", "AWS::Serverless::Function"}
SAM_EVENTS = {
    "Api": "api", "HttpApi": "api",
    "Schedule": "schedule", "ScheduleV2": "schedule",
    "SQS": "event", "Kinesis": "event", "DynamoDB": "event", "MSK": "event", "MQ": "event",
    "SelfManagedKafka": "event", "DocumentDB": "event", "S3": "event", "SNS": "event", "EventBridgeRule": "event",
    "CloudWatchEvent": "event", "CloudWatchLogs": "event", "IoTRule": "event", "Cognito": "event",
}
SLS_EVENTS = {
    "http": "api", "httpApi": "api", "alb": "api", "websocket": "api",
    "schedule": "schedule",
    "sqs": "event", "stream": "event", "kafka": "event", "msk": "event", "activemq": "event", "rabbitmq": "event",
    "s3": "event", "sns": "event", "eventBridge": "event", "cloudwatchEvent": "event", "cloudwatchLog": "event",
    "iot": "event", "cognitoUserPool": "event",
}
PERMISSION_PRINCIPALS = {
    "apigateway.amazonaws.com": "api", "elasticloadbalancing.amazonaws.com": "api",
    "s3.amazonaws.com": "event", "sns.amazonaws.com": "event", "logs.amazonaws.com": "event",
    "iot.amazonaws.com": "event",
}
# Trigger resources that name a function in a property subtree: type -> [(path, kind)].
TRIGGERS = {
    "AWS::Lambda::EventSourceMapping": [(("FunctionName",), "event")],
    "AWS::Lambda::Url": [(("TargetFunctionArn",), "api")],
    "AWS::ApiGateway::Method": [(("Integration", "Uri"), "api")],
    "AWS::ApiGatewayV2::Integration": [(("IntegrationUri",), "api")],
    "AWS::Scheduler::Schedule": [(("Target", "Arn"), "schedule")],
    "AWS::SNS::Subscription": [(("Endpoint",), "event")],
    "AWS::SNS::Topic": [(("Subscription",), "event")],
    "AWS::S3::Bucket": [(("NotificationConfiguration", "LambdaConfigurations"), "event")],
}
# Platform defaults when a value is not declared: (MemorySize, Timeout, architecture).
CFN_DEFAULTS = ("128", "3", "x86_64")
SLS_DEFAULTS = ("1024", "6", "x86_64")
CFN_KEYS = ("MemorySize", "Timeout", "Architectures")
SLS_KEYS = ("memorySize", "timeout", "architecture")
_SERVERLESS = re.compile(r"(?:^|/)serverless(?:\.[\w-]+)?\.ya?ml$", re.I)
_SUB_REF = re.compile(r"\$\{([A-Za-z0-9]+)(?:\.[A-Za-z]+)?\}")
EVIDENCE_SPAN = 8


class Ctx:
    def __init__(self, locator, content, templates=(), services=()):
        self.locator = locator
        self.lines = content.splitlines()
        self.templates = list(templates)
        self.services = list(services)


def _is_service(doc):
    provider = _get(doc, "provider")
    return isinstance(_get(doc, "functions"), Mapping) and isinstance(provider, Mapping) \
        and (_text(_get(provider, "name")) or "").strip() == "aws"


def _looks_serverless(doc):
    """A top-level Serverless Framework `service` or `provider` key (CloudFormation has neither)."""
    return isinstance(doc, Mapping) and ("service" in doc.items or "provider" in doc.items)


def parse(locator, content):
    """Each YAML document is judged on its own. A `serverless*.yml` file without any Serverless
    `service`/`provider` structure (e.g. a SAM template named serverless.yaml) is read as a template."""
    if _SERVERLESS.search(locator.replace("\\", "/")):
        try:
            docs = miniyaml.load_all(content)
        except miniyaml.YamlError as error:
            raise ParseError(str(error)) from None
        if any(_looks_serverless(doc) for doc in docs):
            services = [doc for doc in docs if _is_service(doc)]
            if not services:
                raise NotEvaluated("no Serverless Framework service with provider.name aws and inline functions")
            return Ctx(locator, content, services=services)
    return Ctx(locator, content, templates=inf07.parse(locator, content).templates)


# -- workloads --------------------------------------------------------------------------------


@dataclass
class Workload:
    name: str
    line: int
    kinds: set = field(default_factory=set)
    sizing: tuple | None = None  # effective tokens (memory, timeout, architecture); None = unknown
    declared: bool = False  # MemorySize or Timeout set on the function or in the shared block
    # Where each effective value comes from: ("own", line), ("shared", line) or ("default", None).
    origins: list = field(default_factory=list)

    def lines(self, origin):
        return [line for kind, line in self.origins if kind == origin]

    @property
    def shared(self):  # at least one value comes from the shared Globals/provider block
        return bool(self.lines("shared"))


def _token(node, params, serverless, is_list):
    """Comparable token for one sizing value: ('value', '512'), ('parameter', name), ('variable', raw);
    None when it is another intrinsic (not resolved). miniyaml drops tags, so `!If [...]` and
    `!FindInMap [...]` read as sequences: only the CloudFormation `Architectures` list may be one."""
    if isinstance(node, Mapping) and set(node.items) == {"Ref"}:
        name = _text(node.items["Ref"])
        return ("parameter", name) if name in params else None
    if isinstance(node, Sequence):
        if not is_list:
            return None
        values = [_text(item) for item in node.items]
        if not values or any(v is None or not v.strip() or "${" in v for v in values):
            return None
        return ("value", ",".join(v.strip().lower() for v in values))
    if isinstance(node, Scalar) and node.value and node.value.strip():
        value = node.value.strip()
        if value in params:
            return ("parameter", value)  # `!Ref Param`: miniyaml drops the tag and keeps the name
        if "${" in value:  # Serverless: identical variable references resolve identically; CFN `!Sub`: unknown
            return ("variable", value) if serverless else None
        if is_list:
            return None  # `Architectures` must be a list; a bare scalar is not a valid literal
        try:
            return ("value", str(int(float(value))))
        except ValueError:
            return ("value", value.lower())
    return None


def _size(own, shared, keys, defaults, params, workload):
    """Fill workload.sizing/declared/origins from the function's own mapping and the shared block.
    An omitted value takes the platform default, so `MemorySize: 128` written out and an omitted
    MemorySize compare equal (effective values)."""
    tokens = []
    serverless = keys is SLS_KEYS
    for index, key in enumerate(keys):
        is_list = key == "Architectures"
        if own.get(key) is not None:
            token = _token(own.get(key), params, serverless, is_list)
            workload.origins.append(("own", own.key_lines.get(key, own.line)))
        elif shared is not None and shared.get(key) is not None:
            token = _token(shared.get(key), params, serverless, is_list)
            workload.origins.append(("shared", shared.key_lines.get(key, shared.line)))
        else:
            tokens.append(("value", defaults[index]))
            workload.origins.append(("default", None))
            continue
        if token is None:
            workload.sizing = None
            return
        tokens.append(token)
        if index < 2:
            workload.declared = True
    workload.sizing = tuple(tokens)


def _referenced(node, aliases):
    """Function logical IDs named anywhere in a property subtree (Ref, GetAtt, Sub, Join parts)."""
    found = set()
    if isinstance(node, Mapping):
        for value in node.items.values():
            found |= _referenced(value, aliases)
    elif isinstance(node, Sequence):
        for item in node.items:
            found |= _referenced(item, aliases)
    elif isinstance(node, Scalar) and node.value:
        value = node.value.strip()
        for candidate in [value, value.split(".", 1)[0], *_SUB_REF.findall(value)]:
            if candidate in aliases:
                found.add(aliases[candidate])
    return found


def _template_workloads(doc):
    params = _get(doc, "Parameters")
    params = params.items if isinstance(params, Mapping) else {}
    globals_fn = _get(doc, "Globals", "Function")
    globals_fn = globals_fn if isinstance(globals_fn, Mapping) else None
    resources = doc.get("Resources")
    workloads, aliases = {}, {}
    for logical_id, resource in resources.items.items():
        if not isinstance(resource, Mapping) or _text(resource.get("Type")) not in LAMBDA_TYPES:
            continue
        sam = _text(resource.get("Type")) == "AWS::Serverless::Function"
        workload = Workload(logical_id, resources.key_lines.get(logical_id, resource.line))
        workloads[logical_id] = workload
        aliases[logical_id] = logical_id
        props = resource.get("Properties")
        if props is None or (isinstance(props, Scalar) and not props.value):
            props = Mapping({}, resource.line)
        if not isinstance(props, Mapping) or inf07._intrinsic(props):
            continue  # Fn::If around Properties: sizing unknown
        _size(props, globals_fn if sam else None, CFN_KEYS, CFN_DEFAULTS, params, workload)
        if not sam:
            continue
        events = props.get("Events")
        if isinstance(events, Mapping):
            for event in events.items.values():
                kind = SAM_EVENTS.get(_text(_get(event, "Type")) or "")
                if kind:
                    workload.kinds.add(kind)
        url = props.get("FunctionUrlConfig")
        if url is None and globals_fn is not None:
            url = globals_fn.get("FunctionUrlConfig")
        if isinstance(url, Mapping):
            workload.kinds.add("api")
    for logical_id, resource in resources.items.items():  # versions and aliases stand for their function
        if isinstance(resource, Mapping) and _text(resource.get("Type")) in ("AWS::Lambda::Alias",
                                                                              "AWS::Lambda::Version"):
            targets = _referenced(_get(resource, "Properties", "FunctionName"), aliases)
            if len(targets) == 1:
                aliases[logical_id] = next(iter(targets))
    for resource in resources.items.values():
        if not isinstance(resource, Mapping):
            continue
        resource_type, props = _text(resource.get("Type")), resource.get("Properties")
        if not isinstance(props, Mapping):
            continue
        links = []
        if resource_type in TRIGGERS:
            links = [(_get(props, *path), kind) for path, kind in TRIGGERS[resource_type]]
        elif resource_type == "AWS::Events::Rule":
            kind = "schedule" if props.get("ScheduleExpression") is not None else "event"
            links = [(props.get("Targets"), kind)]
        elif resource_type == "AWS::Lambda::Permission":
            kind = PERMISSION_PRINCIPALS.get((_text(props.get("Principal")) or "").strip())
            links = [(props.get("FunctionName"), kind)] if kind else []
        elif resource_type == "AWS::ElasticLoadBalancingV2::TargetGroup":
            if (_text(props.get("TargetType")) or "").strip() == "lambda":
                links = [(props.get("Targets"), "api")]
        for node, kind in links:
            for function in _referenced(node, aliases):
                workloads[function].kinds.add(kind)
    return list(workloads.values()), globals_fn


def _service_workloads(doc):
    provider = _get(doc, "provider")
    functions = _get(doc, "functions")
    workloads = []
    for name, function in functions.items.items():
        workload = Workload(name, functions.key_lines.get(name, functions.line))
        workloads.append(workload)
        if not isinstance(function, Mapping):
            continue  # `${file(...)}` or another indirection: sizing unknown
        _size(function, provider, SLS_KEYS, SLS_DEFAULTS, {}, workload)
        url = function.get("url")
        if url is not None and (_text(url) or "-").strip().lower() not in ("false", ""):
            workload.kinds.add("api")
        events = function.get("events")
        for event in events.items if isinstance(events, Sequence) else []:
            if not isinstance(event, Mapping) or len(event.items) != 1:
                continue
            key, value = next(iter(event.items.items()))
            kind = SLS_EVENTS.get(key)
            if key == "eventBridge" and _get(value, "schedule") is not None:
                kind = "schedule"
            if kind:
                workload.kinds.add(kind)
    return workloads, provider


# -- judgement --------------------------------------------------------------------------------


def _show(token, defaulted):
    kind, value = token
    if kind == "parameter":
        return f"parameter {value!r}"
    return f"{value} (platform default)" if defaulted else value


def _names(workloads):
    names = [w.name for w in workloads]
    more = f" +{len(names) - NAMES_SHOWN} more" if len(names) > NAMES_SHOWN else ""
    return ", ".join(names[:NAMES_SHOWN]) + more


def _indent(text):
    return len(text) - len(text.lstrip())


def _evidence(text_lines, cited):
    """(line, end) of the first contiguous run of cited sizing-key lines, with each key's own value
    continuation lines (a block list, a closing JSON bracket), so no other key is ever quoted."""
    cited = sorted(set(cited))
    line = end = cited[0]
    key_indent = _indent(text_lines[line - 1]) if line <= len(text_lines) else 0
    while end + 1 <= len(text_lines) and end + 1 < line + EVIDENCE_SPAN:
        following = text_lines[end]
        if end + 1 in cited:
            key_indent = _indent(following)
        elif not following.strip() or not (_indent(following) > key_indent or (
                _indent(following) == key_indent and following.strip()[0] in "]}")):
            break
        end += 1
    return line, end


def _suppressed(lines, hit, cited, blocks):
    """`# noqa: INF-06` on (or in comment lines directly above) any line that sets the cited
    function's effective sizing, or directly above the shared block / cited function."""
    probes = [replace(hit, line=line, block_line=line) for line in cited]
    probes += [replace(hit, block_line=block) for block in blocks]
    return any(textstatic.is_suppressed(lines, probe, NOQA) for probe in probes)


def judge(workloads, shared_block, labels, lines=()):
    """One TextHit when every single-kind workload of all three kinds has identical effective sizing."""
    counted = sorted((w for w in workloads if len(w.kinds) == 1), key=lambda w: w.line)
    by_kind = {kind: [w for w in counted if kind in w.kinds] for kind in KINDS}
    if len(counted) < MIN_WORKLOADS or not all(by_kind.values()):
        return None
    if any(w.sizing is None for w in counted) or not any(w.declared for w in counted):
        return None  # unknown sizing, or every counted function on pure platform defaults: not judged
    if len({w.sizing for w in counted}) != 1:
        return None
    sizing = counted[0].sizing
    defaulted = [all(w.origins[i][0] == "default" for w in counted) for i in range(len(sizing))]
    values = ", ".join(f"{label} {_show(token, d)}" for label, token, d in zip(labels, sizing, defaulted))
    # Cite one function's effective sizing: its own key lines when it overrides anything (the first
    # counted function that does), otherwise the shared-block lines it inherits. Overridden shared
    # keys are never cited.
    first = next((w for w in counted if w.lines("own")), None)
    if first is None:
        first = next(w for w in counted if w.shared)  # some counted function is declared
        block_name, block_key_line, _ = shared_block
        line, end = _evidence(lines, first.lines("shared"))
        where = (f"from the shared {block_name} block, applied to every function regardless of workload kind"
                 if all(w.shared for w in counted)
                 else f"partly from the shared {block_name} block and partly platform defaults")
        block_line = block_key_line
        blocks = [block_key_line]
    else:
        line, end = _evidence(lines, first.lines("own"))
        where = ("declared function by function" if not any(w.shared for w in counted)
                 else "partly from the shared defaults block and partly per function")
        block_line = first.line
        blocks = [first.line] + ([shared_block[1]] if first.shared else [])
    kinds = "; ".join(f"{KIND_LABELS[kind]}: {_names(by_kind[kind])}" for kind in KINDS)
    hit = TextHit(
        line=line,
        end_line=end,
        block_line=block_line,
        anchor="lambda-functions:uniform-sizing",
        summary=(
            f"{len(counted)} Lambda functions of three different workload kinds share identical sizing "
            f"({values}), {where} ({kinds}). Uniform declared sizing across workloads with different latency, "
            "duration and throughput profiles suggests a one-size-fits-all default rather than per-workload sizing. "
            "This is a candidate for reviewer confirmation; it does not show that any function is mis-sized."
        ),
        confidence="low",
    )
    cited = [line for _, line in first.origins if line is not None]
    return None if _suppressed(lines, hit, cited, blocks) else hit


def run(ctx):
    """One judgement per YAML document: functions are never compared across documents."""
    hits = []
    for doc in ctx.templates:
        workloads, globals_fn = _template_workloads(doc)
        globals_node = _get(doc, "Globals")
        shared = None
        if globals_fn is not None:
            shared = ("SAM Globals.Function", globals_node.key_lines.get("Function", globals_fn.line), globals_fn)
        hit = judge(workloads, shared, CFN_KEYS, ctx.lines)
        if hit:
            hits.append(hit)
    for doc in ctx.services:
        workloads, provider = _service_workloads(doc)
        shared = ("Serverless provider", doc.key_lines.get("provider", provider.line), provider)
        hit = judge(workloads, shared, SLS_KEYS, ctx.lines)
        if hit:
            hits.append(hit)
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
