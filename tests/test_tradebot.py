import numpy as np
import pandas as pd
import pytest

from tradebot import data
from tradebot.backtest import run_backtest
from tradebot.orderblocks import find_order_blocks
from tradebot.strategy import Market, StrategyConfig
from tradebot.structure import Pivot, Trendline, find_pivots, sr_zones


def candles(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"]).assign(volume=0.0)


def test_pivots_are_confirmed_after_right_bars():
    highs = [1, 2, 3, 9, 3, 2, 1]
    df = pd.DataFrame({"open": highs, "high": highs, "low": [h - 0.5 for h in highs], "close": highs})
    pivots = [p for p in find_pivots(df, left=3, right=3) if p.kind == "high"]
    assert pivots == [Pivot(3, 9.0, "high", 6)]


def test_zones_cluster_nearby_pivots():
    pivots = [Pivot(i, p, "low", i) for i, p in enumerate([100.0, 100.3, 100.1, 110.0, 120.0, 120.2])]
    zones = sr_zones(pivots, tolerance=0.5, min_touches=2)
    assert [z.touches for z in zones] == [3, 2]
    assert zones[0].low <= 100.0 and zones[0].high >= 100.3


def test_trendline_projection():
    line = Trendline(10, 100.0, 20, 110.0, "support")
    assert line.value_at(30) == pytest.approx(120.0)


def test_bullish_order_block_is_last_down_candle_before_break():
    df = candles([
        (10, 11, 9, 10), (10, 12, 9.5, 11), (11, 15, 10.5, 12),   # swing high 15 at bar 2
        (12, 12.5, 10, 11), (11, 11.5, 9, 10), (10, 10.5, 8, 9),  # pullback; bar 5 = last red candle
        (9, 13, 8.8, 12.8), (12.8, 17, 12.5, 16.5),               # impulse closes above 15
        (16.5, 17, 16, 16.8),
    ])
    pivots = [Pivot(2, 15.0, "high", 4)]
    blocks = find_order_blocks(df, pivots, np.full(len(df), 1.0))
    assert len(blocks) == 1
    ob = blocks[0]
    assert (ob.kind, ob.index, ob.low, ob.high, ob.created_at) == ("bullish", 5, 8.0, 10.5, 7)
    assert ob.invalidated_at is None


@pytest.mark.parametrize("seed", [1, 7])
def test_no_lookahead(seed):
    """What the strategy decides at bar t must not change when future bars are removed."""
    df = data.synthetic(n=600, seed=seed)
    cfg = StrategyConfig(min_confluence=1)
    full = Market(df, cfg)
    for t in range(60, 600, 7):
        a, b = full.analyze(t), Market(df.iloc[: t + 1], cfg).analyze(t)
        assert a.support == b.support and a.resistance == b.resistance
        assert a.trendlines == b.trendlines and a.bias == b.bias
        assert [(o.kind, o.index) for o in a.order_blocks] == [(o.kind, o.index) for o in b.order_blocks]
        assert (a.signal is None) == (b.signal is None)
        if a.signal:
            assert (a.signal.side, a.signal.stop, a.signal.target) == (b.signal.side, b.signal.stop, b.signal.target)


def test_backtest_accounting_and_risk():
    df = data.synthetic(n=1500, seed=3)
    res = run_backtest(df, StrategyConfig(min_confluence=1), initial_equity=10_000, risk_per_trade=0.01, fee_bps=0)
    assert res.stats["trades"] > 0
    assert res.stats["final_equity"] == pytest.approx(10_000 + sum(t.pnl for t in res.trades))
    for tr in res.trades:
        assert tr.exit_bar is not None and tr.exit_bar >= tr.entry_bar
        if tr.exit_reason == "stop":   # a clean stop loses ~1% of equity at entry, never much more
            assert -0.0125 * 10_000 * 1.2 < tr.pnl < 0
        sign = 1 if tr.side == "long" else -1
        assert sign * (tr.target - tr.entry) > 0 > sign * (tr.stop - tr.entry)
