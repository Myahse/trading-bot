"""Every zone the bot could trade from, with its plan, and where the market may go next.

For each support zone (a long) and resistance zone (a short) near price, on either timeframe,
the plan is what the bot would do if price came back to it: an order at the zone's near edge
(after a rejection candle there), the stop beyond the zone, and the target at the next obstacle.
A zone is *tradable* when that plan goes with the higher-timeframe trend and pays at least the
minimum reward:risk. Zones on the higher timeframe that overlap an entry-chart zone are merged
into it and make it *HTF-backed*, the strongest kind.

Scalp charts show the tradable zones nearest to price; swing charts the single best one (`zone_view`).
These are plans to wait for, not orders: an entry still needs the rejection candle and its
confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .strategy import Analysis, Market
from .structure import Zone, px


@dataclass(frozen=True)
class ZonePlan:
    zone: Zone
    side: str            # "long" at support, "short" at resistance
    htf: bool            # a higher-timeframe zone with no entry-chart zone on it
    htf_backed: bool     # an entry-chart zone that sits on a higher-timeframe zone
    entry: float         # where the order would sit: the zone's near edge
    stop: float
    target: float
    tradable: bool
    why_not: str         # why it is not tradable ("" when it is)

    @property
    def rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)

    @property
    def label(self) -> str:
        kind = "BUY" if self.side == "long" else "SELL"
        tf = " (HTF)" if self.htf else (" + HTF" if self.htf_backed else "")
        return f"{kind} ZONE{tf} {px(self.zone.low)}-{px(self.zone.high)}"


def _overlaps(a: Zone, b: Zone) -> bool:
    return a.low <= b.high and b.low <= a.high


def plan_zones(market: Market, an: Analysis, max_atr: float = 8.0) -> list[ZonePlan]:
    """Plans for the zones within `max_atr` ATRs of price, best first: tradable, HTF-backed, nearest."""
    cfg, a, c, t = market.cfg, an.atr, an.price, an.bar
    if a != a:   # ATR not ready
        return []
    plans = []
    for side, zones, htf_zones in (("long", an.support, an.htf_support), ("short", an.resistance, an.htf_resistance)):
        candidates = [(z, False, any(_overlaps(z, h) for h in htf_zones)) for z in zones]
        candidates += [(h, True, False) for h in htf_zones if not any(_overlaps(h, z) for z in zones)]
        for z, htf, backed in candidates:
            near = z.high if side == "long" else z.low
            if abs(c - near) > max_atr * a:
                continue
            entry = near
            stop = z.low - cfg.stop_buffer_atr * a if side == "long" else z.high + cfg.stop_buffer_atr * a
            floor = cfg.min_stop_atr * a
            stop = min(stop, entry - floor) if side == "long" else max(stop, entry + floor)
            target = _target(an, side, entry, stop, t, cfg.default_rr)
            rr = abs(target - entry) / abs(entry - stop)
            why = ""
            if cfg.trend_filter and an.direction == ("down" if side == "long" else "up"):
                why = f"against the {an.direction}trend"
            elif rr < cfg.min_rr:
                why = f"R:R {rr:.1f} (min {cfg.min_rr:g})"
            plans.append(ZonePlan(z, side, htf, backed, float(entry), float(stop), float(target), not why, why))
    return sorted(plans, key=lambda p: (not p.tradable, not p.htf_backed, abs(c - p.entry)))


def _target(an: Analysis, side: str, entry: float, stop: float, t: int, default_rr: float) -> float:
    """The nearest obstacle beyond the entry, as for a live setup (strategy._finish)."""
    if side == "long":
        obstacles = [z.low for z in an.resistance + an.htf_resistance if z.low > entry]
        obstacles += [b.low for b in an.order_blocks if b.kind == "bearish" and b.low > entry]
        obstacles += [v for ln in an.trendlines if ln.kind == "resistance" and ln.broken_at is None
                      and (v := ln.value_at(t)) > entry]
        return min(obstacles) if obstacles else entry + default_rr * (entry - stop)
    obstacles = [z.high for z in an.support + an.htf_support if z.high < entry]
    obstacles += [b.high for b in an.order_blocks if b.kind == "bullish" and b.high < entry]
    obstacles += [v for ln in an.trendlines if ln.kind == "support" and ln.broken_at is None
                  and (v := ln.value_at(t)) < entry]
    return max(obstacles) if obstacles else entry - default_rr * (stop - entry)


def shown_plans(plans: list[ZonePlan], view: str, price: float, atr: float, limit: int = 3,
                faded_atr: float = 2.0) -> list[ZonePlan]:
    """The plans a chart draws: the best one ("best"), or the `limit` tradable zones nearest to
    price plus the untradable ones within `faded_atr` ATRs ("all") - far zones are not actionable."""
    if view == "best":
        return [p for p in [best_plan(plans)] if p is not None]
    near = sorted((p for p in plans if p.tradable), key=lambda p: abs(price - p.entry))[:limit]
    return [p for p in plans if p in near or (not p.tradable and abs(price - p.entry) <= faded_atr * atr)]


def best_plan(plans: list[ZonePlan]) -> ZonePlan | None:
    """The zone the bot would trade next: tradable, HTF-backed first, then nearest to price."""
    return next((p for p in plans if p.tradable), None)


def scenarios(an: Analysis, plans: list[ZonePlan], htf_name: str = "HTF") -> tuple[str, str]:
    """(main, alternative): how the market may evolve from here, in levels to watch."""
    trend = an.direction
    best = best_plan(plans)
    sup = [p for p in plans if p.side == "long"]
    res = [p for p in plans if p.side == "short"]
    nearest = lambda ps: min(ps, key=lambda p: abs(an.price - p.entry)) if ps else None   # noqa: E731
    if trend in ("up", "down") and best is not None:
        up = trend == "up"
        main = (f"{htf_name} {trend}trend: expect a {'pullback' if up else 'rally'} into the "
                f"{'buy' if up else 'sell'} zone {px(best.zone.low)}-{px(best.zone.high)}, a rejection there, then a "
                f"move to {px(best.target)}. The plan fails on a close {'below' if up else 'above'} {px(best.stop)}.")
        beyond = [p for p in (res if up else sup) if (p.zone.low > an.price if up else p.zone.high < an.price)]
        brk = nearest([p for p in (sup if up else res) if p is not best and
                       (p.entry < best.stop if up else p.entry > best.stop)])
        alt = (f"A close {'below' if up else 'above'} {px(best.stop)} breaks the {trend}trend structure: stand aside"
               + (f"; the next {'support' if up else 'resistance'} is {px(brk.zone.low)}-{px(brk.zone.high)}."
                  if brk else ".")
               + (f" Above {px(nearest(beyond).zone.high)} the move can extend." if up and beyond else "")
               + (f" Below {px(nearest(beyond).zone.low)} the move can extend." if not up and beyond else ""))
        return main, alt
    if trend in ("up", "down"):
        return (f"{htf_name} {trend}trend, but no {'buy' if trend == 'up' else 'sell'} zone near price pays "
                f"enough: wait for a new swing to form a zone.",
                "Do not trade against the trend from the zones on the other side.")
    s, r = nearest(sup), nearest(res)
    main = ("No clear trend: range trading only, from "
            + (f"support {px(s.zone.low)}-{px(s.zone.high)}" if s else "-") + " and "
            + (f"resistance {px(r.zone.low)}-{px(r.zone.high)}" if r else "-") + ", with a confirmed rejection.")
    alt = ("A close outside the range sets the next trend"
           + (f": above {px(r.zone.high)} favours buys" if r else "")
           + (f", below {px(s.zone.low)} favours sells" if s else "") + ".")
    return main, alt
