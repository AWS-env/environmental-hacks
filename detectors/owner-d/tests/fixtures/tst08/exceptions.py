# Synthetic TST-08 fixture: legitimate exceptions. Never executed.
import unittest
import warnings

import pytest


class MoneyTest(unittest.TestCase):
    def test_repr(self):
        self.assertEqual(repr(Money(5)), "Money(5)")

    def test___str__(self):
        self.assertEqual(str(Money(5)), "5.00 EUR")

    def test_invalid_amount(self):
        with self.assertRaises(ValueError) as cm:
            Money(-1)
        self.assertEqual(str(cm.exception), "amount must be positive")

    def test_parse(self):
        try:
            parse("x")
        except ParseError as problem:
            self.assertEqual(str(problem), "bad input")


class TestMoneyFormatting:
    def test_eur(self):
        assert str(Money(5)) == "5.00 EUR"


def test_negative_amount():
    with pytest.raises(ValueError) as info:
        Money(-1)
    assert str(info.value) == "amount must be positive"


def test_deprecated_currency():
    with warnings.catch_warnings(record=True) as caught:
        Money(5, "DEM")
    assert str(caught[0].category) == "<class 'DeprecationWarning'>"


def test_returned_error():
    err = validate(Money(-1))
    assert str(err) == "amount must be positive"


def test_noqa():
    assert repr(Money(5)) == "Money(5)"  # noqa: TST-08


def helper():
    assert str(Money(5)) == "5.00 EUR"
