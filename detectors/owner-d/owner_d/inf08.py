"""INF-08: missing CPU/memory limits (noisy neighbor) — static manifest scan.

Detector semantics version 1.0.0. Reads deployment manifests as text (never applied,
rendered or sent to a cluster/AWS) and flags containers that can grow without bound on a
shared host:

- Kubernetes workloads (Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob,
  ReplicationController, DeploymentConfig, Rollout; also inside `kind: List`): containers
  without `resources.requests`/`resources.limits`. A LimitRange in the same file that
  supplies defaults for the namespace counts as set.
- Docker Compose services without `deploy.resources.limits` / `mem_limit` / `cpus`.
- Amazon ECS task definitions (task definition JSON, `describe-task-definition` output and
  CloudFormation `AWS::ECS::TaskDefinition` in JSON or YAML): containers with neither a
  task-level nor a container-level hard `memory` / `cpu`.

Static only: actual usage and contention are not observed, so no measurements are emitted.
"""

from __future__ import annotations

import json
import re
import sys

from . import miniyaml, textstatic
from .miniyaml import Mapping, Scalar, Sequence
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "INF-08"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-08", "INF08")
FORMATS = (
    "Kubernetes manifests (.yaml/.yml), Docker Compose files (.yaml/.yml) and ECS task definitions / "
    "CloudFormation templates (.json/.yaml/.yml)"
)

REFERENCES = (
    "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/",
    "https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/",
    "https://kubernetes.io/docs/concepts/policy/limit-range/",
    "https://docs.docker.com/reference/compose-file/deploy/",
    "https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html",
    "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-ecs-taskdefinition.html",
)
RECOMMENDATION = "Set CPU and memory requests/limits for every container, sized from observed usage."
REC_K8S = (
    "Set resources.requests.cpu/memory and resources.limits.memory (and a CPU limit where bursting must be "
    "capped) on every container, or add a LimitRange with defaults for the namespace; size them from observed "
    "usage (e.g. VPA recommendations or Container Insights)."
)
REC_COMPOSE = "Set deploy.resources.limits (cpus, memory) — or mem_limit/cpus — on each service."
REC_ECS = (
    "Set task-level cpu and memory (required on Fargate) or a container-level cpu and hard memory limit; "
    "memoryReservation alone is only a soft limit."
)
LIMITATION = (
    "Static manifest scan only: INF-08 proves that a container is unbounded in the manifest, not that it "
    "starves neighbours — the taxonomy's noisy-neighbour impact needs runtime telemetry, which v1 does not use, "
    "so no measurements are emitted. Defaults applied outside the file (LimitRange/ResourceQuota in other "
    "files, Kustomize patches, Helm values, admission webhooks, EKS Fargate or ECS capacity settings) are not "
    "visible; Helm/Go templates, init containers and Kubernetes JSON manifests are not evaluated."
)

POD_SPEC = {
    "Pod": ("spec",),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
for _kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job",
              "DeploymentConfig", "Rollout"):
    POD_SPEC[_kind] = ("spec", "template", "spec")
# Compose file/directory names that mark local development or test stacks.
DEV_COMPOSE = {"dev", "development", "debug", "test", "tests", "testing", "local", "override", "ci", "e2e",
               "devcontainer"}


class Ctx:
    def __init__(self, locator, content, docs):
        self.locator = locator
        self.lines = content.splitlines()
        self.docs = docs


def _get(node, *path):
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.items.get(key)
    return node


def _text(node):
    return node.value if isinstance(node, Scalar) else None


def _set(node):
    """True for a present, non-null value (intrinsics/mappings such as {"Ref": ...} count as set)."""
    if node is None:
        return False
    if isinstance(node, Scalar):
        return node.value is not None and node.value.strip() != ""
    return True


def _is_k8s(doc):
    return isinstance(doc, Mapping) and _text(doc.get("apiVersion")) and _text(doc.get("kind"))


def _is_compose(doc):
    services = _get(doc, "services")
    return (
        isinstance(services, Mapping)
        and not _get(doc, "apiVersion")
        and all(isinstance(s, Mapping) or s is None for s in services.items.values())
    )


def _cfn_task_definitions(doc):
    resources = _get(doc, "Resources")
    if not isinstance(resources, Mapping):
        return None
    found = []
    for logical_id, resource in resources.items.items():
        if _text(_get(resource, "Type")) == "AWS::ECS::TaskDefinition":
            found.append((logical_id, _get(resource, "Properties"), "cfn"))
    return found


def _ecs_task_definitions(doc):
    if isinstance(_get(doc, "taskDefinition"), Mapping):
        doc = _get(doc, "taskDefinition")
    if isinstance(_get(doc, "containerDefinitions"), Sequence):
        return [(_text(_get(doc, "family")) or "task", doc, "ecs")]
    return None


def parse(locator, content):
    lower = locator.lower()
    if lower.endswith(".json"):
        try:
            data = json.loads(content)
        except ValueError:
            raise ParseError("invalid JSON") from None
        if not (isinstance(data, dict) and {"containerDefinitions", "taskDefinition", "Resources", "kind"} & set(data)):
            raise NotEvaluated(
                "no Kubernetes objects, Compose services, ECS task definitions or CloudFormation resources found"
            )
    elif not lower.endswith((".yaml", ".yml")):
        raise Unsupported(locator)
    try:
        docs = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    ctx = Ctx(locator, content, docs)
    kinds = set()
    for doc in docs:
        if _is_k8s(doc):
            kinds.add("kubernetes-json" if lower.endswith(".json") else "kubernetes")
        elif _ecs_task_definitions(doc) is not None or _cfn_task_definitions(doc) is not None:
            kinds.add("ecs")
        elif _is_compose(doc):
            kinds.add("compose")
    if "kubernetes-json" in kinds:
        raise Unsupported(locator)
    if not kinds:
        raise NotEvaluated(
            "no Kubernetes objects, Compose services, ECS task definitions or CloudFormation resources found"
        )
    if kinds == {"compose"}:
        parts = [p.lower() for p in re.split(r"[\\/]", locator)]
        tokens = set(re.split(r"[._-]", parts[-1])) | {p.lstrip(".") for p in parts[:-1]}
        marked = sorted(tokens & DEV_COMPOSE)
        if marked:
            raise NotEvaluated(f"Compose file marked as development/test ({marked[0]}); INF-08 v1 evaluates "
                               f"deployment manifests only")
    return ctx


# -- Kubernetes -------------------------------------------------------------------------------


def _limit_range_defaults(docs):
    """{namespace: {"limits.cpu": True, ...}} from LimitRange objects in the same file."""
    defaults = {}
    for doc in _k8s_objects(docs):
        if _text(doc.get("kind")) != "LimitRange":
            continue
        namespace = _text(_get(doc, "metadata", "namespace"))
        entry = defaults.setdefault(namespace, set())
        limits = _get(doc, "spec", "limits")
        for item in limits.items if isinstance(limits, Sequence) else []:
            if _text(_get(item, "type")) not in (None, "Container"):
                continue
            for section, prefix in (("default", "limits"), ("defaultRequest", "requests")):
                for resource in ("cpu", "memory"):
                    if _set(_get(item, section, resource)):
                        entry.add(f"{prefix}.{resource}")
    return defaults


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


def _exempt(*metadata):
    for meta in metadata:
        annotations = _get(meta, "annotations")
        for key in annotations.items if isinstance(annotations, Mapping) else {}:
            if key.startswith("ignore-check.kube-linter.io/unset-"):
                return True
            if key.startswith("polaris.fairwinds.com/") and key.endswith("exempt"):
                return True
    return False


def _k8s_hits(ctx):
    defaults = _limit_range_defaults(ctx.docs)
    for doc in _k8s_objects(ctx.docs):
        kind = _text(doc.get("kind"))
        if kind not in POD_SPEC:
            continue
        pod_spec = _get(doc, *POD_SPEC[kind])
        containers = _get(pod_spec, "containers")
        if not isinstance(containers, Sequence):
            continue
        template_meta = _get(doc, *POD_SPEC[kind][:-1], "metadata") if kind != "Pod" else None
        if _exempt(_get(doc, "metadata"), template_meta):
            continue
        name = _text(_get(doc, "metadata", "name")) or _text(_get(doc, "metadata", "generateName")) or "?"
        namespace = _text(_get(doc, "metadata", "namespace"))
        inherited = defaults.get(namespace, set())
        owner = f"{kind}/{namespace + '/' if namespace else ''}{name}"
        for index, container in enumerate(containers.items):
            if not isinstance(container, Mapping):
                continue
            cname = _text(container.get("name")) or f"#{index}"
            resources = _get(container, "resources")
            have = set(inherited)
            for prefix in ("limits", "requests"):
                for resource in ("cpu", "memory"):
                    if _set(_get(resources, prefix, resource)):
                        have.add(f"{prefix}.{resource}")
            for resource in ("cpu", "memory"):  # Kubernetes copies a limit into an unset request
                if f"limits.{resource}" in have:
                    have.add(f"requests.{resource}")
            missing = [f for f in ("requests.cpu", "requests.memory", "limits.cpu", "limits.memory") if f not in have]
            # A missing CPU limit alone is not flagged: CPU is shared by request weight under contention, and
            # many operators deliberately leave CPU limits unset to avoid throttling.
            if not missing or missing == ["limits.cpu"]:
                continue
            if len(missing) == 4:
                detail = ("sets no CPU/memory requests or limits (BestEffort QoS), so it can take any free CPU and "
                          "memory on the node")
                confidence = "medium"
            elif "limits.memory" in missing:
                detail = f"has no memory limit (missing {', '.join(missing)}), so its memory use is unbounded on the node"
                confidence = "medium"
            else:
                detail = (f"has a memory limit but no CPU request (missing {', '.join(missing)}), so the scheduler "
                          f"reserves no CPU for it and can overpack the node")
                confidence = "low"
            yield TextHit(
                line=_name_line(container, "name"),
                anchor=f"{owner}:{cname}",
                summary=f"Container {cname!r} in {kind} {name!r} {detail}.",
                confidence=confidence,
                block_line=container.line,
                recommendation=REC_K8S,
            )


# -- Docker Compose ---------------------------------------------------------------------------


def _compose_hits(ctx):
    for doc in ctx.docs:
        if _is_k8s(doc) or not _is_compose(doc):
            continue
        services = doc.get("services")
        for name, service in services.items.items():
            if not isinstance(service, Mapping) or "extends" in service.items:
                continue
            limits = _get(service, "deploy", "resources", "limits")
            memory = _set(service.get("mem_limit")) or _set(_get(limits, "memory"))
            cpu = any(_set(service.get(k)) for k in ("cpus", "cpu_quota", "cpu_count", "cpu_percent")) or _set(
                _get(limits, "cpus"))
            if memory:  # a missing CPU limit alone is not flagged (CPU is shared by weight under contention)
                continue
            missing = ["memory"] if cpu else ["memory", "CPU"]
            line = services.key_lines.get(name, service.line)
            yield TextHit(
                line=line,
                anchor=f"service/{name}",
                summary=(
                    f"Compose service {name!r} sets no {' or '.join(missing)} limit (deploy.resources.limits / "
                    f"mem_limit / cpus), so it can take all of the host's {' and '.join(missing)}."
                ),
                confidence="low",
                block_line=line,
                recommendation=REC_COMPOSE,
            )


# -- Amazon ECS -------------------------------------------------------------------------------


def _ecs_hits(ctx):
    for doc in ctx.docs:
        if _is_k8s(doc):
            continue
        tasks = (_ecs_task_definitions(doc) or []) + (_cfn_task_definitions(doc) or [])
        for family, task, style in tasks:
            key = (lambda k: k) if style == "ecs" else (lambda k: k[0].upper() + k[1:])
            task_cpu = _cpu_set(_get(task, key("cpu")))
            task_memory = _set(_get(task, key("memory")))
            containers = _get(task, key("containerDefinitions"))
            if not isinstance(containers, Sequence):
                continue
            for index, container in enumerate(containers.items):
                if not isinstance(container, Mapping):
                    continue
                cname = _text(_get(container, key("name"))) or f"#{index}"
                memory = task_memory or _set(_get(container, key("memory")))
                cpu = task_cpu or _cpu_set(_get(container, key("cpu")))
                missing = []
                if not memory:
                    soft = " (memoryReservation is only a soft limit)" if _set(
                        _get(container, key("memoryReservation"))) else ""
                    missing.append(f"no hard memory limit{soft}")
                if not cpu:
                    missing.append("no CPU units")
                if not missing:
                    continue
                yield TextHit(
                    line=_name_line(container, key("name")),
                    anchor=f"task/{family}:{cname}",
                    summary=(
                        f"Container {cname!r} in ECS task definition {family!r} has {' and '.join(missing)} at "
                        f"task or container level, so on a shared EC2 container instance it can take resources "
                        f"from co-located tasks."
                    ),
                    # Without CPU units the container still gets a minimal CPU share under contention, so a
                    # CPU-only gap is low confidence; an unbounded memory footprint is the real neighbour risk.
                    confidence="medium" if not memory else "low",
                    block_line=container.line,
                    recommendation=REC_ECS,
                )


def _name_line(container, key):
    """Cite the container's name line when present (clearer than a bare `{` in JSON)."""
    return container.key_lines.get(key, container.line)


def _cpu_set(node):
    if not _set(node):
        return False
    return not (isinstance(node, Scalar) and node.value.strip() == "0")


def run(ctx):
    return list(_k8s_hits(ctx)) + list(_compose_hits(ctx)) + list(_ecs_hits(ctx))


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])

