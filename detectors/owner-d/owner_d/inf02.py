"""INF-02: static replicas without demand-based autoscaling — static manifest scan.

Detector semantics version 1.0.0. Reads deployment manifests as text (never applied, rendered
or sent to a cluster/AWS) and flags workloads whose replica count is a fixed literal at or above
`context.min_static_replicas` while no autoscaler in the supplied files targets them:

- Kubernetes `Deployment`, `StatefulSet`, `ReplicaSet`, `ReplicationController` and Argo
  `Rollout` (also inside `kind: List`) with `spec.replicas`, and no `HorizontalPodAutoscaler`
  or KEDA `ScaledObject` whose `scaleTargetRef` matches kind, name and namespace.
- CloudFormation `AWS::ECS::Service` with `DesiredCount`, and no
  `AWS::ApplicationAutoScaling::ScalableTarget` on `ecs:service:DesiredCount` referring to it.
- Docker Compose services with `deploy.replicas` (or legacy `scale`); Compose has no
  demand-based autoscaler at all.

The contract runner (`textstatic.py`) evaluates one file at a time, but an autoscaler often lives
in another file, so `evaluate` first indexes autoscalers across every supplied source and then
judges each scope file against that index. Static only: it proves "fixed replicas and no
autoscaler in these files", not that demand varies, so no measurements are emitted.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

from . import miniyaml, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-02"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-02", "INF02")
SETTING_KEYS = ("min_static_replicas",)
FORMATS = (
    "Kubernetes manifests (.yaml/.yml), Docker Compose files (.yaml/.yml) and CloudFormation templates "
    "(.json/.yaml/.yml)"
)

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/framework/sus_sus_user_a2.html",
    "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/",
    "https://keda.sh/docs/latest/concepts/scaling-deployments/",
    "https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-auto-scaling.html",
    "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-applicationautoscaling-scalabletarget.html",
    "https://docs.docker.com/reference/compose-file/deploy/",
)
RECOMMENDATION = "Scale the workload on demand with an autoscaler and size the minimum for baseline load."
REC_K8S = (
    "Add a HorizontalPodAutoscaler (or KEDA ScaledObject) that targets this workload, with minReplicas sized for "
    "baseline load and a target from observed CPU/request metrics, and drop spec.replicas so each apply does not "
    "reset the autoscaler. If the count is fixed on purpose (quorum, licensing), keep it and add `# noqa: INF-02` "
    "with the reason."
)
REC_ECS = (
    "Register the service with Application Auto Scaling (AWS::ApplicationAutoScaling::ScalableTarget on "
    "ecs:service:DesiredCount plus a target-tracking ScalingPolicy, e.g. ECSServiceAverageCPUUtilization), with "
    "MinCapacity sized for baseline load."
)
REC_COMPOSE = (
    "Compose cannot scale on demand: run only the replicas baseline load needs, or deploy the service on an "
    "orchestrator with demand-based autoscaling (ECS Service Auto Scaling, Kubernetes HPA/KEDA)."
)
LIMITATION = (
    "Static manifest scan only: INF-02 proves that a workload has a fixed replica count at or above "
    "context.min_static_replicas and that no autoscaler in the supplied files targets it, not that demand "
    "varies or that replicas sit idle; no telemetry is used and no measurements are emitted. Autoscalers outside "
    "the supplied files (other repositories, `kubectl autoscale`, scalable targets registered via console/CLI), "
    "Kustomize replica overrides/patches and Helm values are not visible; an HPA pinned at minReplicas == "
    "maxReplicas still counts as an autoscaler. Helm/Go templates, Kubernetes JSON, ECS task definition JSON, "
    "Copilot manifests and EC2 Auto Scaling groups are not evaluated."
)

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "ReplicaSet", "ReplicationController", "Rollout")
# Compose file/directory names that mark local development or test stacks (as in INF-08).
DEV_COMPOSE = {"dev", "development", "debug", "test", "tests", "testing", "local", "override", "ci", "e2e",
               "devcontainer"}
ECS_DIMENSION = "ecs:service:DesiredCount"
AUTOSCALER_TEXT = {
    "kubernetes": re.compile(r"\b(HorizontalPodAutoscaler|ScaledObject)\b"),
    "ecs": re.compile(r"ScalableTarget|ecs:service:DesiredCount"),
}
_INT = re.compile(r"^\+?[0-9]+$")


@dataclass
class Workload:
    fmt: str  # kubernetes | ecs | compose
    kind: str
    name: str
    namespace: str | None
    anchor: str
    count: int | None  # None: not a literal integer
    line: int
    service_name: str | None = None  # literal ECS ServiceName


@dataclass
class Manifest:
    locator: str
    lines: list
    docs: list
    workloads: list = field(default_factory=list)
    k8s_targets: list = field(default_factory=list)  # (kind, name, namespace|None)
    ecs_logical_ids: set = field(default_factory=set)  # ECS services scaled by a target in this template
    ecs_names: set = field(default_factory=set)  # literal ECS service names in scalable target ResourceIds
    ecs_unresolved: int = 0  # ECS scalable targets that could not be tied to a service


@dataclass
class Index:
    """Autoscalers found across every supplied source."""

    k8s_targets: list = field(default_factory=list)
    ecs_names: set = field(default_factory=set)
    ecs_unresolved: bool = False
    # Unreadable files whose text names an autoscaler kind, by format: they could hide one.
    blind: dict = field(default_factory=lambda: {"kubernetes": [], "ecs": []})
    loaded: dict = field(default_factory=dict)  # (locator, content) -> Manifest, so files are parsed once


# -- helpers (same shape as inf08's private helpers) -------------------------------------------


def _get(node, *path):
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.items.get(key)
    return node


def _text(node):
    return node.value if isinstance(node, Scalar) else None


def _is_k8s(doc):
    return isinstance(doc, Mapping) and _text(doc.get("apiVersion")) and _text(doc.get("kind"))


def _is_compose(doc):
    services = _get(doc, "services")
    return (
        isinstance(services, Mapping)
        and not _get(doc, "apiVersion")
        and all(isinstance(s, Mapping) or s is None for s in services.items.values())
    )


def _is_cfn(doc):
    return not _is_k8s(doc) and isinstance(_get(doc, "Resources"), Mapping)


def _k8s_objects(docs):
    for doc in docs:
        if not _is_k8s(doc):
            continue
        if _text(doc.get("kind")) == "List":
            items = doc.get("items")
            for item in items.items if isinstance(items, Sequence) else []:
                if _is_k8s(item):
                    yield item
        else:
            yield doc


def _strings(node):
    """Every scalar string inside a node (intrinsic function arguments included)."""
    if isinstance(node, Scalar):
        if node.value is not None:
            yield node.value
    elif isinstance(node, Sequence):
        for item in node.items:
            yield from _strings(item)
    elif isinstance(node, Mapping):
        for value in node.items.values():
            yield from _strings(value)


def _count(node):
    """The replica count of a node, or None when it is not a literal integer."""
    if isinstance(node, Scalar) and node.value is None:
        return 1  # an explicit null means "use the default" (1)
    if isinstance(node, Scalar) and _INT.match(node.value.strip()):
        return int(node.value.strip())
    return None


def _line(mapping, key, node):
    return mapping.key_lines.get(key, node.line if node is not None else mapping.line)


# -- reading one file --------------------------------------------------------------------------


def _load(locator, content):
    lower = locator.lower()
    if lower.endswith(".json"):
        try:
            data = json.loads(content)
        except ValueError:
            raise ParseError("invalid JSON") from None
        if isinstance(data, dict) and "kind" in data and "apiVersion" in data:
            raise Unsupported(locator)  # Kubernetes JSON is not evaluated (as in INF-08)
        if not (isinstance(data, dict) and isinstance(data.get("Resources"), dict)):
            raise NotEvaluated("no Kubernetes objects, Compose services or CloudFormation resources found")
    elif not lower.endswith((".yaml", ".yml")):
        raise Unsupported(locator)
    try:
        docs = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    kinds = set()
    for doc in docs:
        if _is_k8s(doc):
            kinds.add("kubernetes")
        elif _is_cfn(doc):
            kinds.add("cloudformation")
        elif _is_compose(doc):
            kinds.add("compose")
    if not kinds:
        raise NotEvaluated("no Kubernetes objects, Compose services or CloudFormation resources found")
    if kinds == {"compose"}:
        parts = [p.lower() for p in re.split(r"[\\/]", locator)]
        tokens = set(re.split(r"[._-]", parts[-1])) | {p.lstrip(".") for p in parts[:-1]}
        marked = sorted(tokens & DEV_COMPOSE)
        if marked:
            raise NotEvaluated(f"Compose file marked as development/test ({marked[0]}); INF-02 v1 evaluates "
                               f"deployment manifests only")
    manifest = Manifest(locator=locator, lines=content.splitlines(), docs=docs)
    _read_k8s(manifest)
    _read_cfn(manifest)
    _read_compose(manifest)
    return manifest


def _read_k8s(manifest):
    for doc in _k8s_objects(manifest.docs):
        kind = _text(doc.get("kind"))
        namespace = _text(_get(doc, "metadata", "namespace"))
        if kind in ("HorizontalPodAutoscaler", "ScaledObject"):
            ref = _get(doc, "spec", "scaleTargetRef")
            target_name = _text(_get(ref, "name"))
            # KEDA defaults scaleTargetRef.kind to Deployment; HPA requires it.
            target_kind = _text(_get(ref, "kind")) or ("Deployment" if kind == "ScaledObject" else None)
            if target_name:
                manifest.k8s_targets.append((target_kind, target_name, namespace))
            continue
        if kind not in WORKLOAD_KINDS:
            continue
        spec = _get(doc, "spec")
        if not isinstance(spec, Mapping) or "replicas" not in spec.items:
            continue  # replicas omitted: the API server defaults it to 1
        if kind == "ReplicaSet" and _get(doc, "metadata", "ownerReferences") is not None:
            continue  # managed by a Deployment
        name = _text(_get(doc, "metadata", "name")) or _text(_get(doc, "metadata", "generateName")) or "?"
        node = spec.items["replicas"]
        count = _count(node)
        manifest.workloads.append(Workload(
            fmt="kubernetes", kind=kind, name=name, namespace=namespace,
            anchor=f"{kind}/{namespace + '/' if namespace else ''}{name}",
            count=count, line=_line(spec, "replicas", node),
        ))


def _read_cfn(manifest):
    for doc in manifest.docs:
        if not _is_cfn(doc):
            continue
        resources = doc.get("Resources")
        services = {}
        for logical_id, resource in resources.items.items():
            rtype = _text(_get(resource, "Type"))
            props = _get(resource, "Properties")
            if rtype == "AWS::ECS::Service":
                services[logical_id] = props
                if _text(_get(props, "SchedulingStrategy")) == "DAEMON":
                    continue  # one task per container instance; there is no replica count to scale
                if not isinstance(props, Mapping) or "DesiredCount" not in props.items:
                    continue  # CloudFormation defaults DesiredCount to 1
                node = props.items["DesiredCount"]
                count = _count(node)
                service_name = _text(_get(props, "ServiceName"))
                manifest.workloads.append(Workload(
                    fmt="ecs", kind="AWS::ECS::Service", name=logical_id, namespace=None,
                    anchor=f"AWS::ECS::Service/{logical_id}", count=count,
                    line=_line(props, "DesiredCount", node),
                    service_name=service_name if service_name and _is_literal_name(service_name) else None,
                ))
        for resource in resources.items.values():
            if _text(_get(resource, "Type")) != "AWS::ApplicationAutoScaling::ScalableTarget":
                continue
            props = _get(resource, "Properties")
            if not (_text(_get(props, "ScalableDimension")) == ECS_DIMENSION
                    or _text(_get(props, "ServiceNamespace")) == "ecs"):
                continue
            strings = list(_strings(_get(props, "ResourceId")))
            matched = {lid for lid in services if _mentions(strings, lid, _text(_get(services[lid], "ServiceName")))}
            manifest.ecs_logical_ids |= matched
            literal = {s.rsplit("/", 1)[1] for s in strings if re.fullmatch(r"service/[^/${}]+/[^/${}]+", s)}
            manifest.ecs_names |= literal
            if not matched and not literal:
                manifest.ecs_unresolved += 1


def _is_literal_name(text):
    return "${" not in text and "{{" not in text


def _mentions(strings, logical_id, service_name):
    sub = re.compile(r"\$\{" + re.escape(logical_id) + r"(\.Name)?\}")
    for text in strings:
        if text in (logical_id, f"{logical_id}.Name") or sub.search(text):
            return True
        if service_name and _is_literal_name(service_name) and (
                text == service_name or text.endswith("/" + service_name)):
            return True
    return False


def _read_compose(manifest):
    for doc in manifest.docs:
        if _is_k8s(doc) or _is_cfn(doc) or not _is_compose(doc):
            continue
        services = doc.get("services")
        for name, service in services.items.items():
            if not isinstance(service, Mapping) or "extends" in service.items:
                continue
            deploy = _get(service, "deploy")
            if _text(_get(deploy, "mode")) in ("global", "replicated-job", "global-job"):
                continue
            if isinstance(deploy, Mapping) and "replicas" in deploy.items:
                holder, key = deploy, "replicas"
            elif "scale" in service.items:
                holder, key = service, "scale"
            else:
                continue
            node = holder.items[key]
            count = _count(node)
            manifest.workloads.append(Workload(
                fmt="compose", kind="service", name=name, namespace=None, anchor=f"service/{name}",
                count=count, line=_line(holder, key, node),
            ))


# -- autoscaler matching -----------------------------------------------------------------------


def _k8s_scaled(workload, targets):
    for kind, name, namespace in targets:
        if kind != workload.kind or name != workload.name:
            continue
        # An unset namespace is chosen at apply time (kubectl -n, Kustomize), so it matches any namespace.
        if namespace is None or workload.namespace is None or namespace == workload.namespace:
            return True
    return False


def _scaled(workload, manifest, index):
    if workload.fmt == "kubernetes":
        return _k8s_scaled(workload, manifest.k8s_targets + index.k8s_targets)
    if workload.fmt == "ecs":
        return workload.name in manifest.ecs_logical_ids or (
            workload.service_name is not None and workload.service_name in (manifest.ecs_names | index.ecs_names))
    return False  # Compose has no demand-based autoscaler


def _check_counts(manifest, index):
    """A count that is not a literal integer can only be judged when an autoscaler already covers it."""
    for workload in manifest.workloads:
        if workload.count is None and not _scaled(workload, manifest, index):
            field_name = {"kubernetes": "spec.replicas", "ecs": "DesiredCount", "compose": "replica count"}[
                workload.fmt]
            written = manifest.lines[workload.line - 1].strip() if workload.line <= len(manifest.lines) else "?"
            raise NotEvaluated(
                f"{field_name} of {workload.anchor} on line {workload.line} is not a literal integer "
                f"({written[:80]}); it is set at deploy time"
            )


def parse(locator, content, index=None):
    """Read one file. Without an index only autoscalers in the same file are known."""
    index = index or Index()
    manifest = index.loaded.get((locator, content)) or _load(locator, content)
    _check_counts(manifest, index)
    return manifest


def build_index(sources):
    """Collect autoscalers from every supplied static source, and note manifests that cannot be read."""
    index = Index()
    for source in sources:
        if not isinstance(source, dict) or source.get("kind") != "static":
            continue
        locator, content = source.get("locator"), source.get("content")
        if not isinstance(locator, str) or not isinstance(content, str):
            continue
        try:
            manifest = _load(locator, content)
        except NotEvaluated:
            continue
        except Exception:  # unparseable or Kubernetes JSON: an autoscaler named in it would not be seen
            if not locator.lower().endswith((".yaml", ".yml", ".json")):
                continue
            for fmt, pattern in AUTOSCALER_TEXT.items():
                if pattern.search(content):
                    index.blind[fmt].append(locator)
            continue
        index.loaded[(locator, content)] = manifest
        index.k8s_targets.extend(manifest.k8s_targets)
        index.ecs_names |= manifest.ecs_names
        index.ecs_unresolved = index.ecs_unresolved or manifest.ecs_unresolved > 0
    return index


# -- findings ----------------------------------------------------------------------------------


def run(ctx, settings, index=None):
    index = index or Index()
    threshold = settings["min_static_replicas"]
    hits = []
    for workload in ctx.workloads:
        if workload.count is None or workload.count < threshold or _scaled(workload, ctx, index):
            continue
        caveats = []
        if workload.fmt == "kubernetes":
            what = f"{workload.kind} {workload.name!r} runs a fixed {workload.count} replicas (spec.replicas)"
            missing = "no HorizontalPodAutoscaler or KEDA ScaledObject in the scanned files targets it"
            confidence = "medium"
            recommendation = REC_K8S
            if workload.kind == "StatefulSet":
                confidence = "low"
                caveats.append("StatefulSets are often sized for quorum or storage, so confirm it can scale")
        elif workload.fmt == "ecs":
            what = f"ECS service {workload.name!r} has a fixed DesiredCount of {workload.count}"
            missing = "no Application Auto Scaling scalable target in the scanned files refers to it"
            confidence = "medium"
            recommendation = REC_ECS
            if index.ecs_unresolved:
                confidence = "low"
                caveats.append("an ECS scalable target in the scanned files could not be tied to a service")
        else:
            what = f"Compose service {workload.name!r} runs a fixed {workload.count} replicas"
            missing = "Compose has no demand-based autoscaler"
            confidence = "low"
            recommendation = REC_COMPOSE
        blind = index.blind.get(workload.fmt)
        if blind:
            confidence = "low"
            caveats.append(f"{len(blind)} supplied file(s) that mention an autoscaler could not be read, so an "
                           f"autoscaler there would not be seen")
        summary = f"{what} and {missing}, so capacity stays at that size whatever the demand"
        if caveats:
            summary += "; " + "; ".join(caveats)
        hits.append(TextHit(
            line=workload.line,
            anchor=workload.anchor,
            summary=summary + ".",
            confidence=confidence,
            block_line=workload.line,
            recommendation=recommendation,
        ))
    return hits


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    value = context["min_static_replicas"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        return None, "context.min_static_replicas must be an integer of at least 2"
    return {key: context[key] for key in SETTING_KEYS}, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    sources = payload.get("sources") if isinstance(payload, dict) else None
    index = build_index(sources if isinstance(sources, list) else [])
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, FORMATS=FORMATS,
        parse=lambda locator, content: module.parse(locator, content, index),
        run=lambda ctx: module.run(ctx, settings, index) if settings else [],
    )
    result = textstatic.evaluate_text(payload, check)
    if settings is None:
        result.update(
            status="unavailable",
            coverage={
                "evaluated_scope": [],
                "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION],
            },
            findings=[],
            measurements=[],
        )
    return result
