"""Screenshots that show how the bot read a market.

technical_screenshot   higher-timeframe chart (trend, zones, trendlines, swing labels) on top,
                       entry-timeframe chart (levels, order blocks, setup with entry/SL/TP) below,
                       and a panel listing every step of the analysis with its result.
fundamental_screenshot economic calendar, currency strength, gold drivers (DXY / US10Y / VIX)
                       and the reasons behind the fundamental bias. Volatility indices get a
                       card explaining why they have no fundamentals.
"""

from __future__ import annotations

import textwrap

import numpy as np

from .fundamentals import Fundamentals
from .plot import DOWN, HTF, INK, LINE, MUTED, SURFACE, UP, Labels, candles, draw_entry, swing_labels
from .strategy import Analysis, Market
from .structure import px

BLUE, RED, GRAY = "#2a78d6", "#e34948", "#f0efec"          # diverging pair + neutral midpoint
HIGH, MEDIUM = "#d03b3b", "#fab219"                         # status: critical / warning
OK, NO, DOT = "✓", "✗", "•"


# -- technical ---------------------------------------------------------------------

def technical_screenshot(market: Market, symbol: str, path: str, entry_tf: str, htf_tf: str,
                         fundamentals: Fundamentals | None = None, title: str = "", last: int = 160) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    an = market.analyze(len(market.c) - 1)
    fig = plt.figure(figsize=(18, 11), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 2, width_ratios=[2.6, 1], height_ratios=[1, 1.35], wspace=0.04, hspace=0.18)
    ax_htf, ax_entry, ax_text = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[:, 1])

    if market.htf is not None:
        _draw_htf(ax_htf, market, an)
        ax_htf.set_title(f"1. Big picture - {htf_tf} candles: trend {an.htf_bias.upper()}", loc="left",
                         fontsize=11, color=INK, fontweight="bold")
    else:
        ax_htf.axis("off")
    draw_entry(ax_entry, market, an, last=last)
    ax_entry.set_title(f"2. Entry chart - {entry_tf} candles: structure {an.bias.upper()}, "
                       f"levels, trendlines, order blocks" + (", setup" if an.signal else ""),
                       loc="left", fontsize=11, color=INK, fontweight="bold")
    _steps_panel(ax_text, market, an, entry_tf, htf_tf, fundamentals)

    stamp = f"   |   last closed candle {market.df.time.iloc[-1]:%a %d %b %Y %H:%M} UTC" \
        if "time" in market.df.columns else ""
    fig.subplots_adjust(top=0.93)
    fig.suptitle(f"{symbol} - technical analysis{(' - ' + title) if title else ''}{stamp}", x=0.01, ha="left",
                 fontsize=14, fontweight="bold", color=INK)
    fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def _draw_htf(ax, market: Market, an: Analysis, show: int = 60) -> None:
    from matplotlib.patches import Rectangle

    htf = market.htf
    bars = htf.bars
    k = len(bars)
    first = max(0, k - show)
    x = np.arange(first, k)
    candles(ax, x, bars.open.to_numpy()[x], bars.high.to_numpy()[x], bars.low.to_numpy()[x],
            bars.close.to_numpy()[x])

    # the candle still forming, from the entry bars after the last completed one
    tail = slice(int(htf.known_at[-1]), an.bar + 1) if len(htf.known_at) else slice(0, an.bar + 1)
    po, ph, pl, pc = market.o[tail][0], market.h[tail].max(), market.l[tail].min(), market.c[an.bar]
    candles(ax, np.array([k]), np.array([po]), np.array([ph]), np.array([pl]), np.array([pc]), alpha=0.45)
    ax.annotate("forming", (k, ph), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7, color=MUTED)

    right = k + 4
    lo = min(bars.low.to_numpy()[x].min(), pl)
    hi = max(bars.high.to_numpy()[x].max(), ph)
    pad = (hi - lo) * 0.06
    labels = Labels()
    for z, colour in [(z, UP) for z in an.htf_support[:2]] + [(z, DOWN) for z in an.htf_resistance[:2]]:
        ax.add_patch(Rectangle((first - 1, z.low), right - first + 1, z.high - z.low, facecolor=colour,
                               alpha=0.14, linewidth=0, zorder=1))
        labels.add(z.mid, f"{px(z.mid)} zone x{z.touches}", colour)

    def to_x(i: int) -> int:   # entry-bar index -> higher-timeframe candle index
        return int(np.searchsorted(htf.known_at, i, side="right"))

    for line in (ln for ln in an.trendlines if ln.htf):
        # Drawn straight through its two anchor swings on this chart, as you would by hand.
        x1, x2 = to_x(line.i1), to_x(line.i2)
        if x2 == x1:
            continue
        slope = (line.p2 - line.p1) / (x2 - x1)
        end_x = k + 3 if line.broken_at is None else to_x(line.broken_at)
        xs = np.array([max(first, x1), end_x])
        ax.plot(xs, line.p1 + slope * (xs - x1), color=HTF, linewidth=2, zorder=4)
        touch_x = [to_x(i) for i in line.touches if to_x(i) >= first]
        ax.scatter(touch_x, [line.p1 + slope * (tx - x1) for tx in touch_x], s=46, facecolors="white",
                   edgecolors=HTF, linewidths=1.4, zorder=5)
        if line.broken_at is not None:
            ax.scatter(end_x, line.p1 + slope * (end_x - x1), marker="D", s=36, color=HTF, zorder=5)
        state = "broken" if line.broken_at is not None else "intact"
        labels.add(line.p1 + slope * (end_x - x1), f"TL x{len(line.touches)} {state}", HTF)

    for p, tag in swing_labels([p for p in htf.pivots_known_at(an.bar) if to_x(p.index) >= first]):
        above = p.kind == "high"
        ax.annotate(tag, (to_x(p.index), p.price), xytext=(0, 7 if above else -7), textcoords="offset points",
                    ha="center", va="bottom" if above else "top", fontsize=8, color=INK, fontweight="bold")

    labels.draw(ax, right + 1, lo - pad, hi + pad)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(first - 1, right + max(10, show // 5))
    ax.grid(alpha=0.15)
    ax.set_facecolor(SURFACE)


def _steps_panel(ax, market: Market, an: Analysis, entry_tf: str, htf_tf: str,
                 fundamentals: Fundamentals | None) -> None:
    ax.axis("off")
    ax.set_facecolor(SURFACE)
    steps = analysis_steps(market, an, entry_tf, htf_tf, fundamentals)
    y = 0.99
    ax.text(0, y, "How the bot read it", fontsize=13, fontweight="bold", color=INK, va="top",
            transform=ax.transAxes)
    y -= 0.045
    for mark, head, body in steps:
        colour = {OK: UP, NO: DOWN}.get(mark, MUTED)
        ax.text(0, y, mark, fontsize=12, color=colour, va="top", transform=ax.transAxes, fontweight="bold")
        ax.text(0.07, y, head, fontsize=10.5, color=INK, va="top", transform=ax.transAxes, fontweight="bold")
        y -= 0.03
        for line in textwrap.wrap(body, 52):
            ax.text(0.07, y, line, fontsize=9, color=MUTED, va="top", transform=ax.transAxes)
            y -= 0.024
        y -= 0.014


def analysis_steps(market: Market, an: Analysis, entry_tf: str, htf_tf: str,
                   fundamentals: Fundamentals | None = None) -> list[tuple[str, str, str]]:
    """Each step of the analysis as (mark, heading, explanation)."""
    steps = []
    a = an.atr

    def why(pivots) -> str:
        highs = [p.price for p in pivots if p.kind == "high"][-2:]
        lows = [p.price for p in pivots if p.kind == "low"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "not enough swings yet"
        h = "higher high" if highs[1] > highs[0] else "lower high"
        lw = "higher low" if lows[1] > lows[0] else "lower low"
        return f"last swings: {h} {px(highs[1])} (prev {px(highs[0])}), {lw} {px(lows[1])} (prev {px(lows[0])})"

    if market.htf is not None:
        steps.append((OK if an.htf_bias != "neutral" else DOT, f"Trend on {htf_tf}: {an.htf_bias}",
                      why(market.htf.pivots_known_at(an.bar)) + ". Trades only go with this direction."))
    steps.append((DOT, f"Structure on {entry_tf}: {an.bias}", why(market.pivots_known_at(an.bar)) + "."))

    sup = (an.support[:1] + an.htf_support[:1])
    res = (an.resistance[:1] + an.htf_resistance[:1])
    near_s = max(sup, key=lambda z: z.high) if sup else None
    near_r = min(res, key=lambda z: z.low) if res else None
    loc = []
    for z, kind, dist in ((near_r, "resistance", (near_r.low - an.price) / a if near_r else 0),
                          (near_s, "support", (an.price - near_s.high) / a if near_s else 0)):
        if z is None:
            continue
        span = f"{kind} {px(z.low)}-{px(z.high)}"
        loc.append(f"price is inside {span}" if dist <= 0.05 else
                   f"{span} is {dist:.1f} ATR {'above' if kind == 'resistance' else 'below'}")
    at_level = any(abs(d) <= 0.5 for d in [(near_r.low - an.price) / a if near_r else 9,
                                             (an.price - near_s.high) / a if near_s else 9])
    steps.append((OK if at_level else DOT, f"Price {px(an.price)}: {'at a level' if at_level else 'between levels'}",
                  "; ".join(loc) + "." if loc else "No support/resistance zones nearby."))

    tl = []
    for line in an.trendlines:
        name = f"{'HTF ' if line.htf else ''}{'rising' if line.kind == 'support' else 'falling'}"
        state = "intact" if line.broken_at is None else f"broken {an.bar - line.broken_at} bars ago"
        tl.append(f"{name} line at {px(line.value_at(an.bar))}, {len(line.touches)} touches, {state}")
    steps.append((DOT, f"Trendlines: {len(an.trendlines)}", "; ".join(tl) + "." if tl else "No clean trendline."))

    obs = sorted(an.order_blocks, key=lambda b: abs((b.low + b.high) / 2 - an.price))[:2]
    steps.append((DOT, f"Order blocks: {len(an.order_blocks)} active",
                  "; ".join(f"{b.kind} {px(b.low)}-{px(b.high)}" for b in obs) + "." if obs else "None active."))

    s = an.signal
    if s is not None:
        trig = {"break": f"enter only if price breaks {px(s.trigger)} within {market.cfg.confirm_bars} candles",
                "close": f"enter after a candle closes beyond {px(s.trigger)}",
                "none": "enter at the next open"}[s.confirmation]
        steps.append((OK, f"Setup: {s.side.upper()} ({s.setup})", "; ".join(r for r in s.reasons) + "."))
        steps.append((OK, "Confirmation", trig + f". Cancel if {px(s.stop)} trades first."))
        steps.append((OK, f"Risk: stop {px(s.stop)}, target {px(s.target)}",
                      f"reward:risk {s.rr:.1f} (minimum {market.cfg.min_rr:g})."))
    else:
        steps.append((NO, "Setup: none",
                      f"needs {market.cfg.min_confluence}+ levels tagged with a rejection candle (or a trendline "
                      f"break) in the {an.direction} direction" if an.direction != "neutral" else
                      f"needs {market.cfg.min_confluence}+ levels tagged with a rejection candle, or a trendline "
                      f"break. Wait for price to reach a level."))

    if fundamentals is not None:
        if not fundamentals.applicable:
            steps.append((DOT, "Fundamentals: none (synthetic index)", "Random by design - technicals only."))
        else:
            warn = (" " + fundamentals.warnings[0]) if fundamentals.warnings else ""
            steps.append((DOT, f"Fundamentals: {fundamentals.bias} (score {fundamentals.score:+.1f})",
                          (fundamentals.reasons[0] if fundamentals.reasons else "") + warn))

    steps.append(verdict(an, fundamentals))
    return steps


def verdict(an: Analysis, f: Fundamentals | None) -> tuple[str, str, str]:
    s = an.signal
    fbias = f.bias if f is not None and f.applicable else None
    tech = s.side if s else {"up": "long", "down": "short"}.get(an.direction)
    agree = fbias is None or fbias == "neutral" or tech is None or \
        (tech == "long") == (fbias == "bullish")
    if s is None:
        lean = f"Lean {tech} on pullbacks" if tech else "No directional edge"
        extra = "" if agree or fbias is None else f" - but fundamentals are {fbias}: be selective."
        return DOT, "Verdict: wait", f"{lean}; wait for price at a level and a confirmed setup{extra}"
    if agree:
        text = "Technical setup" + (f" and {fbias} fundamentals agree" if fbias and fbias != "neutral" else "") + \
               ". Take it only if the confirmation triggers; manage with the plan."
        return OK, f"Verdict: {s.side.upper()} if confirmed", text
    return NO, "Verdict: conflict", f"The setup is {s.side} but fundamentals are {fbias}. Skip it or use half size."


# -- fundamental -------------------------------------------------------------------

def fundamental_screenshot(f: Fundamentals, path: str, horizon: str, period: str = "") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    title = f"{f.symbol.removeprefix('frx')} - fundamental analysis{(' - ' + period) if period else ''}"
    if not f.applicable:
        fig, ax = plt.subplots(figsize=(12, 3.2), facecolor=SURFACE)
        ax.axis("off")
        ax.text(0, 0.95, title, fontsize=14, fontweight="bold", color=INK, va="top", transform=ax.transAxes)
        ax.text(0, 0.7, "\n".join(textwrap.wrap(f.reasons[0], 110)), fontsize=11, color=MUTED, va="top",
                transform=ax.transAxes)
        fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=SURFACE)
        plt.close(fig)
        return

    gold = bool(f.drivers)
    fig = plt.figure(figsize=(18, 11 if gold else 7), facecolor=SURFACE)
    rows = [1.25, 0.8, 0.75] if gold else [1.25, 0.6]
    grid = fig.add_gridspec(len(rows), 3, height_ratios=rows, hspace=0.45, wspace=0.25)
    _calendar_table(fig.add_subplot(grid[0, :2]), f, horizon)
    _strength_bars(fig.add_subplot(grid[0, 2]), f)
    if gold:
        for i, (name, series) in enumerate(f.drivers.items()):
            _driver(fig.add_subplot(grid[1, i]), name, series)
    _reasons(fig.add_subplot(grid[-1, :]), f)
    fig.subplots_adjust(top=0.9)
    fig.suptitle(title, x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def _calendar_table(ax, f: Fundamentals, horizon: str) -> None:
    ax.axis("off")
    ax.set_title(f"Economic calendar - {'tomorrow' if horizon == 'day' else 'this week'} "
                 f"(high and medium impact, UTC)", loc="left", fontsize=11, fontweight="bold", color=INK)
    if not f.events:
        ax.text(0, 0.85, "No high or medium impact events for these currencies in the period.",
                fontsize=10, color=MUTED, transform=ax.transAxes)
        return
    rows = f.events[:14]
    y, step = 0.93, min(0.065, 0.9 / (len(rows) + 1))
    for col, head in ((0.0, "When"), (0.16, "Ccy"), (0.23, "Impact"), (0.35, "Event"), (0.78, "Forecast"),
                      (0.9, "Previous")):
        ax.text(col, y, head, fontsize=9, color=MUTED, fontweight="bold", transform=ax.transAxes)
    for e in rows:
        y -= step
        colour = HIGH if e.impact == "High" else MEDIUM
        ax.text(0.0, y, f"{e.when:%a %d %H:%M}", fontsize=9, color=INK, transform=ax.transAxes)
        ax.text(0.16, y, e.currency, fontsize=9, color=INK, fontweight="bold", transform=ax.transAxes)
        ax.add_patch(matplotlib_rect(ax, 0.23, y - 0.008, 0.012, step * 0.6, colour))
        ax.text(0.247, y, e.impact, fontsize=9, color=INK, transform=ax.transAxes)
        ax.text(0.35, y, textwrap.shorten(e.title, 52), fontsize=9, color=INK, transform=ax.transAxes)
        ax.text(0.78, y, e.forecast or "-", fontsize=9, color=MUTED, transform=ax.transAxes)
        ax.text(0.9, y, e.previous or "-", fontsize=9, color=MUTED, transform=ax.transAxes)


def matplotlib_rect(ax, x, y, w, h, colour):
    from matplotlib.patches import Rectangle
    return Rectangle((x, y), w, h, facecolor=colour, transform=ax.transAxes, linewidth=0)


def _strength_bars(ax, f: Fundamentals) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title("Currency strength vs the majors (%)", loc="left", fontsize=11, fontweight="bold", color=INK)
    if not f.strength:
        ax.axis("off")
        ax.text(0, 0.5, "Not available", color=MUTED, transform=ax.transAxes)
        return
    items = list(f.strength.items())[::-1]   # strongest at the top
    names, values = [k for k, _ in items], [v for _, v in items]
    involved = set(f.symbol.removeprefix("frx")[i:i + 3] for i in (0, 3))
    colours = [BLUE if v > 0.02 else RED if v < -0.02 else GRAY for v in values]
    ax.barh(names, values, color=colours, height=0.6, zorder=3)
    ax.axvline(0, color=MUTED, linewidth=0.8)
    span = max(abs(v) for v in values) or 1
    for i, v in enumerate(values):
        ax.text(v + (span * 0.04 if v >= 0 else -span * 0.04), i, f"{v:+.2f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=8, color=INK)
    for label in ax.get_yticklabels():
        if label.get_text() in involved:
            label.set_fontweight("bold")
            label.set_color(INK)
        else:
            label.set_color(MUTED)
    ax.set_xlim(-span * 1.35, span * 1.35)
    ax.tick_params(axis="x", colors=MUTED, labelsize=8)
    ax.grid(axis="x", alpha=0.15)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _driver(ax, name: str, series) -> None:
    ax.set_facecolor(SURFACE)
    unit = "yield %" if name == "US10Y" else "index"
    change = (series.iloc[-1] - series.iloc[-6]) * 100 if name == "US10Y" else \
        (series.iloc[-1] / series.iloc[-6] - 1) * 100
    effect = {"DXY": "rising = bad for gold", "US10Y": "rising = bad for gold", "VIX": "rising = good for gold"}[name]
    ax.plot(range(len(series)), series.to_numpy(), color=BLUE, linewidth=2)
    ax.scatter(len(series) - 1, series.iloc[-1], color=BLUE, s=30, zorder=3)
    ax.set_title(f"{name} {series.iloc[-1]:.2f}  ({change:+.0f}bp 5d)" if name == "US10Y" else
                 f"{name} {series.iloc[-1]:.2f}  ({change:+.1f}% 5d)", loc="left", fontsize=10.5,
                 fontweight="bold", color=INK)
    ax.text(0, -0.2, f"{unit}, last {len(series)} days - {effect}", transform=ax.transAxes, fontsize=8, color=MUTED)
    ax.set_xticks([])
    ax.tick_params(axis="y", colors=MUTED, labelsize=8)
    ax.grid(alpha=0.15)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _reasons(ax, f: Fundamentals) -> None:
    ax.axis("off")
    colour = {"bullish": UP, "bearish": DOWN}.get(f.bias, MUTED)
    ax.text(0, 1.0, f"Fundamental bias: {f.bias.upper()}  (score {f.score:+.2f})", fontsize=13, fontweight="bold",
            color=colour, va="top", transform=ax.transAxes)
    y = 0.8
    for r in f.reasons + [f"Warning: {w}" for w in f.warnings]:
        for i, line in enumerate(textwrap.wrap(r, 150)):
            ax.text(0.01, y, ("- " if i == 0 else "  ") + line, fontsize=9.5, color=INK if i == 0 else MUTED,
                    va="top", transform=ax.transAxes)
            y -= 0.11
    ax.text(0.01, max(y - 0.02, -0.15), "Score: currency strength +/-1, gold drivers +/-0.5 each, your rates and "
            "views in fundamentals.json +/-0.5. Bullish >= +0.75, bearish <= -0.75.", fontsize=8, color=MUTED,
            va="top", transform=ax.transAxes)
