# Synthetic TST-02 fixture: tests sharing a production method. Never executed.
import unittest

import shop.pricing as pricing
from shop.cart import Cart, total


class CartTest(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()

    def test_add_one(self):
        self.cart.add("apple")
        self.assertEqual(self.cart.count(), 1)

    def test_add_two(self):
        self.cart.add("apple")
        self.cart.add("pear")
        self.assertEqual(self.cart.count(), 2)


class TestDiscount:
    def test_half_price(self):
        assert pricing.discount(100, 0.5) == 50

    def test_no_discount(self):
        basket = Cart()
        basket.add("apple")
        assert pricing.discount(basket.subtotal(), 0) == basket.subtotal()


def test_total_empty():
    assert total([]) == 0


def test_total_one():
    assert total([3]) == 3


def test_total_many():
    assert total([1, 2, 3]) == 6
