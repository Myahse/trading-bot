# TradeBot Expert Advisor (MT5)

`TradeBot.mq5` is the trading core of the Python bot, rewritten as a MetaTrader 5 Expert Advisor.
It runs inside MT5 on a chart and places the trades itself.

> **Not compiled yet.** It was written without access to MetaTrader, so it has never been compiled
> or run. Compile it first (below), and if MetaEditor reports errors, send them back and they get fixed.
> Then test it in the Strategy Tester and on a **demo** account before anything else.

## Install

1. In MT5: **File > Open Data Folder**, then go to `MQL5/Experts/`.
2. Copy `TradeBot.mq5` there.
3. In MT5, open MetaEditor (F4). Open `TradeBot.mq5` and press **Compile** (F7). The **Errors** tab at
   the bottom must say `0 errors`. Warnings are fine.
4. Back in MT5, the EA appears under **Navigator > Expert Advisors**. Turn on **Algo Trading** in the toolbar.

## Run it on a demo account

1. Log in to your **Deriv demo** MT5 account.
2. Open a chart: **M5** for the Scalp preset, **H4** for the Swing preset.
3. Drag **TradeBot** onto the chart. In **Common**, tick *Allow Algo Trading*. In **Inputs**, choose the
   preset and your risk.
4. The chart shows:
   - the 2 nearest zones on each side
   - the nearest higher-timeframe zones (purple outline)
   - trendlines: thick purple for the higher timeframe, dotted after a break
   - the nearest order blocks
   - swing labels (HH / HL / LH / LL)
   - for a setup, the entry, SL and TP lines
5. The top-left panel shows how the bot reads the market, step by step, and what it is waiting for.

On a **real** account it only analyses and never sends an order, unless you set *Allow trading a
REAL account* to `true`. Keep it on demo until weeks of results justify more.

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
| Structure | Swing highs and lows (fractals), support/resistance zones, trendlines chosen from pairs of swings, order blocks, trend (HH/HL vs LH/LL) on the chart timeframe and on the higher timeframe |
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

Choose **Custom** to set every value yourself in the inputs.

### Differences from the Python bot

- **Higher-timeframe data:** read straight from MT5's H1/D1/... candles (Python builds them from
  the chart's candles). Higher-timeframe trendlines therefore break on higher-timeframe closes.
- **Breakouts:** only entry-chart trendlines trigger breakout entries. Higher-timeframe lines are used
  for retests and as targets.
- **History used:** the last 800 chart candles and 300 higher-timeframe candles, not all history.
- **Confirmation timing:** the confirmation is watched tick by tick. A pending setup is forgotten if
  the EA is restarted.
- **Not included:** the screenshots with the step panel, fundamentals and outlooks stay in the Python
  bot. The EA has its own chart drawings, panel and screenshots.
