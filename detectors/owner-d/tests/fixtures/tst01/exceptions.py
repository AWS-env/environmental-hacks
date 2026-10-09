# Synthetic TST-01 fixture: suppressions and non-tests. Never executed.


def test_suppressed():  # noqa: TST-01
    user = make_user()
    assert user.name == "ada"
    assert user.age == 36


class TestDisabled:
    __test__ = False

    def test_fields(self, user):
        assert user.name == "ada"
        assert user.age == 36


def check_user(user):
    assert user.name == "ada"
    assert user.age == 36
