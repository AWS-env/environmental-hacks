"""Behavioral tests for the INF-08 detector (issue #175)."""

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

from owner_d import cli, miniyaml
from owner_d.inf08 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf08"
REPOSITORY_ID = "github:AWS-env/example"


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


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-inf08-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "manifest"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_inline(name, content):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])


def findings_for(result, name):
    return {f["identity"]: f for f in result["findings"] if f["scope_id"] == f"file:{name}"}


class Inf08PositiveTests(unittest.TestCase):
    """INF08-01: unbounded containers in Kubernetes, Compose and ECS are flagged with exact evidence."""

    EXPECTED = {
        "k8s-positive.yaml": {
            "Deployment/shop/web:app": (12, "medium", "BestEffort"),
            "Deployment/shop/web:sidecar": (16, "medium", "no memory limit"),
            "CronJob/report:report": (42, "medium", "BestEffort"),
            "Pod/worker:worker": (57, "low", "no CPU request"),
        },
        "docker-compose.yml": {
            "service/web": (10, "low", "no memory or CPU limit"),
            "service/worker": (14, "low", "no memory limit"),
        },
        "taskdef-ec2.json": {
            "task/orders:api": (7, "medium", "memoryReservation is only a soft limit"),
            "task/orders:log-router": (13, "low", "no CPU units"),
        },
        "ecs-service.yaml": {
            "task/ApiTask:api": (12, "medium", "no hard memory limit"),
        },
    }

    def test_unbounded_containers_are_flagged_with_exact_lines(self):
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


class Inf08NegativeTests(unittest.TestCase):
    """INF08-02: bounded containers, Fargate task sizes and non-workload objects are clean."""

    def test_bounded_manifests_are_clean(self):
        _, result = run("k8s-negative.yaml", "taskdef-fargate.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:k8s-negative.yaml", "file:taskdef-fargate.json"])
        self.assertEqual(result["findings"], [])

    def test_bounded_compose_services_and_merge_keys_are_clean(self):
        _, result = run("docker-compose.yml")
        self.assertNotIn("service/db", findings_for(result, "docker-compose.yml"))
        self.assertNotIn("service/cache", findings_for(result, "docker-compose.yml"))  # limits via `<<: *limits`

    def test_missing_cpu_limit_alone_is_not_flagged(self):
        content = (
            "apiVersion: v1\nkind: Pod\nmetadata:\n  name: p\nspec:\n  containers:\n  - name: c\n    image: x\n"
            "    resources:\n      requests: {cpu: 100m, memory: 64Mi}\n      limits: {memory: 64Mi}\n"
        )
        _, result = run_inline("pod.yaml", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Inf08ExceptionTests(unittest.TestCase):
    """INF08-03: same-namespace LimitRange defaults, linter exemptions, noqa and dev Compose files."""

    def test_limit_range_exemptions_and_noqa(self):
        _, result = run("k8s-exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        # The LimitRange covers namespace team-a only, so team-b is still flagged.
        self.assertEqual([f["identity"] for f in result["findings"]], ["Deployment/team-b/other-namespace:app"])

    def test_development_compose_files_are_not_evaluated(self):
        for name in ("docker-compose.override.yml", "compose.dev.yaml", "test/docker-compose.yml"):
            with self.subTest(name=name):
                _, result = run_inline(name, "services:\n  web:\n    image: x\n")
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("development/test", result["coverage"]["limitations"][0])


class Inf08IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF08-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:k8s/deployment.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_templates_malformed_and_unsupported_files_make_result_partial(self):
        """INF08-05: Helm templates, invalid JSON, non-manifests and other files are never reported clean."""
        _, result = run("k8s-positive.yaml", "helm-deployment.yaml", "broken.json", "workflow.yml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:k8s-positive.yaml"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:helm-deployment.yaml: could not be parsed (Go/Helm template syntax on line 4)", limitations)
        self.assertIn("file:broken.json: could not be parsed (invalid JSON)", limitations)
        self.assertIn("file:workflow.yml: not evaluated (no Kubernetes objects", limitations)

    def test_unsupported_extensions_and_unparseable_yaml_are_unavailable(self):
        for name, content, reason in (
            ("values.toml", "a = 1\n", "unsupported file type"),
            ("deploy.yaml", "kind: Pod\n\tapiVersion: v1\n", "tab indentation"),
            ("deploy.yaml", "kind: [Pod\n", "unbalanced flow collection"),
            ("deploy.json", '{"kind": "Pod", "apiVersion": "v1"}', "unsupported file type"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])


class Inf08BoundaryTests(unittest.TestCase):
    """INF08-06: identities are kind/name/container; repeats get #n; line movement keeps fingerprints."""

    def test_identities_distinguish_kinds_and_repeat_suffixes(self):
        _, result = run("boundary.yaml")
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            ["Deployment/api:app", "StatefulSet/api:app", "Deployment/api:app#2"],
        )

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("k8s-positive.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "k8s-positive.yaml").read_text()
        _, after = run_inline("k8s-positive.yaml", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class MiniYamlTests(unittest.TestCase):
    def test_subset_matches_expected_structure(self):
        text = (
            "%YAML 1.2\n---\n"
            "base: &base {a: 1, b: [x, 'y z']}\n"
            "merged:\n  <<: *base\n  b: override\n"
            "script: |\n  echo one\n  echo two\n"
            "quoted: \"line\\u00e9\"  # comment\n"
            "items:\n- name: first\n  tags: !!str 3\n- second\n"
            "ref: !Ref Param\n"
            "github: echo ${{ github.sha }}\n"
            "...\n"
        )
        [doc] = miniyaml.load_all(text)
        self.assertEqual(miniyaml.to_python(doc), {
            "base": {"a": "1", "b": ["x", "y z"]},
            "merged": {"a": "1", "b": "override"},
            "script": "echo one\necho two",
            "quoted": "lineé",
            "items": [{"name": "first", "tags": "3"}, "second"],
            "ref": "Param",
            "github": "echo ${{ github.sha }}",
        })
        self.assertEqual(doc.items["items"].items[0].line, 12)
        self.assertEqual(doc.key_lines["ref"], 15)

    def test_json_documents_keep_line_numbers(self):
        [doc] = miniyaml.load_all('{\n  "a": [\n    {"name": "x"},\n    {\n      "name": "y"\n    }\n  ]\n}\n')
        first, second = doc.items["a"].items
        self.assertEqual((first.key_lines["name"], second.key_lines["name"]), (3, 5))

    def test_outside_subset_is_rejected(self):
        for text in ("? complex\n: key\n", "a: *missing\n", "a: 'open\n", "image: {{ .Values.image }}\n"):
            with self.subTest(text=text), self.assertRaises(miniyaml.YamlError):
                miniyaml.load_all(text)


class Inf08ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:k8s-positive.yaml", "Deployment/shop/web:app")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("k8s-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "        - name: invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("k8s-positive.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("k8s-positive.yaml", "docker-compose.yml", "taskdef-ec2.json")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "inf08-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("k8s-positive.yaml"))


class Inf08CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf08_result(self):
        input_path = FIXTURES / "inf08-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 4)


if __name__ == "__main__":
    unittest.main()
