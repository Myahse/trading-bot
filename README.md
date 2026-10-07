# tradebot: support/resistance, trendlines and order blocks

A rule-based trading algorithm for **Deriv** markets: volatility indices (V10-V100, 1s
variants), gold and forex (GBP, JPY, USD pairs). It reads candles straight from Deriv's API,
works out the market structure on two timeframes, decides whether to go long or short,
waits for confirmation, sizes and manages the position (break-even, partial profit, trailing
stop, daily loss limit), backtests the rules, watches a market live and prints signals,
**paper-trades a virtual account on live candles**, and publishes a **weekly outlook every
Sunday and a next-day outlook every evening**. It never places orders.

> Educational code, not financial advice. Backtest and paper-trade before risking money.

**MetaTrader 5 Expert Advisor:** the trading core is also available as an EA that runs inside MT5
and places trades itself. See [`mql5/`](mql5/README.md). Compile it in MetaEditor, then test it in
the Strategy Tester on Deriv's own prices and on a demo account.

## Modes

| | `--mode scalp` | `--mode swing` |
|---|---|---|
| Entry candles | 5m (1m-15m all work) | 4h |
| Structure / trend from | 1h candles | daily candles |
| Swing size | 3 bars each side | 5 bars each side |
| Minimum reward:risk | 1.5 | 2.0 |
| Entry | **surgical**: 1m change of character in the entry zone, within 3 candles | surgical on 15m, within 2 candles |
| Risk per trade | 0.5% | 1% |
| Partial profit | 50% at +1R | 50% at +1.5R |
| Break-even | at +1R | at +1R |
| Trailing stop | 1.5 ATR behind the best price, from +1R | behind each new swing, from +1.5R |
| Daily limits | stop after -3% or 8 trades | - |
| Stand aside after a loss | 6 bars | 3 bars |

Any flag overrides the preset, e.g. `--mode scalp --interval 1m --htf 15min --trail swing`.

## What it looks at

Everything is computed on **two fractal timeframes**: the same swing, zone and trend
logic runs on the entry candles and on higher-timeframe candles built from them (`--htf`).
The higher timeframe sets the direction and adds its own zones. A higher-timeframe candle
is used only after it has closed.

| Concept | How it is detected (`tradebot/structure.py`, `orderblocks.py`, `fractal.py`) |
|---|---|
| **Swing points (fractals)** | A high/low that is the extreme of `pivot` bars on each side, on both timeframes. A swing is only *known* `pivot` bars later, so there is no look-ahead. |
| **Support / resistance** | Zones with a memory, as a trader keeps levels on the chart. A zone is born from a swing, with its width fixed by the ATR at that moment. Later swings at the same price add touches without moving it, so zones only change when a new swing confirms (about 5% of candles), never on every candle. Broken zones stay (support becomes resistance). The 30-40 most recently touched zones are kept on the entry chart, and up to 200 on the higher timeframe, so old daily and weekly levels count. |
| **Trendlines** | Drawn the way a trader would. Every pair of the last 8 swing lows (rising) or swing highs (falling) is a candidate. A line is rejected if any candle between its anchors pokes through it (wicks included), if the anchors are too close together, or if it is absurdly steep. The survivors are ranked by touches (swing points sitting on the line), then the most recent touch, then length. On each side the bot keeps the best intact line and the best just-broken one (for the retest). It does the same on the higher timeframe. |
| **Order blocks** | Bullish OB = the last red candle before an impulsive move (>= 1 ATR) that closes above the last swing high (break of structure). Bearish is the mirror. It is dead once price closes through it, or after `ob_max_age` bars. |
| **Bias** | Higher highs + higher lows = up, lower highs + lower lows = down. Trades must agree with the higher-timeframe bias. |

## Entry rules (`tradebot/strategy.py`)

Two setups (longs shown; shorts are the mirror). Both are evaluated on each candle close.

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

### Surgical entries (`--confirm refine`, the default in both modes)

The 5m setup says **where** to trade. The 1m chart says **when**, so the stop can be small:

1. **Entry zone.** The setup marks a zone from the level that was tagged (zone, trendline, order
   block) to the signal candle's close. The chart draws it in blue.
2. **Change of character (CHoCH).** The bot waits for a 1m candle to close through the last 1m
   swing against the trade: above the last lower high for a long, below the last higher low for a
   short. It enters at that close.
3. **Stop.** The stop goes just beyond the 1m swing that made the turn: the lowest low since that
   lower high, minus 0.3 x the 1m ATR. It is never closer than 1 x the 1m ATR, to leave room for the
   spread. The target stays the same, so the stop is a fraction of the 5m stop and reward:risk is
   higher. The lot size is calculated from this tighter stop.
4. **Cancelled** if price trades through the 5m stop level first, or if no CHoCH comes within
   `--confirm-bars` 5m candles. It is skipped if the refined reward:risk is still below `--min-rr`.
5. **In the trade,** stop and target are checked 1m candle by 1m candle, so the backtest knows which
   came first.

`--ltf` picks the lower timeframe (scalp `1m`, swing `15m`). The 1m candles come from the same source:
- **Deriv:** as much 1m history as the 5m history.
- **Yahoo:** only the last 7 days of 1m candles.
- **`--csv`:** pass `--ltf-csv`.
- **`--source sim`:** the 1m candles are simulated and the 5m candles are built from them.

Where there are no 1m candles, the setup falls back to a `break` entry. The backtest prints how much
of the history the 1m candles cover.

```bash
python -m tradebot backtest --mode scalp --symbol V75                      # surgical entries (default)
python -m tradebot backtest --mode scalp --symbol V75 --confirm break      # the old entries, to compare
python -m tradebot backtest --mode scalp --csv gbpusd-5m.csv --ltf-csv gbpusd-1m.csv
```

On simulated V75 the refined stops were about half the size of the `break` stops. Check it on
your own symbols with the two commands above.

### Other confirmations (`--confirm`)

- **`break`**: a buy-stop is placed at the signal candle's high (sell-stop at the low).
  It fills only if price breaks that level within `--confirm-bars` candles. It is cancelled if
  price reaches the stop level first.
- **`close`**: the bot waits for a candle to close beyond the signal candle's high/low, then
  enters at the next open.
- **`none`**: enter at the next open.

With `break` and `close`, reward:risk is measured from the confirmation level, not from the signal
candle's close.

## Money management (`tradebot/money.py`)

- **Size:** the position is sized so that hitting the initial stop loses `--risk` of the account
  (scalp 0.5%, swing 1%), capped at `--leverage` x the account (default 30).
- **Lots:** signals print the lot size, using Deriv MT5 contract sizes (forex 100,000, gold 100,
  indices 1; override with `--contract-size`). For JPY crosses such as GBPJPY, pass `--quote-rate`
  (the USD value of 1 JPY).
- **Partial profit:** `--partial-pct` of the position is closed at `--partial` R.
- **Break-even:** the stop moves to entry at `--breakeven` R.
- **Trailing stop:** from `--trail-start` R the stop follows price:
  - `--trail atr`: `--trail-atr` x ATR behind the best price
  - `--trail swing`: just beyond each new swing low (long) / high (short)

  The stop only ever tightens.
- **Daily limits:** no new trades for the rest of the day after losing `--daily-loss` of the
  account, or after `--max-trades-day` trades.
- Any rule can be turned off, e.g. `--breakeven off --partial off --trail none --daily-loss off`.

The backtest moves stops only at candle closes, so it never assumes the order of prices inside
a candle. When one candle touches both the stop and the target, it assumes the stop came first.

## Small accounts and real lot sizes

For forex and gold, positions are sized in real lots. Each lot is 100,000 units for forex
and 100 oz for gold; the minimum is 0.01 lots, in steps of 0.01. Profit and loss is converted to USD.
- **Lot size:** the bot works out the lot size for `--risk`, then rounds it down to the lot step.
- **Minimum lot:** if that comes out below the minimum, the bot uses the minimum lot, but only
  while it risks no more than `--min-lot-max-risk` (default 5%) of the account. Above that the
  setup is skipped and the signal says **NOT TRADEABLE**, with the real risk.
- **Margin:** a position also needs margin. 0.01 lots of GBPUSD is about $1,300 of currency,
  so a $20 account needs at least ~65x leverage. Set `--leverage` to your account's.
- **Partial profit:** 0.01 lots can't be halved, so on tiny positions the partial take-profit is
  skipped.
- **Crosses:** for crosses such as GBPJPY, pass `--quote-rate` (USD per 1 JPY, e.g. 1/150).
- **Volatility indices:** their minimum volume varies, so pass `--min-lot` from the MT5
  specification. Without it they are sized in plain units.

```bash
python -m tradebot analyze  --mode scalp --symbol GBPUSD --equity 20 --leverage 500
python -m tradebot backtest --mode scalp --symbol GBPUSD --equity 20 --leverage 500 --spread 0.00015
python -m tradebot backtest --mode scalp --symbol V75 --equity 20 --min-lot 0.001   # check your spec
```

**What $20 did in a backtest.** These are 5-minute scalps over 60 days of Yahoo data, with the
spreads assumed above and 1:500 leverage assumed:

| Plan | XAUUSD | GBPUSD | USDJPY | GBPJPY |
|---|---|---|---|---|
| 2% risk, min lot up to 5%, stop the day at -10% | $20.00 (all 104 setups skipped) | $4.06 | $14.46 | $9.77 |
| 10% risk, min lot up to 20%, no daily stop | $18.99 (8 trades) | $2.08 | $5.95 | $2.29 |

- **Gold:** 0.01 lots on a $6-7 stop risks about a third of $20, so gold can't be scalped
  sensibly on that account.
- **Forex:** at the minimum lot each trade already risks 2-5%. The spread is a large share of a
  5-pip stop (1.5 of 5 pips on GBPUSD), and that is what drains the account.
- **Risking more** only loses faster.
- **Aim:** with a small account, aim to prove the signals over weeks, not to grow it in a day.

## Paper trading: a $20 forward test (`tradebot/paper.py`)

Before risking money, let the bot trade a **virtual account on live candles** for a few weeks:

```bash
python -m tradebot paper --symbol GBPUSD --mode scalp --equity 20 --leverage 500 --spread 0.00015
python -m tradebot paper --symbol V75 --mode scalp --equity 20 --min-lot 0.001        # check your MT5 spec
python -m tradebot paper --symbol GBPUSD --mode scalp --equity 20 --leverage 500 --report   # how is it doing?
```

- **Same rules as the backtest:** it follows the market candle by candle with exactly the
  backtest's rules, through the same engine (confirmation, real lot sizes, partial profit,
  break-even, trailing stop, daily limits). The forward test can't quietly behave differently
  from the backtest.
- **No orders are sent.** Nothing touches your Deriv account.
- **News filter (gold and forex):** no new orders within 30 minutes of high-impact news.
  Turn it off with `--no-news-filter`.
- **Journal**, in `paper/<symbol>-<interval>/`:
  - `events.log`: every order placed or cancelled, fill, partial profit, stop move and close
  - `trades.csv`: every trade
  - `summary.md`: balance, results by day, open position
  - `shots/`: a technical screenshot of every order and every closed trade
- **Restarts are safe.** Stop it with Ctrl+C and run the same command again. It replays the
  saved candles to rebuild its exact state (open position, balance, limits) and carries on,
  catching up on candles that closed while it was off.
- **Same settings when resuming.** Use the same flags as the first run. A run with different
  settings is refused; `--reset` starts a fresh account.
- **Prices:** Deriv's are used by default. `--source yahoo` works for gold and forex.

To leave it running on Windows, start it in a terminal that stays open, or as a Task Scheduler task
"at log on". On Mac/Linux, use `nohup ... &` or a `tmux` session.

Compare the paper results with what a backtest of the same weeks gives. If they match and the
results hold up over several weeks, the signals are worth a closer look.

## Screenshots: how the bot read the market

Every analysis can be saved as two images, so you can see each step:

**Technical** (`<symbol>-technical.png`):
1. **Big picture:** higher-timeframe candles with swing labels (HH, HL, LH, LL), zones,
   trendlines and the candle still forming.
2. **Entry chart:** zones, trendlines with their touches and breaks, order blocks, swing labels
   and, when there is a setup, the trade plan drawn like TradingView's long/short position tool:
   the BUY/SELL STOP entry line, a green box up to the take-profit and a red box down to the
   stop-loss, each labelled with its distance (% and R). Paper-trading screenshots also draw the
   open trade and the closed ones as boxes.
3. **"How the bot read it":** a panel listing each step and its result (✓ / ✗):
   - the trend, with the swings behind it
   - where price sits against the nearest levels (in ATR)
   - trendlines and order blocks
   - the setup and its confirmation, and the risk
   - the fundamental bias
   - a final verdict (take it if confirmed / wait / conflict)

**Fundamental** (`<symbol>-fundamental.png`, forex and gold):
- **Economic calendar:** high and medium impact events for the currencies involved, tomorrow or
  this week, from the free ForexFactory feed.
- **Currency strength:** each major's % change against the basket of majors (1 day / 5 days).
- **Gold drivers:** US dollar index (DXY), US 10-year yield and VIX, with what each move means
  for gold.
- **Your inputs:** central-bank rates (carry) and your views from `fundamentals.json`. Copy
  `fundamentals.example.json` and fill it in after each central-bank meeting.
- **Fundamental bias** (bullish / bearish / neutral) with every reason listed. If it disagrees
  with the technical setup, the verdict says **conflict**: skip the trade or use half size.

High-impact news within the hour is flagged on live signals: don't enter around it.

Volatility indices get a card explaining why they have no fundamentals. Deriv generates them
with a random number generator, so only the technical picture and risk management apply.

The calendar feed has forecasts but no actual results, so the bot uses it to warn about upcoming
risk. It does not score data surprises.

```bash
python -m tradebot analyze --mode swing --symbol XAUUSD --screenshot shots/   # both images now
python -m tradebot watch   --mode scalp --symbol GBPJPY                      # saves both on every signal
python -m tradebot outlook --symbol XAUUSD,GBPJPY,V75 --horizon week         # report embeds both per market
```

Add `--no-fundamentals` to skip the downloads.

## Outlooks: Sunday for the week, every evening for the next day (`tradebot/outlook.py`)

For each market the report gives:
- the trend on the higher timeframe (daily for the next-day outlook, weekly for the week)
- the typical range (ATR) and classic pivot points
- the key levels above and below price: zones, trendlines projected to the end of the period,
  order blocks and pivots. Where they cluster they are merged and marked **confluence**.
- a main scenario (with the trend) and an alternative
- the trendline breaks and retests to watch, and any live setup

Each report is saved as Markdown with a technical and a fundamental screenshot per market, a
fundamentals section (bias, reasons, calendar table) and a verdict that combines both.

```bash
python -m tradebot outlook --symbol XAUUSD,GBPJPY,USDJPY,V75 --horizon week   # Sunday
python -m tradebot outlook --symbol XAUUSD,GBPJPY,USDJPY,V75 --horizon day    # each evening
python -m tradebot schedule --symbol XAUUSD,GBPJPY,USDJPY,V75 --at 18:00     # leave running: does both
```

`schedule` publishes the weekly outlook every Sunday at `--at` (local time) and a next-day
outlook every evening. Forex and gold are skipped when the next day is Saturday or Sunday;
volatility indices are included every day. Reports go to `reports/` (change with `--out`).

To run it without leaving a terminal open:

```bash
# Mac / Linux: crontab -e
55 17 * * 0   cd /path/to/trading-bot && python -m tradebot outlook --symbol XAUUSD,GBPJPY,V75 --horizon week
55 17 * * 0-4 cd /path/to/trading-bot && python -m tradebot outlook --symbol XAUUSD,GBPJPY,V75 --horizon day
```

On Windows, create two tasks in Task Scheduler that run the same commands with the working
folder set to `trading-bot`: weekly on Sunday, and daily Sunday to Thursday.

## Clean chart

`--plot chart.png` draws only what matters at the latest bar:
- the 2 nearest zones on each side of price
- the nearest higher-timeframe zone on each side (outlined)
- the order block closest to price on each side
- the setup and the trades as **position boxes**, like TradingView's long/short position tool:
  - green box from the entry to the take-profit: where to get out with a profit
  - red box from the entry to the stop-loss: where to get out with a loss
  - a setup's box starts at the signal candle and is labelled with its distances (% and R);
    the entry line is the BUY/SELL STOP level
  - a closed trade's box runs from its entry to its exit, with a dotted line from entry price
    to exit price, an X at the exit and the result in R above it
  - a trade still open runs to the right edge, with a dashed line where the stop is now
    (break-even or trailing)

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
python -m tradebot schedule --symbol XAUUSD,GBPJPY,V75 --at 18:00 # outlooks: Sunday + every evening
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
from tradebot.strategy import mode_config, mode_money

df = load_deriv("XAUUSD", "5m", count=20_000)
signal = analyze(df, mode_config("scalp")).signal     # None, or side/setup/trigger/stop/target/reasons
print(run_backtest(df, mode_config("scalp"), mode_money("scalp"), spread=0.3).stats)
```

## Backtest results so far

These runs used gold and forex from Yahoo (60 days of 5m for scalp, 2 years of 4h for swing).
Deriv's own data could not be reached from the environment this was built in. The spreads
are **my assumptions, not Deriv's figures**: XAUUSD 0.30, GBPJPY 0.03, USDJPY 0.015,
GBPUSD 0.00015.

"Old" enters at the next open with a fixed stop and target. "New" uses the presets above
(confirmation and money management).

| Mode | Market | Old: trades / win % / profit factor / return | New: trades / win % / profit factor / return |
|---|---|---|---|
| scalp | XAUUSD | 133 / 35% / 0.94 / -2.8% | 70 / 51% / 0.93 / -1.2% |
| scalp | GBPJPY | 96 / 26% / 0.84 / -7.5% | 72 / 40% / 0.35 / -16.2% |
| scalp | USDJPY | 119 / 32% / 0.56 / -18.5% | 56 / 48% / 0.71 / -4.5% |
| scalp | GBPUSD | 170 / 31% / 0.51 / -33.0% | 147 / 47% / 0.44 / -25.0% |
| swing | XAUUSD | 28 / 36% / 1.42 / +7.6% | 20 / 45% / 1.17 / +1.8% |
| swing | GBPJPY | 46 / 15% / 0.42 / -21.5% | 25 / 28% / 0.76 / -3.2% |
| swing | USDJPY | 41 / 20% / 0.58 / -13.8% | 26 / 23% / 0.52 / -9.1% |
| swing | GBPUSD | 38 / 29% / 0.86 / -3.7% | 32 / 31% / 0.58 / -8.7% |

Read these honestly:
- **Confirmation** filters out about half the trades and usually cuts losses. It does not
  create an edge.
- **Money management** raises the win rate to 40-50%, but taking half off at 1R and moving to
  break-even trims winners as much as it saves losers. Expectancy barely moves.
- **Forex scalping loses after spreads.** Only gold comes close to break-even on 5m candles.
  Swing on gold is the only positive line, and on 20-28 trades that is not proof.
- **Volatility indices are random by design.** Deriv generates them with a fixed volatility and
  no memory, so support, resistance and trendlines cannot predict them in the long run. Any
  backtest edge there is luck, and the spread is a guaranteed cost. Use them to practise
  execution, not to expect a statistical edge.
- Run your own backtests on Deriv data with your real spreads before trusting any of this,
  and paper-trade on a demo account first.

## Tests

```bash
python -m pytest
```

`test_no_lookahead` checks that each decision at bar *t* (with and without a higher timeframe) is the same when all bars after *t* are removed.

## Next steps

- Re-run the backtests on Deriv data with your real MT5 spreads.
- Paper-trade the `watch` signals on a Deriv demo account before any real money.
- Walk-forward optimise the parameters instead of fitting them to a single backtest.
