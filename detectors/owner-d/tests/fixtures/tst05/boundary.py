# Synthetic TST-05 fixture: identity and repeat-count boundaries. Never executed.


def test_three_times(cart):
    assert cart.total == 3
    assert cart.total == 3
    assert cart.total == 3


def test_two_groups(cart):
    assert cart.total == 3
    assert cart.count == 1
    assert cart.total == 3
    assert cart.count == 1


def test_run_restarts_after_statement(cart):
    assert cart.total == 3
    cart.clear()
    assert cart.total == 3
    assert cart.total == 3
