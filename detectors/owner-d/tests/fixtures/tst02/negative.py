# Synthetic TST-02 fixture: shared calls that are not production methods. Never executed.
import json
import os.path
import unittest
from unittest import mock

import numpy.testing as npt
import pytest
import shop.inventory
import shop.tests.factories

from shop._testing import seed_database
from shop.inventory import Inventory, restock, reserve
from tests.helpers import build_items
from .factories import make_item


def local_helper(items):
    return sorted(items)


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        restock(self.inventory, 10)

    def _prepare(self):
        return build_items(3)

    def test_restock(self):
        seed_database()
        shop.tests.factories.reset()
        stock = Inventory()
        restock(stock, len(self._prepare()))
        self.assertEqual(json.dumps(stock.levels()), "{}")
        self.assertTrue(os.path.exists("/tmp"))

    def test_reserve(self):
        seed_database()
        shop.tests.factories.reset()
        stock = Inventory()
        reserve(stock, make_item())
        npt.assert_equal(local_helper([2, 1]), [1, 2])
        self.assertEqual(len(self._prepare()), 3)

    def test_mocked(self):
        with mock.patch("shop.inventory.restock") as patched:
            patched.return_value = None
            self.assertIsNone(patched())
        with pytest.raises(ValueError):
            build_items(-1)


class TestRestockAgain:
    def test_restock_negative(self):
        with pytest.raises(ValueError):
            restock(Inventory(), -1)


def test_restock_zero(restock):
    assert restock(0) is None


def test_reserve_none():
    reserve = lambda *_: None
    assert reserve(None) is None
