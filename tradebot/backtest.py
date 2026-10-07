"""Bar-by-bar backtest with entry confirmation and money management.

Each bar, in this order:
  1. A pending order is filled, waits or is cancelled (see `StrategyConfig.confirmation`).
  2. An open position is checked against its stop (first: conservative), its partial
     take-profit level and its target, using the bar's high and low.
  3. At the bar's close the stop is moved: to break-even, then trailed. A moved stop only
     applies from the next bar, so the backtest never uses the order of prices inside a bar.
  4. If flat and allowed (cooldown, daily loss / trade limits), the bar is analysed for a new setup.

Costs: `spread` in price units (as shown in MT5 / Deriv, e.g. 0.35 on XAUUSD) per round
trip, and/or `fee_bps` per side as a fraction of price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .money import MoneyManagement
from .strategy import Market, Signal, StrategyConfig


@dataclass
class Trade:
    side: str
    setup: str
    entry_bar: int
    entry: float
    stop: float            # initial stop: defines 1R
    target: float
    size: float
    reasons: list[str]
    current_stop: float = float("nan")
    open_size: float = 0.0
    best: float = float("nan")      # most favourable price reached so far
    stop_kind: str = "stop"         # what the current stop is: stop | breakeven | trailing stop
    partial_bar: int | None = None
    exit_bar: int | None = None
    exit: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0

    def __post_init__(self):
        self.current_stop, self.open_size, self.best = self.stop, self.size, self.entry

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop) * self.size
        return self.pnl / risk if risk else 0.0


@dataclass
class Order:
    signal: Signal
    expires: int
    confirmed: bool = False   # "close" confirmation seen; fill at the next open


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    initial_equity: float
    stats: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        cols = ["side", "setup", "entry_bar", "entry", "stop", "target", "size", "current_stop", "partial_bar",
                "exit_bar", "exit", "exit_reason", "pnl"]
        return pd.DataFrame([{**{k: getattr(t, k) for k in cols}, "r": round(t.r_multiple, 2),
                              "reasons": "; ".join(t.reasons)} for t in self.trades])


def run_backtest(df: pd.DataFrame, cfg: StrategyConfig | None = None, money: MoneyManagement | None = None,
                 initial_equity: float = 10_000.0, fee_bps: float = 0.0, spread: float = 0.0,
                 market: Market | None = None, risk_per_trade: float | None = None,
                 max_leverage: float | None = None) -> BacktestResult:
    market = market or Market(df, cfg)
    mm = money or MoneyManagement()
    if risk_per_trade is not None:
        mm.risk_per_trade = risk_per_trade
    if max_leverage is not None:
        mm.max_leverage = max_leverage
    o, h, l, c, atr = market.o, market.h, market.l, market.c, market.atr
    n, fee = len(c), fee_bps / 10_000
    days = pd.to_datetime(market.df["time"], utc=True).dt.date.to_numpy() if "time" in market.df.columns \
        else np.arange(n)   # without timestamps every bar is its own "day": daily limits are off

    cash = initial_equity
    trades: list[Trade] = []
    equity = np.empty(n)
    pos: Trade | None = None
    order: Order | None = None
    cooldown_until = -1
    day, day_start_cash, trades_today = None, cash, 0

    def realise(trade: Trade, bar: int, price: float, qty: float) -> None:
        nonlocal cash
        cost = fee * (trade.entry + price) + spread   # per unit, per round trip
        pnl = (trade.sign * (price - trade.entry) - cost) * qty
        trade.pnl += pnl
        trade.open_size -= qty
        cash += pnl

    def close_all(trade: Trade, bar: int, price: float, reason: str) -> None:
        realise(trade, bar, price, trade.open_size)
        trade.exit_bar, trade.exit, trade.exit_reason = bar, price, reason

    for t in range(n):
        if days[t] != day:
            day, day_start_cash, trades_today = days[t], cash, 0

        # 1. pending order
        if pos is None and order is not None:
            fill = _fill(order, t, o, h, l, c)
            if fill == "cancel" or (fill is None and t >= order.expires and not order.confirmed):
                order = None
            elif isinstance(fill, float):
                pos = _open(order.signal, t, fill, cash, mm)
                order = None
                if pos is not None:
                    trades.append(pos)
                    trades_today += 1

        # 2. stop / partial / target
        if pos is not None:
            long = pos.side == "long"
            stop, risk = pos.current_stop, abs(pos.entry - pos.stop)
            if t > pos.entry_bar and ((o[t] <= stop) if long else (o[t] >= stop)):
                close_all(pos, t, o[t], f"{pos.stop_kind} (gap)")
            elif (l[t] <= stop) if long else (h[t] >= stop):
                close_all(pos, t, stop, pos.stop_kind)
            else:
                if mm.partial_r is not None and mm.partial_pct > 0 and pos.partial_bar is None:
                    level = pos.entry + pos.sign * mm.partial_r * risk
                    if (h[t] >= level) if long else (l[t] <= level):
                        realise(pos, t, level, pos.size * mm.partial_pct)
                        pos.partial_bar = t
                if (h[t] >= pos.target) if long else (l[t] <= pos.target):
                    close_all(pos, t, pos.target, "target")
            if pos.exit_bar is not None:
                if pos.pnl < 0:
                    cooldown_until = t + market.cfg.cooldown_bars
                pos = None

        # 3. move the stop at the close
        if pos is not None:
            _manage_stop(pos, t, h, l, c, atr, market, mm)

        # 4. look for a new setup
        allowed = (mm.max_daily_loss is None or cash > day_start_cash * (1 - mm.max_daily_loss)) and \
                  (mm.max_trades_per_day is None or trades_today < mm.max_trades_per_day)
        if pos is None and order is None and allowed and t > cooldown_until and t < n - 1:
            sig = market.analyze(t).signal
            if sig is not None:
                order = Order(sig, t + (1 if sig.trigger is None else market.cfg.confirm_bars))

        equity[t] = cash + (pos.sign * (c[t] - pos.entry) * pos.open_size if pos is not None else 0.0)

    if pos is not None:
        close_all(pos, n - 1, c[-1], "end of data")
        equity[-1] = cash

    index = market.df["time"] if "time" in market.df.columns else market.df.index
    result = BacktestResult(trades, pd.Series(equity, index=index), initial_equity)
    result.stats = _stats(result)
    return result


def _fill(order: Order, t: int, o, h, l, c) -> float | str | None:
    """Fill price, "cancel", or None (keep waiting)."""
    sig, long = order.signal, order.signal.side == "long"
    if sig.trigger is None or order.confirmed:          # market order at this bar's open
        return float(o[t])
    if (l[t] <= sig.stop) if long else (h[t] >= sig.stop):
        # Stop level traded before the setup confirmed: the idea is dead. (If the trigger traded in
        # the same bar too we can't know which came first, so skip it - the cautious reading.)
        return "cancel"
    if sig.confirmation == "break":
        if (o[t] >= sig.trigger) if long else (o[t] <= sig.trigger):
            return float(o[t])                           # gapped through the trigger
        if (h[t] >= sig.trigger) if long else (l[t] <= sig.trigger):
            return float(sig.trigger)
        return None
    if (c[t] > sig.trigger) if long else (c[t] < sig.trigger):   # "close" confirmation
        order.confirmed = True
        order.expires = t + 1
    return None


def _open(sig: Signal, t: int, price: float, equity: float, mm: MoneyManagement) -> Trade | None:
    long = sig.side == "long"
    if (price <= sig.stop or price >= sig.target) if long else (price >= sig.stop or price <= sig.target):
        return None
    size = min(equity * mm.risk_per_trade / abs(price - sig.stop), equity * mm.max_leverage / price)
    return Trade(sig.side, sig.setup, t, float(price), sig.stop, sig.target, size, sig.reasons)


def _manage_stop(pos: Trade, t: int, h, l, c, atr, market: Market, mm: MoneyManagement) -> None:
    long, risk = pos.side == "long", abs(pos.entry - pos.stop)
    pos.best = max(pos.best, h[t]) if long else min(pos.best, l[t])
    progress = pos.sign * (pos.best - pos.entry) / risk     # how many R price has gone in our favour

    def tighter(level: float) -> bool:
        return (level > pos.current_stop and level < c[t]) if long else (level < pos.current_stop and level > c[t])

    if mm.breakeven_r is not None and progress >= mm.breakeven_r and tighter(pos.entry):
        pos.current_stop, pos.stop_kind = pos.entry, "breakeven"
    if mm.trail != "none" and progress >= mm.trail_start_r and not np.isnan(atr[t]):
        if mm.trail == "atr":
            level = pos.best - pos.sign * mm.trail_atr * atr[t]
        else:  # behind the latest confirmed swing low (long) / high (short)
            kind = "low" if long else "high"
            swings = [p for p in market.pivots_known_at(t) if p.kind == kind and p.index >= pos.entry_bar]
            if not swings:
                return
            level = swings[-1].price - pos.sign * market.cfg.stop_buffer_atr * atr[t]
        if tighter(level):
            pos.current_stop, pos.stop_kind = level, "trailing stop"


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
        "exits": pd.Series([t.exit_reason for t in res.trades]).value_counts().to_dict() if res.trades else {},
    }
