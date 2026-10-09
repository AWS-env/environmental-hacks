# Synthetic TST-05 fixture: legitimate exceptions. Never executed.
import unittest


class ViewTest(unittest.TestCase):
    def test_custom_assertion_may_run_code(self):
        response = self.client.get("/")
        self.assertTemplateUsed(response, "home.html")
        self.assertTemplateUsed(response, "home.html")

    def test_noqa_marks_deliberate_recheck(self):
        cache = make_cache()
        assert cache.value == 2
        assert cache.value == 2  # noqa: TST-05


async def test_awaited_operand(client):
    assert await client.count() == 1
    assert await client.count() == 1


def test_empty_is_not_a_duplicate():
    pass


def helper(cart):
    assert cart.total == 3
    assert cart.total == 3


class TestSuppressed:
    __test__ = False

    def test_repeat(self, cart):
        assert cart.total == 3
        assert cart.total == 3
