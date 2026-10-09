# Synthetic TST-03 relative imports from a test package inside the production package.
import unittest

from . import helpers
from ..cart import Cart
from .factories import make_order


class RelativeImportTest(unittest.TestCase):
    def test_parent_package_is_production(self):
        cart = Cart()
        cart.add("apple", 1)
        cart.remove("apple")
        cart.count()
        cart.clear()
        self.assertEqual(cart.total(), 0)

    def test_sibling_helpers_are_not(self):
        helpers.seed()
        helpers.reset()
        helpers.load()
        order = make_order()
        order.place()
        order.cancel()
        self.assertTrue(helpers.check(order))
