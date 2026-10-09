# Synthetic TST-07 fixture: limit (30) and identity boundaries. Never executed.


def test_exactly_thirty():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save()
    assert a.ticks == 1


def test_thirty_one():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save()
    assert a.ticks == 1


def test_docstring_not_counted():
    """Thirty statements after this docstring."""
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save()
    assert a.ticks == 1


def test_nested_blocks_count():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    e.save(); f.save(); g.save(); h.save(); a.save()

    def check(node):
        node.tick()
        assert node.ticks == 1

    for node in (a, b, c, d):
        check(node)
    if h.ready:
        h.save()
    else:
        h.reset()
        h.save()
    assert e.ticks == 0


def test_exactly_sixty():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.tock(); b.tock(); c.tock(); d.tock(); e.tock(); f.tock(); g.tock(); h.tock()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
    a.load(); b.load(); c.load(); d.load(); e.load(); f.load(); g.load(); h.load()
    a.stop(); b.stop(); c.stop(); d.stop(); e.stop(); f.stop(); g.stop(); h.stop()
    a.dump(); b.dump(); c.dump()
    assert a.ticks == 1


def test_sixty_one():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.tock(); b.tock(); c.tock(); d.tock(); e.tock(); f.tock(); g.tock(); h.tock()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save(); h.save()
    a.load(); b.load(); c.load(); d.load(); e.load(); f.load(); g.load(); h.load()
    a.stop(); b.stop(); c.stop(); d.stop(); e.stop(); f.stop(); g.stop(); h.stop()
    a.dump(); b.dump(); c.dump(); d.dump()
    assert a.ticks == 1


def test_thirty_one():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save()
    assert a.ticks == 2
