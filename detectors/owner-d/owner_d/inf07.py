"""INF-07: less efficient instance/processor family — static IaC proxy.

Detector semantics version 1.0.0. Reads CloudFormation / SAM templates (YAML or JSON, including
CDK-synthesized `*.template.json`) as text — never deployed, resolved or sent to AWS — and flags
compute that a template declares on an older or less efficient family when a more efficient
equivalent exists:

- EC2 instance types (instances, launch templates, launch configurations, Auto Scaling group
  overrides, EKS managed node groups, Batch compute environments), RDS DB instance classes,
  ElastiCache node types and OpenSearch instance types from an older generation of their family
  (t1/t2, m1-m4, c1/c3/c4, r3/r4), or from a fifth-generation x86 family that has an AWS
  Graviton equivalent (t3, m5, c5, r5 and their a/d/n variants);
- Lambda functions on x86_64 (`Architectures` omitted, the default, or declared `x86_64`)
  whose runtime also supports arm64 (Graviton2).

The family tables below are deliberately small and explicit; families outside them are not
judged. The proxy proves "declared on an older/less efficient family", not runtime efficiency,
utilisation or savings, so no measurements are emitted.
"""

from __future__ import annotations

import json
import re
import sys

from . import miniyaml, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-07"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-07", "INF07")
FORMATS = "CloudFormation/SAM templates (.yaml/.yml/.json/.template, incl. CDK-synthesized *.template.json)"

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_hardware_a3.html",
    "https://docs.aws.amazon.com/ec2/latest/instancetypes/instance-types.html",
    "https://aws.amazon.com/ec2/previous-generation/",
    "https://aws.amazon.com/ec2/graviton/",
    "https://docs.aws.amazon.com/lambda/latest/dg/foundation-arch.html",
    "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.html",
    "https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheNodes.SupportedTypes.html",
    "https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html",
)
RECOMMENDATION = "Move the workload to a current, more efficient instance generation or to AWS Graviton (arm64)."
REC_OLDER = (
    "Move to the newer generation named in the finding (or its Graviton variant once the AMI, engine and "
    "software support arm64), check that it is offered in the project's Region and size it from observed "
    "utilisation (e.g. Compute Optimizer). If the older family is required (licensing, AMI or driver "
    "constraints), document that and add `# noqa: INF-07`."
)
REC_GRAVITON = (
    "Evaluate the AWS Graviton equivalent named in the finding: confirm that the AMI, container images and "
    "native dependencies have arm64 builds (managed engines need a supported engine version), check Region "
    "availability and benchmark before switching. If the workload needs x86-only binaries, document that and "
    "add `# noqa: INF-07`."
)
REC_LAMBDA = (
    "Set Architectures: [arm64] after confirming that native dependencies, layers and container base images "
    "have arm64 builds (build the package for the arm64 platform) and testing the function. Keep x86_64 only "
    "for x86-only binaries or Lambda@Edge, and then add `# noqa: INF-07`."
)
LIMITATION = (
    "Static IaC proxy only: INF-07 proves that a CloudFormation/SAM template declares compute on an older or "
    "x86 family for which a newer or AWS Graviton equivalent exists, not that the workload runs inefficiently, "
    "is compatible with the successor or that the successor is offered in the project's Region; no inventory, "
    "utilisation or Compute Optimizer data is read (the client read-only role is blocked on OQ-7), so no "
    "measurements are emitted. Only the families in the INF-07 table are judged (not 6th/7th-generation x86, "
    "GPU/accelerated/storage-optimised or other families). Intrinsic values other than a Ref to a parameter "
    "with a literal Default are not resolved and are not flagged. Not evaluated: attribute-based "
    "InstanceRequirements, Spot Fleet/EC2 Fleet, EMR, SageMaker, Batch `optimal`, Lambda@Edge association, "
    "Kubernetes nodeSelector/affinity, Terraform/HCL, CDK source code (only synthesized templates), Pulumi and "
    "Serverless Framework files."
)

# Older generation of a family: (current x86 successor, Graviton successor). A fifth-generation x86 family
# with a Graviton equivalent: (None, Graviton successor). Sources: EC2 instance types and previous-generation
# pages, AWS Graviton page, RDS DB instance classes, ElastiCache supported node types and OpenSearch
# supported instance types (see REFERENCES).
EC2_FAMILIES = {
    "t1": ("t3", "t4g"), "t2": ("t3", "t4g"),
    "m1": ("m7i", "m7g"), "m3": ("m7i", "m7g"), "m4": ("m7i", "m7g"), "m2": ("r7i", "r7g"),
    "c1": ("c7i", "c7g"), "c3": ("c7i", "c7g"), "c4": ("c7i", "c7g"),
    "r3": ("r7i", "r7g"), "r4": ("r7i", "r7g"),
    "t3": (None, "t4g"), "t3a": (None, "t4g"),
    "m5": (None, "m7g"), "m5a": (None, "m7g"), "m5d": (None, "m7gd"), "m5ad": (None, "m7gd"),
    "c5": (None, "c7g"), "c5a": (None, "c7g"), "c5d": (None, "c7gd"), "c5n": (None, "c7gn"),
    "r5": (None, "r7g"), "r5a": (None, "r7g"), "r5d": (None, "r7gd"), "r5ad": (None, "r7gd"),
}
RDS_FAMILIES = {
    "t2": ("db.t3", "db.t4g"), "m3": ("db.m6i", "db.m7g"), "m4": ("db.m6i", "db.m7g"),
    "r3": ("db.r6i", "db.r7g"), "r4": ("db.r6i", "db.r7g"),
    "t3": (None, "db.t4g"), "m5": (None, "db.m7g"), "r5": (None, "db.r7g"),
}
CACHE_FAMILIES = {
    "t1": ("cache.t3", "cache.t4g"), "t2": ("cache.t3", "cache.t4g"),
    "m3": ("cache.m5", "cache.m7g"), "m4": ("cache.m5", "cache.m7g"), "c1": ("cache.m5", "cache.m7g"),
    "r3": ("cache.r5", "cache.r7g"), "r4": ("cache.r5", "cache.r7g"),
    "t3": (None, "cache.t4g"), "m5": (None, "cache.m7g"), "r5": (None, "cache.r7g"),
}
SEARCH_FAMILIES = {
    "t2": ("t3", None), "m3": ("m5", "m7g"), "m4": ("m5", "m7g"), "c4": ("c5", "c7g"),
    "r3": ("r5", "r7g"), "r4": ("r5", "r7g"),
    "m5": (None, "m7g"), "c5": (None, "c7g"), "r5": (None, "r7g"),
}
_FAMILY = r"(?P<family>[a-z]+[0-9]+[a-z]*)"
_SIZE = r"\.[a-z0-9-]+"
PATTERNS = {
    "ec2": re.compile(rf"^{_FAMILY}{_SIZE}$"),
    "batch": re.compile(rf"^{_FAMILY}(?:{_SIZE})?$"),  # Batch also accepts a bare family such as `m4`
    "rds": re.compile(rf"^db\.{_FAMILY}{_SIZE}$"),
    "cache": re.compile(rf"^cache\.{_FAMILY}{_SIZE}$"),
    "search": re.compile(rf"^{_FAMILY}{_SIZE}\.(?:search|elasticsearch)$"),
}
TABLES = {"ec2": EC2_FAMILIES, "batch": EC2_FAMILIES, "rds": RDS_FAMILIES, "cache": CACHE_FAMILIES,
          "search": SEARCH_FAMILIES}
# RDS engines with Graviton DB instance classes (Oracle, SQL Server and Db2 have none).
GRAVITON_ENGINES = {"mysql", "mariadb", "postgres", "aurora", "aurora-mysql", "aurora-postgresql"}

# Resource type: (label, [(property path, "scalar" | "list" | "overrides", pattern key)]).
COMPUTE = {
    "AWS::EC2::Instance": ("EC2 instance", [(("InstanceType",), "scalar", "ec2")]),
    "AWS::EC2::LaunchTemplate": ("Launch template", [(("LaunchTemplateData", "InstanceType"), "scalar", "ec2")]),
    "AWS::AutoScaling::LaunchConfiguration": ("Launch configuration", [(("InstanceType",), "scalar", "ec2")]),
    "AWS::AutoScaling::AutoScalingGroup": (
        "Auto Scaling group", [(("MixedInstancesPolicy", "LaunchTemplate", "Overrides"), "overrides", "ec2")]),
    "AWS::EKS::Nodegroup": ("EKS node group", [(("InstanceTypes",), "list", "ec2")]),
    "AWS::Batch::ComputeEnvironment": (
        "Batch compute environment", [(("ComputeResources", "InstanceTypes"), "list", "batch")]),
    "AWS::RDS::DBInstance": ("RDS DB instance", [(("DBInstanceClass",), "scalar", "rds")]),
    "AWS::ElastiCache::CacheCluster": ("ElastiCache cluster", [(("CacheNodeType",), "scalar", "cache")]),
    "AWS::ElastiCache::ReplicationGroup": ("ElastiCache replication group", [(("CacheNodeType",), "scalar", "cache")]),
    "AWS::OpenSearchService::Domain": ("OpenSearch domain", [
        (("ClusterConfig", "InstanceType"), "scalar", "search"),
        (("ClusterConfig", "DedicatedMasterType"), "scalar", "search"),
    ]),
    "AWS::Elasticsearch::Domain": ("Elasticsearch domain", [
        (("ElasticsearchClusterConfig", "InstanceType"), "scalar", "search"),
        (("ElasticsearchClusterConfig", "DedicatedMasterType"), "scalar", "search"),
    ]),
}
LAMBDA_TYPES = {"AWS::Lambda::Function": "Lambda function", "AWS::Serverless::Function": "SAM function"}
# Lambda runtimes without an arm64 build (all deprecated); moving needs a runtime upgrade first.
NO_ARM64_RUNTIMES = {
    "nodejs", "nodejs4.3", "nodejs4.3-edge", "nodejs6.10", "nodejs8.10", "nodejs10.x", "python2.7", "python3.6",
    "python3.7", "java8", "go1.x", "dotnetcore1.0", "dotnetcore2.0", "dotnetcore2.1", "ruby2.5", "provided",
}
# Zip packages for these runtimes are usually architecture-neutral (native extensions aside).
PORTABLE_RUNTIMES = ("python", "nodejs", "ruby", "java", "dotnet")
# Lambda functions that the CDK framework itself synthesizes (custom-resource providers etc.).
_CDK_FRAMEWORK = re.compile(
    r"Custom::|^Custom\w*CustomResourceProvider|AWS679f53fac002430cb0da5b7982bd2287|"
    r"LogRetentionaae0aa3c5b4d4f87b02d85b201efdd8a|BucketNotificationsHandler050a0587b7544547bf325f094a3db834"
)
# Transforms that SAM/CloudFormation apply without rewriting the compute resources checked here.
KNOWN_TRANSFORMS = {"AWS::Serverless-2016-10-31", "AWS::LanguageExtensions"}
_REWRITES = re.compile(r"(?:^|[\s\[{,\"'])(?:!Transform|Fn::Transform|Fn::ForEach::?)\b")
EVIDENCE_SPAN = 8
_RANK = {"low": 0, "medium": 1, "high": 2}


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
    return True


def _intrinsic(node):
    """`{"Fn::If": ...}` / `{"Ref": ...}`, or a sequence where a mapping is expected (miniyaml drops tags,
    so `!If [Cond, {...}, {...}]` reads as a sequence)."""
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
        if _REWRITES.search(miniyaml.strip_comment(line)):
            raise NotEvaluated(f"Fn::Transform/AWS::Include or Fn::ForEach on line {number} can rewrite resources")
    return Ctx(locator, content, templates)


def _resolve(node, params):
    """(value, line, parameter name or None) for a literal or a Ref to a parameter with a literal
    Default; None for anything else (other intrinsics are not resolved)."""
    name = None
    if isinstance(node, Mapping) and set(node.items) == {"Ref"}:
        name = _text(node.items["Ref"])
    elif isinstance(node, Scalar) and node.value and node.value.strip() in params:
        name = node.value.strip()  # `!Ref Param`: miniyaml drops the tag and keeps the parameter name
    elif isinstance(node, Scalar) and node.value and node.value.strip():
        return node.value.strip(), node.line, None
    if name is None:
        return None
    default = _get(params.get(name), "Default")
    if isinstance(default, Scalar) and default.value and default.value.strip():
        return default.value.strip(), default.line, name
    return None


# -- instance families ------------------------------------------------------------------------


def _classify(value, kind, graviton_ok):
    """(tier, family, x86 successor, Graviton successor) or None when the value is not judged."""
    match = PATTERNS[kind].match(value.lower())
    if not match:
        return None
    family = match.group("family")
    entry = TABLES[kind].get(family)
    if entry is None:
        return None
    x86, arm = entry
    if not graviton_ok:
        arm = None
    if x86 is None and arm is None:
        return None
    return ("older" if x86 else "graviton"), family, x86, arm


def _phrase(value, verdict, param):
    tier, family, x86, arm = verdict
    via = f" (Default of parameter {param!r}, which a deployment can override)" if param else ""
    if tier == "older":
        newer = f"{x86}, or {arm} on AWS Graviton" if arm else x86
        return f"{value}{via}, an older-generation {family} family (newer generation: {newer})"
    return f"{value}{via}, an x86 {family} family with an AWS Graviton (Arm) equivalent ({arm})"


def _confidence(verdict, param):
    tier, family = verdict[0], verdict[1]
    if param or tier == "graviton" or family.startswith("t"):
        return "low"  # parameter defaults are overridable; t2/t3 are often chosen for free tier or bursting
    return "medium"


def _graviton_ok(resource_type, props):
    if resource_type == "AWS::RDS::DBInstance":
        return _literal(props.get("Engine"), *GRAVITON_ENGINES)
    if resource_type == "AWS::Elasticsearch::Domain":
        return False  # legacy Elasticsearch versions predate Graviton support
    if resource_type == "AWS::OpenSearchService::Domain":
        version = props.get("EngineVersion")
        return version is None or (_text(version) or "").startswith("OpenSearch_")
    return True


def _value_nodes(props, path, shape):
    node = _get(props, *path)
    if shape == "scalar":
        return [node] if node is not None else []
    if not isinstance(node, Sequence):
        return []
    if shape == "list":
        return node.items
    return [_get(item, "InstanceType") for item in node.items if isinstance(item, Mapping)]


def _compute_hits(logical_id, resource_type, key_line, props, params):
    label, properties = COMPUTE[resource_type]
    graviton_ok = _graviton_ok(resource_type, props)
    for path, shape, kind in properties:
        if path[-1] == "DedicatedMasterType" and _literal(_get(props, path[0], "DedicatedMasterEnabled"), "false"):
            continue
        flagged = []
        for node in _value_nodes(props, path, shape):
            resolved = _resolve(node, params)
            if resolved is None:
                continue
            value, line, param = resolved
            verdict = _classify(value, kind, graviton_ok)
            if verdict:
                flagged.append((value, line, param, verdict))
        if not flagged:
            continue
        prop = ".".join(path)
        phrases = [_phrase(value, verdict, param) for value, _, param, verdict in flagged]
        confidence = max((_confidence(v, p) for _, _, p, v in flagged), key=_RANK.get)
        older = any(v[0] == "older" for *_, v in flagged)
        yield TextHit(
            line=flagged[0][1],
            block_line=key_line,
            anchor=f"{logical_id}:{prop}",
            summary=f"{label} {logical_id!r} declares {prop} {'; '.join(phrases)}.",
            confidence=confidence,
            recommendation=REC_OLDER if older else REC_GRAVITON,
        )


# -- Lambda architecture ----------------------------------------------------------------------


def _lambda_hits(logical_id, resource, resource_type, key_line, props, globals_fn):
    path = _text(_get(resource, "Metadata", "aws:cdk:path")) or ""
    if _CDK_FRAMEWORK.search(logical_id) or _CDK_FRAMEWORK.search(path):
        return  # framework-managed function, not the application's own code
    serverless = resource_type == "AWS::Serverless::Function"

    def setting(key):
        node = props.get(key)
        if node is None and serverless:
            return _get(globals_fn, key), True
        return node, False

    arch, from_globals = setting("Architectures")
    explicit = _set(arch)
    if explicit:
        if not isinstance(arch, Sequence) or not all(isinstance(item, Scalar) for item in arch.items):
            return  # intrinsic (`!If`, `!Ref`): architecture unknown
        if [(_text(item) or "").strip().lower() for item in arch.items] != ["x86_64"]:
            return
    package, _ = setting("PackageType")
    image = _literal(package, "image")
    runtime_node, _ = setting("Runtime")
    runtime = (_text(runtime_node) or "").strip().lower()
    if not image and runtime in NO_ARM64_RUNTIMES:
        return  # no arm64 build of this runtime
    portable = not image and runtime.startswith(PORTABLE_RUNTIMES)
    if image:
        what = "its container image can be rebuilt for arm64"
    elif runtime.startswith(PORTABLE_RUNTIMES) or runtime.startswith("provided."):
        what = f"the {runtime} runtime also supports arm64"
    else:
        what = "arm64 is available"
    label = LAMBDA_TYPES[resource_type]
    if explicit:
        owner = globals_fn if from_globals else props
        line = owner.key_lines.get("Architectures", owner.line)
        last = arch.items[-1].line if arch.items else line
        where = " in SAM Globals" if from_globals else ""
        yield TextHit(
            line=line,
            end_line=last if line <= last < line + EVIDENCE_SPAN else line,
            block_line=key_line,
            anchor=f"{logical_id}:Architectures",
            summary=(f"{label} {logical_id!r} declares Architectures [x86_64]{where}, although {what} "
                     f"(AWS Graviton2)."),
            confidence="low",  # an explicit x86_64 may be a deliberate compatibility choice
            recommendation=REC_LAMBDA,
        )
        return
    type_line = resource.key_lines.get("Type")
    end = type_line if type_line and key_line <= type_line < key_line + EVIDENCE_SPAN else key_line
    yield TextHit(
        line=key_line,
        end_line=end,
        block_line=key_line,
        anchor=f"{logical_id}:Architectures",
        summary=(f"{label} {logical_id!r} declares no Architectures, so it runs on the x86_64 default, although "
                 f"{what} (AWS Graviton2)."),
        # Zip packages of interpreted/bytecode runtimes usually move unchanged; images and custom runtimes
        # need an arm64 build, and an unknown runtime cannot be judged.
        confidence="medium" if portable else "low",
        recommendation=REC_LAMBDA,
    )


def run(ctx):
    hits = []
    for doc in ctx.templates:
        params = _get(doc, "Parameters")
        params = params.items if isinstance(params, Mapping) else {}
        globals_fn = _get(doc, "Globals", "Function")
        globals_fn = globals_fn if isinstance(globals_fn, Mapping) else Mapping({}, 0)
        resources = doc.get("Resources")
        for logical_id, resource in resources.items.items():
            if not isinstance(resource, Mapping):
                continue
            resource_type = _text(resource.get("Type"))
            if resource_type not in COMPUTE and resource_type not in LAMBDA_TYPES:
                continue
            key_line = resources.key_lines.get(logical_id, resource.line)
            props = resource.get("Properties")
            if props is None or (isinstance(props, Scalar) and not _set(props)):
                props = Mapping({}, resource.line)
            elif not isinstance(props, Mapping) or _intrinsic(props):
                continue  # Fn::If around Properties: not resolved
            if resource_type in COMPUTE:
                hits.extend(_compute_hits(logical_id, resource_type, key_line, props, params))
            else:
                hits.extend(_lambda_hits(logical_id, resource, resource_type, key_line, props, globals_fn))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
