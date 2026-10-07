"""Surgical entries: a setup found on the entry chart is entered on a lower timeframe.

The entry chart (e.g. 5m) says *where* to trade: a zone, trendline or order block tagged
by a rejection candle. The lower timeframe (e.g. 1m) says *when*. Inside that entry zone
the bot waits for a change of character (CHoCH): a lower-timeframe candle closing through
the last lower-timeframe swing against the trade (the last lower high, for a long). It
enters at that close. The stop goes just beyond the lower-timeframe swing that made the
turn (the lowest low since that swing high, for a long), not beyond the whole entry-chart
structure. The target stays the same, so the stop is a fraction of the size and
reward:risk is several times higher.

The setup is cancelled if price trades through the entry-chart stop first (the idea is
dead), or if no CHoCH comes within `confirm_bars` entry candles.

Once in the trade, the stop and target are checked candle by candle on the lower
timeframe, so the backtest knows which one was hit first. With a tight stop that matters.

Nothing is looked ahead: a lower-timeframe swing is only known `refine_pivot` candles
after it, and the signal candle's own lower-timeframe candles are used only as history.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .structure import atr, find_pivots


class LowerTimeframe:
    """Lower-timeframe candles lined up with the entry candles they fall in."""

    def __init__(self, ltf: pd.DataFrame, entry_time: pd.Series, pivot: int = 2, atr_period: int = 14):
        ltf = ltf.reset_index(drop=True)
        t_ltf = pd.to_datetime(ltf["time"], utc=True).to_numpy()
        t_entry = pd.to_datetime(pd.Series(entry_time), utc=True).to_numpy()
        entry_step = np.median(np.diff(t_entry)) if len(t_entry) > 1 else np.timedelta64(0, "s")
        # keep only candles inside the entry candles we have (not the one still forming)
        keep = (t_ltf >= t_entry[0]) & (t_ltf < t_entry[-1] + entry_step) if len(t_entry) else t_ltf < t_ltf
        ltf = ltf[keep].reset_index(drop=True)
        self.df = ltf
        self.time = t_ltf[keep]
        self.o, self.h, self.l, self.c = (ltf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = atr(ltf, atr_period).to_numpy() if len(ltf) else np.array([])
        self.step = np.median(np.diff(self.time)) if len(self.time) > 1 else entry_step
        self.bar_of = np.searchsorted(t_entry, self.time, side="right") - 1   # entry candle of each candle
        bars = np.arange(len(t_entry))
        self.start = np.searchsorted(self.bar_of, bars, side="left")
        self.end = np.searchsorted(self.bar_of, bars, side="right")
        self.entry_end = t_entry + entry_step
        self.pivots = find_pivots(ltf, pivot, pivot) if len(ltf) else []
        self._swings = {k: [p for p in self.pivots if p.kind == k] for k in ("high", "low")}
        self._known = {k: np.array([p.confirmed_at for p in v]) for k, v in self._swings.items()}

    def covers(self, t: int) -> bool:
        """True when every lower-timeframe candle of entry candle t is here (it has fully closed)."""
        if t >= len(self.start) or self.end[t] == self.start[t]:
            return False
        return self.time[self.end[t] - 1] + self.step >= self.entry_end[t]

    def candles(self, t: int, after: int | None = None) -> range:
        """Indices of the lower-timeframe candles inside entry candle t (only those after `after`)."""
        first = self.start[t] if after is None else max(self.start[t], after + 1)
        return range(first, self.end[t])

    def last_swing(self, kind: str, j: int):
        """The latest swing high/low known before candle j closes (confirmed at j-1 or earlier)."""
        k = int(np.searchsorted(self._known[kind], j, side="left")) - 1
        return self._swings[kind][k] if k >= 0 else None   # every swing confirms `pivot` candles later,
                                                            # so the last one confirmed is the latest


@dataclass
class Entry:
    index: int       # lower-timeframe candle of the CHoCH
    price: float     # its close: where the trade is entered
    stop: float
    swing: float     # the swing that was broken


def choch(ltf: LowerTimeframe, side: str, j: int, stop_buffer_atr: float, min_stop_atr: float) -> Entry | None:
    """A change of character on candle j: it closes through the last swing against the trade
    (the previous close did not). Long: close above the last swing high; the stop goes below
    the lowest low since that swing high. Short is the mirror."""
    if j < 1 or np.isnan(ltf.atr[j]):
        return None
    long = side == "long"
    swing = ltf.last_swing("high" if long else "low", j)
    if swing is None:
        return None
    level, c, prev = swing.price, ltf.c[j], ltf.c[j - 1]
    if not ((prev <= level < c) if long else (prev >= level > c)):
        return None
    a = ltf.atr[j]
    if long:
        stop = ltf.l[swing.index:j + 1].min() - stop_buffer_atr * a
        stop = min(stop, c - min_stop_atr * a)
    else:
        stop = ltf.h[swing.index:j + 1].max() + stop_buffer_atr * a
        stop = max(stop, c + min_stop_atr * a)
    return Entry(j, float(c), float(stop), float(level))


def resample(ltf: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """Build higher candles (e.g. 5m) from lower ones (e.g. 1m), aligned on the clock."""
    frame = ltf.drop(columns="time").set_index(pd.DatetimeIndex(pd.to_datetime(ltf["time"], utc=True), name="time"))
    bars = frame.resample(f"{seconds}s").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                               "volume": "sum"}).dropna(subset=["open"])
    return bars.reset_index()[["time", "open", "high", "low", "close", "volume"]]
