# Synthetic TST-11 fixture: production module, not a test module. Never executed.
from shop import Database


class OrderService:
    def setUp(self):
        self.db = Database("sqlite://")
        self.cache = Database("redis://")

    def test_connection(self):
        return self.db.ping()

    def test_order(self):
        return self.db.orders()
