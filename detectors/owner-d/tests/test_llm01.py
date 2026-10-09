"""Behavioral tests for the LLM-01 detector (issue #194)."""

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
from owner_d.llm01 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, claude_minimum, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm01"
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
        "scan_id": "scan-llm01-001",
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


def run_source(content, name="inline.py"):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


def prompt_source(chars, model="claude-sonnet-4-5", call=None):
    """A file whose static system prompt has exactly `chars` characters."""
    call = call or f'claude.messages.create(model={model!r}, max_tokens=64, system=PROMPT, messages=question)'
    return (
        "import anthropic\nimport boto3\n\nclaude = anthropic.Anthropic()\nbedrock = boto3.client('bedrock-runtime')\n"
        f"PROMPT = {'p' * chars!r}\n\n\ndef ask(question):\n    return {call}\n"
    )


class Llm01PositiveTests(unittest.TestCase):
    """LLM01-01: large static prefixes without a cache marker are flagged with exact evidence."""

    EXPECTED = {
        "answer:anthropic.messages.create": (16, 21, "medium", "(system prompt) of about 1,175 tokens"),
        "plan_with_tools:anthropic.messages.create": (25, None, "medium", "(tool definitions and system prompt)"),
        "bedrock_answer:bedrock.converse": (29, None, "low", "no cachePoint"),
        "nova_stream:bedrock.converse_stream": (34, None, "low", "no cachePoint"),
        "invoke_claude:bedrock.invoke_model": (44, None, "low", "no cache_control block"),
        "grounded:anthropic.messages.create": (49, None, "medium", "(leading messages)"),
        "Triage.run:anthropic.messages.create": (58, None, "medium", "caches prefixes from 512 tokens"),
    }

    def test_uncached_static_prefixes_are_flagged_with_exact_evidence(self):
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
                self.assertEqual(finding["scope_id"], "file:positive.py")
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


class Llm01NegativeTests(unittest.TestCase):
    """LLM01-02: small or dynamic prefixes, OpenAI, uncacheable models and one-shot calls are clean."""

    def test_similar_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_cached_prefixes_are_clean(self):
        _, result = run("cached.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        # Without the cache markers the same file would be flagged.
        content = (FIXTURES / "cached.py").read_text()
        for marker in ('cache_control=CACHE, ', ', "cache_control": CACHE', ', {"cachePoint": {"type": "default"}}'):
            self.assertIn(marker, content)
            content = content.replace(marker, "")
        _, stripped = run_source(content, "cached.py")
        self.assertEqual(len(stripped["findings"]), 4)


class Llm01ExceptionTests(unittest.TestCase):
    """LLM01-03: unresolvable tools/system/body, **kwargs, extra_body, managed prompts and noqa."""

    def test_unknown_prefixes_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:anthropic.messages.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 53)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 53))


class Llm01IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM01-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/agent.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM01-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertTrue(all(f["scope_id"] == "file:positive.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Llm01BoundaryTests(unittest.TestCase):
    """LLM01-06: minimum-length thresholds, model tables, repeated anchors and line shifts."""

    def flagged(self, content):
        return [f["identity"] for f in run_source(content)[1]["findings"]]

    def test_threshold_is_the_model_minimum_at_four_characters_per_token(self):
        self.assertEqual(self.flagged(prompt_source(4 * 1024)), ["ask:anthropic.messages.create"])
        self.assertEqual(self.flagged(prompt_source(4 * 1024 - 1)), [])
        self.assertEqual(self.flagged(prompt_source(4 * 512, "claude-opus-5-5")), ["ask:anthropic.messages.create"])
        self.assertEqual(self.flagged(prompt_source(4 * 512 - 1, "claude-opus-5-5")), [])

    def test_unknown_model_uses_the_largest_documented_minimum(self):
        call = "claude.messages.create(model=question.model, max_tokens=64, system=PROMPT, messages=question)"
        self.assertEqual(self.flagged(prompt_source(4 * 4096, call=call)), ["ask:anthropic.messages.create"])
        self.assertEqual(self.flagged(prompt_source(4 * 4096 - 1, call=call)), [])

    def test_bedrock_requires_a_model_with_explicit_caching(self):
        def converse(model):
            return f"bedrock.converse(modelId={model!r}, system=[{{'text': PROMPT}}], messages=question)"

        big = 4 * 4096
        self.assertEqual(self.flagged(prompt_source(big, call=converse("anthropic.claude-haiku-4-5-20251001-v1:0"))),
                         ["ask:bedrock.converse"])
        for model in ("anthropic.claude-3-5-sonnet-20240620-v1:0", "us.anthropic.claude-sonnet-4-20250514-v1:0",
                      "mistral.mistral-large-2407-v1:0", "amazon.nova-premier-v1:0"):
            with self.subTest(model=model):
                self.assertEqual(self.flagged(prompt_source(big, call=converse(model))), [])

    def test_nova_tool_definitions_do_not_count_towards_the_prefix(self):
        tools = "{'tools': [{'toolSpec': {'name': 'n', 'description': PROMPT, 'inputSchema': {'json': {}}}}]}"
        for model, expected in (("amazon.nova-pro-v1:0", []), ("anthropic.claude-sonnet-4-5-20250929-v1:0", ["ask:bedrock.converse"])):
            call = f"bedrock.converse(modelId={model!r}, toolConfig={tools}, system=[{{'text': 'Be brief.'}}], messages=question)"
            with self.subTest(model=model):
                self.assertEqual(self.flagged(prompt_source(4 * 1100, call=call)), expected)

    def test_claude_model_minimums(self):
        cases = {
            ("claude-sonnet-4-5-20250929", False): 1024,
            ("claude-opus-4-20250514", False): 1024,
            ("claude-opus-4-7", False): 4096,
            ("claude-haiku-4-5", False): 4096,
            ("claude-opus-5-5", False): 512,
            ("claude-3-5-haiku-latest", False): 2048,
            ("claude-3-opus-20240229", False): None,
            ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", True): 1024,
            ("anthropic.claude-3-5-sonnet-20241022-v2:0", True): 1024,
            ("anthropic.claude-3-5-sonnet-20240620-v1:0", True): None,
            ("us.anthropic.claude-opus-4-1-20250805-v1:0", True): None,
        }
        for (model, bedrock), expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(claude_minimum(model, bedrock), expected)

    def test_repeated_calls_get_distinct_identities_and_main_is_one_shot(self):
        _, result = run("boundary.py")
        findings = result["findings"]
        self.assertEqual(
            [f["identity"] for f in findings],
            ["pipeline:anthropic.messages.create", "pipeline:anthropic.messages.create#2", "main:anthropic.messages.create"],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in findings], [9, 10, 16])

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


class Llm01ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "answer:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "claude.messages.create(model='invented')"
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
        committed = json.loads((FIXTURES / "llm01-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm01CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm01_result(self):
        input_path = FIXTURES / "llm01-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm01PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
