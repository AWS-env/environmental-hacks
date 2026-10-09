# Synthetic TST-08 fixture: assertions that do not compare an object's text. Never executed.
import unittest


class OrderTest(unittest.TestCase):
    def test_structured_equality(self):
        self.assertEqual(make_order(), Order(owner="ana"))

    def test_expected_value_converted(self):
        state = read_state()
        self.assertEqual(state.value, str(EXPECTED_COUNT))

    def test_substring(self):
        self.assertIn("ana", str(make_order()))

    def test_regex(self):
        self.assertRegex(repr(make_order()), r"^<Order")

    def test_decode(self):
        self.assertEqual(str(payload(), "utf-8"), "ok")

    def test_number_conversion(self):
        self.assertEqual(str(len(items())), "3")

    def test_literal(self):
        self.assertEqual(str(5), "5")

    def test_explanation_slot(self):
        order = make_order()
        self.assertEqual(order.total, 5, repr(order))


def test_truthiness():
    assert str(make_order())


def test_bare_explanation():
    order = make_order()
    assert order.total == 5, str(order)


def test_identity():
    assert str(make_order()) is not None
