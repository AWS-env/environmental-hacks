# Synthetic TST-01 fixture: threshold and identity boundaries. Never executed.


def test_exactly_two():
    assert load() == 1
    assert load() == 2


def test_exactly_one():
    assert load() == 1


def test_nested_helper_counts():
    def check(value):
        assert value > 0

    assert load() == 1
    check(load())


def test_exactly_two():
    assert load() == 3
    assert load() == 4
