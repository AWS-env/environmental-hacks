"""Behavioral tests for the LLM-11 detector (issue #204)."""

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
from owner_d.llm11 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm11"
REPOSITORY_ID = "github:AWS-env/example"


def static_source(name, content=None, locator=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    locator = locator or name
    return {
        "source_id": f"src:{locator}",
        "scope_id": f"file:{locator}",
        "kind": "static",
        "locator": locator,
        "content": content,
    }


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-llm11-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"language": "python"},
        "scope": scope if scope is not None else [source["scope_id"] for source in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_content(name, content, locator=None):
    return run(sources=[static_source(name, content, locator)])


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


def evidence_text(name, line, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    return "\n".join(lines[line - 1:(end or line)])


class Llm11PositiveTests(unittest.TestCase):
    """LLM11-01: corpus re-embedding on every run and loop-invariant requests, with exact evidence."""

    EXPECTED = {
        "<module>:reembed:Chroma.from_documents": (23, None, "medium", "read by DirectoryLoader.load()"),
        "<module>:reembed:VectorStoreIndex.from_documents": (28, None, "medium", "SimpleDirectoryReader.load_data()"),
        "handler:reembed:embeddings.create": (37, 40, "medium", "the Lambda handler handler()"),
        "sync_forever:reembed:invoke_model": (49, None, "medium", "a while True loop"),
        "build_faq_index:reembed:FAISS.from_texts": (55, None, "medium", "the same file queries the index"),
        "summarise_all:loop-invariant:chat.completions.create": (62, 65, "medium", "`for ticket in tickets`"),
        "draft_all:loop-invariant:chat.completions.create": (72, None, "low", "`for item in items`"),
    }

    def test_reembedding_and_repeated_requests_are_flagged_with_exact_evidence(self):
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
                    finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, "file:positive.py", identity)
                )
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], "src:positive.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], evidence_text("positive.py", line, end))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_glob_corpus_is_named_as_the_source(self):
        _, result = run("positive.py")
        [finding] = [f for f in result["findings"] if f["identity"] == "sync_forever:reembed:invoke_model"]
        self.assertIn("read by os.listdir()", finding["summary"])


PERSISTED = """if not os.path.exists(PERSIST_DIR):
    documents = SimpleDirectoryReader("data").load_data()
    index = VectorStoreIndex.from_documents(documents)
    index.storage_context.persist(persist_dir=PERSIST_DIR)
else:
    index = load_index_from_storage(StorageContext.from_defaults(persist_dir=PERSIST_DIR))
"""
UNPERSISTED = """documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(documents)
"""


class Llm11NegativeTests(unittest.TestCase):
    """LLM11-02: persisted indexes, guarded builds, per-item inputs, sampling and query embeddings are clean."""

    def test_similar_code_is_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    GUARDS = (
        (PERSISTED, UNPERSISTED, "<module>:reembed:VectorStoreIndex.from_documents"),
        ("    if os.path.isdir(\"db\"):\n"
         "        return Chroma(persist_directory=\"db\", embedding_function=embeddings)\n",
         "", "build_if_missing:reembed:Chroma.from_documents"),
        ("        if doc.metadata[\"source\"] in SEEN:\n            continue\n", "",
         "add_new_only:reembed:add_documents"),
        ("\"content\": doc.page_content}", "\"content\": \"x\"}",
         "summarise_loop:loop-invariant:chat.completions.create"),
        ("for _ in range(3):", "for _ in PROMPT:", "sample_loop:loop-invariant:chat.completions.create"),
        ("        if reply.choices:\n            break\n", "", "first_success:loop-invariant:chat.completions.create"),
        ("PyPDFLoader(upload_path)", "PyPDFLoader(\"upload.pdf\")", "ingest_upload:reembed:Chroma.from_documents"),
        ("BM25Retriever.from_documents", "FAISS.from_documents", "keyword_indexes:reembed:FAISS.from_documents"),
        ("SummaryIndex.from_documents", "VectorStoreIndex.from_documents",
         "keyword_indexes:reembed:VectorStoreIndex.from_documents"),
        ("if os.path.exists(\"kb_index\"):", "if PERSIST_DIR == \"kb\":", "build_store:reembed:FAISS.from_documents"),
        ("load_data(texts=texts)", "load_data(texts=[\"a\", \"b\"])",
         "from_request:reembed:VectorStoreIndex.from_documents"),
    )

    def test_each_negative_differs_from_a_finding_by_its_guard(self):
        """Removing the guard, the variable input, the counting loop or the non-embedding index makes a finding.

        A builder whose only caller checks that the index exists is clean; a caller guarded by an
        unrelated condition (`if PERSIST_DIR == "kb":`) still rebuilds it.
        """
        original = (FIXTURES / "negative.py").read_text()
        for old, new, identity in self.GUARDS:
            with self.subTest(identity=identity):
                self.assertIn(old, original)
                _, result = run_content("negative.py", original.replace(old, new))
                self.assertIn(identity, identities(result))


class Llm11ExceptionTests(unittest.TestCase):
    """LLM11-03: change tracking, ingest-only scripts, unknown corpora, retries, tests and noqa."""

    def test_in_file_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["not_suppressed:reembed:Chroma.from_documents"])
        [evidence] = result["findings"][0]["evidence"]
        self.assertEqual(evidence["line_start"], 45)
        self.assertEqual(evidence["value"], evidence_text("exceptions.py", 45))

    FILES = ("tracked_hash.py", "tracked_index.py", "tracked_cache.py", "tracked_pipeline.py", "ingest_only.py")

    def test_change_tracking_and_ingest_only_files_are_clean(self):
        _, result = run(*self.FILES)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), len(self.FILES))
        self.assertEqual(result["findings"], [])

    LOAD_BEARING = (
        ("exceptions.py", (("def from_param(docs):", "def from_param():\n    docs = TextLoader(\"x.txt\").load()"),),
         "from_param:reembed:Chroma.from_documents"),
        ("exceptions.py", (("        try:\n            client", "        client"),
                           ("        except Exception:\n            time.sleep(1)\n", "")),
         "retried:loop-invariant:chat.completions.create"),
        ("exceptions.py", (("        break\n", ""),), "stops_early:loop-invariant:chat.completions.create"),
        ("exceptions.py", (("def test_build_index", "def build_index"),), "build_index:reembed:Chroma.from_documents"),
        ("exceptions.py", (("# noqa: LLM-11", "#"),), "suppressed:reembed:Chroma.from_documents"),
        ("tracked_hash.py", (("import hashlib\n", ""), ("hashlib.sha256(text.encode()).hexdigest()", "text")),
         "refresh:reembed:embeddings.create"),
        ("tracked_index.py", (("from langchain.indexes import SQLRecordManager, index\n", ""),
                              ("record_manager = SQLRecordManager(", "record_manager = dict("),
                              ("index(docs, record_manager", "print(docs, record_manager")),
         "<module>:reembed:add_documents"),
        ("tracked_cache.py", (("from langchain.embeddings import CacheBackedEmbeddings\n", ""),
                              ("CacheBackedEmbeddings.from_bytes_store(", "dict(")),
         "<module>:reembed:FAISS.from_documents"),
        ("tracked_pipeline.py", (("from llama_index.core.ingestion import IngestionPipeline\n", ""),
                                 ("IngestionPipeline(", "dict(")),
         "<module>:reembed:VectorStoreIndex.from_documents"),
        ("ingest_only.py", (("print(\"indexed\", len(docs))", "print(store.as_retriever())"),),
         "<module>:reembed:Chroma.from_documents"),
    )

    def test_each_exception_is_load_bearing(self):
        """Removing the change tracking, unknown part or exemption of each exception makes it a finding."""
        for name, edits, identity in self.LOAD_BEARING:
            with self.subTest(identity=identity):
                content = (FIXTURES / name).read_text()
                for old, new in edits:
                    self.assertIn(old, content)
                    content = content.replace(old, new)
                _, result = run_content(name, content)
                self.assertIn(identity, identities(result))

    def test_test_files_are_not_judged(self):
        content = (FIXTURES / "positive.py").read_text()
        for locator in ("tests/app.py", "app/test_rag.py", "rag_test.py", "conftest.py"):
            with self.subTest(locator=locator):
                _, result = run_content("positive.py", content, locator=locator)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])
        _, result = run_content("positive.py", content, locator="app/rag.py")
        self.assertEqual(len(result["findings"]), len(Llm11PositiveTests.EXPECTED))


class Llm11IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """LLM11-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/rag.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_invalid_source_is_unavailable_with_reason(self):
        source = static_source("positive.py")
        source["content"] = None
        payload = make_input(sources=[source])
        with self.assertRaises(ContractError):
            validate_pair(payload, evaluate(payload))  # the shared schema rejects the input itself
        result = evaluate(payload)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("string locator and content" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """LLM11-05: parse failures and notebooks are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "notebook.ipynb")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertTrue(all(f["scope_id"] == "file:positive.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:notebook.ipynb: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Llm11BoundaryTests(unittest.TestCase):
    """LLM11-06: emptiness checks are not guards, existence checks are, __main__ is not, repeats get #2."""

    def test_guard_boundaries_and_repeated_identities(self):
        _, result = run("boundary.py")
        self.assertEqual(
            [(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
            [
                ("empty_check:reembed:Chroma.from_documents", 20),
                ("length_check:reembed:Chroma.from_documents", 27),
                ("twice:reembed:Chroma.from_documents", 39),
                ("twice:reembed:Chroma.from_documents#2", 40),
                ("<module>:reembed:Chroma.from_documents", 45),
            ],
        )

    def test_without_a_query_nothing_is_flagged(self):
        content = (FIXTURES / "boundary.py").read_text().replace("store.as_retriever().invoke(\"refunds\")", "store")
        _, result = run_content("boundary.py", content)
        self.assertEqual(result["findings"], [])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        _, after = run_content("boundary.py", "\n\n\n" + (FIXTURES / "boundary.py").read_text())
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Llm11ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "handler:reembed:embeddings.create")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "Chroma.from_documents(docs)  # invented"
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
        committed = json.loads((FIXTURES / "llm11-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Llm11CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm11_result(self):
        input_path = FIXTURES / "llm11-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), len(Llm11PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
