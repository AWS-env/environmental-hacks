# Synthetic TST-02 fixture: thresholds, call forms, import kinds and identities. Never executed.
import requests

from shop import pricing
from shop.cart import Cart, tax, total
from .helpers import build
from ..shop.orders import place_order


class TestOne:
    def test_only(self):
        assert total([1]) == 1


class TestTwo:
    def test_a(self):
        assert tax(1) == 0

    def test_b(self):
        assert tax(2) == 0


class TestThreeSame:
    def test_a(self):
        assert pricing.round_price(1.0) == 1

    def test_b(self):
        assert pricing.round_price(2.0) == 2

    def test_c(self):
        assert pricing.round_price(3.0) == 3


class TestThreeMixed:
    def test_a(self):
        assert pricing.round_price(1.0) == 1

    def test_b(self):
        assert pricing.round_price(2.0) == 2

    def test_c(self):
        assert pricing.round_price(3.0) == total([3])


class TestObjects:
    def test_chain(self):
        Cart().add("a")

    def test_local(self):
        cart = Cart()
        cart.add("b")

    def test_with(self):
        with Cart() as cart:
            cart.add("c")


class TestRelative:
    def test_a(self):
        assert place_order(build()) is not None

    def test_b(self):
        assert place_order(build(2)) is not None


class TestThirdParty:
    def test_a(self):
        assert requests.get("https://a.example").ok

    def test_b(self):
        assert requests.get("https://b.example").ok


class TestNestedInput:
    def test_a(self):
        assert pricing.round_price(tax(1)) == 0

    def test_b(self):
        amount = tax(2)
        assert pricing.round_price(amount) == 0


class TestTwo:
    def test_a(self):
        assert tax(3) == 0

    def test_b(self):
        assert tax(4) == 0
