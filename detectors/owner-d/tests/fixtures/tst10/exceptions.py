# Synthetic TST-10 fixture: legitimate exceptions. Never executed.
import unittest


class EqualityTest(unittest.TestCase):
    def test_reflexive_eq(self):
        money = Money(5)
        self.assertEqual(money, money)
        self.assertTrue(money == money)

    def test_explicit_failure(self):
        try:
            charge()
        except PaymentError:
            self.assertTrue(False, "charge must not raise")


def test_unreachable_branch(kind):
    if kind == "x":
        return
    assert False, "unknown kind"


def test_identity_of_equal_literals():
    assert 1000 is 1000  # noqa: F632


def test_noqa():
    assert True  # noqa: TST-10


def helper():
    assert True
