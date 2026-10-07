"""Order blocks (smart-money concept).

Bullish OB: the last bearish candle before an impulsive move that closes above the
most recent swing high (break of structure). Bearish OB is the mirror image.
An OB becomes known on the break-of-structure bar and is invalidated once a candle
closes through its far side.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .structure import Pivot


@dataclass(frozen=True)
class OrderBlock:
    kind: str                 # "bullish" | "bearish"
    index: int                # bar of the order-block candle
    low: float
    high: float
    created_at: int           # break-of-structure bar
    invalidated_at: int | None

    def active_at(self, t: int) -> bool:
        return self.created_at <= t and (self.invalidated_at is None or self.invalidated_at > t)


def find_order_blocks(df: pd.DataFrame, pivots: list[Pivot], atr: np.ndarray,
                      displacement_atr: float = 1.0, max_lookback: int = 10) -> list[OrderBlock]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(df)
    blocks: list[OrderBlock] = []
    last_high: Pivot | None = None
    last_low: Pivot | None = None
    pending = sorted(pivots, key=lambda p: p.confirmed_at)
    k = 0
    for t in range(n):
        while k < len(pending) and pending[k].confirmed_at <= t:
            p = pending[k]
            if p.kind == "high":
                last_high = p
            else:
                last_low = p
            k += 1
        if np.isnan(atr[t]):
            continue
        if last_high is not None and c[t] > last_high.price:
            ob = _origin(o, c, last_high.index, t, max_lookback, bearish=True)
            if ob is not None and h[ob + 1:t + 1].max() - h[ob] >= displacement_atr * atr[t]:
                blocks.append(OrderBlock("bullish", ob, float(l[ob]), float(h[ob]), t,
                                         _invalidation(c, t, l[ob], below=True)))
            last_high = None  # each swing is broken once
        if last_low is not None and c[t] < last_low.price:
            ob = _origin(o, c, last_low.index, t, max_lookback, bearish=False)
            if ob is not None and l[ob] - l[ob + 1:t + 1].min() >= displacement_atr * atr[t]:
                blocks.append(OrderBlock("bearish", ob, float(l[ob]), float(h[ob]), t,
                                         _invalidation(c, t, h[ob], below=False)))
            last_low = None
    return blocks


def _origin(o, c, swing_index, t, max_lookback, bearish) -> int | None:
    """Last opposite-colour candle before the impulse, searching back from t."""
    for i in range(t - 1, max(swing_index, t - max_lookback) - 1, -1):
        if (c[i] < o[i]) if bearish else (c[i] > o[i]):
            return i
    return None


def _invalidation(c, t, level, below) -> int | None:
    after = c[t + 1:]
    hits = np.nonzero(after < level if below else after > level)[0]
    return int(t + 1 + hits[0]) if hits.size else None
