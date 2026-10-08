"""Check the MetaTrader 5 EA against the Python bot on the same candles.

`mql5/TradeBotParity.mq5` runs the EA's own analysis code (`TradeBotCore.mqh`) at every closed
candle of a chart and writes what it sees to a CSV, plus the chart's candles to `<name>_candles.csv`
(prices written exactly). This module runs the Python bot on those same candles and reports,
column by column, how often the two agree.

    python -m tradebot.parity TradeBot_parity_XAUUSD_M5.csv --mode scalp
"""

from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import pandas as pd

from .data import _normalize
from .strategy import Market, StrategyConfig, mode_config

BIAS = {"up": 1, "down": -1, "neutral": 0, None: 0}
ZONE_COLS = {"sup": "support", "res": "resistance", "hsup": "htf_support", "hres": "htf_resistance"}

# Columns compared, in report order. Prices are compared to a relative tolerance (MT5 stores them
# as doubles with 8 significant digits in the CSV).
EXACT = ["pivots", "bias", "htf_bias", "n_zones", "sup_touches", "res_touches", "n_htf_zones",
         "hsup_touches", "hres_touches", "n_lines", "n_htf_lines", "n_obs", "side", "setup"]
PRICES = ["atr", "sup_lo", "sup_hi", "res_lo", "res_hi", "hsup_lo", "hsup_hi", "hres_lo", "hres_hi",
          "stop", "target", "trigger"]
# What each group of columns depends on, so a report says which part of the port drifts.
GROUPS = {
    "ATR and swings": ["atr", "pivots", "bias"],
    "Entry-chart zones": ["n_zones", "sup_lo", "sup_hi", "sup_touches", "res_lo", "res_hi", "res_touches"],
    "Higher timeframe": ["htf_bias", "n_htf_zones", "hsup_lo", "hsup_hi", "hsup_touches", "hres_lo", "hres_hi",
                         "hres_touches"],
    "Trendlines": ["n_lines", "n_htf_lines"],
    "Order blocks": ["n_obs"],
    "Signals": ["side", "setup", "stop", "target", "trigger"],
}


def read_mt5(path: str) -> pd.DataFrame:
    out = pd.read_csv(path, keep_default_na=False, na_values=[""])
    out["time"] = pd.to_datetime(out["time"], format="%Y.%m.%d %H:%M", utc=True)
    for col in ("side", "setup"):
        out[col] = out[col].fillna("").astype(str)
    return out


def read_candles(path: str) -> pd.DataFrame:
    """The candles TradeBotParity.mq5 wrote, read back to the exact same bits (pandas' default
    float parser can be one bit off, and one bit can tip a swing into a different zone)."""
    raw = pd.read_csv(path, float_precision="round_trip")
    return _normalize(raw, pd.to_datetime(raw["time"], utc=True))


def python_rows(candles: pd.DataFrame, cfg: StrategyConfig, times) -> pd.DataFrame:
    """The parity columns as the Python bot sees them at each candle in `times`."""
    market = Market(candles, cfg)
    index = pd.Series(np.arange(len(candles)), index=pd.to_datetime(candles["time"], utc=True))
    rows = []
    for time in times:
        t = index.get(time)
        if t is None:
            continue
        an = market.analyze(int(t))
        row = {"time": time, "bars": int(t) + 1, "atr": an.atr, "pivots": len(market.pivots_known_at(int(t))),
               "bias": BIAS[an.bias], "htf_bias": BIAS[an.htf_bias],
               "n_zones": len(an.support) + len(an.resistance),
               "n_htf_zones": len(an.htf_support) + len(an.htf_resistance),
               "n_lines": sum(not ln.htf for ln in an.trendlines),
               "n_htf_lines": sum(ln.htf for ln in an.trendlines),
               "n_obs": len(an.order_blocks)}
        for prefix, attr in ZONE_COLS.items():
            zones = getattr(an, attr)
            z = zones[0] if zones else None
            row[f"{prefix}_lo"] = z.low if z else math.nan
            row[f"{prefix}_hi"] = z.high if z else math.nan
            row[f"{prefix}_touches"] = z.touches if z else math.nan
        s = an.signal
        row.update(side=s.side if s else "", setup=s.setup if s else "",
                   stop=s.stop if s else math.nan, target=s.target if s else math.nan,
                   trigger=(s.trigger or math.nan) if s else math.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def _same(a: pd.Series, b: pd.Series, price: bool, rtol: float) -> pd.Series:
    if price:
        a, b = a.astype(float), b.astype(float)
        both_nan = a.isna() & b.isna()
        return both_nan | np.isclose(a, b, rtol=rtol, atol=0)
    a = a.where(a.notna(), "").astype(str).str.replace(r"\.0$", "", regex=True)
    b = b.where(b.notna(), "").astype(str).str.replace(r"\.0$", "", regex=True)
    return a == b


def compare(mt5: pd.DataFrame, py: pd.DataFrame, rtol: float = 1e-6) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-column agreement (share of candles that match) and the merged rows with a match flag per column."""
    merged = mt5.merge(py, on="time", suffixes=("_mt5", "_py"))
    stats = []
    for col in EXACT + PRICES:
        ok = _same(merged[f"{col}_mt5"], merged[f"{col}_py"], col in PRICES, rtol)
        merged[f"{col}_ok"] = ok
        stats.append({"column": col, "agree": float(ok.mean()) if len(ok) else math.nan,
                      "mismatches": int((~ok).sum())})
    return pd.DataFrame(stats).set_index("column"), merged


def signal_overlap(merged: pd.DataFrame) -> dict:
    mt5 = merged["side_mt5"] != ""
    py = merged["side_py"] != ""
    same = mt5 & py & (merged["side_mt5"] == merged["side_py"]) & (merged["setup_mt5"] == merged["setup_py"])
    return {"mt5_signals": int(mt5.sum()), "python_signals": int(py.sum()), "same_signal": int(same.sum()),
            "mt5_only": int((mt5 & ~py).sum()), "python_only": int((py & ~mt5).sum())}


def report(stats: pd.DataFrame, merged: pd.DataFrame) -> str:
    lines = [f"{len(merged)} candles compared"]
    for group, cols in GROUPS.items():
        worst = stats.loc[cols, "agree"].min()
        detail = ", ".join(f"{c} {stats.loc[c, 'agree']:.1%}" for c in cols if stats.loc[c, "agree"] < 1)
        lines.append(f"  {group:<18} {'match' if worst == 1 else f'{worst:.1%} worst'}"
                     + (f"  ({detail})" if detail else ""))
    ov = signal_overlap(merged)
    lines.append(f"  Setups: MT5 {ov['mt5_signals']}, Python {ov['python_signals']}, identical {ov['same_signal']}, "
                 f"MT5 only {ov['mt5_only']}, Python only {ov['python_only']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mt5_csv", help="file written by TradeBotParity.mq5")
    p.add_argument("candles_csv", nargs="?", help="the chart's candles (default: <mt5_csv>_candles.csv)")
    p.add_argument("--mode", choices=["scalp", "swing"], default="scalp")
    p.add_argument("--show", type=int, default=5, help="print the first N differing candles per group")
    args = p.parse_args(argv)

    mt5 = read_mt5(args.mt5_csv)
    py = python_rows(read_candles(args.candles_csv or args.mt5_csv.replace(".csv", "_candles.csv")),
                     mode_config(args.mode), mt5["time"])
    stats, merged = compare(mt5, py)
    print(report(stats, merged))
    for group, cols in GROUPS.items():
        bad = merged[~merged[[f"{c}_ok" for c in cols]].all(axis=1)]
        if len(bad) and args.show:
            print(f"\n{group}: first differences")
            show = [f"{c}_{s}" for c in cols for s in ("mt5", "py") if not merged[f"{c}_ok"].all()]
            print(bad[["time"] + show].head(args.show).to_string(index=False))
    return 0 if (stats["agree"] == 1).all() else 1


if __name__ == "__main__":
    sys.exit(main())
