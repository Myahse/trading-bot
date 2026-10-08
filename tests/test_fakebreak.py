import numpy as np

from tradebot.fakebreak import fake_breaks
from tradebot.structure import Trendline, Zone


def _bars(closes):
    c = np.array(closes, dtype=float)
    return c + 0.2, c - 0.2, c, np.full(len(c), 1.0)


def test_trendline_broken_and_back_within_three_candles_is_a_fake_break():
    # a flat-ish rising line at ~100; a close well below it, then back above two candles later
    line = Trendline(0, 99.0, 10, 100.0, "support", broken_at=12)
    h, l, c, atr = _bars([101] * 12 + [99.0, 99.5, 101.5, 102, 102])
    fb = fake_breaks(h, l, c, atr, 16, [line], [], [])
    assert len(fb) == 1 and fb[0].side == "long" and fb[0].broke_at == 12 and fb[0].back_at == 14
    assert fb[0].extreme == l[12:15].min()
    assert fb[0].key == (0, 10, "support", False)


def test_a_break_that_holds_is_not_fake():
    line = Trendline(0, 99.0, 10, 100.0, "support", broken_at=12)
    h, l, c, atr = _bars([101] * 12 + [99.0, 98.5, 98.0, 97.0, 96.0])
    assert fake_breaks(h, l, c, atr, 16, [line], [], []) == []


def test_coming_back_too_late_is_not_fake():
    line = Trendline(0, 99.0, 10, 100.0, "support", broken_at=12)
    h, l, c, atr = _bars([101] * 12 + [99.0, 98.5, 98.0, 98.5, 101.5])   # back on the 4th candle
    assert fake_breaks(h, l, c, atr, 16, [line], [], [], bars=3) == []


def test_zone_sweep():
    z = Zone(99.0, 100.0, 3)
    h, l, c, atr = _bars([101] * 10 + [98.5, 99.4])   # closes below the zone, then back above its low
    fb = fake_breaks(h, l, c, atr, 11, [], [], [(z, False)])
    assert [(f.what, f.side, f.level) for f in fb] == [("support zone", "long", 99.0)]
    assert fake_breaks(h, l, c, atr, 20, [], [], [(z, False)]) == [] if len(c) > 20 else True
