import numpy as np
import pandas as pd
import pytest

from tradebot import data
from tradebot.backtest import run_backtest
from tradebot.orderblocks import find_order_blocks
from tradebot.fractal import HigherTimeframe
from tradebot.strategy import Market, StrategyConfig, mode_config
from tradebot.structure import Pivot, Trendline, TrendlineFinder, find_pivots, sr_zones


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


def _finder(lows):
    lows = np.asarray(lows, float)
    return TrendlineFinder(lows + 1, lows, lows + 0.5, np.ones(len(lows)))


def _lows():
    lows = np.full(40, 20.0)
    lows[[0, 10, 20, 30]] = [10, 12, 14, 15]     # 0, 10, 20 are on one line (+0.2/bar); 30 undercuts it
    return lows


SWINGS = [Pivot(i, p, "low", i + 3) for i, p in [(0, 10.0), (10, 12.0), (20, 14.0), (30, 15.0)]]


def test_trendline_prefers_the_line_with_most_touches():
    (line,) = _finder(_lows()).lines_at(25, SWINGS[:3], retest_window=10)
    assert (line.kind, line.i1, line.touches, line.broken_at) == ("support", 0, (0, 10, 20), None)


def test_trendline_break_is_revealed_only_when_it_happens_and_a_new_line_takes_over():
    lines = _finder(_lows()).lines_at(35, SWINGS, retest_window=10)
    intact = [ln for ln in lines if ln.broken_at is None]
    broken = [ln for ln in lines if ln.broken_at is not None]
    assert broken[0].touches == (0, 10, 20) and broken[0].broken_at == 30
    assert (intact[0].i1, intact[0].i2) == (0, 30)    # redrawn under the new low
    assert _finder(_lows()).lines_at(29, SWINGS[:3], retest_window=10)[0].broken_at is None


def test_trendline_never_cuts_through_a_candle():
    lows = _lows()
    lows[5] = 9.0                                    # a wick below the 0 -> 10 line
    (line,) = _finder(lows).lines_at(25, SWINGS[:3], retest_window=10)
    assert (line.i1, line.i2) == (10, 20)


def test_breakout_signal_on_trendline_break():
    # Falling highs at bars 5 and 18, then a strong green candle closes through the line.
    rows = [(10, 10.5, 9.5, 10)] * 40
    rows[5], rows[18] = (10, 14, 9.5, 10), (10, 12, 9.5, 10)          # line: 14 -> 12 over 13 bars
    rows[22] = (10, 11.8, 9.9, 11.7)                                    # line at ~11.38 on bar 22
    cfg = StrategyConfig(pivot_left=3, pivot_right=3, atr_period=3, trend_filter=False, min_rr=0.5)
    sig = Market(candles(rows), cfg).analyze(22).signal
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


class FakeDeriv:
    """Stands in for Deriv's WebSocket: serves 12 five-minute candles, newest last."""

    def __init__(self):
        self.requests = []
        self.epochs = [1_700_000_000 + 300 * i for i in range(12)]

    def send(self, message):
        import json
        self.requests.append(json.loads(message))

    def recv(self):
        import json
        req = self.requests[-1]
        end = self.epochs[-1] if req["end"] == "latest" else req["end"]
        rows = [e for e in self.epochs if e <= end][-req["count"]:]
        return json.dumps({"candles": [{"epoch": e, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
                                       for e in rows]})

    def close(self):
        pass


def test_deriv_loader_pages_back_and_drops_the_forming_candle():
    fake = FakeDeriv()
    now = fake.epochs[-1] + 120          # the last candle started 2 minutes ago: still forming
    df = data.load_deriv("V75", "5m", count=10, connect=lambda url: fake, now=now)
    assert fake.requests[0]["ticks_history"] == "R_75" and fake.requests[0]["granularity"] == 300
    assert len(fake.requests) == 1 and len(df) == 9
    assert df.time.is_monotonic_increasing and df.time.iloc[-1].timestamp() == fake.epochs[-2]


def test_deriv_loader_requests_older_pages_until_count(monkeypatch):
    fake = FakeDeriv()
    fake.epochs = [1_700_000_000 + 300 * i for i in range(7000)]
    df = data.load_deriv("XAUUSD", "5m", count=6000, connect=lambda url: fake, now=2e9)
    assert [r["count"] for r in fake.requests] == [5000, 1000]
    assert fake.requests[1]["end"] == fake.epochs[2000] - 1
    assert fake.requests[0]["ticks_history"] == "frxXAUUSD"
    assert len(df) == 6000 and df.time.is_unique


@pytest.mark.parametrize("name, symbol", [("V75", "R_75"), ("v100(1s)", "1HZ100V"), ("Volatility 25 Index", "R_25"),
                                          ("XAUUSD", "frxXAUUSD"), ("gbpjpy", "frxGBPJPY"), ("R_50", "R_50")])
def test_deriv_symbol_names(name, symbol):
    assert data.deriv_symbol(name) == symbol


def test_spread_is_charged_once_per_round_trip():
    df = data.volatility_index(n=3000, seed=4)
    free = run_backtest(df, StrategyConfig(min_confluence=1))
    costly = run_backtest(df, StrategyConfig(min_confluence=1), spread=50.0)
    assert free.trades and len(free.trades) == len(costly.trades)
    t0, t1 = free.trades[0], costly.trades[0]
    assert t1.pnl == pytest.approx(t0.pnl - 50.0 * t0.size)


def test_swings_after_a_break_do_not_count_as_touches():
    lows = _lows()
    lows[35] = 17.0                                   # sits exactly on the old line, but after it broke at 30
    swings = SWINGS + [Pivot(35, 17.0, "low", 38)]
    broken = [ln for ln in _finder(lows).lines_at(39, swings, retest_window=10) if ln.broken_at is not None]
    assert broken[0].touches == (0, 10, 20)
