# Synthetic TST-11 fixture: fixtures that build fields some tests never read. Never executed.
import unittest

import pytest

from shop import Cache, Client, Database, Mailer, Order, load_templates, render


class OrderTest(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite://")
        self.client = Client(self.db)
        self.mailer = Mailer()
        self.cache = Cache(size=1024)
        self.currency = "EUR"

    @property
    def warm_cache(self):
        return self.cache

    def test_total(self):
        order = Order(self.client, currency=self.currency, cache=self.warm_cache)
        self.assertEqual(order.total(), 0)

    def test_email(self):
        self._send()
        self.assertTrue(self.client.connected)

    def test_render(self):
        self.assertIn("EUR", render(self.client, self.cache))

    def _send(self):
        self.mailer.send("hi")


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Database("postgres://reports")
        cls.templates = load_templates()

    def test_summary(self):
        self.assertTrue(render(self.engine, self.templates))

    def test_schema(self):
        self.assertEqual(self.engine.tables(), [])


class TestInvoice:
    def setup_method(self, method):
        self.order = Order(Client(Database("sqlite://")))
        self.pdf = render(self.order)

    def test_pdf(self):
        assert self.pdf

    def test_number(self):
        assert Order.next_number() > 0


class TestShipping:
    @pytest.fixture(autouse=True)
    def carrier(self):
        self.carrier_api = Client(Database("sqlite://"))
        yield

    def test_rate(self):
        assert self.carrier_api.rate("DE")

    def test_free(self):
        assert Order(None).free_shipping()

    def test_label(self):
        assert Order(None).label()


class StorageMixin:
    def setUp(self):
        self.bucket = Database("s3://bucket")
        self.archive = Database("glacier://vault")

    def test_put(self):
        self.bucket.put("k", b"v")
        self.assertTrue(self.bucket.exists("k"))


class LocalStorageTest(StorageMixin, unittest.TestCase):
    def test_list(self):
        self.assertEqual(self.bucket.list(), [])

    def test_restore(self):
        self.assertTrue(Database.restore("k"))
