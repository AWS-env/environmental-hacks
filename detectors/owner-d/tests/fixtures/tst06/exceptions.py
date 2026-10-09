# Synthetic TST-06 fixture: legitimate exceptions. Never executed.
import unittest

import pytest
from matplotlib.testing.decorators import image_comparison

from .checks import check_invoice


class SkippedTest(unittest.TestCase):
    @unittest.skip("flaky on CI")
    def test_skipped(self):
        total([])


@pytest.mark.skip(reason="not supported")
def test_skipped_marker():
    total([])


def test_skips_first():
    pytest.skip("needs a GPU")
    total([])


def test_benchmark(benchmark):
    benchmark(total, [1, 2])


@image_comparison(["plot.png"])
def test_plot():
    draw()


def test_delegates_to_checker():
    check_invoice(make_invoice())


def test_doctests():
    """
    >>> total([1])
    1
    """


def test_smoke():  # noqa: TST-06
    total([1])


class TestNotCollected:
    __test__ = False

    def test_helper(self):
        total([])


@pytest.fixture
def test_data():
    return [1]


@pytest.mark.skipif(True, reason="conditional skips still run elsewhere")
def test_conditionally_skipped():
    total([])
