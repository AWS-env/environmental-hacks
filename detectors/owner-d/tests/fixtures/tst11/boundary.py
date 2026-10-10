# Synthetic TST-11 fixture: thresholds and identity boundaries. Never executed.
import unittest

from pool import Database, register


class OneTest(unittest.TestCase):
    def setUp(self):
        self.used = Database("a://")
        self.spare = Database("b://")

    def test_only(self):
        self.assertTrue(self.used)


class TwoTests(unittest.TestCase):
    def setUp(self):
        self.used = Database("a://")
        self.spare = Database("b://")

    def test_first(self):
        self.assertTrue(self.used and self.spare)

    def test_second(self):
        self.assertTrue(self.used)


class TeardownOnlyTest(unittest.TestCase):
    def setUp(self):
        self.conn = Database("c://")

    def tearDown(self):
        self.conn.close()

    def test_a(self):
        self.assertTrue(Database.ping())

    def test_b(self):
        self.assertTrue(Database.ping())


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.half = Database("h://")
        self.most = Database("m://")

    def test_1(self):
        self.assertTrue(self.half and self.most)

    def test_2(self):
        self.assertTrue(self.half and self.most)

    def test_3(self):
        self.assertTrue(self.most)

    def test_4(self):
        self.assertTrue(Database.ping())


class EscapeTest(unittest.TestCase):
    def setUp(self):
        self.early = Database("e://")
        register(self)
        self.late = Database("l://")

    def test_passes_self(self):
        register(self)

    def test_plain(self):
        self.assertTrue(Database.ping())


class TwoTests(unittest.TestCase):
    def setUp(self):
        self.used = Database("a://")
        self.spare = Database("b://")

    def test_first(self):
        self.assertTrue(self.used)

    def test_second(self):
        self.assertTrue(self.used)
