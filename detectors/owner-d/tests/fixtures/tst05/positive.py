# Synthetic TST-05 fixture: repeated assertions. Never executed.
import unittest

import numpy as np


class CartTest(unittest.TestCase):
    def test_total(self):
        cart = make_cart()
        self.assertEqual(cart.total, 3)
        self.assertEqual(cart.total, 3)

    def test_message_is_ignored(self):
        cart = make_cart()
        self.assertTrue(cart.items, "has items")
        self.assertIn("apple", cart.items)
        self.assertTrue(cart.items, "still has items")

    def test_alias(self):
        eq = self.assertEqual
        cart = make_cart()
        eq(cart.count, 2)
        eq(cart.count, 2)


def test_plain_assert():
    cart = make_cart()
    assert cart.total == 3
    assert cart.total == 3


def test_formatting_differs():
    cart = make_cart()
    assert len(cart.items) == 2
    assert len( cart.items )==2  # same tokens


def test_numpy_style():
    values = load()
    np.testing.assert_allclose(values.mean, 2.0, rtol=1e-6)
    np.testing.assert_allclose(values.mean, 2.0, rtol=1e-6)
