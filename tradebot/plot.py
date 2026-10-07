"""A clean chart: only the levels that matter at the latest bar, plus the trades.

To keep the chart readable it draws the nearest two zones on each side of price,
the nearest higher-timeframe zone on each side, the current trendlines, the active
order blocks closest to price, the swing labels (HH/HL/LH/LL) and, when there is a
setup, its entry, stop-loss and take-profit - not every level ever detected.
"""

from __future__ import annotations

import numpy as np

from .backtest import BacktestResult
from .strategy import Analysis, Market
from .structure import Pivot, px

UP, DOWN, LINE, HTF = "#2a9d8f", "#e76f51", "#264653", "#6d597a"
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


def swing_labels(pivots: list[Pivot]) -> list[tuple[Pivot, str]]:
    """HH / LH for swing highs and HL / LL for swing lows, each against the previous one."""
    out, last = [], {"high": None, "low": None}
    for p in sorted(pivots, key=lambda p: p.index):
        prev = last[p.kind]
        if prev is not None:
            if p.kind == "high":
                out.append((p, "HH" if p.price > prev.price else "LH"))
            else:
                out.append((p, "HL" if p.price > prev.price else "LL"))
        last[p.kind] = p
    return out


def candles(ax, x, o, h, l, c, alpha=1.0, width=0.7) -> None:
    up = c >= o
    ax.vlines(x, l, h, color="#888", linewidth=0.6, zorder=2, alpha=alpha)
    for mask, colour in ((up, UP), (~up, DOWN)):
        ax.bar(x[mask], (c - o)[mask], bottom=o[mask], color=colour, width=width, zorder=3, alpha=alpha)


class Labels:
    """Right-margin labels, nudged apart so they never overlap."""

    def __init__(self):
        self.items: list[tuple[float, str, str]] = []

    def add(self, y: float, text: str, colour: str) -> None:
        self.items.append((y, text, colour))

    def draw(self, ax, x: float, lo: float, hi: float) -> None:
        gap, placed = (hi - lo) * 0.035, []
        for y, text, colour in sorted(i for i in self.items if lo <= i[0] <= hi):
            y_text = max(y, placed[-1] + gap) if placed else max(y, lo + gap / 2)
            if y_text > hi - gap / 2:
                break   # no room left at the top edge
            placed.append(y_text)
            ax.text(x, y_text, text, va="center", fontsize=8, color=colour)


def draw_entry(ax, market: Market, an: Analysis, last: int = 300, result: BacktestResult | None = None,
               show_swings: bool = True) -> None:
    """The entry-timeframe chart with the analysis drawn on it."""
    from matplotlib.patches import Rectangle

    n = an.bar + 1
    start = max(0, n - last)
    x = np.arange(start, n)
    candles(ax, x, market.o[x], market.h[x], market.l[x], market.c[x])
    right = n + max(6, (n - start) // 14)    # lines and boxes are projected up to here; labels go after it
    labels = Labels()

    lo, hi = market.l[x].min(), market.h[x].max()
    s = an.signal
    if s is not None:   # make sure the whole trade plan is on screen
        lo, hi = min(lo, s.stop, s.target), max(hi, s.stop, s.target)
    pad = (hi - lo) * 0.05

    for zones, colour in ((an.support[:2], UP), (an.resistance[:2], DOWN)):
        for z in zones:
            ax.add_patch(Rectangle((start - 1, z.low), right - start + 1, z.high - z.low, facecolor=colour,
                                   alpha=0.13, linewidth=0, zorder=1))
            labels.add(z.mid, f"{px(z.mid)} zone x{z.touches}", colour)
    for z in an.htf_support[:1] + an.htf_resistance[:1]:
        ax.add_patch(Rectangle((start - 1, z.low), right - start + 1, z.high - z.low, facecolor="none",
                               edgecolor=HTF, linewidth=1.5, zorder=1))
        labels.add(z.mid, f"HTF zone {px(z.mid)}", HTF)

    for line in an.trendlines:
        if line.broken_at is not None and line.broken_at < start:
            continue   # broke before the visible window: nothing of it would be on screen
        colour, width = (HTF, 2.2) if line.htf else (LINE, 1.4)
        end = right if line.broken_at is None else line.broken_at
        xs = np.array([max(line.i1, start), end])
        ax.plot(xs, [line.value_at(i) for i in xs], color=colour, linewidth=width, zorder=4, solid_capstyle="round")
        touches = [i for i in line.touches if i >= start]
        ax.scatter(touches, [line.value_at(i) for i in touches], s=46, facecolors="white",
                   edgecolors=colour, linewidths=1.4, zorder=5)
        if line.broken_at is not None:
            window = market.cfg.retest_window * (market.htf.bars_per_candle if line.htf else 1)
            window = min(window, (n - start) / 4)
            xs = np.array([line.broken_at, min(right, int(line.broken_at + window))])
            ax.plot(xs, [line.value_at(i) for i in xs], color=colour, linewidth=width * 0.7,
                    linestyle=(0, (2, 3)), zorder=4)
            ax.scatter(line.broken_at, line.value_at(line.broken_at), marker="D", s=36, color=colour, zorder=5)
        state = "" if line.broken_at is None else " broken"
        labels.add(line.value_at(xs[-1]), f"{'HTF ' if line.htf else ''}TL x{len(line.touches)}{state}", colour)

    for kind, colour in (("bullish", UP), ("bearish", DOWN)):
        nearest = sorted((b for b in an.order_blocks if b.kind == kind),
                         key=lambda b: abs((b.low + b.high) / 2 - an.price))[:1]
        for ob in nearest:
            left = max(ob.index, start)
            ax.add_patch(Rectangle((left, ob.low), right - left, ob.high - ob.low, facecolor=colour,
                                   edgecolor=colour, alpha=0.25, hatch="//", zorder=1))
            labels.add((ob.low + ob.high) / 2, f"{kind} OB", colour)

    if show_swings:
        for p, tag in swing_labels([p for p in market.pivots_known_at(an.bar) if p.index >= start]):
            above = p.kind == "high"
            ax.annotate(tag, (p.index, p.price), xytext=(0, 7 if above else -7), textcoords="offset points",
                        ha="center", va="bottom" if above else "top", fontsize=7, color=MUTED, zorder=6)

    if s is not None:
        entry = s.trigger if s.trigger is not None else s.entry
        x0, width = n - 0.5, right - n + 0.5
        ax.add_patch(Rectangle((x0, min(entry, s.target)), width, abs(s.target - entry), facecolor=UP,
                               alpha=0.18, edgecolor=UP, linewidth=1, zorder=2))
        ax.add_patch(Rectangle((x0, min(entry, s.stop)), width, abs(s.stop - entry), facecolor=DOWN,
                               alpha=0.18, edgecolor=DOWN, linewidth=1, zorder=2))
        verb = "BUY" if s.side == "long" else "SELL"
        order = {"break": f"{verb} STOP", "close": f"{verb} on close", "none": verb}[s.confirmation]
        labels.add(entry, f"{order} {px(entry)}", INK)
        labels.add(s.stop, f"SL {px(s.stop)}", DOWN)
        labels.add(s.target, f"TP {px(s.target)}  R:R {s.rr:.1f}", UP)
        ax.scatter(an.bar, market.c[an.bar], marker="^" if s.side == "long" else "v", s=90,
                   color=UP if s.side == "long" else DOWN, edgecolors="white", zorder=7)

    if result:
        for tr in result.trades:
            if tr.entry_bar < start:
                continue
            colour = UP if tr.side == "long" else DOWN
            ax.scatter(tr.entry_bar, tr.entry, marker="^" if tr.side == "long" else "v", color=colour, s=60, zorder=6)
            if tr.exit_bar is not None:
                ax.plot([tr.entry_bar, tr.exit_bar], [tr.entry, tr.exit], color=colour, linewidth=1, zorder=6)
                ax.scatter(tr.exit_bar, tr.exit, marker="x", color="black" if tr.pnl > 0 else "red", s=40, zorder=6)

    labels.draw(ax, right + 2, lo - pad, hi + pad)
    ax.set_ylim(lo - pad, hi + pad)   # levels far from the visible price are not worth zooming out for
    ax.set_xlim(start - 1, right + max(16, (n - start) // 6))
    ax.grid(alpha=0.15)
    ax.set_facecolor(SURFACE)


def plot(market: Market, path: str, result: BacktestResult | None = None, last: int = 300) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    an = market.analyze(len(market.c) - 1)
    fig, ax = plt.subplots(figsize=(15, 7))
    draw_entry(ax, market, an, last, result)
    trend = f"entry bias {an.bias}" + (f", HTF bias {an.htf_bias}" if an.htf_bias else "")
    ax.set_title(f"{trend}   |   shaded: zones   outlined: HTF zones   TL: trendline (o = touch, "
                 f"dotted after break)   hatched: order block", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
