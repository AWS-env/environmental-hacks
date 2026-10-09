# Synthetic TST-07 fixture: tests with more statements than the limit (30). Never executed.
import unittest


class CheckoutTest(unittest.TestCase):
    def test_full_checkout(self):
        store = Store()
        store.open()
        catalog = store.catalog
        catalog.add(Item("pen", 2))
        catalog.add(Item("ink", 5))
        catalog.add(Item("pad", 3))
        user = User("ada")
        user.verify()
        address = Address("1 Main St")
        user.addresses.append(address)
        card = Card("4242")
        card.expiry = "12/30"
        user.cards.append(card)
        cart = store.cart_for(user)
        cart.add("pen", 3)
        cart.add("ink", 1)
        cart.add("pad", 2)
        coupon = Coupon("TEN", 10)
        cart.apply(coupon)
        shipping = Shipping("standard")
        cart.ship_with(shipping)
        order = cart.checkout(card)
        receipt = order.receipt()
        lines = receipt.lines
        total = receipt.total
        tax = receipt.tax
        self.assertEqual(len(lines), 3)
        self.assertEqual(total, 16)
        self.assertEqual(tax, 1)
        self.assertEqual(order.status, "paid")
        self.assertIn("TEN", receipt.coupons)
        self.assertEqual(order.address, address)


def test_report_pipeline(tmp_path):
    source = tmp_path / "in.csv"
    source.write_text("a,b\n1,2\n")
    target = tmp_path / "out.csv"
    config = load_config()
    config.delimiter = ","
    config.header = True
    pipeline = Pipeline(config)
    pipeline.add(Reader(source))
    pipeline.add(Cleaner())
    pipeline.add(Writer(target))
    pipeline.validate()
    pipeline.warm_up()
    with pipeline.session() as session:
        session.start()
        session.process()
        session.flush()
        session.stop()
        session.commit()
    for stage in pipeline.stages:
        stage.close()
        stage.release()
        assert stage.closed
    pipeline.close()
    report = pipeline.report()
    summary = report.summary()
    rows = report.rows
    errors = report.errors
    assert target.exists()
    assert rows == 1
    assert errors == []
    assert report.duration >= 0
    assert pipeline.closed


def test_everything():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.tock(); b.tock(); c.tock(); d.tock(); e.tock(); f.tock(); g.tock(); h.tock()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
    a.load(); b.load(); c.load(); d.load(); e.load(); f.load(); g.load(); h.load()
    a.dump(); b.dump(); c.dump(); d.dump(); e.dump(); f.dump(); g.dump(); h.dump()
    a.stop(); b.stop(); c.stop(); d.stop(); e.stop(); f.stop(); g.stop(); h.stop()
    assert a.ticks == 1
    assert h.ticks == 1


def test_smoke_all_formats():
    for name in ("csv", "json", "xml"):
        writer = make_writer(name)
        writer.open()
        writer.header()
        writer.row(1)
        writer.row(2)
        writer.row(3)
        writer.footer()
        writer.flush()
        writer.close()
    reader = make_reader("csv")
    reader.open()
    reader.read()
    reader.close()
    reader = make_reader("json")
    reader.open()
    reader.read()
    reader.close()
    reader = make_reader("xml")
    reader.open()
    reader.read()
    reader.close()
    reader = make_reader("yaml")
    reader.open()
    reader.read()
    reader.close()
    reader = make_reader("toml")
    reader.open()
    reader.read()
    reader.close()
    cleanup()
