# Synthetic TST-03 fixture: production module, not a test module. Never executed.
from shop.cart import Cart


def checkout(items):
    cart = Cart()
    for item in items:
        cart.add(item, 1)
    cart.apply_coupon("SAVE10")
    cart.remove("plum")
    cart.count()
    cart.clear()
    return cart.total()
