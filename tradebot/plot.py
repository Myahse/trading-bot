"""A clean chart: only the levels that matter at the latest bar, plus the trades.

To keep the chart readable it draws the nearest two zones on each side of price,
the nearest higher-timeframe zone on each side, the current trendlines, and the
active order blocks closest to price - not every level ever detected.
"""

from __future__ import annotations

import numpy as np

from .backtest import BacktestResult
from .strategy import Market

UP, DOWN, LINE, HTF = "#2a9d8f", "#e76f51", "#264653", "#6d597a"


def plot(market: Market, path: str, result: BacktestResult | None = None, last: int = 300) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    n = len(market.c)
    start = max(0, n - last)
    x = np.arange(start, n)
    an = market.analyze(n - 1)

    fig, ax = plt.subplots(figsize=(15, 7))
    up = market.c[x] >= market.o[x]
    ax.vlines(x, market.l[x], market.h[x], color="#888", linewidth=0.6, zorder=2)
    for mask, colour in ((up, UP), (~up, DOWN)):
        ax.bar(x[mask], (market.c - market.o)[x][mask], bottom=market.o[x][mask], color=colour, width=0.7, zorder=3)

    lo, hi = market.l[x].min(), market.h[x].max()
    pad = (hi - lo) * 0.05

    labels: list[tuple[float, str, str]] = []

    def label(y, text, colour):
        if lo - pad <= y <= hi + pad:
            labels.append((y, text, colour))

    for zones, colour in ((an.support[:2], UP), (an.resistance[:2], DOWN)):
        for z in zones:
            ax.axhspan(z.low, z.high, color=colour, alpha=0.15, zorder=1)
            label(z.mid, f"{z.mid:.5g}", colour)
    for z in an.htf_support[:1] + an.htf_resistance[:1]:
        ax.axhspan(z.low, z.high, facecolor="none", edgecolor=HTF, linewidth=1.5, zorder=1)
        label(z.mid, f"HTF {z.mid:.5g}", HTF)

    for line in an.trendlines:
        end = n - 1 if line.broken_at is None else line.broken_at
        xs = np.array([max(line.i1, start), end])
        ax.plot(xs, [line.value_at(i) for i in xs], color=LINE, linewidth=1.2, zorder=4)
        if line.broken_at is not None:  # projection after the break, where a retest would happen
            xs = np.array([line.broken_at, n - 1])
            ax.plot(xs, [line.value_at(i) for i in xs], color=LINE, linewidth=1, linestyle=":", zorder=4)
            ax.scatter(line.broken_at, line.value_at(line.broken_at), color=LINE, s=30, zorder=5)

    for kind, colour in (("bullish", UP), ("bearish", DOWN)):
        nearest = sorted((b for b in an.order_blocks if b.kind == kind),
                         key=lambda b: abs((b.low + b.high) / 2 - an.price))[:1]
        for ob in nearest:
            left = max(ob.index, start)
            ax.add_patch(Rectangle((left, ob.low), n - 1 - left, ob.high - ob.low, facecolor=colour,
                                   edgecolor=colour, alpha=0.25, hatch="//", zorder=1))
            label((ob.low + ob.high) / 2, "OB", colour)

    if result:
        for tr in result.trades:
            if tr.entry_bar < start:
                continue
            colour = UP if tr.side == "long" else DOWN
            ax.scatter(tr.entry_bar, tr.entry, marker="^" if tr.side == "long" else "v", color=colour, s=60, zorder=6)
            if tr.exit_bar is not None:
                ax.plot([tr.entry_bar, tr.exit_bar], [tr.entry, tr.exit], color=colour, linewidth=1, zorder=6)
                ax.scatter(tr.exit_bar, tr.exit, marker="x", color="black" if tr.pnl > 0 else "red", s=40, zorder=6)

    # Labels sit in a margin right of the last candle, nudged apart so they never overlap.
    gap, placed = (hi - lo + 2 * pad) * 0.03, []
    for y, text, colour in sorted(labels):
        y_text = max(y, placed[-1] + gap) if placed else y
        placed.append(y_text)
        ax.text(n + 1, y_text, text, va="center", fontsize=8, color=colour)
    ax.set_ylim(lo - pad, hi + pad)   # levels far from the visible price are not worth zooming out for
    ax.set_xlim(start - 1, n + max(10, (n - start) // 10))
    trend = f"entry bias {an.bias}" + (f", HTF bias {an.htf_bias}" if an.htf_bias else "")
    ax.set_title(f"{trend} - shaded: zones, outlined: HTF zones, line: trendline (dotted after break), hatched: OB",
                 fontsize=10)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
