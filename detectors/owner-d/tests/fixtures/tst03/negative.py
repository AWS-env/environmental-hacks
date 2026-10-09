# Synthetic TST-03 negatives: busy tests that do not call more than 4 distinct production methods.
import json
import os
import unittest
from unittest import mock

import numpy as np

import shop._testing as tm

from shop.cart import Cart
from shop.checks import assert_cart_equal
from shop.inventory import Inventory, Order, Receipt, restock
from shop.models import Q
from shop.testing import factories
from tests.helpers import build_cart, build_order, make_invoice


def make_full_cart():
    cart = Cart()
    for name in ("add", "remove", "clear", "count", "total", "pop"):
        getattr(cart, name)
    cart.add("a", 1)
    cart.remove("a")
    cart.clear()
    cart.count()
    cart.total()
    cart.pop("a")
    return cart


class OrderTest(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.cart.add("apple", 1)
        self.cart.remove("apple")
        self.cart.clear()
        self.cart.count()
        self.cart.total()
        self.cart.pop("apple")

    def build(self):
        return Cart()

    def test_stdlib_and_builtins(self):
        payload = json.dumps({"a": 1})
        path = os.path.join("a", "b")
        values = sorted(list(range(5)))
        self.cart.add("apple", 1)
        self.assertEqual(len(values), 5)
        self.assertTrue(payload and path)
        self.assertEqual(self.cart.total(), 1)

    def test_numpy_calls(self):
        grid = np.zeros((2, 2))
        self.assertEqual(np.sum(np.ones(3)), 3)
        self.assertEqual(np.dot(grid, grid).mean(), 0)
        self.assertEqual(Cart().total(), 0)

    def test_mocks(self):
        gateway = mock.MagicMock()
        gateway.charge(10)
        gateway.refund(5)
        gateway.void()
        gateway.capture()
        gateway.settle()
        gateway.charge.assert_called_once_with(10)
        self.assertEqual(Cart().count(), 0)

    def test_many_assertions(self):
        cart = Cart()
        cart.add("apple", 3)
        self.assertEqual(cart.total(), 3)
        self.assertIsNotNone(cart)
        self.assertIn("apple", ["apple"])
        self.assertGreater(3, 1)
        self.assertTrue(cart)
        self.assertFalse(None)
        self.assertNotEqual(cart, None)

    def test_assert_helpers_from_production_packages(self):
        cart = Cart()
        cart.add("apple", 1)
        cart.remove("apple")
        cart.count()
        assert_cart_equal(cart, tm.make_cart())
        tm.assert_frame_equal(cart.frame(), tm.make_frame())

    def test_helpers(self):
        cart = build_cart()
        cart.add("apple", 1)
        cart.remove("apple")
        order = build_order(cart)
        order.pay()
        invoice = make_invoice(order)
        invoice.send()
        factories.create_cart().checkout()
        built = self.build()
        built.add("pear", 1)
        self.assertEqual(self.cart.total(), 0)

    def test_one_method_many_times(self):
        cart = Cart()
        for _ in range(3):
            cart.add("apple", 1)
        cart.add("pear", 1)
        cart.add("plum", 1)
        cart.add("kiwi", 1)
        cart.add("fig", 1)
        cart.add("lime", 1)
        cart.add("date", 1)
        cart.add("nut", 1)
        cart.add("yam", 1)
        cart.add("oat", 1)
        assert cart.total() == 12

    def test_properties_and_dicts(self):
        cart = Cart()
        data = {"apple": 1}
        data.get("apple")
        data.items()
        data.keys()
        data.values()
        data.pop("apple")
        assert cart.items == {}
        assert cart.is_empty
        assert cart.owner.name == "x"

    def test_constructors(self):
        Cart()
        Inventory()
        Order()
        Receipt()
        Q(name="apple")
        assert Cart.from_dict({}) is not None

    def test_shadowed_import(self):
        restock = mock.Mock()
        restock("apple", 1)
        Cart = mock.Mock
        cart = Cart()
        cart.add("a", 1)
        cart.remove("a")
        cart.clear()
        cart.count()
        cart.total()
        restock.assert_called_once()
