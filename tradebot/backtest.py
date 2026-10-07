"""Bar-by-bar backtest: signal on bar close, fill at next open, stop/target intrabar.

Costs: `spread` in price units (what you see in MT5 / Deriv, e.g. 0.35 on XAUUSD) per
round trip, and/or `fee_bps` per side as a fraction of price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy import Market, Signal, StrategyConfig


@dataclass
class Trade:
    side: str
    setup: str
    entry_bar: int
    entry: float
    stop: float
    target: float
    size: float
    reasons: list[str]
    exit_bar: int | None = None
    exit: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0

    @property
    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop) * self.size
        return self.pnl / risk if risk else 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    initial_equity: float
    stats: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{**t.__dict__, "reasons": "; ".join(t.reasons), "r": round(t.r_multiple, 2)}
                             for t in self.trades])


def run_backtest(df: pd.DataFrame, cfg: StrategyConfig | None = None, initial_equity: float = 10_000.0,
                 risk_per_trade: float = 0.01, fee_bps: float = 0.0, spread: float = 0.0, max_leverage: float = 1.0,
                 market: Market | None = None) -> BacktestResult:
    market = market or Market(df, cfg)
    o, h, l, c = market.o, market.h, market.l, market.c
    fee = fee_bps / 10_000
    cash = initial_equity
    trades: list[Trade] = []
    equity = np.empty(len(c))
    pos: Trade | None = None
    pending: Signal | None = None
    cooldown_until = -1   # stand aside after a loss so one level isn't traded over and over

    def close(trade: Trade, bar: int, price: float, reason: str) -> None:
        nonlocal cash
        sign = 1 if trade.side == "long" else -1
        trade.exit_bar, trade.exit, trade.exit_reason = bar, price, reason
        cost = fee * (trade.entry + price) + spread   # spread: price units, paid once per round trip
        trade.pnl = (sign * (price - trade.entry) - cost) * trade.size
        cash += trade.pnl

    for t in range(len(c)):
        if pos is None and pending is not None:
            pos = _open(pending, t, o[t], cash, risk_per_trade, max_leverage)
            pending = None
            if pos is not None:
                trades.append(pos)

        if pos is not None:
            long = pos.side == "long"
            if t > pos.entry_bar and (o[t] <= pos.stop if long else o[t] >= pos.stop):
                close(pos, t, o[t], "stop (gap)")
            elif (l[t] <= pos.stop) if long else (h[t] >= pos.stop):  # stop first: conservative
                close(pos, t, pos.stop, "stop")
            elif (h[t] >= pos.target) if long else (l[t] <= pos.target):
                close(pos, t, pos.target, "target")
            if pos.exit_bar is not None:
                if pos.pnl < 0:
                    cooldown_until = t + market.cfg.cooldown_bars
                pos = None

        if pos is None and pending is None and t > cooldown_until and t < len(c) - 1:
            pending = market.analyze(t).signal

        open_pnl = 0.0
        if pos is not None:
            open_pnl = (1 if pos.side == "long" else -1) * (c[t] - pos.entry) * pos.size
        equity[t] = cash + open_pnl

    if pos is not None:
        close(pos, len(c) - 1, c[-1], "end of data")
        equity[-1] = cash

    index = market.df["time"] if "time" in market.df.columns else market.df.index
    result = BacktestResult(trades, pd.Series(equity, index=index), initial_equity)
    result.stats = _stats(result)
    return result


def _open(sig: Signal, t: int, price: float, equity: float, risk: float, leverage: float) -> Trade | None:
    long = sig.side == "long"
    # Gapped past the stop or target before we could get in: skip the trade.
    if (price <= sig.stop or price >= sig.target) if long else (price >= sig.stop or price <= sig.target):
        return None
    per_unit = abs(price - sig.stop)
    size = min(equity * risk / per_unit, equity * leverage / price)
    return Trade(sig.side, sig.setup, t, float(price), sig.stop, sig.target, size, sig.reasons)


def _stats(res: BacktestResult) -> dict:
    pnl = np.array([t.pnl for t in res.trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = res.equity.to_numpy()
    peak = np.maximum.accumulate(eq)
    return {
        "trades": len(pnl),
        "win_rate": float(len(wins) / len(pnl)) if len(pnl) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf") if len(wins) else 0.0,
        "avg_r": float(np.mean([t.r_multiple for t in res.trades])) if res.trades else 0.0,
        "total_return": float(eq[-1] / res.initial_equity - 1),
        "max_drawdown": float(((eq - peak) / peak).min()),
        "final_equity": float(eq[-1]),
    }
