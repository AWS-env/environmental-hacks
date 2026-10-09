"""Behavioral tests for the TST-03 Eager Test detector (issue #258)."""

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
from owner_d.tst03 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst03"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"language": "python", "max_production_methods": 4, "production_packages": ["shop"]}
# Fixture files are not named test_*.py so no runner collects them; the payload locator
# carries the test-module path the detector judges.
LOCATORS = {
    "positive.py": "tests/test_shop.py",
    "negative.py": "tests/test_orders.py",
    "exceptions.py": "tests/test_suppressed.py",
    "boundary.py": "tests/test_boundary.py",
    "relative.py": "src/shop/tests/test_relative.py",
    "helpers.py": "shop/checkout.py",
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
        "scan_id": "scan-tst03-001",
        "commit_sha": "3333333333333333333333333333333333333333",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": copy.deepcopy(CONTEXT) if context is None else context,
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


class Tst03PositiveTests(unittest.TestCase):
    """TST03-01: tests calling more than 4 distinct production methods are flagged with exact evidence."""

    def test_eager_tests_are_flagged_with_exact_lines(self):
        _, result = run("positive.py")
        scope_id = "file:tests/test_shop.py"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [scope_id])
        expected = {
            "CartTest.test_checkout_flow": (
                15, "low", "calls 5 distinct production methods/functions "
                "(Cart.add, Cart.remove, Cart.apply_coupon, Cart.pop, Cart.total)"),
            "CartTest.test_stock_and_prices": (
                24, "low", "calls 5 distinct production methods/functions (Inventory.reserve, "
                "Inventory.release, Inventory.available, inventory.restock, pricing.discount)"),
            "test_everything": (
                37, "medium", "calls 10 distinct production methods/functions (Cart.add, Cart.remove, "
                "Cart.clear, Cart.count, Cart.total, pricing.tax, pricing.discount, inventory.restock, ...)"),
        }
        findings = {finding["identity"]: finding for finding in result["findings"]}
        self.assertEqual(set(findings), set(expected))
        lines = (FIXTURES / "positive.py").read_text().splitlines()
        for identity, (line, confidence, calls) in expected.items():
            finding = findings[identity]
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertIn(calls, finding["summary"])
                self.assertIn("more than the configured 4 (context.max_production_methods)", finding["summary"])
                self.assertEqual(finding["scope_id"], scope_id)
                self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_shop.py")
                self.assertEqual(evidence["line_start"], line)
                self.assertEqual(evidence["value"].splitlines()[0], lines[line - 1])
                self.assertTrue(finding["references"])
        self.assertEqual(result["measurements"], [])

    def test_tsdetect_default_limit_flags_more(self):
        """At tsDetect's default (more than one), the 2- and 4-call tests in the negative fixture are flagged too."""
        _, result = run("negative.py", context={**CONTEXT, "max_production_methods": 1})
        self.assertEqual(identities(result), [
            "OrderTest.test_stdlib_and_builtins", "OrderTest.test_many_assertions",
            "OrderTest.test_assert_helpers_from_production_packages", "OrderTest.test_one_method_many_times",
        ])
        self.assertIn("(Cart.add, Cart.remove, Cart.count, Cart.frame)", result["findings"][2]["summary"])


class Tst03NegativeTests(unittest.TestCase):
    """TST03-02: stdlib, third-party, mocks, assertions, helpers, constructors and repeats are not counted."""

    def test_busy_tests_without_many_production_calls_are_clean(self):
        _, result = run("negative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_orders.py"])
        self.assertEqual(result["findings"], [])

    def test_listing_a_library_as_production_counts_its_calls(self):
        """Production is only what the setting names: listing numpy turns its calls into production calls,
        including a method chained on a production call's result (`np.dot(...).mean()`)."""
        _, result = run("negative.py", context={**CONTEXT, "production_packages": ["shop", "numpy"]})
        self.assertEqual(identities(result), ["OrderTest.test_numpy_calls"])
        self.assertIn("(numpy.zeros, numpy.sum, numpy.ones, dot().mean, numpy.dot, Cart.total)", result["findings"][0]["summary"])

    def test_test_helper_modules_are_not_production_even_when_listed(self):
        _, result = run("negative.py", context={**CONTEXT, "production_packages": ["shop", "tests"]})
        self.assertEqual(result["findings"], [])

    def test_non_test_module_is_evaluated_with_explicit_note(self):
        _, result = run("helpers.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertIn(
            "file:shop/checkout.py: evaluated; no unittest/pytest test functions recognised, so TST-03 has nothing to flag",
            result["coverage"]["limitations"],
        )


class Tst03ExceptionTests(unittest.TestCase):
    """TST03-03: noqa on the def line, skipped tests, __test__ = False and patched methods."""

    def test_legitimate_exceptions_are_not_flagged(self):
        _, result = run("exceptions.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_removing_noqa_restores_the_finding(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("  # noqa: TST-03", "")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_end_to_end_scenario"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 10)

    def test_other_noqa_codes_do_not_suppress(self):
        content = (FIXTURES / "exceptions.py").read_text().replace("# noqa: TST-03", "# noqa: E501")
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_end_to_end_scenario"])

    def test_removing_the_patch_counts_the_method_again(self):
        content = (FIXTURES / "exceptions.py").read_text().replace(
            'with mock.patch.object(Cart, "save"):', 'with mock.patch.object(Cart, "load"):')
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(identities(result), ["test_with_patched_save"])
        self.assertIn("Cart.save", result["findings"][0]["summary"])

    def test_patch_decorator_and_monkeypatch_targets_are_not_counted(self):
        content = (
            "from unittest.mock import patch\n"
            "from shop.cart import Cart\n\n\n"
            "@patch('shop.cart.Cart.save')\n"
            "def test_decorated(mock_save, monkeypatch):\n"
            "    monkeypatch.setattr(Cart, 'load', lambda self: None)\n"
            "    monkeypatch.setattr('shop.cart.Cart.sync', lambda self: None)\n"
            "    cart = Cart()\n"
            "    cart.add('a', 1)\n"
            "    cart.save()\n"
            "    cart.load()\n"
            "    cart.sync()\n"
            "    cart.remove('a')\n"
            "    cart.count()\n"
            "    assert cart.total() == 0\n"
        )
        _, result = run(sources=[static_source("exceptions.py", content)])
        self.assertEqual(result["findings"], [])
        _, result = run(sources=[static_source("exceptions.py", content.replace("@patch('shop.cart.Cart.save')\n", ""))])
        self.assertEqual(identities(result), ["test_decorated"])


class Tst03IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """TST03-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:tests/test_shop.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_missing_or_invalid_context_settings_are_unavailable(self):
        """TST03-04: the limit and the production packages are required; without them nothing is evaluated."""
        limit = "context.max_production_methods must be a positive integer"
        packages = "context.production_packages must be a nonempty list of dotted module names"
        without_limit = {k: v for k, v in CONTEXT.items() if k != "max_production_methods"}
        without_packages = {k: v for k, v in CONTEXT.items() if k != "production_packages"}
        for context, reason in (
            (without_limit, "missing required context setting: max_production_methods"),
            (without_packages, "missing required context setting: production_packages"),
            ({**CONTEXT, "max_production_methods": 0}, limit),
            ({**CONTEXT, "max_production_methods": "4"}, limit),
            ({**CONTEXT, "max_production_methods": True}, limit),
            ({**CONTEXT, "max_production_methods": 4.0}, limit),
            ({**CONTEXT, "production_packages": []}, packages),
            ({**CONTEXT, "production_packages": "shop"}, packages),
            ({**CONTEXT, "production_packages": [""]}, packages),
            ({**CONTEXT, "production_packages": [1]}, packages),
            ({**CONTEXT, "production_packages": ["shop..cart"]}, packages),
            ({**CONTEXT, "production_packages": ["src/shop"]}, packages),
        ):
            with self.subTest(context=context):
                _, result = run("positive.py", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertIn(f"Missing or invalid context settings: {reason}", result["coverage"]["limitations"])

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """TST03-05: parse failures and non-Python files are omitted, never reported clean."""
        _, result = run("positive.py", "broken.py", "cart.test.js")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_shop.py"])
        self.assertEqual(len(result["findings"]), 3)
        self.assertTrue(all(f["scope_id"] == "file:tests/test_shop.py" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:web/cart.test.js: unsupported language", limitations)

    def test_only_malformed_input_is_unavailable(self):
        _, result = run("broken.py")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])


class Tst03BoundaryTests(unittest.TestCase):
    """TST03-06: the limit is strict, calls are distinct per class and method, names repeat."""

    def test_boundaries(self):
        _, result = run("boundary.py")
        got = [(f["identity"], f["evidence"][0]["line_start"], f["confidence"]) for f in result["findings"]]
        self.assertEqual(got, [
            ("test_five", 14, "low"),
            ("test_same_name_on_two_classes", 35, "low"),
            ("test_eight", 44, "low"),
            ("test_nine", 57, "medium"),
            ("test_five#2", 71, "low"),
        ])
        self.assertIn("(Cart.add, Cart.remove, Order.place, Cart.total, Order.total)", result["findings"][1]["summary"])

    def test_relative_imports_resolve_against_the_locator(self):
        _, result = run("relative.py")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["RelativeImportTest.test_parent_package_is_production"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 10)

    def test_relative_imports_outside_production_packages_are_not_production(self):
        _, result = run(sources=[static_source("relative.py", locator="src/other/tests/test_relative.py")])
        self.assertEqual(result["findings"], [])

    def test_narrower_production_packages_count_fewer_calls(self):
        _, result = run("positive.py", context={**CONTEXT, "production_packages": ["shop.cart"]})
        self.assertEqual(identities(result), ["CartTest.test_checkout_flow", "test_everything"])
        self.assertIn("calls 6 distinct", result["findings"][1]["summary"])
        self.assertEqual(result["findings"][1]["confidence"], "low")

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.py")
        shifted = "\n\n\n" + (FIXTURES / "boundary.py").read_text()
        _, after = run(sources=[static_source("boundary.py", shifted)])
        self.assertEqual(
            [f["fingerprint"] for f in before["findings"]],
            [f["fingerprint"] for f in after["findings"]],
        )
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )


class Tst03ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:tests/test_shop.py", "CartTest.test_checkout_flow")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.py")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "def test_invented(self):"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.py", "negative.py", "helpers.py")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "tst03-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.py"))


class Tst03CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst03_result(self):
        input_path = FIXTURES / "tst03-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 3)


if __name__ == "__main__":
    unittest.main()
