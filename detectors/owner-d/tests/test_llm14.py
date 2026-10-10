"""Behavioral tests for the LLM-14 detector (issue #207)."""

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
from owner_d.llm14 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm14"
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
        "scan_id": "scan-llm14-001",
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


def run_text(name, content):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])[1]


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm14PositiveTests(unittest.TestCase):
    """LLM14-01: whole histories that grow every turn are flagged with exact evidence."""

    EXPECTED = {
        "chat:anthropic.messages.create:messages": (19, None, "medium", "`while` loop (line 17)"),
        "agent:openai.chat.completions.create:messages": (26, 29, "medium", "grows inside the loop (line 32)"),
        "converse_loop:bedrock.converse:conversation": (39, None, "medium", "`while` loop"),
        "respond_loop:openai.responses.create:items": (47, None, "medium", "`while` loop"),
        "invoke_loop:bedrock.invoke_model:turns": (55, None, "medium", "`while` loop"),
        "Assistant.ask:anthropic.messages.create:self.history": (65, None, "medium", "created once in __init__()"),
        "Session.send:openai.chat.completions.create:self.turns": (76, None, "medium", "in the class body"),
        "handler:anthropic.messages.create:HISTORY": (81, None, "medium", "module-level `HISTORY`"),
        "grade_all:openai.chat.completions.create:transcript": (90, None, "low", "`for` loop over `answers`"),
        "continue_agent:anthropic.messages.create:messages": (95, None, "low", "`while` loop"),
        "injected:openai.chat.completions.create:log": (103, None, "low", "OpenAI-compatible"),
    }

    def test_growing_histories_are_flagged_with_exact_evidence(self):
        _, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(self.EXPECTED))
        for identity, (line, end, confidence, phrase) in self.EXPECTED.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(phrase, finding["summary"])
                self.assertEqual(finding["scope_id"], "file:positive.py")
                self.assertEqual(
                    finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, "file:positive.py", identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], "src:positive.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], evidence_text("positive.py", line, end))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Llm14NegativeTests(unittest.TestCase):
    """LLM14-02: windows, deques, trimming, length checks, summaries and server-side truncation are clean."""

    def test_bounded_histories_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_removing_each_bound_restores_a_finding(self):
        source = (FIXTURES / "negative.py").read_text()
        flips = {
            "window": ("messages=messages[-10:]", "messages=messages"),
            "system_plus_window": ("history[-6:]", "history"),
            "bounded_deque": ("deque(maxlen=20)", "deque()"),
            "pop_oldest": ("        if len(messages) > 30:\n            messages.pop(0)\n", ""),
            "stop_when_long": ("len(messages) >= MAX_TURNS", "input() == 'q'"),
            "server_side_truncation": ('truncation="auto", ', ""),
            "cleared": ("        messages.clear()\n", ""),
            "Windowed.ask": ("        self.history = self.history[-8:]\n", ""),
        }
        for function, (old, new) in flips.items():
            with self.subTest(function=function):
                self.assertEqual(source.count(old), 1)
                result = run_text("negative.py", source.replace(old, new))
                self.assertEqual([i.split(":")[0] for i in identities(result)], [function])


class Llm14ExceptionTests(unittest.TestCase):
    """LLM14-03: single-turn scripts, per-call histories, resets, unknown memory, **kwargs, closures, noqa."""

    def test_only_the_other_noqa_code_is_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["other_noqa:anthropic.messages.create:messages"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 31)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 31))

    def test_removing_each_guard_restores_a_finding(self):
        source = (FIXTURES / "exceptions.py").read_text()
        flips = {
            "suppressed": ("  # noqa: LLM-14", ""),
            "Resettable.ask": ("    def reset(self):\n        self.history = []\n", ""),
            "WithMemory.ask": ("memory.load()", "[]"),
            "unknown_kwargs": (", **options)", ")"),
            "stored_elsewhere": ("messages = load()", "messages = []"),
            "Chat.ask": ("class ShortChat(Chat):", "class ShortChat:"),
        }
        for function, (old, new) in flips.items():
            with self.subTest(function=function):
                self.assertEqual(source.count(old), 1)
                result = run_text("exceptions.py", source.replace(old, new))
                self.assertIn(function, [i.split(":")[0] for i in identities(result)])


class Llm14IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM14-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/agent.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM14-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm14BoundaryTests(unittest.TestCase):
    """LLM14-06: iteration budgets bound the history, growth must be inside the loop, repeats get #2."""

    def test_loop_bounds_and_growth_placement(self):
        _, result = run("boundary.py")
        self.assertEqual(identities(result), [
            "per_question:anthropic.messages.create:messages",
            "two_calls:anthropic.messages.create:messages",
            "two_calls:anthropic.messages.create:messages#2",
        ])
        self.assertEqual([f["confidence"] for f in result["findings"]], ["low", "medium", "medium"])
        self.assertEqual([f["evidence"][0]["line_start"] for f in result["findings"]], [33, 56, 57])

    def test_removing_the_budget_restores_a_finding(self):
        source = (FIXTURES / "boundary.py").read_text()
        flips = (
            ("range(5)", "iter(input, '')", "five_turns"),
            ("range(MAX_TURNS)", "iter(input, '')", "constant_budget"),
            ("range(max_turns)", "iter(input, '')", "param_budget"),
            ("turn < 3", "True", "counted_while"),
            ("    messages.append({\"role\": \"user\", \"content\": \"Classify each line.\"})\n    while True:\n",
             "    while True:\n        messages.append({\"role\": \"user\", \"content\": \"Classify each line.\"})\n",
             "growth_before_loop_only"),
        )
        for old, new, function in flips:
            with self.subTest(function=function):
                self.assertEqual(source.count(old), 1)
                result = run_text("boundary.py", source.replace(old, new))
                self.assertIn(function, [i.split(":")[0] for i in identities(result)])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        after = run_text("boundary.py", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm14ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "chat:anthropic.messages.create:messages")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "client.messages.create(messages=invented)"
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
        committed = json.loads((FIXTURES / "llm14-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm14CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm14_result(self):
        input_path = FIXTURES / "llm14-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm14PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
