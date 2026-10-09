# Synthetic TST-02 fixture: legitimate exceptions. Never executed.
import unittest

import pytest

from shop.cart import checkout, total


class SuppressedTest(unittest.TestCase):  # noqa: TST-02
    def test_one(self):
        self.assertEqual(total([1]), 1)

    def test_two(self):
        self.assertEqual(total([2]), 2)


class SplitByBehaviourTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), 0)

    def test_negative(self):  # noqa: TST-02
        with self.assertRaises(ValueError):
            total([-1])


class SkippedTest(unittest.TestCase):
    def test_runs(self):
        checkout([1])

    @unittest.skip("flaky on CI")
    def test_skipped(self):
        checkout([2])


class TestNotCollected:
    __test__ = False

    def test_one(self):
        checkout([1])

    def test_two(self):
        checkout([2])


def test_call_line_suppressed():
    assert checkout([1]) == 1  # noqa: TST-02


def test_checkout_two():
    assert checkout([2]) == 2


@pytest.mark.skip(reason="not supported")
def test_skipped_marker():
    checkout([3])


def test_skips_first():
    pytest.skip("needs a GPU")
    checkout([4])
