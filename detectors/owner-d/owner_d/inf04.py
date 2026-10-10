"""INF-04: unused/no-use components kept running — static IaC proxy and idle-function telemetry.

Detector semantics version 1.0.0. One module, two modes, chosen by the payload's scope:

Static IaC proxy (`file:<path>` scope, repository scans). CloudFormation/SAM templates (YAML or JSON,
including CDK-synthesized `*.template.json`) and Terraform (`.tf`) are read as text — never deployed,
resolved or sent to AWS — and four billable components are flagged when the template declares them but
nothing uses them:

- an Elastic IP with no instance/network-interface association that nothing references;
- a NAT gateway that nothing references (no route sends traffic to it);
- an Application/Network/Gateway Load Balancer that nothing references (no listener can serve traffic);
- an EBS volume that nothing references (no attachment).

"References" is deliberately broad, so false positives stay low: any Ref, Fn::GetAtt, Fn::Sub `${...}`,
DependsOn or Output naming the resource anywhere else in the template (CloudFormation), or any
`<type>.<name>` expression in any `.tf` file of the same directory (Terraform), counts as use. The proxy
proves "declared and unreferenced in this template/module", not that the component is idle at runtime:
something outside the template (another stack, a script, the console) may still use it.

Telemetry mode (`resource:lambda/<function>` scope, owner-d-telemetry-analyzer). Normalized CloudWatch
AWS/Lambda Invocations (Sum) per function over a bounded window: a function with no invocations for at
least `min_idle_days` (default 14) is flagged. Lambda publishes Invocations only when a function runs, so
a function without any datapoint in the window is reported as not evaluated (never invoked, or does not
exist under that name), never as clean or idle. `normalize_invocation_metrics` is the pure normalizer the
AWS route (owner_d/aws/registry.py) calls; it never calls AWS and drops account IDs and ARNs.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import inf07, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .obs10 import HclError, parse_hcl
from .static import (  # noqa: F401  (EvaluationError is re-exported for the CLI)
    IDENTITY_FIELDS,
    SCHEMA_VERSION,
    EvaluationError,
    _require,
    _unique_identities,
    fingerprint,
)
from .static import SUPPORTED_KIND as STATIC_KIND
from .textstatic import NotEvaluated, ParseError, TextHit, Unsupported

CHECK_ID = "INF-04"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-04", "INF04")
FORMATS = (
    "CloudFormation/SAM templates (.yaml/.yml/.json/.template, incl. CDK-synthesized *.template.json) and "
    "Terraform (.tf)"
)
TELEMETRY_KIND = "telemetry"
TELEMETRY_SCOPE = "resource:"

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_user_a3.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_hardware_a2.html",
    "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html",
    "https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html",
    "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html",
    "https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html",
)
TELEMETRY_REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_user_a3.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html",
)
RECOMMENDATION = "Remove the unused component, or wire it to the resource that is meant to use it."
STATIC_LIMITATION = (
    "Static IaC proxy only: INF-04 proves that a CloudFormation/SAM template or Terraform module declares an "
    "Elastic IP, NAT gateway, ELBv2 load balancer or EBS volume that nothing in that template (or the .tf files "
    "of that directory) references, not that it is idle at runtime: another stack, a script, a lookup by name "
    "or tag, or the console may still use it. Any Ref, GetAtt, Sub, DependsOn or Output (CloudFormation), or "
    "`<type>.<name>` expression (Terraform), counts as use, so references that exist but serve nothing are not "
    "judged. No inventory or usage data is read, so no measurements are emitted. Not evaluated: other resource "
    "types (Auto Scaling groups, ECS services, provisioned concurrency, target groups, endpoints), templates "
    "with macro transforms, Fn::Transform/AWS::Include or Fn::ForEach, Terraform JSON (.tf.json, read only for "
    "references), CDK source code (only synthesized templates), Pulumi and Serverless Framework files."
)


# -- static IaC proxy -------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    label: str
    associations: tuple  # properties/attributes that associate the component with its user
    unused: str  # what "nothing references it" means for this component
    recommendation: str
    name_property: str | None = None  # explicit physical name another stack could look up


REC_EIP = (
    "Release the Elastic IP (public IPv4 addresses are billed while allocated, attached or not), or associate "
    "it in this template. If another stack or a script attaches it, export it as an Output or add "
    "`# noqa: INF-04` with the reason."
)
REC_NAT = (
    "Delete the NAT gateway (billed per hour even without traffic) or add the private route tables' "
    "0.0.0.0/0 routes to it. If routes in another stack use it, export it as an Output or add "
    "`# noqa: INF-04` with the reason."
)
REC_LB = (
    "Delete the load balancer (billed per hour without listeners or targets) or add the listener that "
    "serves it. If listeners in another stack use it, export it as an Output or add `# noqa: INF-04` with "
    "the reason."
)
REC_VOLUME = (
    "Snapshot the volume if its data is needed, then delete it (EBS bills provisioned storage whether "
    "attached or not), or attach it in this template. If something outside the template attaches it, add "
    "`# noqa: INF-04` with the reason."
)

CFN_RULES = {
    "AWS::EC2::EIP": Rule("Elastic IP", ("InstanceId", "TransferAddress"),
                          "it has no InstanceId, and no association, NAT gateway, load balancer or Output "
                          "references it", REC_EIP),
    "AWS::EC2::NatGateway": Rule("NAT gateway", (), "no route, Output or other resource references it, so no "
                                 "subnet sends traffic through it", REC_NAT),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": Rule(
        "Load balancer", (), "no listener, Output or other resource references it, so it cannot serve traffic",
        REC_LB, name_property="Name"),
    "AWS::EC2::Volume": Rule("EBS volume", (), "no VolumeAttachment, Output or other resource references it",
                             REC_VOLUME),
}
TF_RULES = {
    "aws_eip": Rule("Elastic IP", ("instance", "network_interface"),
                    "it has no instance or network_interface, and no association, NAT gateway, load balancer or "
                    "output references it", REC_EIP),
    "aws_nat_gateway": Rule("NAT gateway", (), "no route, output or other expression references it, so no subnet "
                            "sends traffic through it", REC_NAT),
    "aws_lb": Rule("Load balancer", (), "no listener, output or other expression references it, so it cannot "
                   "serve traffic", REC_LB),
    "aws_alb": Rule("Load balancer", (), "no listener, output or other expression references it, so it cannot "
                    "serve traffic", REC_LB),
    "aws_ebs_volume": Rule("EBS volume", (), "no aws_volume_attachment, output or other expression references it",
                           REC_VOLUME),
}
_SUB = re.compile(r"\$\{([A-Za-z0-9]+)(?:\.[^}]*)?\}")
EVIDENCE_SPAN = 8


class CfnCtx:
    kind = "cloudformation"

    def __init__(self, locator, content, templates):
        self.locator = locator
        self.lines = content.splitlines()
        self.templates = templates


class TfCtx:
    def __init__(self, locator, content, blocks, kind="terraform"):
        self.locator = locator
        self.lines = content.splitlines()
        self.blocks = blocks
        self.kind = kind  # "terraform" or "terraform-json" (read only for references)

    @property
    def directory(self):
        return str(PurePosixPath(self.locator.replace("\\", "/")).parent)


def parse(locator, content):
    """Context for one file. Raises Unsupported, ParseError or NotEvaluated (textstatic semantics)."""
    lower = locator.lower()
    if lower.endswith(".tf.json"):
        try:
            json.loads(content)
        except ValueError:
            raise ParseError("invalid JSON") from None
        return TfCtx(locator, content, [], kind="terraform-json")
    if lower.endswith(".tf"):
        try:
            return TfCtx(locator, content, parse_hcl(content))
        except HclError as error:
            raise ParseError(str(error)) from None
    ctx = inf07.parse(locator, content)  # CloudFormation: same template reader and exclusions as INF-07
    return CfnCtx(locator, content, ctx.templates)


def _names(node):
    """Logical IDs a node may name: Ref/DependsOn values, GetAtt `X.Attr`/`[X, Attr]` and Sub `${X}`.
    miniyaml drops tags, so `!Ref X` reads as the scalar `X` and `!GetAtt X.Arn` as `X.Arn`."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, Scalar):
            value = (item.value or "").strip()
            if value:
                yield value
                yield value.split(".", 1)[0]
                yield from _SUB.findall(value)
        elif isinstance(item, Sequence):
            stack.extend(item.items)
        elif isinstance(item, Mapping):
            stack.extend(item.items.values())


def _referenced(doc, resources):
    """Logical IDs that something other than the resource itself names."""
    names = set()
    for key, node in doc.items.items():
        if key != "Resources":
            names.update(_names(node))  # Outputs, Conditions, Globals, Metadata, Rules, ...
    for logical_id, resource in resources.items.items():
        names.update(name for name in _names(resource) if name != logical_id)
    return names


def _cfn_hits(ctx):
    for doc in ctx.templates:
        resources = doc.get("Resources")
        referenced = _referenced(doc, resources)
        for logical_id, resource in resources.items.items():
            if not isinstance(resource, Mapping):
                continue
            rule = CFN_RULES.get(inf07._text(resource.get("Type")))
            if rule is None or logical_id in referenced:
                continue
            props = resource.get("Properties")
            if props is None or (isinstance(props, Scalar) and not inf07._set(props)):
                props = Mapping({}, resource.line)
            elif not isinstance(props, Mapping) or inf07._intrinsic(props):
                continue  # Fn::If around Properties: not resolved
            if any(inf07._set(props.get(key)) for key in rule.associations):
                continue
            key_line = resources.key_lines.get(logical_id, resource.line)
            type_line = resource.key_lines.get("Type")
            end = type_line if type_line and key_line <= type_line < key_line + EVIDENCE_SPAN else key_line
            conditional = resource.get("Condition") is not None
            named = rule.name_property is not None and inf07._set(props.get(rule.name_property))
            notes = []
            if conditional:
                notes.append("it is created only when its Condition holds")
            if named:
                notes.append(f"its explicit {rule.name_property} lets another stack look it up by name")
            yield TextHit(
                line=key_line,
                end_line=end,
                block_line=key_line,
                anchor=f"{logical_id}:unreferenced",
                summary=(f"{rule.label} {logical_id!r} is declared but nothing in this template uses it: "
                         f"{rule.unused}" + (f" ({'; '.join(notes)})" if notes else "") + "."),
                confidence="low" if notes else "medium",
                recommendation=rule.recommendation,
            )


def _tf_reference(rtype, name):
    return re.compile(rf"(?<![\w.-]){re.escape(rtype)}\.{re.escape(name)}(?![\w-])")


def _tf_hits(ctx, group):
    for block in ctx.blocks:
        if block.type != "resource" or len(block.labels) != 2:
            continue
        rtype, name = block.labels
        rule = TF_RULES.get(rtype)
        if rule is None:
            continue
        count = block.attrs.get("count")
        if count and count[0].strip() == "0":
            continue  # never created
        if any(attr in block.attrs for attr in rule.associations):
            continue
        pattern = _tf_reference(rtype, name)
        end = block.end_line or block.line
        used = False
        for other in group:
            for number, text in enumerate(other.lines, 1):
                if other is ctx and block.line <= number <= end:
                    continue
                if pattern.search(text):
                    used = True
                    break
            if used:
                break
        if used:
            continue
        repeated = "count" in block.attrs or "for_each" in block.attrs
        note = " (count/for_each may create no instances)" if repeated else ""
        yield TextHit(
            line=block.line,
            block_line=block.line,
            anchor=f"{rtype}.{name}:unreferenced",
            summary=(f"{rule.label} {rtype}.{name} is declared but nothing in the .tf files of its directory "
                     f"uses it: {rule.unused}{note}."),
            confidence="low" if repeated else "medium",
            recommendation=rule.recommendation,
        )


def run(ctx, group=None):
    """TextHits for one parsed file. Terraform needs the parsed .tf files of its directory (`group`)."""
    if ctx.kind == "cloudformation":
        return list(_cfn_hits(ctx))
    if ctx.kind == "terraform":
        return list(_tf_hits(ctx, group if group is not None else [ctx]))
    return []


def _parse_scope(scope_id, sources):
    """(source, ctx, None) or (None, locator-or-None, omitted reason)."""
    statics = [s for s in sources if isinstance(s, dict) and s.get("kind") == STATIC_KIND]
    if not statics:
        return None, None, f"{scope_id}: no static source supplied for this scope item"
    if len(statics) > 1:
        return None, None, f"{scope_id}: multiple static sources supplied; evaluation requires exactly one"
    source = statics[0]
    locator, content = source.get("locator"), source.get("content")
    if not isinstance(locator, str) or not isinstance(content, str):
        return None, None, f"{scope_id}: static source needs a string locator and content"
    try:
        return source, parse(locator, content), None
    except Unsupported:
        reason = f"unsupported file type; {CHECK_ID} v{DETECTOR_VERSION} supports {FORMATS} only"
    except ParseError as error:
        reason = f"could not be parsed ({error}); not evaluated"
    except NotEvaluated as error:
        reason = f"not evaluated ({error})"
    except Exception as error:  # a parser bug must not become a clean claim for this file
        reason = f"could not be parsed ({type(error).__name__}); not evaluated"
    return None, locator, f"{scope_id}: {reason}"


def _directory(locator):
    return str(PurePosixPath(locator.replace("\\", "/")).parent)


def _items(ctx, hits, source):
    items = []
    for hit in hits:
        if textstatic.is_suppressed(ctx.lines, hit, NOQA):
            continue
        end = min(hit.end_line or hit.line, hit.line + textstatic.EVIDENCE_MAX_LINES - 1)
        items.append({
            "anchor": hit.anchor,
            "line": hit.line,
            "summary": hit.summary,
            "confidence": hit.confidence,
            "recommendation": hit.recommendation or RECOMMENDATION,
            "evidence": [{
                "source_id": source["source_id"],
                "kind": STATIC_KIND,
                "locator": source["locator"],
                "line_start": hit.line,
                "value": "\n".join(ctx.lines[hit.line - 1:end]),
            }],
        })
    items.sort(key=lambda item: item["line"])
    return _unique_identities(items)


def _evaluate_static(payload, scope, sources):
    parsed, limitations, broken = {}, [], {}
    for scope_id in scope:
        source, ctx, omitted = _parse_scope(
            scope_id, [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id])
        if omitted:
            limitations.append(omitted)
            if isinstance(ctx, str) and ctx.lower().endswith((".tf", ".tf.json")) and "could not be parsed" in omitted:
                broken.setdefault(_directory(ctx), ctx)
        else:
            parsed[scope_id] = (source, ctx)

    groups = {}
    for _, ctx in parsed.values():
        if ctx.kind.startswith("terraform"):
            groups.setdefault(ctx.directory, []).append(ctx)

    evaluated, findings = [], []
    for scope_id, (source, ctx) in parsed.items():
        if ctx.kind == "terraform-json":
            limitations.append(f"{scope_id}: Terraform JSON is read only for references to resources in the .tf "
                               "files of its directory; its own resources are not judged")
            continue
        if ctx.kind == "terraform" and ctx.directory in broken:
            limitations.append(f"{scope_id}: not evaluated ({broken[ctx.directory]} in the same Terraform "
                               "directory could not be parsed, so references from it are unknown)")
            continue
        try:
            hits = run(ctx, groups.get(getattr(ctx, "directory", None)))
            items = _items(ctx, hits, source)
        except Exception as error:  # a detector bug must not become a clean claim for this file
            limitations.append(f"{scope_id}: check failed ({type(error).__name__}); not evaluated")
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
    limitations.append(STATIC_LIMITATION)
    return evaluated, findings, limitations


# -- telemetry mode: idle Lambda functions ----------------------------------------------------

IDENTITY = "no-invocations"
SUPPORTED_METRIC = "invocations"
DEFAULT_MIN_IDLE_DAYS = 14
REQUIRED_DATA_FIELDS = (
    "resource_id", "resource_type", "metric", "window_days", "idle_days", "datapoint_count",
    "total_invocations", "idle_period_datapoints",
)
EVIDENCE_FIELDS = ("total_invocations", "idle_days", "window_days", "datapoint_count", "last_invoked_period")
TELEMETRY_RECOMMENDATION = (
    "Confirm with the owning team that nothing still needs the function (rare schedules, disaster-recovery or "
    "manual runs) and that it was not already deleted, then remove it together with its triggers, provisioned "
    "concurrency and log group, or document why it must stay deployed and leave it out of the analyzer's "
    "resources."
)
TELEMETRY_LIMITATION = (
    "Telemetry mode reads only CloudWatch AWS/Lambda Invocations (Sum) for the functions listed in the "
    "analyzer event: the client read-only role has no lambda:ListFunctions, and ListMetrics returns only "
    "metrics with data in the past two weeks, so idle functions cannot be discovered. Zero invocations do not "
    "prove that a function is unneeded (rare schedules, disaster recovery) or that it still exists (deleted "
    "functions stop publishing too); functions without any datapoint in the window are not evaluated. Other "
    "component types are not read and no measurements are emitted."
)
_LAMBDA_ARN = re.compile(r"^arn:aws[a-z-]*:lambda:[a-z0-9-]+:\d{12}:function:([A-Za-z0-9_-]{1,64})(?::[^:]+)?$")


def function_name(value):
    """A Lambda function name from a name or a function ARN (the account ID is dropped); None otherwise."""
    if not isinstance(value, str):
        return None
    match = _LAMBDA_ARN.match(value.strip())
    name = match.group(1) if match else value.strip()
    return name if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) else None


def _parse_time(value):
    if isinstance(value, dt.datetime):
        moment = value
    elif isinstance(value, str):
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"expected an ISO 8601 timestamp, got {value!r}")
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def _iso(moment):
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _compact(value):
    return int(value) if float(value).is_integer() else round(value, 4)


def summarize_invocations(series, window_start, window_end, period):
    """Invocation totals and the trailing idle span for one function; None without datapoints."""
    start, end = _parse_time(window_start), _parse_time(window_end)
    points = sorted((_parse_time(t), v) for t, v in zip(series.get("timestamps") or [], series.get("values") or [])
                    if _number(v) and v >= 0)
    if not points:
        return None
    window_days = round((end - start).total_seconds() / 86400, 2)
    active = [t for t, v in points if v > 0]
    if active:
        last = max(active)
        idle_days = round(max((end - last).total_seconds() - period, 0) / 86400, 2)
        idle_points = sum(1 for t, _ in points if t > last)
    else:
        last, idle_days, idle_points = None, window_days, len(points)
    return {
        "window_days": window_days,
        "idle_days": min(idle_days, window_days),
        "datapoint_count": len(points),
        "total_invocations": _compact(sum(v for _, v in points)),
        "idle_period_datapoints": idle_points,
        "last_invoked_period": _iso(last) if last else None,
    }


def _locator(region, name, period):
    return f"cloudwatch://{region}/AWS/Lambda/Invocations?FunctionName={name}&period={period}&stat=Sum"


def normalize_invocation_metrics(raw: dict, *, settings: dict | None = None) -> dict:
    """INF-04 telemetry sources from the `invocation_metrics` raw dict (owner_d/aws/metrics.py), one per
    Lambda function with datapoints. Pure: no AWS calls. Function ARNs are reduced to names, so no account
    ID or ARN reaches a scope ID, locator or data field. Functions without usable data stay in scope with no
    source, so INF-04 reports them as not evaluated instead of clean."""
    scope, sources, notes = [], [], []
    period = raw["period_seconds"]
    window = raw.get("window") or {}
    region = raw.get("region") or "ap-south-1"
    for resource in raw.get("resources") or []:
        if resource.get("type") != "lambda":
            continue
        dims = {d.get("Name"): d.get("Value") for d in resource.get("dimensions") or []}
        name = function_name(dims.get("FunctionName"))
        if name is None:
            notes.append("a Lambda resource without a valid function name was skipped")
            continue
        scope_id = f"resource:lambda/{name}"
        if scope_id in scope:
            continue
        scope.append(scope_id)
        series = (raw.get("series") or {}).get(resource.get("id"))
        if series is None or not series.get("complete", False):
            notes.append(f"{scope_id}: CloudWatch returned incomplete Invocations data for the window; not evaluated")
            continue
        if not window.get("start") or not window.get("end"):
            notes.append(f"{scope_id}: the collection window is unknown; not evaluated")
            continue
        summary = summarize_invocations(series, window["start"], window["end"], period)
        if summary is None:
            notes.append(f"{scope_id}: no AWS/Lambda Invocations datapoints in {window['start']}..{window['end']}; "
                         "Lambda publishes Invocations only when a function runs, so it was either not invoked "
                         "in the window or does not exist under this name (the read-only role cannot list "
                         "functions to tell them apart); not evaluated")
            continue
        sources.append({
            "source_id": f"cloudwatch:lambda/{name}:invocations",
            "scope_id": scope_id,
            "kind": TELEMETRY_KIND,
            "locator": _locator(region, name, period),
            "data": {
                "resource_id": f"lambda/{name}",
                "resource_type": "aws_lambda_function",
                "metric": SUPPORTED_METRIC,
                "statistic": "Sum",
                **summary,
                "period_seconds": period,
                "window_start": window["start"],
                "window_end": window["end"],
            },
        })
    return {"scope": scope, "sources": sources, "limitations": notes}


def _read_min_idle_days(context):
    value = context.get("min_idle_days", DEFAULT_MIN_IDLE_DAYS)
    if not _number(value) or value <= 0:
        return None, "context.min_idle_days must be a positive number"
    return value, None


def _telemetry_problems(data, scope_id):
    if not isinstance(data, dict):
        return ["telemetry data must be an object"]
    problems = []
    missing = [name for name in REQUIRED_DATA_FIELDS if name not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    for name in ("resource_id", "resource_type"):
        if name in data and (not isinstance(data[name], str) or not data[name].strip()):
            problems.append(f"{name} must be a nonempty string")
    if "metric" in data and data["metric"] != SUPPORTED_METRIC:
        problems.append(f"unsupported metric {data['metric']!r}; v1 supports {SUPPORTED_METRIC!r}")
    for name in ("window_days", "idle_days", "datapoint_count", "total_invocations", "idle_period_datapoints"):
        if name in data and (not _number(data[name]) or data[name] < 0):
            problems.append(f"{name} must be a nonnegative number")
    if not problems:
        if data["window_days"] <= 0:
            problems.append("window_days must be positive")
        if data["idle_days"] > data["window_days"]:
            problems.append("idle_days must not exceed window_days")
        if data["datapoint_count"] < 1:
            problems.append("datapoint_count must be at least 1 (functions without datapoints are not evaluated)")
        if data["idle_period_datapoints"] > data["datapoint_count"]:
            problems.append("idle_period_datapoints must not exceed datapoint_count")
        last = data.get("last_invoked_period")
        if data["total_invocations"] > 0 and not isinstance(last, str):
            problems.append("last_invoked_period is required when total_invocations is positive")
    resource_id = data.get("resource_id")
    if isinstance(resource_id, str) and resource_id.strip() and scope_id != f"resource:{resource_id}":
        problems.append(f"scope id {scope_id!r} does not match resource_id (expected 'resource:{resource_id}')")
    return problems


def _fmt(value):
    return str(value) if isinstance(value, int) else f"{value:g}"


def _telemetry_finding(repository_id, scope_id, source, data):
    name = data["resource_id"].split("/", 1)[-1]
    if data["total_invocations"] == 0:
        summary = (f"Lambda function {name} reported 0 invocations (AWS/Lambda Invocations Sum) across the whole "
                   f"{_fmt(data['window_days'])}-day window ({data['datapoint_count']} datapoints); it is deployed "
                   "but not used")
    else:
        summary = (f"Lambda function {name} had no invocations in the last {_fmt(data['idle_days'])} days of the "
                   f"{_fmt(data['window_days'])}-day window; it was last invoked in the period starting "
                   f"{data['last_invoked_period']} ({_fmt(data['total_invocations'])} invocations before that)")
    # Zero-valued datapoints after the last invocation show the metric is still published (the function
    # exists); with no datapoints at all after it, the function may also have been deleted.
    confidence = "medium" if data["idle_period_datapoints"] > 0 else "low"
    fields = [f for f in EVIDENCE_FIELDS if data.get(f) is not None]
    return {
        "fingerprint": fingerprint(repository_id, CHECK_ID, scope_id, IDENTITY),
        "scope_id": scope_id,
        "identity": IDENTITY,
        "summary": summary,
        "confidence": confidence,
        "recommendation": TELEMETRY_RECOMMENDATION,
        "references": list(TELEMETRY_REFERENCES),
        "evidence": [{"source_id": source["source_id"], "kind": TELEMETRY_KIND, "locator": source["locator"],
                      "field": f, "value": data[f]} for f in fields],
    }


def _evaluate_telemetry(payload, scope, sources):
    min_idle, reason = _read_min_idle_days(payload["context"])
    if min_idle is None:
        return [], [], [f"Missing or invalid context settings: {reason}", TELEMETRY_LIMITATION]
    evaluated, findings, limitations = [], [], []
    for scope_id in scope:
        telemetry = [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id
                     and s.get("kind") == TELEMETRY_KIND]
        if not telemetry:
            limitations.append(f"{scope_id}: no telemetry source supplied; INF-04 telemetry mode requires "
                               "normalized Invocations data")
            continue
        if len(telemetry) > 1:
            limitations.append(f"{scope_id}: multiple telemetry sources supplied; evaluation requires exactly one")
            continue
        source = telemetry[0]
        data = source.get("data")
        problems = _telemetry_problems(data, scope_id)
        if problems or not isinstance(source.get("source_id"), str) or not isinstance(source.get("locator"), str):
            limitations.append(f"{scope_id}: " + ("; ".join(problems) or "telemetry source needs a source_id and "
                                                  "locator"))
            continue
        if data["window_days"] < min_idle:
            limitations.append(f"{scope_id}: telemetry window {_fmt(data['window_days'])} days is below the required "
                               f"{_fmt(min_idle)} days (min_idle_days); not evaluated")
            continue
        evaluated.append(scope_id)
        if data["idle_days"] >= min_idle:
            findings.append(_telemetry_finding(payload["repository_id"], scope_id, source, data))
    limitations.append(TELEMETRY_LIMITATION)
    return evaluated, findings, limitations


# -- contract entry point ---------------------------------------------------------------------


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload. `resource:` scope
    selects telemetry mode; any other scope (`file:<path>`) is the static IaC proxy."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements "
        f"{DETECTOR_VERSION}",
    )
    for name in IDENTITY_FIELDS:
        _require(name in payload, f"input is missing required field {name}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(all(isinstance(s, str) for s in scope), "scope items must be strings")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    if all(scope_id.startswith(TELEMETRY_SCOPE) for scope_id in scope):
        evaluated, findings, limitations = _evaluate_telemetry(payload, scope, sources)
    else:
        evaluated, findings, limitations = _evaluate_static(payload, scope, sources)
    evaluated = [scope_id for scope_id in scope if scope_id in evaluated]
    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result = {name: payload[name] for name in IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result
