# Synthetic TST-05 fixture: similar but not duplicate assertions. Never executed.
import unittest

import pytest


class OrderTest(unittest.TestCase):
    def test_state_changes_between_checks(self):
        order = make_order()
        self.assertEqual(order.count, 0)
        order.add("apple")
        self.assertEqual(order.count, 1)
        order.remove("apple")
        self.assertEqual(order.count, 0)

    def test_call_in_operand(self):
        reader = open_reader()
        self.assertEqual(reader.read(), b"")
        self.assertEqual(reader.read(), b"")

    def test_raises_call_form_runs_code(self):
        self.assertRaises(ValueError, parse, "x")
        self.assertRaises(ValueError, parse, "x")

    def test_context_managers_guard_different_code(self):
        with self.assertRaises(ValueError):
            parse("x")
        with self.assertRaises(ValueError):
            parse("y")


def test_different_operands():
    cart = make_cart()
    assert cart.total == 3
    assert cart.count == 3


def test_exclusive_branches(flag):
    cart = make_cart()
    if flag:
        assert cart.total == 3
    else:
        assert cart.total == 3


def test_loop_body_is_one_assertion(carts):
    for cart in carts:
        assert cart.total == 3


def test_spelling_differs(items, expected):
    assert items[:4] == expected
    assert items[:4:] == expected


def test_list_consumes_iterator():
    it = make_iter()
    assert list(it) == [1]
    assert list(it) == [1]


def test_lambdas_are_not_statements(run):
    run(lambda c: pytest.approx(c.total) == 3, lambda c: pytest.approx(c.total) == 3)


def test_same_assertion_in_two_tests_a():
    cart = make_cart()
    assert cart.total == 3


def test_same_assertion_in_two_tests_b():
    cart = make_cart()
    assert cart.total == 3
