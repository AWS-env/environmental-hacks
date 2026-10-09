"""Behavioral tests for the OBS-12 detector (issue #236)."""

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

from owner_d import cli, obs12
from owner_d.obs12 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs12"
REPOSITORY_ID = "github:AWS-env/example"
PYTHON = "python:opentelemetry-instrument"
NODE = "node:auto-instrumentations-node/register"
GET_ALL = "node:getNodeAutoInstrumentations()"


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
        "scan_id": "scan-obs12-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "auto-instrumentation"},
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


def identities(result):
    return [f["identity"] for f in result["findings"]]


def container(command, env=""):
    """A one-container Deployment; `env` lines are indented under `env:`."""
    env_block = "          env:\n" + "".join(f"            {line}\n" for line in env.splitlines()) if env else ""
    return (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\nspec:\n  template:\n    spec:\n"
        f"      containers:\n        - name: app\n          command: {command}\n{env_block}"
    )


class Obs12PositiveTests(unittest.TestCase):
    """OBS12-01: default-selection launches in Kubernetes, Compose, Dockerfiles, npm scripts and setup code."""

    EXPECTED = {
        "deployment.yaml": {
            f"Deployment/orders:{PYTHON}": (12, 12, "medium", "Deployment/orders starts"),
            f"Deployment/web:{NODE}": (37, 37, "medium", "all ~45 bundled instrumentations"),
        },
        "compose.yaml": {
            f"service/api:{NODE}": (6, 6, "medium", "service/api starts"),
            f"service/worker:{PYTHON}": (10, 10, "medium", "every installed package"),
        },
        "Dockerfile": {
            f"CMD:{PYTHON}": (10, 11, "low", "The image's CMD"),
        },
        "tracing.js": {
            GET_ALL: (8, 8, "low", "`getNodeAutoInstrumentations()` enables all"),
        },
        # start:dev and test are development scripts and are not judged.
        "package.json": {
            f"scripts/start:{NODE}": (6, 6, "low", "The 'start' npm script"),
        },
    }

    def test_default_selection_launches_are_flagged_with_exact_lines(self):
        names = list(self.EXPECTED)
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["measurements"], [])
        for name, expected in self.EXPECTED.items():
            findings = {f["identity"]: f for f in result["findings"] if f["scope_id"] == f"file:{name}"}
            self.assertEqual(set(findings), set(expected), name)
            lines = (FIXTURES / name).read_text().splitlines()
            for identity, (start, end, confidence, phrase) in expected.items():
                finding = findings[identity]
                with self.subTest(name=name, identity=identity):
                    self.assertEqual(finding["confidence"], confidence)
                    self.assertIn(phrase, finding["summary"])
                    self.assertIn("no instrumentation selection", finding["summary"])
                    self.assertEqual(
                        finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity)
                    )
                    [evidence] = finding["evidence"]
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["line_start"], start)
                    self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                    self.assertTrue(finding["references"])

    def test_recommendations_match_the_language(self):
        _, result = run("deployment.yaml", "tracing.js")
        by_id = {f["identity"]: f["recommendation"] for f in result["findings"]}
        self.assertIn("OTEL_PYTHON_DISABLED_INSTRUMENTATIONS", by_id[f"Deployment/orders:{PYTHON}"])
        self.assertIn("OTEL_NODE_ENABLED_INSTRUMENTATIONS", by_id[f"Deployment/web:{NODE}"])
        self.assertIn("enabled: false", by_id[GET_ALL])


class Obs12NegativeTests(unittest.TestCase):
    """OBS12-02: the same launches with an instrumentation selection are clean."""

    def test_selected_launches_are_clean(self):
        names = ("negative.yaml", "Dockerfile.selected", "tracing-selected.ts")
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["findings"], [])

    def test_selection_in_npm_script_is_clean(self):
        content = (FIXTURES / "package.json").read_text().replace(
            '"start": "node', '"start": "OTEL_NODE_ENABLED_INSTRUMENTATIONS=http,pg node')
        _, result = run_inline("package.json", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_selection_in_setup_code_environment_is_clean(self):
        content = (FIXTURES / "tracing.js").read_text().replace(
            "sdk.start();", "process.env.OTEL_NODE_ENABLED_INSTRUMENTATIONS ??= 'http,express';\nsdk.start();")
        _, result = run_inline("tracing.js", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs12ExceptionTests(unittest.TestCase):
    """OBS12-03: external env sources, comments, package names, non-shipped stages, noqa and dev/test paths."""

    def test_yaml_exceptions_and_noqa(self):
        _, result = run("exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        # Only the launch whose noqa names another rule (E501) is still flagged.
        self.assertEqual(identities(result), [f"Deployment/other-rule:{PYTHON}"])

    def test_js_exceptions_and_noqa(self):
        _, result = run("exceptions.js")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), [GET_ALL])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 13)

    def test_dockerfile_exceptions_and_noqa(self):
        _, result = run("Dockerfile.exceptions")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        unsuppressed = (FIXTURES / "Dockerfile.exceptions").read_text().replace("# noqa: OBS-12", "# reviewed")
        _, result = run_inline("Dockerfile.exceptions", unsuppressed)
        self.assertEqual(identities(result), [f"ENV:{NODE}"])

    def test_development_test_and_ci_files_are_not_evaluated(self):
        manifest = (FIXTURES / "deployment.yaml").read_text()
        for name, content in (
            ("deploy/dev/deployment.yaml", manifest),
            ("contract-tests/images/app/Dockerfile", (FIXTURES / "Dockerfile").read_text()),
            ("docker-compose.test.yml", (FIXTURES / "compose.yaml").read_text()),
            ("Dockerfile.dev", (FIXTURES / "Dockerfile").read_text()),
            ("src/tracing.test.js", (FIXTURES / "tracing.js").read_text()),
            (".github/workflows/deploy.yml", manifest),
        ):
            with self.subTest(name=name):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn("development/test/CI", result["coverage"]["limitations"][0])


class Obs12IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS12-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:k8s/deployment.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_inputs_make_result_partial(self):
        """OBS12-05: templates, broken Dockerfiles and unbalanced calls are never reported clean."""
        helm = container('["opentelemetry-instrument", "python", "app.py"]').replace(
            "name: app\nspec", "name: {{ .Release.Name }}\nspec")
        broken_js = "import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';\n" \
                    "const all = getNodeAutoInstrumentations(\n"
        sources = [
            static_source("deployment.yaml"),
            static_source("chart/templates/deployment.yaml", helm),
            static_source("Dockerfile.broken", "FROM python:3.12-slim\nLAUNCH opentelemetry-instrument python\n"),
            static_source("tracing.mjs", broken_js),
            static_source("svc/package.json", '{"scripts": {"start": "node -r '
                                              '@opentelemetry/auto-instrumentations-node/register'),
        ]
        _, result = run(sources=sources, scope=[source["scope_id"] for source in sources])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:deployment.yaml"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:chart/templates/deployment.yaml: could not be parsed (Go/Helm template syntax on line 4)",
                      limitations)
        self.assertIn("file:Dockerfile.broken: could not be parsed (unknown instruction on line 2)", limitations)
        self.assertIn("file:tracing.mjs: could not be parsed (unbalanced parentheses in getNodeAutoInstrumentations( "
                      "call on line 2)", limitations)
        self.assertIn("file:svc/package.json: could not be parsed (invalid JSON)", limitations)

    def test_unsupported_and_unparseable_inputs_are_unavailable(self):
        launch = container('["opentelemetry-instrument", "python", "app.py"]')
        for name, content, reason in (
            ("task-definition.json", '{"command": ["opentelemetry-instrument", "python"]}', "unsupported file type"),
            ("otel.toml", 'command = "opentelemetry-instrument python app.py"\n', "unsupported file type"),
            ("deployment.yaml", launch.replace("      containers", "\tcontainers"), "tab indentation"),
            ("deployment.yaml", launch.replace('["opentelemetry', '["opentelemetry-instrument", ["opentelemetry'),
             "unbalanced flow collection"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        """The scan worker offers every repository file to parse(); only auto-instrumentation launches are selected."""
        for name, content in (
            ("k8s/deployment.yaml", container('["python", "app.py"]')),
            ("requirements.yaml", "packages:\n  - opentelemetry-instrumentation-flask\n"),
            ("Dockerfile", "FROM python:3.12-slim\nRUN pip install opentelemetry-instrumentation\n"),
            ("src/server.js", "const express = require('express');\n"),
            ("package.json", '{"scripts": {"start": "node index.js"}}'),
            ("src/otel.js", "const { getNodeAutoInstrumentations } = require('./local');\n"
                            "getNodeAutoInstrumentations();\n"),
            ("README.md", "Run `opentelemetry-instrument python app.py`.\n"),
        ):
            with self.subTest(name=name), self.assertRaises(obs12.Unsupported):
                obs12.parse(name, content)


class Obs12BoundaryTests(unittest.TestCase):
    """OBS12-06: per-document selection, launcher token boundaries, empty config objects and stable fingerprints."""

    def test_selection_covers_only_its_own_document(self):
        _, result = run("boundary.yaml")
        self.assertEqual(identities(result), [f"Deployment/unselected:{PYTHON}", f"Deployment/unselected:{PYTHON}#2"])
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [28, 38])

    def test_empty_config_object_is_flagged_and_disabling_config_is_not(self):
        _, result = run("boundary.mjs")
        self.assertEqual(identities(result), [GET_ALL, f"{GET_ALL}#2"])
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [4, 8])
        self.assertIn("getNodeAutoInstrumentations({})", result["findings"][0]["summary"])
        self.assertEqual(result["findings"][1]["evidence"][0]["value"],
                         "export const again = getNodeAutoInstrumentations(\n);")

    def test_launcher_token_boundaries(self):
        for command, flagged in (
            ('["opentelemetry-instrument", "python", "app.py"]', True),
            ('["/srv/.venv/bin/opentelemetry-instrument", "python", "app.py"]', True),
            ("opentelemetry-instrument --traces_exporter otlp python app.py", True),
            ('["opentelemetry-instrumentation", "python", "app.py"]', False),
            ('["opentelemetry-instrument.sh", "python", "app.py"]', False),
            ('["my-opentelemetry-instrument", "python", "app.py"]', False),
            ('["node", "-r", "@opentelemetry/auto-instrumentations-node/register", "app.js"]', True),
            ('["node", "-r", "@opentelemetry/auto-instrumentations-node", "app.js"]', False),
        ):
            with self.subTest(command=command):
                _, result = run_inline("deployment.yaml", container(command))
                # A file without a launcher is out of scope (unsupported), never a clean claim.
                self.assertEqual(result["status"], "completed" if flagged else "unavailable")
                self.assertEqual(len(result["findings"]), 1 if flagged else 0)
                if not flagged:
                    self.assertIn("unsupported file type", result["coverage"]["limitations"][0])

    def test_selection_variable_matches_only_its_language(self):
        python = '["opentelemetry-instrument", "python", "app.py"]'
        node_env = "- name: OTEL_NODE_DISABLED_INSTRUMENTATIONS\n  value: dns"
        python_env = "- name: OTEL_PYTHON_DISABLED_INSTRUMENTATIONS\n  value: sqlite3"
        for env, expected in ((node_env, [f"Deployment/app:{PYTHON}"]), (python_env, []),
                              ("# OTEL_PYTHON_DISABLED_INSTRUMENTATIONS: todo\n- name: X\n  value: y",
                               [f"Deployment/app:{PYTHON}"])):
            with self.subTest(env=env):
                _, result = run_inline("deployment.yaml", container(python, env))
                self.assertEqual(identities(result), expected)

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("deployment.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "deployment.yaml").read_text()
        _, after = run_inline("deployment.yaml", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Obs12ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:deployment.yaml", f"Deployment/orders:{PYTHON}")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("deployment.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "          command: [invented]"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("deployment.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("deployment.yaml", "compose.yaml", "Dockerfile", "tracing.js")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs12-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("deployment.yaml"))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs12)


class Obs12CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs12_result(self):
        input_path = FIXTURES / "obs12-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 2)


if __name__ == "__main__":
    unittest.main()
