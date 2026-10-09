"""Behavioral tests for the OBS-02 detector (issue #226)."""

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
from owner_d.obs02 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs02"
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
        "scan_id": "scan-obs02-001",
        "commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
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


class Obs02PositiveTests(unittest.TestCase):
    """OBS02-01: eager formatting at disabled-by-default levels is flagged with exact evidence."""

    def test_eager_messages_are_flagged_with_exact_lines(self):
        payload, result = run("positive.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        expected = {
            "charge:logger.debug": (11, "an f-string", "medium"),
            "charge:logger.debug#2": (12, "%-formatting", "medium"),
            "charge:logger.info": (13, "str.format()", "low"),
            "charge:audit.debug": (14, "string concatenation", "medium"),
            "charge:logging.debug": (15, "an f-string", "medium"),
            "charge:logger.log": (16, "an f-string", "medium"),
            "charge:trace_log.trace": (17, "an f-string", "medium"),
            "Worker.handle:self.logger.debug": (27, "an f-string", "medium"),
        }
        findings = by_identity(result)
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, kind, confidence) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(kind, finding["summary"])
                self.assertEqual(finding["scope_id"], "file:positive.py")
                self.assertEqual(
                    finding["fingerprint"],
                    fingerprint(REPOSITORY_ID, CHECK_ID, "file:positive.py", identity),
                )
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])


class Obs02NegativeTests(unittest.TestCase):
    """OBS02-02: lazy arguments, constant strings, enabled levels and non-loggers are not flagged."""

    def test_similar_lazy_and_enabled_level_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])


class Obs02ExceptionTests(unittest.TestCase):
    """OBS02-03: level guards and noqa suppress findings; an else branch is not guarded."""

    def test_level_guards_and_noqa_are_respected(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["not_guarded:logger.debug"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 20)


class Obs02IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS02-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/billing.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS02-05: parse failures and non-Python files are omitted, never reported clean."""
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


class Obs02BoundaryTests(unittest.TestCase):
    """OBS02-06: repeated anchors are disambiguated; identities survive line movement."""

    def test_repeated_calls_in_one_function_get_distinct_identities(self):
        _, result = run("boundary.py")
        self.assertEqual([f["identity"] for f in result["findings"]], ["sync:logger.debug", "sync:logger.debug#2"])

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


class Obs02ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "charge:logger.debug")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "logger.debug(f'invented')"
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
        committed = json.loads((FIXTURES / "obs02-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Obs02CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs02_result(self):
        input_path = FIXTURES / "obs02-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 8)


if __name__ == "__main__":
    unittest.main()
