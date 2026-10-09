# Synthetic TST-11 fixture: suppressed, skipped, overridden and uncollected fixtures. Never executed.
import unittest

from audit import Database


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite://")
        self.audit = Database("audit://")  # noqa: TST-11

    def test_write(self):
        self.assertTrue(self.db.write("a"))

    def test_read(self):
        self.assertEqual(self.db.read(), [])


class LedgerTest(unittest.TestCase):
    def setUp(self):  # noqa: TST-11
        self.ledger = Database("ledger://")
        self.archive = Database("archive://")

    def test_post(self):
        self.assertTrue(self.ledger.post(1))

    def test_balance(self):
        self.assertEqual(self.ledger.balance(), 0)


class GpuTest(unittest.TestCase):
    def setUp(self):
        self.gpu = Database("cuda://")

    def test_kernel(self):
        self.assertTrue(self.gpu.run("k"))

    def test_memory(self):
        self.assertEqual(self.gpu.free(), 0)

    @unittest.skip("needs hardware")
    def test_driver_version(self):
        self.assertTrue(True)


class HeavyMixin:
    def setUp(self):
        self.big = Database("big://")


class LightTest(HeavyMixin, unittest.TestCase):
    def setUp(self):
        self.small = Database("small://")

    def test_a(self):
        self.assertTrue(self.small)

    def test_b(self):
        self.assertTrue(self.small.ok)


class AbstractStoreTest(unittest.TestCase):
    __test__ = False

    def setUp(self):
        self.store = Database("store://")
        self.cache = Database("cache://")

    def test_get(self):
        self.assertIsNone(self.store.get("k"))

    def test_put(self):
        self.assertTrue(self.store.put("k", 1))
