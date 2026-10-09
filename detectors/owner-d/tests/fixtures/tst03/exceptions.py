# Synthetic TST-03 legitimate exceptions: each test calls 5 distinct production methods.
import unittest
from unittest import mock

import pytest

from shop.cart import Cart


def test_end_to_end_scenario():  # noqa: TST-03
    cart = Cart()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.clear()
    cart.count()
    assert cart.total() == 0


@pytest.mark.skip(reason="flaky upstream")
def test_skipped():
    cart = Cart()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.clear()
    cart.count()
    assert cart.total() == 0


class SkippedInBody(unittest.TestCase):
    def test_skipped_first(self):
        self.skipTest("needs a database")
        cart = Cart()
        cart.add("apple", 1)
        cart.remove("apple")
        cart.clear()
        cart.count()
        self.assertEqual(cart.total(), 0)


class TestNotCollected:
    __test__ = False

    def test_not_collected(self):
        cart = Cart()
        cart.add("apple", 1)
        cart.remove("apple")
        cart.clear()
        cart.count()
        assert cart.total() == 0


def test_with_patched_save():
    cart = Cart()
    with mock.patch.object(Cart, "save"):
        cart.add("apple", 1)
        cart.remove("apple")
        cart.count()
        cart.save()
        assert cart.total() == 0
