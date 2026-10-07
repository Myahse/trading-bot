"""Market structure: ATR, swing pivots, support/resistance zones and trendlines.

Every object carries the bar index at which it became *known* (``confirmed_at``),
so the strategy can be evaluated bar by bar without looking into the future.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


@dataclass(frozen=True)
class Pivot:
    index: int          # bar of the swing high/low
    price: float
    kind: str           # "high" | "low"
    confirmed_at: int   # first bar at which the pivot is known (index + right)


def find_pivots(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[Pivot]:
    """Fractal swing points: a high higher than `left` bars before and `right` bars after."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    pivots: list[Pivot] = []
    for i in range(left, len(df) - right):
        if highs[i] > highs[i - left:i].max() and highs[i] >= highs[i + 1:i + right + 1].max():
            pivots.append(Pivot(i, float(highs[i]), "high", i + right))
        if lows[i] < lows[i - left:i].min() and lows[i] <= lows[i + 1:i + right + 1].min():
            pivots.append(Pivot(i, float(lows[i]), "low", i + right))
    return sorted(pivots, key=lambda p: (p.confirmed_at, p.index))


@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    touches: int

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


def sr_zones(pivots: list[Pivot], tolerance: float, min_touches: int = 2) -> list[Zone]:
    """Cluster pivot prices that sit within `tolerance` of each other into zones.

    A zone is not support or resistance by itself: it is support while price is
    above it and resistance while price is below it.
    """
    prices = sorted(p.price for p in pivots)
    zones: list[Zone] = []
    cluster: list[float] = []
    for price in prices:
        if cluster and price - cluster[0] > tolerance:
            if len(cluster) >= min_touches:
                zones.append(_zone(cluster, tolerance))
            cluster = []
        cluster.append(price)
    if len(cluster) >= min_touches:
        zones.append(_zone(cluster, tolerance))
    return zones


def _zone(cluster: list[float], tolerance: float) -> Zone:
    pad = max(tolerance / 4 - (cluster[-1] - cluster[0]) / 2, 0.0)  # keep a minimum zone width
    return Zone(cluster[0] - pad, cluster[-1] + pad, len(cluster))


@dataclass(frozen=True)
class Trendline:
    i1: int
    p1: float
    i2: int
    p2: float
    kind: str                     # "support" (rising, through lows) | "resistance" (falling, through highs)
    broken_at: int | None = None  # first bar that closed through the line

    @property
    def slope(self) -> float:
        return (self.p2 - self.p1) / (self.i2 - self.i1)

    def value_at(self, i: int) -> float:
        return self.p1 + self.slope * (i - self.i1)


def trendlines(pivots: list[Pivot], closes: np.ndarray, t: int, tolerance: float) -> list[Trendline]:
    """Rising support through the last two swing lows / falling resistance through the
    last two swing highs, as known at bar t.

    A line that closes were already beyond between its anchors is discarded. Otherwise
    ``broken_at`` is the first bar up to t whose close went through the line by more
    than `tolerance` (None while the line is intact).
    """
    lines: list[Trendline] = []
    lows = [p for p in pivots if p.kind == "low"][-2:]
    highs = [p for p in pivots if p.kind == "high"][-2:]
    if len(lows) == 2 and lows[1].price > lows[0].price:
        lines.append(Trendline(lows[0].index, lows[0].price, lows[1].index, lows[1].price, "support"))
    if len(highs) == 2 and highs[1].price < highs[0].price:
        lines.append(Trendline(highs[0].index, highs[0].price, highs[1].index, highs[1].price, "resistance"))
    out = []
    for line in lines:
        idx = np.arange(line.i1, t + 1)
        values = line.p1 + line.slope * (idx - line.i1)
        crossed = closes[idx] < values - tolerance if line.kind == "support" else closes[idx] > values + tolerance
        hits = np.nonzero(crossed)[0]
        if not hits.size:
            out.append(line)
        elif idx[hits[0]] > line.i2:
            out.append(replace(line, broken_at=int(idx[hits[0]])))
    return out


def market_bias(pivots: list[Pivot]) -> str:
    """'up' for higher highs + higher lows, 'down' for lower highs + lower lows."""
    highs = [p.price for p in pivots if p.kind == "high"][-2:]
    lows = [p.price for p in pivots if p.kind == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"
    if highs[1] > highs[0] and lows[1] > lows[0]:
        return "up"
    if highs[1] < highs[0] and lows[1] < lows[0]:
        return "down"
    return "neutral"
