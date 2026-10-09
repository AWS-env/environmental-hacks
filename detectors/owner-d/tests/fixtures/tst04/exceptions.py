# Synthetic TST-04 fixture: suppressions and non-tests. Never executed.


def test_suppressed():  # noqa: TST-04
    assert make_cart().total() == 42


def test_line_suppressed():
    assert make_cart().total() == 42  # noqa: PLR2004


class TestDisabled:
    __test__ = False

    def test_total(self):
        assert make_cart().total() == 42


def check_total(cart):
    assert cart.total() == 42
