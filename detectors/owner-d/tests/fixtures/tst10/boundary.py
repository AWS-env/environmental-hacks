# Synthetic TST-10 fixture: identity and literal boundaries. Never executed.


def test_two_in_one_test():
    assert True
    assert 1 == 1


def test_spacing_is_ignored():
    assert {1: 2} == {1:2}


def test_empty_tuple_is_false_and_not_reported():
    assert ()


def test_none_is_none():
    assert None is None
