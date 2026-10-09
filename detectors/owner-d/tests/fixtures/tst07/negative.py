# Synthetic TST-07 fixture: long code that is not a verbose test (limit 30). Never executed.
import unittest

import pytest

EXPECTED = {"pen": 2, "ink": 5}


def build_world():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
    return a


@pytest.fixture
def test_world():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
    return a


class InventoryTest(unittest.TestCase):
    def setUp(self):
        a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
        a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
        a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
        a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
        self.world = a

    def test_short(self):
        self.assertEqual(self.world.ticks, 1)

    def test_large_expected_literal(self):
        report = self.world.report()
        expected = {
            "pen": {"count": 2, "price": 1.5},
            "ink": {"count": 5, "price": 3.0},
            "pad": {"count": 3, "price": 2.25},
            "cap": {"count": 1, "price": 0.5},
            "nib": {"count": 9, "price": 0.75},
            "box": {"count": 4, "price": 6.0},
            "bag": {"count": 7, "price": 9.5},
            "tag": {"count": 8, "price": 0.1},
            "map": {"count": 6, "price": 4.0},
            "pin": {"count": 2, "price": 0.2},
            "cup": {"count": 3, "price": 5.0},
            "jar": {"count": 1, "price": 7.5},
            "lid": {"count": 5, "price": 0.3},
            "mug": {"count": 4, "price": 8.0},
            "rug": {"count": 2, "price": 30.0},
            "fan": {"count": 1, "price": 25.0},
            "pot": {"count": 3, "price": 12.0},
            "pan": {"count": 2, "price": 15.0},
            "key": {"count": 9, "price": 1.0},
            "toy": {"count": 6, "price": 3.5},
            "hat": {"count": 2, "price": 11.0},
            "kit": {"count": 1, "price": 40.0},
            "net": {"count": 1, "price": 18.0},
            "oar": {"count": 2, "price": 22.0},
            "saw": {"count": 1, "price": 14.0},
            "axe": {"count": 1, "price": 19.0},
            "awl": {"count": 3, "price": 2.0},
            "vat": {"count": 1, "price": 55.0},
            "urn": {"count": 1, "price": 33.0},
            "tub": {"count": 2, "price": 21.0},
        }
        self.assertEqual(report, expected)

    def test_documented(self):
        """Checks the totals after a restock.

        The docstring, comments and blank lines below are not statements, so
        this test stays under the limit however long it looks.
        """
        # Restock the pens first: the supplier ships in packs of ten.
        self.world.restock("pen", 10)

        # Then the ink, which ships in packs of five.
        self.world.restock("ink", 5)

        # A restock never changes prices.
        # Prices are checked in test_prices.

        # Totals: 2 + 10 pens, 5 + 5 ink.
        self.assertEqual(self.world.count("pen"), 12)
        self.assertEqual(self.world.count("ink"), 10)


def test_call_spanning_many_lines():
    order = place_order(
        user="ada",
        items=["pen", "ink", "pad", "cap", "nib", "box", "bag", "tag", "map", "pin"],
        address="1 Main St",
        city="Springfield",
        country="US",
        card="4242",
        expiry="12/30",
        coupon="TEN",
        shipping="standard",
        gift=False,
        note=None,
        currency="USD",
        locale="en_US",
        newsletter=False,
        source="web",
        campaign=None,
        referrer=None,
        channel="direct",
        priority=1,
        retries=3,
        timeout=30,
        dry_run=True,
    )
    assert order.total == EXPECTED["pen"] + EXPECTED["ink"]
