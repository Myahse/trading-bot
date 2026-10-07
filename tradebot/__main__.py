"""Command line: `python -m tradebot analyze|backtest [--mode scalp|swing] ...`."""

from __future__ import annotations

import argparse

from . import data
from .backtest import run_backtest
from .strategy import MODES, Market, StrategyConfig


def _load(args):
    if args.csv:
        return data.load_csv(args.csv)
    if args.symbol:
        return data.load_yahoo(args.symbol, args.interval, args.period)
    return data.synthetic(seed=args.seed)


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
    parser.add_argument("command", choices=["analyze", "backtest"])
    parser.add_argument("--mode", choices=sorted(MODES), help="scalp (5m entries, 1h structure) or "
                        "swing (daily entries, weekly structure); sets defaults that other flags override")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--csv", help="OHLCV csv file")
    src.add_argument("--symbol", help="Yahoo Finance symbol, e.g. BTC-USD, EURUSD=X, AAPL")
    parser.add_argument("--seed", type=int, default=7, help="seed for synthetic data (default source)")
    parser.add_argument("--interval", help="candle size for --symbol, e.g. 1m, 5m, 1h, 1d")
    parser.add_argument("--period", help="history to download for --symbol, e.g. 60d, 2y")
    parser.add_argument("--htf", help="higher timeframe for structure: 1h, 4h, D, W, a bar count, or none")
    parser.add_argument("--pivot", type=int, help="bars each side of a swing point")
    parser.add_argument("--min-confluence", type=int)
    parser.add_argument("--min-rr", type=float)
    parser.add_argument("--no-trend-filter", action="store_true")
    parser.add_argument("--no-breakouts", action="store_true", help="don't trade trendline breaks")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, help="fraction of equity risked per trade (default 0.01)")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--plot", help="save a chart to this .png")
    parser.add_argument("--bars", type=int, default=300, help="bars shown on the chart")
    parser.add_argument("--trades", help="save trades to this .csv")
    args = parser.parse_args(argv)

    mode = MODES.get(args.mode, {})
    args.interval = args.interval or mode.get("interval", "1d")
    args.period = args.period or mode.get("period", "2y")
    risk = args.risk if args.risk is not None else mode.get("risk", 0.01)

    market = Market(_load(args), _config(args))
    result = None

    if args.command == "analyze":
        an = market.analyze(len(market.c) - 1)
        print(f"price {an.price:.5g}   ATR {an.atr:.4g}   bias {an.bias}"
              + (f"   HTF bias {an.htf_bias}" if an.htf_bias else ""))
        print("resistance:", ", ".join(f"{z.low:.5g}-{z.high:.5g} (x{z.touches})" for z in an.resistance[:3]) or "-")
        print("support:   ", ", ".join(f"{z.low:.5g}-{z.high:.5g} (x{z.touches})" for z in an.support[:3]) or "-")
        if market.htf is not None:
            print("HTF resistance:", ", ".join(f"{z.low:.5g}-{z.high:.5g}" for z in an.htf_resistance[:2]) or "-")
            print("HTF support:   ", ", ".join(f"{z.low:.5g}-{z.high:.5g}" for z in an.htf_support[:2]) or "-")
        for line in an.trendlines:
            state = "intact" if line.broken_at is None else f"BROKEN {an.bar - line.broken_at} bars ago"
            print(f"{line.kind} trendline now at {line.value_at(an.bar):.5g} ({state})")
        for ob in an.order_blocks[-4:]:
            print(f"{ob.kind} order block {ob.low:.5g}-{ob.high:.5g}")
        s = an.signal
        if s:
            print(f"\nSIGNAL {s.side.upper()} ({s.setup}) next open ~{s.entry:.5g}  stop {s.stop:.5g}  "
                  f"target {s.target:.5g}  R:R {s.rr:.2f}")
            print("  because: " + "; ".join(s.reasons))
        else:
            print("\nno setup on the latest bar")
    else:
        result = run_backtest(market.df, initial_equity=args.equity, risk_per_trade=risk,
                              fee_bps=args.fee_bps, max_leverage=args.leverage, market=market)
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


if __name__ == "__main__":
    main()
