# TradeBot cBot (cTrader)

`TradeBot.cs` is the MT5 Expert Advisor (`mql5/TradeBot.mq5` + `mql5/TradeBotCore.mqh`) rewritten as a
cTrader cBot. It finds the same zones, trendlines, order blocks and patterns, takes the same setups, and
sizes, protects and manages trades the same way.

| File | What it is |
|---|---|
| `TradeBot.cs` | the cBot, in one file: paste it into cTrader |
| `Parity/` | a check that runs the cBot's analysis on the MT5 parity candles and compares it with the EA (below) |

> **Built, but not traded yet.** It builds against cTrader's own API package (`cTrader.Automate`)
> with 0 errors and 0 warnings. Its analysis matches the EA on every candle of the MT5 parity fixture.
> Orders, partial closes and stop moves have not run yet, because that needs a cTrader account.
> Run it on a demo account first.

## Install

1. Open cTrader and log in to a **demo** account.
2. Go to **Algo** (left menu), then **cBots**, and click **New**. Name it `TradeBot`.
3. Delete the template code, paste all of `TradeBot.cs`, and click **Build** (Ctrl+B). The build result
   at the bottom must say *Build succeeded*.
4. Click **+** next to TradeBot to add an instance. Choose the symbol and timeframe: **M5** for the Scalp
   preset, **H4** for the Swing preset. Set the parameters, then press **Start**.

The cBot needs no extra permissions (`AccessRights.None`). It refuses to trade a live account unless
*Allow trading a LIVE account* is on.

## What it prints

The **Log** tab shows the same journal lines as the EA: `setup armed`, `filled`, `partial`, `stop moved`,
`order cancelled` and `skipped`, with the reason. At start it prints the symbol's **minimum volume**, its volume step and its lot size, all
read from the broker:

    TradeBot <symbol>: minimum volume <lots> lot (<units> units), step <units> units, lot size <units> units

That line tells you whether a small account can trade this symbol. The bot skips a setup when even the
minimum volume would risk more than *Skip if even the minimum volume risks more than this %* (5% on the
presets). *Allow the minimum volume up to this % risk on any preset* overrides that limit.

## Backtest and optimise

- **Backtest:** open the cBot instance and select the **Backtesting** tab. Pick the dates, choose
  *Tick data from server* for the most accurate fills, and press **Start**. The Log shows every
  decision, and the chart shows each trade.
- **Optimise:** on the **Optimisation** tab, set the *Preset* to **Custom**, because Scalp and Swing
  overwrite the other parameters. Tick the parameters to vary, such as minimum R:R, risk, swing size,
  trailing and break-even. Optimise on one period, then backtest the best settings on a later period
  the optimiser never saw. Settings that only work on the period they were tuned on are overfitted.

## Differences from the MT5 EA

The trading logic is the same. These parts are left out or work differently:

- **News filter:** cTrader has no economic calendar API, so there is no news filter.
- **Chart and panel:** the cBot draws the zones it would trade, the trendlines with their breaks, and
  the live setup's entry, SL and TP. The panel shows TREND, PLAN or ENTER, and STATUS. The full analysis
  view (patterns, fake breaks, swing labels, scenarios) is not ported.
- **Alerts and screenshots:** neither is ported. Use cTrader's own notifications instead.
- **Volume:** cTrader trades in units, and a lot is `Symbol.LotSize` units. The log shows lots.
- **"Setup already taken":** this is remembered while the cBot runs. A restart waits for the next candle
  anyway, so the same setup is not sent twice.
- **1R after a restart:** the stop at entry is written in the position's comment (`SL=...`), so
  break-even, trailing and the partial still measure R from the original stop.

## Parity check

`Parity/` runs the cBot's own analysis code on the 2,500 GBPUSD M5 candles that `mql5/TradeBotParity.mq5`
recorded inside MT5 (`tests/data/mt5_parity_GBPUSD_M5*.csv.gz`). It compares the result with what the EA
saw, column by column. Last run: all 35 columns agree on 100% of candles (ATR, swings, bias, zones,
higher-timeframe zones, trendlines, order blocks, setups with their stop, target and trigger, zone
plans, candle patterns), and both take the same 13 setups. The fixture uses the Scalp preset, so the
Swing preset's patterns-as-levels path is not covered.

    cd ctrader/Parity
    zcat ../../tests/data/mt5_parity_GBPUSD_M5_candles.csv.gz > candles.csv
    zcat ../../tests/data/mt5_parity_GBPUSD_M5.csv.gz > mt5.csv
    dotnet run -- candles.csv mt5.csv Scalp
