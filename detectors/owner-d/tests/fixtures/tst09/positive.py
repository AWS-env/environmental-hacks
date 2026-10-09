# Synthetic TST-09 fixture: assertions guarded by conditions or loops. Never executed.
import sys
import unittest


class OrderTest(unittest.TestCase):
    def test_platform_branch(self):
        order = make_order()
        if sys.platform == "win32":
            self.assertEqual(order.path, "C:\\orders")

    def test_loop_over_results(self):
        for item in fetch_items():
            self.assertTrue(item.valid)

    def test_while_polling(self):
        job = start_job()
        while job.running:
            self.assertLess(job.progress, 101)
            job = refresh(job)


def test_branch_per_parameter(mode):
    result = run(mode)
    if mode == "fast":
        assert result.cached
    else:
        assert not result.cached


def test_fixed_cases():
    for value in (1, 2, 3):
        assert double(value) == value * 2


def test_comprehension_assert():
    [assert_valid(order) for order in load_orders()]


def test_match_on_kind(kind):
    match kind:
        case "a":
            assert parse("a") == 1
        case _:
            pass
