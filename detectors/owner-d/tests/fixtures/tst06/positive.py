# Synthetic TST-06 fixture: tests without assertions. Never executed.
import unittest


class CartTest(unittest.TestCase):
    def setUp(self):
        self.cart = []

    def test_add_item(self):
        self.cart.append("apple")
        len(self.cart)

    def test_empty(self):
        pass

    def test_total_is_checked(self):
        self.assertEqual(len(self.cart), 0)


def test_checkout_runs():
    total = sum([1, 2, 3])
    print(total)


class TestInvoice:
    def test_render(self):
        invoice = {"total": 3}
        str(invoice)

    async def test_send(self):
        await send_later()
