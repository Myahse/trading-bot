"""Market structure: ATR, swing pivots, support/resistance zones and trendlines.

Every object carries the bar index at which it became *known* (``confirmed_at``),
so the strategy can be evaluated bar by bar without looking into the future.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


def px(price: float) -> str:
    """Price for display: 2 decimals for big numbers (indices, gold), up to 5 significant digits for FX."""
    return f"{price:,.2f}" if abs(price) >= 1000 else f"{price:.5g}"


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
    kind: str                     # "support" (rising, under the lows) | "resistance" (falling, over the highs)
    broken_at: int | None = None  # first bar that closed through the line
    touches: tuple[int, ...] = () # bars of the swing points that sit on the line
    htf: bool = False             # drawn through higher-timeframe swings

    @property
    def slope(self) -> float:
        return (self.p2 - self.p1) / (self.i2 - self.i1)

    def value_at(self, i: int) -> float:
        return self.p1 + self.slope * (i - self.i1)


class TrendlineFinder:
    """Draws trendlines the way a trader would.

    Every pair of recent swing lows (rising) or swing highs (falling) is a candidate.
    A candidate is kept only if no candle between its two anchors pokes through it
    (wicks included, within `wick_atr`), its anchors are at least `min_span` bars apart,
    and its slope is not absurdly steep. At bar t
    the candidates are ranked by how many swing points touch the line, then by how
    recent the last touch is, then by length. Per side, the best intact line and the
    best line broken within the retest window are returned.

    Break bars are computed once over all data but only revealed once t reaches them,
    so nothing is known before it happens.
    """

    def __init__(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, *,
                 candidates: int = 8, touch_atr: float = 0.25, wick_atr: float = 0.1,
                 break_atr: float = 0.1, max_slope_atr: float = 0.5, min_span: int = 10, htf: bool = False):
        self.h, self.l, self.c, self.atr = high, low, close, atr
        self.candidates, self.touch_atr, self.wick_atr = candidates, touch_atr, wick_atr
        self.break_atr, self.max_slope_atr, self.min_span, self.htf = break_atr, max_slope_atr, min_span, htf
        self._cache: dict[tuple, list[tuple[Trendline, int | None]]] = {}

    def lines_at(self, t: int, pivots: list[Pivot], retest_window: int) -> list[Trendline]:
        out: list[Trendline] = []
        for kind, pivot_kind in (("support", "low"), ("resistance", "high")):
            swings = [p for p in pivots if p.kind == pivot_kind]
            recent = tuple(swings[-self.candidates:])
            if recent not in self._cache:
                self._cache[recent] = self._candidates(kind, recent)
            best: dict[bool, tuple[tuple, Trendline]] = {}
            for line, break_bar in self._cache[recent]:
                broken = break_bar is not None and break_bar <= t
                if broken and t - break_bar > retest_window:
                    continue
                touches = tuple(p.index for p in swings if line.i1 <= p.index
                                and (break_bar is None or p.index < break_bar)   # nothing counts after a break
                                and abs(p.price - line.value_at(p.index)) <= self.touch_atr * self.atr[p.index])
                if len(touches) < 2:
                    continue
                score = (len(touches), touches[-1], line.i2 - line.i1)
                if broken not in best or score > best[broken][0]:
                    best[broken] = (score, replace(line, touches=touches,
                                                   broken_at=break_bar if broken else None))
            out += [best[k][1] for k in (False, True) if k in best]
        return out

    def _candidates(self, kind: str, swings: tuple[Pivot, ...]) -> list[tuple[Trendline, int | None]]:
        found = []
        for i, a in enumerate(swings):
            for b in swings[i + 1:]:
                if b.index - a.index < self.min_span:   # two swings side by side make a meaningless line
                    continue
                slope = (b.price - a.price) / (b.index - a.index)
                if (slope <= 0 if kind == "support" else slope >= 0):
                    continue
                atr_b = self.atr[b.index]
                if np.isnan(atr_b) or abs(slope) > self.max_slope_atr * atr_b:
                    continue
                span = np.arange(a.index, b.index + 1)
                line_vals = a.price + slope * (span - a.index)
                tol = self.wick_atr * atr_b
                cut = self.l[span] < line_vals - tol if kind == "support" else self.h[span] > line_vals + tol
                if cut.any():
                    continue
                after = np.arange(b.index + 1, len(self.c))
                vals = a.price + slope * (after - a.index)
                atr_after = np.nan_to_num(self.atr[after], nan=atr_b)
                crossed = self.c[after] < vals - self.break_atr * atr_after if kind == "support" \
                    else self.c[after] > vals + self.break_atr * atr_after
                hits = np.nonzero(crossed)[0]
                found.append((Trendline(a.index, a.price, b.index, b.price, kind, htf=self.htf),
                              int(after[hits[0]]) if hits.size else None))
        return found


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
