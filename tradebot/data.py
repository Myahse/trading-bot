"""Load OHLCV data from a CSV, Yahoo Finance, or generate a synthetic series."""

from __future__ import annotations

import re
import time

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
    if time is not None:   # always UTC, so day boundaries and "UTC" labels are right whatever the source
        df.insert(0, "time", pd.to_datetime(pd.Series(list(time)), utc=True).to_numpy())
        df["time"] = pd.to_datetime(df["time"], utc=True)
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
    symbol = yahoo_symbol(symbol)
    four_hour = interval == "4h"   # Yahoo has no 4h candles: build them from 1h
    raw = yf.download(symbol, interval="1h" if four_hour else interval, period=period,
                      progress=False, auto_adjust=True)
    if raw.empty:
        raise SystemExit(f"no data returned for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [str(c).lower() for c in raw.columns]
    if four_hour:
        raw = raw.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                      "volume": "sum"}).dropna(subset=["open"])
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


# -- Deriv ----------------------------------------------------------------------

DERIV_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"
DERIV_GRANULARITY = {"1m": 60, "2m": 120, "3m": 180, "5m": 300, "10m": 600, "15m": 900, "30m": 1800,
                     "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "1d": 86400}


def deriv_symbol(name: str) -> str:
    """Friendly names to Deriv API symbols: V75 -> R_75, V75(1s) -> 1HZ75V, XAUUSD -> frxXAUUSD."""
    n = name.strip().upper().replace(" ", "")
    m = re.fullmatch(r"(?:VOLATILITY|VIX|V)(10|25|50|75|100)(?:INDEX)?(\(1S\)|1S)?", n)
    if m:
        return f"1HZ{m[1]}V" if m[2] else f"R_{m[1]}"
    if len(n) == 6 and n.isalpha():
        return "frx" + n
    return name.strip()


def load_deriv(symbol: str, interval: str = "5m", count: int = 5000, app_id: int = 1089,
               connect=None, now: float | None = None) -> pd.DataFrame:
    """Closed candles from Deriv's public API (no account needed), paging back 5000 at a time.

    The candle that is still forming is dropped, so signals only ever use closed candles.

    app_id 1089 is Deriv's public test id; register your own at api.deriv.com for regular use.
    """
    if interval not in DERIV_GRANULARITY:
        raise ValueError(f"Deriv intervals are {', '.join(DERIV_GRANULARITY)}")
    if connect is None:
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("pip install websocket-client to download Deriv data") from exc
        connect = lambda url: websocket.create_connection(url, timeout=30)  # noqa: E731
    import json

    sym = deriv_symbol(symbol)
    try:
        ws = connect(DERIV_URL.format(app_id=app_id))
    except Exception as exc:  # network, firewall, proxy without WebSocket support
        raise SystemExit(f"could not connect to Deriv ({exc.__class__.__name__}: {str(exc)[:80]}). "
                         "Check your internet connection, or use --source yahoo / --csv.") from exc
    candles: list[dict] = []
    end: str | int = "latest"
    try:
        while len(candles) < count:
            want = min(5000, count - len(candles))
            ws.send(json.dumps({"ticks_history": sym, "style": "candles", "end": end, "count": want,
                                "granularity": DERIV_GRANULARITY[interval]}))
            reply = json.loads(ws.recv())
            if "error" in reply:
                raise SystemExit(f"Deriv: {reply['error'].get('message')} ({sym})")
            batch = reply.get("candles") or []
            candles = batch + candles
            if len(batch) < want:
                break
            end = int(batch[0]["epoch"]) - 1
    finally:
        ws.close()
    if not candles:
        raise SystemExit(f"Deriv returned no candles for {sym}")
    raw = pd.DataFrame(candles).drop_duplicates("epoch").sort_values("epoch")
    now = time.time() if now is None else now
    raw = raw[raw["epoch"].astype(int) + DERIV_GRANULARITY[interval] <= now]
    return _normalize(raw, pd.to_datetime(raw["epoch"], unit="s", utc=True))


YAHOO_ALIASES = {"XAUUSD": "GC=F", "XAGUSD": "SI=F"}


def yahoo_symbol(name: str) -> str:
    """XAUUSD -> GC=F (gold futures), GBPJPY -> GBPJPY=X; anything else unchanged."""
    n = name.strip().upper()
    if n in YAHOO_ALIASES:
        return YAHOO_ALIASES[n]
    return n + "=X" if len(n) == 6 and n.isalpha() else name


def volatility_index(n: int = 5000, vol: float = 0.75, bar_seconds: int = 300, seed: int = 1,
                     start: float = 100_000.0, steps: int = 30) -> pd.DataFrame:
    """Simulated Deriv-style volatility index: driftless geometric Brownian motion with a
    fixed annualised volatility (V75 -> vol=0.75), sampled into OHLC candles."""
    rng = np.random.default_rng(seed)
    dt = bar_seconds / steps / (365 * 24 * 3600)
    log_steps = rng.normal(-0.5 * vol ** 2 * dt, vol * np.sqrt(dt), (n, steps))
    path = start * np.exp(np.cumsum(log_steps.ravel())).reshape(n, steps)
    open_ = np.r_[start, path[:-1, -1]]
    high = np.maximum(path.max(axis=1), open_)
    low = np.minimum(path.min(axis=1), open_)
    time = pd.date_range("2026-01-01", periods=n, freq=f"{bar_seconds}s", tz="UTC")
    return pd.DataFrame({"time": time, "open": open_, "high": high, "low": low, "close": path[:, -1],
                         "volume": 0.0})
