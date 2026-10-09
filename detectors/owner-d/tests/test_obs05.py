"""Behavioral tests for the OBS-05 detector (issue #229)."""

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

from owner_d import cli
from owner_d.obs05 import CHECK_ID, DETECTOR_VERSION, SAMPLER, EvaluationError, evaluate, fingerprint, match_key
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs05"
REPOSITORY_ID = "github:AWS-env/example"


def files(case):
    base = FIXTURES / case
    return sorted(str(path.relative_to(base)) for path in base.rglob("*") if path.is_file())


def static_source(case, name, content=None):
    if content is None:
        content = (FIXTURES / case / name).read_text()
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{name}",
        "kind": "static",
        "locator": name,
        "content": content,
    }


def make_input(case, names=None, sources=None, scope=None, context=None):
    names = files(case) if names is None else names
    sources = [static_source(case, name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-obs05-001",
        "commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {} if context is None else context,
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(case, **kwargs):
    payload = make_input(case, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_key(result):
    return {(f["scope_id"], f["identity"]): f for f in result["findings"]}


def source_lines(case, name, start, count=1):
    lines = (FIXTURES / case / name).read_text().splitlines()
    return "\n".join(lines[start - 1:start - 1 + count])


class Obs05PositiveTests(unittest.TestCase):
    """OBS05-01: settings that keep every trace are flagged with exact evidence and tiered confidence."""

    EXPECTED = {
        # (file, identity): (line_start, evidence line count, confidence)
        (".env.production", "trace-sampling:OTEL_TRACES_SAMPLER"): (3, 1, "high"),
        ("Dockerfile", "ignored-sampler-arg:production.OTEL_TRACES_SAMPLER_ARG"): (5, 1, "low"),
        ("app/production/tracing.py", "<module>:os.environ:OTEL_TRACES_SAMPLER"): (9, 1, "medium"),
        ("app/production/tracing.py", "<module>:TracerProvider.sampler"): (11, 1, "medium"),
        ("app/production/tracing.py", "<module>:xray_recorder.configure.sampling"): (13, 1, "medium"),
        ("app/production/tracing.py", "build_provider:TracerProvider.sampler"): (17, 1, "low"),
        ("app/production/tracing.py", "ratio_provider:TracerProvider.sampler"): (22, 1, "medium"),
        ("app/production/tracing.py", "guarded_provider:TracerProvider.sampler"): (27, 1, "low"),
        ("config/xray-sampling-rules.prod.json", "xray-sampling-rule:*:*:POST:/checkout"): (10, 1, "medium"),
        ("config/xray-sampling-rules.prod.json", "xray-sampling-rule:default"): (15, 1, "high"),
        ("deploy/docker-compose.prod.yaml", "trace-sampling:services.api.environment.OTEL_TRACES_SAMPLER"): (5, 2, "high"),
        ("deploy/docker-compose.prod.yaml", "trace-sampling:services.worker.environment.OTEL_TRACES_SAMPLER"): (10, 1, "medium"),
        ("deploy/ecs-task.prod.json", "trace-sampling:containerDefinitions.environment.OTEL_TRACES_SAMPLER"): (8, 2, "medium"),
        ("infra/xray-prod.yaml", "xray-sampling-rule:catch-all"): (9, 1, "high"),
        ("infra/xray-prod.yaml", "xray-sampling-rule:CheckoutRule"): (23, 1, "medium"),
        ("infra/xray-prod.yaml",
         "trace-sampling:Resources.ApiFunction.Properties.Environment.Variables.OTEL_TRACES_SAMPLER"): (38, 1, "high"),
        ("k8s/deployment-prod.yaml", "trace-sampling:spec.template.spec.containers.env.OTEL_TRACES_SAMPLER"): (12, 2, "high"),
        ("src/main/resources/application-prod.properties",
         "trace-sampling:management.tracing.sampling.probability"): (2, 1, "high"),
        ("src/main/resources/application-prod.properties", "trace-sampling:otel.traces.sampler"): (3, 1, "high"),
    }

    def test_full_sampling_in_production_is_flagged_with_exact_lines(self):
        payload, result = run("positive")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 9)
        findings = by_key(result)
        self.assertEqual(set(findings), {(f"file:{name}", identity) for name, identity in self.EXPECTED})
        for (name, identity), (line, count, confidence) in self.EXPECTED.items():
            finding = findings[(f"file:{name}", identity)]
            with self.subTest(file=name, identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertEqual(
                    finding["fingerprint"],
                    fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity),
                )
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], name)
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], source_lines("positive", name, line, count))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_summaries_explain_why_every_trace_is_kept(self):
        findings = by_key(run("positive")[1])
        summary = findings[("file:deploy/docker-compose.prod.yaml",
                            "trace-sampling:services.api.environment.OTEL_TRACES_SAMPLER")]["summary"]
        self.assertIn("a sampling ratio of 100%", summary)
        summary = findings[("file:Dockerfile", "ignored-sampler-arg:production.OTEL_TRACES_SAMPLER_ARG")]["summary"]
        self.assertIn("the ratio is ignored", summary)
        summary = findings[("file:app/production/tracing.py", "build_provider:TracerProvider.sampler")]["summary"]
        self.assertIn("(via sampler)", summary)
        self.assertIn("every root trace", summary)
        summary = findings[("file:app/production/tracing.py", "guarded_provider:TracerProvider.sampler")]["summary"]
        self.assertIn("whenever the enclosing condition enables it", summary)


class Obs05NegativeTests(unittest.TestCase):
    """OBS05-02: sampled, remote, disabled, non-literal and default samplers in production are clean."""

    def test_similar_production_settings_are_clean(self):
        payload, result = run("negative")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 5)
        self.assertEqual(result["findings"], [])

    def test_key_rules(self):
        self.assertEqual(match_key(["OTEL_TRACES_SAMPLER"], SAMPLER), 1)
        self.assertEqual(match_key(["otel", "traces", "sampler"], SAMPLER), 3)
        self.assertEqual(match_key(["quarkus", "otel", "traces", "sampler"], SAMPLER), 3)
        self.assertEqual(match_key(["QUARKUS_OTEL_TRACES_SAMPLER"], SAMPLER), 1)
        self.assertEqual(match_key(["MY_OTEL_TRACES_SAMPLER"], SAMPLER), 0)
        self.assertEqual(match_key(["my", "OTEL_TRACES_SAMPLER"], SAMPLER), 1)
        self.assertEqual(match_key(["OTEL_TRACES_SAMPLER_ARG"], SAMPLER), 0)


class Obs05ExceptionTests(unittest.TestCase):
    """OBS05-03: non-production files/keys/stages, disabled SDK/export, runtime samplers and noqa."""

    def test_non_production_runtime_and_acknowledged_settings_are_not_flagged(self):
        payload, result = run("exceptions")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 8)
        self.assertEqual(result["findings"], [])

    def test_noqa_is_what_suppresses_the_acknowledged_override(self):
        name = "config/production.yaml"
        content = static_source("exceptions", name)["content"].replace("# noqa: OBS-05", "#")
        source = static_source("exceptions", name, content)
        _, result = run("exceptions", sources=[source], scope=[source["scope_id"]])
        self.assertEqual([f["identity"] for f in result["findings"]],
                         ["trace-sampling:services.worker.environment.OTEL_TRACES_SAMPLER"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 8)

    def test_disabled_sdk_and_exporter_are_what_suppress_the_other_services(self):
        name = "config/production.yaml"
        content = static_source("exceptions", name)["content"]
        content = content.replace('OTEL_SDK_DISABLED: "true"', 'OTEL_SDK_DISABLED: "false"')
        content = content.replace("OTEL_TRACES_EXPORTER: none", "OTEL_TRACES_EXPORTER: otlp")
        source = static_source("exceptions", name, content)
        _, result = run("exceptions", sources=[source], scope=[source["scope_id"]])
        self.assertEqual({f["identity"] for f in result["findings"]}, {
            "trace-sampling:services.api.environment.OTEL_TRACES_SAMPLER",
            "trace-sampling:services.batch.environment.OTEL_TRACES_SAMPLER",
        })

    def test_unconditional_sampler_in_the_same_code_is_flagged(self):
        """The runtime cases are clean because a sampler is chosen at runtime, not because the code is unsupported."""
        name = "app/production/tracing.py"
        content = static_source("exceptions", name)["content"] + "\nfixed = TracerProvider(sampler=ALWAYS_ON)\n"
        source = static_source("exceptions", name, content)
        _, result = run("exceptions", sources=[source], scope=[source["scope_id"]])
        self.assertEqual([(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
                         [("<module>:TracerProvider.sampler", 30)])

    def test_if_without_an_alternative_sampler_is_a_guard_not_a_choice(self):
        name = "app/production/tracing.py"
        content = static_source("exceptions", name)["content"].replace(
            "else:\n    switched = TracerProvider(sampler=TraceIdRatioBased(0.1))\n", "")
        source = static_source("exceptions", name, content)
        _, result = run("exceptions", sources=[source], scope=[source["scope_id"]])
        self.assertEqual([(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]],
                         [("<module>:TracerProvider.sampler", 16, "low")])


class Obs05IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS05-04: requested scope without a static source is not evaluated."""
        _, result = run("positive", sources=[], scope=["file:.env.production"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS05-05: parse failures, unsupported constructs and file types are omitted, never clean."""
        sources = [static_source("malformed", name) for name in files("malformed")]
        sources.append(static_source("positive", ".env.production"))
        _, result = run("malformed", sources=sources, scope=[s["scope_id"] for s in sources])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:.env.production"])
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:.env.production"})
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.prod.json: could not be parsed (JSONDecodeError)", limitations)
        self.assertIn("file:broken.prod.toml: could not be parsed (TOMLDecodeError)", limitations)
        self.assertIn("file:deployment.prod.yaml: could not be parsed (YamlError)", limitations)
        self.assertIn("file:fly.production.toml: could not be parsed (UnsupportedConstruct)", limitations)
        self.assertIn("file:xray.prod.tf: unsupported file type", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("malformed")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])

    def test_limitation_states_static_scope_and_collector_gap(self):
        _, result = run("negative")
        [limitation] = result["coverage"]["limitations"]
        self.assertIn("Head-only sampling (no tail sampling) is not judged", limitation)
        self.assertIn("OpenTelemetry Collector probabilistic_sampler config: not evaluated in v1", limitation)


class Obs05BoundaryTests(unittest.TestCase):
    """OBS05-06: ratio boundaries, quoting, default ARG, tiers, repeated settings and line movement."""

    def test_ratio_and_tier_boundaries(self):
        _, result = run("boundary")
        self.assertEqual(result["status"], "completed")
        confidence = {key: f["confidence"] for key, f in by_key(result).items()}
        compose = "file:deploy/compose.prod.yaml"
        service = "trace-sampling:services.{}.environment.OTEL_TRACES_SAMPLER"
        self.assertEqual(confidence, {
            # ARG "1", 1 and 1.0 keep every trace; 0.99, invalid 1.5 and ${TRACE_RATIO} do not.
            (compose, service.format("quoted")): "high",
            (compose, service.format("number")): "high",
            (compose, service.format("decimal")): "high",
            (compose, service.format("fallback")): "high",
            # No ARG: the specified default ratio 1.0 applies, one tier lower (two for parent-based).
            (compose, service.format("default-ratio")): "medium",
            (compose, service.format("default-parent")): "low",
            ("file:config/tracing.yaml", "trace-sampling:otel.env.OTEL_TRACES_SAMPLER"): "low",
            ("file:k8s/deployment-prod.yaml", "trace-sampling:spec.template.spec.containers.env.OTEL_TRACES_SAMPLER"): "high",
            ("file:k8s/deployment-prod.yaml",
             "trace-sampling:spec.template.spec.containers.env.OTEL_TRACES_SAMPLER#2"): "high",
            # Python: 1 and 1.0 are a full ratio; "1", True and 0.99 are not flagged.
            ("file:app/production/samplers.py", "<module>:TracerProvider.sampler"): "medium",
            ("file:app/production/samplers.py", "<module>:TracerProvider.sampler#2"): "medium",
        })
        python = [f["evidence"][0]["line_start"] for f in result["findings"] if f["scope_id"].endswith(".py")]
        self.assertEqual(python, [4, 5])

    def test_declared_production_context_raises_unmarked_files_to_medium(self):
        _, result = run("boundary", names=["config/tracing.yaml"], context={"environment": "production"})
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "medium")
        _, staging = run("boundary", names=["config/tracing.yaml"], context={"environment": "staging"})
        self.assertEqual(staging["findings"][0]["confidence"], "low")
        self.assertIn("production use not established", staging["findings"][0]["summary"])

    def test_repeated_setting_gets_distinct_identity_and_both_lines(self):
        _, result = run("boundary", names=["k8s/deployment-prod.yaml"])
        evidence = [(f["evidence"][0]["line_start"], f["evidence"][0]["value"]) for f in result["findings"]]
        self.assertEqual(evidence, [
            (9, source_lines("boundary", "k8s/deployment-prod.yaml", 9, 2)),
            (13, source_lines("boundary", "k8s/deployment-prod.yaml", 13, 2)),
        ])

    def test_fingerprints_do_not_change_when_lines_move(self):
        name = "deploy/compose.prod.yaml"
        _, before = run("boundary", names=[name])
        shifted = static_source("boundary", name, "# moved\n\n\n" + (FIXTURES / "boundary" / name).read_text())
        _, after = run("boundary", sources=[shifted], scope=[shifted["scope_id"]])
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )

    def test_invalid_environment_context_is_rejected(self):
        with self.assertRaises(EvaluationError):
            evaluate(make_input("boundary", context={"environment": ["production"]}))


class Obs05ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:.env.production", "trace-sampling:OTEL_TRACES_SAMPLER")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "OTEL_TRACES_SAMPLER=always_on  # invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixtures(self):
        committed = json.loads((FIXTURES / "obs05-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive"))


class Obs05CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs05_result(self):
        input_path = FIXTURES / "obs05-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Obs05PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
