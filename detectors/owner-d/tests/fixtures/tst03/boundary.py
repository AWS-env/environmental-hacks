# Synthetic TST-03 boundaries at max_production_methods = 4 (medium above 8).
from shop.cart import Cart
from shop.orders import Order


def test_exactly_four():
    cart = Cart()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.count()
    assert cart.total() == 0


def test_five():
    cart = Cart()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.count()
    cart.clear()
    assert cart.total() == 0


def test_two_carts_count_once():
    first = Cart()
    second = Cart()
    first.add("apple", 1)
    second.add("pear", 1)
    first.remove("apple")
    second.remove("pear")
    first.count()
    second.count()
    assert first.total() == second.total()


def test_same_name_on_two_classes():
    cart = Cart()
    order = Order()
    cart.add("apple", 1)
    cart.remove("apple")
    order.place()
    assert cart.total() == order.total()


def test_eight():
    cart = Cart()
    order = Order()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.count()
    cart.clear()
    assert cart.total() == 0
    order.place()
    order.cancel()
    assert order.total() == 0


def test_nine():
    cart = Cart()
    order = Order()
    cart.add("apple", 1)
    cart.remove("apple")
    cart.count()
    cart.clear()
    assert cart.total() == 0
    order.place()
    order.cancel()
    order.refund()
    assert order.total() == 0


def test_five():
    with Cart() as cart:
        cart.add("apple", 1).add("pear", 1)
        cart.remove("apple")
        cart.count()
        cart.clear()
        assert cart.total() == 0
