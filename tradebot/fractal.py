"""Higher-timeframe (fractal) market structure seen from the entry timeframe.

The same swing/zone logic is run on candles of a higher timeframe built from the
entry candles. A higher-timeframe candle only becomes known on the first entry bar
of the *next* period, so nothing is used before it has closed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .structure import Pivot, atr, find_pivots


def htf_groups(df: pd.DataFrame, htf: str | int) -> np.ndarray:
    """Group id per entry bar: every `htf` bars (int) or per calendar period ("1h", "4h", "D", "W")."""
    if isinstance(htf, int) or str(htf).isdigit():
        return np.arange(len(df)) // int(htf)
    if "time" not in df.columns:
        raise ValueError(f"higher timeframe {htf!r} needs a time column; pass a bar count instead")
    t = pd.to_datetime(df["time"], utc=True)
    try:
        key = t.dt.floor(htf)
    except ValueError:  # non-fixed periods such as "W" or "M"
        key = t.dt.tz_convert(None).dt.to_period(htf).astype(str)
    return ((key != key.shift()).cumsum() - 1).to_numpy()


class HigherTimeframe:
    def __init__(self, df: pd.DataFrame, htf: str | int, pivot: int, atr_period: int):
        groups = htf_groups(df, htf)
        starts = np.r_[0, np.nonzero(np.diff(groups))[0] + 1]
        self.known_at = starts[1:]                 # group j is known at the first bar of group j+1
        complete = len(self.known_at)              # the last (possibly unfinished) group is dropped
        frame = df.assign(_g=groups)[groups < complete]
        bars = frame.groupby("_g").agg(open=("open", "first"), high=("high", "max"),
                                       low=("low", "min"), close=("close", "last"))
        hi_at = frame.groupby("_g")["high"].idxmax().to_numpy()
        lo_at = frame.groupby("_g")["low"].idxmin().to_numpy()
        self.bars = bars.reset_index(drop=True)
        self.atr = atr(self.bars, atr_period).to_numpy()
        # Re-express pivots in entry-bar coordinates.
        self.pivots = [Pivot(int(hi_at[p.index] if p.kind == "high" else lo_at[p.index]), p.price, p.kind,
                             int(self.known_at[p.confirmed_at]))
                       for p in find_pivots(self.bars, pivot, pivot)]
        self.pivots.sort(key=lambda p: (p.confirmed_at, p.index))
        self._confirmed = np.array([p.confirmed_at for p in self.pivots])

    def pivots_known_at(self, t: int) -> list[Pivot]:
        return self.pivots[: int(np.searchsorted(self._confirmed, t, side="right"))]

    def atr_known_at(self, t: int) -> float:
        j = int(np.searchsorted(self.known_at, t, side="right")) - 1
        return float(self.atr[j]) if j >= 0 else float("nan")
