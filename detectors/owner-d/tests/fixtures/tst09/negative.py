# Synthetic TST-09 fixture: control flow that does not decide whether assertions run. Never executed.
import unittest

HAS_DB = detect_db()


class OrderTest(unittest.TestCase):
    def test_skip_guard(self):
        if not HAS_DB:
            self.skipTest("needs a database")
        self.assertEqual(count_orders(), 0)

    def test_hand_written_assertion(self):
        result = find("order-1")
        if result is None:
            self.fail("order-1 is missing")

    def test_loop_with_subtests(self):
        for value in load_values():
            with self.subTest(value=value):
                self.assertGreater(value, 0)

    def test_loop_builds_data(self):
        total = 0
        for value in load_values():
            total += value
        self.assertEqual(total, 6)


def test_conditional_operand(fast):
    assert run(fast) == (1 if fast else 2)


def test_nested_helper():
    def check(value):
        if value:
            assert value > 0

    process(check)


def test_pytest_subtests(subtests):
    for value in load_values():
        with subtests.test(value=value):
            assert value > 0


def test_raise_assertion_error(order):
    if order.total < 0:
        raise AssertionError("negative total")
