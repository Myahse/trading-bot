"""Trading patterns: candlesticks on the signal candle, and chart patterns built from the swings.

Candlesticks (the candle at bar t, against the one or two before it):
  engulfing        its body covers the previous candle's opposite-coloured body
  morning/evening star  a strong candle, a small one, then a candle closing past the middle of the first
  hammer / shooting star  a wick at least 60% of the range and twice the body, the other wick small
They count as one more level in the confluence rule, in their own direction only.

Chart patterns (from swings already confirmed at bar t, so nothing is seen early):
  double bottom / top          two swings within 0.5 ATR, at least 1.5 ATR from the swing between
                               them; the neckline is that swing's price
  inverse / head and shoulders a head at least 0.5 ATR beyond two shoulders within 1 ATR of each
                               other; the neckline runs through the two swings between them
A strong candle closing through the neckline is a breakout entry; after the break, a rejection at
the neckline counts as a level (its retest). An unbroken double bottom/top counts as a level at its
bottom/top. A pattern is dropped when price closes beyond its invalidation level (the bottoms, or
the head), when it has not broken within `max_age` bars, or when its retest window has passed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .structure import Pivot

PATTERN_TOL_ATR = 0.5        # the two bottoms/tops (and the shoulders, x2) agree within this many ATRs
PATTERN_DEPTH_ATR = 1.5      # a double bottom/top is at least this deep
HEAD_ATR = 0.5               # the head stands out from the shoulders by at least this
BREAK_ATR = 0.1              # a close this far through the neckline breaks it
FAIL_ATR = 0.3               # a close this far beyond the bottoms invalidates a double bottom


def candle_pattern(o, h, l, c, t: int) -> tuple[str, str] | None:
    """(name, "long"|"short") for the candle at t, or None."""
    if t < 2:
        return None
    rng = h[t] - l[t]
    if rng <= 0:
        return None
    body = abs(c[t] - o[t])
    body1 = abs(c[t - 1] - o[t - 1])
    if c[t] > o[t] and c[t - 1] < o[t - 1] and c[t] >= o[t - 1] and o[t] <= c[t - 1] and body > body1:
        return "bullish engulfing", "long"
    if c[t] < o[t] and c[t - 1] > o[t - 1] and c[t] <= o[t - 1] and o[t] >= c[t - 1] and body > body1:
        return "bearish engulfing", "short"
    rng2, body2 = h[t - 2] - l[t - 2], abs(c[t - 2] - o[t - 2])
    if rng2 > 0 and body2 >= 0.6 * rng2 and body1 <= 0.3 * body2:
        mid2 = (o[t - 2] + c[t - 2]) / 2
        if c[t - 2] < o[t - 2] and c[t] > o[t] and c[t] > mid2:
            return "morning star", "long"
        if c[t - 2] > o[t - 2] and c[t] < o[t] and c[t] < mid2:
            return "evening star", "short"
    lower, upper = min(o[t], c[t]) - l[t], h[t] - max(o[t], c[t])
    if lower >= 0.6 * rng and lower >= 2 * body and upper <= 0.25 * rng:
        return "hammer", "long"
    if upper >= 0.6 * rng and upper >= 2 * body and lower <= 0.25 * rng:
        return "shooting star", "short"
    return None


@dataclass(frozen=True)
class ChartPattern:
    kind: str                       # "double bottom", "double top", "inverse head and shoulders", "head and shoulders"
    side: str                       # "long" (bullish) | "short" (bearish)
    points: tuple[tuple[int, float], ...]   # the swings that make it, in order
    n1: int                         # neckline: through (n1, p1) and (n2, p2)
    p1: float
    n2: int
    p2: float
    complete_at: int                # bar the last swing was confirmed
    broken_at: int | None           # bar a candle closed through the neckline (None: not yet)
    invalid: float                  # a close beyond this kills an unbroken pattern
    height: float                   # from the neckline to the extreme: the measured move

    def neck(self, i: float) -> float:
        return self.p1 if self.n2 == self.n1 else self.p1 + (self.p2 - self.p1) / (self.n2 - self.n1) * (i - self.n1)

    @property
    def target(self) -> float:
        """The measured move from the break: the pattern's height beyond the neckline."""
        x = self.broken_at if self.broken_at is not None else self.points[-1][0]
        return self.neck(x) + (self.height if self.side == "long" else -self.height)


def chart_patterns(pivots: list[Pivot], c: np.ndarray, atr: np.ndarray, t: int, max_age: int,
                   retest: int) -> list[ChartPattern]:
    """The chart patterns alive at bar t, from the swings confirmed by t."""
    lows = sorted((p for p in pivots if p.kind == "low"), key=lambda p: p.index)
    highs = sorted((p for p in pivots if p.kind == "high"), key=lambda p: p.index)
    found = []
    for side in ("long", "short"):
        ext, opp = (lows, highs) if side == "long" else (highs, lows)
        sign = 1 if side == "long" else -1           # long: bottoms below, neckline above
        # double bottom (long) / double top (short): the last two extremes and the swing between
        if len(ext) >= 2:
            a, b = ext[-2], ext[-1]
            between = [p for p in opp if a.index < p.index < b.index]
            atr_b = atr[b.index]
            if between and not np.isnan(atr_b):
                mid = max(between, key=lambda p: sign * p.price)
                worst = min(a.price, b.price) if side == "long" else max(a.price, b.price)
                if abs(a.price - b.price) <= PATTERN_TOL_ATR * atr_b and \
                        sign * (mid.price - worst) >= PATTERN_DEPTH_ATR * atr_b:
                    found.append(_track("double bottom" if side == "long" else "double top", side,
                                        ((a.index, a.price), (mid.index, mid.price), (b.index, b.price)),
                                        mid.index, mid.price, b.index, mid.price, b.confirmed_at,
                                        worst, sign * (mid.price - worst), c, atr, t, max_age, retest))
        # inverse head and shoulders (long) / head and shoulders (short): the last three extremes
        if len(ext) >= 3:
            s1, hd, s2 = ext[-3], ext[-2], ext[-1]
            atr_s = atr[s2.index]
            t1 = [p for p in opp if s1.index < p.index < hd.index]
            t2 = [p for p in opp if hd.index < p.index < s2.index]
            if t1 and t2 and not np.isnan(atr_s) and abs(s1.price - s2.price) <= 2 * PATTERN_TOL_ATR * atr_s and \
                    sign * (min(s1.price, s2.price) if side == "long" else max(s1.price, s2.price)) - sign * hd.price \
                    >= HEAD_ATR * atr_s:
                m1 = max(t1, key=lambda p: sign * p.price)
                m2 = max(t2, key=lambda p: sign * p.price)
                pat = ChartPattern("", side, (), m1.index, m1.price, m2.index, m2.price, 0, None, 0.0, 0.0)
                height = sign * (pat.neck(hd.index) - hd.price)
                if height > 0:
                    found.append(_track("inverse head and shoulders" if side == "long" else "head and shoulders",
                                        side, ((s1.index, s1.price), (m1.index, m1.price), (hd.index, hd.price),
                                               (m2.index, m2.price), (s2.index, s2.price)),
                                        m1.index, m1.price, m2.index, m2.price, s2.confirmed_at, hd.price, height,
                                        c, atr, t, max_age, retest))
    return [p for p in found if p is not None]


def _track(kind, side, points, n1, p1, n2, p2, complete, invalid, height, c, atr, t, max_age, retest):
    """Follow the pattern from its completion to t: its neckline break, or its failure."""
    pat = ChartPattern(kind, side, points, n1, p1, n2, p2, complete, None, invalid, height)
    if complete > t:
        return None
    sign = 1 if side == "long" else -1
    for j in range(complete, t + 1):
        a = atr[j]
        if np.isnan(a):
            continue
        if sign * (c[j] - pat.neck(j)) > BREAK_ATR * a:
            if t - j > retest:
                return None                     # broken too long ago: its retest window has passed
            return ChartPattern(kind, side, points, n1, p1, n2, p2, complete, j, invalid, height)
        if sign * (invalid - c[j]) > FAIL_ATR * a:
            return None                         # closed beyond the bottoms / the head: it failed
    return pat if t - complete <= max_age else None


def triangle(trendlines) -> bool:
    """A rising and a falling entry-chart trendline, both intact: price is coiling into a triangle."""
    intact = [ln for ln in trendlines if not ln.htf and ln.broken_at is None]
    return any(ln.kind == "support" for ln in intact) and any(ln.kind == "resistance" for ln in intact)
