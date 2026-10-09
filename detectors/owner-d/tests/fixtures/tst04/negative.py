# Synthetic TST-04 fixture: named, explained or ordinary values. Never executed.
import unittest

import pytest

TAX_RATE = 0.2
EXPECTED_TOTAL = 42


class CartTest(unittest.TestCase):
    def test_named_constant(self):
        self.assertEqual(make_cart().total(), EXPECTED_TOTAL)

    def test_derived_expectation(self):
        price, quantity = 14, 3
        self.assertEqual(make_cart(price, quantity).total(), price * quantity)

    def test_ordinary_values(self):
        cart = make_cart()
        self.assertEqual(len(cart.items), 0)
        self.assertEqual(cart.find("x"), -1)
        self.assertEqual(cart.version, 1.0)
        self.assertIs(cart.empty, True)

    def test_message_explains(self):
        self.assertEqual(make_cart().total(), 42, "3 items at 14 each")
        self.assertGreater(make_cart().weight(), 2.5, msg="heavier than the box")

    def test_tolerance_only(self):
        self.assertAlmostEqual(make_cart().tax(), TAX_RATE, places=3)
        self.assertAlmostEqual(make_cart().tax(), TAX_RATE, delta=0.01)


def test_comment_explains():
    # 3 items at 14 each
    assert make_cart().total() == 42
    assert make_cart().weight() == 1.5  # kg, from the catalogue fixture


def test_literals_inside_expressions():
    assert make_cart(items=3).total() == 3 * 14
    assert totals()[2] == EXPECTED_TOTAL
    assert round(make_cart().tax(), 2) == pytest.approx(TAX_RATE)


def test_literal_outside_assertion():
    cart = make_cart(price=14, quantity=3)
    assert cart.is_valid()
