"""A clean chart: only the levels that matter at the latest bar, plus the trades.

To keep the chart readable it draws the nearest two zones on each side of price,
the nearest higher-timeframe zone on each side, the current trendlines, the active
order blocks closest to price, the swing labels (HH/HL/LH/LL) and, when there is a
setup, its entry, stop-loss and take-profit - not every level ever detected.

Setups and trades are drawn like TradingView's long/short position tool: a green box from
the entry to the take-profit (where to get out with a profit) and a red box from the entry
to the stop-loss (where to get out with a loss), starting at the candle where the trade begins.
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


def position_box(ax, x0: float, x1: float, entry: float, stop: float, target: float, alpha: float = 0.18,
                 text: bool = True) -> None:
    """TradingView-style position: green box entry -> take-profit, red box entry -> stop-loss.

    With `text`, each box is labelled with its distance from the entry (in % and, for the
    target, in R) - only worth it when the box is wide enough to hold the words.
    """
    from matplotlib.patches import Rectangle

    for level, colour in ((target, UP), (stop, DOWN)):
        ax.add_patch(Rectangle((x0, min(entry, level)), x1 - x0, abs(level - entry), facecolor=colour,
                               alpha=alpha, edgecolor="none", zorder=2))
        ax.add_patch(Rectangle((x0, min(entry, level)), x1 - x0, abs(level - entry), facecolor="none",
                               edgecolor=colour, linewidth=0.9, zorder=2))
    ax.plot([x0, x1], [entry, entry], color=INK, linewidth=1.1, zorder=3)
    if text:   # outside the far edge of each box, as TradingView does, so a thin box stays readable
        risk = abs(entry - stop)
        for level, colour, word in ((target, UP, "Take profit"), (stop, DOWN, "Stop loss")):
            pct = abs(level - entry) / abs(entry) if entry else 0.0
            extra = f"  {abs(target - entry) / risk:.1f}R" if level == target and risk else ""
            above = level >= entry
            ax.annotate(f"{word} {pct:.2%}{extra}", ((x0 + x1) / 2, level), xytext=(0, 2 if above else -2),
                        textcoords="offset points", ha="center", va="bottom" if above else "top", fontsize=7,
                        fontweight="bold", color=colour, zorder=7)


def draw_entry(ax, market: Market, an: Analysis, last: int = 300, result: BacktestResult | None = None,
               show_swings: bool = True, trades: list | None = None) -> None:
    """The entry-timeframe chart with the analysis drawn on it.

    `trades` (or `result.trades`) are drawn as position boxes from their entry to their exit;
    a trade still open runs to the right edge with its current stop.
    """
    from matplotlib.patches import Rectangle

    n = an.bar + 1
    start = max(0, n - last)
    x = np.arange(start, n)
    candles(ax, x, market.o[x], market.h[x], market.l[x], market.c[x])
    s = an.signal
    trades = [t for t in (trades if trades is not None else result.trades if result else [])
              if start <= t.entry_bar < n]
    # a backtest closes its last trade at the final candle ("end of data"): on the chart it is still running
    open_trades = [t for t in trades if t.exit_bar is None or t.exit_bar >= n
                   or (t.exit_reason == "end of data" and t.exit_bar == n - 1)]
    # lines and boxes are projected up to here; labels go after it. Wider with a trade plan to draw.
    right = n + max(6, (n - start) // (6 if s is not None or open_trades else 14))
    labels = Labels()

    lo, hi = market.l[x].min(), market.h[x].max()
    if s is not None:   # make sure the whole trade plan is on screen
        lo, hi = min(lo, s.stop, s.target), max(hi, s.stop, s.target)
    for t in open_trades:
        lo, hi = min(lo, t.stop, t.target, t.current_stop), max(hi, t.stop, t.target, t.current_stop)
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
        position_box(ax, n - 0.5, right, entry, s.stop, s.target)
        verb = "BUY" if s.side == "long" else "SELL"
        order = {"break": f"{verb} STOP", "close": f"{verb} on close", "none": verb}[s.confirmation]
        labels.add(entry, f"{order} {px(entry)}", INK)
        labels.add(s.stop, f"SL {px(s.stop)}", DOWN)
        labels.add(s.target, f"TP {px(s.target)}  R:R {s.rr:.1f}", UP)
        ax.scatter(an.bar, market.c[an.bar], marker="^" if s.side == "long" else "v", s=90,
                   color=UP if s.side == "long" else DOWN, edgecolors="white", zorder=7)

    for tr in trades:
        long_ = tr.side == "long"
        colour = UP if long_ else DOWN
        still_open = tr in open_trades
        x1 = right if still_open else tr.exit_bar + 0.5
        position_box(ax, tr.entry_bar - 0.5, x1, tr.entry, tr.stop, tr.target, alpha=0.14, text=still_open)
        ax.scatter(tr.entry_bar, tr.entry, marker="^" if long_ else "v", color=colour, s=60,
                   edgecolors="white", zorder=6)
        if still_open:
            verb = "LONG" if long_ else "SHORT"
            labels.add(tr.entry, f"{verb} open {px(tr.entry)}", INK)
            labels.add(tr.target, f"TP {px(tr.target)}", UP)
            if tr.current_stop != tr.stop:   # stop moved (break-even / trailing): show where it is now
                ax.plot([tr.entry_bar - 0.5, right], [tr.current_stop] * 2, color=DOWN, linewidth=1.2,
                        linestyle=(0, (4, 2)), zorder=4)
                labels.add(tr.current_stop, f"SL now {px(tr.current_stop)} ({tr.stop_kind})", DOWN)
            else:
                labels.add(tr.stop, f"SL {px(tr.stop)}", DOWN)
        else:
            win = tr.pnl > 0
            ax.plot([tr.entry_bar, tr.exit_bar], [tr.entry, tr.exit], color=INK, linewidth=1,
                    linestyle=(0, (2, 2)), zorder=6)
            ax.scatter(tr.exit_bar, tr.exit, marker="X", color=UP if win else DOWN, edgecolors="white",
                       s=55, zorder=6)
            top = max(tr.entry, tr.stop, tr.target)
            ax.annotate(f"{tr.r_multiple:+.1f}R", ((tr.entry_bar + tr.exit_bar) / 2, top), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom", fontsize=7, fontweight="bold",
                        color=UP if win else DOWN, zorder=7)

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
                 f"dotted after break)   hatched: order block   green/red box: take-profit / stop-loss",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
