"""Behavioral tests for the OBS-09 detector (issue #233)."""

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

from owner_d import cli, obs09
from owner_d.obs09 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs09"
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
        "scan_id": "scan-obs09-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "otel-config"},
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


def collector(exporter_settings, pipeline="receivers: [otlp]\n      exporters: [otlp]"):
    """A one-exporter collector config; `exporter_settings` is indented under `otlp:`."""
    settings = "".join(f"    {line}\n" for line in exporter_settings.splitlines())
    return f"exporters:\n  otlp:\n{settings}service:\n  pipelines:\n    traces:\n      {pipeline}\n"


class Obs09PositiveTests(unittest.TestCase):
    """OBS09-01: unbatched pipelines, compression: none and SimpleSpanProcessor are flagged with exact evidence."""

    EXPECTED = {
        "positive.yaml": {
            "exporter/otlphttp:compression-none": (22, 22, "medium", "disables compression"),
            "pipeline/traces:unbatched": (30, 33, "medium", "exports to otlp/backend unbatched"),
            "pipeline/logs:unbatched": (34, 36, "medium", "it has no batch processor"),
            "pipeline/metrics:unbatched": (37, 42, "low", "endpoint is unresolved"),
        },
        "collector-cr.yaml": {
            "OpenTelemetryCollector/adot-gateway:exporter/otlp:compression-none": (16, 16, "medium", "non-loopback"),
            "OpenTelemetryCollector/agent:pipeline/logs:unbatched": (39, 41, "medium", "exports to otlphttp"),
        },
        "configmap.yaml": {
            "ConfigMap/otel-collector:relay:pipeline/traces:unbatched": (19, 21, "medium", "exports to otlp_http"),
        },
        "sdk_positive.py": {
            "<module>:SimpleSpanProcessor(OTLPSpanExporter)": (13, 13, "medium", "every span"),
            "configure_logging:SimpleLogRecordProcessor(OTLPLogExporter)": (21, 23, "medium", "every log record"),
        },
    }

    def test_unbatched_and_uncompressed_exports_are_flagged_with_exact_lines(self):
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

    def test_recommendations_match_the_rule(self):
        _, result = run("positive.yaml", "sdk_positive.py")
        by_id = {f["identity"]: f["recommendation"] for f in result["findings"]}
        self.assertIn("batch", by_id["pipeline/traces:unbatched"])
        self.assertIn("gzip", by_id["exporter/otlphttp:compression-none"])
        self.assertIn("BatchSpanProcessor", by_id["<module>:SimpleSpanProcessor(OTLPSpanExporter)"])


class Obs09NegativeTests(unittest.TestCase):
    """OBS09-02: batch processors, exporter-side batching, default/other compression and Batch*Processor are clean."""

    def test_batched_and_compressed_exports_are_clean(self):
        _, result = run("negative.yaml", "sdk_negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.yaml", "file:sdk_negative.py"])
        self.assertEqual(result["findings"], [])

    def test_unset_compression_is_not_flagged(self):
        _, result = run_inline("otel.yaml", collector("endpoint: backend.example.com:4317\nsending_queue:\n  batch:"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs09ExceptionTests(unittest.TestCase):
    """OBS09-03: debug/loopback/connector/fragment exceptions, noqa, console exporters and dev/test files."""

    def test_collector_exceptions_and_noqa(self):
        _, result = run("exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        # Only the exporter whose noqa names another rule (E501) is still flagged.
        self.assertEqual(identities(result), ["exporter/otlp/other:compression-none"])

    def test_sdk_exceptions_and_noqa(self):
        _, result = run("sdk_exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["other_code:SimpleSpanProcessor(OTLPSpanExporter)"])

    def test_development_and_test_files_are_not_evaluated(self):
        config = (FIXTURES / "positive.yaml").read_text()
        sdk = (FIXTURES / "sdk_positive.py").read_text()
        for name, content in (
            ("deploy/otel-collector-dev.yaml", config),
            ("local/otel.yaml", config),
            ("tests/otel.yml", config),
            ("collector/testdata/config.yaml", config),
            ("tests/test_tracing.py", sdk),
            ("app/conftest.py", sdk),
            ("app/debug_tracing.py", sdk),
        ):
            with self.subTest(name=name):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn("development/test", result["coverage"]["limitations"][0])


class Obs09IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS09-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:otel/collector.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_templates_and_invalid_python_make_result_partial(self):
        """OBS09-05: Helm templates and invalid Python are never reported clean."""
        _, result = run("positive.yaml", "helm-collector.yaml", "broken.py")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.yaml"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:helm-collector.yaml: could not be parsed (Go/Helm template syntax on line 4)", limitations)
        self.assertIn("file:broken.py: could not be parsed (invalid Python)", limitations)

    def test_unsupported_and_unparseable_inputs_are_unavailable(self):
        pipelines = "service:\n  pipelines:\n    traces:\n      exporters: [otlp]\n"
        broken_configmap = (
            "kind: ConfigMap\nmetadata:\n  name: c\ndata:\n  relay: |\n    exporters:\n      otlp: [\n"
            "    service:\n      pipelines: {}\n"
        )
        helm_values = "mode: deployment\nconfig:\n  exporters:\n    otlp: {}\n  " + pipelines.replace("\n", "\n  ")
        for name, content, reason in (
            ("otel.toml", "exporters = 1\n", "unsupported file type"),
            ("otel.json", '{"exporters": {}, "service": {"pipelines": {}}}', "unsupported file type"),
            ("tracing.py", "import logging\n", "unsupported file type"),
            ("otel.yaml", "exporters:\n\totlp: {}\n" + pipelines, "tab indentation"),
            ("otel.yaml", "exporters: [otlp\n" + pipelines, "unbalanced flow collection"),
            ("configmap.yaml", broken_configmap, "unbalanced flow collection"),
            ("values.yaml", helm_values, "Helm chart values"),
            ("otel.yaml", "exporters:\n  otlp: {}\nextensions:\n  pipelines: {}\n", "no OpenTelemetry Collector"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        """The scan worker offers every repository file to parse(); only OTel inputs may be selected."""
        for name, content in (
            ("k8s/deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"),
            ("alertmanager.yml", "receivers:\n  - name: team\nroute:\n  receiver: team\n"),
            ("app/main.py", "import os\nprint(os.getcwd())\n"),
            ("README.md", "exporters:\npipelines:\n"),
        ):
            with self.subTest(name=name), self.assertRaises(obs09.Unsupported):
                obs09.parse(name, content)


class Obs09BoundaryTests(unittest.TestCase):
    """OBS09-06: batch timeout 0 vs 1ms, loopback vs remote endpoints, repeat suffixes and stable fingerprints."""

    def test_zero_timeout_batch_is_unbatched_and_repeats_get_suffixes(self):
        _, result = run("boundary.yaml")
        self.assertEqual(identities(result), ["pipeline/traces:unbatched", "pipeline/traces:unbatched#2"])
        self.assertIn("timeout 0", result["findings"][0]["summary"])
        self.assertIn("no batch processor", result["findings"][1]["summary"])

    def test_loopback_endpoints_are_exempt_and_others_are_not(self):
        for endpoint, flagged in (
            ("localhost:4317", False),
            ("http://[::1]:4318", False),
            ("unix:///var/run/otel.sock", False),
            ("dns:///127.0.0.1:4317", False),
            ("0.0.0.0:4317", False),
            ("otel-agent:4317", True),
            ("https://10.0.0.5:4318", True),
            ("https://localhost.example.com", True),
        ):
            with self.subTest(endpoint=endpoint):
                content = collector(f"endpoint: {endpoint}\ncompression: none\nsending_queue:\n  batch: {{}}")
                _, result = run_inline("otel.yaml", content)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(identities(result), ["exporter/otlp:compression-none"] if flagged else [])

    def test_compression_values(self):
        for value, flagged in (('""', True), ("none", True), ("gzip", False), ("snappy", False),
                               ("${env:OTLP_COMPRESSION}", False), ("", False)):
            with self.subTest(value=value):
                content = collector(f"endpoint: backend.example.com:4317\ncompression: {value}\nsending_queue:\n"
                                    f"  batch: {{}}")
                _, result = run_inline("otel.yaml", content)
                self.assertEqual(identities(result), ["exporter/otlp:compression-none"] if flagged else [])

    def test_unused_exporter_and_unresolved_pipeline_lists_are_not_judged(self):
        unused = "exporters:\n  otlp:\n    endpoint: a.example.com:4317\n    compression: none\n" \
                 "service:\n  pipelines:\n    traces:\n      exporters: [debug]\n"
        unresolved = collector("endpoint: a.example.com:4317", "exporters: ${env:TRACE_EXPORTERS}")
        for content in (unused, unresolved):
            with self.subTest(content=content):
                _, result = run_inline("otel.yaml", content)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("positive.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "positive.yaml").read_text()
        _, after = run_inline("positive.yaml", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Obs09ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.yaml", "pipeline/traces:unbatched")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "    compression: invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.yaml", "collector-cr.yaml", "sdk_positive.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs09-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.yaml"))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs09)


class Obs09CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs09_result(self):
        input_path = FIXTURES / "obs09-01-positive-input.json"
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
