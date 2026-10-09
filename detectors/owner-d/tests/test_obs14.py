"""Behavioral tests for the OBS-14 detector (issue #238)."""

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

from owner_d import cli, obs14
from owner_d.obs14 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs14"
REPOSITORY_ID = "github:AWS-env/example"
HONEYCOMB = "OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io\n"


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
        "scan_id": "scan-obs14-001",
        "commit_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "telemetry-config"} if context is None else context,
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_inline(name, content, context=None):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"], context=context)


def identities(result):
    return [f["identity"] for f in result["findings"]]


def confidences(result):
    return [f["confidence"] for f in result["findings"]]


class Obs14PositiveTests(unittest.TestCase):
    """OBS14-01: unreduced non-production telemetry to managed backends is flagged with exact evidence."""

    ENV_KEY = "spec.template.spec.containers.env."
    EXPECTED = {
        "overlays/staging/deployment.yaml": {
            f"nonprod-traces:{ENV_KEY}OTEL_EXPORTER_OTLP_ENDPOINT": (15, 16, "medium", "OTEL_TRACES_SAMPLER=always_on"),
            f"nonprod-logs:{ENV_KEY}OTEL_LOGS_EXPORTER": (17, 18, "medium", "LOG_LEVEL enables DEBUG logging"),
        },
        "docker-compose.qa.yml": {
            "nonprod-traces:services.api.environment.OTEL_EXPORTER_OTLP_ENDPOINT": (
                6, 6, "low", "sets no OTEL_TRACES_SAMPLER"),
            "nonprod-traces:services.worker.environment.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": (
                14, 14, "medium", "a sampling ratio of 100%"),
        },
        "config/.env.development": {
            "nonprod-traces:OTEL_EXPORTER_OTLP_ENDPOINT": (4, 4, "low", "(development, from the file path)"),
        },
        "collector/otel-collector.yaml": {
            "pipeline/traces:nonprod-export": (23, 26, "low", "every span it receives to awsxray"),
            "pipeline/logs:nonprod-export": (
                27, 30, "low", "otlphttp/grafana (otlp-gateway-prod-us-east-0.grafana.net)"),
        },
        "infra/stg/xray-sampling.yaml": {
            "xray-sampling-rule:catch-all": (11, 11, "medium", "FixedRate=1"),
        },
        "config/staging/sampling-rules.json": {
            "xray-sampling-rule:*:*:*:/checkout/*": (10, 10, "medium", "rate=1.0"),
        },
    }

    def test_nonprod_exports_are_flagged_with_exact_lines(self):
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
                    self.assertEqual(
                        finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity)
                    )
                    [evidence] = finding["evidence"]
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["line_start"], start)
                    self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                    self.assertTrue(finding["references"])

    def test_environment_source_and_attribute_note(self):
        _, result = run("docker-compose.qa.yml", "collector/otel-collector.yaml")
        by_id = {f["identity"]: f["summary"] for f in result["findings"]}
        api = by_id["nonprod-traces:services.api.environment.OTEL_EXPORTER_OTLP_ENDPOINT"]
        worker = by_id["nonprod-traces:services.worker.environment.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        self.assertIn("(qa, from the file path)", api)
        self.assertIn("No deployment.environment.name resource attribute", api)
        self.assertIn("(qa, from its deployment.environment attribute)", worker)
        self.assertNotIn("No deployment.environment.name", worker)
        self.assertIn("(uat, from its deployment.environment attribute)", by_id["pipeline/traces:nonprod-export"])

    def test_recommendations_match_the_rule(self):
        _, result = run("overlays/staging/deployment.yaml", "collector/otel-collector.yaml",
                        "infra/stg/xray-sampling.yaml")
        by_id = {f["identity"].split(":", 1)[0]: f["recommendation"] for f in result["findings"]}
        self.assertIn("parentbased_traceidratio", by_id["nonprod-traces"])
        self.assertIn("INFO", by_id["nonprod-logs"])
        self.assertIn("probabilistic_sampler", by_id["pipeline/traces"])
        self.assertIn("FixedRate", by_id["xray-sampling-rule"])


class Obs14NegativeTests(unittest.TestCase):
    """OBS14-02: sampled, in-house, exporter-off and filtered staging telemetry is clean."""

    def test_reduced_or_unmanaged_staging_telemetry_is_clean(self):
        names = ["deploy/staging/deployment.yaml", "deploy/staging/otel-collector.yaml",
                 "deploy/staging/xray-sampling.yaml"]
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["findings"], [])

    def test_default_endpoint_is_local(self):
        _, result = run_inline("deploy/staging/.env", "OTEL_TRACES_SAMPLER=always_on\nOTEL_LOGS_EXPORTER=otlp\n")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs14ExceptionTests(unittest.TestCase):
    """OBS14-03: noqa, a production attribute, a disabled SDK, loopback/connector/undefined exporters and
    files outside the non-production claim."""

    def test_compose_exceptions_and_noqa(self):
        _, result = run("deploy/uat/docker-compose.yml")
        self.assertEqual(result["status"], "completed")
        # Only the endpoint whose noqa names another rule (E501) is still flagged.
        self.assertEqual(identities(result),
                         ["nonprod-traces:services.other-rule.environment.OTEL_EXPORTER_OTLP_ENDPOINT"])

    def test_collector_exceptions_and_noqa(self):
        content = (FIXTURES / "deploy/uat/otel-collector.yaml").read_text()
        _, result = run_inline("deploy/uat/otel-collector.yaml", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        _, unsuppressed = run_inline("deploy/uat/otel-collector.yaml", content.replace("  # noqa: OBS-14", ""))
        self.assertEqual(identities(unsuppressed), ["pipeline/logs/suppressed:nonprod-export"])

    def test_files_outside_the_nonprod_claim_are_not_evaluated(self):
        content = (FIXTURES / "overlays/staging/deployment.yaml").read_text()
        for name, reason in (
            ("overlays/production/deployment.yaml", "no non-production marker"),
            ("k8s/deployment.yaml", "no non-production marker"),
            ("deploy/prod-staging/deployment.yaml", "no non-production marker"),
            ("examples/staging/deployment.yaml", "(examples)"),
            ("tests/staging/deployment.yaml", "(tests)"),
            ("docs/staging/deployment.yaml", "(docs)"),
            ("app/testdata/staging.yaml", "(testdata)"),
            (".github/workflows/staging.yml", "(ci)"),
            ("deploy/staging/deployment.example.yaml", "(example)"),
        ):
            with self.subTest(name=name):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_production_attribute_overrides_a_staging_path(self):
        content = HONEYCOMB + "OTEL_TRACES_SAMPLER=always_on\n"
        attribute = "OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name="
        _, result = run_inline("deploy/staging/.env", content + attribute + "production\n")
        self.assertEqual(result["status"], "unavailable")
        _, result = run_inline("k8s/.env", content + attribute + "staging\n")
        self.assertEqual(confidences(result), ["medium"])


class Obs14IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS14-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:overlays/staging/deployment.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_inputs_are_never_reported_clean(self):
        """OBS14-05: templates, tabs, broken JSON and TOML inline tables give partial/unavailable."""
        endpoint = "      - name: OTEL_EXPORTER_OTLP_ENDPOINT\n"
        for name, content, reason in (
            ("deploy/staging/deployment.yaml", "env:\n" + endpoint + "        value: {{ .Values.endpoint }}\n",
             "Go/Helm template"),
            ("deploy/staging/deployment.yaml", "env:\n\t- name: OTEL_EXPORTER_OTLP_ENDPOINT\n", "tab"),
            ("config/staging/sampling-rules.json", '{"rules": [{"fixed_target": 1, "rate": 1}', "could not be parsed"),
            ("deploy/staging/otel.toml", 'env = { OTEL_TRACES_SAMPLER = "always_on" }\n', "inline table"),
            ("deploy/staging/notes.yaml", "# OTEL_EXPORTER_OTLP_ENDPOINT is set by the platform\nname: x\n",
             "no OpenTelemetry exporter/sampler settings"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

        _, result = run("overlays/staging/deployment.yaml", "deploy/staging/broken.yaml",
                        sources=[static_source("overlays/staging/deployment.yaml"),
                                 static_source("deploy/staging/broken.yaml", "a: [\n" + HONEYCOMB)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:overlays/staging/deployment.yaml"])

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        """The scan worker offers every repository file to parse(); only telemetry configs may be selected."""
        for name, content in (
            ("deploy/staging/deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"),
            ("app/tracing.py", 'os.environ["OTEL_TRACES_SAMPLER"] = "always_on"\n'),
            ("README.md", HONEYCOMB),
            ("deploy/staging/main.tf", HONEYCOMB),
        ):
            with self.subTest(name=name), self.assertRaises(obs14.Unsupported):
                obs14.parse(name, content)


class Obs14BoundaryTests(unittest.TestCase):
    """OBS14-06: sampler ratios, endpoint hosts, environment tokens, context and identity stability."""

    def test_sampler_ratio_boundary(self):
        for sampler, flagged in (
            ("OTEL_TRACES_SAMPLER=traceidratio\nOTEL_TRACES_SAMPLER_ARG=1\n", "medium"),
            ('OTEL_TRACES_SAMPLER=parentbased_traceidratio\nOTEL_TRACES_SAMPLER_ARG="1.0"\n', "medium"),
            ("OTEL_TRACES_SAMPLER=traceidratio\nOTEL_TRACES_SAMPLER_ARG=0.99\n", None),
            ("OTEL_TRACES_SAMPLER=traceidratio\n", "low"),
            ("OTEL_TRACES_SAMPLER=parentbased_always_on\n", "medium"),
            ("OTEL_TRACES_SAMPLER=always_off\n", None),
            ("OTEL_TRACES_SAMPLER=${SAMPLER}\n", None),
            ("", "low"),
        ):
            with self.subTest(sampler=sampler):
                _, result = run_inline("deploy/qa/.env", HONEYCOMB + sampler)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(confidences(result), [flagged] if flagged else [])

    def test_endpoint_hosts(self):
        for endpoint, flagged in (
            ("https://api.eu1.honeycomb.io:443", True),
            ("https://xray.ap-south-1.amazonaws.com/v1/traces", True),
            ("${OTLP_ENDPOINT:-https://otlp.nr-data.net}", True),
            ("https://honeycomb.io.example.com", False),
            ("https://nothoneycomb.io", False),
            ("http://otel-collector:4317", False),
            ("localhost:4317", False),
            ("${OTLP_ENDPOINT}", False),
        ):
            with self.subTest(endpoint=endpoint):
                content = f"OTEL_EXPORTER_OTLP_ENDPOINT={endpoint}\nOTEL_TRACES_SAMPLER=always_on\n"
                _, result = run_inline("deploy/qa/.env", content)
                self.assertEqual(len(result["findings"]), 1 if flagged else 0)

    def test_signal_specific_endpoint_wins(self):
        content = HONEYCOMB + "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces\n"
        _, result = run_inline("deploy/qa/.env", content)
        self.assertEqual(result["findings"], [])

    def test_environment_tokens_and_volume(self):
        content = HONEYCOMB + "OTEL_TRACES_SAMPLER=always_on\n"
        for name, expected in (
            ("deploy/pre-prod/.env", "medium"),
            ("deploy/non-production/.env", "medium"),
            ("envs/uat.env", "medium"),
            ("deploy/stage/.env", "medium"),
            ("deploy/dev/.env", "low"),
            ("deploy/local/.env", "low"),
            ("deploy/prod/.env", None),
            ("deploy/live/.env", None),
            ("deploy/development-production/.env", None),
        ):
            with self.subTest(name=name):
                _, result = run_inline(name, content)
                if expected:
                    self.assertEqual(confidences(result), [expected])
                else:
                    self.assertEqual(result["status"], "unavailable")

    def test_key_names_mark_the_environment(self):
        compose = ("services:\n  api-staging:\n    environment:\n      OTEL_EXPORTER_OTLP_ENDPOINT: "
                   "https://api.honeycomb.io\n      OTEL_TRACES_SAMPLER: always_on\n  api:\n    environment:\n"
                   "      OTEL_EXPORTER_OTLP_ENDPOINT: https://api.honeycomb.io\n")
        _, result = run_inline("docker-compose.yml", compose)
        self.assertEqual(identities(result),
                         ["nonprod-traces:services.api-staging.environment.OTEL_EXPORTER_OTLP_ENDPOINT"])
        self.assertIn("from its key names", result["findings"][0]["summary"])
        dockerfile = "FROM python:3.12 AS staging\nENV OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io\n"
        _, result = run_inline("Dockerfile", dockerfile)
        self.assertEqual(confidences(result), ["low"])

    def test_context_environment(self):
        content = HONEYCOMB + "OTEL_TRACES_SAMPLER=always_on\n"
        _, result = run_inline("k8s/.env", content, context={"environment": "staging"})
        self.assertEqual(confidences(result), ["medium"])
        self.assertIn("from the scan context", result["findings"][0]["summary"])
        for declared in ("production", "", "perf"):
            with self.subTest(declared=declared):
                _, result = run_inline("k8s/.env", content, context={"environment": declared})
                self.assertEqual(result["status"], "unavailable")
        _, result = run_inline("deploy/prod/.env", content, context={"environment": "staging"})
        self.assertEqual(result["status"], "unavailable")
        with self.assertRaises(EvaluationError):
            evaluate(make_input("config/.env.development", context={"environment": ["staging"]}))

    def test_repeats_get_suffixes_and_fingerprints_ignore_lines(self):
        name = "overlays/staging/deployment.yaml"
        content = (FIXTURES / name).read_text()
        container = content[content.index("        - name: checkout"):]
        _, result = run_inline(name, content + container)
        self.assertEqual(len(identities(result)), 4)
        self.assertTrue(identities(result)[2].endswith("#2"))
        _, before = run(name)
        _, after = run_inline(name, "# moved\n\n\n" + content)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual([f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
                         [f["evidence"][0]["line_start"] for f in after["findings"]])


class Obs14ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:docker-compose.qa.yml", "nonprod-traces:OTEL_EXPORTER_OTLP_ENDPOINT")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("docker-compose.qa.yml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "      OTEL_TRACES_SAMPLER: invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("docker-compose.qa.yml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("overlays/staging/deployment.yaml", "collector/otel-collector.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs14-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("overlays/staging/deployment.yaml", "docker-compose.qa.yml"))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs14)


class Obs14CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs14_result(self):
        input_path = FIXTURES / "obs14-01-positive-input.json"
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
