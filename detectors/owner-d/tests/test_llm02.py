"""Behavioral tests for the LLM-02 detector (issue #195)."""

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
from owner_d.llm02 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm02"
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
        "scan_id": "scan-llm02-001",
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


def run_content(name, content):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])[1]


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm02PositiveTests(unittest.TestCase):
    """LLM02-01: repeatable requests on repeated paths without a cache are flagged with exact evidence."""

    EXPECTED = {
        "faq:anthropic.messages.create": (35, 41, "medium", "Route handler faq()", "fully static; temperature=0"),
        "explain:openai.chat.completions.create": (
            48, 51, "low", "Route handler explain()", "built only from topic; temperature not set",
        ),
        "lambda_handler:bedrock.converse": (
            56, 60, "medium", "Lambda handler lambda_handler()", "fully static; temperature=0",
        ),
        "describe_region:anthropic.messages.create": (
            66, 68, "low", "describe_region(), reached from route handler plans()", "built only from region, tier",
        ),
        "summarise_policy:bedrock.invoke_model": (
            84, 84, "medium", "summarise_policy(), reached from route handler policy()", "fully static; temperature=0",
        ),
        "poll_status:anthropic.messages.create": (
            94, 99, "medium", "A `while True` loop in poll_status()", "fully static; temperature=0",
        ),
        "chain_client_answer:openai.chat.completions.create": (
            105, 107, "low", "OpenAI-compatible llm.chat.completions.create()", "built only from page; temperature=0",
        ),
    }

    def test_repeatable_uncached_requests_are_flagged_with_exact_evidence(self):
        _, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(self.EXPECTED))
        for identity, (line, end, confidence, where, request) in self.EXPECTED.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(where, finding["summary"])
                self.assertIn(request, finding["summary"])
                self.assertIn("no response cache", finding["summary"])
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

    def test_streamlit_module_level_static_call_is_flagged(self):
        _, result = run("streamlit_app.py")
        self.assertEqual(result["status"], "completed")
        [finding] = result["findings"]
        self.assertEqual(finding["identity"], "<module>:openai.chat.completions.create")
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("Streamlit script reruns on every interaction", finding["summary"])
        self.assertEqual(finding["evidence"][0]["line_start"], 10)
        self.assertEqual(finding["evidence"][0]["value"], evidence_text("streamlit_app.py", 10, 14))


class Llm02NegativeTests(unittest.TestCase):
    """LLM02-02: cached, dynamic and one-shot requests are clean."""

    FILES = ("negative.py", "negative_redis.py", "negative_langchain.py", "negative_streamlit.py")

    def test_cached_dynamic_and_one_shot_requests_are_clean(self):
        _, result = run(*self.FILES)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in self.FILES])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    LOAD_BEARING = (
        ("negative.py", (("@functools.lru_cache(maxsize=32)\n", ""),), "greeting_text:anthropic.messages.create"),
        ("negative.py", (("    if topic in ANSWERS:\n        return ANSWERS[topic]\n", ""),),
         "answer:anthropic.messages.create"),
        ("negative.py", (("def main():", "@app.get(\"/main\")\ndef main():"),), "main:anthropic.messages.create"),
        ("negative_redis.py", (("import redis\n", ""), ("store = redis.Redis()\n", "")),
         "static_prompt:openai.chat.completions.create"),
        ("negative_redis.py", (("import redis\n", ""), ("    if hit:\n        return hit\n", "")),
         "explain:openai.chat.completions.create"),
        ("negative_langchain.py", (("set_llm_cache(InMemoryCache())\n", ""),
                                   ("from langchain_core.globals import set_llm_cache\n", ""),
                                   ("from langchain_core.caches import InMemoryCache\n", "")),
         "motto:openai.chat.completions.create"),
        ("negative_streamlit.py", (("@st.cache_data(ttl=3600)\n", ""),), "welcome:openai.chat.completions.create"),
        ("negative_streamlit.py", (('if "tip" not in st.session_state:', "if True:"),
                                   ("st.session_state.tip", "tip_text")),
         "tip:openai.chat.completions.create"),
    )

    def test_each_cache_is_load_bearing(self):
        """Removing the cache (or making the one-shot call a handler) turns the request into a finding."""
        for name, edits, identity in self.LOAD_BEARING:
            with self.subTest(identity=identity):
                content = (FIXTURES / name).read_text()
                for old, new in edits:
                    self.assertIn(old, content)
                    content = content.replace(old, new)
                self.assertIn(identity, identities(run_content(name, content)))


class Llm02ExceptionTests(unittest.TestCase):
    """LLM02-03: intended variety, LLM-11 loops, embeddings, streaming, tests and noqa are not flagged."""

    def test_only_unsuppressed_static_requests_are_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            identities(result),
            ["footer:openai.chat.completions.create", "prompt_cached:openai.chat.completions.create"],
        )
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 88)
        self.assertEqual(result["findings"][0]["evidence"][0]["value"], evidence_text("exceptions.py", 88, 90))

    LOAD_BEARING = (
        (("temperature=0.9, ", "temperature=0, "), "idea:openai.chat.completions.create"),
        (("n=3, ", "n=1, "), "names:openai.chat.completions.create"),
        (("temperature=CREATIVITY", "temperature=0"), "slogan:openai.chat.completions.create"),
        (("    for _topic in TOPICS:\n        replies.append(", "    if TOPICS:\n        replies.append("),
         "digest:openai.chat.completions.create"),
        (("amazon.titan-embed-text-v2:0", "amazon.titan-text-lite-v1"), "embedding:bedrock.invoke_model"),
        (("stream=True", "stream=False"), "stream:openai.chat.completions.create"),
        (("def test_handler_prompt", "def handler_prompt"),
         "handler_prompt.inner_route:openai.chat.completions.create"),
        (("# noqa: LLM-02", "#"), "legal:openai.chat.completions.create"),
        (('@app.get("/health")\ndef health_check', '@app.get("/status")\ndef status'),
         "status:openai.chat.completions.create"),
    )

    def test_each_exception_is_load_bearing(self):
        """Removing the sampling, loop, embedding, streaming, test or noqa marker makes it a finding."""
        original = (FIXTURES / "exceptions.py").read_text()
        for (old, new), identity in self.LOAD_BEARING:
            with self.subTest(identity=identity):
                self.assertIn(old, original)
                self.assertIn(identity, identities(run_content("exceptions.py", original.replace(old, new))))


class Llm02IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM02-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/routes.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM02-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm02BoundaryTests(unittest.TestCase):
    """LLM02-06: temperature, key types, path vs query, loop exits and intermediate caches."""

    def test_boundaries_and_repeated_identities(self):
        _, result = run("boundary.py")
        self.assertEqual(
            [(f["identity"], f["confidence"], f["evidence"][0]["line_start"]) for f in result["findings"]],
            [
                ("zero:anthropic.messages.create", "medium", 13),
                ("unset:anthropic.messages.create", "low", 27),
                ("page:anthropic.messages.create", "medium", 32),
                ("item_path:anthropic.messages.create", "medium", 48),
                ("heartbeat:anthropic.messages.create", "medium", 64),
                ("twice:anthropic.messages.create", "medium", 96),
                ("twice:anthropic.messages.create#2", "medium", 99),
            ],
        )

    def test_intermediate_cache_is_load_bearing(self):
        content = (FIXTURES / "boundary.py").read_text().replace("@functools.lru_cache(maxsize=None)\n", "")
        self.assertIn("leaf:anthropic.messages.create", identities(run_content("boundary.py", content)))

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        after = run_content("boundary.py", "\n\n\n" + (FIXTURES / "boundary.py").read_text())
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm02ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "faq:anthropic.messages.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "    reply = claude.messages.create(  # invented"
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
        committed = json.loads((FIXTURES / "llm02-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm02CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm02_result(self):
        input_path = FIXTURES / "llm02-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm02PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
