"""Behavioral tests for the TST-02 Lazy Test detector (issue #257)."""

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
from owner_d.tst02 import CHECK_ID, DETECTOR_VERSION, NO_PACKAGES_NOTE, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst02"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python"}
WITH_PACKAGES = {"language": "python", "production_packages": ["shop"]}
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_cart.py",
    "negative.py": "tests/test_inventory.py",
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
        "scan_id": "scan-tst02-001",
        "commit_sha": "2222222222222222222222222222222222222222",
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


def identities(result):
    return [finding["identity"] for finding in result["findings"]]


def shape(result):
    """(identity, evidence lines, confidence) per finding."""
    return [
        (f["identity"], [e["line_start"] for e in f["evidence"]], f["confidence"]) for f in result["findings"]
    ]


class Tst02PositiveTests(unittest.TestCase):
    """TST02-01: tests of one group calling the same production method are flagged with exact evidence."""

    def test_shared_production_methods_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_cart.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        self.assertEqual(shape(result), [
            ("CartTest:shop.cart.Cart.add", [13, 17], "low"),
            ("CartTest:shop.cart.Cart.count", [14, 19], "low"),
            ("TestDiscount:shop.pricing.discount", [24, 29], "low"),
            ("<module>:shop.cart.total", [33, 37, 41], "medium"),
        ])
        summaries = {f["identity"]: f["summary"] for f in result["findings"]}
        self.assertIn(
            "2 tests in test class CartTest call the production method shop.cart.Cart.add (test_add_one, test_add_two)",
            summaries["CartTest:shop.cart.Cart.add"],
        )
        self.assertIn("but only two tests share it", summaries["CartTest:shop.cart.Cart.add"])
        self.assertIn("other production calls differ", summaries["TestDiscount:shop.pricing.discount"])
        self.assertIn(
            "3 module-level tests call the production method shop.cart.total "
            "(test_total_empty, test_total_one, test_total_many)",
            summaries["<module>:shop.cart.total"],
        )
        self.assertIn("one parametrized test could cover them", summaries["<module>:shop.cart.total"])
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for finding in result["findings"]:
            with self.subTest(identity=finding["identity"]):
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(
                    finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, finding["identity"])
                )
                self.assertTrue(finding["references"])
                for evidence in finding["evidence"]:
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["source_id"], "src:positive.py")
                    self.assertEqual(evidence["locator"], "tests/test_cart.py")
                    self.assertEqual(evidence["value"], lines[evidence["line_start"] - 1])
        self.assertEqual(result["measurements"], [])
        self.assertIn(NO_PACKAGES_NOTE, result["coverage"]["limitations"])

    def test_production_packages_keep_the_same_findings(self):
        _, result = run("positive.py", context=WITH_PACKAGES)
        self.assertEqual(len(result["findings"]), 4)
        self.assertNotIn(NO_PACKAGES_NOTE, result["coverage"]["limitations"])


class Tst02NegativeTests(unittest.TestCase):
    """TST02-02: shared calls that are not production methods, and calls shared across groups, are clean."""

    def test_non_production_shared_calls_are_clean(self):
        for context in (CONTEXT, WITH_PACKAGES):
            with self.subTest(context=context):
                _, result = run("negative.py", context=context)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_inventory.py"])
                self.assertEqual(result["findings"], [])

    def test_a_second_class_test_is_still_not_compared_across_groups(self):
        """restock is called in InventoryTest and TestRestockAgain once each: different groups."""
        content = (FIXTURES / "negative.py").read_text().replace(
            "        with mock.patch(", "        restock(Inventory(), 1)\n        with mock.patch(", 1
        )
        _, result = run(sources=[static_source("negative.py", content)])
        self.assertEqual(identities(result), ["InventoryTest:shop.inventory.restock"])
        self.assertEqual([e["line_start"] for e in result["findings"][0]["evidence"]], [34, 47])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:app/helpers.py: evaluated; no unittest/pytest test functions recognised, so TST-02 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst02ExceptionTests(unittest.TestCase):
    """TST02-03: noqa on class/def/call lines, skipped tests and __test__ = False are not flagged."""

    RESTORED = [
        ("SuppressedTest:shop.cart.total", [11, 14], "low"),
        ("SplitByBehaviourTest:shop.cart.total", [19, 23], "low"),
        ("<module>:shop.cart.checkout", [46, 50], "low"),
    ]

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_findings(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("  # noqa: TST-02", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(shape(result), self.RESTORED)

    def test_other_noqa_codes_do_not_suppress(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("# noqa: TST-02", "# noqa: E501")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(shape(result), self.RESTORED)

    def test_removing_the_skip_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace('    @unittest.skip("flaky on CI")\n', "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["SkippedTest:shop.cart.checkout"])


class Tst02IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST02-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_cart.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_invalid_production_packages_is_unavailable(self):
        """TST02-04: an invalid optional setting is reported, never silently ignored."""
        reason = "context.production_packages must be a nonempty list of top-level package names"
        for value in ([], "shop", [""], [1], ["shop.cart"], None):
            with self.subTest(value=value):
                _, result = run("positive.py", context={**CONTEXT, "production_packages": value})
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST02-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_cart.py"])
        self.assertEqual(len(result["findings"]), 4)
        self.assertTrue(all(f["scope_id"] == "file:tests/test_cart.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertNotIn(NO_PACKAGES_NOTE, result["coverage"]["limitations"])


class Tst02BoundaryTests(unittest.TestCase):
    """TST02-06: thresholds, confidence, call forms, import kinds, input builders and identities."""

    EXPECTED = [
        ("TestTwo:shop.cart.tax", [17, 20], "low"),
        ("TestThreeSame:shop.pricing.round_price", [25, 28, 31], "medium"),
        ("TestThreeMixed:shop.pricing.round_price", [36, 39, 42], "low"),
        ("TestObjects:shop.cart.Cart.add", [47, 51, 55], "medium"),
        ("TestRelative:shop.orders.place_order", [60, 63], "low"),
        ("TestThirdParty:requests.get", [68, 71], "low"),
        ("TestNestedInput:shop.pricing.round_price", [76, 80], "low"),
        ("TestTwo:shop.cart.tax#2", [85, 88], "low"),
    ]

    def test_boundaries(self):
        _, result = run("boundary.py")
        self.assertEqual(shape(result), self.EXPECTED)

    def test_production_packages_exclude_third_party_calls(self):
        _, result = run("boundary.py", context=WITH_PACKAGES)
        self.assertEqual(shape(result), [item for item in self.EXPECTED if item[0] != "TestThirdParty:requests.get"])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        _, after = run(sources=[static_source("boundary.py", shifted)])
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [[e["line_start"] + 3 for e in f["evidence"]] for f in before["findings"]],
            [[e["line_start"] for e in f["evidence"]] for f in after["findings"]],
        )


class Tst02ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_cart.py", "CartTest:shop.cart.Cart.add")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        for index in (0, 1):
            with self.subTest(evidence=index):
                tampered = copy.deepcopy(result)
                tampered["findings"][0]["evidence"][index]["value"] = "cart.add('invented')"
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
        committed = json.loads((FIXTURES / "tst02-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst02CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst02_result(self):
        input_path = FIXTURES / "tst02-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 4)


if __name__ == "__main__":
    unittest.main()
