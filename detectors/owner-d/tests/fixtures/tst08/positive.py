# Synthetic TST-08 fixture: equality assertions on str()/repr() text. Never executed.
import unittest


class CartTest(unittest.TestCase):
    def test_add_item(self):
        cart = Cart()
        cart.add("apple")
        self.assertEqual(str(cart), "Cart(items=['apple'])")

    def test_owner(self):
        order = Order(owner="ana")
        self.assertEqual(repr(order.owner), "<User: ana>")

    def test_copy(self):
        cart = Cart(["apple"])
        self.assertEqual(repr(cart.copy()), repr(cart))

    def test_not_empty(self):
        self.assertNotEqual(Cart(["pear"]).__str__(), "Cart(items=[])")


def test_total_label():
    total = Total(5)
    expected = "Total: 5.00 EUR"
    assert str(total) == expected


def test_receipt():
    receipt = checkout()
    assert f"Receipt #{receipt.id}" == str(receipt)


def test_reload():
    assert str(load("a")) == str(load("b"))


def test_build():
    assert repr(build()) == (
        "Build(id=1)"
    )
