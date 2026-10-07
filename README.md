# tradebot: support/resistance, trendlines and order blocks

A rule-based trading algorithm for **Deriv** markets: volatility indices (V10-V100, 1s
variants), gold and forex (GBP, JPY, USD pairs). It reads candles straight from Deriv's API,
works out the market structure on two timeframes, decides whether to go long or short,
backtests the rules, and can watch a market live and print signals. It never places orders.

> Educational code, not financial advice. Backtest and paper-trade before risking money.

## Modes

| | `--mode scalp` | `--mode swing` |
|---|---|---|
| Entry candles | 5m (1m-15m all work) | 4h |
| Structure / trend from | 1h candles | daily candles |
| Swing size | 3 bars each side | 5 bars each side |
| Risk per trade | 0.5% | 1% |
| Minimum reward:risk | 1.5 | 2.0 |
| Stand aside after a loss | 6 bars | 3 bars |

Any flag overrides the preset, e.g. `--mode scalp --interval 1m --htf 15min`.

## What it looks at

Everything is computed on **two fractal timeframes**: the same swing, zone and trend
logic runs on the entry candles and on higher-timeframe candles built from them (`--htf`).
The higher timeframe sets the direction and adds its own zones. A higher-timeframe candle
is used only after it has closed.

| Concept | How it is detected (`tradebot/structure.py`, `orderblocks.py`, `fractal.py`) |
|---|---|
| **Swing points (fractals)** | A high/low that is the extreme of `pivot` bars on each side, on both timeframes. A swing is only *known* `pivot` bars later, so there is no look-ahead. |
| **Support / resistance** | Recent swings whose prices sit within `0.6 x ATR` of each other are clustered into zones (2+ touches), separately per timeframe. |
| **Trendlines** | Drawn the way a trader would. Every pair of the last 8 swing lows (rising) or swing highs (falling) is a candidate. A line is rejected if any candle between its anchors pokes through it (wicks included), if the anchors are too close together, or if it is absurdly steep. The survivors are ranked by touches (swing points sitting on the line), then the most recent touch, then length. On each side the bot keeps the best intact line and the best just-broken one (for the retest). It does the same on the higher timeframe. |
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
- the order block closest to price on each side
- the trades

Trendlines show:
- every swing point that touches them (o)
- where they broke (diamond)
- a dotted projection through the retest window
- higher-timeframe lines thicker, in purple

Labels sit in a margin on the right, so they never cover candles.

## Usage

```bash
pip install -r requirements.txt

python -m tradebot analyze  --mode scalp --symbol V75            # what the bot sees now, and any signal
python -m tradebot analyze  --mode swing --symbol XAUUSD
python -m tradebot backtest --mode scalp --symbol GBPJPY --spread 0.03 --plot chart.png --trades trades.csv
python -m tradebot watch    --mode scalp --symbol "V75(1s)"      # live: prints a signal at each candle close
```

Symbols are written the way you see them on Deriv: `V10`, `V25`, `V50`, `V75`, `V100`, the 1-second
versions `V75(1s)`, `XAUUSD`, `GBPJPY`, `USDJPY`, `GBPUSD`, ... Raw API symbols (`R_75`, `1HZ75V`,
`frxXAUUSD`) also work. Deriv data needs no account.

`--app-id 1089` is Deriv's public test app id. Register your own app at api.deriv.com for regular use.

Other data sources:
- `--source yahoo`: gold and forex history from Yahoo, up to 60 days of 5m candles or 2 years of 4h.
- `--source sim`: a simulated volatility index, for offline tests.
- `--csv file.csv`: any OHLC export with columns `time,open,high,low,close`.

**Costs:** pass the spread from your MT5 symbol specification in price units, e.g. `--spread 0.3`
on XAUUSD. It is charged once per round trip. Without it the backtest assumes free trading.

From Python:

```python
from tradebot import analyze, run_backtest
from tradebot.data import load_deriv
from tradebot.strategy import mode_config

df = load_deriv("XAUUSD", "5m", count=20_000)
signal = analyze(df, mode_config("scalp")).signal     # None, or side/setup/entry/stop/target/reasons
print(run_backtest(df, mode_config("scalp"), risk_per_trade=0.005, spread=0.3).stats)
```

## Backtest results so far

These runs used gold and forex from Yahoo, plus simulated V75. Deriv's own data could not be
reached from the environment this was built in. The spreads are **my assumptions, not Deriv's
figures**: XAUUSD 0.30, GBPJPY 0.03, USDJPY 0.015, GBPUSD 0.00015.

| Mode | Market | Trades | Profit factor (no spread) | Profit factor (with spread) | Return (with spread) |
|---|---|---|---|---|---|
| scalp 5m / 60 days | XAUUSD | 130 | 0.95 | 0.90 | -1.8% |
| scalp | GBPJPY | 121 | 0.97 | 0.72 | -1.8% |
| scalp | USDJPY | 106 | 1.03 | 0.88 | -0.8% |
| scalp | GBPUSD | 152 | 1.07 | 0.68 | -1.5% |
| scalp | V75 (simulated, 3 runs) | ~185 | 0.83-0.88 | - | -7% to -10% |
| swing 4h / 2 years | XAUUSD | 28 | 1.59 | 1.57 | +8.9% |
| swing | GBPJPY | 39 | 0.31 | 0.29 | -10.2% |
| swing | USDJPY | 30 | 0.94 | 0.92 | -0.9% |
| swing | GBPUSD | 27 | 0.60 | 0.57 | -4.3% |
| swing | V75 (simulated, 3 runs) | 25-44 | 0.54-1.05 | - | -15% to +1% |

Read these honestly:
- Apart from swing on gold, the rules show **no edge yet**, and the spread turns break-even scalps into losses.
- 28 trades on gold is too few to trust on its own.
- **Volatility indices are random by design.** Deriv generates them with a fixed volatility and no
  memory, so support, resistance and trendlines cannot predict them in the long run. Any
  backtest edge there is luck, and the spread is a guaranteed cost. Use V-indices to practise
  execution, not to expect a statistical edge.

## Tests

```bash
python -m pytest
```

`test_no_lookahead` checks that each decision at bar *t* (with and without a higher timeframe) is the same when all bars after *t* are removed.

## Next steps

- Re-run the backtests on Deriv data with your real MT5 spreads.
- Paper-trade the `watch` signals on a Deriv demo account before any real money.
- Walk-forward optimise the parameters instead of fitting them to a single backtest.
