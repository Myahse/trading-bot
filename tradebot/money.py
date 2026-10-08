"""Money management: position size, break-even, partial profits, trailing stops, daily limits."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class MoneyManagement:
    risk_per_trade: float = 0.01          # fraction of the account lost if the initial stop is hit
    max_leverage: float = 30.0            # cap on position value / account
    breakeven_r: float | None = 1.0       # move the stop to entry once price has gone this many R in favour
    partial_r: float | None = 1.0         # take part of the position off at this many R...
    partial_pct: float = 0.5              # ...this fraction of it
    trail: str = "atr"                    # "none" | "atr" (best price - trail_atr x ATR) | "swing" (behind swings)
    trail_start_r: float = 1.0            # start trailing once price has gone this many R in favour
    trail_atr: float = 2.0
    max_daily_loss: float | None = 0.03   # no new trades for the rest of the day after losing this fraction
    max_spread_risk: float | None = 0.2   # skip a setup when the spread is more than this share of its risk
    max_trades_per_day: int | None = None
    # Real lot sizes. With contract_size None, positions are sized in fractional units (no lots).
    contract_size: float | None = None    # units per lot (forex 100,000, gold 100, indices 1)
    min_lot: float = 0.01
    lot_step: float = 0.01
    quote_rate: float | None = None       # USD per unit of quote currency; None: 1, or derived from `symbol`
    symbol: str | None = None             # Deriv symbol, to derive the rate for USDxxx pairs
    min_lot_max_risk: float = 0.05        # if even the minimum lot risks more than this, skip the trade

    def rate(self, price: float) -> float:
        if self.quote_rate is not None:
            return self.quote_rate
        if self.symbol:
            r = quote_rate(self.symbol, price)
            if r is None:
                raise ValueError(f"{self.symbol} is a cross: pass --quote-rate (USD per 1 {self.symbol[-3:]})")
            return r
        return 1.0

    @classmethod
    def for_symbol(cls, deriv_symbol: str, **kwargs) -> "MoneyManagement":
        """Lot sizes for a Deriv symbol: forex and gold use real lots; synthetic indices only when
        `min_lot` is given, because their minimum volume varies (check the MT5 specification)."""
        mm = cls(**kwargs)
        mm.symbol = deriv_symbol
        if mm.contract_size is None and deriv_symbol.startswith("frx"):
            mm.contract_size = contract_size(deriv_symbol)
        return mm


@dataclass
class Position:
    units: float            # 0 when the trade must be skipped
    lots: float | None      # None when not sizing in lots
    risk: float             # fraction of the account actually at risk
    rate: float             # USD per unit of quote currency used
    note: str = ""


def position(mm: MoneyManagement, equity: float, entry: float, stop: float) -> Position:
    """How big to trade so that the stop loses `risk_per_trade` of `equity`, within the lot rules."""
    rate = mm.rate(entry)
    dist = abs(entry - stop)
    if dist <= 0 or equity <= 0:
        return Position(0.0, 0.0, 0.0, rate, "no risk distance or no equity")
    max_units = equity * mm.max_leverage / (entry * rate)
    if mm.contract_size is None:
        units = min(equity * mm.risk_per_trade / (dist * rate), max_units)
        return Position(units, None, units * dist * rate / equity, rate)

    per_lot = dist * mm.contract_size * rate          # USD lost per lot if the stop is hit
    step = mm.lot_step
    want = math.floor(equity * mm.risk_per_trade / per_lot / step + 1e-9) * step
    note = ""
    if want < mm.min_lot - 1e-12:
        min_risk = mm.min_lot * per_lot / equity
        if min_risk > mm.min_lot_max_risk:
            return Position(0.0, 0.0, min_risk, rate, f"skipped: the minimum {mm.min_lot:g} lot would risk "
                                                      f"{min_risk:.1%} of the account (limit {mm.min_lot_max_risk:.0%})")
        want, note = mm.min_lot, f"minimum lot: risking {min_risk:.1%} instead of {mm.risk_per_trade:.1%}"
    cap = math.floor(max_units / mm.contract_size / step + 1e-9) * step
    if want > cap:
        if cap < mm.min_lot - 1e-12:
            need = mm.min_lot * mm.contract_size * entry * rate / mm.max_leverage
            return Position(0.0, 0.0, 0.0, rate, f"skipped: not enough margin - {mm.min_lot:g} lot needs "
                                                 f"${need:,.2f} at {mm.max_leverage:g}x leverage (set --leverage "
                                                 f"to your account's)")
        want, note = cap, f"capped at {mm.max_leverage:g}x leverage"
    want = round(want, 8)
    return Position(want * mm.contract_size, want, want * per_lot / equity, rate, note)


# Contract size per lot on Deriv MT5 - check the symbol specification in your terminal.
def contract_size(deriv_symbol: str) -> float:
    if deriv_symbol in ("frxXAUUSD", "frxXAGUSD"):
        return 100.0 if deriv_symbol == "frxXAUUSD" else 5000.0
    if deriv_symbol.startswith("frx"):
        return 100_000.0
    return 1.0  # synthetic indices


def quote_rate(deriv_symbol: str, price: float) -> float | None:
    """Value of one unit of the quote currency in USD, or None when it needs another rate (crosses)."""
    pair = deriv_symbol.removeprefix("frx")
    if not deriv_symbol.startswith("frx") or pair.endswith("USD"):
        return 1.0
    if pair.startswith("USD"):
        return 1.0 / price
    return None


def lots(equity: float, risk: float, entry: float, stop: float, size_per_lot: float,
         rate: float = 1.0, step: float = 0.01) -> float:
    """Lots so that hitting the stop loses `risk` of `equity` (account currency), rounded down to `step`."""
    loss_per_lot = abs(entry - stop) * size_per_lot * rate
    if loss_per_lot <= 0:
        return 0.0
    return math.floor(equity * risk / loss_per_lot / step + 1e-9) * step
