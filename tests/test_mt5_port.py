"""The MetaTrader 5 EA (mql5/) must make the same decisions as the Python bot.

The parity fixture was produced inside MetaTrader 5 (build 6246) by mql5/TradeBotParity.mq5, which
runs the EA's own analysis code (TradeBotCore.mqh) on every candle of a GBPUSD M5 chart and saves
those candles too. If a change to the Python strategy breaks one of these tests, make the same
change in TradeBotCore.mqh, then regenerate the fixture in MT5 (see mql5/README.md).
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradebot import data
from tradebot.orderblocks import find_order_blocks
from tradebot.parity import EXACT, PRICES, compare, python_rows, read_candles, read_mt5, signal_overlap
from tradebot.strategy import MODES, StrategyConfig, mode_config
from tradebot.structure import TrendlineFinder, atr
from tradebot.zoneplan import SAME_PLACE_ATR

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "data" / "mt5_parity_GBPUSD_M5.csv.gz"
CORE = (ROOT / "mql5" / "TradeBotCore.mqh").read_text()


@pytest.fixture(scope="module")
def parity():
    mt5 = read_mt5(str(FIXTURE))
    candles = read_candles(str(FIXTURE).replace(".csv.gz", "_candles.csv.gz"))
    return compare(mt5, python_rows(candles, mode_config("scalp"), mt5["time"]))


def test_ea_sees_the_same_market_as_python(parity):
    stats, merged = parity
    assert len(merged) == 2500
    # Higher-timeframe trendlines may differ on a few candles: Python sizes their minimum length and
    # retest window from the average chart candles per HTF candle over the whole download, the EA
    # from the candles seen so far. Fake breaks of those lines follow them.
    near = ["n_htf_lines", "n_fakes", "fake_side", "fake_level"]
    exact = stats.drop(index=near)
    assert exact["agree"].eq(1.0).all(), exact[exact["agree"] < 1]
    assert (stats.loc[near, "agree"] >= 0.99).all(), stats.loc[near]


def test_ea_takes_the_same_setups_as_python(parity):
    _, merged = parity
    ov = signal_overlap(merged)
    assert ov["mt5_signals"] > 5
    assert ov["mt5_only"] == ov["python_only"] == 0
    assert ov["same_signal"] == ov["mt5_signals"]


def test_parity_report_columns_cover_the_dump():
    header = pd.read_csv(FIXTURE, nrows=0).columns
    assert set(EXACT + PRICES) <= set(header)


def test_ea_atr_is_bit_identical_to_pandas():
    """CalcATR in TradeBotCore.mqh, written out in Python: zones are built from the ATR, and a
    last-bit difference can tip a swing into a different zone."""
    df = data.synthetic(3000, seed=11)
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    alpha = 1.0 / 14
    keep = 1.0 - alpha
    w, ea = 0.0, np.full(len(c), np.nan)
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i == 0:
            w = tr
        elif w != tr:
            w = (keep * w + alpha * tr) / (keep + alpha)
        if i >= 13:
            ea[i] = w
    py = atr(df).to_numpy()
    assert np.array_equal(ea, py, equal_nan=True)
    assert "w = (keep * w + alpha * tr) / (keep + alpha);" in CORE


def _preset(name: str) -> dict[str, float]:
    block = CORE[CORE.index(f"if(preset == PRESET_{name.upper()})"):]
    block = block[:block.index("}")]
    out = {}
    for key, value in re.findall(r"C\.(\w+)\s*=\s*([\w.]+);", block):
        out[key] = {"true": 1.0, "false": 0.0}.get(value, value)
        try:
            out[key] = float(out[key])
        except ValueError:
            pass
    return out


@pytest.mark.parametrize("mode", ["scalp", "swing"])
def test_ea_presets_match_python_modes(mode):
    ea, cfg, money = _preset(mode), MODES[mode]["config"], MODES[mode]["money"]
    full = StrategyConfig(**cfg)
    assert ea["htf"] == {"1h": "PERIOD_H1", "D": "PERIOD_D1"}[cfg["htf"]]
    pairs = {
        "pivot": full.pivot_left, "htfPivot": full.htf_pivot, "minConfluence": full.min_confluence,
        "confirmBars": full.confirm_bars, "retest": full.retest_window, "cooldown": full.cooldown_bars,
        "obMaxAge": full.ob_max_age, "zoneLookback": full.zone_lookback_pivots, "minRR": full.min_rr,
        "defaultRR": full.default_rr, "minStopATR": full.min_stop_atr, "trendFilter": float(full.trend_filter),
        "breakouts": float(full.breakouts),
        "zoneBest": float(full.zone_view == "best"),
        "riskPct": money["risk_per_trade"] * 100, "beR": money["breakeven_r"], "partialR": money["partial_r"],
        "partialPct": money["partial_pct"] * 100, "trailStartR": money["trail_start_r"],
        "minLotMaxRisk": money["min_lot_max_risk"] * 100,
        "maxDailyLoss": (money.get("max_daily_loss") or 0) * 100, "maxTradesDay": money.get("max_trades_per_day") or 0,
    }
    for key, want in pairs.items():
        assert ea[key] == pytest.approx(want), key
    assert ea["confirm"] == f"CONFIRM_{full.confirmation.upper()}"
    assert ea["trail"] == f"TRAIL_{money['trail'].upper()}"
    if money["trail"] == "atr":
        assert ea["trailATR"] == pytest.approx(money["trail_atr"])


def test_ea_fixed_rules_match_python_defaults():
    defines = dict(re.findall(r"#define\s+(\w+)\s+([\d.]+)", CORE))
    cfg = StrategyConfig()
    finder = TrendlineFinder.__init__.__kwdefaults__
    pairs = {
        "ATR_PERIOD": cfg.atr_period, "ZONE_TOL_ATR": cfg.zone_tolerance_atr,
        "HTF_ZONE_TOL_ATR": cfg.htf_zone_tolerance_atr, "TOUCH_BUF_ATR": cfg.touch_buffer_atr,
        "STOP_BUF_ATR": cfg.stop_buffer_atr, "OB_DISP_ATR": cfg.ob_displacement_atr,
        "BREAKOUT_BODY": cfg.breakout_body, "OB_LOOKBACK": find_order_blocks.__defaults__[-1], "LINE_CANDIDATES": finder["candidates"],
        "LINE_TOUCH_ATR": finder["touch_atr"], "LINE_WICK_ATR": finder["wick_atr"],
        "LINE_BREAK_ATR": finder["break_atr"], "LINE_MAX_SLOPE_ATR": finder["max_slope_atr"],
        "SAME_PLACE_ATR": SAME_PLACE_ATR,
    }
    for name, want in pairs.items():
        assert float(defines[name]) == pytest.approx(want), name
