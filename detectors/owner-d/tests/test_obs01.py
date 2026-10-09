"""Behavioral tests for the OBS-01 detector (issue #225)."""

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
from owner_d.obs01 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint, level_key_index
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs01"
EXAMPLES = REPO_ROOT / "shared" / "contracts" / "examples"
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
        "scan_id": "scan-obs01-001",
        "commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
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


class Obs01PositiveTests(unittest.TestCase):
    """OBS01-01: DEBUG/TRACE in production-named config and settings is flagged with exact evidence."""

    EXPECTED = {
        # (file, identity): (line_start, evidence line count, confidence, level)
        (".env.production", "log-level:LOG_LEVEL"): (3, 1, "high", "DEBUG"),
        (".env.production", "log-level:WORKER_LOG_LEVEL"): (4, 1, "high", "TRACE"),
        ("Dockerfile", "log-level:production.LOG_LEVEL"): (7, 1, "high", "DEBUG"),
        ("config/production.yaml", "log-level:log_level"): (3, 1, "high", "DEBUG"),
        ("config/production.yaml", "log-level:logging.level.org.hibernate.SQL"): (7, 1, "high", "DEBUG"),
        ("config/production.yaml", "log-level:spec.containers.env.LOG_LEVEL"): (15, 2, "high", "TRACE"),
        ("config/production.yaml", "log-level:services.worker.environment.RUST_LOG"): (22, 1, "high", "DEBUG"),
        ("deploy/ecs-task.prod.json", "log-level:containerDefinitions.environment.LOG_LEVEL"): (9, 2, "high", "DEBUG"),
        ("fly.production.toml", "log-level:env.LOG_LEVEL"): (6, 1, "high", "DEBUG"),
        ("logging.prod.ini", "log-level:logger_root.level"): (12, 1, "high", "DEBUG"),
        ("settings/production.py", "<module>:logging.basicConfig"): (5, 1, "medium", "DEBUG"),
        ("settings/production.py", "<module>:logger.setLevel"): (8, 1, "medium", "DEBUG"),
        ("settings/production.py", "<module>:os.getenv:LOG_LEVEL"): (10, 1, "medium", "DEBUG"),
        ("settings/production.py", "<module>:LOGGING.root.level"): (15, 1, "medium", "DEBUG"),
        ("settings/production.py", "<module>:LOGGING.loggers.django.db.backends.level"): (17, 1, "medium", "DEBUG"),
        ("src/main/resources/application-prod.properties", "log-level:logging.level.root"): (3, 1, "high", "DEBUG"),
        ("src/main/resources/application-prod.properties", "log-level:log4j.rootLogger"): (5, 1, "high", "TRACE"),
    }

    def test_verbose_levels_in_production_are_flagged_with_exact_lines(self):
        payload, result = run("positive")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 8)
        findings = by_key(result)
        self.assertEqual(set(findings), {(f"file:{name}", identity) for name, identity in self.EXPECTED})
        for (name, identity), (line, count, confidence, level) in self.EXPECTED.items():
            finding = findings[(f"file:{name}", identity)]
            with self.subTest(file=name, identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(level, finding["summary"])
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

    def test_contract_example_pair_is_reproduced(self):
        """The OBS-01 example written with the contract: same status, scope, evidence and confidence."""
        for name, expected_name in (("obs01-input", "obs01-detected"), ("obs01-after-input", "obs01-clean")):
            payload = json.loads((EXAMPLES / f"{name}.json").read_text())
            expected = json.loads((EXAMPLES / f"{expected_name}.json").read_text())
            result = evaluate(payload)
            validate_pair(payload, result)
            with self.subTest(example=name):
                self.assertEqual(result["status"], expected["status"])
                self.assertEqual(result["coverage"]["evaluated_scope"], expected["coverage"]["evaluated_scope"])
                self.assertTrue(result["coverage"]["limitations"][-1].startswith(expected["coverage"]["limitations"][0]))
                self.assertEqual(len(result["findings"]), len(expected["findings"]))
                for got, want in zip(result["findings"], expected["findings"]):
                    self.assertEqual(got["evidence"], want["evidence"])
                    self.assertEqual(got["confidence"], want["confidence"])
                    self.assertEqual(got["scope_id"], want["scope_id"])
                    # Deliberate divergence: the identity is key-qualified (one file can hold several
                    # level settings) instead of the example's fixed `production-log-level`.
                    self.assertEqual(got["identity"], "log-level:log_level")


class Obs01NegativeTests(unittest.TestCase):
    """OBS01-02: INFO/WARN levels, handler levels, framework DEBUG flags and level names are clean."""

    def test_similar_production_settings_are_clean(self):
        payload, result = run("negative")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 3)
        self.assertEqual(result["findings"], [])

    def test_key_rules(self):
        self.assertIsNotNone(level_key_index(["Logging", "LogLevel", "Default"]))
        self.assertIsNotNone(level_key_index(["loggers", "app", "level"]))
        self.assertIsNotNone(level_key_index(["AWS_LAMBDA_LOG_LEVEL"]))
        self.assertIsNone(level_key_index(["handlers", "console", "level"]))
        self.assertIsNone(level_key_index(["alerts", "level"]))
        self.assertIsNone(level_key_index(["TRACE_LOG_LEVEL"]))


class Obs01ExceptionTests(unittest.TestCase):
    """OBS01-03: dev/test/local/CI files, non-production keys or stages, and noqa are not flagged."""

    def test_non_production_and_acknowledged_settings_are_not_flagged(self):
        payload, result = run("exceptions")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(len(payload["scope"]), 7)
        self.assertEqual(result["findings"], [])

    def test_noqa_is_what_suppresses_the_production_override(self):
        content = static_source("exceptions", "config/production.yaml")["content"].replace("# noqa: OBS-01", "#")
        source = static_source("exceptions", "config/production.yaml", content)
        _, result = run("exceptions", sources=[source], scope=[source["scope_id"]])
        self.assertEqual([f["identity"] for f in result["findings"]], ["log-level:log_level"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 2)


class Obs01IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS01-04: requested scope without a static source is not evaluated."""
        _, result = run("positive", sources=[], scope=["file:config/production.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_contract_unavailable_example_is_reproduced(self):
        payload = json.loads((EXAMPLES / "obs01-unavailable-input.json").read_text())
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS01-05: parse failures, unsupported constructs and file types are omitted, never clean."""
        tabbed = static_source("malformed", "tabs.prod.yaml", "logging:\n\tlevel: debug\n")
        sources = [static_source("malformed", name) for name in files("malformed")] + [tabbed]
        sources.append(static_source("positive", "fly.production.toml"))
        _, result = run("malformed", sources=sources, scope=[s["scope_id"] for s in sources])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:fly.production.toml"])
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:fly.production.toml"})
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.prod.json: could not be parsed (JSONDecodeError)", limitations)
        self.assertIn("file:broken.prod.toml: could not be parsed (TOMLDecodeError)", limitations)
        self.assertIn("file:inline.prod.yaml: could not be parsed (UnsupportedConstruct)", limitations)
        self.assertIn("file:tabs.prod.yaml: could not be parsed (ValueError)", limitations)
        self.assertIn("file:nginx.prod.conf: unsupported file type", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("malformed")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Obs01BoundaryTests(unittest.TestCase):
    """OBS01-06: environment confidence tiers, repeated settings and line movement."""

    def test_environment_marker_sets_confidence(self):
        _, result = run("boundary")
        self.assertEqual(result["status"], "completed")
        confidence = {key: f["confidence"] for key, f in by_key(result).items()}
        self.assertEqual(confidence, {
            ("file:.env.production.example", "log-level:LOG_LEVEL"): "medium",
            ("file:config/settings.yaml", "log-level:log_level"): "low",
            ("file:k8s/deployment-prod.yaml", "log-level:spec.template.spec.containers.env.LOG_LEVEL"): "high",
            ("file:k8s/deployment-prod.yaml", "log-level:spec.template.spec.containers.env.LOG_LEVEL#2"): "high",
        })
        settings = by_key(result)[("file:config/settings.yaml", "log-level:log_level")]
        self.assertIn("production use not established", settings["summary"])

    def test_declared_production_context_raises_unmarked_files_to_medium(self):
        _, result = run("boundary", names=["config/settings.yaml"], context={"environment": "production"})
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "medium")
        _, staging = run("boundary", names=["config/settings.yaml"], context={"environment": "staging"})
        self.assertEqual(staging["findings"][0]["confidence"], "low")

    def test_repeated_setting_gets_distinct_identity_and_both_lines(self):
        _, result = run("boundary", names=["k8s/deployment-prod.yaml"])
        evidence = [(f["evidence"][0]["line_start"], f["evidence"][0]["value"]) for f in result["findings"]]
        self.assertEqual(evidence, [
            (8, source_lines("boundary", "k8s/deployment-prod.yaml", 8, 2)),
            (12, source_lines("boundary", "k8s/deployment-prod.yaml", 12, 2)),
        ])

    def test_fingerprints_do_not_change_when_lines_move(self):
        name = "k8s/deployment-prod.yaml"
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


class Obs01ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:config/production.yaml", "log-level:log_level")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "LOG_LEVEL=trace"
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
        committed = json.loads((FIXTURES / "obs01-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive"))


class Obs01CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs01_result(self):
        input_path = FIXTURES / "obs01-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Obs01PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
