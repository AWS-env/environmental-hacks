"""Behavioral tests for the LLM-04 detector (issue #197)."""

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
from owner_d.llm04 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm04"
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
        "scan_id": "scan-llm04-001",
        "commit_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
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


def fixture_function(name, function):
    """Source of the imports/clients header plus one function of a fixture."""
    text = (FIXTURES / name).read_text()
    header = text.split("\n\n\ndef ", 1)[0]
    body = next(part for part in text.split("\n\n\n") if part.startswith(f"def {function}("))
    return f"{header}\n\n\n{body}\n"


class Llm04PositiveTests(unittest.TestCase):
    """LLM04-01: later steps that receive the whole history or document again are flagged."""

    EXPECTED = {
        "research:anthropic.messages.create": (20, None, "medium", "conversation `messages`, which step 1 (line 17)"),
        "research:anthropic.messages.create#2": (22, None, "medium", "which step 2 (line 20) already received, grown"),
        "draft_and_critique:openai.chat.completions.create": (27, 30, "medium", "with new messages appended"),
        "converse_pipeline:bedrock.converse": (37, None, "medium", "conversation `conversation`"),
        "respond_twice:openai.responses.create": (43, None, "medium", "conversation `history`"),
        "contract_review:openai.chat.completions.create": (51, None, "low", "re-embeds the whole `contract_text`"),
        "invoke_pipeline:bedrock.invoke_model": (62, None, "medium", "conversation `chat_log`"),
        "injected:openai.chat.completions.create": (68, None, "low", "OpenAI-compatible"),
    }

    def test_whole_context_steps_are_flagged_with_exact_evidence(self):
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

    def test_document_finding_names_the_earlier_output(self):
        _, result = run("positive.py")
        summary = by_identity(result)["contract_review:openai.chat.completions.create"]["summary"]
        self.assertIn("step 2 of 3 in contract_review", summary)
        self.assertIn("already sent to step 1 (line 48)", summary)
        self.assertIn("earlier step's output (`risk_prompt`)", summary)


class Llm04NegativeTests(unittest.TestCase):
    """LLM04-02: slices, summaries, output-only chains, repeated requests and other SDKs are clean."""

    def test_scoped_context_is_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_negatives_flip_when_the_whole_context_is_sent(self):
        """The clean cases are clean for the stated reason: each one-token change is flagged."""
        variants = {
            "sliced": ("messages=history[-4:]", "messages=history"),
            "summarised": ("    history = [{", "    other = [{"),
            "short_inputs": ("question", "document"),
            "excerpt": ("{document[:2000]}", "{document}"),
        }
        for function, (old, new) in variants.items():
            with self.subTest(function=function):
                source = fixture_function("negative.py", function)
                self.assertEqual(run_source(source)[1]["findings"], [])
                _, result = run_source(source.replace(old, new))
                self.assertEqual(len(result["findings"]), 1, result["findings"])


class Llm04ExceptionTests(unittest.TestCase):
    """LLM04-03: tool turns, alternatives, trimming, final synthesis, unknown requests, caching and noqa."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:openai.chat.completions.create"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 78)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 78))

    def test_exceptions_flip_without_their_guard(self):
        variants = {
            "tool_continuation": ('"type": "tool_result", ', ""),
            "alternatives": ("    else:\n", "    if not short:\n"),
            "trimmed": ("    trim(history)\n", ""),
            "compacted": (", context_management=edits", ""),
        }
        for function, (old, new) in variants.items():
            with self.subTest(function=function):
                source = fixture_function("exceptions.py", function)
                self.assertIn(old, source)
                self.assertEqual(run_source(source)[1]["findings"], [])
                self.assertEqual(len(run_source(source.replace(old, new))[1]["findings"]), 1)

    def test_prompt_caching_in_the_file_suppresses_findings(self):
        _, result = run("cached.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        uncached = (FIXTURES / "cached.py").read_text().replace(', "cache_control": CACHE', "")
        self.assertEqual([f["identity"] for f in run_source(uncached)[1]["findings"]], ["research:anthropic.messages.create"])


class Llm04IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM04-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/pipeline.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM04-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm04BoundaryTests(unittest.TestCase):
    """LLM04-06: smallest pipelines, the final-step exemption, scope boundaries and stable identities."""

    def test_pipeline_boundaries(self):
        _, result = run("boundary.py")
        self.assertEqual(
            [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]],
            [
                # 2-step document pipeline: step 2 is the final synthesis step, not flagged.
                ("three_step_document:openai.chat.completions.create", 14, "low"),  # middle step only
                # outer()/inner(): a call in a nested function does not pair with the outer call.
                ("chat:openai.chat.completions.create", 31, "medium"),  # 2 steps are enough for history
                ("chat:openai.chat.completions.create#2", 33, "medium"),
            ],
        )

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


class Llm04ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "research:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "client.chat.completions.create(messages=invented)"
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
        committed = json.loads((FIXTURES / "llm04-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm04CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm04_result(self):
        input_path = FIXTURES / "llm04-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm04PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
