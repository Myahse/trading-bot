"""Fundamental side of the analysis, for forex and gold.

What it uses (all free, no account):
  * Economic calendar - this week's events from the ForexFactory JSON feed
    (nfs.faireconomy.media). The feed has forecasts and previous values but no actuals,
    so it is used to flag *upcoming* risk, not to score surprises.
  * Currency strength - each major currency's % change against the basket of majors
    over the period, from Yahoo FX prices.
  * Gold drivers - US dollar index (DXY), US 10-year yield and VIX from Yahoo.
  * Optional `fundamentals.json` you maintain yourself: central-bank rates and your own
    view per currency (e.g. "GBP": "hawkish"). Rates turn into a carry bias.

Volatility indices have no fundamentals: Deriv generates them from a random number generator.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MAJORS = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
# Yahoo pair per currency and whether it is quoted against USD (XXXUSD) or USD against it (USDXXX)
YAHOO_FX = {"EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "AUD": ("AUDUSD=X", False),
            "NZD": ("NZDUSD=X", False), "JPY": ("USDJPY=X", True), "CAD": ("USDCAD=X", True),
            "CHF": ("USDCHF=X", True)}
GOLD_DRIVERS = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "VIX": "^VIX"}
VIEW_SCORE = {"hawkish": 1, "bullish": 1, "dovish": -1, "bearish": -1, "neutral": 0}


@dataclass
class Event:
    when: dt.datetime        # UTC
    currency: str
    impact: str              # High | Medium | Low | Holiday
    title: str
    forecast: str = ""
    previous: str = ""


@dataclass
class Fundamentals:
    symbol: str
    applicable: bool                     # False for synthetic indices
    bias: str = "neutral"                # bullish | bearish | neutral (for the symbol)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    strength: dict[str, float] = field(default_factory=dict)      # % vs basket over the period
    drivers: dict[str, pd.Series] = field(default_factory=dict)   # gold: DXY / US10Y / VIX closes
    warnings: list[str] = field(default_factory=list)


def currencies(deriv_symbol: str) -> tuple[str, str] | None:
    """('XAU', 'USD') for frxXAUUSD, ('GBP', 'JPY') for frxGBPJPY, None for synthetic indices."""
    if not deriv_symbol.startswith("frx"):
        return None
    pair = deriv_symbol[3:]
    return pair[:3], pair[3:]


# -- data ------------------------------------------------------------------------

def fetch_calendar(url: str = CALENDAR_URL) -> list[Event]:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tradebot)"})
    with urllib.request.urlopen(req, timeout=20) as r:   # noqa: S310 (fixed https URL)
        raw = json.load(r)
    return parse_calendar(raw)


def parse_calendar(raw: list[dict]) -> list[Event]:
    events = []
    for e in raw:
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(dt.timezone.utc)
        except (KeyError, ValueError):
            continue
        events.append(Event(when, e.get("country", ""), e.get("impact", ""), e.get("title", ""),
                            e.get("forecast", "") or "", e.get("previous", "") or ""))
    return sorted(events, key=lambda ev: ev.when)


def fetch_closes(tickers: list[str], period: str = "3mo") -> pd.DataFrame:
    import warnings

    import yfinance as yf
    warnings.filterwarnings("ignore")
    df = yf.download(tickers, period=period, interval="1d", progress=False, auto_adjust=True)["Close"]
    return df.to_frame() if isinstance(df, pd.Series) else df


def usd_values(fx_closes: pd.DataFrame) -> pd.DataFrame:
    """Value of one unit of each major in USD, per day."""
    out = pd.DataFrame(index=fx_closes.index)
    out["USD"] = 1.0
    for ccy, (ticker, usd_base) in YAHOO_FX.items():
        if ticker in fx_closes:
            out[ccy] = 1.0 / fx_closes[ticker] if usd_base else fx_closes[ticker]
    return out.ffill().dropna()


def currency_strength(values: pd.DataFrame, days: int) -> dict[str, float]:
    """% change of each currency against the equal-weight basket of all of them, over `days`."""
    if len(values) <= days:
        return {}
    change = (values.iloc[-1] / values.iloc[-1 - days] - 1) * 100
    return (change - change.mean()).sort_values(ascending=False).round(2).to_dict()


def load_views(path: str | Path = "fundamentals.json") -> dict:
    """{"rates": {"USD": 4.5, ...}, "views": {"GBP": "hawkish", ...}} - all optional."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {"rates": {k: v for k, v in (raw.get("rates") or {}).items() if v is not None},
            "views": {k: v for k, v in (raw.get("views") or {}).items() if v}}


# -- analysis --------------------------------------------------------------------

def analyse(deriv_symbol: str, horizon: str, now: dt.datetime, calendar: list[Event] | None,
            fx_values: pd.DataFrame | None, drivers: pd.DataFrame | None, views: dict | None = None,
            ) -> Fundamentals:
    pair = currencies(deriv_symbol)
    if pair is None:
        return Fundamentals(deriv_symbol, False, reasons=[
            "Synthetic index: Deriv generates it with a random number generator at a fixed volatility. "
            "There is no economy, central bank or news behind it, so there is no fundamental analysis - "
            "only the technical picture and your risk management apply."])
    base, quote = pair
    f = Fundamentals(deriv_symbol, True)
    days = 1 if horizon == "day" else 5
    views = views or {}

    # 1. upcoming calendar risk for the currencies involved
    if calendar is not None:
        start, end = _window(horizon, now)
        involved = {base, quote} | ({"USD"} if base == "XAU" else set())
        f.events = [e for e in calendar if e.currency in involved and e.impact in ("High", "Medium")
                    and start <= e.when < end]
        highs = [e for e in f.events if e.impact == "High"]
        if highs:
            names = ", ".join(f"{e.currency} {e.title} ({e.when:%a %H:%M} UTC)" for e in highs[:4])
            f.warnings.append(f"{len(highs)} high-impact event(s): {names}. Expect spikes and wider spreads - "
                              "avoid new entries 30 minutes either side.")
    else:
        f.warnings.append("Economic calendar unavailable - check it manually before trading.")

    # 2. currency strength (price-based)
    score = 0.0
    if fx_values is not None and not fx_values.empty:
        f.strength = currency_strength(fx_values, days)
        if base == "XAU":
            usd = f.strength.get("USD")
            if usd is not None:
                s = _clip(-usd / 0.5)
                score += s
                f.reasons.append(f"USD {'strengthened' if usd > 0 else 'weakened'} {abs(usd):.2f}% vs the "
                                 f"majors over the last {days} day(s): {_word(s)} for gold.")
        elif base in f.strength and quote in f.strength:
            diff = f.strength[base] - f.strength[quote]
            s = _clip(diff / 0.5)
            score += s
            f.reasons.append(f"{base} {f.strength[base]:+.2f}% vs {quote} {f.strength[quote]:+.2f}% against the "
                             f"majors ({days}d): {_word(s)} for {base}{quote}.")

    # 3. gold drivers: dollar index, real-rate proxy (US 10y), risk sentiment (VIX)
    if base == "XAU" and drivers is not None and len(drivers) > days:
        # (name, effect on gold of a rise, size of a "full" move)
        for name, sign, full in (("DXY", -1, 0.5), ("US10Y", -1, 10.0), ("VIX", 1, 10.0)):
            col = GOLD_DRIVERS[name]
            if col not in drivers:
                continue
            series = drivers[col].dropna()
            f.drivers[name] = series.tail(40)
            a, b = float(series.iloc[-1 - days]), float(series.iloc[-1])
            change = (b - a) * 100 if name == "US10Y" else (b / a - 1) * 100   # yield in % -> basis points
            s = _clip(sign * change / full) * 0.5
            score += s
            rising = {"DXY": "a stronger dollar", "US10Y": "higher yields", "VIX": "more fear"}
            falling = {"DXY": "a weaker dollar", "US10Y": "lower yields", "VIX": "less fear"}
            amount = f"{change:+.0f}bp" if name == "US10Y" else f"{change:+.1f}%"
            f.reasons.append(f"{name} {amount} ({days}d), {(rising if b > a else falling)[name]}: "
                             f"{_word(s)} for gold.")

    # 4. your own inputs: rate differential (carry) and central-bank views
    rates, my_views = views.get("rates", {}), views.get("views", {})
    if base != "XAU" and base in rates and quote in rates:
        diff = float(rates[base]) - float(rates[quote])
        s = _clip(diff / 2) * 0.5
        score += s
        f.reasons.append(f"Rate differential {base} {rates[base]}% - {quote} {rates[quote]}% = {diff:+.2f}%: "
                         f"carry is {_word(s)} for {base}{quote}.")
    for ccy, sign in ((base, 1), (quote, -1)):
        view = str(my_views.get(ccy, "")).lower()
        if view in VIEW_SCORE and view != "neutral":
            s = sign * VIEW_SCORE[view] * 0.5
            score += s
            f.reasons.append(f"Your view: {ccy} {view} -> {_word(s)} for {base}{quote}.")

    f.score = round(score, 2)
    f.bias = "bullish" if score >= 0.75 else "bearish" if score <= -0.75 else "neutral"
    if not f.reasons:
        f.reasons.append("No fundamental data available.")
    return f


def events_soon(calendar: list[Event], deriv_symbol: str, now: dt.datetime, minutes: int = 60) -> list[Event]:
    """High-impact events for this symbol's currencies within `minutes` of now (for live signals)."""
    pair = currencies(deriv_symbol)
    if pair is None:
        return []
    involved = set(pair) | {"USD"}
    window = dt.timedelta(minutes=minutes)
    return [e for e in calendar if e.impact == "High" and e.currency in involved and abs(e.when - now) <= window]


def _window(horizon: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Next day (UTC) for a daily outlook; the coming Monday-Saturday for a weekly one."""
    today = now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if horizon == "day":
        return today + dt.timedelta(days=1), today + dt.timedelta(days=2)
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)
    if today.weekday() < 5:            # run mid-week: cover the rest of this week instead
        monday = today
    return monday, monday + dt.timedelta(days=6)


def _clip(x: float, lim: float = 1.0) -> float:
    return 0.0 if math.isnan(x) else max(-lim, min(lim, x))


def _word(s: float) -> str:
    return "supportive" if s > 0.15 else "negative" if s < -0.15 else "neutral"
