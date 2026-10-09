"""Behavioral tests for the OBS-18 detector (issue #242)."""

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
from owner_d.obs18 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint, normalise
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs18"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_field_spellings": 1, "max_fields_per_event": 20}
EVIDENCE_MAX_LINES = 8


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
        "scan_id": "scan-obs18-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope if scope is not None else [s["scope_id"] for s in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_key(result):
    return {(f["scope_id"], f["identity"]): f for f in result["findings"]}


def evidence_text(name, start, end=None):
    lines = (FIXTURES / name).read_text().splitlines()
    end = min(end or start, start + EVIDENCE_MAX_LINES - 1)
    return "\n".join(lines[start - 1:end])


class Obs18PositiveTests(unittest.TestCase):
    """OBS18-01: drifting names, a wide call and object dumps, with exact evidence."""

    EXPECTED = {
        ("file:positive.py", "drift:request_id"): (11, None, "medium", ["`request_id` (2 uses in 1 file)",
                                                                        "This file uses `requestId`"]),
        ("file:positive.py", "drift:user_id"): (13, None, "medium", ["written under 4 spellings", "`usr_id`",
                                                                     "This file uses `userId`, `user.id`"]),
        ("file:positive.py", "dump:lambda_handler:logger.info"): (16, None, "medium", ["vars(order)"]),
        ("file:positive.py", "dump:lambda_handler:logger.debug"): (17, None, "medium", ["order.__dict__"]),
        ("file:positive.py", "dump:lambda_handler:print"): (18, None, "low", ["vars(order)"]),
        ("file:service.py", "drift:user_id"): (10, None, "low", ["This file uses `usr_id` instead of `user_id`"]),
        ("file:service.py", "wide:audit:log.info"): (14, 22, "medium", ["21 structured fields", "more than 20"]),
    }

    def test_drift_wide_and_dumps_are_flagged_with_exact_evidence(self):
        _, result = run("positive.py", "service.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py", "file:service.py"])
        self.assertEqual(result["coverage"]["limitations"], [LIMITATION])
        findings = by_key(result)
        self.assertEqual(set(findings), set(self.EXPECTED))
        for key, (start, end, confidence, phrases) in self.EXPECTED.items():
            finding = findings[key]
            scope_id, identity = key
            name = scope_id.removeprefix("file:")
            with self.subTest(key=key):
                self.assertEqual(finding["confidence"], confidence)
                for phrase in phrases:
                    self.assertIn(phrase, finding["summary"])
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["source_id"], f"src:{name}")
                self.assertEqual(evidence["line_start"], start)
                self.assertEqual(evidence["value"], evidence_text(name, start, end))
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_drift_counts_the_spellings_of_the_supplied_payload_only(self):
        _, result = run("service.py")
        findings = by_key(result)
        self.assertEqual(set(findings), {("file:service.py", "drift:user_id"),
                                         ("file:service.py", "wide:audit:log.info")})
        self.assertIn("written under 2 spellings", findings[("file:service.py", "drift:user_id")]["summary"])
        _, result = run("positive.py")
        findings = by_key(result)
        self.assertNotIn(("file:positive.py", "drift:request_id"), findings)  # `requestId` is its only spelling
        self.assertIn("This file uses `userId` instead of `user.id`",
                      findings[("file:positive.py", "drift:user_id")]["summary"])

    def test_normalisation(self):
        cases = {"userId": "user_id", "UserID": "user_id", "user.id": "user_id", "user-id": "user_id",
                 "HTTPStatusCode": "http_status_code", "duration_ms": "duration_ms"}
        for spelling, expected in cases.items():
            with self.subTest(spelling=spelling):
                self.assertEqual(normalise(spelling), expected)


class Obs18NegativeTests(unittest.TestCase):
    """OBS18-02: one spelling per concept, other units, qualified names, look-alikes, 20 fields."""

    def test_similar_logging_is_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_consistent_project_is_clean(self):
        _, result = run("negative.py", "consistent.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs18ExceptionTests(unittest.TestCase):
    """OBS18-03: EMF payloads, __main__, noqa and exempt paths."""

    def test_emf_main_block_and_noqa(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        findings = by_key(result)
        self.assertEqual(set(findings), {("file:exceptions.py", "drift:request_id"),
                                         ("file:exceptions.py", "dump:dump:log.info")})
        drift = findings[("file:exceptions.py", "drift:request_id")]
        self.assertEqual(drift["confidence"], "low")  # synonym only, no case variant
        self.assertIn("2 spellings", drift["summary"])  # EMF `userId`, noqa'd `userId`, __main__ `UserID` not counted
        self.assertEqual(drift["evidence"][0]["line_start"], 13)
        self.assertEqual(drift["evidence"][0]["value"], evidence_text("exceptions.py", 13))
        dump = findings[("file:exceptions.py", "dump:dump:log.info")]
        self.assertEqual(dump["evidence"][0]["line_start"], 22)
        self.assertEqual(dump["evidence"][0]["value"], evidence_text("exceptions.py", 22))

    def test_noqa_key_removes_the_drift(self):
        text = (FIXTURES / "exceptions.py").read_text().replace("# noqa: E501", "# noqa: OBS-18")
        _, result = run(sources=[static_source("exceptions.py", text)])
        self.assertEqual(result["findings"], [])

    def test_exempt_paths_are_not_judged_and_contribute_no_spellings(self):
        drifting = (FIXTURES / "drifting.py").read_text()
        exempt = [
            static_source(name, drifting) for name in (
                "tests/test_app.py", "scripts/backfill.py", "vendor/lib.py", "examples/demo.py", "layer/python/lib.py",
            )
        ] + [static_source("cli_tool.py", "import argparse\n" + drifting)]
        sources = [static_source("consistent.py")] + exempt
        _, result = run(sources=sources)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [s["scope_id"] for s in sources])
        self.assertEqual(result["findings"], [])
        _, control = run("consistent.py", "drifting.py")
        self.assertEqual(set(by_key(control)), {("file:drifting.py", "drift:user_id"),
                                                ("file:drifting.py", "dump:record:log.info")})


class Obs18IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """OBS18-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:app/handler.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """OBS18-04: the thresholds are required judgment calls; without them nothing is evaluated."""
        for context, reason in (
            ({"language": "python"}, "missing required context settings: max_field_spellings, max_fields_per_event"),
            ({**CONTEXT, "max_field_spellings": 0}, "context.max_field_spellings must be a positive integer"),
            ({**CONTEXT, "max_fields_per_event": "20"}, "context.max_fields_per_event must be a positive integer"),
            ({**CONTEXT, "max_fields_per_event": True}, "context.max_fields_per_event must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", "service.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["limitations"],
                                 [f"Missing or invalid context settings: {reason}", LIMITATION])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """OBS18-05: parse failures and non-Python files are omitted and contribute no spellings."""
        _, result = run("consistent.py", "broken.py", "client.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:consistent.py"])
        self.assertEqual(result["findings"], [])  # broken.py's `userId` is not counted
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:client.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])


class Obs18BoundaryTests(unittest.TestCase):
    """OBS18-06: strict thresholds, nested fields, the tie-break and line movement."""

    def test_twenty_fields_pass_and_twenty_one_are_flagged(self):
        _, result = run("boundary.py")
        findings = by_key(result)
        self.assertEqual(set(findings), {("file:boundary.py", "wide:twenty_one:log.info"),
                                         ("file:boundary.py", "drift:trace_id")})
        wide = findings[("file:boundary.py", "wide:twenty_one:log.info")]
        self.assertIn("21 structured fields", wide["summary"])  # 19 + 2 nested leaves; **TWENTY counts 0
        self.assertEqual(wide["evidence"][0]["line_start"], 17)
        self.assertEqual(wide["evidence"][0]["value"], evidence_text("boundary.py", 17, 21))
        _, relaxed = run("boundary.py", context={**CONTEXT, "max_fields_per_event": 21})
        self.assertNotIn(("file:boundary.py", "wide:twenty_one:log.info"), by_key(relaxed))

    def test_tie_prefers_snake_case_and_spelling_threshold_is_strict(self):
        _, result = run("boundary.py")
        drift = by_key(result)[("file:boundary.py", "drift:trace_id")]
        self.assertEqual(drift["confidence"], "medium")
        self.assertIn("This file uses `traceId` instead of `trace_id`", drift["summary"])
        self.assertEqual(drift["evidence"][0]["line_start"], 26)
        self.assertEqual(drift["evidence"][0]["value"], evidence_text("boundary.py", 26))
        _, relaxed = run("boundary.py", context={**CONTEXT, "max_field_spellings": 2})
        self.assertEqual(list(by_key(relaxed)), [("file:boundary.py", "wide:twenty_one:log.info")])

    def test_most_used_spelling_is_the_reference(self):
        text = (FIXTURES / "boundary.py").read_text() + '    log.info("span again", traceId=span.trace_id)\n'
        _, result = run(sources=[static_source("boundary.py", text)])
        drift = by_key(result)[("file:boundary.py", "drift:trace_id")]
        self.assertIn("This file uses `trace_id` instead of `traceId`", drift["summary"])
        self.assertEqual(drift["evidence"][0]["line_start"], 25)

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("positive.py", "service.py")
        shifted = "\n\n\n" + (FIXTURES / "positive.py").read_text()
        _, after = run(sources=[static_source("positive.py", shifted), static_source("service.py")])
        self.assertEqual(
            sorted(f["fingerprint"] for f in before["findings"]),
            sorted(f["fingerprint"] for f in after["findings"]),
        )


class Obs18ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.py", "drift:user_id")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py", "service.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "logger.info('invented', userId=1)"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_other_check_ids_are_rejected(self):
        payload = make_input("positive.py")
        payload["check_id"] = "OBS-04"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(EvaluationError):
            evaluate(["not", "a", "payload"])

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "service.py", "negative.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "obs18-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py", "service.py"))


class Obs18CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs18_result(self):
        input_path = FIXTURES / "obs18-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 7)


if __name__ == "__main__":
    unittest.main()
