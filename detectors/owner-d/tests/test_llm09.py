"""Behavioral tests for the LLM-09 detector (issue #202)."""

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
from owner_d.llm09 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm09"
REPOSITORY_ID = "github:AWS-env/example"
IDENTITY = "module:token-usage"
NEAR_MISSES = ("near_miss/requirements.txt", "near_miss/logs.tf", "near_miss/k8s.yaml", "near_miss/tracing.py")
MARKERS = {
    "negative.py": "reads token usage",
    "markers/otel_setup.py": "imports opentelemetry.instrumentation.botocore (GenAI OpenTelemetry instrumentation), line 3",
    "markers/requirements.txt": "GenAI OpenTelemetry instrumentation (opentelemetry-instrumentation-openai-v2), line 3",
    "markers/bedrock_logging.tf": "Bedrock model invocation logging (invocation_logging_configuration), line 6",
    "markers/Dockerfile": "zero-code instrumentation (opentelemetry-instrument), line 4",
    "markers/request_metadata.py": "Bedrock requestMetadata (invocation-log attribution), line 12",
    "markers/span_attributes.py": "token telemetry ('gen_ai.usage.'), line 5",
    "markers/powertools_metrics.py": "reads token usage ('inputTokens'), line 13",
    "markers/langfuse_client.py": "imports langfuse.openai (LLM observability SDK), line 3",
    "boundary/print_response.py": "logs a whole LLM response (print()), line 9",
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
        "scan_id": "scan-llm09-001",
        "commit_sha": "9999999999999999999999999999999999999999",
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


def flagged(result):
    return [finding["scope_id"] for finding in result["findings"]]


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm09PositiveTests(unittest.TestCase):
    """LLM09-01: modules that only read response text are flagged once each, with exact evidence."""

    EXPECTED = {
        "positive.py": (11, 15, "4 LLM call(s)", (
            "Anthropic messages.create, Anthropic messages.stream, OpenAI chat.completions.create, "
            "OpenAI responses.create"
        )),
        "bedrock_app.py": (12, 16, "3 LLM call(s)", "Bedrock converse, Bedrock converse_stream, Bedrock invoke_model"),
    }

    def test_dropped_usage_is_flagged_per_module(self):
        _, result = run("positive.py", "bedrock_app.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py", "file:bedrock_app.py"])
        self.assertEqual(result["coverage"]["limitations"], [LIMITATION])
        self.assertEqual(flagged(result), ["file:positive.py", "file:bedrock_app.py"])
        for finding in result["findings"]:
            name = finding["scope_id"].removeprefix("file:")
            line, end, count, kinds = self.EXPECTED[name]
            with self.subTest(name=name):
                self.assertEqual(finding["identity"], IDENTITY)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, finding["scope_id"], IDENTITY))
                self.assertEqual(finding["confidence"], "low")  # no manifest or IaC file was supplied
                self.assertIn(f"{count} in this module ({kinds}) discard the token usage", finding["summary"])
                [evidence] = finding["evidence"]
                self.assertEqual((evidence["kind"], evidence["source_id"]), ("static", f"src:{name}"))
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], evidence_text(name, line, end))
                self.assertTrue(finding["references"])
        bedrock = result["findings"][1]["summary"]
        self.assertIn("InputTokenCount/OutputTokenCount metrics give per-model totals only", bedrock)
        self.assertNotIn("per-model totals", result["findings"][0]["summary"])
        self.assertEqual(result["measurements"], [])

    def test_near_miss_markers_do_not_suppress_and_raise_confidence(self):
        _, result = run("positive.py", *NEAR_MISSES)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 5)
        self.assertEqual(flagged(result), ["file:positive.py"])
        self.assertEqual(result["findings"][0]["confidence"], "medium")


class Llm09NegativeTests(unittest.TestCase):
    """LLM09-02: a usage read anywhere in the payload means no module is flagged."""

    def test_usage_read_is_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_usage_read_in_another_file_suppresses_payload(self):
        _, result = run("positive.py", "bedrock_app.py", "negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        [note] = result["coverage"]["limitations"][:-1]
        self.assertIn("Token observability is visible in negative.py: reads token usage", note)
        self.assertTrue(note.endswith("no module was flagged."))


class Llm09ExceptionTests(unittest.TestCase):
    """LLM09-03: markers, escaping responses, exempt paths and noqa."""

    def test_each_marker_suppresses_findings_with_its_reason(self):
        for name, reason in MARKERS.items():
            with self.subTest(marker=name):
                _, result = run("positive.py", name)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
                self.assertIn(f"Token observability is visible in {name}: {reason}", result["coverage"]["limitations"][0])

    def test_several_marker_files_are_counted(self):
        _, result = run("positive.py", "markers/otel_setup.py", "markers/Dockerfile")
        self.assertIn("markers/otel_setup.py (and 1 other file(s))", result["coverage"]["limitations"][0])

    def test_escaping_response_makes_module_unknown(self):
        names = ["escapes/returned.py", "escapes/passed.py", "escapes/stored.py", "escapes/serialized.py",
                 "escapes/closure.py", "boundary/final_returned.py"]
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["findings"], [])

    def test_tests_samples_scripts_and_main_blocks_are_exempt(self):
        _, result = run("tests/test_summary.py", "examples/quickstart.py", "scripts/backfill.py", "main_block.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 4)
        self.assertEqual(result["findings"], [])

    def test_noqa_for_this_check_only(self):
        _, result = run("noqa_suppressed.py", "noqa_other.py")
        self.assertEqual(flagged(result), ["file:noqa_other.py"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 9)
        self.assertEqual(evidence["value"], evidence_text("noqa_other.py", 9))

    def test_vendored_sdk_code_is_not_a_marker(self):
        _, result = run("positive.py", "site-packages/anthropic/types.py")
        self.assertEqual(flagged(result), ["file:positive.py"])


class Llm09IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM09-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/assistant.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertIn("file:app/assistant.py: no static source supplied for this scope item", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM09-05: parse failures and unsupported files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js", "near_miss/requirements.txt")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py", "file:near_miss/requirements.txt"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported file type", limitations)
        # The unparseable file could hide a marker, so the finding stays low even with a manifest.
        self.assertEqual(flagged(result), ["file:positive.py"])
        self.assertEqual(result["findings"][0]["confidence"], "low")

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Llm09BoundaryTests(unittest.TestCase):
    """LLM09-06: what counts as dropping vs passing on the usage, and stable identities."""

    def test_printing_text_drops_usage_but_printing_response_records_it(self):
        _, result = run("boundary/print_text.py")
        self.assertEqual(flagged(result), ["file:boundary/print_text.py"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 9)
        _, result = run("boundary/print_text.py", "boundary/print_response.py")
        self.assertEqual(result["findings"], [])

    def test_one_escaping_call_keeps_the_module_unflagged(self):
        _, result = run("boundary/final_returned.py")
        self.assertEqual(result["findings"], [])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("positive.py")
        shifted = "\n\n\n" + (FIXTURES / "positive.py").read_text()
        _, after = run(sources=[static_source("positive.py", shifted)], scope=["file:positive.py"])
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(after["findings"][0]["evidence"][0]["line_start"], 14)


class Llm09ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", IDENTITY)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "claude.messages.create(model=INVENTED)"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "bedrock_app.py", *NEAR_MISSES)
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "llm09-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py", "bedrock_app.py"))


class Llm09CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm09_result(self):
        input_path = FIXTURES / "llm09-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm09PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
