"""INF-04: unused/no-use components kept running (absorbs JOB-08).

Detector semantics version 1.0.0. Two evidence modes, dispatched on source kind:

- static (primary): a CloudFormation/SAM template (YAML or JSON, including CDK-synthesized
  `*.template.json`) is read as text. It is never deployed, resolved or sent to AWS. Flagged:
  Lambda functions, SQS queues and SNS topics that nothing in the template references (no
  trigger, producer, consumer, policy or output), and disabled triggers (EventBridge rules,
  EventBridge Scheduler schedules, event source mappings, SAM events) that are the only thing
  connecting a component that stays deployed.
- telemetry (optional): normalized CloudWatch activity per deployed resource, collected by
  `owner-d-telemetry-analyzer` through the read-only role (GetMetricData/ListMetrics only):
  Lambda `Invocations`, Application Load Balancer `RequestCount` and RDS `DatabaseConnections`
  with no activity over the lookback window.

Both modes prove "no use is visible", not that nothing uses the component, so findings are
candidates for decommissioning and no measurements are emitted. Missing, unparseable or
unsupported input is never reported as clean.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys

from . import miniyaml, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .static import IDENTITY_FIELDS, SCHEMA_VERSION, _require
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-04"
DETECTOR_VERSION = "1.0.0"
STATIC_KIND = "static"
TELEMETRY_KIND = "telemetry"
NOQA = ("INF-04", "INF04")
FORMATS = "CloudFormation/SAM templates (.yaml/.yml/.json/.template, incl. CDK-synthesized *.template.json)"
TELEMETRY_IDENTITY = "no-observed-use"

SETTING_KEYS = ("min_window_days", "max_activity")
# Reference values from "INF-04 > Context settings" in detectors/owner-d/README.md (telemetry mode only;
# the static mode reads no settings). Repository scans pass them; owner_d/aws/registry.py uses the same.
REFERENCE_SETTINGS = {
    "min_window_days": 14,  # README INF-04: shortest window over which "no use" is judged
    "max_activity": 0,  # README INF-04: highest activity (total invocations/requests, peak connections) still "unused"
}

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_user_a3.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_dev_a2.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html",
    "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html",
    "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-metrics.html",
)
RECOMMENDATION = (
    "Confirm with the owning team that nothing outside this template uses the component (callers by name, "
    "other stacks, runbooks, seasonal jobs), for example with the INF-04 telemetry mode, then remove it and its "
    "trigger. If it is kept on purpose (disaster recovery, a paused job, a manual runbook), document why and add "
    "`# noqa: INF-04`."
)
TELEMETRY_RECOMMENDATION = (
    "Confirm that the resource has no clients and no planned use (seasonal or quarterly jobs, disaster-recovery "
    "standby, a launch in preparation), then decommission it (take a final snapshot of a database first) or "
    "stop it. Remove the matching IaC so it is not redeployed."
)
STATIC_LIMITATION = (
    "Static IaC proxy only: INF-04 proves that a CloudFormation/SAM template declares a Lambda function, SQS "
    "queue or SNS topic that nothing in the same template references, or a disabled trigger that is the only "
    "connection to a component kept deployed. Callers outside the template (other stacks, SDK calls by name, "
    "consoles, runbooks, test harnesses) are invisible, so every static finding is a low-confidence candidate. "
    "No inventory or utilisation data is read and no measurements are emitted. Not judged: ECS "
    "services, Kubernetes workloads or Auto Scaling groups scaled to zero (nothing runs), EC2 instances, "
    "databases, state machines, Terraform/HCL, CDK source code (only synthesized templates), Pulumi and "
    "Serverless Framework files."
)
TELEMETRY_LIMITATION = (
    "Telemetry mode: INF-04 judges only the activity metric read for each resource (Lambda Invocations, "
    "Application Load Balancer RequestCount, RDS DatabaseConnections) over the lookback window. Activity outside "
    "the window (seasonal or quarterly jobs), use through another path (for example the RDS Data API or "
    "replication) and planned use are invisible, so findings are candidates to confirm with the owning team; no "
    "measurements are emitted."
)

# ---- telemetry metrics -------------------------------------------------------------------------
# metric -> (statistic, publishes_when_idle, label). CloudWatch publishes Lambda and Application Load
# Balancer metrics only when there is traffic, so "no datapoints" means no use (or a resource that no
# longer exists under that name). RDS publishes DatabaseConnections every minute while the instance runs,
# so datapoints prove the instance was up and an all-zero series means it ran without clients.
METRICS = {
    "invocations": ("Sum", False, "invocations"),
    "request_count": ("Sum", False, "requests"),
    "database_connections": ("Maximum", True, "peak database connections"),
}
REQUIRED_DATA_FIELDS = (
    "resource_id",
    "resource_type",
    "metric",
    "statistic",
    "activity_value",
    "datapoints",
    "observed_days",
    "window_days",
)
EVIDENCE_FIELDS = ("activity_value", "datapoints", "observed_days", "window_days")

# ---- static: resource types --------------------------------------------------------------------
LABELS = {
    "AWS::Lambda::Function": "Lambda function",
    "AWS::Serverless::Function": "SAM function",
    "AWS::SQS::Queue": "SQS queue",
    "AWS::SNS::Topic": "SNS topic",
}
NAME_PROPERTY = {
    "AWS::Lambda::Function": "FunctionName",
    "AWS::Serverless::Function": "FunctionName",
    "AWS::SQS::Queue": "QueueName",
    "AWS::SNS::Topic": "TopicName",
}
FUNCTION_TYPES = {"AWS::Lambda::Function", "AWS::Serverless::Function"}
TRIGGER_LABELS = {
    "AWS::Events::Rule": "EventBridge rule",
    "AWS::Scheduler::Schedule": "EventBridge Scheduler schedule",
    "AWS::Lambda::EventSourceMapping": "Event source mapping",
}
# References that describe a resource rather than use it: (referrer type -> top-level properties, or
# None for every property). Lambda versions and aliases pass on the use of the alias to the function.
PASSIVE = {
    "AWS::Logs::LogGroup": None,
    "AWS::Logs::MetricFilter": None,
    "AWS::CloudWatch::Dashboard": None,
    "AWS::CloudWatch::Alarm": {"Dimensions", "Metrics", "AlarmDescription"},
    "AWS::Lambda::Version": {"FunctionName"},
    "AWS::Lambda::Alias": {"FunctionName", "FunctionVersion"},
    "AWS::Lambda::EventInvokeConfig": {"FunctionName", "Qualifier"},
}
FORWARDING = {"AWS::Lambda::Version", "AWS::Lambda::Alias"}
# Definitions kept outside the template can reference any function in it.
EXTERNAL_DEFINITIONS = {
    "AWS::Serverless::Api": ("DefinitionUri",),
    "AWS::Serverless::HttpApi": ("DefinitionUri",),
    "AWS::Serverless::StateMachine": ("DefinitionUri",),
    "AWS::Serverless::GraphQLApi": ("SchemaUri",),
    "AWS::ApiGateway::RestApi": ("BodyS3Location",),
    "AWS::ApiGatewayV2::Api": ("BodyS3Location",),
    "AWS::StepFunctions::StateMachine": ("DefinitionS3Location",),
    "AWS::AppSync::Resolver": ("CodeS3Location",),
}
# Lambda functions that the CDK framework itself synthesizes (custom-resource providers etc.).
_CDK_FRAMEWORK = re.compile(
    r"Custom::|^Custom\w*CustomResourceProvider|AWS679f53fac002430cb0da5b7982bd2287|"
    r"LogRetentionaae0aa3c5b4d4f87b02d85b201efdd8a|BucketNotificationsHandler050a0587b7544547bf325f094a3db834"
)
# Transforms that SAM/CloudFormation apply without hiding references between resources.
KNOWN_TRANSFORMS = {"AWS::Serverless-2016-10-31", "AWS::LanguageExtensions"}
_REWRITES = re.compile(r"(?:^|[\s\[{,\"'])(?:!Transform|Fn::Transform|Fn::ForEach::?)\b")
_SUB_REF = re.compile(r"\$\{([A-Za-z0-9]+)(?:\.[^}]*)?\}")
EVIDENCE_SPAN = 8
OUTPUTS = "Outputs"
GLOBALS = "Globals"


class Ctx:
    def __init__(self, locator, content, templates):
        self.locator = locator
        self.lines = content.splitlines()
        self.templates = templates


def _get(node, *path):
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.items.get(key)
    return node


def _text(node):
    return node.value if isinstance(node, Scalar) else None


def _set(node):
    if node is None:
        return False
    if isinstance(node, Scalar):
        return node.value is not None and node.value.strip() != ""
    if isinstance(node, (Mapping, Sequence)):
        return bool(node.items)
    return True


def _literal(node, *values):
    text = _text(node)
    return text is not None and text.strip().lower() in values


def _is_template(doc):
    resources = _get(doc, "Resources")
    if not isinstance(resources, Mapping):
        return False
    if _get(doc, "AWSTemplateFormatVersion") is not None or _get(doc, "Transform") is not None:
        return True
    if any(key.startswith("Fn::ForEach") for key in resources.items):
        return True
    return any("::" in (_text(_get(r, "Type")) or "") for r in resources.items.values())


def _transforms(doc):
    node = _get(doc, "Transform")
    if isinstance(node, Sequence):
        return [_text(item) or "?" for item in node.items]
    if node is None:
        return []
    return [_text(node) or "?"]


def parse(locator, content):
    lower = locator.lower()
    is_json = lower.endswith(".json") or (lower.endswith(".template") and content.lstrip().startswith("{"))
    if is_json:
        try:
            data = json.loads(content)
        except ValueError:
            raise ParseError("invalid JSON") from None
        if not (isinstance(data, dict) and isinstance(data.get("Resources"), dict)):
            raise NotEvaluated("no CloudFormation Resources found")
    elif not lower.endswith((".yaml", ".yml", ".template")):
        raise Unsupported(locator)
    try:
        docs = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    templates = [doc for doc in docs if _is_template(doc)]
    if not templates:
        raise NotEvaluated("no CloudFormation Resources found")
    for doc in templates:
        unknown = sorted(set(_transforms(doc)) - KNOWN_TRANSFORMS)
        if unknown:
            raise NotEvaluated(f"template uses the macro transform {unknown[0]}, which can rewrite resources")
    for number, line in enumerate(content.splitlines(), 1):
        if _REWRITES.search(miniyaml.strip_comment(line)):
            raise NotEvaluated(f"Fn::Transform/AWS::Include or Fn::ForEach on line {number} can rewrite resources")
    return Ctx(locator, content, templates)


# ---- static: the reference graph ---------------------------------------------------------------


def _refs(node, ids, out):
    """Logical IDs that `node` may reference: `Ref`/`!Ref X`, `Fn::GetAtt`/`!GetAtt X.Attr`, `${X}` in
    `Fn::Sub`. miniyaml drops tags, so any scalar equal to a logical ID counts; over-counting a reference only
    ever removes findings."""
    if isinstance(node, Scalar):
        value = node.value
        if not value:
            return
        text = value.strip()
        head = text.split(".", 1)[0]
        if text in ids:
            out.add(text)
        elif head in ids:
            out.add(head)
        for match in _SUB_REF.finditer(value):
            if match.group(1) in ids:
                out.add(match.group(1))
    elif isinstance(node, Mapping):
        for child in node.items.values():
            _refs(child, ids, out)
    elif isinstance(node, Sequence):
        for child in node.items:
            _refs(child, ids, out)


class _Template:
    """Resources of one template document and who references whom."""

    def __init__(self, doc):
        resources = doc.get("Resources")
        self.key_lines = resources.key_lines
        self.resources = {k: v for k, v in resources.items.items() if isinstance(v, Mapping)}
        self.types = {k: _text(v.get("Type")) or "" for k, v in self.resources.items()}
        ids = set(self.resources)
        # referrers[target] = [(referrer id, referrer type, top-level property)]
        self.referrers = {k: [] for k in ids}
        for rid, resource in self.resources.items():
            props = resource.get("Properties")
            if not isinstance(props, Mapping):
                found = set()
                _refs(props, ids, found)
                for target in found - {rid}:
                    self.referrers[target].append((rid, self.types[rid], None))
                continue
            for key, value in props.items.items():
                found = set()
                _refs(value, ids, found)
                for target in found - {rid}:
                    self.referrers[target].append((rid, self.types[rid], key))
        for section in (OUTPUTS, GLOBALS):
            found = set()
            _refs(doc.get(section), ids, found)
            for target in found:
                self.referrers[target].append((section, section, None))
        self.disabled = {rid for rid in self.resources if self._disabled_trigger(rid)}
        # Roles used only by disabled triggers, and permissions for disabled triggers, belong to the trigger.
        self.trigger_owned = set()
        for rid, rtype in self.types.items():
            users = [ref for ref, _, _ in self.active_raw(rid)]
            if rtype == "AWS::IAM::Role" and users and all(u in self.disabled for u in users):
                self.trigger_owned.add(rid)
            if rtype == "AWS::Lambda::Permission":
                found = set()
                _refs(self.props(rid), set(self.disabled), found)
                if found:
                    self.trigger_owned.add(rid)
        self.external_definitions = any(
            any(_set(self.props(rid).get(key)) for key in EXTERNAL_DEFINITIONS[rtype])
            for rid, rtype in self.types.items() if rtype in EXTERNAL_DEFINITIONS
        )

    def props(self, rid):
        props = self.resources[rid].get("Properties")
        return props if isinstance(props, Mapping) else Mapping({}, self.resources[rid].line)

    def _disabled_trigger(self, rid):
        rtype, props = self.types[rid], self.props(rid)
        if rtype in ("AWS::Events::Rule", "AWS::Scheduler::Schedule"):
            return _literal(props.get("State"), "disabled")
        if rtype == "AWS::Lambda::EventSourceMapping":
            return _literal(props.get("Enabled"), "false")
        return False

    def active_raw(self, target):
        """Referrers that use `target` (passive references removed; aliases and versions forward their use)."""
        out, seen, stack = [], {target}, [target]
        while stack:
            current = stack.pop()
            for ref in self.referrers.get(current, []):
                rid, rtype, key = ref
                if rtype in PASSIVE and (PASSIVE[rtype] is None or key in PASSIVE[rtype]):
                    if rtype in FORWARDING and rid not in seen:
                        seen.add(rid)
                        stack.append(rid)
                    continue
                out.append(ref)
        return out

    def users(self, target):
        """(active referrers, disabled triggers among the referrers)."""
        active, disabled = [], []
        for ref in self.active_raw(target):
            if ref[0] in self.disabled:
                disabled.append(ref[0])
            elif ref[0] not in self.trigger_owned:
                active.append(ref)
        return active, sorted(set(disabled), key=lambda rid: self.resources[rid].line)


def _sam_events(props):
    """(active events, [(event name, the line that disables it)]) of a SAM function."""
    events = props.get("Events")
    if not isinstance(events, Mapping):
        return (1 if _set(events) else 0), []
    active, disabled = 0, []
    for name, event in events.items.items():
        evprops = _get(event, "Properties")
        enabled, state = _get(evprops, "Enabled"), _get(evprops, "State")
        if _literal(enabled, "false"):
            disabled.append((name, enabled.line))
        elif _literal(state, "disabled"):
            disabled.append((name, state.line))
        else:
            active += 1
    return active, disabled


def _inline_use(rtype, props):
    """(active inline triggers/subscriptions, disabled SAM events)."""
    if rtype == "AWS::Serverless::Function":
        active, disabled = _sam_events(props)
        return active + (1 if _set(props.get("FunctionUrlConfig")) else 0), disabled
    if rtype == "AWS::SNS::Topic":
        return (1 if _set(props.get("Subscription")) else 0), []
    return 0, []


def _span(resource, key_line):
    type_line = resource.key_lines.get("Type")
    return type_line if type_line and key_line <= type_line < key_line + EVIDENCE_SPAN else key_line


def _what_references(rtype):
    if rtype in FUNCTION_TYPES:
        return ("no event source mapping, permission, function URL, rule or schedule target, API or state machine "
                "integration, subscription, custom resource, IAM policy, environment variable or output refers to it")
    if rtype == "AWS::SQS::Queue":
        return ("no producer, consumer, event source mapping, subscription, redrive or dead-letter configuration, "
                "queue policy, IAM policy or output refers to it")
    return "it has no subscriptions, and no publisher, alarm action, topic policy, IAM policy or output refers to it"


def _orphan_hit(tpl, rid, rtype, key_line):
    resource, props = tpl.resources[rid], tpl.props(rid)
    name_node = props.get(NAME_PROPERTY[rtype])
    named = _set(name_node)
    conditional = _set(resource.get("Condition"))
    label = LABELS[rtype]
    if named:
        name = _text(name_node)
        how = (f"; it can still be reached by its {NAME_PROPERTY[rtype]} {name.strip()!r} from outside this template"
               if name and name.strip() else f"; it can still be reached by its {NAME_PROPERTY[rtype]} from outside "
                                             "this template")
    else:
        how = (f"; it has no {NAME_PROPERTY[rtype]}, so a caller outside the template would have to look up its "
               "generated name (for example by logical ID)")
    summary = f"{label} {rid!r} is referenced by nothing in this template: {_what_references(rtype)}{how}."
    if conditional:
        summary = summary[:-1] + " (created only under a Condition)."
    return TextHit(
        line=key_line,
        end_line=_span(resource, key_line),
        block_line=key_line,
        anchor=f"{rid}:unreferenced",
        summary=summary,
        # Callers outside the template (other stacks, SDK or CLI calls by name or logical ID, test harnesses,
        # consoles) are invisible to a static read, so an unreferenced component is only ever a weak signal.
        confidence="low",
    )


def _trigger_hit(tpl, trigger, targets):
    rtype = tpl.types[trigger]
    props = tpl.props(trigger)
    node = props.get("Enabled") if rtype == "AWS::Lambda::EventSourceMapping" else props.get("State")
    what = "Enabled: false" if rtype == "AWS::Lambda::EventSourceMapping" else "State DISABLED"
    labels = [f"{LABELS[tpl.types[t]]} {t!r}" for t in targets]
    names = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    verb = "stays" if len(targets) == 1 else "stay"
    key_line = tpl.key_lines.get(trigger, tpl.resources[trigger].line)
    return TextHit(
        line=node.line,
        block_line=key_line,
        anchor=f"{trigger}:disabled-trigger",
        summary=(f"{TRIGGER_LABELS[rtype]} {trigger!r} declares {what}, and it is the only thing in this template "
                 f"that uses {names}, which {verb} deployed with no active trigger."),
        confidence="low",  # a disabled trigger may be a deliberate, temporary pause
    )


def _sam_disabled_hits(rid, rtype, disabled, key_line):
    for name, line in disabled:
        yield TextHit(
            line=line,
            block_line=key_line,
            anchor=f"{rid}:Events.{name}:disabled",
            summary=(f"{LABELS[rtype]} {rid!r} has only disabled triggers: its event {name!r} is disabled and "
                     "nothing else in this template uses the function, which stays deployed."),
            confidence="low",
        )


def run(ctx):
    hits = []
    for doc in ctx.templates:
        tpl = _Template(doc)
        flagged_triggers = {}
        for rid, resource in tpl.resources.items():
            rtype = tpl.types[rid]
            if rtype not in LABELS:
                continue
            if rtype in FUNCTION_TYPES:
                path = _text(_get(resource, "Metadata", "aws:cdk:path")) or ""
                if _CDK_FRAMEWORK.search(rid) or _CDK_FRAMEWORK.search(path):
                    continue  # framework-managed function, not the application's own code
                if tpl.external_definitions:
                    continue  # an API/state machine definition outside the template may reference it
            props = resource.get("Properties")
            if props is not None and not isinstance(props, Mapping) and _set(props):
                continue  # Fn::If around Properties: not resolved
            key_line = tpl.key_lines.get(rid, resource.line)
            inline_active, inline_disabled = _inline_use(rtype, tpl.props(rid))
            active, disabled = tpl.users(rid)
            if active or inline_active:
                continue
            if not disabled and not inline_disabled:
                if not tpl.active_raw(rid):  # only roles/permissions of disabled triggers: not judged
                    hits.append(_orphan_hit(tpl, rid, rtype, key_line))
                continue
            for trigger in disabled:
                flagged_triggers.setdefault(trigger, []).append(rid)
            hits.extend(_sam_disabled_hits(rid, rtype, inline_disabled, key_line))
        for trigger, targets in flagged_triggers.items():
            hits.append(_trigger_hit(tpl, trigger, targets))
    return hits


# ---- telemetry ---------------------------------------------------------------------------------


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fmt(value):
    if isinstance(value, int):
        return str(value)
    return f"{value:g}"


def _read_settings(context):
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in SETTING_KEYS:
        if not _is_number(context[key]):
            return None, f"context.{key} must be a number"
        settings[key] = context[key]
    if settings["min_window_days"] <= 0:
        return None, "context.min_window_days must be positive"
    if settings["max_activity"] < 0:
        return None, "context.max_activity must not be negative"
    return settings, None


def _telemetry_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    problems = []
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    for field in ("resource_id", "resource_type"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            problems.append(f"{field} must be a nonempty string")
    metric = data.get("metric")
    if "metric" in data and metric not in METRICS:
        problems.append(f"unsupported metric {metric!r}; INF-04 v{DETECTOR_VERSION} supports {sorted(METRICS)}")
    elif metric in METRICS and "statistic" in data and data["statistic"] != METRICS[metric][0]:
        problems.append(f"{metric} must be read with the {METRICS[metric][0]} statistic, not {data['statistic']!r}")
    for field in ("activity_value", "observed_days", "window_days"):
        if field in data and (not _is_number(data[field]) or data[field] < 0):
            problems.append(f"{field} must be a nonnegative number")
    if "datapoints" in data and (not isinstance(data["datapoints"], int) or isinstance(data["datapoints"], bool)
                                 or data["datapoints"] < 0):
        problems.append("datapoints must be a nonnegative integer")
    if _is_number(data.get("window_days")) and data["window_days"] <= 0:
        problems.append("window_days must be positive")
    if data.get("datapoints") == 0 and (data.get("activity_value") != 0 or data.get("observed_days") != 0):
        problems.append("activity_value and observed_days must be 0 when there are no datapoints")
    resource_id = data.get("resource_id")
    if isinstance(resource_id, str) and resource_id.strip() and scope_id != f"resource:{resource_id}":
        problems.append(f"scope id {scope_id!r} does not match resource_id (expected 'resource:{resource_id}')")
    return problems


def _telemetry_finding(source, data, settings):
    _, idle_reported, label = METRICS[data["metric"]]
    window = _fmt(data["window_days"])
    who = f"{data['resource_type']} {data['resource_id']}"
    if data["datapoints"] == 0:
        summary = (f"{who} recorded no {label} in the {window}-day window: CloudWatch has no datapoints for it, "
                   "and this metric is published only when there is traffic, so the component was not used (or no "
                   "longer exists under this name).")
    elif idle_reported:
        summary = (f"{who} ran for {_fmt(data['observed_days'])} of the last {window} days with at most "
                   f"{_fmt(data['activity_value'])} {label} (limit {_fmt(settings['max_activity'])}); it kept running "
                   "without clients.")
    else:
        summary = (f"{who} recorded {_fmt(data['activity_value'])} {label} in {data['datapoints']} datapoints over "
                   f"{window} days (limit {_fmt(settings['max_activity'])}).")
    return {
        "anchor": TELEMETRY_IDENTITY,
        "identity": TELEMETRY_IDENTITY,
        "summary": summary,
        # A running RDS instance publishes zeros, which proves it was up; a missing Lambda/ALB series also
        # matches a deleted or misnamed resource.
        "confidence": "medium" if idle_reported else "low",
        "recommendation": TELEMETRY_RECOMMENDATION,
        "evidence": [{"source_id": source["source_id"], "kind": TELEMETRY_KIND, "locator": source["locator"],
                      "field": field, "value": data[field]} for field in EVIDENCE_FIELDS],
    }


def _evaluate_telemetry(scope_id, telemetry, settings, settings_error):
    """Return (finding items, omitted_reason)."""
    if len(telemetry) > 1:
        return None, f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one"
    if settings is None:
        return None, f"{scope_id}: missing or invalid context settings for telemetry mode: {settings_error}"
    source = telemetry[0]
    if not isinstance(source.get("source_id"), str) or not isinstance(source.get("locator"), str):
        return None, f"{scope_id}: telemetry source needs a string source_id and locator"
    data = source.get("data")
    problems = _telemetry_problems(data, scope_id)
    if problems:
        return None, f"{scope_id}: " + "; ".join(problems)
    if data["window_days"] < settings["min_window_days"]:
        return None, (f"{scope_id}: telemetry window {_fmt(data['window_days'])} days is below the required "
                      f"{_fmt(settings['min_window_days'])} days")
    _, idle_reported, label = METRICS[data["metric"]]
    if idle_reported:
        if data["datapoints"] == 0:
            return None, (f"{scope_id}: no {label} datapoints in the window; the resource was stopped, deleted or not "
                          "found, so its use cannot be judged")
        if data["observed_days"] < settings["min_window_days"]:
            return None, (f"{scope_id}: datapoints cover only {_fmt(data['observed_days'])} days (the resource is newer "
                          f"than the window or was stopped), below the required {_fmt(settings['min_window_days'])}")
    if data["activity_value"] <= settings["max_activity"]:
        return [_telemetry_finding(source, data, settings)], None
    return [], None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
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
    scope = payload["scope"]
    sources = payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")
    sources = [s for s in sources if isinstance(s, dict)]
    settings, settings_error = _read_settings(payload["context"])
    module = sys.modules[__name__]

    evaluated, findings, limitations = [], [], []
    used = set()
    for scope_id in scope:
        scope_sources = [s for s in sources if s.get("scope_id") == scope_id]
        telemetry = [s for s in scope_sources if s.get("kind") == TELEMETRY_KIND]
        statics = [s for s in scope_sources if s.get("kind") == STATIC_KIND]
        if telemetry and statics:
            items, omitted = None, (f"{scope_id}: static and telemetry sources supplied for one scope item; "
                                    "evaluation requires one kind")
        elif telemetry:
            items, omitted = _evaluate_telemetry(scope_id, telemetry, settings, settings_error)
            used.add(TELEMETRY_KIND)
        elif statics:
            items, omitted = textstatic._evaluate_file(scope_id, statics, module)
            used.add(STATIC_KIND)
        else:
            items, omitted = None, (f"{scope_id}: no static template or telemetry source supplied; INF-04 requires a "
                                    "CloudFormation/SAM template (static) or normalized CloudWatch activity (telemetry)")
        if items is None:
            limitations.append(omitted)
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
    if STATIC_KIND in used or not used:
        limitations.append(STATIC_LIMITATION)
    if TELEMETRY_KIND in used:
        limitations.append(TELEMETRY_LIMITATION)

    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"

    result = {field: payload[field] for field in IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result


# ---- telemetry normalizer (owner-d-telemetry-analyzer, source activity_metrics) ----------------------


def normalize_activity_metrics(raw: dict, *, settings: dict | None = None) -> dict:
    """INF-04 telemetry sources from the activity_metrics raw dict (owner_d/aws/activity.py). Pure: no AWS.

    One source per resource whose GetMetricData result is complete. A resource with an incomplete result stays
    in scope without a source, so INF-04 reports it as not evaluated instead of clean."""
    scope, sources, notes = [], [], []
    period = raw.get("period_seconds")
    window = raw.get("window") or {}
    window_days = raw.get("window_days")
    for resource in raw.get("resources", []):
        scope_id = f"resource:{resource['id']}"
        scope.append(scope_id)
        series = raw.get("series", {}).get(resource["id"])
        if series is None or not series.get("complete"):
            notes.append(f"{scope_id}: CloudWatch returned incomplete {resource['metric_name']} data for the window; "
                         "not evaluated")
            continue
        points = sorted((t, v) for t, v in zip(series.get("timestamps", []), series.get("values", []))
                        if _is_number(v))
        values = [v for _, v in points]
        if resource["statistic"] == "Sum":
            activity = sum(values)
        else:
            activity = max(values) if values else 0
        activity = int(activity) if float(activity).is_integer() else round(activity, 4)
        observed = 0
        if points:
            first, last = _parse_time(points[0][0]), _parse_time(points[-1][0])
            observed = round(((last - first).total_seconds() + period) / 86400, 2)
        sources.append({
            "source_id": f"cloudwatch:{resource['id']}",
            "scope_id": scope_id,
            "kind": TELEMETRY_KIND,
            "locator": (f"cloudwatch://{raw.get('region', 'ap-south-1')}/{resource['namespace']}/"
                        f"{resource['metric_name']}?"
                        + "&".join(f"{d['Name']}={d['Value']}" for d in resource["dimensions"])
                        + f"&period={period}&stat={resource['statistic']}"),
            "data": {
                "resource_id": resource["id"],
                "resource_type": resource["resource_type"],
                "metric": resource["metric"],
                "statistic": resource["statistic"],
                "activity_value": activity,
                "datapoints": len(values),
                "observed_days": observed,
                "window_days": window_days,
                "period_seconds": period,
                "window_start": window.get("start"),
                "window_end": window.get("end"),
            },
        })
    return {"scope": scope, "sources": sources, "limitations": notes}


def _parse_time(value):
    """A GetMetricData timestamp (datetime or ISO 8601 string) as an aware datetime."""
    moment = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)
