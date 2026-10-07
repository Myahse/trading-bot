"""Command line.

  python -m tradebot analyze  --symbol V75 --mode scalp       what the bot sees now, and any signal
  python -m tradebot backtest --symbol XAUUSD --mode swing    test the rules on history
  python -m tradebot watch    --symbol GBPJPY --mode scalp    print signals live at each candle close
  python -m tradebot outlook  --symbol XAUUSD,GBPJPY,V75 --horizon week|day
  python -m tradebot schedule --symbol XAUUSD,GBPJPY,V75 --at 18:00
                              weekly outlook every Sunday, next-day outlook every evening
  python -m tradebot paper    --symbol GBPUSD --mode scalp --equity 20 --leverage 500 --spread 0.00015
                              forward-test on live candles with a virtual account (no orders sent)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import time
from pathlib import Path

import pandas as pd

from . import data
from .backtest import run_backtest
from .money import MoneyManagement, position
from . import fundamentals as fund
from .outlook import HORIZONS, build_outlook, to_markdown
from .strategy import MODES, Market, StrategyConfig
from .structure import px


# -- argument helpers --------------------------------------------------------------

def _r_or_off(value: str) -> float | None:
    return None if value.lower() in ("off", "none", "0") else float(value)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradebot", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("command", choices=["analyze", "backtest", "watch", "outlook", "schedule", "paper", "download"])
    p.add_argument("--mode", choices=sorted(MODES), help="scalp (5m, 1h structure) or swing (4h, daily structure)")

    g = p.add_argument_group("market data")
    g.add_argument("--symbol", default="V75", help="V10..V100, V75(1s), XAUUSD, GBPJPY, USDJPY, GBPUSD ...; "
                   "comma-separated for outlook/schedule")
    g.add_argument("--source", choices=["deriv", "yahoo", "sim"], default="deriv")
    g.add_argument("--csv", help="OHLC csv file instead of downloading")
    g.add_argument("--interval", help="1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, 8h, 1d")
    g.add_argument("--count", type=int, help="candles to download from Deriv / simulate")
    g.add_argument("--period", help="history for --source yahoo, e.g. 60d, 730d")
    g.add_argument("--app-id", type=int, default=1089, help="your Deriv API app id")
    g.add_argument("--seed", type=int, default=7, help="seed for --source sim")

    g = p.add_argument_group("strategy")
    g.add_argument("--htf", help="higher timeframe: 1h, 4h, D, W, a bar count, or none")
    g.add_argument("--pivot", type=int, help="bars each side of a swing point")
    g.add_argument("--min-confluence", type=int)
    g.add_argument("--min-rr", type=float)
    g.add_argument("--confirm", choices=["refine", "break", "close", "none"],
                   help="refine: surgical entry on a lower-timeframe change of character inside the entry "
                        "zone (default in the modes); break: when price breaks the signal candle; close: after "
                        "a candle closes beyond it; none: next open")
    g.add_argument("--ltf", help="lower timeframe for --confirm refine: 1m (scalp default), 15m (swing default)")
    g.add_argument("--ltf-csv", help="lower-timeframe candles csv, for --csv backtests with --confirm refine")
    g.add_argument("--confirm-bars", type=int, help="candles the confirmation may take")
    g.add_argument("--no-trend-filter", action="store_true")
    g.add_argument("--no-breakouts", action="store_true", help="don't trade trendline breaks")

    g = p.add_argument_group("money management")
    g.add_argument("--equity", type=float, default=10_000, help="account size in USD")
    g.add_argument("--risk", type=float, help="fraction of the account risked per trade (scalp 0.005, swing 0.01)")
    g.add_argument("--leverage", type=float, help="max position value / account (default 30)")
    g.add_argument("--breakeven", type=_r_or_off, help="move stop to entry at this many R, or off")
    g.add_argument("--partial", type=_r_or_off, help="take partial profit at this many R, or off")
    g.add_argument("--partial-pct", type=float, help="fraction closed at --partial (default 0.5)")
    g.add_argument("--trail", choices=["none", "atr", "swing"], help="trailing stop")
    g.add_argument("--trail-start", type=float, help="start trailing at this many R")
    g.add_argument("--trail-atr", type=float, help="ATR multiple for --trail atr")
    g.add_argument("--daily-loss", type=_r_or_off, help="stop trading for the day after losing this fraction, or off")
    g.add_argument("--max-trades-day", type=int)
    g.add_argument("--spread", type=float, default=0.0, help="spread in price units per round trip (MT5 spec)")
    g.add_argument("--fee-bps", type=float, default=0.0, help="commission per side, bps of price")
    g.add_argument("--contract-size", type=float, help="units per lot (auto: forex 100000, gold 100, indices 1)")
    g.add_argument("--quote-rate", type=float, help="USD value of 1 unit of the quote currency, for crosses "
                   "such as GBPJPY (e.g. 0.0067 when USDJPY is 150)")
    g.add_argument("--lot-step", type=float, default=0.01)
    g.add_argument("--min-lot", type=float, help="smallest volume your broker accepts (forex/gold 0.01; "
                   "synthetic indices vary - check the MT5 specification)")
    g.add_argument("--min-lot-max-risk", type=float, default=0.05,
                   help="skip a trade if even the minimum lot would risk more than this fraction (default 0.05)")

    g = p.add_argument_group("output")
    g.add_argument("--plot", help="save a chart to this .png")
    g.add_argument("--bars", type=int, default=300, help="bars shown on the chart")
    g.add_argument("--trades", help="save trades to this .csv")
    g.add_argument("--screenshot", metavar="DIR", help="analyze: save technical + fundamental screenshots here")
    g.add_argument("--no-fundamentals", action="store_true", help="skip calendar / currency strength / drivers")
    g.add_argument("--views", default="fundamentals.json",
                   help="your central-bank rates and views (see fundamentals.example.json)")
    g.add_argument("--horizon", choices=["day", "week"], default="day", help="outlook period")
    g.add_argument("--out", default="reports", help="folder for outlook reports and charts")
    g.add_argument("--at", default="18:00", help="schedule: local time to publish outlooks")

    g = p.add_argument_group("paper trading")
    g.add_argument("--folder", help="paper account folder (default paper/<symbol>-<interval>)")
    g.add_argument("--reset", action="store_true", help="delete the paper account and start fresh")
    g.add_argument("--report", action="store_true", help="print the paper account summary and exit")
    g.add_argument("--no-news-filter", action="store_true",
                   help="paper: allow new orders within 30 minutes of high-impact news")
    g.add_argument("--no-screenshots", action="store_true", help="paper: don't save a chart per order/trade")
    return p


def _config(args) -> StrategyConfig:
    base = dict(MODES[args.mode]["config"]) if args.mode else {}
    for key, value in (("min_confluence", args.min_confluence), ("min_rr", args.min_rr), ("htf", args.htf),
                       ("pivot_left", args.pivot), ("pivot_right", args.pivot),
                       ("confirmation", args.confirm), ("confirm_bars", args.confirm_bars), ("ltf", args.ltf)):
        if value is not None:
            base[key] = value
    if args.htf == "none":
        base["htf"] = None
    if base.get("confirmation") == "refine" and not base.get("ltf"):
        base["ltf"] = "1m"
    if args.no_trend_filter:
        base["trend_filter"] = False
    if args.no_breakouts:
        base["breakouts"] = False
    return StrategyConfig(**base)


def _money(args) -> MoneyManagement:
    base = dict(MODES[args.mode]["money"]) if args.mode else {}
    given = {"risk_per_trade": args.risk, "max_leverage": args.leverage, "partial_pct": args.partial_pct,
             "trail": args.trail, "trail_start_r": args.trail_start, "trail_atr": args.trail_atr,
             "max_trades_per_day": args.max_trades_day, "contract_size": args.contract_size,
             "quote_rate": args.quote_rate, "lot_step": args.lot_step, "min_lot": args.min_lot,
             "min_lot_max_risk": args.min_lot_max_risk}
    base.update({k: v for k, v in given.items() if v is not None})
    argv = " ".join(_argv)
    for flag, key, value in (("--breakeven", "breakeven_r", args.breakeven), ("--partial", "partial_r", args.partial),
                             ("--daily-loss", "max_daily_loss", args.daily_loss)):
        if re.search(rf"{flag}(\s|=|$)", argv):   # given explicitly, possibly as "off"
            base[key] = value
    if args.csv:   # unknown instrument: size in plain units unless told otherwise
        return MoneyManagement(**base)
    mm = MoneyManagement.for_symbol(data.deriv_symbol(args.symbol), **base)
    if args.min_lot is not None and mm.contract_size is None:
        mm.contract_size = 1.0   # synthetic indices: 1 unit per lot
    return mm


def _load(args, symbol: str | None = None, interval: str | None = None, count: int | None = None):
    symbol, interval, count = symbol or args.symbol, interval or args.interval, count or args.count
    if args.csv:
        return data.load_csv(args.csv)
    if args.source == "deriv":
        return data.load_deriv(symbol, interval, count, args.app_id)
    if args.source == "yahoo":
        return data.load_yahoo(symbol, interval, args.period or "730d")
    vol = re.search(r"(10|25|50|75|100)", symbol or "")   # sim: V75 -> 75% volatility
    return data.volatility_index(n=count, vol=int(vol[1]) / 100 if vol else 0.75,
                                 bar_seconds=data.DERIV_GRANULARITY.get(interval, 300), seed=args.seed)


def _load_market(args, cfg: StrategyConfig) -> Market:
    """Entry candles, plus the lower-timeframe candles a surgical ("refine") entry needs."""
    if cfg.confirmation != "refine":
        return Market(_load(args), cfg)
    if args.source == "sim" and not args.csv:   # simulate the lower timeframe and build the entry candles from it
        from .refine import resample
        ratio = _ratio(args.interval, cfg.ltf)
        ltf = _load(args, interval=cfg.ltf, count=args.count * ratio)
        return Market(resample(ltf, data.DERIV_GRANULARITY[args.interval]), cfg, ltf)
    df = _load(args)
    return Market(df, cfg, _load_ltf(args, cfg, len(df)))


def _ratio(interval: str, ltf: str) -> int:
    entry, low = data.DERIV_GRANULARITY.get(interval), data.DERIV_GRANULARITY.get(ltf)
    if not entry or not low or entry % low or entry <= low:
        raise SystemExit(f"--ltf {ltf} must be a smaller timeframe that divides --interval {interval}")
    return entry // low


def _load_ltf(args, cfg: StrategyConfig, bars: int, symbol: str | None = None) -> pd.DataFrame | None:
    """Lower-timeframe candles covering the entry candles, or None (refine then falls back to "break")."""
    try:
        if args.ltf_csv:
            return data.load_csv(args.ltf_csv)
        if args.csv:
            print("note: --confirm refine needs --ltf-csv with lower-timeframe candles; using break entries")
            return None
        ratio = _ratio(args.interval, cfg.ltf)
        if args.source == "yahoo":   # Yahoo keeps 1m candles for 7 days, 2m-30m for 60 days
            return data.load_yahoo(symbol or args.symbol, cfg.ltf, "7d" if cfg.ltf == "1m" else "60d")
        return data.load_deriv(symbol or args.symbol, cfg.ltf, min(bars * ratio, 200_000), args.app_id)
    except SystemExit as exc:
        print(f"note: no {cfg.ltf} candles ({exc}); using break entries")
        return None


TF_NAMES = {"D": "daily", "W": "weekly", "1h": "1h", "4h": "4h", "15min": "15m"}


def _tf_name(tf) -> str:
    return TF_NAMES.get(str(tf), str(tf)) if tf else "-"


_macro_cache: dict = {}


def _fundamentals(args, symbol: str, horizon: str, now: dt.datetime | None = None):
    """Fundamental analysis for one symbol, or None when switched off. Downloads are shared per run
    and failures degrade gracefully (the analysis says what is missing)."""
    if args.no_fundamentals:
        return None
    sym = data.deriv_symbol(symbol)
    now = now or dt.datetime.now(dt.timezone.utc)
    if fund.currencies(sym) is not None and "calendar" not in _macro_cache:
        for key, getter in (("calendar", fund.fetch_calendar),
                            ("fx", lambda: fund.usd_values(fund.fetch_closes([t for t, _ in fund.YAHOO_FX.values()]))),
                            ("drivers", lambda: fund.fetch_closes(list(fund.GOLD_DRIVERS.values())))):
            try:
                _macro_cache[key] = getter()
            except Exception as exc:  # network down, feed changed...
                print(f"note: {key} data unavailable ({exc.__class__.__name__})")
                _macro_cache[key] = None
    return fund.analyse(sym, horizon, now, _macro_cache.get("calendar"), _macro_cache.get("fx"),
                        _macro_cache.get("drivers"), fund.load_views(args.views))


def _screenshots(market: Market, args, symbol: str, out_dir: Path, prefix: str, f, entry_tf: str,
                 title: str = "", horizon: str = "day") -> list[tuple[str, str]]:
    """Save the technical (and fundamental) screenshot; returns [(caption, file name)]."""
    from .screenshot import fundamental_screenshot, technical_screenshot
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "", symbol)
    shots = []
    tech = f"{prefix}{safe}-technical.png"
    technical_screenshot(market, symbol, str(out_dir / tech), entry_tf, _tf_name(market.cfg.htf), f, title)
    shots.append(("technical analysis", tech))
    if f is not None:
        name = f"{prefix}{safe}-fundamental.png"
        fundamental_screenshot(f, str(out_dir / name), horizon, title)
        shots.append(("fundamental analysis", name))
    return shots


# -- commands --------------------------------------------------------------------

def _report(market: Market, args, money: MoneyManagement, f=None) -> None:
    an = market.analyze(len(market.c) - 1)
    if "time" in market.df.columns:
        print(f"last closed candle {market.df.time.iloc[-1]}")
    print(f"price {px(an.price)}   ATR {px(an.atr)}   bias {an.bias}"
          + (f"   HTF bias {an.htf_bias}" if an.htf_bias else ""))
    print("resistance:", ", ".join(f"{px(z.low)}-{px(z.high)} (x{z.touches})" for z in an.resistance[:3]) or "-")
    print("support:   ", ", ".join(f"{px(z.low)}-{px(z.high)} (x{z.touches})" for z in an.support[:3]) or "-")
    if market.htf is not None:
        print("HTF resistance:", ", ".join(f"{px(z.low)}-{px(z.high)}" for z in an.htf_resistance[:2]) or "-")
        print("HTF support:   ", ", ".join(f"{px(z.low)}-{px(z.high)}" for z in an.htf_support[:2]) or "-")
    for line in an.trendlines:
        state = "intact" if line.broken_at is None else f"BROKEN {an.bar - line.broken_at} bars ago"
        print(f"{'HTF ' if line.htf else ''}{line.kind} trendline now at {px(line.value_at(an.bar))} "
              f"({len(line.touches)} touches, {state})")
    for ob in an.order_blocks[-4:]:
        print(f"{ob.kind} order block {px(ob.low)}-{px(ob.high)}")

    if f is not None:
        if f.applicable:
            print(f"fundamentals: {f.bias} (score {f.score:+.2f})")
            for r in f.reasons:
                print(f"  - {r}")
            for w in f.warnings:
                print(f"  ! {w}")
        else:
            print("fundamentals: none - synthetic index (random by design)")
        soon = fund.events_soon(_macro_cache.get("calendar") or [], data.deriv_symbol(args.symbol),
                                dt.datetime.now(dt.timezone.utc))
        for e in soon:
            print(f"  !! HIGH-IMPACT NEWS within the hour: {e.currency} {e.title} at {e.when:%H:%M} UTC - "
                  f"do not enter around it")

    from .screenshot import verdict
    _, head, text = verdict(an, f)
    s = an.signal
    if not s:
        print("\nno setup on the latest bar")
        print(f"{head}: {text}")
        return
    action = "BUY" if s.side == "long" else "SELL"
    if s.confirmation == "refine":
        ltf = market.cfg.ltf
        way = "above the last lower high" if s.side == "long" else "below the last higher low"
        how = (f"{action} ZONE {px(s.zone[0])}-{px(s.zone[1])}: wait for a {ltf} candle to close {way} "
               f"(change of character) within {market.cfg.confirm_bars} candles, stop just beyond that {ltf} swing; "
               f"cancel if price reaches {px(s.stop)} first")
        if market.ltf is None:
            how += f" [no {ltf} candles loaded: the bot itself would use a {action} STOP at {px(s.trigger)}]"
    elif s.confirmation == "break":
        how = f"{action} STOP at {px(s.trigger)} (valid {market.cfg.confirm_bars} candles; cancel if price " \
              f"reaches {px(s.stop)} first)"
    elif s.confirmation == "close":
        how = f"{action} at the next open after a candle closes {'above' if s.side == 'long' else 'below'} " \
              f"{px(s.trigger)} (within {market.cfg.confirm_bars} candles)"
    else:
        how = f"{action} at the next open (~{px(s.entry)})"
    print(f"\nSIGNAL {s.side.upper()} ({s.setup}): {how}")
    if s.confirmation == "refine":
        print(f"  invalidation {px(s.stop)}   target {px(s.target)}   R:R at least {s.rr:.2f} "
              f"(higher once the {market.cfg.ltf} stop is known)")
    else:
        print(f"  stop {px(s.stop)}   target {px(s.target)}   R:R {s.rr:.2f}")
    print("  because: " + "; ".join(s.reasons))

    if s.confirmation == "refine":
        print(f"  size: set from the {market.cfg.ltf} stop when the entry comes (the lot size below is the "
              f"smallest - for a stop at the invalidation)")
    try:
        pos = position(money, args.equity, s.entry, s.stop)
    except ValueError as exc:   # a cross without --quote-rate
        print(f"  size: {exc}")
    else:
        if pos.units <= 0:
            print(f"  size: {pos.note} - NOT TRADEABLE on a {args.equity:,.0f} USD account")
        elif pos.lots is not None:
            print(f"  size: {pos.lots:g} lots, {pos.risk:.1%} of {args.equity:,.0f} USD at risk "
                  f"(${pos.risk * args.equity:,.2f})" + (f" - {pos.note}" if pos.note else ""))
        else:
            print(f"  size: {pos.units:,.4g} units, {pos.risk:.1%} of {args.equity:,.0f} USD at risk"
                  + ("" if data.deriv_symbol(args.symbol).startswith("frx") else
                     " (pass --min-lot from your MT5 spec to size in lots)"))
    plan = []
    if money.partial_r is not None and money.partial_pct > 0:
        plan.append(f"close {money.partial_pct:.0%} at +{money.partial_r:g}R")
    if money.breakeven_r is not None:
        plan.append(f"stop to break-even at +{money.breakeven_r:g}R")
    if money.trail != "none":
        what = f"{money.trail_atr:g} ATR behind the best price" if money.trail == "atr" else "behind each new swing"
        plan.append(f"trail {what} from +{money.trail_start_r:g}R")
    if plan:
        print("  manage: " + ", ".join(plan))
    print(f"{head}: {text}")


def _backtest(market: Market, args, money: MoneyManagement):
    if args.spread == 0 and args.fee_bps == 0:
        print("note: no trading costs set - pass --spread from your MT5 symbol specification")
    if market.cfg.confirmation == "refine":
        ltf = market.ltf
        if ltf is None:
            print(f"entries: break of the signal candle (no {market.cfg.ltf} candles)")
        else:
            covered = sum(ltf.covers(t) for t in range(len(market.c)))
            print(f"entries: surgical, on {market.cfg.ltf} changes of character - {market.cfg.ltf} candles cover "
                  f"{covered / len(market.c):.0%} of the entry candles (break entries elsewhere)")
    result = run_backtest(market.df, money=money, initial_equity=args.equity, fee_bps=args.fee_bps,
                          spread=args.spread, market=market)
    st = result.stats
    print(f"trades {st['trades']}   win rate {st['win_rate']:.1%}   profit factor {st['profit_factor']:.2f}   "
          f"avg R {st['avg_r']:.2f}")
    print(f"return {st['total_return']:.2%}   max drawdown {st['max_drawdown']:.2%}   "
          f"final equity {st['final_equity']:,.2f}")
    if st["exits"]:
        print("exits: " + ", ".join(f"{k} {v}" for k, v in st["exits"].items()))
    if st["skipped_min_lot"] or st["at_min_lot"]:
        print(f"lot size: {st['skipped_min_lot']} setups skipped (minimum lot too risky), "
              f"{st['at_min_lot']} trades forced up to the minimum lot")
    if result.trades:
        frame = result.trades_frame()
        for setup, g in frame.groupby("setup"):
            print(f"  {setup:<9} {len(g):>3} trades, win rate {(g.pnl > 0).mean():.0%}, avg R {g.r.mean():.2f}")
    if args.trades:
        result.trades_frame().to_csv(args.trades, index=False)
        print(f"trades -> {args.trades}")
    return result


def _watch(args, money: MoneyManagement) -> None:
    """Re-analyse right after every candle closes; print each new signal once. No orders are sent."""
    if args.source != "deriv" or args.csv:
        raise SystemExit("watch needs live data: use --source deriv")
    step = data.DERIV_GRANULARITY[args.interval]
    cfg, last_signal_time = _config(args), None
    print(f"watching {data.deriv_symbol(args.symbol)} on {args.interval} candles - Ctrl+C to stop")
    while True:
        df = data.load_deriv(args.symbol, args.interval, min(args.count, 5000), args.app_id)
        market = Market(df, cfg)
        an = market.analyze(len(market.c) - 1)
        stamp = df.time.iloc[-1]
        if an.signal and stamp != last_signal_time:
            last_signal_time = stamp
            _macro_cache.clear()   # fresh calendar and prices for each signal
            f = _fundamentals(args, args.symbol, "day")
            print()
            _report(market, args, money, f)
            shots = _screenshots(market, args, args.symbol, Path(args.out), f"signal-{stamp:%Y%m%d-%H%M}-", f,
                                 args.interval, f"signal {stamp:%d %b %H:%M}")
            print("screenshots -> " + ", ".join(str(Path(args.out) / n) for _, n in shots))
            print("\a", end="", flush=True)
        else:
            print(f"{stamp:%Y-%m-%d %H:%M} {px(an.price)}  no setup", flush=True)
        time.sleep(step - time.time() % step + 2)   # just after the next candle closes


def _live_feed(args):
    """Closed candles for paper trading: Deriv by default, Yahoo for gold/forex."""
    step = data.DERIV_GRANULARITY[args.interval]

    def feed(symbol: str, interval: str, count: int):
        if args.source == "deriv":
            return data.load_deriv(symbol, interval, count, args.app_id)
        if args.source == "yahoo":
            seconds = data.DERIV_GRANULARITY[interval]   # the entry or the lower timeframe
            period = "7d" if seconds == 60 else "60d" if seconds < 3600 else "730d"
            df = data.load_yahoo(symbol, interval, period)
            closed = df[df.time + pd.Timedelta(seconds=seconds) <= pd.Timestamp.now(tz="UTC")]   # drop the forming one
            return closed.tail(count).reset_index(drop=True)
        raise SystemExit("paper trading needs live prices: --source deriv (or yahoo for gold/forex)")
    return feed, step


def _news_veto(args):
    """No new orders within 30 minutes of high-impact news for the symbol's currencies."""
    sym = data.deriv_symbol(args.symbol)
    if args.no_news_filter or fund.currencies(sym) is None:
        return None
    cache = {"at": 0.0, "events": []}

    def veto(when, signal):
        if time.time() - cache["at"] > 6 * 3600:   # refresh the calendar a few times a day
            try:
                cache["events"], cache["at"] = fund.fetch_calendar(), time.time()
            except Exception as exc:
                print(f"note: calendar unavailable ({exc.__class__.__name__}) - news filter paused")
                cache["at"] = time.time() - 5 * 3600   # retry within the hour
        soon = fund.events_soon(cache["events"], sym, when.to_pydatetime(), minutes=30)
        return f"high-impact news: {', '.join(f'{e.currency} {e.title} {e.when:%H:%M} UTC' for e in soon)}" \
            if soon else None
    return veto


def _paper(args, money: MoneyManagement) -> None:
    import shutil
    from .paper import PaperTrader
    folder = Path(args.folder or f"paper/{re.sub(r'[^A-Za-z0-9]+', '', args.symbol)}-{args.interval}")
    if args.report:
        path = folder / "summary.md"
        print(path.read_text() if path.exists() else f"no paper account in {folder}")
        return
    if args.reset and folder.exists():
        shutil.rmtree(folder)
        print(f"deleted {folder}")
    if args.spread == 0 and args.fee_bps == 0:
        print("note: no trading costs set - pass --spread from your MT5 symbol specification")
    feed, step = _live_feed(args)
    trader = PaperTrader(args.symbol, args.interval, _config(args), money, args.equity, folder, feed,
                         spread=args.spread, fee_bps=args.fee_bps, step_seconds=step,
                         screenshots=not args.no_screenshots, news_veto=_news_veto(args))
    print("paper trading - virtual account, no orders are sent. Ctrl+C to stop; run again to resume.")
    trader.run()


def _download(args) -> None:
    """Save candles to data/<symbol>-<interval>.csv (comma-separated symbols allowed), for backtests
    with --csv or to share with someone who can't reach Deriv."""
    out = Path("data")
    out.mkdir(exist_ok=True)
    for symbol in _symbols(args):
        df = _load(args, symbol)
        path = out / f"{re.sub(r'[^A-Za-z0-9]+', '', symbol)}-{args.interval}.csv"
        df.to_csv(path, index=False)
        print(f"{symbol}: {len(df)} candles, {df.time.iloc[0]:%Y-%m-%d %H:%M} to {df.time.iloc[-1]:%Y-%m-%d %H:%M} "
              f"UTC -> {path}")


def _symbols(args) -> list[str]:
    return [s.strip() for s in args.symbol.split(",") if s.strip()]


def _is_synthetic(symbol: str) -> bool:
    return not data.deriv_symbol(symbol).startswith("frx")


def _outlook(args, horizon: str, symbols: list[str], now: dt.datetime | None = None) -> Path | None:
    if not symbols:
        return None
    spec = HORIZONS[horizon]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%d")
    outlooks, shots, funds, verdicts = [], {}, {}, {}
    from .screenshot import verdict
    for symbol in symbols:
        df = _load(args, symbol, spec["interval"], spec["count"])
        o = build_outlook(df, symbol, horizon, now)
        f = _fundamentals(args, symbol, horizon, now)
        outlooks.append(o)
        funds[symbol] = f
        verdicts[symbol] = verdict(o.market.analyze(len(o.market.c) - 1), f)
        try:
            shots[symbol] = _screenshots(o.market, args, symbol, out_dir, f"{horizon}-{stamp}-", f,
                                         spec["interval"], o.period, horizon)
        except ImportError:   # matplotlib not installed: text report only
            pass
    md = to_markdown(outlooks, horizon, fundamentals=funds, screenshots=shots, verdicts=verdicts)
    path = out_dir / f"outlook-{horizon}-{stamp}.md"
    path.write_text(md, encoding="utf-8")
    print(md)
    print(f"report -> {path}")
    return path


def _schedule(args) -> None:
    """Every evening at --at (local time): next-day outlook. On Sunday also the weekly outlook.
    Forex and gold are skipped when the next day is a weekend; volatility indices trade every day."""
    hh, mm = (int(x) for x in args.at.split(":"))
    symbols = _symbols(args)
    print(f"outlooks at {args.at} local time for {', '.join(symbols)} - Ctrl+C to stop")
    while True:
        now = dt.datetime.now()
        run = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if run <= now:
            run += dt.timedelta(days=1)
        print(f"next outlook {run:%a %d %b %H:%M}", flush=True)
        time.sleep((run - dt.datetime.now()).total_seconds())
        for horizon, due in outlooks_due(run, symbols):
            _safe(_outlook, args, horizon, due)


def outlooks_due(run: dt.datetime, symbols: list[str]) -> list[tuple[str, list[str]]]:
    """What to publish on the evening of `run`: the weekly outlook on Sunday, and a next-day
    outlook for every market that trades tomorrow (forex and gold rest on Saturday and Sunday)."""
    due = []
    if run.weekday() == 6:
        due.append(("week", symbols))
    tomorrow_is_weekday = (run.weekday() + 1) % 7 < 5
    day = [s for s in symbols if tomorrow_is_weekday or _is_synthetic(s)]
    if day:
        due.append(("day", day))
    return due


def _safe(fn, *a):
    try:
        fn(*a)
    except (SystemExit, Exception) as exc:   # keep the schedule alive through a network hiccup
        print(f"outlook failed: {exc}")


_argv: list[str] = []


def main(argv: list[str] | None = None) -> None:
    import sys
    global _argv
    _argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(_argv)

    mode = MODES.get(args.mode, {})
    args.interval = args.interval or mode.get("interval", "5m")
    args.period = args.period or mode.get("period")
    args.count = args.count or mode.get("count", 5000)
    money = _money(args)

    if args.command == "outlook":
        _outlook(args, args.horizon, _symbols(args))
        return
    if args.command == "schedule":
        _schedule(args)
        return
    if args.command == "watch":
        _watch(args, money)
        return
    if args.command == "paper":
        _paper(args, money)
        return
    if args.command == "download":
        _download(args)
        return

    market = _load_market(args, _config(args))
    result = None
    if args.command == "backtest":
        result = _backtest(market, args, money)
    else:
        f = _fundamentals(args, args.symbol, "day")
        _report(market, args, money, f)
        if args.screenshot:
            shots = _screenshots(market, args, args.symbol, Path(args.screenshot), "", f, args.interval)
            print("screenshots -> " + ", ".join(str(Path(args.screenshot) / n) for _, n in shots))
    if args.plot:
        from .plot import plot
        plot(market, args.plot, result, last=args.bars)
        print(f"chart -> {args.plot}")


if __name__ == "__main__":
    main()
