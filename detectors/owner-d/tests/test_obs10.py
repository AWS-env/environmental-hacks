"""Behavioral tests for the OBS-10 detector (issue #234)."""

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

from owner_d import cli, obs10
from owner_d.obs10 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs10"
REPOSITORY_ID = "github:AWS-env/example"
POSITIVE = ("agent.yaml", "gateway.yaml", "relay-configmap.yaml", "index.tf")
AGENT_LOGS = "OpenTelemetryCollector/node-agent:pipeline/logs:exporter/otlp/gateway:filtered-downstream"
AGENT_TRACES = "OpenTelemetryCollector/node-agent:pipeline/traces:exporter/otlp/gateway:filtered-downstream"
RELAY_LOGS = "ConfigMap/edge-relay:relay.yaml:pipeline/logs:exporter/otlp:filtered-downstream"
DROP_DEBUG = "datadog_logs_index.main:exclusion_filter/drop-debug"
HEALTH = "datadog_logs_index.main:exclusion_filter/health-checks"


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
        "scan_id": "scan-obs10-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "otel-config+terraform"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, extra=()):
    """Evaluate fixture files plus inline (name, content) sources."""
    sources = [static_source(name) for name in names] + [static_source(name, content) for name, content in extra]
    payload = make_input(sources=sources, scope=[source["scope_id"] for source in sources])
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def identities(result):
    return [f["identity"] for f in result["findings"]]


def limitations(result):
    return " ".join(result["coverage"]["limitations"])


def agent(endpoint="gateway:4317", processors="[batch]", receivers="[otlp]", extra=""):
    """A plain one-pipeline logs agent; `extra` is YAML indented under `processors:`."""
    body = "".join(f"  {line}\n" for line in extra.splitlines())
    return (
        "receivers:\n  otlp:\n    protocols:\n      grpc:\n  filelog:\n    include: [/var/log/app.log]\n"
        "processors:\n  batch:\n" + body
        + f"exporters:\n  otlp:\n    endpoint: {endpoint}\nservice:\n  pipelines:\n    logs:\n"
        f"      receivers: {receivers}\n      processors: {processors}\n      exporters: [otlp]\n"
    )


DROP = "filter/drop:\n  logs:\n    log_record:\n      - 'severity_number < SEVERITY_NUMBER_WARN'\n"


def gateway(processors="[filter/drop, batch]", extra=DROP, pipelines=""):
    """A plain logs gateway fed by otlp; `pipelines` adds YAML under `service.pipelines`."""
    body = "".join(f"  {line}\n" for line in extra.splitlines())
    return (
        "receivers:\n  otlp:\n    protocols:\n      grpc:\nprocessors:\n  batch:\n" + body
        + "exporters:\n  otlphttp:\n    endpoint: https://logs.vendor.example.com\n  awss3:\n    s3uploader:\n"
        "      s3_bucket: audit\nservice:\n  pipelines:\n    logs:\n      receivers: [otlp]\n"
        f"      processors: {processors}\n      exporters: [otlphttp]\n" + pipelines
    )


def tf_index(exclusion, prefix=""):
    return (f'{prefix}resource "datadog_logs_index" "main" {{\n  name = "main"\n  filter {{\n    query = "*"\n  }}\n'
            f"{exclusion}}}\n")


def exclusion(rate="1.0", enabled="true"):
    return (f'  exclusion_filter {{\n    name       = "drop"\n    is_enabled = {enabled}\n    filter {{\n'
            f'      query       = "status:debug"\n      sample_rate = {rate}\n    }}\n  }}\n')


class Obs10PositiveTests(unittest.TestCase):
    """OBS10-01: agents forwarding everything to a filtering gateway and 100% index exclusions are flagged."""

    EXPECTED = {
        AGENT_LOGS: ("agent.yaml", 33, 36, "low"),
        AGENT_TRACES: ("agent.yaml", 37, 40, "low"),
        RELAY_LOGS: ("relay-configmap.yaml", 18, 21, "low"),
        DROP_DEBUG: ("index.tf", 8, 15, "medium"),
        HEALTH: ("index.tf", 35, 39, "medium"),
    }

    def test_findings_have_exact_evidence_and_coverage(self):
        _, result = run(*POSITIVE)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in POSITIVE])
        self.assertEqual(result["measurements"], [])
        self.assertEqual(sorted(identities(result)), sorted(self.EXPECTED))
        for finding in result["findings"]:
            name, start, end, confidence = self.EXPECTED[finding["identity"]]
            lines = (FIXTURES / name).read_text().splitlines()
            with self.subTest(identity=finding["identity"]):
                self.assertEqual(finding["scope_id"], f"file:{name}")
                self.assertEqual(finding["confidence"], confidence)
                self.assertEqual(finding["fingerprint"],
                                 fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", finding["identity"]))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["line_start"], start)
                self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                self.assertTrue(finding["references"])

    def test_summaries_name_the_gateway_filter_and_the_billing_reason(self):
        _, result = run(*POSITIVE)
        by_id = {f["identity"]: f for f in result["findings"]}
        logs = by_id[AGENT_LOGS]["summary"]
        self.assertIn("to otel-gateway-collector", logs)
        self.assertIn("gateway.yaml (OpenTelemetryCollector/otel-gateway): 'logs' (filter/debug-logs)", logs)
        self.assertIn("runs after k8sattributes", logs)
        self.assertNotIn("runs after", by_id[AGENT_TRACES]["summary"])
        self.assertIn("'traces' (filter/health)", by_id[AGENT_TRACES]["summary"])
        self.assertIn("to otel-gateway,", by_id[RELAY_LOGS]["summary"])
        self.assertIn("filter` processor", by_id[AGENT_LOGS]["recommendation"])
        debug = by_id[DROP_DEBUG]
        self.assertIn("'status:debug'", debug["summary"])
        self.assertIn("still ingested and billed for ingestion", debug["summary"])
        self.assertIn("exclude_at_match", debug["recommendation"])
        self.assertIn("no literal query", by_id[HEALTH]["summary"])

    def test_plain_gateway_files_match_by_stem_and_directory(self):
        for name, endpoint in (("otel/gateway-config.yaml", "http://gateway:4317"),
                               ("deploy/gateway/config.yaml", "gateway.observability:4317"),
                               ("logs-gateway.yml", "https://logs-gateway.internal.example.com:4318")):
            with self.subTest(name=name):
                _, result = run(extra=[("agent.yaml", agent(endpoint)), (name, gateway())])
                self.assertEqual(identities(result), ["pipeline/logs:exporter/otlp:filtered-downstream"])
                self.assertEqual(result["findings"][0]["scope_id"], "file:agent.yaml")


class Obs10NegativeTests(unittest.TestCase):
    """OBS10-02: source-side filtering, gateways that keep data and inactive exclusions are clean."""

    def test_agent_side_reducers_are_clean(self):
        operator = ("receivers:\n  filelog:\n    include: [/var/log/app.log]\n    operators:\n      - type: filter\n"
                    "        expr: 'body matches \"DEBUG\"'\n")
        for processors, extra, receivers, label in (
            ("[filter/drop, batch]", DROP, "[otlp]", "filter"),
            ("[probabilistic_sampler, batch]", "probabilistic_sampler:\n  sampling_percentage: 10\n", "[otlp]",
             "probabilistic_sampler"),
            ("[tail_sampling]", "tail_sampling:\n  policies: []\n", "[otlp]", "tail_sampling"),
        ):
            with self.subTest(reducer=label):
                _, result = run(extra=[("agent.yaml", agent(processors=processors, extra=extra,
                                                            receivers=receivers)),
                                       ("gateway.yaml", gateway())])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
        with_operator = agent(receivers="[filelog]").replace(
            "receivers:\n  otlp:\n    protocols:\n      grpc:\n  filelog:\n    include: [/var/log/app.log]\n", operator)
        _, result = run(extra=[("agent.yaml", with_operator), ("gateway.yaml", gateway())])
        self.assertEqual(result["findings"], [])
        self.assertEqual(identities(run(extra=[("agent.yaml", agent(receivers="[filelog]")),
                                               ("gateway.yaml", gateway())])[1]),
                         ["pipeline/logs:exporter/otlp:filtered-downstream"])

    def test_gateway_without_a_drop_is_clean(self):
        for processors, extra in (("[batch]", ""), ("[filter/empty, batch]", "filter/empty:\n  error_mode: ignore\n")):
            with self.subTest(processors=processors):
                _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", gateway(processors, extra))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
        _, result = run("agent.yaml", "gateway.yaml")  # the agent metrics pipeline filters; the gateway does not
        self.assertNotIn("metrics", " ".join(identities(result)))

    def test_agent_exclude_at_match_rule_suppresses_index_findings(self):
        _, result = run("index.tf", "datadog-agent.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:index.tf", "file:datadog-agent.yaml"])
        self.assertEqual(result["findings"], [])
        self.assertIn("2 Datadog index exclusion filter(s) dropping 100% were not flagged: datadog-agent.yaml "
                      "declares a Datadog Agent exclude_at_match rule", limitations(result))

    def test_inactive_or_partial_exclusions_are_clean(self):
        for rate, enabled in (("0.99", "true"), ("1.0", "false"), ("var.rate", "true"), ("1.0", "var.enabled"),
                              ("0", "true")):
            with self.subTest(rate=rate, enabled=enabled):
                _, result = run(extra=[("dd.tf", tf_index(exclusion(rate, enabled)))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
        _, result = run(extra=[("dd.tf", tf_index(exclusion("1")))])
        self.assertEqual(identities(result), ["datadog_logs_index.main:exclusion_filter/drop"])


class Obs10ExceptionTests(unittest.TestCase):
    """OBS10-03: audit pipelines, archives, log-based metrics, noqa and development paths."""

    def test_gateway_audit_pipeline_keeping_the_full_stream_exempts_the_agent(self):
        audit = "    logs/audit:\n      receivers: [otlp]\n      processors: [batch]\n      exporters: [awss3]\n"
        _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", gateway(pipelines=audit))])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        debug_only = audit.replace("[awss3]", "[debug]")
        _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", gateway(pipelines=debug_only).replace(
            "  awss3:\n", "  debug:\n  awss3:\n"))])
        self.assertEqual(len(result["findings"]), 1)  # a debug-only pipeline keeps nothing

    def test_archive_exempts_and_metric_lowers_confidence(self):
        _, result = run("index.tf", "archive.tf")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn("archive.tf declares a datadog_logs_archive, so excluded logs are still archived",
                      limitations(result))
        metric = ('resource "datadog_logs_metric" "errors" {\n  name = "errors"\n  compute {\n'
                  '    aggregation_type = "count"\n  }\n  filter {\n    query = "status:debug"\n  }\n}\n')
        _, result = run("index.tf", extra=[("metrics.tf", metric)])
        self.assertEqual(sorted(identities(result)), [DROP_DEBUG, HEALTH])
        for finding in result["findings"]:
            self.assertEqual(finding["confidence"], "low")
            self.assertIn("metrics.tf declares a datadog_logs_metric", finding["summary"])

    def test_noqa_on_or_above_the_flagged_line(self):
        pipeline = "    logs:\n      receivers: [otlp]"
        for marker, flagged in (("    # noqa: OBS-10\n", False), ("    # noqa: OBS10\n", False),
                                ("    # noqa\n", False), ("    # noqa: E501\n", True)):
            with self.subTest(marker=marker):
                content = agent().replace(pipeline, marker + pipeline)
                _, result = run(extra=[("agent.yaml", content), ("gateway.yaml", gateway())])
                self.assertEqual(bool(result["findings"]), flagged)
        content = tf_index(exclusion().replace("  exclusion_filter {", "  # noqa: OBS-10\n  exclusion_filter {"))
        self.assertEqual(run(extra=[("dd.tf", content)])[1]["findings"], [])
        content = tf_index(exclusion().replace("  exclusion_filter {", "  exclusion_filter { # noqa: OBS-10"))
        self.assertEqual(run(extra=[("dd.tf", content)])[1]["findings"], [])

    def test_development_paths_are_not_evaluated(self):
        for name, content in (("dev/agent.yaml", agent()), ("terraform/test/dd.tf", tf_index(exclusion())),
                              ("local/datadog.yaml", (FIXTURES / "datadog-agent.yaml").read_text())):
            with self.subTest(name=name):
                _, result = run(extra=[(name, content)])
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("development/test", result["coverage"]["limitations"][0])
        # a dev agent rule does not count as source-side filtering for production indexes
        _, result = run("index.tf", extra=[("local/datadog.yaml", (FIXTURES / "datadog-agent.yaml").read_text())])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["findings"]), 2)


class Obs10IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_or_partial(self):
        """OBS10-04: requested scope without a static source is not evaluated."""
        payload = make_input(sources=[], scope=["file:otel/agent.yaml"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertIn("no static source", limitations(result))
        payload = make_input("agent.yaml", "gateway.yaml", scope=["file:agent.yaml", "file:gateway.yaml", "file:x.tf"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(sorted(identities(result)), [AGENT_LOGS, AGENT_TRACES])

    def test_context_only_payload_is_unavailable(self):
        """OBS10-04: an Agent rule alone has nothing to be judged against."""
        _, result = run("datadog-agent.yaml")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("supplies only source-side filtering context", result["coverage"]["limitations"][0])

    def test_malformed_inputs_are_never_clean(self):
        """OBS10-05: broken YAML/HCL next to valid files gives partial with the reason."""
        for name, content, reason in (
            ("otel.yaml", "exporters:\n\totlp: {}\nservice:\n  pipelines: {}\n", "tab indentation"),
            ("dd.tf", tf_index(exclusion()).rstrip("}\n") + "\n", "unbalanced"),
            ("dd.tf", tf_index(exclusion()) + "/* open comment\n", "unbalanced"),
            ("dd.tf", 'resource "datadog_logs_index" "x" { name = "x" } }\n', "unsupported block syntax"),
            ("otel.yaml", "exporters:\n  otlp: {}\nextensions:\n  pipelines: {}\n", "no OpenTelemetry Collector"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run("index.tf", extra=[(name, content)])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["file:index.tf"])
                self.assertIn(reason, limitations(result))

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        for name, content in (
            ("main.tf", 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'),
            ("k8s/deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\n"),
            ("otel.json", '{"exporters": {}, "service": {"pipelines": {}}}'),
            ("README.md", "exporters:\npipelines:\ndatadog_logs_index\n"),
        ):
            with self.subTest(name=name), self.assertRaises(obs10.Unsupported):
                obs10.parse(name, content)


class Obs10BoundaryTests(unittest.TestCase):
    """OBS10-06: endpoint matching, unknown gateway filters, HCL syntax and stable fingerprints."""

    def test_endpoints_that_do_not_name_a_payload_gateway_are_not_judged(self):
        for endpoint in ("other-gateway:4317", "localhost:4317", "${env:GATEWAY_ENDPOINT}", "127.0.0.1:4317",
                         "https://logs.vendor.example.com"):
            with self.subTest(endpoint=endpoint):
                _, result = run(extra=[("agent.yaml", agent(endpoint)), ("gateway.yaml", gateway())])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])

    def test_unknown_gateway_filters_are_not_judged(self):
        for processors, extra in (("[filter/remote, batch]", ""),
                                  ("[filter/env]", "filter/env:\n  logs:\n    log_record:\n      - ${env:DROP}\n"),
                                  ("${env:PROCESSORS}", DROP)):
            with self.subTest(processors=processors):
                _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", gateway(processors, extra))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])

    def test_gateway_routing_to_a_connector_is_not_judged(self):
        routed = gateway().replace("exporters: [otlphttp]", "exporters: [forward]").replace(
            "service:", "connectors:\n  forward:\nservice:")
        _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", routed)])
        self.assertEqual(result["findings"], [])

    def test_signals_must_match(self):
        traces_only = gateway().replace("    logs:\n      receivers", "    traces:\n      receivers")
        _, result = run(extra=[("agent.yaml", agent()), ("gateway.yaml", traces_only)])
        self.assertEqual(result["findings"], [])

    def test_hcl_strings_comments_and_one_line_blocks(self):
        tricky = (
            '  # exclusion_filter {\n  // is_enabled = true\n  tags = {\n    "a" = "}{"\n  }\n'
            '  description = <<-EOT\n    exclusion_filter {\n  EOT\n'
        )
        _, result = run(extra=[("dd.tf", tf_index(tricky + exclusion()))])
        self.assertEqual(identities(result), ["datadog_logs_index.main:exclusion_filter/drop"])
        unnamed = '  exclusion_filter {\n    is_enabled = true\n    filter { sample_rate = 1.0 }\n  }\n'
        _, result = run(extra=[("dd.tf", tf_index(unnamed + unnamed))])
        self.assertEqual(identities(result), ["datadog_logs_index.main:exclusion_filter/#1",
                                              "datadog_logs_index.main:exclusion_filter/#2"])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run(*POSITIVE)
        shifted = [(name, "# moved\n\n\n" + (FIXTURES / name).read_text()) for name in ("agent.yaml", "index.tf")]
        _, after = run("gateway.yaml", "relay-configmap.yaml", extra=shifted)
        key = lambda f: f["identity"]  # noqa: E731
        before_sorted, after_sorted = sorted(before["findings"], key=key), sorted(after["findings"], key=key)
        self.assertEqual([f["fingerprint"] for f in before_sorted], [f["fingerprint"] for f in after_sorted])
        for old, new in zip(before_sorted, after_sorted):
            shift = 0 if old["scope_id"] == "file:relay-configmap.yaml" else 3
            self.assertEqual(old["evidence"][0]["line_start"] + shift, new["evidence"][0]["line_start"])


class Obs10ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:index.tf", DROP_DEBUG)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run(*POSITIVE)
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "        logs/invented:"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("agent.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input(*POSITIVE)
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs10-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(*POSITIVE))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs10)


class Obs10CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs10_result(self):
        input_path = FIXTURES / "obs10-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(sorted(identities(result)), sorted(Obs10PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
