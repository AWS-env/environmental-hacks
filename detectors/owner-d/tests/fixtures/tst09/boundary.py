# Synthetic TST-09 fixture: identity and confidence boundaries. Never executed.
CASES = [("a", 1), ("b", 2)]


def test_elif_chain_is_one_finding(kind):
    if kind == "a":
        assert parse(kind) == 1
    elif kind == "b":
        assert parse(kind) == 2


def test_nested_loop_and_if(rows):
    for row in rows:
        if row.active:
            assert row.total > 0


def test_module_constant_is_fixed():
    for name, value in CASES:
        assert parse(name) == value


def test_empty_literal_is_not_fixed():
    for value in ():
        assert value


def test_range_boundaries():
    for i in range(3):
        assert square(i) >= 0
    for i in range(0):
        assert square(i) >= 0
