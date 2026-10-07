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

from .money import MoneyManagement, position
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
    rate: float = 1.0               # USD per unit of quote currency (P&L is in USD)
    lots: float | None = None
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
        risk = abs(self.entry - self.stop) * self.size * self.rate
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
        cols = ["side", "setup", "entry_bar", "entry", "stop", "target", "size", "lots", "current_stop", "partial_bar",
                "exit_bar", "exit", "exit_reason", "pnl"]
        return pd.DataFrame([{**{k: getattr(t, k) for k in cols}, "r": round(t.r_multiple, 2),
                              "reasons": "; ".join(t.reasons)} for t in self.trades])


class Engine:
    """The trading rules, one candle at a time. The backtest and paper trading both drive it,
    so a forward test can never behave differently from the backtest.

    `step(t)` processes candle t (see the module docstring for the order) and appends what
    happened to `events`. The market can be replaced by a longer one between steps (paper
    trading appends candles as they close): indices of past candles must not change.
    """

    def __init__(self, market: Market, money: MoneyManagement, equity: float, fee_bps: float = 0.0,
                 spread: float = 0.0, start: int = 0):
        self.market, self.mm = market, money
        self.fee, self.spread = fee_bps / 10_000, spread
        self.start = start                  # no new setups before this candle (paper trading warm-up)
        self.cash = equity
        self.trades: list[Trade] = []
        self.pos: Trade | None = None
        self.order: Order | None = None
        self.cooldown_until = -1
        self.day, self.day_start_cash, self.trades_today = None, equity, 0
        self.skipped: list[str] = []
        self.events: list[dict] = []
        self.veto = None   # optional (bar, signal) -> reason to skip a new setup, e.g. high-impact news

    def _event(self, t: int, kind: str, **info) -> None:
        self.events.append({"bar": t, "event": kind, **info})

    def _realise(self, trade: Trade, price: float, qty: float) -> float:
        cost = self.fee * (trade.entry + price) + self.spread   # per unit, per round trip, in quote currency
        pnl = (trade.sign * (price - trade.entry) - cost) * qty * trade.rate
        trade.pnl += pnl
        trade.open_size -= qty
        self.cash += pnl
        return pnl

    def close_all(self, t: int, price: float, reason: str) -> None:
        trade = self.pos
        self._realise(trade, price, trade.open_size)
        trade.exit_bar, trade.exit, trade.exit_reason = t, price, reason
        self._event(t, "closed", side=trade.side, price=price, reason=reason, pnl=trade.pnl,
                    r=trade.r_multiple, balance=self.cash)
        if trade.pnl < 0:
            self.cooldown_until = t + self.market.cfg.cooldown_bars
        self.pos = None

    def day_of(self, t: int):
        df = self.market.df
        return pd.Timestamp(df["time"].iloc[t]).date() if "time" in df.columns else t

    def equity_at(self, t: int) -> float:
        p = self.pos
        return self.cash + (p.sign * (self.market.c[t] - p.entry) * p.open_size * p.rate if p is not None else 0.0)

    def step(self, t: int, allow_last: bool = False) -> None:
        m, mm = self.market, self.mm
        o, h, l, c, atr = m.o, m.h, m.l, m.c, m.atr
        day = self.day_of(t)
        if day != self.day:
            self.day, self.day_start_cash, self.trades_today = day, self.cash, 0

        # 1. pending order
        if self.pos is None and self.order is not None:
            order = self.order
            fill = _fill(order, t, o, h, l, c)
            if fill == "cancel" or (fill is None and t >= order.expires and not order.confirmed):
                self.order = None
                self._event(t, "order cancelled", side=order.signal.side,
                            reason="stop level traded first" if fill == "cancel" else "not confirmed in time")
            elif isinstance(fill, float):
                pos, why = _open(order.signal, t, fill, self.cash, mm)
                self.order = None
                if why:
                    self.skipped.append(why)
                if pos is None:
                    self._event(t, "skipped", side=order.signal.side, reason=why or "price already beyond stop/target")
                else:
                    self.pos = pos
                    self.trades.append(pos)
                    self.trades_today += 1
                    self._event(t, "filled", side=pos.side, price=pos.entry, stop=pos.stop, target=pos.target,
                                lots=pos.lots, units=pos.size, note=why)

        # 2. stop / partial / target
        if self.pos is not None:
            pos = self.pos
            long = pos.side == "long"
            stop, risk = pos.current_stop, abs(pos.entry - pos.stop)
            if t > pos.entry_bar and ((o[t] <= stop) if long else (o[t] >= stop)):
                self.close_all(t, o[t], f"{pos.stop_kind} (gap)")
            elif (l[t] <= stop) if long else (h[t] >= stop):
                self.close_all(t, stop, pos.stop_kind)
            else:
                if mm.partial_r is not None and mm.partial_pct > 0 and pos.partial_bar is None:
                    level = pos.entry + pos.sign * mm.partial_r * risk
                    qty = _partial_qty(pos, mm)
                    if qty > 0 and ((h[t] >= level) if long else (l[t] <= level)):
                        pnl = self._realise(pos, level, qty)
                        pos.partial_bar = t
                        self._event(t, "partial", side=pos.side, price=level, pnl=pnl, balance=self.cash)
                if (h[t] >= pos.target) if long else (l[t] <= pos.target):
                    self.close_all(t, pos.target, "target")

        # 3. move the stop at the close
        if self.pos is not None:
            before = (self.pos.current_stop, self.pos.stop_kind)
            _manage_stop(self.pos, t, h, l, c, atr, m, mm)
            if (self.pos.current_stop, self.pos.stop_kind) != before:
                self._event(t, "stop moved", side=self.pos.side, stop=self.pos.current_stop, stop_kind=self.pos.stop_kind)

        # 4. look for a new setup
        allowed = (mm.max_daily_loss is None or self.cash > self.day_start_cash * (1 - mm.max_daily_loss)) and \
                  (mm.max_trades_per_day is None or self.trades_today < mm.max_trades_per_day)
        last_ok = allow_last or t < len(c) - 1
        if self.pos is None and self.order is None and allowed and t > self.cooldown_until and t >= self.start \
                and last_ok:
            sig = m.analyze(t).signal
            why = self.veto(t, sig) if sig is not None and self.veto is not None else None
            if why:
                self._event(t, "skipped", side=sig.side, reason=why)
            elif sig is not None:
                self.order = Order(sig, t + (1 if sig.trigger is None else m.cfg.confirm_bars))
                self._event(t, "order placed", side=sig.side, setup=sig.setup, trigger=sig.trigger,
                            entry=sig.entry, stop=sig.stop, target=sig.target, rr=sig.rr, reasons=sig.reasons)


def run_backtest(df: pd.DataFrame, cfg: StrategyConfig | None = None, money: MoneyManagement | None = None,
                 initial_equity: float = 10_000.0, fee_bps: float = 0.0, spread: float = 0.0,
                 market: Market | None = None, risk_per_trade: float | None = None,
                 max_leverage: float | None = None, start: int = 0) -> BacktestResult:
    market = market or Market(df, cfg)
    mm = money or MoneyManagement()
    if risk_per_trade is not None:
        mm.risk_per_trade = risk_per_trade
    if max_leverage is not None:
        mm.max_leverage = max_leverage
    n = len(market.c)
    engine = Engine(market, mm, initial_equity, fee_bps, spread, start)
    equity = np.empty(n)
    for t in range(n):
        engine.step(t)
        equity[t] = engine.equity_at(t)
    if engine.pos is not None:
        engine.close_all(n - 1, market.c[-1], "end of data")
        equity[-1] = engine.cash

    index = market.df["time"] if "time" in market.df.columns else market.df.index
    result = BacktestResult(engine.trades, pd.Series(equity, index=index), initial_equity)
    result.stats = _stats(result)
    result.stats["skipped_min_lot"] = sum(w.startswith("skipped") for w in engine.skipped)
    result.stats["at_min_lot"] = sum(w.startswith("minimum lot") for w in engine.skipped)
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


def _open(sig: Signal, t: int, price: float, equity: float, mm: MoneyManagement) -> tuple[Trade | None, str]:
    long = sig.side == "long"
    if (price <= sig.stop or price >= sig.target) if long else (price >= sig.stop or price <= sig.target):
        return None, ""
    pos = position(mm, equity, price, sig.stop)
    if pos.units <= 0:
        return None, pos.note
    return Trade(sig.side, sig.setup, t, float(price), sig.stop, sig.target, pos.units, sig.reasons,
                 rate=pos.rate, lots=pos.lots), pos.note


def _partial_qty(pos: Trade, mm: MoneyManagement) -> float:
    """Units to close at the partial target; 0 when lots can't be split (e.g. 0.01 lot can't be halved)."""
    if pos.lots is None:
        return pos.size * mm.partial_pct
    step = mm.lot_step
    part = np.floor(pos.lots * mm.partial_pct / step + 1e-9) * step
    if part < mm.min_lot - 1e-12 or pos.lots - part < mm.min_lot - 1e-12:
        return 0.0
    return float(part) * (pos.size / pos.lots)


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
