"""The strategy: trade rejections from support/resistance, trendlines and order blocks.

Long setup at the close of bar t (short is the mirror image):
  1. Price tags at least `min_confluence` of: a support zone, a rising trendline,
     an active bullish order block.
  2. Bar t is a bullish rejection candle (green, closes in the top half of its range).
  3. Market structure is not making lower highs and lower lows (trend filter).
  4. Stop goes below the lowest structure that was tagged; target is the nearest
     opposing obstacle (resistance zone, bearish OB, falling trendline), or
     `default_rr` x risk when nothing is overhead. Skipped if reward:risk < `min_rr`.
The order is placed at the next bar's open.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .orderblocks import OrderBlock, find_order_blocks
from .structure import Pivot, Trendline, Zone, atr, find_pivots, market_bias, sr_zones, trendlines


@dataclass
class StrategyConfig:
    pivot_left: int = 5
    pivot_right: int = 5
    atr_period: int = 14
    zone_tolerance_atr: float = 0.6     # pivots within this many ATRs form one zone
    zone_min_touches: int = 2
    zone_lookback_pivots: int = 40      # only the most recent pivots build zones
    touch_buffer_atr: float = 0.25      # how close counts as "tagging" a level
    stop_buffer_atr: float = 0.3        # stop distance beyond the structure
    ob_displacement_atr: float = 1.0    # impulse size needed to create an order block
    ob_max_age: int = 200               # bars after which an untouched order block is ignored
    min_confluence: int = 2
    min_rr: float = 1.5
    default_rr: float = 2.0
    trend_filter: bool = True


@dataclass
class Signal:
    side: str          # "long" | "short"
    bar: int
    entry: float       # reference price (close of the signal bar)
    stop: float
    target: float
    reasons: list[str] = field(default_factory=list)

    @property
    def rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


@dataclass
class Analysis:
    bar: int
    price: float
    atr: float
    bias: str
    support: list[Zone]
    resistance: list[Zone]
    trendlines: list[Trendline]
    order_blocks: list[OrderBlock]
    signal: Signal | None


class Market:
    """Precomputes everything once, then answers 'what is known at bar t?'."""

    def __init__(self, df: pd.DataFrame, cfg: StrategyConfig | None = None):
        self.cfg = cfg or StrategyConfig()
        self.df = df.reset_index(drop=True)
        self.o, self.h, self.l, self.c = (self.df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = atr(self.df, self.cfg.atr_period).to_numpy()
        self.pivots = find_pivots(self.df, self.cfg.pivot_left, self.cfg.pivot_right)
        self._confirmed = np.array([p.confirmed_at for p in self.pivots])
        self.order_blocks = find_order_blocks(self.df, self.pivots, self.atr, self.cfg.ob_displacement_atr)

    def pivots_known_at(self, t: int) -> list[Pivot]:
        return self.pivots[: int(np.searchsorted(self._confirmed, t, side="right"))]

    def analyze(self, t: int) -> Analysis:
        cfg, a, c = self.cfg, self.atr[t], self.c[t]
        pivots = self.pivots_known_at(t)
        zones = sr_zones(pivots[-cfg.zone_lookback_pivots:], cfg.zone_tolerance_atr * a, cfg.zone_min_touches) \
            if not np.isnan(a) else []
        lines = trendlines(pivots, self.c, t, 0.1 * a) if not np.isnan(a) else []
        obs = [ob for ob in self.order_blocks if ob.active_at(t) and t - ob.created_at <= cfg.ob_max_age]
        analysis = Analysis(
            bar=t, price=float(c), atr=float(a), bias=market_bias(pivots),
            support=sorted((z for z in zones if z.mid < c), key=lambda z: -z.mid),
            resistance=sorted((z for z in zones if z.mid >= c), key=lambda z: z.mid),
            trendlines=lines, order_blocks=obs, signal=None)
        if not np.isnan(a):
            analysis.signal = self._long(t, analysis) or self._short(t, analysis)
        return analysis

    # -- setups ---------------------------------------------------------------

    def _long(self, t: int, an: Analysis) -> Signal | None:
        cfg, a = self.cfg, an.atr
        o, h, lo, c = self.o[t], self.h[t], self.l[t], self.c[t]
        if cfg.trend_filter and an.bias == "down":
            return None
        if not (c > o and c >= lo + 0.5 * (h - lo)):
            return None
        buf, reasons, floors = cfg.touch_buffer_atr * a, [], [lo]

        zone = next((z for z in an.support if lo <= z.high + buf and c > z.low), None)
        if zone:
            reasons.append(f"support zone {zone.low:.4g}-{zone.high:.4g} ({zone.touches} touches)")
            floors.append(zone.low)
        for line in an.trendlines:
            v = line.value_at(t)
            if line.kind == "support" and lo <= v + buf and c > v:
                reasons.append(f"rising trendline at {v:.4g}")
                floors.append(v)
        ob = next((b for b in reversed(an.order_blocks)
                   if b.kind == "bullish" and lo <= b.high + buf and c > b.low), None)
        if ob:
            reasons.append(f"bullish order block {ob.low:.4g}-{ob.high:.4g}")
            floors.append(ob.low)
        if len(reasons) < cfg.min_confluence:
            return None

        stop = min(floors) - cfg.stop_buffer_atr * a
        obstacles = [z.low for z in an.resistance if z.low > c]
        obstacles += [b.low for b in an.order_blocks if b.kind == "bearish" and b.low > c]
        obstacles += [v for line in an.trendlines if line.kind == "resistance" and (v := line.value_at(t)) > c]
        target = min(obstacles) if obstacles else c + cfg.default_rr * (c - stop)
        signal = Signal("long", t, float(c), float(stop), float(target), reasons + [f"bias {an.bias}"])
        return signal if signal.rr >= cfg.min_rr else None

    def _short(self, t: int, an: Analysis) -> Signal | None:
        cfg, a = self.cfg, an.atr
        o, hi, lo, c = self.o[t], self.h[t], self.l[t], self.c[t]
        if cfg.trend_filter and an.bias == "up":
            return None
        if not (c < o and c <= hi - 0.5 * (hi - lo)):
            return None
        buf, reasons, ceilings = cfg.touch_buffer_atr * a, [], [hi]

        zone = next((z for z in an.resistance if hi >= z.low - buf and c < z.high), None)
        if zone:
            reasons.append(f"resistance zone {zone.low:.4g}-{zone.high:.4g} ({zone.touches} touches)")
            ceilings.append(zone.high)
        for line in an.trendlines:
            v = line.value_at(t)
            if line.kind == "resistance" and hi >= v - buf and c < v:
                reasons.append(f"falling trendline at {v:.4g}")
                ceilings.append(v)
        ob = next((b for b in reversed(an.order_blocks)
                   if b.kind == "bearish" and hi >= b.low - buf and c < b.high), None)
        if ob:
            reasons.append(f"bearish order block {ob.low:.4g}-{ob.high:.4g}")
            ceilings.append(ob.high)
        if len(reasons) < cfg.min_confluence:
            return None

        stop = max(ceilings) + cfg.stop_buffer_atr * a
        obstacles = [z.high for z in an.support if z.high < c]
        obstacles += [b.high for b in an.order_blocks if b.kind == "bullish" and b.high < c]
        obstacles += [v for line in an.trendlines if line.kind == "support" and (v := line.value_at(t)) < c]
        target = max(obstacles) if obstacles else c - cfg.default_rr * (stop - c)
        signal = Signal("short", t, float(c), float(stop), float(target), reasons + [f"bias {an.bias}"])
        return signal if signal.rr >= cfg.min_rr else None


def analyze(df: pd.DataFrame, cfg: StrategyConfig | None = None, t: int | None = None) -> Analysis:
    """What the strategy sees at bar t (default: the latest bar)."""
    market = Market(df, cfg)
    return market.analyze(len(market.df) - 1 if t is None else t)
