"""INF-10: storage without lifecycle/retention management — static IaC proxy.

Detector semantics version 1.0.0. Reads CloudFormation / SAM templates (YAML or JSON, including
CDK-synthesized `*.template.json`) as text — never deployed, resolved or sent to AWS — and flags
storage resources whose template declares no lifecycle or retention:

- `AWS::S3::Bucket` with no enabled lifecycle rule that expires or transitions objects;
- a versioned `AWS::S3::Bucket` whose lifecycle never expires/transitions noncurrent versions;
- `AWS::Logs::LogGroup` without `RetentionInDays` (never expire);
- `AWS::ECR::Repository` without a `LifecyclePolicy`.

The proxy proves "no retention/lifecycle declared in this template", not that data grows, is old
or is unused, so no measurements are emitted. Values given by intrinsic functions (`!Ref`,
`Fn::If`, ...) count as declared.
"""

from __future__ import annotations

import json
import re
import sys

from . import miniyaml, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-10"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-10", "INF10")
FORMATS = "CloudFormation/SAM templates (.yaml/.yml/.json/.template, incl. CDK-synthesized *.template.json)"

REFERENCES = (
    "https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html",
    "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-s3-bucket-lifecycleconfiguration.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html",
    "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-logs-loggroup.html",
    "https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html",
    "https://docs.aws.amazon.com/config/latest/developerguide/s3-lifecycle-policy-check.html",
    "https://docs.aws.amazon.com/config/latest/developerguide/cw-loggroup-retention-period-check.html",
)
RECOMMENDATION = "Declare a lifecycle or retention policy so data that is no longer needed expires or moves to colder storage."
REC_S3 = (
    "Add a LifecycleConfiguration rule that expires (ExpirationInDays) or transitions (Transitions) objects once "
    "they are no longer needed. If the bucket must keep its data (compliance, website assets, release artifacts), "
    "document that and add `# noqa: INF-10`."
)
REC_NONCURRENT = (
    "Add NoncurrentVersionExpiration (optionally NewerNoncurrentVersions) or NoncurrentVersionTransitions to an "
    "enabled lifecycle rule, plus ExpiredObjectDeleteMarker, so old object versions do not accumulate."
)
REC_LOGS = (
    "Set RetentionInDays on the log group to the period the logs are actually needed (CloudWatch Logs keeps them "
    "forever otherwise), and export to S3 if they must be archived longer."
)
REC_ECR = (
    "Add a LifecyclePolicy (LifecyclePolicyText) that expires untagged images and keeps only the last N tagged "
    "images."
)
LIMITATION = (
    "Static IaC proxy only: INF-10 proves that a CloudFormation/SAM template declares no lifecycle or retention "
    "for an S3 bucket, CloudWatch Logs log group or ECR repository, not that data grows, is old or is unused; no "
    "inventory or telemetry is read (the AWS Config / Resource Explorer half is blocked on OQ-7), so no "
    "measurements are emitted. Lifecycle applied outside the template (console/CLI, put-lifecycle-policy, other "
    "stacks) is not visible. Values set by intrinsic functions, and Fn::If around a lifecycle configuration, rule "
    "or property, are treated as declared. Not evaluated: implicit Lambda/SAM function log groups and "
    "Custom::LogRetention, DynamoDB TTL, Kinesis retention, snapshots and S3 directory buckets, Terraform/HCL, "
    "CDK source code (only synthesized templates), Pulumi and Serverless Framework files, and whether a "
    "declared retention period is appropriate (see OBS-07)."
)

BUCKET, LOG_GROUP, ECR_REPOSITORY = "AWS::S3::Bucket", "AWS::Logs::LogGroup", "AWS::ECR::Repository"
CURRENT_ACTIONS = ("ExpirationInDays", "ExpirationDate", "Transitions", "Transition")
NONCURRENT_ACTIONS = (
    "NoncurrentVersionExpiration", "NoncurrentVersionExpirationInDays",
    "NoncurrentVersionTransitions", "NoncurrentVersionTransition",
)
CLEANUP_ACTIONS = ("AbortIncompleteMultipartUpload", "ExpiredObjectDeleteMarker")
# Transforms that SAM/CloudFormation apply without rewriting the storage resources checked here.
KNOWN_TRANSFORMS = {"AWS::Serverless-2016-10-31", "AWS::LanguageExtensions"}
_REWRITES = re.compile(r"(?:^|[\s\[{,\"'])(?:!Transform|Fn::Transform|Fn::ForEach::?)\b")
EVIDENCE_SPAN = 8


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
    """True for a present, non-null value (intrinsics such as {"Ref": ...} count as set)."""
    if node is None:
        return False
    if isinstance(node, Scalar):
        return node.value is not None and node.value.strip() != ""
    return True


def _intrinsic(node):
    """An intrinsic where a mapping is expected: `{"Fn::If": ...}` / `{"Ref": ...}`, or a sequence
    (miniyaml drops YAML tags, so `!If [Cond, {...}, !Ref AWS::NoValue]` reads as a sequence)."""
    if isinstance(node, Sequence):
        return True
    if isinstance(node, Mapping) and len(node.items) == 1:
        key = next(iter(node.items))
        return key == "Ref" or key.startswith("Fn::")
    return False


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
        match = _REWRITES.search(miniyaml.strip_comment(line))
        if match:
            raise NotEvaluated(f"Fn::Transform/AWS::Include or Fn::ForEach on line {number} can rewrite resources")
    return Ctx(locator, content, templates)


def _resources(ctx):
    for doc in ctx.templates:
        resources = doc.get("Resources")
        for logical_id, resource in resources.items.items():
            if isinstance(resource, Mapping):
                yield logical_id, resource, resources.key_lines.get(logical_id, resource.line)


def _header_hit(logical_id, resource, key_line, **kwargs):
    """Evidence from the logical-ID line through the `Type` line (when it is close below)."""
    type_line = resource.key_lines.get("Type")
    end = type_line if type_line and key_line <= type_line < key_line + EVIDENCE_SPAN else key_line
    return TextHit(line=key_line, end_line=end, block_line=key_line, **kwargs)


# -- Amazon S3 --------------------------------------------------------------------------------


def _object_lock(props):
    return _literal(props.get("ObjectLockEnabled"), "true") or _literal(
        _get(props, "ObjectLockConfiguration", "ObjectLockEnabled"), "enabled")


def _bucket_hits(logical_id, resource, key_line, props):
    if _object_lock(props):  # compliance (WORM) retention is declared; keeping data is intended
        return
    versioning = props.get("VersioningConfiguration")
    status = _get(versioning, "Status")
    versioned = _literal(status, "enabled")
    # Status is Enabled or Suspended (which still holds earlier versions); any other value is an intrinsic
    # (miniyaml drops tags, so `!Ref VersioningStatus` reads as a plain scalar) and may be either.
    maybe_versioned = _set(status) or _intrinsic(versioning)

    lifecycle = props.get("LifecycleConfiguration")
    if not _set(lifecycle):
        lifecycle = None
    elif not isinstance(lifecycle, Mapping) or _intrinsic(lifecycle):
        return  # conditional/computed lifecycle: treated as declared
    rules = _get(lifecycle, "Rules")
    if _set(rules) and not isinstance(rules, Sequence):
        return  # e.g. Rules: {"Fn::If": ...}
    items = rules.items if isinstance(rules, Sequence) else []
    current = noncurrent = False
    enabled = cleanup = 0
    for rule in items:
        if not isinstance(rule, Mapping) or _intrinsic(rule):
            current = noncurrent = True  # Fn::If around one rule: treated as declared
            enabled += 1
            continue
        if _literal(rule.get("Status"), "disabled"):
            continue
        enabled += 1
        current = current or any(_set(rule.get(k)) for k in CURRENT_ACTIONS)
        noncurrent = noncurrent or any(_set(rule.get(k)) for k in NONCURRENT_ACTIONS)
        cleanup += any(_set(rule.get(k)) for k in CLEANUP_ACTIONS)

    if not current and not (noncurrent and maybe_versioned):
        if lifecycle is None:
            detail = "declares no LifecycleConfiguration"
        elif not items:
            detail = "declares a LifecycleConfiguration without rules"
        elif not enabled:
            detail = f"has {len(items)} lifecycle rule(s), all Disabled"
        elif noncurrent:
            detail = "only has lifecycle rules for noncurrent versions, but versioning is not enabled"
        elif cleanup:
            detail = ("only has lifecycle rules that abort incomplete multipart uploads or remove expired delete "
                      "markers")
        else:
            detail = "has no enabled lifecycle rule that expires or transitions objects"
        versions = " (versioning is enabled, so overwritten versions are kept too)" if versioned else ""
        yield _header_hit(
            logical_id, resource, key_line,
            anchor=f"{logical_id}:s3-lifecycle",
            summary=(f"S3 bucket {logical_id!r} {detail}, so no object ever expires or moves to a colder storage "
                     f"class and stored data is kept indefinitely{versions}."),
            # Many buckets hold durable data on purpose (assets, artifacts, compliance archives).
            confidence="low",
            recommendation=REC_S3,
        )
    elif versioned and not noncurrent:
        line = props.key_lines.get("VersioningConfiguration", key_line)
        status_line = versioning.key_lines.get("Status", line) if isinstance(versioning, Mapping) else line
        yield TextHit(
            line=line,
            end_line=status_line if line <= status_line < line + EVIDENCE_SPAN else line,
            block_line=key_line,
            anchor=f"{logical_id}:s3-noncurrent-versions",
            summary=(f"Versioned S3 bucket {logical_id!r} has lifecycle rules for current objects but none that "
                     f"expires or transitions noncurrent versions, so every overwritten or deleted object version "
                     f"is kept indefinitely."),
            confidence="low",
            recommendation=REC_NONCURRENT,
        )


# -- CloudWatch Logs and ECR ------------------------------------------------------------------


def _log_group_hits(logical_id, resource, key_line, props):
    if _set(props.get("RetentionInDays")) or _literal(props.get("LogGroupClass"), "delivery"):
        return  # DELIVERY log groups have a fixed 1-day retention
    yield _header_hit(
        logical_id, resource, key_line,
        anchor=f"{logical_id}:log-retention",
        summary=(f"CloudWatch Logs log group {logical_id!r} sets no RetentionInDays, so its log events never "
                 f"expire and are stored indefinitely."),
        confidence="medium",
        recommendation=REC_LOGS,
    )


def _ecr_hits(logical_id, resource, key_line, props):
    policy = props.get("LifecyclePolicy")
    if _set(policy) and (not isinstance(policy, Mapping) or _intrinsic(policy)
                         or _set(policy.get("LifecyclePolicyText"))):
        return
    detail = "has a LifecyclePolicy without LifecyclePolicyText" if _set(policy) else "declares no LifecyclePolicy"
    yield _header_hit(
        logical_id, resource, key_line,
        anchor=f"{logical_id}:ecr-lifecycle",
        summary=(f"ECR repository {logical_id!r} {detail}, so every pushed image (including untagged ones) is "
                 f"kept indefinitely."),
        confidence="medium",
        recommendation=REC_ECR,
    )


HANDLERS = {BUCKET: _bucket_hits, LOG_GROUP: _log_group_hits, ECR_REPOSITORY: _ecr_hits}


def run(ctx):
    hits = []
    for logical_id, resource, key_line in _resources(ctx):
        handler = HANDLERS.get(_text(resource.get("Type")))
        if handler is None:
            continue
        props = resource.get("Properties")
        if props is None or (isinstance(props, Scalar) and not _set(props)):
            props = Mapping({}, resource.line)
        elif not isinstance(props, Mapping) or _intrinsic(props):
            continue  # Fn::If around Properties: treated as declared
        hits.extend(handler(logical_id, resource, key_line, props))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
