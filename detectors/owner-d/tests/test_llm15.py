"""Behavioral tests for the LLM-15 detector (issue #208)."""

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
from owner_d.llm15 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm15"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_tools_per_call": 20, "max_tool_definition_tokens": 10000}


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
        "scan_id": "scan-llm15-001",
        "commit_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
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


class Llm15PositiveTests(unittest.TestCase):
    """LLM15-01: calls loading more tools than allowed are flagged with exact evidence."""

    EXPECTED = {
        "plan:anthropic.messages.create": (15, None, "medium", "24 tool definitions (more than 20)"),
        "summarise:anthropic.messages.create": (19, None, "medium", "same registry is passed to 2 calls"),
        "chat:openai.chat.completions.create": (24, None, "medium", "24 tool definitions"),
        "respond:openai.responses.create": (28, None, "medium", "26 tool definitions"),
        "converse:bedrock.converse": (33, 37, "medium", "24 tool definitions"),
        "invoke:bedrock.invoke_model": (42, None, "medium", "24 tool definitions"),
        "injected:openai.chat.completions.create": (46, None, "low", "OpenAI-compatible"),
    }

    def test_tool_sprawl_is_flagged_with_exact_evidence(self):
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
        self.assertNotIn("same registry", findings["chat:openai.chat.completions.create"]["summary"])
        self.assertEqual(result["measurements"], [])


class Llm15NegativeTests(unittest.TestCase):
    """LLM15-02: small, filtered or sliced lists, other SDKs and deferred loading are clean."""

    def test_similar_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_tool_search_with_deferred_loading_is_clean(self):
        _, result = run("deferred.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Llm15ExceptionTests(unittest.TestCase):
    """LLM15-03: tool lists from callers/MCP, mutated lists, **kwargs, extra_body and noqa."""

    def test_uncountable_tool_lists_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:anthropic.messages.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 38)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 38))


class Llm15IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM15-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/agent.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """LLM15-04: the thresholds are required judgment calls; without them nothing is evaluated."""
        for context, reason in (
            ({"language": "python"}, "missing required context settings: max_tools_per_call, max_tool_definition_tokens"),
            ({**CONTEXT, "max_tools_per_call": 0}, "context.max_tools_per_call must be a positive integer"),
            ({**CONTEXT, "max_tool_definition_tokens": "10000"}, "context.max_tool_definition_tokens must be a positive integer"),
            ({**CONTEXT, "max_tools_per_call": True}, "context.max_tools_per_call must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM15-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm15BoundaryTests(unittest.TestCase):
    """LLM15-06: strict thresholds, the token trigger, repeated anchors and line shifts."""

    def test_count_threshold_is_strict(self):
        _, result = run("boundary.py")
        findings = result["findings"]
        self.assertEqual(
            [f["identity"] for f in findings],
            ["over_limit:anthropic.messages.create", "over_limit:anthropic.messages.create#2"],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in findings], [12, 13])
        self.assertTrue(all("21 tool definitions (more than 20)" in f["summary"] for f in findings))

    def test_token_threshold_flags_few_but_large_definitions(self):
        context = {**CONTEXT, "max_tools_per_call": 30}
        _, result = run("boundary.py", context={**context, "max_tool_definition_tokens": 191})
        findings = by_identity(result)
        # The 20/21-tool registries are about 645/672 tokens, so every call is over 191.
        self.assertEqual(len(findings), 4)
        finding = findings["few_but_large:anthropic.messages.create"]
        self.assertEqual(finding["evidence"][0]["line_start"], 17)
        self.assertIn("about 192 tokens of tool definitions (estimated; more than 191)", finding["summary"])
        self.assertNotIn("tool definitions (more than", finding["summary"])
        _, result = run("boundary.py", context={**context, "max_tool_definition_tokens": 192})
        self.assertNotIn("few_but_large:anthropic.messages.create", by_identity(result))
        self.assertEqual(len(result["findings"]), 3)
        _, result = run("boundary.py", context={**context, "max_tool_definition_tokens": 10000})
        self.assertEqual(result["findings"], [])

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


class Llm15ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "plan:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "claude.messages.create(tools=INVENTED)"
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
        committed = json.loads((FIXTURES / "llm15-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm15CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm15_result(self):
        input_path = FIXTURES / "llm15-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm15PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
