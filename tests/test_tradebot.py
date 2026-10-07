import numpy as np
import pandas as pd
import pytest

from tradebot import data
from tradebot.backtest import run_backtest
from tradebot.orderblocks import find_order_blocks
from tradebot.fractal import HigherTimeframe
from tradebot.strategy import Market, StrategyConfig, mode_config
from tradebot.structure import Pivot, Trendline, find_pivots, sr_zones, trendlines


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


def test_trendline_break_is_recorded():
    pivots = [Pivot(0, 10.0, "high", 2), Pivot(10, 8.0, "high", 12)]   # falling: -0.2 per bar
    closes = np.full(30, 5.0)
    closes[20] = 7.5                                                    # line is at 6.0 on bar 20
    (line,) = trendlines(pivots, closes, 25, tolerance=0.1)
    assert line.kind == "resistance" and line.broken_at == 20
    assert trendlines(pivots, closes, 19, tolerance=0.1)[0].broken_at is None


def test_breakout_signal_on_trendline_break():
    # Falling highs at bars 5 and 15, then a strong green candle closes through the line.
    rows = [(10, 10.5, 9.5, 10)] * 40
    rows[5], rows[15] = (10, 14, 9.5, 10), (10, 12, 9.5, 10)          # line: 14 -> 12, slope -0.2
    rows[20] = (10, 11.6, 9.9, 11.5)                                    # line at 11.0 on bar 20
    cfg = StrategyConfig(pivot_left=3, pivot_right=3, atr_period=3, trend_filter=False, min_rr=0.5)
    sig = Market(candles(rows), cfg).analyze(20).signal
    assert sig is not None and (sig.side, sig.setup) == ("long", "breakout")
    assert sig.stop < 9.9


def test_higher_timeframe_candle_is_known_only_after_it_closes():
    df = data.synthetic(n=100, seed=2)
    htf = HigherTimeframe(df, 10, pivot=2, atr_period=3)
    assert list(htf.known_at[:3]) == [10, 20, 30]
    assert len(htf.bars) == 9                       # the last group may be unfinished, so it is dropped
    assert htf.bars.high[0] == df.high[:10].max()
    assert all(p.confirmed_at >= p.index for p in htf.pivots)


CONFIGS = [StrategyConfig(min_confluence=1), StrategyConfig(min_confluence=1, htf=6),
           StrategyConfig(min_confluence=1, htf="4h", pivot_left=3, pivot_right=3)]


@pytest.mark.parametrize("seed", [1, 7])
@pytest.mark.parametrize("cfg", CONFIGS, ids=["entry-only", "htf-bars", "htf-4h"])
def test_no_lookahead(seed, cfg):
    """What the strategy decides at bar t must not change when future bars are removed."""
    df = data.synthetic(n=600, seed=seed)
    full = Market(df, cfg)
    for t in range(60, 600, 7):
        a, b = full.analyze(t), Market(df.iloc[: t + 1], cfg).analyze(t)
        assert a.support == b.support and a.resistance == b.resistance
        assert a.trendlines == b.trendlines and a.bias == b.bias
        assert a.htf_bias == b.htf_bias and a.htf_support == b.htf_support and a.htf_resistance == b.htf_resistance
        assert [(o.kind, o.index) for o in a.order_blocks] == [(o.kind, o.index) for o in b.order_blocks]
        assert (a.signal is None) == (b.signal is None)
        if a.signal:
            assert (a.signal.side, a.signal.setup, a.signal.stop, a.signal.target) == \
                (b.signal.side, b.signal.setup, b.signal.stop, b.signal.target)


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


@pytest.mark.parametrize("mode", ["scalp", "swing"])
def test_modes_run(mode):
    df = data.synthetic(n=1200, seed=5)
    cfg = mode_config(mode, htf=12)   # synthetic data is hourly, so use a bar count for the higher timeframe
    res = run_backtest(df, cfg)
    assert res.stats["final_equity"] > 0


def test_cooldown_spaces_out_trades_after_a_loss():
    df = data.synthetic(n=1500, seed=3)
    res = run_backtest(df, StrategyConfig(min_confluence=1, cooldown_bars=10))
    for prev, nxt in zip(res.trades, res.trades[1:]):
        if prev.pnl < 0:
            assert nxt.entry_bar - prev.exit_bar > 10
