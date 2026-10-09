# Synthetic TST-06 fixture: every test asserts something. Never executed.
import unittest
from unittest import mock

import numpy as np
import pytest


class OrderTests(unittest.TestCase):
    def test_assert_method(self):
        self.assertEqual(total([1, 2]), 3)

    def test_assert_raises_context(self):
        with self.assertRaises(ValueError):
            total(None)

    def test_fail_on_error(self):
        try:
            total([])
        except ValueError:
            self.fail("empty orders must total 0")

    def test_mock_assert(self):
        sender = mock.Mock()
        notify(sender)
        sender.send.assert_called_once_with("done")

    def test_alias(self):
        eq = self.assertEqual
        eq(total([1]), 1)

    def test_same_class_helper(self):
        self.roundtrip([2])

    def roundtrip(self, items):
        self.assertEqual(total(items), sum(items))


def test_plain_assert():
    assert total([1, 2]) == 3


def test_pytest_raises():
    with pytest.raises(ValueError):
        total(None)


def test_numpy_testing():
    np.testing.assert_allclose(mean([1.0, 3.0]), 2.0)


def test_raise_assertion_error():
    if total([]) != 0:
        raise AssertionError("empty orders must total 0")


def test_module_helper():
    roundtrip_total([1, 2])


def roundtrip_total(items):
    assert total(items) == sum(items)


def test_nested_callback():
    def callback(value):
        assert value > 0

    process([1], callback)


def helper():
    total([])


class Helpers:
    def test_like_but_not_collected(self):
        total([])
