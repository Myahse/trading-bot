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
    max_trades_per_day: int | None = None


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
