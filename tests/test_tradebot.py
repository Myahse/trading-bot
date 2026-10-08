import numpy as np
import pandas as pd
import pytest

from tradebot import data
from tradebot.backtest import Order, _fill, run_backtest
from tradebot.strategy import Signal
from tradebot.orderblocks import find_order_blocks
from tradebot.fractal import HigherTimeframe
from tradebot.money import MoneyManagement, lots, quote_rate
from tradebot.strategy import Market, StrategyConfig, mode_config, mode_money
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
        if tr.exit_reason == "stop" and tr.partial_bar is None:   # a clean stop loses ~1%, never much more
            assert -0.0125 * 10_000 * 1.2 < tr.pnl < 0
        if tr.exit_reason == "stop" and tr.partial_bar is not None:  # half banked at +1R, half lost at -1R
            assert -1e-6 <= tr.r_multiple <= 0.5 + 1e-6
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
    mm = lambda: MoneyManagement(max_daily_loss=None)   # noqa: E731  (the daily limit depends on costs)
    free = run_backtest(df, StrategyConfig(min_confluence=1), mm())
    costly = run_backtest(df, StrategyConfig(min_confluence=1), mm(), spread=50.0)
    assert free.trades and len(free.trades) == len(costly.trades)
    t0, t1 = free.trades[0], costly.trades[0]
    assert t1.pnl == pytest.approx(t0.pnl - 50.0 * t0.size)


def test_swings_after_a_break_do_not_count_as_touches():
    lows = _lows()
    lows[35] = 17.0                                   # sits exactly on the old line, but after it broke at 30
    swings = SWINGS + [Pivot(35, 17.0, "low", 38)]
    broken = [ln for ln in _finder(lows).lines_at(39, swings, retest_window=10) if ln.broken_at is not None]
    assert broken[0].touches == (0, 10, 20)



# -- confirmation ----------------------------------------------------------------

def _order(confirmation, trigger=10.0, stop=8.0):
    return Order(Signal("long", "rejection", 0, trigger, stop, 14.0, [], trigger, confirmation), expires=3)


def _bars(*rows):
    o, h, l, c = (np.array(col, float) for col in zip(*rows))
    return o, h, l, c


def test_break_confirmation_fills_at_the_trigger_only_when_price_breaks_it():
    o, h, l, c = _bars((9.5, 9.9, 9.2, 9.6), (9.6, 10.4, 9.5, 10.2))
    order = _order("break")
    assert _fill(order, 0, o, h, l, c) is None           # high 9.9 < trigger 10: keep waiting
    assert _fill(order, 1, o, h, l, c) == 10.0           # broke it: buy-stop filled at 10


def test_break_confirmation_is_cancelled_if_the_stop_trades_first():
    o, h, l, c = _bars((9.5, 9.7, 7.9, 8.2))
    assert _fill(_order("break"), 0, o, h, l, c) == "cancel"


def test_close_confirmation_enters_on_the_next_open():
    o, h, l, c = _bars((9.5, 10.5, 9.4, 9.9), (9.9, 10.6, 9.8, 10.3), (10.4, 10.8, 10.2, 10.6))
    order = _order("close")
    assert _fill(order, 0, o, h, l, c) is None           # traded above 10 but closed below: not confirmed
    assert _fill(order, 1, o, h, l, c) is None and order.confirmed
    assert _fill(order, 2, o, h, l, c) == 10.4


def test_confirmation_never_enters_before_the_trigger():
    df = data.volatility_index(n=3000, seed=4)
    cfg = StrategyConfig(min_confluence=1, confirmation="break", confirm_bars=3)
    res = run_backtest(df, cfg)
    h, l = df.high.to_numpy(), df.low.to_numpy()
    assert res.trades
    for tr in res.trades:
        recent = slice(max(0, tr.entry_bar - 3), tr.entry_bar)   # the signal candle is one of these
        if tr.side == "long":
            assert tr.entry >= h[recent].min() - 1e-9
        else:
            assert tr.entry <= l[recent].max() + 1e-9


# -- money management ------------------------------------------------------------

def _trending(n=60, step=1.0):
    """A clean rally: each candle 1 point higher."""
    close = 100 + step * np.arange(n)
    return candles([(c - step, c + 0.2, c - step - 0.2, c) for c in close])


class OneLong(Market):
    """Market that signals one long on bar 20 (stop 2 points away, target far) and nothing else."""

    def analyze(self, t):
        an = super().analyze(t)
        an.signal = Signal("long", "rejection", t, self.c[t], self.c[t] - 2, self.c[t] + 100) if t == 20 else None
        return an


def test_breakeven_partial_and_trailing_stop():
    df = _trending()
    falls = df.copy()
    falls.loc[30:, ["open", "high", "low", "close"]] = falls.loc[29, "close"] - np.arange(1, 31)[:, None] * 0.7
    cfg = StrategyConfig(confirmation="none", trend_filter=False)
    mm = MoneyManagement(breakeven_r=1.0, partial_r=1.0, partial_pct=0.5, trail="atr", trail_atr=2.0,
                         max_daily_loss=None)
    res = run_backtest(falls, cfg, mm, market=OneLong(falls, cfg))
    (tr,) = res.trades
    assert tr.partial_bar is not None                     # half off at +1R
    assert tr.exit_reason.startswith("trailing stop")
    assert tr.exit > tr.entry + 2                         # trailed well past break-even before the drop
    assert tr.r_multiple > 1


def test_daily_loss_limit_stops_new_trades_for_the_day():
    df = data.volatility_index(n=4000, seed=6, bar_seconds=60)
    loose = run_backtest(df, StrategyConfig(min_confluence=1), MoneyManagement(max_daily_loss=None))
    tight = run_backtest(df, StrategyConfig(min_confluence=1), MoneyManagement(max_daily_loss=0.01))
    assert len(tight.trades) < len(loose.trades)


def test_max_trades_per_day():
    df = data.volatility_index(n=4000, seed=6, bar_seconds=60)
    res = run_backtest(df, StrategyConfig(min_confluence=1), MoneyManagement(max_trades_per_day=2,
                                                                              max_daily_loss=None))
    days = pd.Series([df.time[t.entry_bar].date() for t in res.trades])
    assert days.value_counts().max() <= 2


def test_lot_size():
    # 1% of 10,000 = $100 at risk; gold stop $5 away, 100 oz per lot -> $500 per lot -> 0.2 lots
    assert lots(10_000, 0.01, 2400.0, 2395.0, 100) == pytest.approx(0.2)
    assert quote_rate("frxUSDJPY", 150.0) == pytest.approx(1 / 150)
    assert quote_rate("frxGBPJPY", 190.0) is None and quote_rate("R_75", 1e5) == 1.0


# -- outlooks ----------------------------------------------------------------------

def test_outlook_levels_scenarios_and_report(tmp_path):
    from tradebot.outlook import build_outlook, to_markdown
    df = data.volatility_index(n=2000, bar_seconds=3600, seed=3)
    o = build_outlook(df, "V75", "day")
    assert o.above and o.below
    assert all(lv.mid > o.price for lv in o.above) and all(lv.mid < o.price for lv in o.below)
    assert [lv.mid for lv in o.above] == sorted(lv.mid for lv in o.above)
    assert o.pivots["S1"] < o.pivots["P"] < o.pivots["R1"]
    assert len(o.scenarios) == 2 and o.expected_range > 0
    md = to_markdown([o], "day")
    assert "## V75" in md and "Pivots" in md and "Scenarios" in md


def test_confluence_levels_do_not_chain_into_wide_bands():
    from tradebot.outlook import Level, _merge
    levels = [Level(p, p, [f"l{p}"]) for p in (100.0, 100.8, 101.6, 102.4, 103.2)]   # 0.8 apart
    merged = _merge(levels, gap=1.0)
    assert [len(lv.labels) for lv in merged] == [2, 2, 1]
    assert max(lv.high - lv.low for lv in merged) <= 1.0


@pytest.mark.parametrize("day, expected", [
    ("2026-10-11", [("week", ["XAUUSD", "V75"]), ("day", ["XAUUSD", "V75"])]),   # Sunday
    ("2026-10-07", [("day", ["XAUUSD", "V75"])]),                                  # Wednesday
    ("2026-10-09", [("day", ["V75"])]),                                            # Friday: no forex Saturday
    ("2026-10-10", [("day", ["V75"])]),                                            # Saturday
])
def test_schedule_publishes_weekly_on_sunday_and_skips_forex_weekends(day, expected):
    import datetime as dt
    from tradebot.__main__ import outlooks_due
    run = dt.datetime.fromisoformat(day + "T18:00")
    assert outlooks_due(run, ["XAUUSD", "V75"]) == expected


def test_outlook_command_writes_report_and_charts(tmp_path):
    from tradebot.__main__ import main
    main(["outlook", "--source", "sim", "--symbol", "V75,V25", "--horizon", "week", "--out", str(tmp_path)])
    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.startswith("outlook-week-") and f.endswith(".md") for f in files)
    for sym in ("V75", "V25"):   # a technical and a fundamental screenshot per market
        assert any(f.endswith(f"{sym}-technical.png") for f in files)
        assert any(f.endswith(f"{sym}-fundamental.png") for f in files)
    report = next(tmp_path.glob("outlook-week-*.md")).read_text()
    assert "### Technical" in report and "### Fundamental" in report and "Verdict" in report


def test_an_intact_twin_of_a_broken_line_is_dropped():
    lows = _lows()
    lows[30] = 15.85                              # swing low just under the 0-10-20 line (16.0 there)...
    closes = lows + 0.5
    closes[30] = 15.88                            # ...closing far enough below it to break it
    finder = TrendlineFinder(lows + 1, lows, closes, np.ones(len(lows)))
    lines = finder.lines_at(31, SWINGS[:3] + [Pivot(30, 15.85, "low", 31)], retest_window=10)
    # The 0 -> 30 line is intact but runs right beside the broken one: only the broken one is kept.
    assert [(ln.touches, ln.broken_at) for ln in lines] == [((0, 10, 20), 30)]



# -- fundamentals --------------------------------------------------------------------

import datetime as _dt

from tradebot import fundamentals as fund

NOW = _dt.datetime(2026, 10, 11, 18, 0, tzinfo=_dt.timezone.utc)   # a Sunday evening


def _calendar():
    return fund.parse_calendar([
        {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High",
         "forecast": "0.3%", "previous": "0.4%"},
        {"title": "GDP m/m", "country": "GBP", "date": "2026-10-12T02:00:00-04:00", "impact": "Medium"},
        {"title": "Retail Sales", "country": "AUD", "date": "2026-10-13T21:30:00-04:00", "impact": "High"},
        {"title": "Bank Holiday", "country": "JPY", "date": "2026-10-12T00:00:00-04:00", "impact": "Holiday"},
        {"title": "Last week", "country": "USD", "date": "2026-10-08T08:30:00-04:00", "impact": "High"},
    ])


def _fx(usd_move=0.01, gbp_move=0.0):
    """Ten days of USD values per currency; on the last 5 days the USD gains `usd_move`."""
    idx = pd.date_range("2026-10-01", periods=10, freq="D")
    values = pd.DataFrame({c: 1.0 for c in fund.MAJORS}, index=idx)
    for c in fund.MAJORS:
        if c != "USD":
            values.loc[idx[5:], c] = 1 / (1 + usd_move)
    values.loc[idx[5:], "GBP"] *= 1 + gbp_move
    return values


def _drivers(dxy=1.0, tnx=0.10, vix=-5.0):
    idx = pd.date_range("2026-09-01", periods=30, freq="D")
    d = pd.DataFrame({"DX-Y.NYB": 100.0, "^TNX": 4.0, "^VIX": 20.0}, index=idx)
    d.iloc[-5:, 0] = 100 * (1 + dxy / 100)
    d.iloc[-5:, 1] = 4.0 + tnx
    d.iloc[-5:, 2] = 20 * (1 + vix / 100)
    return d


def test_parse_calendar_converts_to_utc():
    cpi = _calendar()[-1]
    assert cpi.title == "CPI m/m" and cpi.when == _dt.datetime(2026, 10, 14, 12, 30, tzinfo=_dt.timezone.utc)


def test_weekly_calendar_keeps_relevant_high_and_medium_events():
    f = fund.analyse("frxGBPJPY", "week", NOW, _calendar(), None, None)
    assert [e.title for e in f.events] == ["GDP m/m"]                 # not AUD, not holidays, not last week
    f = fund.analyse("frxXAUUSD", "week", NOW, _calendar(), None, None)
    assert [e.title for e in f.events] == ["CPI m/m"] and "CPI" in f.warnings[0]


def test_gold_is_bearish_when_dollar_and_yields_rise_and_fear_falls():
    f = fund.analyse("frxXAUUSD", "week", NOW, [], _fx(usd_move=0.01), _drivers(dxy=1.0, tnx=0.10, vix=-10))
    assert f.bias == "bearish" and f.score < -0.75
    f = fund.analyse("frxXAUUSD", "week", NOW, [], _fx(usd_move=-0.01), _drivers(dxy=-1.0, tnx=-0.10, vix=10))
    assert f.bias == "bullish"


def test_pair_strength_rates_and_views():
    f = fund.analyse("frxGBPJPY", "week", NOW, [], _fx(gbp_move=0.01), None,
                     {"rates": {"GBP": 4.0, "JPY": 0.5}, "views": {"JPY": "dovish"}})
    assert f.bias == "bullish" and len(f.reasons) == 3


def test_synthetic_indices_have_no_fundamentals():
    f = fund.analyse("R_75", "week", NOW, _calendar(), _fx(), _drivers())
    assert not f.applicable and "random" in f.reasons[0]


def test_events_soon_flags_news_within_the_hour():
    cpi_time = _dt.datetime(2026, 10, 14, 12, 0, tzinfo=_dt.timezone.utc)
    assert [e.title for e in fund.events_soon(_calendar(), "frxXAUUSD", cpi_time)] == ["CPI m/m"]
    assert fund.events_soon(_calendar(), "R_75", cpi_time) == []


def test_verdict_flags_conflict_between_setup_and_fundamentals():
    from tradebot.screenshot import verdict
    df = data.volatility_index(n=4000, vol=0.75, bar_seconds=300, seed=3)
    m = Market(df, mode_config("scalp"))
    t = next(t for t in range(3000, 4000) if m.analyze(t).signal)
    an = m.analyze(t)
    against = fund.Fundamentals("frxXAUUSD", True, bias="bearish" if an.signal.side == "long" else "bullish")
    assert verdict(an, against)[1] == "Verdict: conflict"
    assert verdict(an, None)[1].startswith("Verdict: " + an.signal.side.upper())


def test_screenshots_render(tmp_path):
    from tradebot.screenshot import fundamental_screenshot, technical_screenshot
    df = data.volatility_index(n=4000, vol=0.75, bar_seconds=300, seed=3)
    m = Market(df.iloc[:3876], mode_config("scalp"))
    gold = fund.analyse("frxXAUUSD", "week", NOW, _calendar(), _fx(), _drivers())
    technical_screenshot(m, "V75", str(tmp_path / "t.png"), "5m", "1h", gold)
    fundamental_screenshot(gold, str(tmp_path / "g.png"), "week")
    fundamental_screenshot(fund.analyse("frxGBPJPY", "day", NOW, [], _fx(), None), str(tmp_path / "p.png"), "day")
    fundamental_screenshot(fund.analyse("R_75", "day", NOW, None, None, None), str(tmp_path / "s.png"), "day")
    assert all((tmp_path / n).stat().st_size > 10_000 for n in ("t.png", "g.png", "p.png", "s.png"))


# -- small accounts and real lot sizes -------------------------------------------------

from tradebot.money import position


def test_minimum_lot_too_risky_is_skipped():
    # $20, gold, $6.70 stop: 0.01 lot (1 oz) loses $6.70 = 33.5% of the account
    mm = MoneyManagement.for_symbol("frxXAUUSD", risk_per_trade=0.02)
    pos = position(mm, 20.0, 2400.0, 2393.3)
    assert pos.units == 0 and "33.5%" in pos.note


def test_minimum_lot_used_when_within_the_limit():
    # $20, GBPUSD, 5-pip stop: 0.01 lot loses $0.50 = 2.5%; wanted 1% -> forced up to the minimum lot
    mm = MoneyManagement.for_symbol("frxGBPUSD", risk_per_trade=0.01, max_leverage=500)
    pos = position(mm, 20.0, 1.3000, 1.2995)
    assert pos.lots == 0.01 and pos.risk == pytest.approx(0.025) and pos.note.startswith("minimum lot")


def test_twenty_dollars_at_30x_cannot_margin_the_minimum_forex_lot():
    # 0.01 lot GBPUSD = 1,000 GBP ~ $1,300 of exposure; $20 x 30 = $600
    pos = position(MoneyManagement.for_symbol("frxGBPUSD", max_leverage=30), 20.0, 1.3000, 1.2995)
    assert pos.units == 0 and "margin" in pos.note


def test_lots_round_down_and_respect_leverage():
    mm = MoneyManagement.for_symbol("frxGBPUSD", risk_per_trade=0.01, max_leverage=30)
    pos = position(mm, 10_000.0, 1.3000, 1.2990)        # $100 risk / ($10 per lot per 10 pips) = 1.0 lot
    assert pos.lots == pytest.approx(1.0)
    pos = position(mm, 10_000.0, 1.3000, 1.29999)       # tiny stop -> capped by 30x leverage = 2.3 lots
    assert pos.lots == pytest.approx(2.3) and "leverage" in pos.note


def test_usdjpy_pnl_is_in_dollars():
    mm = MoneyManagement.for_symbol("frxUSDJPY", risk_per_trade=0.01)
    pos = position(mm, 10_000.0, 150.0, 149.5)          # 50 pips; 1 lot = 100,000 USD -> $333 per lot
    assert pos.risk * 10_000 == pytest.approx(pos.lots * 100_000 * 0.5 / 150.0)
    assert pos.risk == pytest.approx(0.01, abs=0.001)


def test_cross_needs_a_quote_rate():
    with pytest.raises(ValueError, match="quote-rate"):
        position(MoneyManagement.for_symbol("frxGBPJPY"), 1000.0, 190.0, 189.5)
    mm = MoneyManagement.for_symbol("frxGBPJPY", quote_rate=1 / 150)
    assert position(mm, 1000.0, 190.0, 189.5).lots == pytest.approx(0.03)


def test_small_account_backtest_uses_whole_lots_and_skips_partials_it_cannot_split():
    df = data.volatility_index(n=4000, vol=0.08, bar_seconds=300, seed=5, start=1.30)   # GBPUSD-like prices
    mm = MoneyManagement.for_symbol("frxGBPUSD", risk_per_trade=0.02, partial_r=1.0, partial_pct=0.5,
                                    max_daily_loss=None, min_lot_max_risk=1.0, max_leverage=500)
    res = run_backtest(df, StrategyConfig(min_confluence=1), mm, initial_equity=20.0)
    assert res.trades
    for tr in res.trades:
        assert tr.lots is not None and round(tr.lots / 0.01, 6) == round(tr.lots / 0.01)
        if tr.lots < 0.02:
            assert tr.partial_bar is None      # 0.01 lot can't be halved
    assert res.stats["final_equity"] == pytest.approx(20.0 + sum(t.pnl for t in res.trades))


def test_gold_on_twenty_dollars_skips_every_setup():
    df = data.volatility_index(n=3000, vol=0.15, bar_seconds=300, seed=5, start=2400.0)
    mm = MoneyManagement.for_symbol("frxXAUUSD", risk_per_trade=0.05, max_daily_loss=None)
    res = run_backtest(df, StrategyConfig(min_confluence=1), mm, initial_equity=20.0)
    assert res.stats["trades"] == 0 and res.stats["skipped_min_lot"] > 0


# -- paper trading -------------------------------------------------------------------------

from tradebot.paper import PaperTrader


class FakeFeed:
    """Serves closed candles of a prepared series, revealing more as 'time' passes."""

    def __init__(self, df, revealed):
        self.df, self.revealed = df.reset_index(drop=True), revealed

    def __call__(self, symbol, interval, count):
        return self.df.iloc[max(0, self.revealed - count): self.revealed].reset_index(drop=True)

    def advance(self, n):
        self.revealed = min(len(self.df), self.revealed + n)


def _paper(folder, feed, veto=None, risk=0.01):
    return PaperTrader("V75", "5m", StrategyConfig(min_confluence=1), MoneyManagement(risk_per_trade=risk),
                       10_000.0, folder, feed, history=1500, screenshots=False, news_veto=veto, log=lambda m: None)


def _closed(trades):
    return [(t.side, t.entry_bar, t.exit_bar, round(t.pnl, 6), t.exit_reason) for t in trades if t.exit_bar is not None]


PAPER_DF = data.volatility_index(n=2600, seed=11)


def test_paper_trading_matches_the_backtest(tmp_path):
    feed = FakeFeed(PAPER_DF, 1500)
    trader = _paper(tmp_path, feed)
    trader.bootstrap()
    while feed.revealed < len(PAPER_DF):
        feed.advance(37)
        trader.poll()
    expected = run_backtest(PAPER_DF, StrategyConfig(min_confluence=1), MoneyManagement(risk_per_trade=0.01),
                            initial_equity=10_000.0, start=1500)
    assert _closed(trader.engine.trades) and _closed(trader.engine.trades) == _closed(expected.trades)
    assert all(t.entry_bar >= 1500 for t in trader.engine.trades)   # nothing traded in the warm-up history


def test_paper_trading_survives_a_restart(tmp_path):
    feed = FakeFeed(PAPER_DF, 1500)
    first = _paper(tmp_path / "a", feed)
    first.bootstrap()
    for _ in range(15):
        feed.advance(37)
        first.poll()
    second = _paper(tmp_path / "a", feed)                 # the program was restarted
    second.bootstrap()
    while feed.revealed < len(PAPER_DF):
        feed.advance(37)
        second.poll()

    feed2 = FakeFeed(PAPER_DF, 1500)
    straight = _paper(tmp_path / "b", feed2)
    straight.bootstrap()
    while feed2.revealed < len(PAPER_DF):
        feed2.advance(37)
        straight.poll()
    assert _closed(second.engine.trades) == _closed(straight.engine.trades)
    assert second.engine.cash == pytest.approx(straight.engine.cash)
    events = (tmp_path / "a" / "events.log").read_text().splitlines()
    assert len(events) == len(straight.engine.events)        # nothing logged twice after the restart


def test_paper_refuses_to_resume_with_different_settings(tmp_path):
    feed = FakeFeed(PAPER_DF, 1500)
    _paper(tmp_path, feed).bootstrap()
    with pytest.raises(SystemExit, match="different settings"):
        _paper(tmp_path, feed, risk=0.05).bootstrap()


def test_paper_news_veto_is_replayed_after_restart(tmp_path):
    feed = FakeFeed(PAPER_DF, 1500)
    calls = []

    def news(when, signal):           # pretend every setup in the first stretch is next to big news
        calls.append(when)
        return "high-impact news" if len(calls) <= 3 else None

    trader = _paper(tmp_path, feed, veto=news)
    trader.bootstrap()
    while feed.revealed < len(PAPER_DF):
        feed.advance(37)
        trader.poll()
    skipped = [e for e in trader.engine.events if e["event"] == "skipped"]
    assert len(skipped) == 3

    again = _paper(tmp_path, feed, veto=lambda when, s: pytest.fail("replay must not ask the news feed"))
    again.bootstrap()
    assert _closed(again.engine.trades) == _closed(trader.engine.trades)


def test_paper_writes_journal_files(tmp_path):
    feed = FakeFeed(PAPER_DF, 1500)
    trader = _paper(tmp_path, feed)
    trader.bootstrap()
    while feed.revealed < len(PAPER_DF):
        feed.advance(100)
        trader.poll()
    for name in ("candles.csv", "meta.json", "events.log", "trades.csv", "summary.md"):
        assert (tmp_path / name).exists()
    assert len(pd.read_csv(tmp_path / "candles.csv")) == len(PAPER_DF)
    summary = (tmp_path / "summary.md").read_text()
    assert "Balance" in summary and "Trades" in summary



# -- stable levels --------------------------------------------------------------------------

from tradebot.structure import LevelBook


def test_levels_change_only_when_a_swing_confirms():
    df = data.volatility_index(n=2000, seed=3)
    m = Market(df, StrategyConfig())
    confirmations = {p.confirmed_at for p in m.pivots}
    prev = None
    for t in range(300, 2000):
        z = m.levels.at(t)
        if prev is not None and z != prev:
            assert t in confirmations          # never just because another candle closed
        prev = z


def test_level_keeps_its_place_when_touched_again():
    atr = np.ones(100)
    swings = [Pivot(10, 100.0, "low", 13), Pivot(30, 100.2, "low", 33), Pivot(50, 100.1, "high", 53)]
    book = LevelBook(swings, atr, tol_atr=0.6)
    assert book.at(20) == []                    # one touch is not a zone yet
    (z1,) = book.at(40)
    (z2,) = book.at(60)
    assert (z1.touches, z2.touches) == (2, 3)
    assert z2.low == z1.low and z2.high == z1.high   # a third touch inside the zone doesn't move it
    assert z1.high - z1.low <= 0.6 + 1e-9


def test_far_swing_starts_a_new_level_instead_of_moving_the_old_one():
    atr = np.ones(100)
    swings = [Pivot(10, 100.0, "low", 13), Pivot(20, 100.1, "low", 23), Pivot(40, 105.0, "high", 43),
              Pivot(60, 105.2, "high", 63)]
    zones = sorted(LevelBook(swings, atr, tol_atr=0.6).at(70), key=lambda z: z.low)
    assert len(zones) == 2 and zones[0].high < 101 and zones[1].low > 104


def test_chart_draws_setup_and_trades_as_position_boxes(tmp_path):
    pytest.importorskip("matplotlib")
    from tradebot.screenshot import technical_screenshot
    cfg = StrategyConfig(min_confluence=1, htf=6)
    df = data.synthetic(n=600, seed=3)
    market = Market(df, cfg)
    res = run_backtest(df, cfg, market=market)
    path = tmp_path / "shot.png"
    technical_screenshot(market, "SYN", str(path), "1h", "6h", trades=res.trades)
    assert path.stat().st_size > 0


# -- surgical entries (refine) ------------------------------------------------------

from tradebot.refine import LowerTimeframe, choch, resample

M1 = data.volatility_index(n=30_000, bar_seconds=60, seed=4)
M5 = resample(M1, 300)
REFINE = mode_config("scalp", confirmation="refine", ltf="1m")


def test_resample_builds_higher_candles_from_lower_ones():
    assert len(M5) == len(M1) // 5
    first = M1.iloc[:5]
    assert M5.open.iloc[0] == first.open.iloc[0] and M5.close.iloc[0] == first.close.iloc[-1]
    assert M5.high.iloc[0] == first.high.max() and M5.low.iloc[0] == first.low.min()


def test_choch_enters_on_the_close_through_the_last_lower_high():
    # 1m: a drop into the zone with a lower high at 104 (index 4), a low of 98, then a close above 104
    highs = [108, 105, 103, 102, 104, 102, 100, 99, 99.5, 101, 103, 105.5]
    lows = [106, 103, 101, 100, 102, 100, 99, 98, 98.5, 99.5, 101, 102]
    closes = [106.5, 104, 102, 101, 103, 101, 99.5, 98.5, 99.2, 100.5, 102.5, 105]
    opens = closes
    n = len(highs)
    time = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    ltf_df = pd.DataFrame({"time": time, "open": opens, "high": highs, "low": lows, "close": closes, "volume": 0.0})
    ltf = LowerTimeframe(ltf_df, pd.Series(time[::4]), pivot=2, atr_period=3)
    hits = [e for j in range(n) if (e := choch(ltf, "long", j, 0.0, 0.0)) is not None]
    assert len(hits) == 1
    e = hits[0]
    assert e.index == n - 1 and e.price == 105 and e.swing == 104
    assert e.stop == 98   # lowest low since the swing high
    assert choch(ltf, "short", n - 1, 0.0, 0.0) is None


def test_refined_entries_have_tighter_stops_and_enough_reward():
    res = run_backtest(M5, REFINE, mode_money("scalp"), ltf=M1)
    refined = [t for t in res.trades if t.fill_ltf is not None]
    assert refined
    market = Market(M5, REFINE, M1)
    for t in refined:
        assert abs(t.target - t.entry) / abs(t.entry - t.stop) >= REFINE.min_rr - 1e-9
        assert abs(t.entry - t.stop) >= REFINE.refine_min_stop_atr * market.ltf.atr[t.fill_ltf] - 1e-9
        assert market.ltf.bar_of[t.fill_ltf] == t.entry_bar
        assert t.entry == market.ltf.c[t.fill_ltf]
    plain = run_backtest(M5, mode_config("scalp", confirmation="break"), mode_money("scalp"))
    assert np.mean([abs(t.entry - t.stop) for t in refined]) < np.mean([abs(t.entry - t.stop) for t in plain.trades])


def test_refined_entries_never_look_ahead():
    full = run_backtest(M5, REFINE, mode_money("scalp"), ltf=M1)
    cut = 4000
    part = run_backtest(M5.iloc[:cut], REFINE, mode_money("scalp"), ltf=M1)   # later 1m candles are dropped
    done = [(t.side, t.entry_bar, t.entry, t.stop, t.exit_bar, t.exit) for t in full.trades if t.exit_bar < cut - 1]
    assert done and done == [(t.side, t.entry_bar, t.entry, t.stop, t.exit_bar, t.exit)
                             for t in part.trades if t.exit_bar < cut - 1]


def test_refine_without_lower_timeframe_candles_falls_back_to_break_entries():
    res = run_backtest(M5, REFINE, mode_money("scalp"))
    assert res.trades and all(t.fill_ltf is None for t in res.trades)


class TimeFeed:
    """Serves each timeframe's candles that have closed by `now`."""

    def __init__(self, frames, now):
        self.frames, self.now = frames, now

    def __call__(self, symbol, interval, count):
        df, step = self.frames[interval], pd.Timedelta(seconds=data.DERIV_GRANULARITY[interval])
        return df[df.time + step <= self.now].tail(count).reset_index(drop=True)


def test_paper_trading_with_surgical_entries_matches_the_backtest(tmp_path, monkeypatch):
    import tradebot.paper as paper_mod
    monkeypatch.setattr(paper_mod.time, "sleep", lambda s: None)
    start = 3000
    feed = TimeFeed({"5m": M5, "1m": M1}, M5.time.iloc[start - 1] + pd.Timedelta(minutes=5))
    trader = PaperTrader("V75", "5m", REFINE, mode_money("scalp"), 10_000.0, tmp_path, feed, history=start,
                         screenshots=False, log=lambda m: None)
    trader.bootstrap()
    while feed.now < M5.time.iloc[-1] + pd.Timedelta(minutes=5):
        feed.now += pd.Timedelta(minutes=5 * 23)
        trader.poll()
    expected = run_backtest(M5, REFINE, mode_money("scalp"), initial_equity=10_000.0, start=start, ltf=M1)
    got = _closed(trader.engine.trades)
    assert got and any(t.fill_ltf is not None for t in trader.engine.trades)
    assert got == _closed(expected.trades)[:len(got)]


@pytest.mark.parametrize("mode", ["next", "htf", "runner"])
def test_further_targets_never_sit_closer_than_the_nearest_obstacle(mode):
    df = data.synthetic(n=900, seed=5)
    base = Market(df, StrategyConfig(min_confluence=1, htf=6, min_rr=0.1))
    far = Market(df, StrategyConfig(min_confluence=1, htf=6, min_rr=0.1, target=mode, runner_rr=8))
    compared = 0
    for t in range(100, 900):
        a, b = base.analyze(t).signal, far.analyze(t).signal
        if a is None or b is None or a.side != b.side:
            continue
        sign = 1 if a.side == "long" else -1
        assert sign * (b.target - a.target) >= -1e-9
        if mode == "runner":
            runner = df.close.iloc[t] + sign * 8 * abs(df.close.iloc[t] - b.stop)
            assert b.target == pytest.approx(max(runner, a.target) if sign > 0 else min(runner, a.target))
        compared += 1
    assert compared > 0


def test_runner_needs_room_before_the_first_obstacle():
    df = data.synthetic(n=900, seed=5)
    tight = Market(df, StrategyConfig(min_confluence=1, htf=6, min_rr=0.1, target="runner"))
    strict = Market(df, StrategyConfig(min_confluence=1, htf=6, min_rr=3.0, target="runner"))
    loose = sum(tight.analyze(t).signal is not None for t in range(100, 900))
    kept = sum(strict.analyze(t).signal is not None for t in range(100, 900))
    assert kept < loose   # a far target alone doesn't pass the reward:risk filter
