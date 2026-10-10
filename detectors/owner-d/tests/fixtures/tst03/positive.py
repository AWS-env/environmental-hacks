# Synthetic TST-03 positives: each test calls more than 4 distinct production methods.
import unittest

import pytest

from shop import pricing
from shop.cart import Cart
from shop.inventory import Inventory, restock


class CartTest(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()

    def test_checkout_flow(self):
        cart = Cart()
        cart.add("apple", 2)
        cart.add("pear", 1)
        cart.remove("pear")
        cart.apply_coupon("SAVE10")
        self.assertRaises(KeyError, lambda: cart.pop("plum"))
        self.assertEqual(cart.total(), 9)

    def test_stock_and_prices(self):
        self.inventory.reserve("apple", 2)
        self.inventory.release("apple", 1)
        self.assertEqual(self.inventory.available("apple"), 9)
        restock("apple", 10)
        self.assertAlmostEqual(pricing.discount(10, 0.1), 9.0)


@pytest.fixture
def cart():
    return Cart()


def test_everything(cart):
    cart.add("apple", 1)
    cart.remove("apple")
    cart.clear()
    assert cart.count() == 0
    assert cart.total() == 0
    assert pricing.tax(10) == 1
    assert pricing.discount(10, 0.5) == 5
    restock("apple", 1)
    assert Inventory().reserve("apple", 1)
    assert Cart.from_dict({"apple": 1})
