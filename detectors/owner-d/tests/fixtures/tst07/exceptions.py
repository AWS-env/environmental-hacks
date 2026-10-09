# Synthetic TST-07 fixture: suppressions, skipped tests and non-tests (limit 30). Never executed.
import pytest


def test_end_to_end_scenario():  # noqa: TST-07
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save()
    assert a.ticks == 1


@pytest.mark.skip(reason="flaky upstream")
def test_skipped_scenario():
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save()
    assert a.ticks == 1


def test_skipped_at_runtime():
    pytest.skip("needs a GPU")
    a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
    a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
    a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
    a.save(); b.save(); c.save(); d.save(); e.save(); f.save()
    assert a.ticks == 1


class TestScenarioBase:
    __test__ = False

    def test_scenario(self):
        a = make(1); b = make(2); c = make(3); d = make(4); e = make(5); f = make(6); g = make(7); h = make(8)
        a.link(b); b.link(c); c.link(d); d.link(e); e.link(f); f.link(g); g.link(h); h.link(a)
        a.tick(); b.tick(); c.tick(); d.tick(); e.tick(); f.tick(); g.tick(); h.tick()
        a.save(); b.save(); c.save(); d.save(); e.save(); f.save(); g.save()
        assert a.ticks == 1
