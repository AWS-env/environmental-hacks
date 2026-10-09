# Synthetic TST-04 fixture: value and identity boundaries. Never executed.


def test_two_is_magic():
    assert count() == 2


def test_one_is_not():
    assert count() == 1
    assert count() != -1


def test_minus_two_is_magic():
    assert offset() == -2


def test_directive_comment_is_not_an_explanation():
    assert count() == 7  # noqa: E501


def test_comment_two_lines_above_does_not_explain():
    # the count of fixture rows

    assert count() == 5


def test_setup_echo_is_low():
    rows = make_rows(5)
    assert count(rows) == 5


def test_two_is_magic():
    assert count() == 3
