# tradebot: support/resistance, trendlines and order blocks

A rule-based trading algorithm. It reads OHLCV candles, works out the market structure,
decides whether to take a long or short position, and backtests the result.

> Educational code, not financial advice. Backtest and paper-trade before risking money.

## What it looks at

| Concept | How it is detected (`tradebot/structure.py`, `tradebot/orderblocks.py`) |
|---|---|
| **Swing points** | Fractal pivots: a high/low that is the extreme of `pivot` bars on each side. A pivot is only *known* `pivot` bars later, so there is no look-ahead. |
| **Support / resistance** | Recent pivots whose prices sit within `0.6 × ATR` of each other are clustered into zones (2+ touches). A zone below price is support, above price is resistance. |
| **Trendlines** | Rising support through the last two higher lows, falling resistance through the last two lower highs. A line is dropped once a close breaks it. |
| **Order blocks** | Bullish OB = the last red candle before an impulsive move (≥ 1 ATR) that closes above the last swing high (break of structure). Bearish is the mirror. The OB is dead once price closes through it, or after 200 bars. |
| **Bias** | Higher highs + higher lows = up, lower highs + lower lows = down. |

## Entry rules (`tradebot/strategy.py`)

**Long**, evaluated at the close of each bar:
1. Price tags at least **2** of: a support zone, a rising trendline, a bullish order block.
2. The bar is a bullish rejection: it closes green, in the top half of its range.
3. The bias is not down (trend filter).
4. The stop goes just below the lowest level that was tagged (with a 0.3 ATR buffer).
5. The target is the nearest thing in the way: a resistance zone, a bearish OB or a falling trendline.
   With nothing overhead it is 2R. The trade is skipped if reward:risk < 1.5.

**Short** is the exact mirror. Orders fill at the next bar's open. Each trade risks 1% of equity.
The backtest charts 5 bps fees per side and, when one bar hits both stop and target, assumes the stop was hit first.

## Usage

```bash
pip install -r requirements.txt

python -m tradebot analyze --symbol BTC-USD                 # what the bot sees right now and any signal
python -m tradebot backtest --symbol EURUSD=X --interval 1h --period 1y --plot chart.png --trades trades.csv
python -m tradebot backtest --csv my_data.csv               # columns: time,open,high,low,close[,volume]
python -m tradebot backtest                                 # synthetic demo data, no network needed

# tuning
python -m tradebot backtest --symbol AAPL --min-confluence 1 --min-rr 2 --pivot 3 --no-trend-filter --risk 0.005
```

From Python:

```python
from tradebot import StrategyConfig, analyze, run_backtest
from tradebot.data import load_yahoo

df = load_yahoo("BTC-USD", "4h", "1y")      # Yahoo limits intraday history
signal = analyze(df).signal                  # None, or side/entry/stop/target/reasons
result = run_backtest(df, StrategyConfig(min_confluence=2))
print(result.stats)
```

## Tests

```bash
python -m pytest
```

`test_no_lookahead` checks that each decision at bar *t* is the same when all bars after *t* are removed.

## Next steps

- Connect a broker or exchange API (e.g. `ccxt` for crypto, OANDA or Alpaca) and call `analyze()` on each new closed candle.
- Add a higher-timeframe filter, for example taking 1h entries only in the direction of the daily bias.
- Walk-forward optimise the parameters instead of fitting them to a single backtest.
