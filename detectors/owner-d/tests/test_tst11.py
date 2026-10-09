"""Behavioral tests for the TST-11 General Fixture detector (issue #266)."""

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
from owner_d.tst11 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst11"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python"}
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_orders.py",
    "negative.py": "tests/test_catalog.py",
    "exceptions.py": "tests/test_suppressed.py",
    "boundary.py": "tests/test_boundary.py",
    "helpers.py": "app/helpers.py",
    "broken.py": "tests/test_broken.py",
    "cart.test.js": "web/cart.test.js",
}


def static_source(name, content=None, locator=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    locator = locator or LOCATORS[name]
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{locator}",
        "kind": "static",
        "locator": locator,
        "content": content,
    }


def make_input(*names, sources=None, scope=None, context=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-tst11-001",
        "commit_sha": "1111111111111111111111111111111111111111",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope if scope is not None else [source["scope_id"] for source in sources],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_text(name, content):
    return run(sources=[static_source(name, content)])


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


def edited(name, old, new):
    content = (FIXTURES / name).read_text()
    if old not in content:
        raise AssertionError(f"{old!r} not in {name}")
    return content.replace(old, new)


class Tst11PositiveTests(unittest.TestCase):
    """TST11-01: one finding per fixture field that some tests running the fixture never read."""

    def test_general_fixture_fields_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_orders.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "OrderTest.setUp:mailer": (13, "medium", "builds self.mailer, but 2 of the 3 tests",
                                       "OrderTest.test_total, OrderTest.test_render;"),
            "OrderTest.setUp:cache": (14, "low", "1 of the 3 tests", ": OrderTest.test_email;"),
            "ReportTest.setUpClass:templates": (40, "low", "builds cls.templates, but 1 of the 2 tests",
                                                "built once per class"),
            "TestInvoice.setup_method:pdf": (52, "medium", "1 of the 2 tests", ": TestInvoice.test_number;"),
            "TestShipping.carrier:carrier_api": (64, "medium", "2 of the 3 tests",
                                                 "TestShipping.test_free, TestShipping.test_label"),
            "StorageMixin.setUp:bucket": (79, "low", "1 of the 3 tests that run it in LocalStorageTest",
                                          ": LocalStorageTest.test_restore;"),
            "StorageMixin.setUp:archive": (80, "medium", "none of the 3 tests that run it in LocalStorageTest",
                                           "LocalStorageTest.test_put, LocalStorageTest.test_list"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, confidence, *phrases) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                for phrase in phrases:
                    self.assertIn(phrase, finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_orders.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_fields_used_by_every_test_are_not_flagged(self):
        """db builds client (fixture-consumed), client is read by every test, currency is call-free."""
        _, result = run("positive.py")
        flagged = {identity.split(":")[1] for identity in identities(result)}
        self.assertTrue(flagged.isdisjoint({"db", "client", "currency", "engine", "order"}))


class Tst11NegativeTests(unittest.TestCase):
    """TST11-02: fields every test reads, fixture-internal fields and dynamic access are not flagged."""

    def test_fully_used_and_unjudgeable_fixtures_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_catalog.py"])
        self.assertEqual(result["findings"], [])

    def test_dynamic_access_counts_as_use(self):
        """Without `check_fixture(self)` that test reads neither field, so both are reported."""
        _, result = run_text("negative.py", edited("negative.py", "check_fixture(self)", "check_fixture(None)"))
        self.assertEqual(identities(result), ["DynamicAccessTest.setUp:store", "DynamicAccessTest.setUp:queue"])
        self.assertTrue(all(f["confidence"] == "low" for f in result["findings"]))  # imported ScenarioMixin
        self.assertIn(": DynamicAccessTest.test_helper;", result["findings"][0]["summary"])

    def test_teardown_restore_is_not_use_but_exempts_saved_state(self):
        """A value tearDown passes back is saved state; a resource tearDown only closes is still unused."""
        content = edited("negative.py", "Database.set_level(self.old_level)", "self.old_level.close()")
        _, result = run_text("negative.py", content)
        self.assertEqual(identities(result), ["RestoredStateTest.setUp:old_level"])

    def test_fixture_reading_a_field_consumes_it(self):
        content = edited("negative.py", "self.repo = Repo(self.conn)", "self.repo = Repo(None)")
        _, result = run_text("negative.py", content)
        self.assertEqual(identities(result), ["BuildChainTest.setUp:conn"])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-11 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst11ExceptionTests(unittest.TestCase):
    """TST11-03: noqa, skipped tests, overridden fixtures and uncollected classes."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_findings(self):
        _, result = run_text("exceptions.py", edited("exceptions.py", "  # noqa: TST-11", ""))
        got = [(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]]
        self.assertEqual(got, [("AuditTest.setUp:audit", 10), ("LedgerTest.setUp:archive", 22)])

    def test_other_noqa_codes_do_not_suppress(self):
        _, result = run_text("exceptions.py", edited("exceptions.py", "# noqa: TST-11", "# noqa: E501"))
        self.assertEqual(identities(result), ["AuditTest.setUp:audit", "LedgerTest.setUp:archive"])

    def test_unskipping_the_test_restores_the_finding(self):
        _, result = run_text("exceptions.py", edited("exceptions.py", '    @unittest.skip("needs hardware")\n', ""))
        self.assertEqual(identities(result), ["GpuTest.setUp:gpu"])
        self.assertEqual(result["findings"][0]["confidence"], "low")

    def test_inherited_fixture_runs_unless_overridden_without_super(self):
        content = edited("exceptions.py", '        self.small = Database("small://")',
                         '        super().setUp()\n        self.small = Database("small://")')
        _, result = run_text("exceptions.py", content)
        self.assertEqual(identities(result), ["HeavyMixin.setUp:big"])
        self.assertIn("none of the 2 tests that run it in LightTest", result["findings"][0]["summary"])

    def test_collected_class_is_judged(self):
        _, result = run_text("exceptions.py", edited("exceptions.py", "    __test__ = False\n", ""))
        self.assertEqual(identities(result), ["AbstractStoreTest.setUp:cache"])


class Tst11IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST11-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_orders.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST11-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_orders.py"])
        self.assertTrue(all(f["scope_id"] == "file:tests/test_orders.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst11BoundaryTests(unittest.TestCase):
    """TST11-06: two-test minimum, tearDown reads, the half rule, escapes and repeated names."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("TwoTests.setUp:spare", 19, "medium"),
            ("TeardownOnlyTest.setUp:conn", 30, "medium"),
            ("ShareTest.setUp:half", 44, "medium"),
            ("ShareTest.setUp:most", 45, "low"),
            ("EscapeTest.setUp:late", 64, "medium"),
            ("TwoTests.setUp:spare#2", 76, "medium"),
        ])

    def test_single_test_class_is_not_judged(self):
        _, result = run("boundary.py")
        self.assertFalse(any(identity.startswith("OneTest.") for identity in identities(result)))
        content = edited("boundary.py", "    def test_only(self):",
                         "    def test_other(self):\n        pass\n\n    def test_only(self):")
        _, result = run_text("boundary.py", content)
        self.assertIn("OneTest.setUp:spare", identities(result))

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


class Tst11ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_orders.py", "OrderTest.setUp:mailer")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "        self.invented = Database()"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py", "boundary.py", "helpers.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "tst11-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst11CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst11_result(self):
        input_path = FIXTURES / "tst11-01-positive-input.json"
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
