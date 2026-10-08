"""Fake breaks: price closes through a level, then closes back on the other side within a few candles.

The breakout traders are trapped on the wrong side, and the move back often runs. Three kinds:
  trendline / neckline  broken by a close beyond it (BREAK_ATR), then a close back on the side it
                        came from within `bars` candles
  zone sweep            a close below a support zone (above a resistance zone) from inside or above
                        it, then a close back above its low (below its high) within `bars` candles

`side` is the direction of the trap: "long" when price faked down and came back up. `extreme` is
the furthest price reached beyond the level - the stop goes beyond it. A fake-broken trendline is
not "broken" any more for its retest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BREAK_ATR = 0.1     # a close this far beyond the level breaks it; this far back makes the break fake


@dataclass(frozen=True)
class FakeBreak:
    what: str           # "rising trendline", "HTF falling trendline", "double bottom neckline", "support zone", ...
    side: str           # direction of the trap: "long" (faked down, came back up) | "short"
    level: float        # the level at the candle that came back
    broke_at: int       # the candle that closed through it
    back_at: int        # the candle that closed back
    extreme: float      # the lowest low (long) / highest high (short) from the break to the return
    key: tuple = ()     # identifies the trendline (i1, i2, kind, htf), so its retest can be ignored

    @property
    def label(self) -> str:
        return f"fake break {'below' if self.side == 'long' else 'above'} {self.what}"


def _back(c, atr, level_at, b, t, bars, side) -> int | None:
    """First candle after the break at b, within `bars`, that closes back on the trap side."""
    for j in range(b + 1, min(b + bars, t) + 1):
        a = atr[j]
        if np.isnan(a):
            continue
        v = level_at(j)
        if (c[j] > v + BREAK_ATR * a) if side == "long" else (c[j] < v - BREAK_ATR * a):
            return j
    return None


def fake_breaks(h, l, c, atr, t: int, trendlines, patterns, zones, bars: int = 3) -> list[FakeBreak]:
    """The fake breaks up to t, most recent first: every one on a live trendline or neckline (it stays
    "not broken"), and zone sweeps that came back within the last `bars` candles."""
    out: list[FakeBreak] = []
    for ln in trendlines:
        if ln.broken_at is None or ln.broken_at >= t:
            continue
        side = "long" if ln.kind == "support" else "short"     # a rising line broken down, then back up: long
        j = _back(c, atr, ln.value_at, ln.broken_at, t, bars, side)
        if j is not None:   # kept while the line lives: it is not broken
            span = slice(ln.broken_at, j + 1)
            ext = float(l[span].min() if side == "long" else h[span].max())
            name = f"{'HTF ' if ln.htf else ''}{'rising' if ln.kind == 'support' else 'falling'} trendline"
            out.append(FakeBreak(name, side, float(ln.value_at(j)), ln.broken_at, j, ext,
                                 (ln.i1, ln.i2, ln.kind, ln.htf)))
    for pat in patterns:
        if pat.broken_at is None or pat.broken_at >= t:
            continue
        side = "short" if pat.side == "long" else "long"       # a bullish neckline broken up, then back down: short
        j = _back(c, atr, pat.neck, pat.broken_at, t, bars, side)
        if j is not None:   # kept while the line lives: it is not broken
            span = slice(pat.broken_at, j + 1)
            ext = float(l[span].min() if side == "long" else h[span].max())
            out.append(FakeBreak(f"{pat.kind} neckline", side, float(pat.neck(j)), pat.broken_at, j, ext))
    for z, htf in zones:
        for side in ("long", "short"):
            edge = z.low if side == "long" else z.high
            for i in range(max(1, t - 2 * bars), t + 1):
                a = atr[i]
                if np.isnan(a):
                    continue
                beyond = (c[i] < edge - BREAK_ATR * a) if side == "long" else (c[i] > edge + BREAK_ATR * a)
                came_from = (c[i - 1] >= edge) if side == "long" else (c[i - 1] <= edge)
                if not (beyond and came_from):
                    continue
                j = _back(c, atr, lambda _j, e=edge: e, i, t, bars, side)
                if j is not None and t - j < bars:
                    span = slice(i, j + 1)
                    ext = float(l[span].min() if side == "long" else h[span].max())
                    name = f"{'HTF ' if htf else ''}{'support' if side == 'long' else 'resistance'} zone"
                    out.append(FakeBreak(name, side, float(edge), i, j, ext))
                break
    return sorted(out, key=lambda f: -f.back_at)
