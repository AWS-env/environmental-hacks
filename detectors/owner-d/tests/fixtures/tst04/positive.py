# Synthetic TST-04 fixture: unexplained numeric literals in assertions. Never executed.
import unittest

import numpy as np
import pytest


class CartTest(unittest.TestCase):
    def test_total(self):
        cart = make_cart(price=14, quantity=3)
        self.assertEqual(cart.total(), 42)

    def test_item_count(self):
        cart = make_cart(price=14, quantity=3)
        self.assertEqual(len(cart.items), 3)
        self.assertEqual(cart.quantity, 3)

    def test_rounding(self):
        self.assertAlmostEqual(cart_average(), 4.67, places=2)
        self.assertTrue(cart_weight() > 2.5)


def test_shipping_fee():
    assert shipping_fee(weight=10) == 7.25


def test_status():
    response = client.get("/cart")
    assert response.status_code == 200


def test_ratio_approx():
    assert ratio() == pytest.approx(-0.25)


def test_scores():
    np.testing.assert_allclose(scores().mean(), 0.75, rtol=1e-3)
    assert 0 < scores().max() < 100
