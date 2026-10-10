"""Behavioral tests for the OBS-08 detector (issue #232)."""

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

from owner_d import cli, obs08
from owner_d.obs08 import (
    CHECK_ID, DETECTOR_VERSION, REC_AMP, REC_THANOS, RECOMMENDATION, REFERENCE_SETTINGS, EvaluationError,
    NotEvaluated, Unsupported, evaluate, fingerprint, seconds,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs08"
REPOSITORY_ID = "github:AWS-env/example"
SETTINGS = dict(REFERENCE_SETTINGS)
AMP_URL = "https://aps-workspaces.ap-south-1.amazonaws.com/workspaces/ws-1234abcd/api/v1/remote_write"


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
        "scan_id": "scan-obs08-001",
        "commit_sha": "8888888888888888888888888888888888888888",
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


def evidence(finding):
    item = finding["evidence"][0]
    return item["line_start"], item["value"]


def prometheus(name, interval=None, retention=None, extra=""):
    lines = ["apiVersion: monitoring.coreos.com/v1", "kind: Prometheus", "metadata:", f"  name: {name}", "spec:"]
    if interval is not None:
        lines.append(f"  scrapeInterval: {interval}")
    if retention is not None:
        lines.append(f"  retention: {retention}")
    return "\n".join(lines) + "\n" + extra


def server(args, image="prom/prometheus:v3.5.0"):
    return (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: prometheus\nspec:\n  template:\n    spec:\n"
        f"      containers:\n        - name: prometheus\n          image: {image}\n          args: {json.dumps(args)}\n"
    )


def config_map(config):
    body = "".join(f"    {line}\n" for line in config.splitlines())
    return f"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: prom\ndata:\n  prometheus.yml: |\n{body}"


JOB_1S = "scrape_configs:\n  - job_name: app\n    scrape_interval: 1s\n"


class Obs08PositiveTests(unittest.TestCase):
    """OBS08-01: high-resolution scrapes kept raw for long, and Thanos without downsampling."""

    def setUp(self):
        _, self.result = run("operator.yaml", "server.yaml", "prometheus.yml", "collector.yaml", "thanos.yaml",
                             "docker-compose.yml", "values.yaml")

    def test_completed_with_exact_evidence_and_confidence(self):
        self.assertEqual(self.result["status"], "completed")
        self.assertEqual(self.result["measurements"], [])
        expected = {
            "operator.yaml": {
                "Prometheus/monitoring/k8s:scrapeInterval": (9, "  scrapeInterval: 5s", "medium"),
                "PrometheusAgent/agent:scrapeInterval": (40, "  scrapeInterval: 10s", "low"),
            },
            "server.yaml": {
                "ConfigMap/prometheus-config:prometheus.yml:global:scrape_interval":
                    (10, "      scrape_interval: 10s", "medium"),
                "ConfigMap/prometheus-config:prometheus.yml:job/envoy:scrape_interval":
                    (17, "        scrape_interval: 1s", "medium"),
            },
            "prometheus.yml": {"job/api:scrape_interval": (6, "    scrape_interval: 5s", "low")},
            "collector.yaml": {
                "receiver/prometheus:job/app:scrape_interval": (9, "          scrape_interval: 1s", "low"),
            },
            "thanos.yaml": {
                "StatefulSet/monitoring/thanos-compact:compact:downsampling.disable":
                    (17, "            - --downsampling.disable", "medium"),
            },
            "docker-compose.yml": {
                "service/compactor:downsampling.disable": (
                    5, "    command: compact --wait --downsampling.disable --retention.resolution-raw=1y "
                       "--objstore.config-file=/b.yml", "medium"),
            },
            "values.yaml": {
                "values/prometheus.prometheusSpec:scrapeInterval": (6, "    scrapeInterval: 10s", "medium"),
            },
        }
        for name, hits in expected.items():
            with self.subTest(name=name):
                found = findings_for(self.result, name)
                self.assertEqual(set(found), set(hits))
                for identity, (line, value, confidence) in hits.items():
                    self.assertEqual(evidence(found[identity]), (line, value))
                    self.assertEqual(found[identity]["confidence"], confidence)

    def test_summaries_name_the_interval_and_the_store(self):
        server_hit = findings_for(self.result, "server.yaml")[
            "ConfigMap/prometheus-config:prometheus.yml:job/envoy:scrape_interval"]
        self.assertIn("Scrape job 'envoy' scrapes every 1s (below 15s)", server_hit["summary"])
        self.assertIn("Deployment/monitoring/prometheus:prometheus (retention 90d, line 40) keeps them 90 days",
                      server_hit["summary"])
        amp = findings_for(self.result, "prometheus.yml")["job/api:scrape_interval"]
        self.assertIn("Amazon Managed Service for Prometheus remote write keeps them 150 days", amp["summary"])
        self.assertIn("workspace retention is not visible", amp["summary"])
        thanos = findings_for(self.result, "thanos.yaml")
        self.assertIn("keeps raw blocks forever", next(iter(thanos.values()))["summary"])

    def test_recommendations_match_the_store(self):
        self.assertEqual(findings_for(self.result, "operator.yaml")["Prometheus/monitoring/k8s:scrapeInterval"][
            "recommendation"], RECOMMENDATION)
        self.assertEqual(findings_for(self.result, "collector.yaml")["receiver/prometheus:job/app:scrape_interval"][
            "recommendation"], REC_AMP)
        self.assertEqual(findings_for(self.result, "docker-compose.yml")["service/compactor:downsampling.disable"][
            "recommendation"], REC_THANOS)


class Obs08NegativeTests(unittest.TestCase):
    """OBS08-02: standard intervals, short stores, overridden globals and downsampling compactors."""

    def test_similar_configs_are_clean(self):
        _, result = run("negative.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.yaml"])
        self.assertEqual(result["findings"], [])

    def test_default_or_short_retention_and_default_interval_are_not_flagged(self):
        _, result = run("operator.yaml", "thanos.yaml")
        flagged = {f["identity"].split(":")[0] for f in result["findings"]}
        for clean in ("Prometheus/default-interval", "Prometheus/default-retention", "Prometheus/short-retention",
                      "StatefulSet/thanos-compact-downsampled", "StatefulSet/thanos-compact-short-raw"):
            self.assertNotIn(clean, flagged)

    def test_short_server_retention_is_clean_and_lengthening_it_flags(self):
        _, short = run_inline(("prom.yaml", config_map(JOB_1S) + "---\n" +
                               server(["--storage.tsdb.retention.time=7d"])))
        self.assertEqual(short["status"], "completed")
        self.assertEqual(short["findings"], [])
        _, default = run_inline(("prom.yaml", config_map(JOB_1S) + "---\n" + server([])))
        self.assertEqual(default["findings"], [])  # Prometheus default retention 15d is not above 15 days
        _, long = run_inline(("prom.yaml", config_map(JOB_1S) + "---\n" +
                              server(["--storage.tsdb.retention.time", "30d"])))
        self.assertEqual([f["identity"] for f in long["findings"]],
                         ["ConfigMap/prom:prometheus.yml:job/app:scrape_interval"])

    def test_server_without_its_config_is_judged_only_when_retention_is_short(self):
        _, result = run_inline(("server.yaml", server(["--storage.tsdb.retention.time=7d"])))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        _, result = run_inline(("server.yaml", server(["--storage.tsdb.retention.time=90d"])))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("scrape intervals are configured in another file", result["coverage"]["limitations"][0])


class Obs08ExceptionTests(unittest.TestCase):
    """OBS08-03: suppressions, unknown stores, agent mode and development paths."""

    def test_only_the_unsuppressed_hit_is_reported(self):
        _, result = run("exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["Prometheus/other-rule:scrapeInterval"])
        self.assertEqual(evidence(result["findings"][0]), (16, "  scrapeInterval: 5s  # noqa: E501"))

    def test_agent_mode_size_only_and_non_amp_remote_write_are_not_judged(self):
        for content, reason in (
            (config_map(JOB_1S) + "---\n" + server(["--agent"]), "no Prometheus server or remote write"),
            (config_map(JOB_1S) + "---\n" + server(["--enable-feature=exemplar-storage,agent"]),
             "no Prometheus server or remote write"),
            (config_map(JOB_1S) + "---\n" + server(["--storage.tsdb.retention.size=50GB"]), "bounded by size only"),
            (config_map(JOB_1S) + "---\n" + server(["--storage.tsdb.retention.time=$(RETENTION)"]),
             "retention '$(RETENTION)'"),
            (prometheus("p", "5s", extra="  retentionSize: 10GB\n"), "size-bounded or chart default"),
            (config_map(JOB_1S + "remote_write:\n  - url: http://mimir:9009/api/v1/push\n"),
             "1 non-AMP remote write target(s)"),
        ):
            with self.subTest(reason=reason):
                _, result = run_inline(("prom.yaml", content))
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("raw retention not visible in this file", result["coverage"]["limitations"][0])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_amp_remote_write_makes_an_agent_config_judgeable(self):
        content = config_map(JOB_1S + f"remote_write:\n  - url: {AMP_URL}\n") + "---\n" + server(["--agent"])
        _, result = run_inline(("prom.yaml", content))
        self.assertEqual([(f["identity"], f["confidence"]) for f in result["findings"]],
                         [("ConfigMap/prom:prometheus.yml:job/app:scrape_interval", "low")])

    def test_development_paths_are_not_evaluated(self):
        content = (FIXTURES / "operator.yaml").read_text()
        for path in ("deploy/dev/prometheus.yaml", "monitoring/prometheus-local.yaml", "test/prometheus.yaml"):
            with self.subTest(path=path):
                _, result = run_inline((path, content))
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("path marks a development/test config", result["coverage"]["limitations"][0])

    def test_unresolved_interval_is_not_judged(self):
        _, result = run_inline(("p.yaml", prometheus("p", "${SCRAPE_INTERVAL}", "30d")))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs08IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS08-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:prometheus.yml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_settings_are_unavailable(self):
        """OBS08-04: both thresholds are required judgment-call settings."""
        for context, reason in (
            ({}, "missing required context settings: min_scrape_interval_seconds, max_raw_retention_days"),
            ({"min_scrape_interval_seconds": 15}, "missing required context settings: max_raw_retention_days"),
            ({**SETTINGS, "min_scrape_interval_seconds": 0}, "context.min_scrape_interval_seconds must be a positive"),
            ({**SETTINGS, "max_raw_retention_days": -1}, "context.max_raw_retention_days must be a positive"),
            ({**SETTINGS, "max_raw_retention_days": "15"}, "context.max_raw_retention_days must be a positive"),
            ({**SETTINGS, "min_scrape_interval_seconds": True}, "context.min_scrape_interval_seconds must be a"),
        ):
            with self.subTest(context=context):
                _, result = run("operator.yaml", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_malformed_unpaired_and_unsupported_files_are_never_clean(self):
        """OBS08-05: templates, broken YAML and configs without a visible store give partial results."""
        broken_embedded = config_map("scrape_configs:\n  - job_name: a\n\tscrape_interval: 1s\n")
        _, result = run_inline(
            ("operator.yaml", (FIXTURES / "operator.yaml").read_text()),
            ("helm-prometheus.yaml", (FIXTURES / "helm-prometheus.yaml").read_text()),
            ("unpaired.yml", (FIXTURES / "unpaired.yml").read_text()),
            ("broken.yaml", "kind: Prometheus\nspec:\n\tscrapeInterval: 5s\n"),
            ("embedded.yaml", broken_embedded),
            ("prometheus.json", '{"scrape_configs": []}'),
            ("prometheus.toml", "scrape_interval = '1s'\n"),
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:operator.yaml"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:helm-prometheus.yaml: could not be parsed (Go/Helm template syntax on line 4)", limitations)
        self.assertIn("file:unpaired.yml: not evaluated (raw retention not visible in this file for the scrape "
                      "config (1 non-AMP remote write target(s)))", limitations)
        self.assertIn("file:broken.yaml: could not be parsed (tab indentation on line 3)", limitations)
        self.assertIn("file:embedded.yaml: could not be parsed (embedded config ConfigMap/prom:prometheus.yml at "
                      "line 6", limitations)
        self.assertIn("file:prometheus.json: unsupported file type", limitations)
        self.assertIn("file:prometheus.toml: unsupported file type", limitations)
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:operator.yaml"})

    def test_module_parse_for_scanner_selection(self):
        """Without settings, parse() only selects files; unrelated YAML is rejected cheaply."""
        for name, content in (
            ("deploy.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"),
            (".github/workflows/ci.yml", "on: push\njobs: {}\n"),
            ("prometheus.py", "scrape_configs = []\n"),
        ):
            with self.subTest(name=name), self.assertRaises(Unsupported):
                obs08.parse(name, content)
        with self.assertRaises(NotEvaluated):
            obs08.parse("notes.yaml", "text: mentions scrape_interval: but is not a config\n")
        ctx = obs08.parse("unpaired.yml", (FIXTURES / "unpaired.yml").read_text())
        self.assertEqual(ctx.hits, [])
        self.assertEqual(len(ctx.groups), 1)


class Obs08BoundaryTests(unittest.TestCase):
    """OBS08-06: strict thresholds, duration formats, #n identities and stable fingerprints."""

    def test_thresholds_are_strict(self):
        _, result = run("boundary.yaml")
        self.assertEqual(
            [(f["identity"], evidence(f)[0]) for f in result["findings"]],
            [("Prometheus/below-threshold:scrapeInterval", 15), ("Prometheus/below-threshold:scrapeInterval#2", 31)],
        )
        _, wider = run("boundary.yaml", context={"min_scrape_interval_seconds": 91, "max_raw_retention_days": 14})
        self.assertEqual(
            [f["identity"] for f in wider["findings"]],
            ["Prometheus/at-threshold:scrapeInterval", "Prometheus/below-threshold:scrapeInterval",
             "Prometheus/retention-at-threshold:scrapeInterval", "Prometheus/below-threshold:scrapeInterval#2",
             "Prometheus/compound:scrapeInterval"],
        )
        _, narrow = run("boundary.yaml", context={"min_scrape_interval_seconds": 14, "max_raw_retention_days": 365})
        self.assertEqual(narrow["status"], "completed")
        self.assertEqual(narrow["findings"], [])

    def test_thanos_raw_retention_boundary(self):
        def compactor(raw):
            args = ["compact", "--downsampling.disable"] + ([f"--retention.resolution-raw={raw}"] if raw else [])
            return server(args, image="quay.io/thanos/thanos:v0.39.2")

        for raw, flagged in (("15d", False), ("16d", True), ("0d", True), (None, True), ("${RAW}", None)):
            with self.subTest(raw=raw):
                _, result = run_inline(("thanos.yaml", compactor(raw)))
                if flagged is None:
                    self.assertEqual(result["status"], "unavailable")
                else:
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(len(result["findings"]), int(flagged))

    def test_duration_parsing(self):
        for value, expected in (("1s", 1), ("500ms", 0.5), ("1m30s", 90), ("2w1d", 15 * 86400), ("1y", 365 * 86400),
                                ("0", 0), ("", None), ("10", None), ("1.5s", None), ("5 s", None), (None, None)):
            with self.subTest(value=value):
                self.assertEqual(seconds(value), expected)

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("server.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "server.yaml").read_text()
        _, after = run_inline(("server.yaml", shifted))
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Obs08ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:operator.yaml", "Prometheus/monitoring/k8s:scrapeInterval")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("operator.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "  scrapeInterval: 1s"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("operator.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("operator.yaml", "server.yaml", "collector.yaml", "thanos.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs08-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("operator.yaml", "server.yaml"))


class Obs08CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs08_result(self):
        input_path = FIXTURES / "obs08-01-positive-input.json"
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
