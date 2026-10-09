# Synthetic TST-10 fixture: assertions whose outcome depends on the code under test. Never executed.
import unittest

import numpy as np


class OrderTest(unittest.TestCase):
    def test_real_value(self):
        self.assertEqual(total([1, 2]), 3)

    def test_truthy_result(self):
        self.assertTrue(is_ready())

    def test_none_result(self):
        self.assertIsNone(find("missing"))


def test_comparison_with_literal():
    assert total([1]) == 1


def test_assert_with_message():
    result = run_job()
    assert result.ok, "job must succeed"


def test_numpy_real_values():
    np.testing.assert_equal(load(), [1, 2])


def test_different_literals():
    assert 0o20 == 16
    assert "a" in "abc"
