"""Behavioral tests for the OBS-13 detector (issue #237)."""

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

from owner_d import cli, obs13
from owner_d.obs13 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs13"
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
        "scan_id": "scan-obs13-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "otel-config+k8s"},
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


def collector(processors="", pipeline_processors="[]"):
    """A one-pipeline gateway; `processors` is YAML indented under `processors:`."""
    body = "".join(f"  {line}\n" for line in processors.splitlines())
    return (
        "receivers:\n  otlp:\n    protocols:\n      grpc:\nprocessors:\n  batch:\n" + body
        + "exporters:\n  otlp:\n    endpoint: tempo.example.com:4317\nservice:\n  pipelines:\n    traces:\n"
        f"      receivers: [otlp]\n      processors: {pipeline_processors}\n      exporters: [otlp]\n"
    )


def workload(path, extra_env="", period=""):
    env = "".join(f"            - name: {name}\n              value: {value}\n"
                  for name, value in (line.split("=", 1) for line in extra_env.splitlines()))
    period_line = f"            periodSeconds: {period}\n" if period else ""
    return (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n"
        "      containers:\n        - name: web\n          image: example/web:1\n          env:\n"
        "            - name: OTEL_EXPORTER_OTLP_ENDPOINT\n              value: http://otel-collector:4317\n"
        f"{env}          livenessProbe:\n            httpGet:\n              path: {path}\n"
        f"              port: 8080\n{period_line}"
    )


class Obs13PositiveTests(unittest.TestCase):
    """OBS13-01: traces pipelines that keep probe spans of instrumented workloads are flagged with exact evidence."""

    EXPECTED = {
        "collector.yaml": ("pipeline/traces:probe-spans", 28, 31),
        "configmap.yaml": ("ConfigMap/agent:relay:pipeline/traces:probe-spans", 18, 21),
    }

    def test_pipelines_are_flagged_with_exact_lines_and_probe_details(self):
        _, result = run("collector.yaml", "configmap.yaml", "workloads.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"],
                         ["file:collector.yaml", "file:configmap.yaml", "file:workloads.yaml"])
        self.assertEqual(result["measurements"], [])
        self.assertEqual(len(result["findings"]), 2)
        for name, (identity, start, end) in self.EXPECTED.items():
            [finding] = [f for f in result["findings"] if f["scope_id"] == f"file:{name}"]
            lines = (FIXTURES / name).read_text().splitlines()
            with self.subTest(name=name):
                self.assertEqual(finding["identity"], identity)
                self.assertEqual(finding["confidence"], "medium")
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["line_start"], start)
                self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                summary = finding["summary"]
                # 3 x 86400/10 + 3 x 86400/5 + 86400/15 = 83,520 (the startup probe and envoy sidecar do not count)
                for phrase in ("/healthz (Deployment/api in workloads.yaml, liveness every 10s)",
                               "/ready (Deployment/api in workloads.yaml, readiness every 5s)",
                               "/actuator/health/liveness (Rollout/checkout in workloads.yaml, liveness every 15s)",
                               "about 83,520 spans per day"):
                    self.assertIn(phrase, summary)
                for absent in ("/stats", "/actuator/health ", "verbose"):
                    self.assertNotIn(absent, summary)
                self.assertIn('span.attributes["url.path"] == "/healthz"', finding["recommendation"])
                self.assertTrue(finding["references"])
        traces = result["findings"][0]["summary"]
        self.assertIn("exports them to otlp/backend,", traces)  # debug is not counted as an export

    def test_workloads_alone_produce_no_findings_but_are_evaluated_with_a_collector(self):
        _, result = run("collector.yaml", "workloads.yaml")
        self.assertEqual([f["scope_id"] for f in result["findings"]], ["file:collector.yaml"])


class Obs13NegativeTests(unittest.TestCase):
    """OBS13-02: filters that drop the probe paths (OTTL, legacy and user-agent forms) leave nothing to flag."""

    def test_ottl_filter_covering_every_probe_path_is_clean(self):
        _, result = run("negative.yaml", "workloads.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_legacy_and_user_agent_filters_are_clean(self):
        legacy = (
            "filter/legacy:\n  spans:\n    exclude:\n      match_type: regexp\n      attributes:\n"
            "        - key: http.target\n          value: .*/(healthz|ready|health).*\n"
        )
        deprecated = (
            "filter/old:\n  traces:\n    span:\n      - 'attributes[\"http.target\"] == \"/healthz\"'\n"
            "      - 'IsMatch(attributes[\"http.target\"], \"/ready|/actuator/health\")'\n"
        )
        user_agent = (
            "filter/ua:\n  trace_conditions:\n"
            "    - IsMatch(span.attributes[\"user_agent.original\"], \"^kube-probe/\")\n"
        )
        for processors, name in ((legacy, "filter/legacy"), (deprecated, "filter/old"), (user_agent, "filter/ua")):
            with self.subTest(filter=name):
                _, result = run("collector.yaml", "workloads.yaml",
                                extra=[("gateway.yaml", collector(processors, f"[{name}, batch]"))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])


class Obs13ExceptionTests(unittest.TestCase):
    """OBS13-03: sampling, other-collector filters, SDK exclusions, uninstrumented/non-HTTP probes, noqa, dev paths."""

    def test_tail_sampling_or_unknown_filters_anywhere_exempt_every_pipeline(self):
        for processors, pipeline, reason in (
            ("tail_sampling:\n  policies: []\n", "[tail_sampling]", "uses tail_sampling (its policies"),
            ("", "[filter/remote]", "uses filter/remote, which is not defined"),
            ("filter/env:\n  trace_conditions:\n    - ${env:DROP_RULE}\n", "[filter/env]", "unresolved"),
            ("", "${env:PROCESSORS}", "unresolved processors list"),
        ):
            with self.subTest(reason=reason):
                _, result = run("collector.yaml", "workloads.yaml",
                                extra=[("gateway.yaml", collector(processors, pipeline))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, " ".join(result["coverage"]["limitations"]))

    def test_sdk_exclusions_in_code_cover_the_probe_paths(self):
        _, result = run("collector.yaml", "workloads.yaml", "app/main.py")
        self.assertEqual(result["status"], "completed")
        self.assertIn("file:app/main.py", result["coverage"]["evaluated_scope"])
        self.assertEqual(result["findings"], [])

    def test_probes_that_produce_no_kept_spans_are_ignored(self):
        """Env exclusions, OTEL_TRACES_EXPORTER=none, uninstrumented/collector containers, startup, tcp/exec, noqa."""
        content = (FIXTURES / "exceptions-workloads.yaml").read_text()
        with self.assertRaises(obs13.NotEvaluated):
            obs13.parse("k8s/exceptions.yaml", content)
        _, result = run("collector.yaml", "exceptions-workloads.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:collector.yaml"])
        self.assertEqual(result["findings"], [])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:exceptions-workloads.yaml: not evaluated (no OpenTelemetry Collector config", limitations)
        self.assertIn("there was no probe traffic to judge", limitations)
        # Next to one ordinary probed workload in the same file, only that workload's probe is reported.
        _, result = run("collector.yaml", extra=[("k8s/all.yaml", content + "---\n" + workload("/orders/health"))])
        [finding] = result["findings"]
        self.assertIn("probed over HTTP at /orders/health (Deployment/web in k8s/all.yaml, liveness every 10s). ",
                      finding["summary"])
        self.assertIn("about 8,640 spans per day at the declared replica counts.", finding["summary"])

    def test_pipeline_noqa(self):
        _, result = run("exceptions-collector.yaml", "workloads.yaml")
        self.assertEqual(identities(result), ["pipeline/traces/main:probe-spans"])

    def test_development_and_test_files_are_not_evaluated(self):
        for name, fixture in (("deploy/otel-collector-dev.yaml", "collector.yaml"),
                              ("local/workloads.yaml", "workloads.yaml"),
                              ("tests/app/main.py", "app/main.py")):
            with self.subTest(name=name):
                _, result = run(extra=[(name, (FIXTURES / fixture).read_text())])
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("development/test", result["coverage"]["limitations"][0])


class Obs13IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS13-04: requested scope without a static source is not evaluated."""
        payload = make_input(sources=[], scope=["file:otel/collector.yaml"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_probes_without_a_collector_config_are_unavailable(self):
        """OBS13-04: probe and exclusion sources alone cannot be judged."""
        _, result = run("workloads.yaml", "app/main.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertIn("no OpenTelemetry Collector config in the payload", result["coverage"]["limitations"][0])

    def test_templates_and_broken_yaml_are_never_clean(self):
        """OBS13-05: a Helm template next to valid files makes the result partial."""
        _, result = run("collector.yaml", "workloads.yaml", "helm-workload.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("file:helm-workload.yaml", result["coverage"]["evaluated_scope"])
        self.assertIn("file:helm-workload.yaml: could not be parsed (Go/Helm template syntax on line 4)",
                      " ".join(result["coverage"]["limitations"]))
        broken_configmap = (
            "kind: ConfigMap\nmetadata:\n  name: c\ndata:\n  relay: |\n    exporters:\n      otlp: [\n"
            "    service:\n      pipelines: {}\n"
        )
        for name, content, reason in (
            ("otel.yaml", "exporters:\n\totlp: {}\nservice:\n  pipelines: {}\n", "tab indentation"),
            ("configmap.yaml", broken_configmap, "unbalanced flow collection"),
            ("k8s/web.yaml", workload("/healthz").replace("image: example/web:1", "image: [x"), "unbalanced"),
            ("otel.yaml", "exporters:\n  otlp: {}\nextensions:\n  pipelines: {}\n", "no OpenTelemetry Collector"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run(extra=[(name, content)])
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        for name, content in (
            ("k8s/deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\nlivenessProbe:\n  httpGet:\n"),
            ("k8s/otel-env.yaml", "env:\n  - name: OTEL_SERVICE_NAME\n    value: web\n"),
            ("app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n"),
            ("otel.json", '{"exporters": {}, "service": {"pipelines": {}}}'),
            ("README.md", "exporters:\npipelines:\nhttpGet OTEL_\n"),
        ):
            with self.subTest(name=name), self.assertRaises(obs13.Unsupported):
                obs13.parse(name, content)


class Obs13BoundaryTests(unittest.TestCase):
    """OBS13-06: partial coverage, root path, sampling confidence, probe rate, repeats and stable fingerprints."""

    def test_only_uncovered_paths_are_reported(self):
        healthz = "filter/h:\n  trace_conditions:\n    - span.attributes[\"url.path\"] == \"/healthz\"\n"
        _, result = run("collector.yaml", "workloads.yaml", extra=[("gateway.yaml", collector(healthz, "[filter/h]"))])
        self.assertEqual(identities(result), ["pipeline/traces:probe-spans", "pipeline/traces:probe-spans"])
        summary = result["findings"][0]["summary"]
        self.assertNotIn("/healthz", summary)
        self.assertIn("/ready", summary)
        self.assertIn('== "/ready"', result["findings"][0]["recommendation"])
        self.assertIn("about 57,600 spans per day", summary)  # 3 x 86400/5 + 86400/15

    def test_root_path_needs_an_exact_match(self):
        for literal, flagged in (("/api", True), ("/", False), ("^/$", False)):
            with self.subTest(literal=literal):
                filters = f"filter/r:\n  trace_conditions:\n    - IsMatch(span.attributes[\"url.path\"], \"{literal}\")\n"
                _, result = run(extra=[("gateway.yaml", collector(filters, "[filter/r]")),
                                       ("web.yaml", workload("/"))])
                self.assertEqual(identities(result), ["pipeline/traces:probe-spans"] if flagged else [])

    def test_probabilistic_sampler_lowers_confidence(self):
        sampler = "probabilistic_sampler:\n  sampling_percentage: 10\n"
        _, result = run(extra=[("gateway.yaml", collector(sampler, "[probabilistic_sampler]")),
                               ("web.yaml", workload("/healthz"))])
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "low")
        self.assertIn("probabilistic_sampler", finding["summary"])

    def test_probe_rate_uses_the_declared_period(self):
        for period, phrase in (("", "every 10s): about 8,640"), ("30", "every 30s): about 2,880"),
                               ("0", "every 10s): about 8,640")):
            with self.subTest(period=period):
                _, result = run(extra=[("gateway.yaml", collector()), ("web.yaml", workload("/healthz", period=period))])
                self.assertIn(phrase, result["findings"][0]["summary"].replace(". Each probe becomes a server span:", ":"))

    def test_env_exclusion_patterns(self):
        for value, flagged in (("healthz", False), ("^http://[^/]+/healthz$", False), ("metrics", True),
                               ("[unclosed", False)):
            with self.subTest(value=value):
                _, result = run(extra=[("gateway.yaml", collector()),
                                       ("web.yaml", workload("/healthz", f"OTEL_PYTHON_EXCLUDED_URLS={value}"))])
                self.assertEqual(identities(result), ["pipeline/traces:probe-spans"] if flagged else [])

    def test_repeated_pipeline_ids_get_suffixes(self):
        two = collector() + "---\n" + collector()
        _, result = run(extra=[("gateway.yaml", two), ("web.yaml", workload("/healthz"))])
        self.assertEqual(identities(result), ["pipeline/traces:probe-spans", "pipeline/traces:probe-spans#2"])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("collector.yaml", "workloads.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "collector.yaml").read_text()
        _, after = run("workloads.yaml", extra=[("collector.yaml", shifted)])
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual([f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
                         [f["evidence"][0]["line_start"] for f in after["findings"]])


class Obs13ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:collector.yaml", "pipeline/traces:probe-spans")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("collector.yaml", "workloads.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "    traces/invented:"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("collector.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("collector.yaml", "configmap.yaml", "workloads.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs13-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("collector.yaml", "workloads.yaml"))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs13)


class Obs13CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs13_result(self):
        input_path = FIXTURES / "obs13-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(identities(result), ["pipeline/traces:probe-spans"])


if __name__ == "__main__":
    unittest.main()
