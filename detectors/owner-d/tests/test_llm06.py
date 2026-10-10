"""Behavioral tests for the LLM-06 detector (issue #199)."""

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
from owner_d.llm06 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm06"
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
        "scan_id": "scan-llm06-001",
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


def identities(content):
    return [finding["identity"] for finding in run_source(content)[1]["findings"]]


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


def fan_out_source(chars, model="claude-sonnet-4-5", system=None, fan_out=None, extra="", request=None):
    """An async fan-out over a call whose cached system block has exactly `chars` characters."""
    system = system or '[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}]'
    request = request or f"system={system}"
    fan_out = fan_out or "await asyncio.gather(*(answer(q) for q in questions))"
    return (
        "import asyncio\nimport anthropic\nimport boto3\n\nclaude = anthropic.AsyncAnthropic()\n"
        f"PROMPT = {'p' * chars!r}\n{extra}\n\n"
        f"async def answer(question):\n    return await claude.messages.create(model={model!r}, max_tokens=64, "
        f"{request}, messages=question)\n\n\n"
        f"async def answer_all(questions):\n    return {fan_out}\n"
    )


ANSWER_ALL = "answer_all:asyncio.gather->answer:anthropic.messages.create"


class Llm06PositiveTests(unittest.TestCase):
    """LLM06-01: cold concurrent fan-outs over a cached static prefix are flagged with exact evidence."""

    EXPECTED = {
        "answer_all:asyncio.gather->answer:anthropic.messages.create": (22, None, "low", "a variable number of"),
        "classify_all:TaskGroup.create_task->classify:bedrock.converse": (35, None, "low", "cached with cachePoint"),
        "summarize_all:Executor.map->summarize:anthropic.messages.create": (45, None, "low", "through summarize()"),
        "Grader.grade_all:asyncio.gather->Grader.grade:anthropic.messages.create": (60, None, "low", "self.client"),
        "compare:asyncio.gather->review:anthropic.messages.create": (69, None, "low", "launches 2 concurrent"),
        "extract_all:Executor.submit->extract:bedrock.invoke_model": (83, None, "low", "bedrock.invoke_model()"),
        "main:asyncio.gather->main:anthropic.messages.create": (89, 92, "medium", "(system prompt) of about 1,175 tokens"),
    }

    def test_cold_fan_outs_are_flagged_with_exact_evidence(self):
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
                self.assertIn("readable only after the first response", finding["summary"])
                self.assertEqual(finding["scope_id"], "file:positive.py")
                self.assertEqual(
                    finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, "file:positive.py", identity)
                )
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], "src:positive.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], evidence_text("positive.py", line, end))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Llm06NegativeTests(unittest.TestCase):
    """LLM06-02: sequential, warmed, uncached, small, blocking, single-worker and mixed fan-outs are clean."""

    def test_similar_code_is_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_each_negative_is_one_change_away_from_a_finding(self):
        content = (FIXTURES / "negative.py").read_text()
        cases = {
            "warm-up removed": (
                "    first = await answer(questions[0])\n", "    first = None\n",
                "first_then_fan_out:asyncio.gather->answer:anthropic.messages.create",
            ),
            "two items": ('["Where is my order?"]', '["Where is my order?", "Refund?"]',
                          "single_item:asyncio.gather->answer:anthropic.messages.create"),
            "short prompt made long": ("text\": SHORT,", "text\": SUPPORT_POLICY,",
                                       "not_cacheable_or_small:asyncio.gather->short_prompt:anthropic.messages.create"),
            "blocking call awaited": ("    return sync_claude.messages.create(model=SONNET, max_tokens=256",
                                      "    return await claude.messages.create(model=SONNET, max_tokens=256",
                                      "blocking_all:asyncio.gather->blocking:anthropic.messages.create"),
            "eight workers": ("max_workers=1", "max_workers=8",
                              "one_worker:Executor.map->summarize:anthropic.messages.create"),
            "same prompt twice": ("gather(answer(question), triage(question))",
                                  "gather(answer(question), answer(question))",
                                  "two_different_prompts:asyncio.gather->answer:anthropic.messages.create"),
        }
        for name, (old, new, expected) in cases.items():
            with self.subTest(name):
                self.assertIn(old, content)
                self.assertEqual(identities(content.replace(old, new, 1)), [expected])


class Llm06ExceptionTests(unittest.TestCase):
    """LLM06-03: noqa, imported targets, unknown prefixes and pre-warmed or serialized files."""

    def test_only_the_unsuppressed_fan_out_is_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            ["not_suppressed:asyncio.gather->answer:anthropic.messages.create"],
        )
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 59)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 59))

    def test_noqa_on_the_fan_out_or_the_call_suppresses(self):
        content = (FIXTURES / "exceptions.py").read_text()
        stripped = content.replace("  # noqa: LLM-06 (cold batch accepted)", "").replace("  # noqa: LLM-06", "")
        self.assertEqual(
            sorted(identities(stripped)),
            [
                "accepted:asyncio.gather->answer:anthropic.messages.create",
                "acknowledged_all:asyncio.gather->acknowledged:anthropic.messages.create",
                "not_suppressed:asyncio.gather->answer:anthropic.messages.create",
            ],
        )

    def test_pre_warmed_file_is_clean(self):
        _, result = run("prewarmed.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        content = (FIXTURES / "prewarmed.py").read_text().replace("max_tokens=0", "max_tokens=1")
        self.assertEqual(identities(content), [ANSWER_ALL])

    def test_warm_up_names_and_serial_semaphores_suppress_the_file(self):
        self.assertEqual(identities(fan_out_source(5000)), [ANSWER_ALL])
        for extra in ("async def warm_cache():\n    pass\n", "warmup()\n", "LIMIT = asyncio.Semaphore(1)\n"):
            with self.subTest(extra=extra):
                self.assertEqual(identities(fan_out_source(5000, extra=extra)), [])
        self.assertEqual(identities(fan_out_source(5000, extra="LIMIT = asyncio.Semaphore(4)\n")), [ANSWER_ALL])


class Llm06IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM06-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/agent.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM06-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm06BoundaryTests(unittest.TestCase):
    """LLM06-06: model minimums, breakpoint placement, fan-out width, repeated anchors and line shifts."""

    def test_threshold_is_the_cached_prefix_at_four_characters_per_token(self):
        self.assertEqual(identities(fan_out_source(4 * 1024)), [ANSWER_ALL])
        self.assertEqual(identities(fan_out_source(4 * 1024 - 1)), [])
        self.assertEqual(identities(fan_out_source(4 * 512, "claude-opus-5-5")), [ANSWER_ALL])
        self.assertEqual(identities(fan_out_source(4 * 512 - 1, "claude-opus-5-5")), [])
        self.assertEqual(identities(fan_out_source(4 * 4096, "claude-haiku-4-5")), [ANSWER_ALL])
        self.assertEqual(identities(fan_out_source(4 * 4096 - 1, "claude-haiku-4-5")), [])

    def test_only_the_static_text_before_the_last_static_breakpoint_counts(self):
        marked = '{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}'
        cases = {
            # a breakpoint after a dynamic block does not cache a shared prefix
            f'[{{"type": "text", "text": f"Today is {{question.day}}"}}, {marked}]': [],
            # text after the breakpoint is not cached
            '[{"type": "text", "text": "Rules."}, {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}, '
            '{"type": "text", "text": PROMPT}]': [],
            # a static block before the marked block is part of the cached prefix
            '[{"type": "text", "text": PROMPT}, {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]': [ANSWER_ALL],
            # plain string system prompt: no explicit breakpoint (LLM-01's territory)
            "PROMPT": [],
        }
        for system, expected in cases.items():
            with self.subTest(system=system):
                self.assertEqual(identities(fan_out_source(5000, system=system)), expected)

    def test_tool_definitions_count_without_the_marker_itself(self):
        tools = '[{"name": "lookup", "description": PROMPT, "input_schema": {}, "cache_control": {"type": "ephemeral"}}]'
        overhead = len('{"name":"lookup","description":"","input_schema":{}}')
        for chars, expected in ((4 * 1024 - overhead, [ANSWER_ALL]), (4 * 1024 - overhead - 1, [])):
            with self.subTest(chars=chars):
                self.assertEqual(identities(fan_out_source(chars, request=f"tools={tools}")), expected)

    def test_unknown_models(self):
        unknown = "model='claude-sonnet-4-5'", "model=question.model"
        self.assertEqual(identities(fan_out_source(4 * 4096).replace(*unknown)), [ANSWER_ALL])
        self.assertEqual(identities(fan_out_source(4 * 4096 - 1).replace(*unknown)), [])
        bedrock = (
            "import asyncio\n\n\nasync def classify(runtime, ticket, model):\n"
            "    system = [{'text': PROMPT}, {'cachePoint': {'type': 'default'}}]\n"
            "    return await runtime.converse(modelId=model, system=system, messages=ticket)\n\n\n"
            "async def classify_all(runtime, tickets, model):\n"
            "    return await asyncio.gather(*(classify(runtime, t, model) for t in tickets))\n\n\n"
            f"PROMPT = {'p' * 20000!r}\n"
        )
        self.assertEqual(identities(bedrock), [])
        self.assertEqual(
            identities(bedrock.replace("modelId=model", "modelId='amazon.nova-pro-v1:0'")),
            ["classify_all:asyncio.gather->classify:bedrock.converse"],
        )

    def test_fan_out_width(self):
        cases = {
            "await asyncio.gather(*(answer(q) for q in ['a']))": [],
            "await asyncio.gather(*(answer(q) for q in ['a', 'b']))": [ANSWER_ALL],
            "await asyncio.gather(*(answer(q) for q in range(1)))": [],
            "await asyncio.gather(*(answer(q) for q in range(3)))": [ANSWER_ALL],
            "await asyncio.gather(answer(questions))": [],
            "await asyncio.gather(answer(questions), answer(questions))": [ANSWER_ALL],
            "await asyncio.wait([answer(q) for q in questions])": ["answer_all:asyncio.wait->answer:anthropic.messages.create"],
            "[await answer(q) for q in questions]": [],
        }
        for fan_out, expected in cases.items():
            with self.subTest(fan_out=fan_out):
                self.assertEqual(identities(fan_out_source(5000, fan_out=fan_out)), expected)

    def test_repeated_anchors_get_distinct_identities_and_a_second_gather_is_warm(self):
        _, result = run("boundary.py")
        findings = result["findings"]
        self.assertEqual(
            [f["identity"] for f in findings],
            [
                "run_all:asyncio.gather->run_all:anthropic.messages.create",
                "run_all:asyncio.gather->run_all:anthropic.messages.create#2",
                "twice:asyncio.gather->twice:anthropic.messages.create",
            ],
        )
        self.assertEqual([f["evidence"][0]["line_start"] for f in findings], [11, 14, 18])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        _, after = run(sources=[static_source("boundary.py", shifted)], scope=["file:boundary.py"])
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm06ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", ANSWER_ALL)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "await asyncio.gather(*invented)"
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
        committed = json.loads((FIXTURES / "llm06-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm06CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm06_result(self):
        input_path = FIXTURES / "llm06-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm06PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
