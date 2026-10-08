"""The strategy: support/resistance, trendlines and order blocks on two fractal timeframes.

Two setups are evaluated at the close of every entry bar (shorts mirror longs):

Rejection (long)
  1. Price tags at least `min_confluence` of: an entry-timeframe support zone, a
     higher-timeframe support zone, a rising trendline, the retest of a falling
     trendline that was just broken, or an active bullish order block.
  2. The bar is a bullish rejection candle (green, closes in the top half of its range).

Trendline breakout (long)
  The bar closes above a falling trendline with a strong green body.

Both need the trend filter to agree: the higher-timeframe structure when one is set
(fractal top-down), otherwise the entry timeframe's own swings. The stop goes beyond
the structure that was used; the target is the nearest opposing obstacle (zone,
order block or trendline on either timeframe), or `default_rr` x risk when nothing
is in the way. Trades below `min_rr` are skipped. `target` picks a further target for a
higher reward:risk: the next obstacle, the nearest higher-timeframe one, or a runner.

Confirmation (`confirmation`): by default a setup is only entered once price breaks the
signal candle's high (long) / low (short) within `confirm_bars` bars - a buy/sell stop.
"close" waits for a candle to close beyond that level and enters at the next open;
"none" enters at the next open straight away. "refine" is the surgical entry: the setup
marks an entry zone, and the trade is entered on a lower-timeframe change of character
inside it, with a lower-timeframe stop (see refine.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .fractal import HigherTimeframe
from .orderblocks import OrderBlock, find_order_blocks
from .structure import LevelBook, Pivot, Trendline, TrendlineFinder, Zone, atr, px, find_pivots, market_bias


@dataclass
class StrategyConfig:
    pivot_left: int = 5
    pivot_right: int = 5
    atr_period: int = 14
    zone_tolerance_atr: float = 0.6     # pivots within this many ATRs form one zone
    htf_zone_tolerance_atr: float = 0.3 # the same on the higher timeframe (its ATR is much bigger)
    zone_min_touches: int = 2
    zone_lookback_pivots: int = 40      # entry-chart zones kept (the most recently touched)
    htf_zone_lookback: int = 0          # higher-timeframe zones kept (0 = 200: old daily/weekly levels matter)
    touch_buffer_atr: float = 0.25      # how close counts as "tagging" a level
    stop_buffer_atr: float = 0.3        # stop distance beyond the structure
    min_stop_atr: float = 0.0           # never place the stop closer than this many ATRs
    ob_displacement_atr: float = 1.0    # impulse size needed to create an order block
    ob_max_age: int = 200               # bars after which an untouched order block is ignored
    min_confluence: int = 2
    min_rr: float = 1.5
    default_rr: float = 2.0
    target: str = "nearest"             # "nearest": the first obstacle in the way
                                        # "next": the one after it (levels within 0.5 ATR count as one)
                                        # "htf": the nearest higher-timeframe level
                                        # "runner": no fixed target (runner_rr x risk); the trailing stop exits.
                                        #   Needs min_rr of room before the first obstacle.
    runner_rr: float = 10.0
    trend_filter: bool = True
    htf: str | int | None = None        # higher timeframe: "1h", "4h", "D", "W" or a bar count
    htf_pivot: int = 3                  # bars each side of a higher-timeframe swing
    breakouts: bool = True              # trade trendline breaks
    breakout_body: float = 0.5          # breakout candle body as a fraction of its range
    retest_window: int = 20             # bars after a trendline break during which a retest counts
    cooldown_bars: int = 0              # bars to stand aside after a losing trade
    confirmation: str = "break"         # "break": enter when price breaks the signal candle's high (long)
                                        # "close": enter after a candle closes beyond it
                                        # "none": enter at the next open
                                        # "refine": enter on a lower-timeframe change of character (needs ltf)
    confirm_bars: int = 3               # bars the confirmation may take before the setup is cancelled
    ltf: str | None = None              # lower timeframe for "refine", e.g. "1m" (data is loaded separately)
    refine_pivot: int = 2               # bars each side of a lower-timeframe swing
    refine_stop_buffer_atr: float = 0.3 # stop beyond the lower-timeframe swing, in lower-timeframe ATR
    refine_min_stop_atr: float = 1.0    # never closer than this many lower-timeframe ATRs (spread, noise)


MODES: dict[str, dict] = {
    # 5m entries (1m-15m all work), direction and big levels from the hourly chart.
    "scalp": dict(interval="5m", count=20_000, period="60d",
                  config=dict(htf="1h", pivot_left=3, pivot_right=3, htf_pivot=3, zone_lookback_pivots=30,
                              ob_max_age=100, min_rr=1.5, default_rr=1.5, retest_window=12, cooldown_bars=6,
                              min_stop_atr=1.0, confirmation="refine", ltf="1m", confirm_bars=3),
                  money=dict(risk_per_trade=0.005, breakeven_r=1.0, partial_r=1.0, partial_pct=0.5,
                             trail="atr", trail_start_r=1.0, trail_atr=1.5, max_daily_loss=0.03,
                             max_trades_per_day=8)),
    # 4h entries, direction and big levels from the daily chart.
    "swing": dict(interval="4h", count=5_000, period="730d",
                  config=dict(htf="D", pivot_left=5, pivot_right=5, htf_pivot=3, zone_lookback_pivots=40,
                              ob_max_age=150, min_rr=2.0, default_rr=2.5, retest_window=15, cooldown_bars=3,
                              confirmation="refine", ltf="15m", confirm_bars=2),
                  money=dict(risk_per_trade=0.01, breakeven_r=1.0, partial_r=1.5, partial_pct=0.5,
                             trail="swing", trail_start_r=1.5, max_daily_loss=None)),
}


def mode_config(mode: str, **overrides) -> StrategyConfig:
    return StrategyConfig(**{**MODES[mode]["config"], **overrides})


def mode_money(mode: str, **overrides):
    from .money import MoneyManagement
    return MoneyManagement(**{**MODES[mode]["money"], **overrides})


@dataclass
class Signal:
    side: str          # "long" | "short"
    setup: str         # "rejection" | "breakout"
    bar: int
    entry: float       # reference price (close of the signal bar)
    stop: float
    target: float
    reasons: list[str] = field(default_factory=list)
    trigger: float | None = None   # level price must break/close beyond to confirm (None: no confirmation)
    confirmation: str = "none"
    zone: tuple[float, float] | None = None   # "refine": (low, high) of the entry zone - from the structure
                                              # that was tagged to the signal close

    @property
    def rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


@dataclass
class Analysis:
    bar: int
    price: float
    atr: float
    bias: str                 # entry timeframe structure
    htf_bias: str | None      # higher timeframe structure (None when no htf is set)
    support: list[Zone]       # nearest first
    resistance: list[Zone]
    htf_support: list[Zone]
    htf_resistance: list[Zone]
    trendlines: list[Trendline]
    order_blocks: list[OrderBlock]
    signal: Signal | None

    @property
    def direction(self) -> str:
        """The bias trades must agree with: the higher timeframe's when there is one."""
        return self.htf_bias if self.htf_bias is not None else self.bias


class Market:
    """Precomputes everything once, then answers 'what is known at bar t?'."""

    def __init__(self, df: pd.DataFrame, cfg: StrategyConfig | None = None, ltf: pd.DataFrame | None = None):
        self.cfg = cfg or StrategyConfig()
        self.df = df.reset_index(drop=True)
        # lower-timeframe candles for surgical entries (refine.py); None: refine falls back to "break"
        self.ltf = None
        if ltf is not None and len(ltf) and "time" in self.df.columns:
            from .refine import LowerTimeframe
            self.ltf = LowerTimeframe(ltf, self.df["time"], self.cfg.refine_pivot, self.cfg.atr_period)
        self.o, self.h, self.l, self.c = (self.df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = atr(self.df, self.cfg.atr_period).to_numpy()
        self.pivots = find_pivots(self.df, self.cfg.pivot_left, self.cfg.pivot_right)
        self._confirmed = np.array([p.confirmed_at for p in self.pivots])
        self.order_blocks = find_order_blocks(self.df, self.pivots, self.atr, self.cfg.ob_displacement_atr)
        self.htf = HigherTimeframe(self.df, self.cfg.htf, self.cfg.htf_pivot, self.cfg.atr_period) \
            if self.cfg.htf else None
        # zones with a memory: they change only when a new swing confirms
        self.levels = LevelBook(self.pivots, self.atr, self.cfg.zone_tolerance_atr, self.cfg.zone_lookback_pivots,
                                self.cfg.zone_min_touches)
        self.htf_levels = LevelBook(self.htf.pivots, self.htf.atr_by_bar(len(self.c)), self.cfg.htf_zone_tolerance_atr,
                                    self.cfg.htf_zone_lookback or 200, 2) if self.htf else None
        span = 2 * (self.cfg.pivot_left + self.cfg.pivot_right)
        self.lines = TrendlineFinder(self.h, self.l, self.c, self.atr, min_span=span)
        self.htf_lines = TrendlineFinder(
            self.h, self.l, self.c, self.htf.atr_by_bar(len(self.c)), htf=True,
            min_span=int(4 * self.cfg.htf_pivot * self.htf.bars_per_candle)) if self.htf else None

    def pivots_known_at(self, t: int) -> list[Pivot]:
        return self.pivots[: int(np.searchsorted(self._confirmed, t, side="right"))]

    def analyze(self, t: int) -> Analysis:
        cfg, a, c = self.cfg, self.atr[t], self.c[t]
        ready = not np.isnan(a)
        pivots = self.pivots_known_at(t)
        zones = self.levels.at(t) if ready else []
        lines = self.lines.lines_at(t, pivots, cfg.retest_window) if ready else []
        obs = [ob for ob in self.order_blocks if ob.active_at(t) and t - ob.created_at <= cfg.ob_max_age]

        htf_bias, htf_zones = None, []
        if self.htf is not None:
            hp = self.htf.pivots_known_at(t)
            htf_bias = market_bias(hp)
            lines += self.htf_lines.lines_at(t, hp, int(cfg.retest_window * self.htf.bars_per_candle))
            ha = self.htf.atr_known_at(t)
            if not np.isnan(ha):
                htf_zones = self.htf_levels.at(t)

        def split(zs):
            return (sorted((z for z in zs if z.mid < c), key=lambda z: -z.mid),
                    sorted((z for z in zs if z.mid >= c), key=lambda z: z.mid))

        support, resistance = split(zones)
        htf_support, htf_resistance = split(htf_zones)
        an = Analysis(t, float(c), float(a), market_bias(pivots), htf_bias, support, resistance,
                      htf_support, htf_resistance, lines, obs, None)
        if ready:
            an.signal = self._long(t, an) or self._short(t, an)
        return an

    # -- setups ---------------------------------------------------------------

    def _long(self, t: int, an: Analysis) -> Signal | None:
        cfg, a = self.cfg, an.atr
        o, h, lo, c = self.o[t], self.h[t], self.l[t], self.c[t]
        if cfg.trend_filter and an.direction == "down":
            return None
        if not (c > o and c >= lo + 0.5 * (h - lo)):
            return None
        buf = cfg.touch_buffer_atr * a
        tags: list[tuple[str, float]] = []  # (reason, level the stop must sit beyond)

        zone = next((z for z in an.support if lo <= z.high + buf and c > z.low), None)
        if zone:
            tags.append((f"support zone {px(zone.low)}-{px(zone.high)} (x{zone.touches})", zone.low))
        zone = next((z for z in an.htf_support if lo <= z.high + buf and c > z.low), None)
        if zone:
            tags.append((f"HTF support zone {px(zone.low)}-{px(zone.high)} (x{zone.touches})", zone.low))
        for line in an.trendlines:
            v = line.value_at(t)
            if line.kind == "support" and line.broken_at is None and lo <= v + buf and c > v:
                tags.append((f"{_tf(line)}rising trendline at {px(v)} ({len(line.touches)} touches)", v))
            if line.kind == "resistance" and line.broken_at is not None and line.broken_at < t \
                    and lo <= v + buf and c > v:
                tags.append((f"retest of broken {_tf(line)}falling trendline at {px(v)}", v))
        ob = next((b for b in reversed(an.order_blocks)
                   if b.kind == "bullish" and lo <= b.high + buf and c > b.low), None)
        if ob:
            tags.append((f"bullish order block {px(ob.low)}-{px(ob.high)}", ob.low))

        if len(tags) >= cfg.min_confluence:
            stop = min([lo] + [lvl for _, lvl in tags]) - cfg.stop_buffer_atr * a
            return self._finish("long", "rejection", t, an, stop, [r for r, _ in tags])

        if cfg.breakouts and c - o >= cfg.breakout_body * (h - lo):
            for line in an.trendlines:
                if line.kind == "resistance" and line.broken_at == t:
                    stop = min(lo, line.value_at(t)) - cfg.stop_buffer_atr * a
                    return self._finish("long", "breakout", t, an, stop,
                                        [f"close above {_tf(line)}falling trendline at {px(line.value_at(t))}"])
        return None

    def _short(self, t: int, an: Analysis) -> Signal | None:
        cfg, a = self.cfg, an.atr
        o, hi, lo, c = self.o[t], self.h[t], self.l[t], self.c[t]
        if cfg.trend_filter and an.direction == "up":
            return None
        if not (c < o and c <= hi - 0.5 * (hi - lo)):
            return None
        buf = cfg.touch_buffer_atr * a
        tags: list[tuple[str, float]] = []

        zone = next((z for z in an.resistance if hi >= z.low - buf and c < z.high), None)
        if zone:
            tags.append((f"resistance zone {px(zone.low)}-{px(zone.high)} (x{zone.touches})", zone.high))
        zone = next((z for z in an.htf_resistance if hi >= z.low - buf and c < z.high), None)
        if zone:
            tags.append((f"HTF resistance zone {px(zone.low)}-{px(zone.high)} (x{zone.touches})", zone.high))
        for line in an.trendlines:
            v = line.value_at(t)
            if line.kind == "resistance" and line.broken_at is None and hi >= v - buf and c < v:
                tags.append((f"{_tf(line)}falling trendline at {px(v)} ({len(line.touches)} touches)", v))
            if line.kind == "support" and line.broken_at is not None and line.broken_at < t \
                    and hi >= v - buf and c < v:
                tags.append((f"retest of broken {_tf(line)}rising trendline at {px(v)}", v))
        ob = next((b for b in reversed(an.order_blocks)
                   if b.kind == "bearish" and hi >= b.low - buf and c < b.high), None)
        if ob:
            tags.append((f"bearish order block {px(ob.low)}-{px(ob.high)}", ob.high))

        if len(tags) >= cfg.min_confluence:
            stop = max([hi] + [lvl for _, lvl in tags]) + cfg.stop_buffer_atr * a
            return self._finish("short", "rejection", t, an, stop, [r for r, _ in tags])

        if cfg.breakouts and o - c >= cfg.breakout_body * (hi - lo):
            for line in an.trendlines:
                if line.kind == "support" and line.broken_at == t:
                    stop = max(hi, line.value_at(t)) + cfg.stop_buffer_atr * a
                    return self._finish("short", "breakout", t, an, stop,
                                        [f"close below {_tf(line)}rising trendline at {px(line.value_at(t))}"])
        return None

    def _finish(self, side: str, setup: str, t: int, an: Analysis, stop: float, reasons: list[str]) -> Signal | None:
        c, floor = self.c[t], self.cfg.min_stop_atr * an.atr
        structure = stop + (1 if side == "long" else -1) * self.cfg.stop_buffer_atr * an.atr   # before buffer/floor
        stop = min(stop, c - floor) if side == "long" else max(stop, c + floor)
        sign, risk = (1 if side == "long" else -1), abs(c - stop)
        if side == "long":
            obstacles = [(z.low, z in an.htf_resistance) for z in an.resistance + an.htf_resistance if z.low > c]
            obstacles += [(b.low, False) for b in an.order_blocks if b.kind == "bearish" and b.low > c]
            obstacles += [(v, line.htf) for line in an.trendlines if line.kind == "resistance" and line.broken_at is None
                          and (v := line.value_at(t)) > c]
        else:
            obstacles = [(z.high, z in an.htf_support) for z in an.support + an.htf_support if z.high < c]
            obstacles += [(b.high, False) for b in an.order_blocks if b.kind == "bullish" and b.high < c]
            obstacles += [(v, line.htf) for line in an.trendlines if line.kind == "support" and line.broken_at is None
                          and (v := line.value_at(t)) < c]
        obstacles.sort(key=lambda o: abs(o[0] - c))
        default = c + sign * self.cfg.default_rr * risk
        nearest = obstacles[0][0] if obstacles else default
        room = nearest   # what reward:risk is judged against
        mode = self.cfg.target
        if mode == "next":
            distinct = [o for o in obstacles if abs(o[0] - nearest) > 0.5 * an.atr]
            target = room = distinct[0][0] if distinct else (default if sign * (default - nearest) > 0 else nearest)
        elif mode == "htf":
            htf = [o for o in obstacles if o[1]]
            target = room = htf[0][0] if htf else nearest
        elif mode == "runner":
            target = c + sign * self.cfg.runner_rr * risk
            target = target if sign * (target - nearest) > 0 else nearest   # never short of the first obstacle
            room = nearest   # the runner still needs min_rr of room before the first obstacle
        else:
            target = nearest
        trend = f"HTF bias {an.htf_bias}" if an.htf_bias is not None else f"bias {an.bias}"
        confirm = self.cfg.confirmation
        trigger = None if confirm == "none" else float(self.h[t] if side == "long" else self.l[t])
        # With confirmation the fill is at (or beyond) the trigger, so judge reward:risk from there.
        # A refined entry comes inside the zone, at or better than the close (the trigger is only
        # the fallback when there are no lower-timeframe candles).
        entry = float(c) if trigger is None or confirm == "refine" else trigger
        zone = None
        if confirm == "refine":   # from the structure that was tagged to the close
            zone = (float(min(structure, c)), float(max(structure, c)))
        signal = Signal(side, setup, t, entry, float(stop), float(target), reasons + [trend], trigger, confirm, zone)
        beyond_target = (entry >= room) if side == "long" else (entry <= room)
        room_rr = abs(room - entry) / abs(entry - stop)
        return signal if not beyond_target and room_rr >= self.cfg.min_rr else None


def _tf(line: Trendline) -> str:
    return "HTF " if line.htf else ""


def analyze(df: pd.DataFrame, cfg: StrategyConfig | None = None, t: int | None = None) -> Analysis:
    """What the strategy sees at bar t (default: the latest bar)."""
    market = Market(df, cfg)
    return market.analyze(len(market.df) - 1 if t is None else t)
