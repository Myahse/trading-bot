# TradeBot Expert Advisor (MT5)

`TradeBot.mq5` is the trading core of the Python bot, rewritten as a MetaTrader 5 Expert Advisor.
It runs inside MT5 on a chart and places the trades itself.

| File | What it is |
|---|---|
| `TradeBot.mq5` | the Expert Advisor: orders, money management, chart drawings, panel |
| `TradeBotCore.mqh` | the analysis it shares with the parity check: swings, zones, trendlines, order blocks, setups |
| `TradeBotParity.mq5` | a script that checks the EA's analysis against the Python bot (below) |

> **Compiled, but not traded yet.** It compiles in MetaEditor (MT5 build 6246) with 0 errors and
> 0 warnings. Its analysis was checked against the Python bot on gold and forex (below). It has
> not yet run in the Strategy Tester or on a demo account; both need you to log in to an account.
> Do both before anything else.

## Install

1. In MT5: **File > Open Data Folder**, then go to `MQL5/Experts/`.
2. Copy `TradeBot.mq5` **and** `TradeBotCore.mqh` there (the EA includes the core).
3. In MT5, open MetaEditor (F4). Open `TradeBot.mq5` and press **Compile** (F7). The **Errors** tab at
   the bottom must say `0 errors`.
4. Back in MT5, the EA appears under **Navigator > Expert Advisors**. Turn on **Algo Trading** in the toolbar.

## Run it on a demo account

1. Log in to your **Deriv demo** MT5 account.
2. Open a chart: **M5** for the Scalp preset, **H4** for the Swing preset.
3. Drag **TradeBot** onto the chart. In **Common**, tick *Allow Algo Trading*. In **Inputs**, choose the
   preset and your risk.
4. The chart shows:
   - the zones it could trade from, each with its plan: `BUY ZONE 4134.20-4136.20  TP 4146.50  R:R 2.7`.
     Scalp shows the 2 tradable zones nearest to price; Swing shows only the best one, with its SL and TP lines. A
     thicker outline means the zone sits on a higher-timeframe zone (`+ HTF`). Zones it would not
     trade (against the higher-timeframe trend, or too little R:R) are not drawn
   - trendlines: thick purple for the higher timeframe, dotted after a break
   - every break: a dot and **BREAK ▲** (green: price broke up through a falling line) or **BREAK ▼**
     (red: down through a rising one) on the candle that closed through. On a breakout setup the
     broken line is drawn thick, labelled **TRENDLINE BREAK**, with the ENTER HERE arrow
   - the nearest order blocks
   - chart patterns (double bottom/top, head and shoulders and its inverse): their swings joined, the
     neckline dashed, the name; and the candlestick pattern's name under/over the last candle
   - swing labels (HH / HL / LH / LL)
   - for a setup, the entry, SL and TP lines. A setup waiting for its confirmation stays on the chart
     until it fills or is cancelled. While it is on:
     - the zone it came off is filled strong, outlined and labelled **ENTRY ZONE** (with the
       higher-timeframe zone, if one was tagged too), and the other zones fade
     - an arrow on the next candle points at the order level, labelled **ENTER HERE: BUY STOP**
       (or SELL STOP) and the price
5. The panel at the top left (a white box, green for buying, red for selling) shows how the bot reads
   the market and what it is waiting for, one row each:
   - **TREND:** both timeframes, and whether it is buys only, sells only or no direction
   - **PRICE:** the price and the nearest support and resistance
   - **LEVELS:** trendlines and order blocks
   - **PATTERNS:** the chart patterns, a triangle, the candlestick pattern ("shown only" on Scalp)
   - **BREAK** (after a trendline break): which line broke, where, how many candles ago, and whether to
     buy/sell on the breakout or the retest, or that it goes against the trend
   - **SETUP:** the live setup, the one waiting for its confirmation, or none
   - **ZONES** (Scalp: how many are tradable, and the best) or **ENTRY ZONE** (Swing), with SL, TP and R:R
   - **MAIN** and **ALTERNATIVE:** the two scenarios
   - **STATUS:** trading, in a trade, limits reached, ...

   The scenarios read like this:
   - **Main:** e.g. *H1 uptrend: expect a pullback into the buy zone 4129.93-4132.90, a rejection
     there, then a move to 4146.50. The plan fails on a close below 4128.79.*
   - **Alternative:** what happens if that level breaks, and the next zone to watch.

   These are levels to watch, not orders: an entry still needs the rejection candle and its confirmation.

**Alerts** (inputs *Alerts*): a pop-up with sound, and a push notification to your phone, when
- a trendline breaks (`BREAK UP` / `BREAK DOWN`, with the line and price)
- the trend changes (`TREND CHANGE - H1 trend is now down`)
- a setup forms (`ENTER HERE - BUY STOP ..., SL ..., TP ...`)

For the phone: install the MetaTrader 5 app, copy its MetaQuotes ID (Settings > Chats and messages),
then in the desktop MT5 go to **Tools > Options > Notifications**, tick *Enable Push notifications*
and paste the ID. Alerts are off in the Strategy Tester.

On a **real** account it only analyses and never sends an order, unless you set *Allow trading a
REAL account* to `true`. Keep it on demo until weeks of results justify more.

## Check that it matches the Python bot

`TradeBotParity.mq5` runs the EA's own analysis (`TradeBotCore.mqh`) on every closed candle of a
chart. It writes what it sees to `MQL5/Files/TradeBot_parity_<symbol>_<tf>.csv`, and the candles it
used to `..._candles.csv`. The Python bot then analyses the same candles and compares, column by column:
the ATR, swings, trend, nearest zones on both timeframes, trendlines, order blocks, every setup and
the zone plans (how many zones are tradable, and the best one with its stop and target).

1. Copy `TradeBotParity.mq5` and `TradeBotCore.mqh` to `MQL5/Scripts/` and compile.
2. Open a chart (M5 for Scalp, H4 for Swing). Drag **TradeBotParity** onto it, choose the preset
   and how many candles to check.
3. Copy the two CSV files from `MQL5/Files/` next to the Python bot and run:

```bash
python -m tradebot.parity TradeBot_parity_XAUUSD_M5.csv --mode scalp
```

```
12000 candles compared
  ATR and swings     match
  Entry-chart zones  match
  Higher timeframe   match
  Trendlines         match
  Order blocks       match
  Signals            match
  Setups: MT5 52, Python 52, identical 52, MT5 only 0, Python only 0
```

**Results (October 2026, Yahoo candles imported into MT5 as custom symbols):**

| Market | Scalp M5 (12,000 candles) | Swing H4 (3,400 candles) |
|---|---|---|
| XAUUSD | everything matches; 52/52 setups identical | 25/25 setups identical; HTF trendline count differs on 0.2% of candles |
| GBPJPY | everything matches; 52/52 | 25/25; 0.5% |
| USDJPY | everything matches; 45/45 | 23/23; 2.8% |
| GBPUSD | everything matches; 61/61 | 13/13; 0.2% |

The swing differences are the trendline spacing described above.

`tests/test_mt5_port.py` keeps a GBPUSD M5 run from MT5 (`tests/data/`) and checks it on every
`pytest` run, along with the presets and fixed rules. If you change the Python strategy, make the same
change in `TradeBotCore.mqh`, then regenerate those two files with the script.

The check found and fixed four differences in the first port:
- **ATR:** the EA's Wilder formula differed from pandas in the last bit. Swings often sit on
  exactly the same forex prices, so that bit can put a swing in a different zone. On GBPUSD M5 it
  changed 455 of 3,265 zone updates. The EA now computes the ATR exactly as pandas does.
- **Higher-timeframe candles:** the EA used an H1/D1 candle one chart candle earlier than Python.
- **Higher-timeframe zone widths:** these used a different ATR from Python.
- **Higher-timeframe trendlines:** the EA drew these on higher-timeframe candles and never traded
  their breakouts. They now work as in Python.

Even with the first three fixed, 15 of about 245 setups still differed between the EA and the Python bot.

## Backtest it on Deriv's own prices

**View > Strategy Tester** (Ctrl+R):
- **Expert:** TradeBot
- **Symbol:** for example *Volatility 75 Index*, XAUUSD or GBPJPY
- **Timeframe:** M5 (Scalp) or H4 (Swing)
- **Modelling:** *Every tick based on real ticks* (most realistic) or *Every tick*
- **Deposit:** your real amount, e.g. 20 USD; **Leverage:** your account's

The tester uses Deriv's prices, spreads and contract specifications, so these are the backtests that
count. Run several months, then compare the **Report** (profit factor, drawdown, trades) with
what the Python backtests showed. Tick *Visual mode* to watch it trade candle by candle.

## What it does (same rules as the Python bot)

| Part | Detail |
|---|---|
| Structure | Swing highs and lows (fractals), support/resistance zones **with a memory** (they change only when a new swing confirms, not on every candle; all history on the higher timeframe), trendlines chosen from pairs of swings, order blocks, trend (HH/HL vs LH/LL) on the chart timeframe and on the higher timeframe |
| Setups | **Rejection:** price tags 2+ levels and closes back the other way. **Breakout:** a strong candle closes through a trendline. Both only in the higher-timeframe direction |
| Confirmation | **Break:** enter when price trades through the signal candle's high/low within N candles, cancelled if the stop level trades first. **Close:** enter after a candle closes beyond it. **None:** enter at the next open |
| Stop / target | The stop goes beyond the levels used (and at least `min stop ATR` away). The target is the nearest opposing level, or a default R. Setups below the minimum reward:risk are skipped. Both sit on the server |
| Size | Risk % of the balance, from MT5's tick value (correct for any symbol, JPY crosses included). If the minimum lot would risk more than the limit, or margin is short, the trade is skipped |
| Management | Partial profit at +1R (lots permitting), stop to break-even, trailing stop (ATR behind the best price, or behind each new swing). Stops only tighten |
| Limits | Daily loss limit and max trades per day (all charts running this magic number together), a cooldown after a loss |
| News | No new trades within 30 minutes of high-impact news for the symbol's currencies (forex/gold), from MT5's built-in economic calendar. Not used in the Strategy Tester |
| Journal | Every event goes to the Experts log and `MQL5/Files/TradeBot_journal.csv`. Screenshots of every setup and fill go to `MQL5/Files/` |

### Presets

| | Scalp | Swing |
|---|---|---|
| Chart / higher timeframe | M5 / H1 | H4 / D1 |
| Swing size (chart / HTF) | 3 / 3 | 5 / 3 |
| Min reward:risk / default target | 1.5 / 1.5R | 2.0 / 2.5R |
| Confirmation | break, 3 candles | break, 2 candles |
| Risk per trade | 0.5% | 1% |
| Partial / break-even / trail | 50% at 1R / 1R / 1.5 ATR from 1R | 50% at 1.5R / 1R / swings from 1.5R |
| Daily limits | -3%, 8 trades | off |
| Patterns count as levels | no (shown only) | yes |

Choose **Custom** to set every value yourself in the inputs.

### Differences from the Python bot

- **Higher-timeframe data:** read from MT5's H1/D1/... candles. Python builds them from the chart's
  candles. On the same prices they are the same candles. As in Python, a higher-timeframe candle
  counts from the first chart candle after it closed. Higher-timeframe trendlines are drawn through
  its swings on the chart's candles, so they break, and can trigger breakouts, on a chart candle's close.
- **Higher-timeframe trendline spacing:** their minimum length and retest window are scaled by the
  average number of chart candles per higher-timeframe candle. The EA averages the candles it has
  loaded; Python averages the whole download. On swing (H4/D1) forex this moves the window by one
  candle now and then (about 5.43 vs 5.51 candles per day). In the checks below that changed no setups.
- **History used:** the last `History: chart candles` (default 5,000) and `higher-timeframe candles`
  (default 1,000, about 4 years of D1) candles. Higher-timeframe zones are built from **all** of
  them, so old daily/weekly levels still count, as in the Python bot. More history is slower: in
  the Strategy Tester, 2,000 chart candles gives the same signals and runs faster.
- **Confirmation timing:** the confirmation is watched tick by tick. A pending setup is forgotten if
  the EA is restarted.
- **Not included:** the screenshots with the step panel, fundamentals and outlooks stay in the Python
  bot. The EA has its own chart drawings, panel and screenshots.
