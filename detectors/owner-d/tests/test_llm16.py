"""Behavioral tests for the LLM-16 detector (issue #209)."""

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
from owner_d.llm16 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm16"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_buffered_output_tokens": 256}


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
        "scan_id": "scan-llm16-001",
        "commit_sha": "ffffffffffffffffffffffffffffffffffffffff",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm16PositiveTests(unittest.TestCase):
    """LLM16-01: non-streaming calls in request handlers are flagged with exact evidence."""

    EXPECTED = {
        "chat:anthropic.messages.create": (24, None, "medium", "up to 4,096 output tokens"),
        "answer:openai.chat.completions.create": (30, None, "medium", "with no output-token cap"),
        "respond:openai.responses.create": (35, None, "medium", "up to 2,000 output tokens"),
        "summary:bedrock.converse": (40, 44, "medium", "the streaming form is converse_stream()"),
        "invoke:bedrock.invoke_model": (51, None, "medium", "invoke_model_with_response_stream()"),
        "draft_reply:anthropic.messages.create": (55, None, "low", "draft_reply(), which request handler reply() calls"),
        "compare:openai.chat.completions.create": (65, None, "low", "OpenAI-compatible"),
    }

    def test_buffered_calls_in_handlers_are_flagged_with_exact_evidence(self):
        _, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        findings = by_identity(result)
        self.assertEqual(set(findings), set(self.EXPECTED))
        for identity, (line, end, confidence, phrase) in self.EXPECTED.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(phrase, finding["summary"])
                self.assertIn("does not stream", finding["summary"])
                self.assertEqual(
                    finding["fingerprint"],
                    fingerprint(REPOSITORY_ID, CHECK_ID, "file:positive.py", identity),
                )
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], "src:positive.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], evidence_text("positive.py", line, end))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Llm16NegativeTests(unittest.TestCase):
    """LLM16-02: streaming handlers, other SDKs and code that serves no request are clean."""

    def test_similar_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])


class Llm16ExceptionTests(unittest.TestCase):
    """LLM16-03: structured/forced-tool replies, unknown arguments, indirect helpers and noqa."""

    def test_replies_that_must_be_complete_or_unknown_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:openai.chat.completions.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 98)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 98))


class Llm16IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM16-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/api.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """LLM16-04: the output-cap threshold is a required judgment call."""
        for context, reason in (
            ({"language": "python"}, "missing required context settings: max_buffered_output_tokens"),
            ({**CONTEXT, "max_buffered_output_tokens": 0}, "context.max_buffered_output_tokens must be a positive integer"),
            ({**CONTEXT, "max_buffered_output_tokens": "256"}, "context.max_buffered_output_tokens must be a positive integer"),
            ({**CONTEXT, "max_buffered_output_tokens": True}, "context.max_buffered_output_tokens must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM16-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Llm16BoundaryTests(unittest.TestCase):
    """LLM16-06: strict output-cap threshold, free tool choice, repeated anchors and line shifts."""

    def test_output_cap_threshold_is_strict(self):
        _, result = run("boundary.py")
        findings = result["findings"]
        self.assertEqual(
            [f["identity"] for f in findings],
            [
                "over_limit:anthropic.messages.create",
                "over_limit:anthropic.messages.create#2",
                "bedrock_free:bedrock.converse",
            ],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in findings], [19, 20, 27])
        self.assertIn("up to 257 output tokens", findings[0]["summary"])
        self.assertIn("with no output-token cap", findings[2]["summary"])

    def test_threshold_setting_moves_the_boundary(self):
        _, result = run("boundary.py", context={**CONTEXT, "max_buffered_output_tokens": 257})
        self.assertEqual([f["identity"] for f in result["findings"]], ["bedrock_free:bedrock.converse"])
        _, result = run("boundary.py", context={**CONTEXT, "max_buffered_output_tokens": 255})
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [14, 19, 20, 25, 27])
        _, result = run("positive.py", context={**CONTEXT, "max_buffered_output_tokens": 8192})
        self.assertEqual(sorted(by_identity(result)), ["answer:openai.chat.completions.create"])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        _, after = run(sources=[static_source("boundary.py", shifted)], scope=["file:boundary.py"])
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm16ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "chat:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "client.chat.completions.create(stream=INVENTED)"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "llm16-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm16CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm16_result(self):
        input_path = FIXTURES / "llm16-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm16PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
