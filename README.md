# tradebot: support/resistance, trendlines and order blocks

A rule-based trading algorithm. It reads OHLCV candles, works out the market structure,
decides whether to take a long or short position, and backtests the result.

> Educational code, not financial advice. Backtest and paper-trade before risking money.

## Modes

| | `--mode scalp` | `--mode swing` |
|---|---|---|
| Entry candles | 5m (any of 1m-15m works) | 1d |
| Structure / trend from | 1h candles | weekly candles |
| Swing size | 3 bars each side | 5 bars each side |
| Risk per trade | 0.5% | 1% |
| Minimum reward:risk | 1.5 | 2.0 |
| Stand aside after a loss | 6 bars | 3 bars |

Any flag overrides the preset, e.g. `--mode scalp --interval 1m --period 7d --htf 15min`.

## What it looks at

Everything is computed on **two fractal timeframes**: the same swing, zone and trend
logic runs on the entry candles and on higher-timeframe candles built from them (`--htf`).
The higher timeframe sets the direction and adds its own zones. A higher-timeframe candle
is used only after it has closed.

| Concept | How it is detected (`tradebot/structure.py`, `orderblocks.py`, `fractal.py`) |
|---|---|
| **Swing points (fractals)** | A high/low that is the extreme of `pivot` bars on each side, on both timeframes. A swing is only *known* `pivot` bars later, so there is no look-ahead. |
| **Support / resistance** | Recent swings whose prices sit within `0.6 x ATR` of each other are clustered into zones (2+ touches), separately per timeframe. |
| **Trendlines** | Rising support through the last two higher lows, falling resistance through the last two lower highs. The bot records the bar where a close **breaks** the line and keeps the line for `retest_window` bars afterwards to catch the retest. |
| **Order blocks** | Bullish OB = the last red candle before an impulsive move (>= 1 ATR) that closes above the last swing high (break of structure). Bearish is the mirror. It is dead once price closes through it, or after `ob_max_age` bars. |
| **Bias** | Higher highs + higher lows = up, lower highs + lower lows = down. Trades must agree with the higher-timeframe bias. |

## Entry rules (`tradebot/strategy.py`)

Two setups (longs shown; shorts are the mirror). Both are evaluated on each candle close and fill at the next open.

**1. Rejection.** Price tags at least **2** of:
- a support zone
- a higher-timeframe support zone
- a rising trendline
- the **retest of a falling trendline that was just broken**
- a bullish order block

The candle must also close green, in the top half of its range. The stop goes just below the lowest level that was tagged.

**2. Trendline breakout.** A strong green candle (body >= half its range) closes through a falling trendline. The stop goes below the breakout candle.

For both setups:
- The higher-timeframe bias must not be down.
- The stop is at least `min_stop_atr` away.
- The target is the nearest thing in the way on either timeframe: a resistance zone, a bearish OB or a falling trendline. With nothing in the way it is `default_rr` x risk.
- The trade is skipped if reward:risk is below `min_rr`.

The backtest charges `--fee-bps` per side. When one bar hits both the stop and the target, it assumes the stop was hit first.

## Clean chart

`--plot chart.png` draws only what matters at the latest bar:
- the 2 nearest zones on each side of price
- the nearest higher-timeframe zone on each side (outlined)
- the current trendlines (dotted after a break)
- the order block closest to price on each side
- the trades

It never draws every level ever detected.

## Usage

```bash
pip install -r requirements.txt

python -m tradebot analyze  --mode scalp --symbol BTC-USD       # what the bot sees right now, and any signal
python -m tradebot backtest --mode scalp --symbol EURUSD=X --fee-bps 0.5 --plot chart.png --trades trades.csv
python -m tradebot backtest --mode swing --symbol SPY --plot chart.png
python -m tradebot backtest --csv my_data.csv --htf 4h          # columns: time,open,high,low,close[,volume]
python -m tradebot backtest                                     # synthetic demo data, no network needed
```

Yahoo only serves 7 days of 1m candles and 60 days of 5m candles. For serious scalping
tests, export longer history from your broker or exchange as CSV.

From Python:

```python
from tradebot import analyze, run_backtest
from tradebot.data import load_yahoo
from tradebot.strategy import mode_config

df = load_yahoo("BTC-USD", "5m", "60d")
signal = analyze(df, mode_config("scalp")).signal    # None, or side/setup/entry/stop/target/reasons
print(run_backtest(df, mode_config("scalp"), risk_per_trade=0.005, fee_bps=1).stats)
```

## Costs decide scalping

5-minute stops are often only 2-5 bps away. Backtests on 60 days of Yahoo data:

| Run | Trades | Profit factor | Avg R |
|---|---|---|---|
| scalp EURUSD, 0 fees | 158 | 1.01 | +0.05 |
| scalp EURUSD, 1 bps/side | 158 | 0.48 | -0.86 |
| scalp BTC, 0 fees | 153 | 0.81 | -0.18 |
| swing BTC, 5y daily, 5 bps/side | 23 | 1.35 | +0.25 |

These are small samples, not proof of an edge either way. Test with your broker's
real spread and commission before scalping live.

## Tests

```bash
python -m pytest
```

`test_no_lookahead` checks that each decision at bar *t* (with and without a higher timeframe) is the same when all bars after *t* are removed.

## Next steps

- Connect a broker or exchange API (e.g. `ccxt` for crypto, OANDA or Alpaca) and call `analyze()` on each new closed candle.
- Walk-forward optimise the parameters instead of fitting them to a single backtest.
