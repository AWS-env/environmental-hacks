"""Behavioral tests for the OBS-03 detector (issue #227)."""

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
from owner_d.obs03 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs03"
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
        "scan_id": "scan-obs03-001",
        "commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
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


class Obs03PositiveTests(unittest.TestCase):
    """OBS03-01: per-iteration log calls are flagged with exact evidence and graded confidence."""

    def test_per_iteration_logging_is_flagged_with_exact_lines(self):
        payload, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        expected = {
            "import_rows:logger.debug": (10, "medium", "DEBUG on every iteration of the `for` loop (line 9)"),
            "import_rows:audit.info": (11, "medium", "INFO on every iteration"),
            "import_rows:logger.log": (12, "low", "an unresolved level"),
            "import_rows:logger.debug#2": (13, "medium", "every iteration of the comprehension (line 13)"),
            "drain:logger.info": (19, "medium", "every iteration of the `while` loop (line 17)"),
            "score:logger.debug": (25, "medium", "inside 2 nested loops"),
            "sync:logger.debug": (31, "low", "on some iterations"),
            "serve:logger.info": (38, "low", "`while` loop (line 36)"),
            "Consumer.consume:self.logger.debug": (47, "low", "`async for` loop (line 46)"),
            "Consumer.handle:self.logger.debug": (51, "medium", "DEBUG on every iteration"),
        }
        findings = by_identity(result)
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, confidence, phrase) in expected.items():
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
                self.assertEqual(evidence["value"], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Obs03NegativeTests(unittest.TestCase):
    """OBS03-02: calls outside the loop body, failure paths, small/polling/once loops are clean."""

    def test_similar_non_per_item_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])


class Obs03ExceptionTests(unittest.TestCase):
    """OBS03-03: level guards, flag guards, sampling, verbosity switches and noqa suppress findings."""

    def test_guards_sampling_and_noqa_are_respected(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        [finding] = result["findings"]
        self.assertEqual(finding["identity"], "not_guarded:logger.debug")
        self.assertEqual(finding["confidence"], "low")
        self.assertEqual(finding["evidence"][0]["line_start"], 31)
        self.assertEqual(
            finding["evidence"][0]["value"],
            '            logger.debug("else branch is not guarded %s", row)',
        )


class Obs03IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS03-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/importer.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS03-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertTrue(result["findings"])
        self.assertTrue(all(f["scope_id"] == "file:positive.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])


class Obs03BoundaryTests(unittest.TestCase):
    """OBS03-06: identity dedupe, the small-loop bound, break-exits and line movement."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        lines = (FIXTURES / "boundary.py").read_text().splitlines()
        expected = [
            ("sync:logger.debug", 9, "medium"),
            ("sync:logger.debug#2", 10, "medium"),
            ("sizes:logger.debug", 17, "medium"),  # range(11) is hot; range(10) on line 15 is not
            ("match:logger.debug", 24, "low"),  # break exits the inner loop, not the outer one
        ]
        self.assertEqual(
            [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]],
            expected,
        )
        for finding, (_, line, _) in zip(result["findings"], expected):
            self.assertEqual(finding["evidence"][0]["value"], lines[line - 1])
        self.assertIn("`for` loop (line 21)", result["findings"][-1]["summary"])

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


class Obs03ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "import_rows:logger.debug")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "logger.debug('invented')"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_other_check_ids_are_rejected(self):
        payload = make_input("positive.py")
        payload["check_id"] = "OBS-02"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs03-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Obs03CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs03_result(self):
        input_path = FIXTURES / "obs03-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 10)


if __name__ == "__main__":
    unittest.main()
