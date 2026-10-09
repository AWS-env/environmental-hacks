# Synthetic TST-06 fixture: identity and threshold boundaries. Never executed.
import unittest


class TestA:
    def test_load(self):
        load()


class TestB:
    def test_load(self):
        load()


class TestOuter:
    class TestInner:
        def test_deep(self):
            load()


def test_retry():
    load()


def test_retry():
    load()


def test_one_assert_is_enough():
    load()
    assert True


class ServiceTest(unittest.TestCase):
    def test_start(self):
        start()
