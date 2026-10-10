"""Behavioral tests for the OBS-15 detector (issue #239)."""

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

from owner_d import cli, obs15
from owner_d.obs15 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from owner_d.textstatic import NotEvaluated, ParseError, Unsupported
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs15"
REPOSITORY_ID = "github:AWS-env/example"
POSITIVE = ("orders/requirements.txt", "web/package.json", "billing/go.mod", "deployment.yaml", "task-definition.json")
TRACING, ERRORS, METRICS = "tracing-apm", "error-tracking", "metrics"


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
        "scan_id": "scan-obs15-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "observability-tooling"},
        "scope": scope if scope is not None else [source["scope_id"] for source in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_files(files):
    return run(sources=[static_source(name, content) for name, content in files.items()])


def found(result):
    """{(scope_id, identity): (evidence lines, confidence, summary)}."""
    return {
        (f["scope_id"], f["identity"]): ([e["line_start"] for e in f["evidence"]], f["confidence"], f["summary"])
        for f in result["findings"]
    }


def pod(name, *containers, init=()):
    """A Deployment whose containers are (name, image, env dict); init containers get restartPolicy Always."""
    def block(items, sidecar):
        out = ""
        for cname, image, env in items:
            out += f"        - name: {cname}\n          image: {image}\n"
            if sidecar:
                out += "          restartPolicy: Always\n"
            if env:
                out += "          env:\n" + "".join(
                    f"            - name: {k}\n              value: \"{v}\"\n" for k, v in env.items())
        return out

    text = f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {name}\nspec:\n  template:\n    spec:\n"
    if init:
        text += "      initContainers:\n" + block(init, True)
    return text + "      containers:\n        - name: app\n          image: example.com/app:1\n" + block(containers, False)


class Obs15PositiveTests(unittest.TestCase):
    """OBS15-01: overlapping tools in Python, npm and Go manifests and in pod/task sidecars."""

    EXPECTED = {
        ("file:orders/requirements.txt", TRACING): ([4, 5], "medium", "Datadog APM (ddtrace), New Relic (newrelic)"),
        ("file:orders/requirements.txt", ERRORS): ([7, 8], "medium", "Rollbar (rollbar), Sentry (sentry-sdk)"),
        ("file:orders/requirements.txt", METRICS): ([9, 10], "low", "DogStatsD (datadog), Prometheus client"),
        ("file:web/package.json", TRACING): (
            [9, 11, 12], "medium",
            "Datadog APM (dd-trace), OpenTelemetry (@opentelemetry/auto-instrumentations-node, @opentelemetry/sdk-node)"),
        ("file:billing/go.mod", TRACING): (
            [8, 10], "medium",
            "Datadog APM (gopkg.in/DataDog/dd-trace-go.v1), OpenTelemetry (go.opentelemetry.io/otel/sdk)"),
        ("file:deployment.yaml", "Deployment/shop/checkout:sidecar-agents"): (
            [14, 20], "low", "traces: Datadog Agent, OpenTelemetry Collector; metrics: Datadog Agent, OpenTelemetry"),
        ("file:deployment.yaml", "Deployment/catalog:sidecar-agents"): ([31, 37], "low", "logs: Fluent Bit, Fluentd"),
        ("file:task-definition.json", "task/orders-api:sidecar-agents"): (
            [12, 17], "low", "traces: AWS X-Ray daemon, OpenTelemetry Collector"),
    }

    def test_overlapping_tools_are_flagged_with_exact_evidence(self):
        payload, result = run(*POSITIVE)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["measurements"], [])
        actual = found(result)
        self.assertEqual(set(actual), set(self.EXPECTED))
        sources = {s["scope_id"]: s["content"].splitlines() for s in payload["sources"]}
        for key, (lines, confidence, fragment) in self.EXPECTED.items():
            with self.subTest(key=key):
                self.assertEqual(actual[key][0], lines)
                self.assertEqual(actual[key][1], confidence)
                self.assertIn(fragment, actual[key][2])
        for finding in result["findings"]:
            for evidence in finding["evidence"]:
                self.assertEqual(evidence["value"], sources[finding["scope_id"]][evidence["line_start"] - 1])
                self.assertEqual(evidence["source_id"], f"src:{finding['scope_id'][5:]}")

    def test_otel_api_and_dev_dependencies_are_not_evidence(self):
        _, result = run("web/package.json", "orders/requirements.txt")
        values = [e["value"] for f in result["findings"] for e in f["evidence"]]
        self.assertFalse([v for v in values if "@opentelemetry/api" in v or "opentelemetry-api" in v])
        self.assertFalse([v for v in values if "newrelic" in v and "11.19" in v])  # devDependencies

    def test_recommendations_match_the_kind_of_finding(self):
        _, result = run(*POSITIVE)
        for finding in result["findings"]:
            expected = obs15.REC_SIDECARS if finding["identity"].endswith(":sidecar-agents") else obs15.RECOMMENDATION
            self.assertEqual(finding["recommendation"], expected)
            self.assertEqual(finding["references"], list(obs15.REFERENCES))


class Obs15NegativeTests(unittest.TestCase):
    """OBS15-02: one tool per category, tools in different services, agents in different pods."""

    def test_one_tool_per_category_is_clean(self):
        payload, result = run("negative/requirements.txt", "negative/package.json", "negative/deployment.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])

    def test_different_services_are_not_correlated(self):
        _, result = run("negative/requirements.txt", "other/requirements.txt")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_agents_in_different_pods_are_not_correlated(self):
        content = (pod("a", ("dd", "datadog/agent:7", {})) + "---\n"
                   + pod("b", ("otel", "otel/opentelemetry-collector:0.1", {})))
        _, result = run_files({"k8s/apps.yaml": content})
        self.assertEqual(result["findings"], [])


class Obs15ExceptionTests(unittest.TestCase):
    """OBS15-03: noqa, OpenTelemetry bridges, dev/test dependencies and dev/test paths."""

    def test_noqa_bridges_and_dev_dependencies(self):
        names = ("exceptions/requirements.txt", "exceptions/pyproject.toml", "exceptions/package.json",
                 "exceptions/go.mod", "exceptions/deployment.yaml")
        payload, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        # Only the metrics overlap remains: `# noqa: E501` names another rule.
        actual = found(result)
        self.assertEqual(set(actual), {("file:exceptions/requirements.txt", METRICS)})
        self.assertEqual(actual[("file:exceptions/requirements.txt", METRICS)][:2], ([7, 8], "low"))

    def test_datadog_agent_without_otlp_ingest_is_not_a_bridge(self):
        _, result = run_files({"a.yaml": pod("x", ("dd", "datadog/agent:7", {"DD_API_KEY": "k"}),
                                             ("otel", "otel/opentelemetry-collector-contrib:0.1", {}))})
        self.assertEqual([f["identity"] for f in result["findings"]], ["Deployment/x:sidecar-agents"])
        _, result = run_files({"a.yaml": pod("x", ("dd", "datadog/agent:7",
                                                   {"DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT": "0.0.0.0:4318"}),
                                             ("otel", "otel/opentelemetry-collector-contrib:0.1", {}))})
        self.assertEqual(result["findings"], [])

    def test_otel_distros_count_as_one_opentelemetry_stack(self):
        _, result = run_files({
            "svc/requirements.txt": "aws-opentelemetry-distro==0.3\nopentelemetry-sdk==1.25\n"
                                    "opentelemetry-instrumentation-flask==0.46b0\nopentelemetry-exporter-otlp==1.25\n",
            "web/package.json": '{"dependencies": {"@splunk/otel": "2", "@opentelemetry/sdk-node": "0.52",\n'
                                '"@opentelemetry/instrumentation-http": "0.52", "dd-trace": null}}',
        })
        # dd-trace with a null version is still declared: only the npm overlap is flagged.
        self.assertEqual([(f["scope_id"], f["identity"]) for f in result["findings"]],
                         [("file:web/package.json", TRACING)])

    def test_otel_instrumentation_libraries_feeding_a_vendor_tracer_are_a_bridge(self):
        _, result = run_files({
            "svc/requirements.txt": "ddtrace==2.9\nopentelemetry-instrumentation-flask==0.46b0\n"
                                    "opentelemetry-instrumentation==0.46b0\n",
            "web/package.json": '{"dependencies": {"dd-trace": "5", "@opentelemetry/instrumentation-http": "0.52"}}',
            "rpc/go.mod": "module x\nrequire (\n\tgopkg.in/DataDog/dd-trace-go.v1 v1.64.1\n"
                          "\tgo.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.52.0\n)\n",
        })
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_development_test_and_vendored_paths_are_not_evaluated(self):
        cases = {
            "requirements-dev.txt": "development/test dependency set (dev)",
            "requirements/test.txt": "development/test dependency set (test)",
            "tests/requirements.txt": "test, example or docs material (tests)",
            "examples/app/package.json": "test, example or docs material (examples)",
            "contract-tests/go.mod": "test, example or docs material (tests)",
            "node_modules/dd-trace/package.json": "vendored/tooling material (node_modules)",
            "docs/deployment.yaml": "test, example or docs material (docs)",
        }
        content = {"go.mod": "module x\nrequire gopkg.in/DataDog/dd-trace-go.v1 v1\n",
                   "package.json": '{"dependencies": {"dd-trace": "5", "newrelic": "11"}}',
                   "deployment.yaml": pod("x", ("dd", "datadog/agent:7", {}), ("o", "otel/opentelemetry-collector:1", {}))}
        for name, reason in cases.items():
            with self.subTest(name=name):
                text = content.get(name.rsplit("/", 1)[-1], "ddtrace\nnewrelic\n")
                _, result = run_files({name: text})
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(f"file:{name}: not evaluated (", result["coverage"]["limitations"][0])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_poetry_optional_and_pipfile_dev_packages_are_not_counted(self):
        _, result = run_files({
            "a/pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.12"\nddtrace = "^2"\n'
                                'newrelic = {version = "^9", optional = true}\n'
                                '[tool.poetry.group.dev.dependencies]\nelastic-apm = "^6"\n',
            "b/Pipfile": '[packages]\nddtrace = "*"\n\n[dev-packages]\nnewrelic = "*"\n',
        })
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs15IncompleteTests(unittest.TestCase):
    """OBS15-04 and OBS15-05: missing evidence, malformed and unsupported input."""

    def test_missing_source_is_unavailable_with_reason(self):
        _, result = run(sources=[], scope=["file:svc/requirements.txt"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["coverage"]["limitations"][0],
                         "file:svc/requirements.txt: no static source supplied for this scope item")

    def test_malformed_inputs_make_result_partial(self):
        cases = {
            "bad/package.json": ('{"dependencies": {"dd-trace": "5",}', "could not be parsed (invalid JSON)"),
            "bad/pyproject.toml": ('[project\ndependencies = ["ddtrace"]\n', "could not be parsed (invalid TOML"),
            "dotted/pyproject.toml": ('project.dependencies = ["ddtrace", "newrelic"]\n',
                                      "could not locate the declaration of 'ddtrace'"),
            "chart/templates/deployment.yaml": (
                "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
                "        - image: datadog/agent:{{ .Values.tag }}\n", "could not be parsed ("),
            "nomod/go.mod": ("require gopkg.in/DataDog/dd-trace-go.v1 v1\n", "could not be parsed (no module directive)"),
            "open/go.mod": ("module x\nrequire (\n\tgopkg.in/DataDog/dd-trace-go.v1 v1\n",
                            "could not be parsed (unterminated require block)"),
        }
        for name, (content, reason) in cases.items():
            with self.subTest(name=name):
                payload, result = run_files({"orders/requirements.txt": "ddtrace\nnewrelic\n", name: content})
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["file:orders/requirements.txt"])
                self.assertTrue(result["coverage"]["limitations"][0].startswith(f"file:{name}: "))
                self.assertIn(reason, result["coverage"]["limitations"][0])
                self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:orders/requirements.txt"})

    def test_unparseable_sibling_is_reported_as_a_correlation_limit(self):
        _, result = run_files({"svc/requirements.txt": "ddtrace\n", "svc/pyproject.toml": "[project\n"})
        self.assertEqual(result["status"], "partial")
        self.assertIn("svc/pyproject.toml: overlap between it and the other pypi manifests in svc/ is not judged",
                      result["coverage"]["limitations"][1])

    def test_unsupported_files_are_declined_cheaply_for_the_scanner(self):
        for name, content in {
            "README.md": "ddtrace and newrelic",
            "k8s/app.yaml": pod("x"),
            "config.json": '{"image": "datadog/agent:7"}',
            "requirements.cfg": "ddtrace\nnewrelic\n",
            "Dockerfile": "FROM datadog/agent:7\n",
        }.items():
            with self.subTest(name=name):
                with self.assertRaises(Unsupported):
                    obs15.parse(name, content)
                _, result = run_files({name: content})
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("unsupported file type", result["coverage"]["limitations"][0])

    def test_yaml_without_pod_templates_is_declined(self):
        with self.assertRaises(NotEvaluated):
            obs15.parse("values.yaml", "agents:\n  image: datadog/agent:7\n")
        with self.assertRaises(ParseError):
            obs15.parse("package.json", "[1, 2]")


class Obs15BoundaryTests(unittest.TestCase):
    """OBS15-06: directory grouping, identity, name normalisation and agent signal boundaries."""

    def test_sibling_python_manifests_are_one_package(self):
        _, result = run_files({
            "payments/requirements.txt": "flask\naws-xray-sdk==2.14\n",
            "payments/pyproject.toml": '[project]\nname = "payments"\ndependencies = [\n  "elastic-apm>=6",\n]\n',
        })
        actual = found(result)
        self.assertEqual(set(actual), {("file:payments/requirements.txt", TRACING),
                                       ("file:payments/pyproject.toml", TRACING)})
        self.assertEqual(actual[("file:payments/requirements.txt", TRACING)][:2], ([2], "low"))
        self.assertEqual(actual[("file:payments/pyproject.toml", TRACING)][:2], ([4], "low"))
        self.assertIn("Elastic APM (elastic-apm) in sibling manifest payments/pyproject.toml",
                      actual[("file:payments/requirements.txt", TRACING)][2])

    def test_same_tool_in_sibling_manifests_is_not_an_overlap(self):
        _, result = run_files({"ledger/requirements.txt": "ddtrace==2.9.2\n",
                               "ledger/pyproject.toml": '[project]\nname = "l"\ndependencies = ["ddtrace>=2"]\n'})
        self.assertEqual(result["findings"], [])

    def test_per_deployable_requirements_variants_are_not_correlated(self):
        _, result = run_files({"deploy/requirements.orders.txt": "ddtrace\n",
                               "deploy/requirements.billing.txt": "newrelic\n",
                               "deploy/requirements.txt": "elastic-apm\n"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_requirements_directory_layout_groups_and_skips_dev_sets(self):
        _, result = run_files({"requirements/base.txt": "ddtrace\n", "requirements/prod.txt": "-r base.txt\nnewrelic\n",
                               "requirements/dev.txt": "elastic-apm\n"})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(sorted(found(result)), [("file:requirements/base.txt", TRACING),
                                                 ("file:requirements/prod.txt", TRACING)])

    def test_package_names_are_normalised(self):
        _, result = run_files({"svc/requirements.txt": (
            "DDTrace>=2  # tracer\nElastic_APM[flask] ; python_version >= '3.8'\n"
            "-e git+https://example.com/newrelic.git#egg=newrelic\nhttps://example.com/newrelic-9.tar.gz\n"
            "newrelic @ https://example.com/newrelic-9.whl \\\n    --hash=sha256:00\n")})
        lines, confidence, summary = found(result)[("file:svc/requirements.txt", TRACING)]
        self.assertEqual((lines, confidence), ([1, 2, 5], "medium"))
        self.assertIn("3 overlapping tracing/APM agents", summary)

    def test_go_indirect_and_otel_api_modules_are_not_counted(self):
        _, result = run_files({"svc/go.mod": (
            "module x\nrequire github.com/newrelic/go-agent/v3 v3.0.0 // indirect\n"
            "require go.opentelemetry.io/otel v1.27.0\nrequire github.com/DataDog/dd-trace-go/v2 v2.0.0\n")})
        self.assertEqual(result["findings"], [])

    def test_agent_signal_defaults(self):
        cases = [
            # (containers, expected overlapping signals or None)
            ((("dd", "datadog/agent:7", {}), ("fb", "fluent/fluent-bit:3", {})), None),
            ((("dd", "datadog/agent:7", {"DD_LOGS_ENABLED": "true"}), ("fb", "fluent/fluent-bit:3", {})), "logs"),
            ((("dd", "datadog/agent:7", {"DD_APM_ENABLED": "false"}), ("x", "amazon/aws-xray-daemon:3", {})), None),
            ((("cw", "amazon/cloudwatch-agent:1", {}), ("nr", "newrelic/infrastructure:1", {})), "metrics"),
            ((("a", "otel/opentelemetry-collector:1", {}), ("b", "amazon/aws-otel-collector:1", {})), None),
            ((("v", "timberio/vector:0.39", {}), ("p", "grafana/promtail:3", {})), "logs"),
            ((("dd", "registry.example.com:5000/mirror/datadog/agent@sha256:ab", {}),
              ("x", "amazon/aws-xray-daemon:3", {})), "traces"),
            ((("ag", "example.com/myagent:1", {}), ("dd", "datadog/agent:7", {})), None),
        ]
        for containers, signal in cases:
            with self.subTest(containers=containers):
                _, result = run_files({"a.yaml": pod("x", *containers)})
                if signal is None:
                    self.assertEqual(result["findings"], [])
                else:
                    self.assertEqual(len(result["findings"]), 1)
                    self.assertIn(f"({signal}: ", result["findings"][0]["summary"])

    def test_only_native_sidecar_init_containers_count(self):
        _, result = run_files({"a.yaml": pod("x", ("fb", "fluent/fluent-bit:3", {}),
                                             init=[("fd", "fluent/fluentd:v1", {})])})
        self.assertEqual([f["identity"] for f in result["findings"]], ["Deployment/x:sidecar-agents"])
        _, result = run_files({"a.yaml": pod("x", ("fb", "fluent/fluent-bit:3", {})).replace(
            "      containers:", "      initContainers:\n        - name: fd\n          image: fluent/fluentd:v1\n"
            "      containers:")})
        self.assertEqual(result["findings"], [])

    def test_cloudformation_task_definition(self):
        content = (
            "Resources:\n  ApiTask:\n    Type: AWS::ECS::TaskDefinition\n    Properties:\n"
            "      ContainerDefinitions:\n        - Name: app\n          Image: example.com/api:1\n"
            "        - Name: cw\n          Image: public.ecr.aws/cloudwatch-agent/cloudwatch-agent:latest\n"
            "        - Name: adot\n          Image: public.ecr.aws/aws-observability/aws-otel-collector:latest\n")
        _, result = run_files({"infra/template.yaml": content})
        self.assertEqual(found(result)[("file:infra/template.yaml", "ApiTask:sidecar-agents")][:2], ([9, 11], "low"))

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run_files({"svc/requirements.txt": "ddtrace\nnewrelic\n"})
        _, after = run_files({"svc/requirements.txt": "# moved\nflask\nnewrelic==9\n\nddtrace==2\n"})
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual([e["line_start"] for e in after["findings"][0]["evidence"]], [3, 5])

    def test_vendor_table_is_unambiguous_and_excludes_otel_api_packages(self):
        for ecosystem, rows in obs15.PACKAGES.items():
            names = [name for _, _, names in rows for name in names]
            self.assertEqual(len(names), len(set(names)), ecosystem)
            self.assertTrue(all(category in obs15.CATEGORIES for category, _, _ in rows))
        for ecosystem, name in (("pypi", "opentelemetry-api"), ("npm", "@opentelemetry/api"),
                                ("npm", "@opentelemetry/instrumentation"), ("go", "go.opentelemetry.io/otel"),
                                ("pypi", "opentelemetry-instrumentation-flask"), ("pypi", "opentelemetry-instrumentation"),
                                ("npm", "@opentelemetry/instrumentation-http"),
                                ("go", "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"),
                                ("go", "go.opentelemetry.io/otel/trace")):
            self.assertIsNone(obs15.classify(ecosystem, name))


class Obs15ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:svc/requirements.txt", TRACING)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("orders/requirements.txt")
        forged = copy.deepcopy(result)
        forged["findings"][0]["evidence"][0]["value"] = "ddtrace==9.9.9"
        with self.assertRaises(ContractError):
            validate_pair(payload, forged)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("orders/requirements.txt")
        payload["detector_version"] = "0.9.0"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        self.assertEqual(run(*POSITIVE)[1], run(*POSITIVE)[1])

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs15-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(*POSITIVE))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], obs15)


class Obs15CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs15_result(self):
        input_path = FIXTURES / "obs15-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 8)


if __name__ == "__main__":
    unittest.main()
