"""Behavioral tests for the LLM-13 detector (issue #206)."""

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
from owner_d.llm13 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm13"
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
        "scan_id": "scan-llm13-001",
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


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm13PositiveTests(unittest.TestCase):
    """LLM13-01: each cache shape without TTL or invalidation is flagged with exact evidence."""

    EXPECTED = {
        "<module>:langchain:SQLiteCache": (23, None, "medium", "SQLiteCache has no TTL support"),
        "<module>:langchain:InMemoryCache": (24, None, "medium", "InMemoryCache has no TTL support"),
        "answer:memoize:functools.lru_cache": (27, None, "medium", "Anthropic messages.create responses"),
        "faq:memoize:functools.cache": (37, None, "medium", "LLM responses (through ask())"),
        "classify:memoize:cachetools.cached": (42, None, "medium", "@cached(LRUCache(maxsize=256))"),
        "summarise:memoize:async_lru.alru_cache": (47, None, "medium", "OpenAI responses.create responses"),
        "cached_converse:redis.set": (59, None, "medium", "Redis store.set() stores Bedrock converse"),
        "lookup:dict:ANSWERS": (67, None, "medium", "module-level dict ANSWERS"),
        "draft:memoize:functools.lru_cache": (71, None, "low", "OpenAI-compatible chat.completions.create"),
        "ReplyCache.reply:redis.hset": (85, 89, "medium", "Redis self.redis.hset() stores OpenAI responses.create"),
    }

    def test_uncached_ttl_less_caches_are_flagged_with_exact_evidence(self):
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
                self.assertIn("never expire", finding["summary"])
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


class Llm13NegativeTests(unittest.TestCase):
    """LLM13-02: TTL caches, non-LLM memoization, embeddings, write-only stores and look-alikes are clean."""

    def test_ttl_caches_and_lookalikes_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])


class Llm13ExceptionTests(unittest.TestCase):
    """LLM13-03: invalidation, manual expiry, unknown values and noqa suppress; other noqa codes do not."""

    def test_invalidated_and_unknown_caches_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_suppressed:memoize:functools.lru_cache"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 111)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 111))

    LOAD_BEARING = (
        ((("invalidated.cache_clear()", "pass"),), "invalidated:memoize:functools.lru_cache"),
        ((("REPLIES.clear()", "pass"),), "cleared_store:memoize:cachetools.cached"),
        ((("ttl_hash=None", "tag=None"),), "bucketed:memoize:functools.lru_cache"),
        ((("BOUNDED.pop(question, None)", "pass"),), "bounded:dict:BOUNDED"),
        ((("if entry and time.time() - entry[0] < 300:", "if entry:"), ("(time.time(), ", "(0, ")),
         "stamped:dict:STAMPED"),
        ((("store.expire(question, 600)", "pass"),), "expired_later:redis.set"),
        (((", keepttl=True", ""),), "kept_ttl:redis.set"),
        (((", **opts)", ")"),), "with_options:redis.set"),
        ((("def injected_store(cache, question)", "def injected_store(question)"), ("cache.", "store.")),
         "injected_store:redis.set"),
        ((("# noqa: LLM-13", "#"),), "glossary:memoize:functools.lru_cache"),
        ((("def test_cache_hit", "def cache_hit"),), "cache_hit:langchain:InMemoryCache"),
    )

    def test_each_exception_is_load_bearing(self):
        """Removing the invalidation, expiry or unknown part of an exception makes it a finding."""
        original = (FIXTURES / "exceptions.py").read_text()
        for edits, identity in self.LOAD_BEARING:
            with self.subTest(identity=identity):
                content = original
                for old, new in edits:
                    self.assertIn(old, content)
                    content = content.replace(old, new)
                _, result = run(sources=[static_source("exceptions.py", content)], scope=["file:exceptions.py"])
                self.assertIn(identity, {f["identity"] for f in result["findings"]})


class Llm13IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM13-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/cache.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM13-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Llm13BoundaryTests(unittest.TestCase):
    """LLM13-06: size bounds are not expiry, explicit None is no TTL, repeats get #2, identities survive moves."""

    def test_ttl_boundaries_and_repeated_identities(self):
        _, result = run("boundary.py")
        self.assertEqual(
            [(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
            [
                ("<module>:langchain:RedisCache", 18),
                ("<module>:langchain:InMemoryCache", 20),
                ("sized:memoize:functools.lru_cache", 27),
                ("no_ttl:memoize:async_lru.alru_cache", 37),
                ("explicit_none:redis.set", 44),
                ("pipeline:dict:CACHE", 58),
                ("pipeline:dict:CACHE#2", 59),
            ],
        )
        self.assertIn("created without ttl", result["findings"][0]["summary"])

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


class Llm13ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "answer:memoize:functools.lru_cache")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "@functools.lru_cache(maxsize=None)  # invented"
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
        committed = json.loads((FIXTURES / "llm13-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm13CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm13_result(self):
        input_path = FIXTURES / "llm13-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm13PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
