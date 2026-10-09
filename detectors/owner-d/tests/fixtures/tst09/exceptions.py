# Synthetic TST-09 fixture: suppressions and non-tests. Never executed.
import sys


def test_documented_platform_branch():
    path = config_path()
    if sys.platform == "win32":  # noqa: TST-09
        assert path.startswith("C:")


class TestDisabled:
    __test__ = False

    def test_loop(self, items):
        for item in items:
            assert item


def helper(items):
    for item in items:
        assert item
