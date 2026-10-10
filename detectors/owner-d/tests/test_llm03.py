"""Behavioral tests for the LLM-03 detector (issue #196)."""

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
from owner_d.llm03 import (
    CHECK_ID, DETECTOR_VERSION, LIMITATION, REFERENCE_SETTINGS, EvaluationError, evaluate, fingerprint,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm03"
REPOSITORY_ID = "github:AWS-env/example"
# The fixtures use a small size budget (800 characters) so that oversized prompts stay readable.
CONTEXT = {"language": "python", "max_system_prompt_tokens": 200, "min_repeated_instruction_chars": 40}


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
        "scan_id": "scan-llm03-001",
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


class Llm03PositiveTests(unittest.TestCase):
    """LLM03-01: oversized or redundant system prompts are flagged with exact evidence."""

    REPEAT = "repeats an instruction within it"
    EXPECTED = {
        "support:anthropic.messages.create": (
            41, None, "medium", 'e.g. "Always answer in valid JSON with the keys answer and sources." (2 times)'),
        "plan_trip:openai.chat.completions.create": (
            52, None, "medium", 'repeats 2 instructions within it, e.g. "Recommend trains over flights'),
        "book:openai.responses.create": (
            56, None, "low", "is about 204 tokens (estimated from its literal text; more than 200)"),
        "summarise:openai.chat.completions.create": (61, None, "medium", REPEAT),
        "converse:bedrock.converse": (
            65, 72, "medium", "Translate the user's message into formal business German."),
        "invoke:bedrock.invoke_model": (77, None, "medium", REPEAT),
        "injected:openai.chat.completions.create": (81, None, "low", "OpenAI-compatible"),
    }

    def test_bloated_prompts_are_flagged_with_exact_evidence(self):
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
        self.assertNotIn("tokens", findings["support:anthropic.messages.create"]["summary"])
        self.assertNotIn("repeats", findings["book:openai.responses.create"]["summary"])
        self.assertEqual(result["measurements"], [])


class Llm03NegativeTests(unittest.TestCase):
    """LLM03-02: short or distinct prompts, user-message repeats and other SDKs are clean."""

    def test_similar_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])


class Llm03ExceptionTests(unittest.TestCase):
    """LLM03-03: examples, code blocks, templates, unknown prompts, noqa and prompt caching."""

    def test_examples_templates_and_unknown_prompts_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:anthropic.messages.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 88)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 88))

    def test_caching_exempts_size_but_not_repeats(self):
        _, result = run("cached.py")
        self.assertEqual([f["identity"] for f in result["findings"]], ["repeated:anthropic.messages.create"])
        finding = result["findings"][0]
        self.assertEqual(finding["evidence"][0]["line_start"], 28)
        self.assertIn('"Point new engineers to the runbook before they change any alert threshold in pr…"', finding["summary"])
        self.assertNotIn("tokens", finding["summary"])
        # Without the cache markers the same prompts are over the size budget.
        content = (FIXTURES / "cached.py").read_text().replace(', "cache_control": {"type": "ephemeral"}', "")
        _, result = run(sources=[static_source("cached.py", content)], scope=["file:cached.py"])
        self.assertEqual(len(result["findings"]), 2)
        self.assertTrue(all("tokens (estimated" in f["summary"] for f in result["findings"]))


class Llm03IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM03-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/agent.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """LLM03-04: the thresholds are required judgment calls; without them nothing is evaluated."""
        for context, reason in (
            ({"language": "python"},
             "missing required context settings: max_system_prompt_tokens, min_repeated_instruction_chars"),
            ({**CONTEXT, "max_system_prompt_tokens": 0}, "context.max_system_prompt_tokens must be a positive integer"),
            ({**CONTEXT, "min_repeated_instruction_chars": "40"},
             "context.min_repeated_instruction_chars must be a positive integer"),
            ({**CONTEXT, "max_system_prompt_tokens": True}, "context.max_system_prompt_tokens must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_reference_settings_are_valid(self):
        self.assertEqual(REFERENCE_SETTINGS, {"max_system_prompt_tokens": 4000, "min_repeated_instruction_chars": 40})
        _, result = run("positive.py", context={"language": "python", **REFERENCE_SETTINGS})
        self.assertEqual(result["status"], "completed")
        # The 204-token prompt is under the 4,000-token reference budget; the repeats stay.
        self.assertEqual(len(result["findings"]), len(Llm03PositiveTests.EXPECTED) - 1)

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM03-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm03BoundaryTests(unittest.TestCase):
    """LLM03-06: strict size threshold, inclusive repeat length, repeated anchors and line shifts."""

    def test_thresholds(self):
        _, result = run("boundary.py")
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            [
                "over_budget:anthropic.messages.create",
                "repeats:anthropic.messages.create",
                "repeats:anthropic.messages.create#2",
            ],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [31, 35, 36])
        self.assertIn("about 201 tokens (estimated from its literal text; more than 200)", result["findings"][0]["summary"])
        self.assertIn('"Write the reply in plain British English." (2 times)', result["findings"][1]["summary"])

    def test_settings_move_the_boundaries(self):
        _, result = run("boundary.py", context={**CONTEXT, "max_system_prompt_tokens": 199})
        self.assertIn("at_budget:anthropic.messages.create", by_identity(result))
        _, result = run("boundary.py", context={**CONTEXT, "max_system_prompt_tokens": 201})
        self.assertNotIn("over_budget:anthropic.messages.create", by_identity(result))
        _, result = run("boundary.py", context={**CONTEXT, "min_repeated_instruction_chars": 39})
        self.assertIn("short_repeats:anthropic.messages.create", by_identity(result))
        _, result = run("boundary.py", context={**CONTEXT, "min_repeated_instruction_chars": 41})
        self.assertEqual([f["identity"] for f in result["findings"]], ["over_budget:anthropic.messages.create"])

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


class Llm03ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "support:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "claude.messages.create(system=INVENTED)"
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
        committed = json.loads((FIXTURES / "llm03-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm03CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm03_result(self):
        input_path = FIXTURES / "llm03-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm03PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
