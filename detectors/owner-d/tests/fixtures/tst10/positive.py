# Synthetic TST-10 fixture: assertions that always pass. Never executed.
import unittest

import numpy as np


class PlaceholderTest(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(True)

    def test_same_literal(self):
        self.assertEqual(1, 1)

    def test_literal_none(self):
        self.assertIsNone(None)

    def test_not_empty_literal(self):
        self.assertFalse([])


def test_bare_true():
    run_job()
    assert True


def test_tuple_mistake():
    result = run_job()
    assert (result.ok, "job must succeed")


def test_same_string():
    assert "ready" == "ready"


def test_numpy_same_literal():
    np.testing.assert_equal([1, 2], [1, 2])
