"""Chart of price with the zones, trendlines, order blocks and trades the strategy used."""

from __future__ import annotations

import numpy as np

from .backtest import BacktestResult
from .strategy import Market


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
    ax.vlines(x, market.l[x], market.h[x], color="#555", linewidth=0.6)
    ax.bar(x[up], (market.c - market.o)[x][up], bottom=market.o[x][up], color="#2a9d8f", width=0.7)
    ax.bar(x[~up], (market.c - market.o)[x][~up], bottom=market.o[x][~up], color="#e76f51", width=0.7)

    for z in an.support:
        ax.axhspan(z.low, z.high, color="#2a9d8f", alpha=0.15)
    for z in an.resistance:
        ax.axhspan(z.low, z.high, color="#e76f51", alpha=0.15)
    for line in an.trendlines:
        xs = np.array([max(line.i1, start), n - 1])
        ax.plot(xs, [line.value_at(i) for i in xs], color="#264653", linestyle="--", linewidth=1.2)
    for ob in market.order_blocks:
        end = min(ob.invalidated_at if ob.invalidated_at is not None else n - 1,
                  ob.created_at + market.cfg.ob_max_age)
        if end < start:
            continue
        colour = "#2a9d8f" if ob.kind == "bullish" else "#e76f51"
        left = max(ob.index, start)
        ax.add_patch(Rectangle((left, ob.low), end - left, ob.high - ob.low,
                               facecolor=colour, edgecolor=colour, alpha=0.3, hatch="//"))

    if result:
        for tr in result.trades:
            if tr.entry_bar < start:
                continue
            colour = "#2a9d8f" if tr.side == "long" else "#e76f51"
            ax.scatter(tr.entry_bar, tr.entry, marker="^" if tr.side == "long" else "v", color=colour, s=60, zorder=5)
            if tr.exit_bar is not None:
                ax.plot([tr.entry_bar, tr.exit_bar], [tr.entry, tr.exit], color=colour, linewidth=1)
                ax.scatter(tr.exit_bar, tr.exit, marker="x", color="black" if tr.pnl > 0 else "red", s=40, zorder=5)

    ax.set_xlim(start - 1, n + 1)
    ax.set_title(f"Zones (shaded), trendlines (dashed), order blocks (hatched) - bias: {an.bias}")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
