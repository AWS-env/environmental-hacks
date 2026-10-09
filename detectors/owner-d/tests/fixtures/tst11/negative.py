# Synthetic TST-11 fixture: fixtures whose fields every test needs, or that cannot be judged. Never executed.
import tempfile
import unittest
from unittest import mock

import pytest

from catalog import Database, Index, Repo, ScenarioMixin, check_fixture, connect


class AllUsedTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(connect("sqlite://"))
        self.index = Index()
        self.search = Index()

    @property
    def searcher(self):
        return self.search

    def test_direct(self):
        self.assertEqual(self.repo.find(self.index, self.search), [])

    def test_helper(self):
        self._lookup()
        self.assertTrue(self.searcher)

    def test_property_and_super_class_name(self):
        self.assertIsNot(AllUsedTest.repo, self.searcher)
        self.assertTrue(type(self).index)

    def _lookup(self):
        return self.repo.find(self.index)


class BuildChainTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect("sqlite://")
        self.repo = Repo(self.conn)
        self.addCleanup(self.repo.close)

    def test_find(self):
        self.assertEqual(self.repo.find("a"), [])

    def test_count(self):
        self.assertEqual(self.repo.count(), 0)


class CheapFieldsTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.name = "widget"
        self.limit = 10
        self.items = []
        self.default = Database.DEFAULT

    def test_name(self):
        self.assertEqual(self.name, "widget")

    def test_limit(self):
        self.assertEqual(self.limit, 10)


class SideEffectHandlesTest(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch("catalog.connect")
        self.fake_connect = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def test_one(self):
        self.assertTrue(connect("x"))

    def test_two(self):
        self.assertTrue(connect("y"))


class RestoredStateTest(unittest.TestCase):
    def setUp(self):
        self.old_level = Database.set_level("debug")

    def tearDown(self):
        Database.set_level(self.old_level)

    def test_one(self):
        self.assertTrue(Database.ping())

    def test_two(self):
        self.assertTrue(Database.ping())


class DynamicAccessTest(ScenarioMixin, unittest.TestCase):
    def setUp(self):
        self.store = Database("sqlite://")
        self.queue = Database("redis://")

    def test_by_name(self):
        for name in ("store", "queue"):
            self.assertIsNotNone(getattr(self, name))

    def test_helper(self):
        check_fixture(self)

    def test_mixin(self):
        self.run_scenario("checkout")

    def test_closure(self):
        rows = [row for row in self.store.rows() if self.queue.has(row)]
        self.assertEqual(rows, [])


class TestRequestedFixtures:
    @pytest.fixture
    def index(self):
        self.engine = Index()
        return self.engine

    def test_query(self, index):
        assert index.query("a") == []

    def test_empty(self, catalog):
        assert catalog.empty()


@pytest.fixture
def catalog():
    return Database("sqlite://")


class NoFixtureTest(unittest.TestCase):
    def test_one(self):
        self.assertTrue(Database("sqlite://"))

    def test_two(self):
        self.assertTrue(Index())
