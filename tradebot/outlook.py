"""Outlooks: a weekly one (run on Sunday) and a next-day one (run each evening).

For each market it reports the higher-timeframe trend, the expected range (ATR), classic
pivot points, the key levels above and below price (zones, trendlines projected to the end
of the period, order blocks, pivots - merged into confluence where they cluster) and a
main and an alternative scenario. These are levels and conditions to watch, not predictions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy import Analysis, Market, StrategyConfig
from .structure import px

HORIZONS = {
    # entry candles, how many, higher timeframe (sets the trend and the period of the pivots)
    "day": dict(interval="1h", count=2000, htf="D", name="day"),
    "week": dict(interval="4h", count=3000, htf="W", name="week"),
}


@dataclass
class Level:
    low: float
    high: float
    labels: list[str]

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def text(self) -> str:
        where = px(self.mid) if self.high - self.low < 1e-12 else f"{px(self.low)}-{px(self.high)}"
        tag = "**confluence**: " if len(self.labels) > 1 else ""
        return f"{where} - {tag}{', '.join(self.labels)}"


@dataclass
class Outlook:
    symbol: str
    horizon: str
    period: str                 # e.g. "Thu 8 Oct 2026" or "week of 12 Oct 2026"
    price: float
    expected_range: float       # higher-timeframe ATR
    trend: str                  # higher-timeframe structure
    entry_trend: str
    pivots: dict[str, float]
    above: list[Level]
    below: list[Level]
    scenarios: list[str]
    watch: list[str] = field(default_factory=list)
    market: Market | None = None


def build_outlook(df: pd.DataFrame, symbol: str, horizon: str, now: dt.datetime | None = None) -> Outlook:
    spec = HORIZONS[horizon]
    cfg = StrategyConfig(htf=spec["htf"], pivot_left=5, pivot_right=5, htf_pivot=2)
    market = Market(df, cfg)
    t = len(market.c) - 1
    an = market.analyze(t)
    price = float(market.c[t])
    rng = float(market.htf.atr[-1]) if market.htf is not None and len(market.htf.atr) else float(an.atr)
    if np.isnan(rng):
        rng = float(an.atr) * (24 if horizon == "day" else 30)

    pivots = _pivots(market.df, spec["htf"])
    ahead = int(round(market.htf.bars_per_candle)) if market.htf is not None else 24
    above, below = _levels(an, market, pivots, price, t + ahead, merge=0.15 * rng)
    now = now or dt.datetime.now(dt.timezone.utc)
    period = _period_name(horizon, now)
    out = Outlook(symbol, horizon, period, price, rng, an.htf_bias or an.bias, an.bias, pivots,
                  above[:4], below[:4], [], [], market)
    out.scenarios = _scenarios(out)
    out.watch = _watch_list(an, t, ahead, price, rng)
    if an.signal is not None:
        s = an.signal
        how = f"on a break of {px(s.trigger)}" if s.trigger is not None else "at the next open"
        out.watch.insert(0, f"Live setup now: **{s.side.upper()}** {how}, stop {px(s.stop)}, "
                            f"target {px(s.target)} (R:R {s.rr:.1f}) - {'; '.join(s.reasons)}")
    return out


def _pivots(df: pd.DataFrame, rule: str) -> dict[str, float]:
    """Classic floor pivots from the current (latest) day or week."""
    t = pd.to_datetime(df["time"], utc=True)
    key = t.dt.floor("D") if rule == "D" else t.dt.tz_convert(None).dt.to_period("W").astype(str)
    last = df[key == key.iloc[-1]]
    h, l, c = last["high"].max(), last["low"].min(), last["close"].iloc[-1]
    p = (h + l + c) / 3
    return {"R2": p + (h - l), "R1": 2 * p - l, "P": p, "S1": 2 * p - h, "S2": p - (h - l)}


def _levels(an: Analysis, market: Market, pivots: dict[str, float], price: float, t_end: int,
            merge: float) -> tuple[list[Level], list[Level]]:
    raw: list[Level] = []
    for z in an.support + an.resistance:
        raw.append(Level(z.low, z.high, [f"zone x{z.touches}"]))
    for z in an.htf_support + an.htf_resistance:
        raw.append(Level(z.low, z.high, [f"{'daily' if market.cfg.htf == 'D' else 'weekly'} zone"]))
    for line in an.trendlines:
        if line.broken_at is None:
            v = line.value_at(t_end)
            tf = "HTF " if line.htf else ""
            raw.append(Level(v, v, [f"{tf}{'rising' if line.kind == 'support' else 'falling'} trendline "
                                    f"(x{len(line.touches)}, projected)"]))
    for ob in an.order_blocks:
        raw.append(Level(ob.low, ob.high, [f"{ob.kind} order block"]))
    for name, v in pivots.items():
        raw.append(Level(v, v, [f"pivot {name}"]))

    above = sorted((lv for lv in raw if lv.mid > price), key=lambda lv: lv.mid)
    below = sorted((lv for lv in raw if lv.mid < price), key=lambda lv: -lv.mid)
    return _merge(above, merge), _merge(below, merge)


def _merge(levels: list[Level], gap: float) -> list[Level]:
    """Levels whose middles sit within `gap` of a cluster's first level become one confluence level.
    Measured from the first level, not chained, so a confluence never grows into a wide band."""
    out: list[Level] = []
    anchor = None
    for lv in levels:
        if out and abs(lv.mid - anchor) <= gap:
            prev = out[-1]
            out[-1] = Level(min(prev.low, lv.low), max(prev.high, lv.high), prev.labels + lv.labels)
        else:
            out.append(Level(lv.low, lv.high, list(lv.labels)))
            anchor = lv.mid
    return out


def _scenarios(o: Outlook) -> list[str]:
    def beyond(levels: list[Level], edge: float, up: bool) -> Level | None:
        """The next level past `edge` - where a breakout through it is heading."""
        return next((lv for lv in levels if (lv.mid > edge if up else lv.mid < edge)), None)

    p = lambda lv, attr: px(getattr(lv, attr)) if lv else "-"  # noqa: E731
    r1 = o.above[0] if o.above else None
    s1 = o.below[0] if o.below else None
    r2 = beyond(o.above[1:], r1.high, True) if r1 else None
    s2 = beyond(o.below[1:], s1.low, False) if s1 else None
    confirm = "wait for a rejection candle and enter on a break of its"
    if o.trend == "up":
        return [f"**Main (with the trend): buy pullbacks.** Watch {p(s1, 'mid')} for support; {confirm} high. "
                f"Targets {p(r1, 'mid')}, then {p(r2, 'mid')}.",
                f"**Alternative:** a close below {p(s2 or s1, 'low')} breaks the up-structure. Stand aside "
                f"or look for sells on a retest."]
    if o.trend == "down":
        return [f"**Main (with the trend): sell rallies.** Watch {p(r1, 'mid')} for resistance; {confirm} low. "
                f"Targets {p(s1, 'mid')}, then {p(s2, 'mid')}.",
                f"**Alternative:** a close above {p(r2 or r1, 'high')} breaks the down-structure. Stand aside "
                f"or look for buys on a retest."]
    return [f"**Range:** no clear trend. Sell near {p(r1, 'mid')} / buy near {p(s1, 'mid')}, "
            f"only with a confirmed rejection.",
            f"**Breakout:** a close above {p(r1, 'high')} opens {p(r2, 'mid')}; a close below "
            f"{p(s1, 'low')} opens {p(s2, 'mid')}."]


def _watch_list(an: Analysis, t: int, ahead: int, price: float, rng: float) -> list[str]:
    """Trendlines worth watching this period: near price, and if broken, broken recently."""
    out = []
    for line in an.trendlines:
        now, later = line.value_at(t), line.value_at(t + ahead)
        if abs(now - price) > 2 * rng or (line.broken_at is not None and t - line.broken_at > 2 * ahead):
            continue
        name = f"{'HTF ' if line.htf else ''}{'rising' if line.kind == 'support' else 'falling'} trendline"
        title = name[0].upper() + name[1:]
        if line.broken_at is None:
            side = "above" if line.kind == "resistance" else "below"
            action = "buy" if line.kind == "resistance" else "sell"
            pressing = price > now if line.kind == "resistance" else price < now
            where = f"{title} at {px(now)} (≈{px(later)} by period end, x{len(line.touches)} touches)"
            if pressing:
                out.append(f"{where}: price is already testing it from {side} - a strong close further "
                           f"{side} confirms the break ({action} signal).")
            else:
                out.append(f"{where}: a strong close {side} it is a breakout {action} signal.")
        else:
            role = "support" if line.kind == "resistance" else "resistance"
            ago = t - line.broken_at
            out.append(f"{title} broke {ago} bar{'s' if ago != 1 else ''} ago: a retest near "
                       f"{px(now)} may now act as {role}.")
    return out


def _period_name(horizon: str, now: dt.datetime) -> str:
    if horizon == "day":
        nxt = now + dt.timedelta(days=1)
        return nxt.strftime("%a %d %b %Y")
    monday = now + dt.timedelta(days=(7 - now.weekday()) % 7 or 7)
    return "week of " + monday.strftime("%d %b %Y")


def to_markdown(outlooks: list[Outlook], horizon: str, charts: dict[str, str] | None = None) -> str:
    when = outlooks[0].period if outlooks else ""
    title = "Weekly outlook" if horizon == "week" else "Daily outlook"
    lines = [f"# {title} - {when}", "",
             "_Levels and conditions to watch, not predictions. Always wait for confirmation._", ""]
    for o in outlooks:
        unit = "day" if horizon == "day" else "week"
        lines += [f"## {o.symbol}", "",
                  f"- **Price** {px(o.price)}   **Trend** {o.trend} ({'daily' if horizon == 'day' else 'weekly'} "
                  f"structure), {o.entry_trend} on {'1h' if horizon == 'day' else '4h'}",
                  f"- **Typical {unit} range** ≈ {px(o.expected_range)} "
                  f"({px(o.price - o.expected_range / 2)} - {px(o.price + o.expected_range / 2)} if centred on price)",
                  "- **Pivots** " + "  ".join(f"{k} {px(v)}" for k, v in o.pivots.items()), "",
                  "**Resistance above**", ""]
        lines += [f"{i}. {lv.text()}" for i, lv in enumerate(o.above, 1)] or ["- none found"]
        lines += ["", "**Support below**", ""]
        lines += [f"{i}. {lv.text()}" for i, lv in enumerate(o.below, 1)] or ["- none found"]
        lines += ["", "**Scenarios**", ""] + [f"- {s}" for s in o.scenarios]
        if o.watch:
            lines += ["", "**Watch**", ""] + [f"- {w}" for w in o.watch]
        if charts and o.symbol in charts:
            lines += ["", f"![{o.symbol}]({charts[o.symbol]})"]
        lines.append("")
    return "\n".join(lines)
