"""Command line: `python -m tradebot analyze|backtest ...`."""

from __future__ import annotations

import argparse

from . import data
from .backtest import run_backtest
from .strategy import Market, StrategyConfig


def _load(args) -> "data.pd.DataFrame":
    if args.csv:
        return data.load_csv(args.csv)
    if args.symbol:
        return data.load_yahoo(args.symbol, args.interval, args.period)
    return data.synthetic(seed=args.seed)


def _config(args) -> StrategyConfig:
    return StrategyConfig(pivot_left=args.pivot, pivot_right=args.pivot, min_confluence=args.min_confluence,
                          min_rr=args.min_rr, trend_filter=not args.no_trend_filter)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument("command", choices=["analyze", "backtest"])
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--csv", help="OHLCV csv file")
    src.add_argument("--symbol", help="Yahoo Finance symbol, e.g. BTC-USD, EURUSD=X, AAPL")
    parser.add_argument("--seed", type=int, default=7, help="seed for synthetic data (default source)")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--pivot", type=int, default=5, help="bars each side of a swing point")
    parser.add_argument("--min-confluence", type=int, default=2)
    parser.add_argument("--min-rr", type=float, default=1.5)
    parser.add_argument("--no-trend-filter", action="store_true")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, default=0.01, help="fraction of equity risked per trade")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--plot", help="save a chart to this .png")
    parser.add_argument("--trades", help="save trades to this .csv")
    args = parser.parse_args(argv)

    market = Market(_load(args), _config(args))

    if args.command == "analyze":
        an = market.analyze(len(market.c) - 1)
        print(f"price {an.price:.5g}   ATR {an.atr:.4g}   bias {an.bias}")
        print("resistance:", ", ".join(f"{z.low:.5g}-{z.high:.5g} (x{z.touches})" for z in an.resistance[:3]) or "-")
        print("support:   ", ", ".join(f"{z.low:.5g}-{z.high:.5g} (x{z.touches})" for z in an.support[:3]) or "-")
        for line in an.trendlines:
            print(f"{line.kind} trendline now at {line.value_at(an.bar):.5g}")
        for ob in an.order_blocks[-4:]:
            print(f"{ob.kind} order block {ob.low:.5g}-{ob.high:.5g} (formed bar {ob.index})")
        s = an.signal
        if s:
            print(f"\nSIGNAL {s.side.upper()} next open ~{s.entry:.5g}  stop {s.stop:.5g}  "
                  f"target {s.target:.5g}  R:R {s.rr:.2f}")
            print("  because: " + "; ".join(s.reasons))
        else:
            print("\nno setup on the latest bar")
        result = None
    else:
        result = run_backtest(market.df, initial_equity=args.equity, risk_per_trade=args.risk,
                              fee_bps=args.fee_bps, max_leverage=args.leverage, market=market)
        st = result.stats
        print(f"trades {st['trades']}   win rate {st['win_rate']:.1%}   profit factor {st['profit_factor']:.2f}   "
              f"avg R {st['avg_r']:.2f}")
        print(f"return {st['total_return']:.2%}   max drawdown {st['max_drawdown']:.2%}   "
              f"final equity {st['final_equity']:.2f}")
        if args.trades:
            result.trades_frame().to_csv(args.trades, index=False)
            print(f"trades -> {args.trades}")

    if args.plot:
        from .plot import plot
        plot(market, args.plot, result)
        print(f"chart -> {args.plot}")


if __name__ == "__main__":
    main()
