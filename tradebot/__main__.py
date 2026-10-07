"""Command line: `python -m tradebot analyze|backtest|watch --symbol V75 [--mode scalp|swing] ...`."""

from __future__ import annotations

import argparse
import re
import time

from . import data
from .backtest import run_backtest
from .strategy import MODES, Market, StrategyConfig
from .structure import px


def _load(args):
    if args.csv:
        return data.load_csv(args.csv)
    if args.source == "deriv":
        return data.load_deriv(args.symbol, args.interval, args.count, args.app_id)
    if args.source == "yahoo":
        return data.load_yahoo(args.symbol, args.interval, args.period)
    # sim: a Deriv-style volatility index with the volatility taken from the symbol (V75 -> 75%)
    vol = re.search(r"(10|25|50|75|100)", args.symbol or "")
    seconds = data.DERIV_GRANULARITY.get(args.interval, 300)
    return data.volatility_index(n=args.count, vol=int(vol[1]) / 100 if vol else 0.75,
                                 bar_seconds=seconds, seed=args.seed)


def _config(args) -> StrategyConfig:
    base = dict(MODES[args.mode]["config"]) if args.mode else {}
    overrides = dict(min_confluence=args.min_confluence, min_rr=args.min_rr, htf=args.htf,
                     pivot_left=args.pivot, pivot_right=args.pivot)
    base.update({k: v for k, v in overrides.items() if v is not None})
    if args.htf == "none":
        base["htf"] = None
    if args.no_trend_filter:
        base["trend_filter"] = False
    if args.no_breakouts:
        base["breakouts"] = False
    return StrategyConfig(**base)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument("command", choices=["analyze", "backtest", "watch"],
                        help="watch: re-analyse at every candle close and print new signals (Deriv only)")
    parser.add_argument("--mode", choices=sorted(MODES), help="scalp (5m entries, 1h structure) or "
                        "swing (daily entries, weekly structure); sets defaults that other flags override")
    parser.add_argument("--symbol", default="V75",
                        help="V10..V100, V75(1s), XAUUSD, GBPJPY, USDJPY, GBPUSD ... (default V75)")
    parser.add_argument("--source", choices=["deriv", "yahoo", "sim"], default="deriv",
                        help="deriv (default), yahoo (forex/gold only), sim (simulated volatility index)")
    parser.add_argument("--csv", help="OHLCV csv file instead of downloading")
    parser.add_argument("--interval", help="candle size: 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, 8h, 1d")
    parser.add_argument("--count", type=int, help="candles to download from Deriv / simulate")
    parser.add_argument("--period", help="history for --source yahoo, e.g. 60d, 730d")
    parser.add_argument("--app-id", type=int, default=1089, help="your Deriv API app id")
    parser.add_argument("--seed", type=int, default=7, help="seed for --source sim")
    parser.add_argument("--htf", help="higher timeframe for structure: 1h, 4h, D, W, a bar count, or none")
    parser.add_argument("--pivot", type=int, help="bars each side of a swing point")
    parser.add_argument("--min-confluence", type=int)
    parser.add_argument("--min-rr", type=float)
    parser.add_argument("--no-trend-filter", action="store_true")
    parser.add_argument("--no-breakouts", action="store_true", help="don't trade trendline breaks")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, help="fraction of equity risked per trade (default 0.01)")
    parser.add_argument("--spread", type=float, default=0.0,
                        help="spread in price units per round trip, as shown in MT5 (e.g. 0.35 for XAUUSD)")
    parser.add_argument("--fee-bps", type=float, default=0.0, help="commission per side, in bps of price")
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--plot", help="save a chart to this .png")
    parser.add_argument("--bars", type=int, default=300, help="bars shown on the chart")
    parser.add_argument("--trades", help="save trades to this .csv")
    args = parser.parse_args(argv)

    mode = MODES.get(args.mode, {})
    args.interval = args.interval or mode.get("interval", "5m")
    args.period = args.period or mode.get("period", "60d")
    args.count = args.count or mode.get("count", 5000)
    risk = args.risk if args.risk is not None else mode.get("risk", 0.01)

    if args.command == "watch":
        return _watch(args)

    market = Market(_load(args), _config(args))
    result = None

    if args.command == "analyze":
        _report(market)
    else:
        if args.spread == 0 and args.fee_bps == 0:
            print("note: no trading costs set - pass --spread from your MT5 symbol specification")
        result = run_backtest(market.df, initial_equity=args.equity, risk_per_trade=risk, fee_bps=args.fee_bps,
                              spread=args.spread, max_leverage=args.leverage, market=market)
        st = result.stats
        print(f"trades {st['trades']}   win rate {st['win_rate']:.1%}   profit factor {st['profit_factor']:.2f}   "
              f"avg R {st['avg_r']:.2f}")
        print(f"return {st['total_return']:.2%}   max drawdown {st['max_drawdown']:.2%}   "
              f"final equity {st['final_equity']:.2f}")
        if result.trades:
            frame = result.trades_frame()
            for setup, g in frame.groupby("setup"):
                print(f"  {setup:<9} {len(g):>3} trades, win rate {(g.pnl > 0).mean():.0%}, avg R {g.r.mean():.2f}")
        if args.trades:
            result.trades_frame().to_csv(args.trades, index=False)
            print(f"trades -> {args.trades}")

    if args.plot:
        from .plot import plot
        plot(market, args.plot, result, last=args.bars)
        print(f"chart -> {args.plot}")


def _report(market: Market) -> None:
    an = market.analyze(len(market.c) - 1)
    if "time" in market.df.columns:
        print(f"last closed candle {market.df.time.iloc[-1]}")
    print(f"price {px(an.price)}   ATR {an.atr:.4g}   bias {an.bias}"
          + (f"   HTF bias {an.htf_bias}" if an.htf_bias else ""))
    print("resistance:", ", ".join(f"{px(z.low)}-{px(z.high)} (x{z.touches})" for z in an.resistance[:3]) or "-")
    print("support:   ", ", ".join(f"{px(z.low)}-{px(z.high)} (x{z.touches})" for z in an.support[:3]) or "-")
    if market.htf is not None:
        print("HTF resistance:", ", ".join(f"{px(z.low)}-{px(z.high)}" for z in an.htf_resistance[:2]) or "-")
        print("HTF support:   ", ", ".join(f"{px(z.low)}-{px(z.high)}" for z in an.htf_support[:2]) or "-")
    for line in an.trendlines:
        state = "intact" if line.broken_at is None else f"BROKEN {an.bar - line.broken_at} bars ago"
        tf = "HTF " if line.htf else ""
        print(f"{tf}{line.kind} trendline now at {px(line.value_at(an.bar))} "
              f"({len(line.touches)} touches, {state})")
    for ob in an.order_blocks[-4:]:
        print(f"{ob.kind} order block {px(ob.low)}-{px(ob.high)}")
    s = an.signal
    if s:
        print(f"\nSIGNAL {s.side.upper()} ({s.setup}) next open ~{px(s.entry)}  stop {px(s.stop)}  "
              f"target {px(s.target)}  R:R {s.rr:.2f}")
        print("  because: " + "; ".join(s.reasons))
    else:
        print("\nno setup on the latest bar")


def _watch(args) -> None:
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
            print()
            _report(market)
            print("\a", end="", flush=True)
        else:
            print(f"{stamp:%Y-%m-%d %H:%M} {px(an.price)}  no setup", flush=True)
        time.sleep(step - time.time() % step + 2)   # just after the next candle closes


if __name__ == "__main__":
    main()
