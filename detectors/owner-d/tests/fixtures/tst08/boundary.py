# Synthetic TST-08 fixture: identity and rule boundaries. Never executed.


def test_two_in_one_test():
    assert str(load()) == "a"
    assert str(load()) == "b"


def test_chained_comparison_is_not_read():
    assert str(load()) == "a" == text


def test_reassigned_expected_is_not_literal():
    expected = "Load(1)"
    expected = expected.upper()
    assert str(load()) == expected


def test_concatenated_expected():
    assert str(load()) == "Load(" + ident + ")"


def test_stream_prefix_is_not_matched():
    assert repr(stream()) == "<Stream>"
