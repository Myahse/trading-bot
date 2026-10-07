"""Load OHLCV data from a CSV, Yahoo Finance, or generate a synthetic series."""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED = ["open", "high", "low", "close"]


def _normalize(raw: pd.DataFrame, time=None) -> pd.DataFrame:
    raw = raw.rename(columns={c: str(c).strip().lower() for c in raw.columns})
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        raise ValueError(f"data is missing columns: {missing}")
    df = raw[REQUIRED].astype(float)
    df["volume"] = raw["volume"].astype(float) if "volume" in raw.columns else 0.0
    if time is not None:
        df.insert(0, "time", list(time))
    return df.dropna(subset=REQUIRED).reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns open, high, low, close[, volume] and optionally a date/time column."""
    raw = pd.read_csv(path)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    time_col = next((c for c in ("time", "date", "datetime", "timestamp") if c in raw.columns), None)
    time = pd.to_datetime(raw[time_col], utc=True, errors="coerce") if time_col else None
    return _normalize(raw, time)


def load_yahoo(symbol: str, interval: str = "1d", period: str = "2y") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pip install yfinance to download market data") from exc
    raw = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=True)
    if raw.empty:
        raise SystemExit(f"no data returned for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalize(raw, raw.index)


def synthetic(n: int = 1500, seed: int = 7, start: float = 100.0) -> pd.DataFrame:
    """Random walk with alternating trend regimes - for demos and tests only."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.0012, 0.0, 0.0012], size=n // 150 + 1), 150)[:n]
    rets = drift + rng.normal(0, 0.01, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.002, n))
    span = np.abs(rng.normal(0, 0.006, n)) * close
    high = np.maximum(open_, close) + span * rng.random(n)
    low = np.minimum(open_, close) - span * rng.random(n)
    time = pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"time": time, "open": open_, "high": high, "low": low,
                         "close": close, "volume": rng.integers(100, 1000, n).astype(float)})
