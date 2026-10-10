"""Behavioral tests for the LLM-08 detector (issue #201)."""

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
from owner_d.llm08 import (
    CHECK_ID, DETECTOR_VERSION, LIMITATION, REFERENCE_SETTINGS, EvaluationError, evaluate, fingerprint, top_tier,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm08"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_simple_output_tokens": 256, "max_simple_prompt_tokens": 500}


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
        "scan_id": "scan-llm08-001",
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


def run_text(name, content, context=None):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"], context=context)


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm08PositiveTests(unittest.TestCase):
    """LLM08-01: top-tier models hard-coded for simple tasks are flagged with exact evidence."""

    EXPECTED = {
        "sentiment:anthropic.messages.create": (16, None, "medium", ("'claude-opus-5-5'", "output capped at 10 tokens", "'classify'")),
        "is_spam:openai.chat.completions.create": (21, None, "medium", ("'gpt-5-pro'", "capped at 5 tokens", "'yes or no'")),
        "ticket_category:openai.responses.create": (25, None, "low", ("'o3-pro'", "'categorize'")),
        "triage:bedrock.converse": (29, 33, "low", ("us.anthropic.claude-opus-4-1", "capped at 64 tokens")),
        "order_number:bedrock.invoke_model": (38, None, "medium", ("Nova Premier", "capped at 20 tokens", "'extract the'")),
        "language:openai.chat.completions.create": (42, None, "low", ("OpenAI-compatible", "'gpt-5.5-pro'")),
    }

    def test_fixed_top_tier_models_on_simple_tasks_are_flagged(self):
        _, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        findings = by_identity(result)
        self.assertEqual(set(findings), set(self.EXPECTED))
        for identity, (line, end, confidence, phrases) in self.EXPECTED.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                for phrase in phrases:
                    self.assertIn(phrase, finding["summary"])
                self.assertIn("no model selection in this file", finding["summary"])
                self.assertIn("quality and cost are not measured", finding["summary"])
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
        self.assertNotIn("capped", findings["ticket_category:openai.responses.create"]["summary"])
        self.assertNotIn("static prompt", findings["triage:bedrock.converse"]["summary"])
        self.assertEqual(result["measurements"], [])

    def test_tier_table_recognises_platform_spellings(self):
        for model in (
            "claude-opus-4-1", "claude-opus-4-1@20250805", "anthropic.claude-opus-4-1-20250805-v1:0",
            "global.anthropic.claude-opus-4-5-20251101-v1:0", "claude-3-opus-20240229", "claude-fable-5-1",
            "claude-mythos-5-1", "us.amazon.nova-premier-v1:0", "o1-pro", "gpt-5-pro-2025-10-06",
        ):
            with self.subTest(model=model):
                self.assertIsNotNone(top_tier(model))
        for model in (
            "claude-sonnet-5-5", "claude-haiku-4-5", "claude-3-5-sonnet-20241022", "amazon.nova-pro-v1:0",
            "gpt-5", "gpt-5-mini", "o3", "o4-mini", "my-opus-deployment", "gpt-5-professional",
        ):
            with self.subTest(model=model):
                self.assertIsNone(top_tier(model))


class Llm08NegativeTests(unittest.TestCase):
    """LLM08-02: complex, agentic, tuned or non-top-tier calls are clean."""

    def test_top_tier_calls_that_do_not_look_simple_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_simple_tasks_on_smaller_models_are_clean(self):
        _, result = run("smaller.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_dropping_the_tuning_restores_a_finding(self):
        text = (FIXTURES / "negative.py").read_text()
        _, result = run_text("negative.py", text.replace('output_config={"effort": "low"}, ', ""))
        self.assertEqual([f["identity"] for f in result["findings"]], ["tuned_classifier:anthropic.messages.create"])
        self.assertEqual(result["findings"][0]["confidence"], "medium")


class Llm08ExceptionTests(unittest.TestCase):
    """LLM08-03: runtime model choice, model routing in the file, opaque requests and noqa."""

    def test_models_chosen_at_runtime_and_opaque_requests_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:anthropic.messages.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 49)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 49))

    def test_file_that_also_uses_a_smaller_model_is_not_flagged(self):
        _, result = run("routed.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        text = (FIXTURES / "routed.py").read_text().replace('"claude-haiku-4-5"', '"claude-opus-5-5"')
        _, result = run_text("routed.py", text)
        findings = by_identity(result)
        self.assertEqual(set(findings), {"sentiment:anthropic.messages.create", "clause_risk:anthropic.messages.create"})
        self.assertEqual(findings["clause_risk:anthropic.messages.create"]["confidence"], "medium")

    def test_file_with_a_prompt_router_is_not_flagged(self):
        _, result = run("router.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        text = (FIXTURES / "router.py").read_text().replace("default-prompt-router", "inference-profile")
        _, result = run_text("router.py", text)
        self.assertEqual([f["identity"] for f in result["findings"]], ["pinned:bedrock.converse"])


class Llm08IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM08-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/classify.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """LLM08-04: the thresholds are required judgment calls; without them nothing is evaluated."""
        for context, reason in (
            ({"language": "python"}, "missing required context settings: max_simple_output_tokens, max_simple_prompt_tokens"),
            ({**CONTEXT, "max_simple_output_tokens": 0}, "context.max_simple_output_tokens must be a positive integer"),
            ({**CONTEXT, "max_simple_prompt_tokens": "500"}, "context.max_simple_prompt_tokens must be a positive integer"),
            ({**CONTEXT, "max_simple_output_tokens": True}, "context.max_simple_output_tokens must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM08-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertEqual(len(result["findings"]), len(Llm08PositiveTests.EXPECTED))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Llm08BoundaryTests(unittest.TestCase):
    """LLM08-06: inclusive thresholds, repeated anchors and line shifts."""

    def test_thresholds_are_inclusive(self):
        _, result = run("boundary.py")
        findings = result["findings"]
        self.assertEqual(
            [f["identity"] for f in findings],
            [
                "cap_at_limit:anthropic.messages.create",
                "prompt_at_limit:anthropic.messages.create",
                "twice:anthropic.messages.create",
                "twice:anthropic.messages.create#2",
            ],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in findings], [10, 18, 26, 27])
        self.assertIn("output capped at 256 tokens (at most 256)", findings[0]["summary"])
        self.assertIn("about 500 tokens, estimated", findings[1]["summary"])
        self.assertIn("output capped at 1 token (at most 256)", findings[2]["summary"])

    def test_settings_move_the_thresholds(self):
        context = {**CONTEXT, "max_simple_output_tokens": 257, "max_simple_prompt_tokens": 501}
        _, result = run("boundary.py", context=context)
        self.assertEqual(
            {f["identity"] for f in result["findings"]},
            {
                "cap_at_limit:anthropic.messages.create", "cap_over_limit:anthropic.messages.create",
                "prompt_at_limit:anthropic.messages.create", "prompt_over_limit:anthropic.messages.create",
                "twice:anthropic.messages.create", "twice:anthropic.messages.create#2",
            },
        )
        context = {**CONTEXT, "max_simple_output_tokens": 255, "max_simple_prompt_tokens": 499}
        _, result = run("boundary.py", context=context)
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            ["twice:anthropic.messages.create", "twice:anthropic.messages.create#2"],
        )

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        _, after = run_text("boundary.py", "\n\n\n" + (FIXTURES / "boundary.py").read_text())
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm08ContractTests(unittest.TestCase):
    def test_reference_settings_match_the_test_context(self):
        self.assertEqual(REFERENCE_SETTINGS, {key: CONTEXT[key] for key in REFERENCE_SETTINGS})

    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "sentiment:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = 'claude.messages.create(model="claude-opus-5-5")'
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
        committed = json.loads((FIXTURES / "llm08-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm08CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm08_result(self):
        input_path = FIXTURES / "llm08-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm08PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
