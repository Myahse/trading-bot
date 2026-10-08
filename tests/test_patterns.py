import numpy as np

from tradebot.patterns import candle_pattern, chart_patterns
from tradebot.structure import Pivot


def _ohlc(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_candlestick_patterns():
    cases = {
        "bullish engulfing": [(10, 10.5, 9, 9.5), (10, 10.2, 9.6, 9.8), (9.7, 10.4, 9.6, 10.3)],
        "bearish engulfing": [(10, 10.5, 9, 9.5), (9.8, 10.2, 9.6, 10.0), (10.1, 10.2, 9.5, 9.6)],
        "morning star": [(10, 12.2, 9.9, 10.1), (12, 12.1, 9.9, 10.0), (10.0, 10.3, 9.8, 10.1), (10.1, 11.5, 10.0, 11.4)],
        "hammer": [(10, 10.5, 9, 9.5), (10, 10.5, 9, 10.2), (10.0, 10.1, 9.0, 10.1)],
        "shooting star": [(10, 10.5, 9, 9.5), (9.5, 10.5, 9.4, 9.6), (10.0, 11.0, 9.98, 10.0)],
    }
    for name, rows in cases.items():
        o, h, l, c = _ohlc(rows)
        got = candle_pattern(o, h, l, c, len(o) - 1)
        assert got is not None and got[0] == name, (name, got)
    o, h, l, c = _ohlc([(10, 10.5, 9.5, 10.2), (10.2, 10.6, 9.9, 10.4), (10.4, 10.8, 10.1, 10.6)])
    assert candle_pattern(o, h, l, c, 2) is None          # ordinary candles


def test_double_bottom_breaks_on_a_close_above_the_neckline():
    n = 60
    c = np.full(n, 102.0)                                  # between the bottoms (100) and the neckline (104)
    atr = np.full(n, 1.0)
    pivots = [Pivot(10, 100.0, "low", 13), Pivot(20, 104.0, "high", 23), Pivot(30, 100.2, "low", 33)]
    c[40] = 104.5                                          # closes 0.5 ATR above the 104 neckline
    before = chart_patterns(pivots, c, atr, 39, max_age=100, retest=12)
    assert [p.kind for p in before] == ["double bottom"] and before[0].broken_at is None
    after = chart_patterns(pivots, c, atr, 41, max_age=100, retest=12)
    assert after[0].broken_at == 40
    assert after[0].target == 104.0 + (104.0 - 100.0)     # the measured move
    assert chart_patterns(pivots, c, atr, 32, max_age=100, retest=12) == []   # not known before the 2nd low confirms


def test_double_bottom_fails_on_a_close_below_the_bottoms():
    c = np.full(60, 102.0)
    c[36] = 99.5                                           # 0.5 ATR below the 100 bottom
    pivots = [Pivot(10, 100.0, "low", 13), Pivot(20, 104.0, "high", 23), Pivot(30, 100.2, "low", 33)]
    assert chart_patterns(pivots, c, np.full(60, 1.0), 40, max_age=100, retest=12) == []


def test_head_and_shoulders():
    c = np.full(80, 103.0)
    atr = np.full(80, 1.0)
    pivots = [Pivot(10, 108.0, "high", 13), Pivot(15, 103.0, "low", 18), Pivot(20, 111.0, "high", 23),
              Pivot(25, 103.2, "low", 28), Pivot(30, 108.3, "high", 33)]
    pats = chart_patterns(pivots, c, atr, 40, max_age=100, retest=12)
    hs = [p for p in pats if p.kind == "head and shoulders"]
    assert hs and hs[0].side == "short" and hs[0].invalid == 111.0
