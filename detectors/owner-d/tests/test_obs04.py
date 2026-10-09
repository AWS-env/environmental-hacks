"""Behavioral tests for the OBS-04 detector (issue #228)."""

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
from owner_d.obs04 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs04"
REPOSITORY_ID = "github:AWS-env/example"
MARKERS = {
    "markers/jsonlogger_config.py": "pythonjsonlogger.jsonlogger.JsonFormatter",
    "markers/structlog_setup.py": "imports structlog",
    "markers/powertools_logger.py": "imports aws_lambda_powertools.Logger",
    "markers/formatter_class.py": "JSON formatter class JsonLines",
    "markers/json_format_string.py": "JSON-shaped log format string",
    "markers/cdk_stack.py": "Lambda JSON log format (logging_format=)",
    "markers/extra_fields.py": "logger.info(extra=...) fields",
    "markers/keyword_fields.py": "log.info(amount=...) structured fields",
    "markers/loguru_serialize.py": "loguru serialize=True",
}


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
        "scan_id": "scan-obs04-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"language": "python"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_key(result):
    return {(f["scope_id"], f["identity"]): f for f in result["findings"]}


def line(name, number):
    return (FIXTURES / name).read_text().splitlines()[number - 1]


class Obs04PositiveTests(unittest.TestCase):
    """OBS04-01: free-text log lines are reported once per module and kind, with exact evidence."""

    def test_lambda_and_service_modules_are_flagged(self):
        _, result = run("positive.py", "service.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py", "file:service.py"])
        expected = {
            ("file:positive.py", "module:logging"): (
                11, "medium", ["4 logging call(s)", "Lambda handler module", "%-style arguments", "an f-string",
                               "str.format()", "string concatenation", "`parse`"]),
            ("file:positive.py", "module:print"): (16, "medium", ["2 print() call(s)", "an f-string", "print arguments"]),
            ("file:service.py", "module:logging"): (10, "low", ["1 logging call(s) in this module", "%-style arguments"]),
        }
        findings = by_key(result)
        self.assertEqual(set(findings), set(expected))
        for key, (number, confidence, phrases) in expected.items():
            finding = findings[key]
            scope_id, identity = key
            name = scope_id.removeprefix("file:")
            with self.subTest(key=key):
                self.assertEqual(finding["confidence"], confidence)
                for phrase in phrases:
                    self.assertIn(phrase, finding["summary"])
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], f"src:{name}")
                self.assertEqual(evidence["line_start"], number)
                self.assertEqual(evidence["value"], line(name, number))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])
        self.assertEqual(result["coverage"]["limitations"][:-1], [])

    def test_text_format_raises_confidence_project_wide(self):
        _, result = run("textformat.py", "service.py")
        findings = by_key(result)
        self.assertEqual(set(findings), {("file:textformat.py", "module:logging"), ("file:service.py", "module:logging")})
        for finding in findings.values():
            self.assertEqual(finding["confidence"], "medium")
            self.assertIn("plain-text log format is configured", finding["summary"])
        self.assertEqual(findings[("file:textformat.py", "module:logging")]["evidence"][0]["line_start"], 9)


class Obs04NegativeTests(unittest.TestCase):
    """OBS04-02: constant/variable messages, json.dumps lines and non-loggers are clean."""

    def test_similar_lines_without_free_text_values_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])


class Obs04ExceptionTests(unittest.TestCase):
    """OBS04-03: structured-logging markers, tests, scripts, CLIs, __main__ and noqa."""

    def test_structured_markers_suppress_logging_but_not_lambda_print(self):
        for marker, reason in MARKERS.items():
            with self.subTest(marker=marker):
                _, result = run("positive.py", "service.py", marker)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(list(by_key(result)), [("file:positive.py", "module:print")])
                limitation = result["coverage"]["limitations"][0]
                self.assertIn(f"Structured logging is configured in {marker}", limitation)
                self.assertIn(reason, limitation)

    def test_markers_in_vendored_or_unparseable_files_are_ignored(self):
        vendored = static_source("vendor/structlog_setup.py", (FIXTURES / "markers/structlog_setup.py").read_text())
        _, result = run(sources=[static_source("service.py"), vendored, static_source("broken.py")],
                        scope=["file:service.py", "file:vendor/structlog_setup.py", "file:broken.py"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(list(by_key(result)), [("file:service.py", "module:logging")])

    def test_tests_scripts_clis_and_vendored_code_are_not_judged(self):
        layer = static_source("layer/python/billing.py", (FIXTURES / "service.py").read_text())
        sample = static_source("examples/billing.py", (FIXTURES / "service.py").read_text())
        names = ("tests/test_app.py", "scripts/backfill.py", "cli_tool.py")
        sources = [static_source(name) for name in names] + [layer, sample]
        scope = [s["scope_id"] for s in sources]
        _, result = run(sources=sources, scope=scope)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], scope)
        self.assertEqual(result["findings"], [])

    def test_lambda_handler_in_scripts_dir_is_still_judged(self):
        moved = static_source("scripts/handler.py", (FIXTURES / "positive.py").read_text())
        _, result = run(sources=[moved], scope=["file:scripts/handler.py"])
        self.assertEqual(len(result["findings"]), 2)

    def test_noqa_and_main_block_are_respected(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        [finding] = result["findings"]
        self.assertEqual(finding["identity"], "module:logging")
        self.assertEqual(finding["confidence"], "low")  # basicConfig(format=...) in __main__ is a dev path
        self.assertIn("1 logging call(s)", finding["summary"])
        self.assertEqual(finding["evidence"][0]["line_start"], 9)
        self.assertEqual(finding["evidence"][0]["value"], line("exceptions.py", 9))


class Obs04IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS04-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/handler.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS04-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertEqual(len(result["findings"]), 2)  # broken.py's structlog import is not a marker
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])


class Obs04BoundaryTests(unittest.TestCase):
    """OBS04-06: one interpolated call, empty extra, one-parameter handler and line movement."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        [finding] = result["findings"]
        self.assertEqual(finding["identity"], "module:logging")  # handler(event) is not a Lambda handler
        self.assertEqual(finding["confidence"], "low")
        self.assertIn("2 logging call(s)", finding["summary"])  # extra={} carries no fields
        self.assertEqual(finding["evidence"][0]["line_start"], 10)
        self.assertEqual(finding["evidence"][0]["value"], line("boundary.py", 10))

    def test_single_interpolated_call_is_enough_and_constants_are_not(self):
        text = (FIXTURES / "boundary.py").read_text()
        one = text.replace('    logger.info("empty extra %s", event, extra={})\n', "")
        constant = one.replace('logger.info("only %s", event["id"])', 'logger.info("only")')
        _, result = run(sources=[static_source("boundary.py", one)], scope=["file:boundary.py"])
        self.assertIn("1 logging call(s)", result["findings"][0]["summary"])
        _, result = run(sources=[static_source("boundary.py", constant)], scope=["file:boundary.py"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_nonempty_extra_is_a_structured_marker(self):
        text = (FIXTURES / "boundary.py").read_text().replace("extra={}", 'extra={"k": 1}')
        _, result = run(sources=[static_source("boundary.py", text)], scope=["file:boundary.py"])
        self.assertEqual(result["findings"], [])
        self.assertIn("logger.info(extra=...) fields", result["coverage"]["limitations"][0])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("positive.py")
        shifted = "\n\n\n" + (FIXTURES / "positive.py").read_text()
        _, after = run(sources=[static_source("positive.py", shifted)], scope=["file:positive.py"])
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Obs04ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "module:logging")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "logger.info('invented')"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_other_check_ids_are_rejected(self):
        payload = make_input("positive.py")
        payload["check_id"] = "OBS-03"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(EvaluationError):
            evaluate(["not", "a", "payload"])

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py", "markers/structlog_setup.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs04-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Obs04CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs04_result(self):
        input_path = FIXTURES / "obs04-01-positive-input.json"
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
