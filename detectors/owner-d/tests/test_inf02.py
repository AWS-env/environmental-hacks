"""Behavioral tests for the INF-02 detector (issue #170)."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli, inf02
from owner_d.inf02 import CHECK_ID, DETECTOR_VERSION, EvaluationError, NotEvaluated, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf02"
REPOSITORY_ID = "github:AWS-env/example"
SETTINGS = {"format": "manifest", "min_static_replicas": 2}


def static_source(name, content=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{name}",
        "kind": "static",
        "locator": name,
        "content": content,
    }


def make_input(*names, sources=None, scope=None, context=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-inf02-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(SETTINGS) if context is None else context,
        "scope": scope if scope is not None else [s["scope_id"] for s in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_inline(*files, context=None):
    """files: (name, content) pairs."""
    return run(sources=[static_source(name, content) for name, content in files], context=context)


def findings_for(result, name):
    return {f["identity"]: f for f in result["findings"] if f["scope_id"] == f"file:{name}"}


def deployment(name, replicas, namespace=None, kind="Deployment"):
    ns = f"\n  namespace: {namespace}" if namespace else ""
    return f"apiVersion: apps/v1\nkind: {kind}\nmetadata:\n  name: {name}{ns}\nspec:\n  replicas: {replicas}\n"


def hpa(target, namespace=None, kind="Deployment"):
    ns = f"\n  namespace: {namespace}" if namespace else ""
    return (
        f"apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: {target}{ns}\nspec:\n"
        f"  scaleTargetRef:\n    apiVersion: apps/v1\n    kind: {kind}\n    name: {target}\n"
        f"  minReplicas: 1\n  maxReplicas: 5\n"
    )


class Inf02PositiveTests(unittest.TestCase):
    """INF02-01: fixed replica counts in Kubernetes, CloudFormation ECS and Compose are flagged."""

    EXPECTED = {
        "k8s-positive.yaml": {
            "Deployment/shop/web": (8, "medium", "fixed 4 replicas"),
            "StatefulSet/queue": (25, "low", "quorum"),
            "Deployment/api": (44, "medium", "fixed 2 replicas"),  # inside kind: List
            "Rollout/canary": (82, "medium", "fixed 5 replicas"),  # the HPA targets kind Deployment
        },
        "ecs-service.yaml": {
            "AWS::ECS::Service/Api": (10, "medium", "fixed DesiredCount of 3"),
        },
        "docker-compose.yml": {
            "service/web": (6, "low", "Compose has no demand-based autoscaler"),
            "service/worker": (9, "low", "fixed 2 replicas"),  # legacy `scale`
        },
    }

    def test_static_replicas_are_flagged_with_exact_lines(self):
        names = list(self.EXPECTED)
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["measurements"], [])
        for name, expected in self.EXPECTED.items():
            findings = findings_for(result, name)
            self.assertEqual(set(findings), set(expected), name)
            lines = (FIXTURES / name).read_text().splitlines()
            for identity, (line, confidence, phrase) in expected.items():
                finding = findings[identity]
                with self.subTest(name=name, identity=identity):
                    self.assertEqual(finding["confidence"], confidence)
                    self.assertIn(phrase, finding["summary"])
                    self.assertEqual(
                        finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity)
                    )
                    [evidence] = finding["evidence"]
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["line_start"], line)
                    self.assertEqual(evidence["value"], lines[line - 1])
                    self.assertTrue(finding["references"])

    def test_recommendations_are_format_specific(self):
        _, result = run("k8s-positive.yaml", "ecs-service.yaml", "docker-compose.yml")
        recommendations = {f["identity"]: f["recommendation"] for f in result["findings"]}
        self.assertIn("HorizontalPodAutoscaler", recommendations["Deployment/shop/web"])
        self.assertIn("ScalableTarget", recommendations["AWS::ECS::Service/Api"])
        self.assertIn("Compose cannot scale on demand", recommendations["service/web"])


class Inf02NegativeTests(unittest.TestCase):
    """INF02-02: defaulted/single replicas and workloads with a same-file autoscaler are clean."""

    def test_autoscaled_and_default_workloads_are_clean(self):
        _, result = run("k8s-negative.yaml", "ecs-autoscaled.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:k8s-negative.yaml", "file:ecs-autoscaled.json"])
        self.assertEqual(result["findings"], [])

    def test_ecs_scalable_target_via_sub_and_daemon_and_default_count_are_clean(self):
        _, result = run("ecs-service.yaml")
        self.assertEqual(list(findings_for(result, "ecs-service.yaml")), ["AWS::ECS::Service/Api"])

    def test_compose_global_and_single_replicas_are_clean(self):
        findings = findings_for(run("docker-compose.yml")[1], "docker-compose.yml")
        for name in ("db", "agent", "cache"):
            self.assertNotIn(f"service/{name}", findings)

    def test_removing_the_autoscaler_restores_the_finding(self):
        content = deployment("frontend", 3, "shop") + "---\n" + hpa("frontend", "shop")
        self.assertEqual(run_inline(("app.yaml", content))[1]["findings"], [])
        _, result = run_inline(("app.yaml", deployment("frontend", 3, "shop")))
        self.assertEqual([f["identity"] for f in result["findings"]], ["Deployment/shop/frontend"])


class Inf02ExceptionTests(unittest.TestCase):
    """INF02-03: cross-file autoscalers, namespace matching, noqa and development Compose files."""

    def test_hpa_in_another_supplied_file_counts_but_other_namespace_does_not(self):
        _, alone = run("k8s-cross-file.yaml")
        self.assertEqual(
            list(findings_for(alone, "k8s-cross-file.yaml")), ["Deployment/shop/checkout", "Deployment/shop/search"]
        )
        _, together = run("k8s-cross-file.yaml", "hpa.yaml")
        self.assertEqual(together["status"], "completed")
        self.assertEqual(together["coverage"]["evaluated_scope"], ["file:k8s-cross-file.yaml", "file:hpa.yaml"])
        # The search HPA is in namespace staging, so it does not cover shop/search.
        self.assertEqual([f["identity"] for f in together["findings"]], ["Deployment/shop/search"])
        self.assertEqual(together["findings"][0]["confidence"], "medium")

    def test_unset_namespace_matches_any_namespace(self):
        for workload_ns, hpa_ns in ((None, "shop"), ("shop", None), (None, None)):
            with self.subTest(workload_ns=workload_ns, hpa_ns=hpa_ns):
                _, result = run_inline(("a.yaml", deployment("web", 3, workload_ns)), ("b.yaml", hpa("web", hpa_ns)))
                self.assertEqual(result["findings"], [])

    def test_hpa_kind_must_match(self):
        _, result = run_inline(("a.yaml", deployment("web", 3, kind="StatefulSet")), ("b.yaml", hpa("web")))
        self.assertEqual([f["identity"] for f in result["findings"]], ["StatefulSet/web"])

    def test_noqa_suppresses_only_inf02(self):
        _, result = run("k8s-exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["Deployment/other-rule"])

    def test_ecs_scalable_target_in_another_template_matches_a_literal_service_name(self):
        service = (
            "Resources:\n  Payments:\n    Type: AWS::ECS::Service\n    Properties:\n"
            "      ServiceName: payments\n      DesiredCount: 3\n"
        )
        target = (
            "Resources:\n  Scaling:\n    Type: AWS::ApplicationAutoScaling::ScalableTarget\n    Properties:\n"
            "      ServiceNamespace: ecs\n      ScalableDimension: ecs:service:DesiredCount\n"
            "      ResourceId: service/prod/{}\n"
        )
        _, matched = run_inline(("svc.yaml", service), ("scaling.yaml", target.format("payments")))
        self.assertEqual(matched["findings"], [])
        _, other = run_inline(("svc.yaml", service), ("scaling.yaml", target.format("orders")))
        self.assertEqual([f["identity"] for f in other["findings"]], ["AWS::ECS::Service/Payments"])
        self.assertEqual(other["findings"][0]["confidence"], "medium")

    def test_unresolved_ecs_scalable_target_lowers_confidence(self):
        target = (
            "Resources:\n  Scaling:\n    Type: AWS::ApplicationAutoScaling::ScalableTarget\n    Properties:\n"
            "      ScalableDimension: ecs:service:DesiredCount\n      ResourceId: !ImportValue ServiceResourceId\n"
        )
        sources = [static_source("ecs-service.yaml"), static_source("t.yaml", target)]
        _, result = run(sources=sources)
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "low")
        self.assertIn("could not be tied to a service", finding["summary"])

    def test_development_compose_files_are_not_evaluated(self):
        for name in ("docker-compose.override.yml", "compose.dev.yaml", "test/docker-compose.yml"):
            with self.subTest(name=name):
                _, result = run_inline((name, "services:\n  web:\n    image: x\n    deploy:\n      replicas: 3\n"))
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("development/test", result["coverage"]["limitations"][0])


class Inf02IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF02-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:k8s/deployment.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_threshold_is_unavailable(self):
        """INF02-04: the replica threshold is a required judgment-call setting."""
        for context, reason in (
            ({"format": "manifest"}, "missing required context settings: min_static_replicas"),
            ({"min_static_replicas": 1}, "must be an integer of at least 2"),
            ({"min_static_replicas": "2"}, "must be an integer of at least 2"),
            ({"min_static_replicas": True}, "must be an integer of at least 2"),
            ({"min_static_replicas": 2.0}, "must be an integer of at least 2"),
        ):
            with self.subTest(context=context):
                _, result = run("k8s-positive.yaml", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_templates_deploy_time_counts_and_malformed_files_make_result_partial(self):
        """INF02-05: Helm templates, substituted counts, invalid JSON and non-manifests are never reported clean."""
        _, result = run("k8s-positive.yaml", "helm-deployment.yaml", "templated.yaml", "ecs-param.yaml",
                        "broken.json", "workflow.yml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:k8s-positive.yaml"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:helm-deployment.yaml: could not be parsed (Go/Helm template syntax on line 4)", limitations)
        self.assertIn("file:templated.yaml: not evaluated (spec.replicas of Deployment/web on line 7 is not a literal "
                      "integer (replicas: ${REPLICAS})", limitations)
        self.assertIn("file:ecs-param.yaml: not evaluated (DesiredCount of AWS::ECS::Service/Api on line 11 is not a "
                      "literal integer (DesiredCount: !Ref DesiredCount)", limitations)
        self.assertIn("file:broken.json: could not be parsed (invalid JSON)", limitations)
        self.assertIn("file:workflow.yml: not evaluated (no Kubernetes objects", limitations)
        # None of the unreadable files mentions an autoscaler, so confidence is unchanged.
        self.assertEqual(findings_for(result, "k8s-positive.yaml")["Deployment/shop/web"]["confidence"], "medium")

    def test_unreadable_file_that_mentions_an_autoscaler_lowers_confidence(self):
        """INF02-05: a Helm HPA template could target the workload, so its findings drop to low."""
        _, result = run("k8s-positive.yaml", "helm-hpa.yaml", "ecs-service.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertIn("file:helm-hpa.yaml: could not be parsed", " ".join(result["coverage"]["limitations"]))
        k8s = findings_for(result, "k8s-positive.yaml")
        self.assertEqual({f["confidence"] for f in k8s.values()}, {"low"})
        self.assertTrue(all("1 supplied file(s) that mention an autoscaler could not be read" in f["summary"]
                            for f in k8s.values()))
        # The Helm template names no ECS scalable target, so the ECS finding keeps its confidence.
        self.assertEqual(findings_for(result, "ecs-service.yaml")["AWS::ECS::Service/Api"]["confidence"], "medium")

    def test_deploy_time_count_is_evaluated_when_an_autoscaler_covers_it(self):
        _, result = run("ecs-autoscaled.json")
        self.assertEqual(result["status"], "completed")
        _, result = run_inline(("a.yaml", deployment("web", "${REPLICAS}")), ("b.yaml", hpa("web")))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_unsupported_extensions_and_unparseable_yaml_are_unavailable(self):
        for name, content, reason in (
            ("values.toml", "a = 1\n", "unsupported file type"),
            ("deploy.yaml", "kind: Pod\n\tapiVersion: v1\n", "tab indentation"),
            ("deploy.json", '{"kind": "Deployment", "apiVersion": "apps/v1"}', "unsupported file type"),
            ("taskdef.json", '{"family": "x", "containerDefinitions": []}', "no Kubernetes objects"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline((name, content))
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_module_parse_for_scanner_selection(self):
        """Without the payload index, only same-file autoscalers can cover a deploy-time count."""
        with self.assertRaises(NotEvaluated):
            inf02.parse("templated.yaml", (FIXTURES / "templated.yaml").read_text())
        manifest = inf02.parse("ecs-autoscaled.json", (FIXTURES / "ecs-autoscaled.json").read_text())
        self.assertEqual(len(manifest.workloads), 2)


class Inf02BoundaryTests(unittest.TestCase):
    """INF02-06: threshold is inclusive; identities are kind/[ns/]name; repeats get #n; lines may move."""

    def test_threshold_is_inclusive_and_identities_distinguish_kinds(self):
        _, result = run("boundary.yaml")
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            ["Deployment/api", "StatefulSet/api", "Deployment/api#2"],
        )
        _, raised = run("boundary.yaml", context={"min_static_replicas": 3})
        self.assertEqual([f["identity"] for f in raised["findings"]], ["Deployment/api"])
        self.assertEqual(raised["findings"][0]["evidence"][0]["value"], "  replicas: 3")
        _, high = run("boundary.yaml", context={"min_static_replicas": 4})
        self.assertEqual(high["status"], "completed")
        self.assertEqual(high["findings"], [])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("k8s-positive.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "k8s-positive.yaml").read_text()
        _, after = run_inline(("k8s-positive.yaml", shifted))
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Inf02ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:k8s-positive.yaml", "Deployment/shop/web")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("k8s-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "  replicas: 40"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("k8s-positive.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("k8s-positive.yaml", "docker-compose.yml", "ecs-service.yaml", "hpa.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "inf02-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("k8s-positive.yaml"))


class Inf02CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf02_result(self):
        input_path = FIXTURES / "inf02-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["findings"]), 4)


if __name__ == "__main__":
    unittest.main()
