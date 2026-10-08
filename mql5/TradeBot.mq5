//+------------------------------------------------------------------+
//|                                                     TradeBot.mq5 |
//|  Support/resistance, trendlines and order blocks on two          |
//|  timeframes, with confirmation entries and money management.     |
//|  MQL5 port of the Python bot (tradebot/strategy.py, backtest.py).|
//+------------------------------------------------------------------+
#property copyright "Myahse"
#property version   "1.00"
#property description "Trades rejections from support/resistance zones, trendlines and order blocks,"
#property description "and trendline breakouts, in the direction of the higher-timeframe structure."
#property description "Confirmation entries, lot sizing from risk, partial profit, break-even, trailing stop,"
#property description "daily limits and a news filter. Refuses real accounts unless allowed."

#include <Trade/Trade.mqh>
#include "TradeBotCore.mqh"

input group "Preset"
input EPreset  InpPreset          = PRESET_SCALP;   // Preset (Custom uses every value below)

input group "Strategy (Custom preset)"
input ENUM_TIMEFRAMES InpHTF      = PERIOD_H1;      // Higher timeframe (trend and big levels)
input int      InpPivot           = 3;              // Swing size: bars each side (entry chart)
input int      InpHTFPivot        = 3;              // Swing size on the higher timeframe
input int      InpMinConfluence   = 2;              // Levels that must be tagged together
input double   InpMinRR           = 1.5;            // Minimum reward:risk
input double   InpDefaultRR       = 1.5;            // Target in R when nothing is in the way
input EConfirm InpConfirm         = CONFIRM_BREAK;  // Entry confirmation
input int      InpConfirmBars     = 3;              // Candles the confirmation may take
input bool     InpTrendFilter     = true;           // Only trade with the higher-timeframe trend
input bool     InpBreakouts       = true;           // Trade trendline breakouts
input int      InpRetestBars      = 12;             // Bars a broken trendline stays valid for its retest
input int      InpCooldownBars    = 6;              // Bars to stand aside after a losing trade
input double   InpMinStopATR      = 1.0;            // Minimum stop distance in ATR
input int      InpOBMaxAge        = 100;            // Bars an order block stays valid
input int      InpZoneLookback    = 30;             // Recent swings used to build zones
input bool     InpZoneBest        = false;          // Chart: only the best zone (true) or every tradable zone
input bool     InpPatterns        = true;           // Candlestick/chart patterns count as levels (Custom preset)

input group "Money management (Custom preset)"
input double   InpRiskPct         = 0.5;            // Risk per trade, % of balance
input double   InpMinLotMaxRisk   = 5.0;            // Skip if even the minimum lot risks more than this %
input double   InpMaxSpreadRisk   = 20.0;           // Skip a setup when the spread is more than this % of its risk (0 = off)
input double   InpBreakevenR      = 1.0;            // Move stop to entry at this many R (0 = off)
input double   InpPartialR        = 1.0;            // Partial profit at this many R (0 = off)
input double   InpPartialPct      = 50.0;           // % of the position closed at the partial
input ETrail   InpTrail           = TRAIL_ATR;      // Trailing stop
input double   InpTrailStartR     = 1.0;            // Start trailing at this many R
input double   InpTrailATR        = 1.5;            // ATR multiple for the ATR trailing stop
input double   InpMaxDailyLossPct = 3.0;            // No new trades after losing this % today (0 = off)
input int      InpMaxTradesDay    = 8;              // Max trades per day (0 = off)

input group "News and safety"
input bool     InpNewsFilter      = true;           // No new trades around high-impact news (forex/gold)
input int      InpNewsMinutes     = 30;             // Minutes before/after the news
input bool     InpAllowReal       = false;          // Allow trading a REAL account
input long     InpMagic           = 20261007;       // Magic number

input group "History"
input int      InpHistoryBars     = 5000;           // Chart candles analysed (more = older levels, slower)
input int      InpHTFHistoryBars  = 1000;           // Higher-timeframe candles (zones use all of them)

input group "Display"
input bool     InpDraw            = true;           // Draw zones, trendlines, order blocks, swings
input bool     InpPanel           = true;           // Show the analysis panel
input bool     InpScreenshots     = true;           // Save a screenshot for every trade (MQL5/Files)

input group "Alerts"
input bool     InpAlertPopup      = true;           // Pop-up and sound: break, fake break, trend change, new setup
input bool     InpAlertPush       = true;           // Push to your phone too (MT5 app: set your MetaQuotes ID in Options)

//--- EA state
CTrade   trade;
datetime lastBar = 0;
bool     tradingPermitted = false;
string   lastNote = "";

// a setup waiting for its confirmation
bool     armed = false;
Signal   armedSig;
int      armedBarsLeft = 0;

//+------------------------------------------------------------------+
//| Small helpers                                                    |
//+------------------------------------------------------------------+
int LotDigits(double step)
  {
   int d = (int)MathRound(-MathLog10(step));
   return d < 0 ? 0 : d;
  }

double StopsLevel()
  {
   return (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
  }

void Journal(string event, string detail)
  {
   PrintFormat("TradeBot %s: %s - %s", _Symbol, event, detail);
   StringReplace(detail, ",", ";");
   int h = FileOpen("TradeBot_journal.csv", FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE, ',');
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWrite(h, TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES), _Symbol, event, detail);
   FileClose(h);
  }

void Screenshot(string tag)
  {
   if(!InpScreenshots)
      return;
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;
   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);
   string sym = _Symbol;
   StringReplace(sym, " ", "");
   string name = StringFormat("TradeBot_%s_%04d%02d%02d_%02d%02d_%s.png", sym, d.year, d.mon, d.day, d.hour, d.min, tag);
   ChartRedraw(0);
   if(!ChartScreenShot(0, name, 1600, 900, ALIGN_RIGHT))
      Print("TradeBot: screenshot failed, error ", GetLastError());
  }

//+------------------------------------------------------------------+
//| Presets                                                          |
//+------------------------------------------------------------------+
void LoadConfig()
  {
   // Custom: the inputs
   C.htf = InpHTF; C.pivot = InpPivot; C.htfPivot = InpHTFPivot; C.minConfluence = InpMinConfluence;
   C.confirm = (int)InpConfirm; C.confirmBars = InpConfirmBars; C.retest = InpRetestBars; C.cooldown = InpCooldownBars;
   C.obMaxAge = InpOBMaxAge; C.zoneLookback = InpZoneLookback; C.minRR = InpMinRR; C.defaultRR = InpDefaultRR;
   C.minStopATR = InpMinStopATR; C.trendFilter = InpTrendFilter; C.breakouts = InpBreakouts;
   C.riskPct = InpRiskPct; C.minLotMaxRisk = InpMinLotMaxRisk; C.beR = InpBreakevenR; C.partialR = InpPartialR;
   C.partialPct = InpPartialPct; C.trail = (int)InpTrail; C.trailStartR = InpTrailStartR; C.trailATR = InpTrailATR;
   C.maxDailyLoss = InpMaxDailyLossPct; C.maxTradesDay = InpMaxTradesDay; C.zoneBest = InpZoneBest; C.patterns = InpPatterns;

   ApplyPreset((int)InpPreset);   // Scalp/Swing overwrite the inputs
   gHistoryBars = InpHistoryBars;
   gHTFHistoryBars = InpHTFHistoryBars;
  }

//+------------------------------------------------------------------+
//| Account checks                                                   |
//+------------------------------------------------------------------+
bool IsSynthetic()
  {
   string path = SymbolInfoString(_Symbol, SYMBOL_PATH);
   return StringFind(path, "Volatility") >= 0 || StringFind(path, "Synthetic") >= 0 || StringFind(path, "Derived") >= 0 ||
          StringFind(_Symbol, "Index") >= 0 || StringFind(_Symbol, "Volatility") >= 0;
  }

bool NewsBlocked(string &why)
  {
   if(!InpNewsFilter || MQLInfoInteger(MQL_TESTER) || IsSynthetic()) return false;
   string cur[3];
   cur[0] = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   cur[1] = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   cur[2] = "USD";
   datetime now = TimeTradeServer();
   datetime from = now - InpNewsMinutes * 60, to = now + InpNewsMinutes * 60;
   for(int k = 0; k < 3; k++)
     {
      if(cur[k] == "" || (k == 2 && (cur[0] == "USD" || cur[1] == "USD"))) continue;
      MqlCalendarValue values[];
      int n = CalendarValueHistory(values, from, to, NULL, cur[k]);
      for(int i = 0; i < n; i++)
        {
         MqlCalendarEvent ev;
         if(CalendarEventById(values[i].event_id, ev) && ev.importance == CALENDAR_IMPORTANCE_HIGH)
           {
            why = StringFormat("high-impact news: %s %s at %s", cur[k], ev.name, TimeToString(values[i].time, TIME_MINUTES));
            return true;
           }
        }
     }
   return false;
  }

datetime DayStart()
  {
   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);
   d.hour = 0; d.min = 0; d.sec = 0;
   return StructToTime(d);
  }

// Daily loss and trade-count limits, over all symbols traded with this magic number.
bool DailyLimitsOk(string &why)
  {
   datetime start = DayStart();
   if(!HistorySelect(start, TimeCurrent() + 60)) return true;
   double closedToday = 0;
   int entries = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic) continue;
      long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN) entries++;
      closedToday += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) + HistoryDealGetDouble(d, DEAL_COMMISSION);
     }
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double startBalance = balance - closedToday;
   if(C.maxDailyLoss > 0 && startBalance > 0 && balance <= startBalance * (1 - C.maxDailyLoss / 100.0))
     { why = StringFormat("daily loss limit reached (%.2f today)", closedToday); return false; }
   if(C.maxTradesDay > 0 && entries >= C.maxTradesDay)
     { why = StringFormat("%d trades today (limit %d)", entries, C.maxTradesDay); return false; }
   return true;
  }

// The spread must not eat more than InpMaxSpreadRisk % of the setup's risk (it made most scalp losses).
bool SpreadOk(const Signal &s, string &why)
  {
   if(InpMaxSpreadRisk <= 0) return true;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return true;
   double spread = tick.ask - tick.bid, risk = MathAbs((s.trigger > 0 ? s.trigger : s.entry) - s.stop);
   if(risk <= 0 || spread <= InpMaxSpreadRisk / 100.0 * risk) return true;
   why = StringFormat("skipped: the spread (%s) is %.0f%% of the risk (max %.0f%%)", PS(spread), 100 * spread / risk, InpMaxSpreadRisk);
   return false;
  }

// Stand aside for C.cooldown bars after a losing trade on this symbol.
bool InCooldown(string &why)
  {
   if(C.cooldown <= 0) return false;
   if(!HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent() + 60)) return false;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic || HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
      long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;
      long pid = HistoryDealGetInteger(d, DEAL_POSITION_ID);
      double total = 0;
      for(int j = HistoryDealsTotal() - 1; j >= 0; j--)
        {
         ulong e = HistoryDealGetTicket(j);
         if(e != 0 && HistoryDealGetInteger(e, DEAL_POSITION_ID) == pid)
            total += HistoryDealGetDouble(e, DEAL_PROFIT) + HistoryDealGetDouble(e, DEAL_SWAP) + HistoryDealGetDouble(e, DEAL_COMMISSION);
        }
      if(total >= 0) return false;
      int barsSince = iBarShift(_Symbol, PERIOD_CURRENT, (datetime)HistoryDealGetInteger(d, DEAL_TIME)) - 1;
      if(barsSince <= C.cooldown)
        { why = StringFormat("cooldown after a loss (%d/%d bars)", barsSince, C.cooldown); return true; }
      return false;
     }
   return false;
  }

ulong MyPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return tk;
     }
   return 0;
  }

//+------------------------------------------------------------------+
//| Position size from risk (tick value handles any currency)        |
//+------------------------------------------------------------------+
double CalcLots(int side, double entry, double stop, string &note)
  {
   note = "";
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double dist = MathAbs(entry - stop);
   if(balance <= 0 || tickSize <= 0 || tickValue <= 0 || step <= 0 || dist <= 0)
     { note = "symbol data unavailable"; return 0; }
   double perLot = dist / tickSize * tickValue;          // account currency lost per lot at the stop
   double lots = MathFloor(balance * C.riskPct / 100.0 / perLot / step + 1e-9) * step;
   if(lots < vmin - 1e-12)
     {
      double minRisk = vmin * perLot / balance * 100.0;
      if(minRisk > C.minLotMaxRisk)
        {
         note = StringFormat("skipped: the minimum %.3g lot would risk %.1f%% of the account (limit %.1f%%)", vmin, minRisk, C.minLotMaxRisk);
         return 0;
        }
      lots = vmin;
      note = StringFormat("minimum lot: risking %.1f%% instead of %.1f%%", minRisk, C.riskPct);
     }
   lots = MathMin(lots, vmax);
   double margin = 0;
   ENUM_ORDER_TYPE type = (side == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(OrderCalcMargin(type, _Symbol, lots, entry, margin) && margin > free * 0.95)
     {
      double perLotMargin = margin / lots;
      lots = MathFloor(free * 0.95 / perLotMargin / step + 1e-9) * step;
      if(lots < vmin - 1e-12)
        {
         note = StringFormat("skipped: not enough free margin (%.2f) for the minimum lot", free);
         return 0;
        }
      note = "reduced to fit the free margin";
     }
   return NormalizeDouble(lots, LotDigits(step));
  }

//+------------------------------------------------------------------+
//| Orders                                                           |
//+------------------------------------------------------------------+
string GV(ulong ticket, string key) { return "TB." + IntegerToString((long)ticket) + "." + key; }

bool OpenMarket(const Signal &s)
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return false;
   double price = (s.side == 1) ? tick.ask : tick.bid;
   if(s.side == 1 ? (price <= s.stop || price >= s.target) : (price >= s.stop || price <= s.target))
     { Journal("skipped", "price already beyond the stop or the target"); return false; }
   double sl = Px(s.stop), tp = Px(s.target);
   double minDist = StopsLevel();
   if(MathAbs(price - sl) < minDist || MathAbs(tp - price) < minDist)
     { Journal("skipped", "stop or target closer than the broker allows"); return false; }
   string note;
   double lots = CalcLots(s.side, price, sl, note);
   if(lots <= 0) { Journal("skipped", note); lastNote = note; return false; }

   string comment = "TradeBot " + s.setup;
   bool ok = (s.side == 1) ? trade.Buy(lots, _Symbol, 0, sl, tp, comment) : trade.Sell(lots, _Symbol, 0, sl, tp, comment);
   if(!ok || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
     {
      Journal("order failed", StringFormat("%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return false;
     }
   ulong tk = MyPosition();
   if(tk > 0 && PositionSelectByTicket(tk))
     {
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      GlobalVariableSet(GV(tk, "risk"), MathAbs(openPrice - sl));   // 1R, kept after the stop moves
      GlobalVariableSet(GV(tk, "part"), 0);
     }
   lastNote = note;
   Journal("filled", StringFormat("%s %s %.2f lots at %s, SL %s, TP %s, R:R %.1f%s - %s",
                                  s.side == 1 ? "BUY" : "SELL", s.setup, lots, PS(price), PS(sl), PS(tp), s.rr,
                                  note == "" ? "" : " (" + note + ")", s.reasons));
   Screenshot(s.side == 1 ? "buy" : "sell");
   return true;
  }

// Confirmation: checked on every tick for "break", at each candle close for "close".
void CheckArmedTick()
  {
   if(!armed || armedSig.trigger <= 0 || C.confirm != CONFIRM_BREAK) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   if(armedSig.side == 1 ? tick.bid <= armedSig.stop : tick.ask >= armedSig.stop)
     { armed = false; Journal("order cancelled", "stop level traded before the confirmation"); return; }
   if(armedSig.side == 1 ? tick.ask >= armedSig.trigger : tick.bid <= armedSig.trigger)
     {
      armed = false;
      OpenMarket(armedSig);
     }
  }

void CheckArmedBar()
  {
   if(!armed) return;
   int t = N - 1;
   if(C.confirm == CONFIRM_CLOSE)
     {
      if(armedSig.side == 1 ? R[t].low <= armedSig.stop : R[t].high >= armedSig.stop)
        { armed = false; Journal("order cancelled", "stop level traded before the confirmation"); return; }
      if(armedSig.side == 1 ? R[t].close > armedSig.trigger : R[t].close < armedSig.trigger)
        { armed = false; OpenMarket(armedSig); return; }
     }
   armedBarsLeft--;
   if(armed && armedBarsLeft <= 0)
     { armed = false; Journal("order cancelled", "not confirmed in time"); }
  }

//+------------------------------------------------------------------+
//| Position management                                              |
//+------------------------------------------------------------------+
// Partial profit: on every tick, once price has gone partialR x 1R in favour.
void ManagePartial()
  {
   if(C.partialR <= 0 || C.partialPct <= 0) return;
   ulong tk = MyPosition();
   if(tk == 0 || !PositionSelectByTicket(tk)) return;
   if(GlobalVariableCheck(GV(tk, "part")) && GlobalVariableGet(GV(tk, "part")) > 0) return;
   int side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double risk = GlobalVariableCheck(GV(tk, "risk")) ? GlobalVariableGet(GV(tk, "risk")) : MathAbs(openPrice - PositionGetDouble(POSITION_SL));
   if(risk <= 0) return;
   double level = openPrice + side * C.partialR * risk;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   if(side == 1 ? tick.bid < level : tick.ask > level) return;
   double vol = PositionGetDouble(POSITION_VOLUME);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double part = NormalizeDouble(MathFloor(vol * C.partialPct / 100.0 / step + 1e-9) * step, LotDigits(step));
   GlobalVariableSet(GV(tk, "part"), 1);
   if(part < vmin - 1e-12 || vol - part < vmin - 1e-12)
     { Journal("partial skipped", StringFormat("%.3g lots can't be split", vol)); return; }
   if(trade.PositionClosePartial(tk, part))
      Journal("partial", StringFormat("closed %.3g of %.3g lots at +%.1fR", part, vol, C.partialR));
  }

// Break-even and trailing stop: at each candle close, only ever tightening.
void ManageStops()
  {
   ulong tk = MyPosition();
   if(tk == 0 || !PositionSelectByTicket(tk)) return;
   int t = N - 1;
   int side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
   double risk = GlobalVariableCheck(GV(tk, "risk")) ? GlobalVariableGet(GV(tk, "risk")) : MathAbs(openPrice - sl);
   if(risk <= 0) return;
   int shift = iBarShift(_Symbol, PERIOD_CURRENT, (datetime)PositionGetInteger(POSITION_TIME));
   if(shift < 1) return;                                   // no closed candle since the entry yet
   double best = (side == 1) ? iHigh(_Symbol, PERIOD_CURRENT, iHighest(_Symbol, PERIOD_CURRENT, MODE_HIGH, shift, 1))
                             : iLow(_Symbol, PERIOD_CURRENT, iLowest(_Symbol, PERIOD_CURRENT, MODE_LOW, shift, 1));
   double progress = side * (best - openPrice) / risk;
   double lastClose = R[t].close;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   double market = (side == 1) ? tick.bid : tick.ask, minDist = StopsLevel();
   double newSL = sl;
   string kind = "";

   if(C.beR > 0 && progress >= C.beR && (side == 1 ? openPrice > newSL && openPrice < lastClose : openPrice < newSL && openPrice > lastClose))
     { newSL = openPrice; kind = "break-even"; }
   if(C.trail != TRAIL_NONE && progress >= C.trailStartR)
     {
      double level = 0;
      bool haveLevel = false;
      if(C.trail == TRAIL_ATR)
        { level = best - side * C.trailATR * A[t]; haveLevel = true; }
      else
        {
         int entryIdx = N - shift;                          // the entry candle in R[] (R[N-1] is shift 1)
         for(int i = NP - 1; i >= 0; i--)
            if(P[i].kind == -side && P[i].index >= entryIdx)
              { level = P[i].price - side * STOP_BUF_ATR * A[t]; haveLevel = true; break; }
        }
      if(haveLevel && (side == 1 ? level > newSL && level < lastClose : level < newSL && level > lastClose))
        { newSL = level; kind = "trailing stop"; }
     }
   newSL = Px(newSL);
   if(newSL == Px(sl) || kind == "") return;
   if(side == 1 ? newSL > market - minDist : newSL < market + minDist) return;   // too close to price for the broker
   if(trade.PositionModify(tk, newSL, tp))
      Journal("stop moved", StringFormat("%s to %s (+%.1fR reached)", kind, PS(newSL), progress));
  }

//+------------------------------------------------------------------+
//| Drawing and panel                                                |
//+------------------------------------------------------------------+
void Rect(string name, datetime t1, double p1, datetime t2, double p2, color clr, bool fill, int width)
  {
   ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, fill);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

void Segment(string name, datetime t1, double p1, datetime t2, double p2, color clr, int width, ENUM_LINE_STYLE style, bool ray)
  {
   ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, ray);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

void Text(string name, datetime t, double p, string txt, color clr, ENUM_ANCHOR_POINT anchor, int size = 7, string font = "Arial")
  {
   ObjectCreate(0, name, OBJ_TEXT, 0, t, p);
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

void Draw(const Signal &s, bool haveSignal)
  {
   ObjectsDeleteAll(0, PFX);
   if(!InpDraw || N < 2) return;
   int t = N - 1;
   int sec = PeriodSeconds(PERIOD_CURRENT);
   datetime left = R[MathMax(0, N - 200)].time, right = R[t].time + 12 * sec;
   color up = C'42,157,143', dn = C'231,111,81', ink = C'38,70,83', htf = C'109,89,122';

   // the setup on the chart: a new one, or one still waiting for its confirmation until filled/cancelled
   Signal shown = s;
   bool show = haveSignal;
   if(armed) { shown = armedSig; show = true; }

   // zones: every one the bot could trade from, with its plan (scalp), or only the best one (swing).
   // With a setup on, the zone it came off is drawn strong and the others faded.
   ZPlan plans[];
   PlanZones(plans);
   int best = BestPlan(plans);
   datetime planLabelAt = R[t].time + 2 * sec;
   for(int i = 0; i < ArraySize(plans); i++)
     {
      ZPlan p = plans[i];
      if(C.zoneBest ? i != best : !PlanShown(plans, i, R[t].close, A[t])) continue;
      if(show && ((p.lo == shown.zoneLo && p.hi == shown.zoneHi) || (p.lo == shown.htfLo && p.hi == shown.htfHi))) continue;
      string nm = PFX + "pz" + IntegerToString(i);
      color clr = (p.side == 1) ? up : dn;
      Rect(nm, left, p.lo, right, p.hi, show ? (p.side == 1 ? C'230,243,241' : C'252,238,233') : (p.side == 1 ? C'190,226,221' : C'248,208,196'), true, 1);
      if(!show) Rect(nm + "b", left, p.lo, right, p.hi, clr, false, (p.htf || p.backed) ? 2 : 1);
      Text(nm + "T", planLabelAt, (p.lo + p.hi) / 2, StringFormat("%s  TP %s  R:R %.1f", PlanLabel(p), PS(p.target), p.rr), clr, ANCHOR_LEFT, 8);
      if(C.zoneBest && !show)                       // the one plan: its stop and target too
        {
         Segment(nm + "sl", R[t].time, p.stop, right, p.stop, dn, 1, STYLE_DASH, false);
         Segment(nm + "tp", R[t].time, p.target, right, p.target, up, 1, STYLE_DASH, false);
         Text(nm + "slT", right, p.stop, "SL " + PS(p.stop), dn, ANCHOR_LEFT);
         Text(nm + "tpT", right, p.target, "TP " + PS(p.target), up, ANCHOR_LEFT);
        }
     }
   datetime zoneLabelAt = R[MathMax(0, t - 60)].time;      // zone labels sit just left of the recent candles
   if(show && shown.zoneHi > 0)
     {
      color side = (shown.side == 1) ? up : dn;
      Rect(PFX + "ez", left, shown.zoneLo, right, shown.zoneHi, (shown.side == 1) ? C'150,205,198' : C'240,170,150', true, 1);
      Rect(PFX + "ezb", left, shown.zoneLo, right, shown.zoneHi, side, false, 2);
      Text(PFX + "ezT", zoneLabelAt, shown.zoneHi, "ENTRY ZONE " + PS(shown.zoneLo) + "-" + PS(shown.zoneHi), side, ANCHOR_LEFT_LOWER, 8, "Arial Bold");
     }
   if(show && shown.htfHi > 0)
     {
      Rect(PFX + "ehz", left, shown.htfLo, right, shown.htfHi, htf, false, 3);
      Text(PFX + "ehzT", zoneLabelAt, shown.htfLo, "ENTRY ZONE (HTF) " + PS(shown.htfLo) + "-" + PS(shown.htfHi), htf, ANCHOR_LEFT_UPPER, 8, "Arial Bold");
     }

   for(int k = 0; k < 2; k++)                               // trendlines: entry chart, then HTF (on the chart's candles too)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         string nm = PFX + (k == 0 ? "l" : "lh") + IntegerToString(i);
         color clr = (k == 0) ? ink : htf;
         int width = (k == 0) ? 2 : 3;
         bool breakout = show && shown.setup == "breakout" && ln.broken == t && ln.kind == -shown.side;
         if(ln.broken < 0)
           { Segment(nm, R[ln.i1].time, ln.p1, R[ln.i2].time, ln.p2, clr, width, STYLE_SOLID, true); continue; }
         double at = LineAt(ln, ln.broken);
         Segment(nm, R[ln.i1].time, ln.p1, R[ln.broken].time, at, clr, breakout ? width + 2 : width, STYLE_SOLID, false);
         Segment(nm + "x", R[ln.broken].time, at, right, LineAt(ln, t + 12), clr, 1, STYLE_DOT, false);
         // the break: where a candle closed through the line, green when price broke up, red when down
         bool up = ln.kind == -1;
         color bc = up ? C'42,157,143' : C'231,111,81';
         string mk = nm + "brk";
         ObjectCreate(0, mk, OBJ_ARROW, 0, R[ln.broken].time, at);
         ObjectSetInteger(0, mk, OBJPROP_ARROWCODE, 159);  // dot
         ObjectSetInteger(0, mk, OBJPROP_COLOR, bc);
         ObjectSetInteger(0, mk, OBJPROP_WIDTH, 4);
         ObjectSetInteger(0, mk, OBJPROP_ANCHOR, ANCHOR_CENTER);
         ObjectSetInteger(0, mk, OBJPROP_SELECTABLE, false);
         Text(mk + "T", R[ln.broken].time, at, (breakout ? "TRENDLINE BREAK " : "BREAK ") + (up ? "\x25B2" : "\x25BC"), bc,
              up ? ANCHOR_RIGHT_UPPER : ANCHOR_RIGHT_LOWER, breakout ? 9 : 8, "Arial Bold");
        }
     }

   // chart patterns: their swings joined, the neckline dashed, the name; the candlestick pattern under/over its candle
   for(int i = 0; i < ArraySize(PAT); i++)
     {
      CPattern pt = PAT[i];
      if(pt.pi[0] < N - 200) continue;
      color pc = (pt.side == 1) ? up : dn;
      string nm = PFX + "pat" + IntegerToString(i);
      for(int q = 0; q + 1 < pt.np; q++)
         Segment(nm + "s" + IntegerToString(q), R[pt.pi[q]].time, pt.pp[q], R[pt.pi[q + 1]].time, pt.pp[q + 1], pc, 1, STYLE_SOLID, false);
      int end = (pt.broken >= 0) ? pt.broken : t;
      Segment(nm + "n", R[pt.n1].time, PatNeck(pt, pt.n1), R[end].time, PatNeck(pt, end), pc, 2, STYLE_DASH, false);
      double ext = pt.pp[0];
      for(int q = 1; q < pt.np; q++) ext = (pt.side == 1) ? MathMin(ext, pt.pp[q]) : MathMax(ext, pt.pp[q]);
      string name = pt.kind;
      StringToUpper(name);
      Text(nm + "T", R[pt.pi[pt.np / 2]].time, ext, name + (pt.broken >= 0 ? " - neckline broken" : ""), pc,
           pt.side == 1 ? ANCHOR_UPPER : ANCHOR_LOWER, 8, "Arial Bold");
     }
   for(int i = 0; i < ArraySize(FB); i++)                  // fake breaks: where price came back through the level
     {
      if(FB[i].back < N - 200) continue;
      color fc = C'230,140,20';
      string nm = PFX + "fake" + IntegerToString(i);
      Segment(nm + "l", R[FB[i].broke].time, FB[i].level, R[FB[i].back].time, FB[i].level, fc, 2, STYLE_SOLID, false);
      ObjectCreate(0, nm, OBJ_ARROW, 0, R[FB[i].back].time, FB[i].level);
      ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, 251);   // a cross: the break failed
      ObjectSetInteger(0, nm, OBJPROP_COLOR, fc);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      ObjectSetInteger(0, nm, OBJPROP_ANCHOR, ANCHOR_CENTER);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      Text(nm + "T", R[FB[i].back].time, FB[i].level, "FAKE BREAK " + (FB[i].side == 1 ? "\x25B2" : "\x25BC"), fc,
           FB[i].side == 1 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER, 9, "Arial Bold");
     }

   if(candleSide != 0)
      Text(PFX + "candle", R[t].time, candleSide == 1 ? R[t].low : R[t].high, candleName, candleSide == 1 ? up : dn,
           candleSide == 1 ? ANCHOR_UPPER : ANCHOR_LOWER, 8);

   for(int kind = -1; kind <= 1; kind += 2)                // nearest active order block on each side
     {
      int bestI = -1;
      double bestD = 0;
      for(int i = 0; i < ArraySize(OB); i++)
        {
         if(OB[i].kind != kind || !OBActive(OB[i], t)) continue;
         double d = MathAbs((OB[i].lo + OB[i].hi) / 2 - R[t].close);
         if(bestI < 0 || d < bestD) { bestI = i; bestD = d; }
        }
      if(bestI >= 0)
         Rect(PFX + "ob" + IntegerToString(kind + 1), R[OB[bestI].index].time, OB[bestI].lo, right, OB[bestI].hi,
              kind == 1 ? up : dn, false, 1);
     }

   double lastH = 0, lastL = 0;                            // swing labels HH / HL / LH / LL
   bool haveH = false, haveL = false;
   for(int i = 0; i < NP; i++)
     {
      bool isHigh = P[i].kind == 1;
      string tag = "";
      if(isHigh) { if(haveH) tag = P[i].price > lastH ? "HH" : "LH"; lastH = P[i].price; haveH = true; }
      else      { if(haveL) tag = P[i].price > lastL ? "HL" : "LL"; lastL = P[i].price; haveL = true; }
      if(tag != "" && P[i].index >= N - 200)
         Text(PFX + "s" + IntegerToString(i), R[P[i].index].time, P[i].price, tag, clrGray, isHigh ? ANCHOR_LOWER : ANCHOR_UPPER);
     }

   if(show)
     {
      Signal s2 = shown;
      double entry = (s2.trigger > 0) ? s2.trigger : s2.entry;
      Segment(PFX + "entry", R[t].time, entry, right, entry, ink, 2, STYLE_DASH, false);
      Segment(PFX + "sl", R[t].time, s2.stop, right, s2.stop, dn, 2, STYLE_SOLID, false);
      Segment(PFX + "tp", R[t].time, s2.target, right, s2.target, up, 2, STYLE_SOLID, false);
      string verb = (s2.side == 1) ? "BUY" : "SELL";
      string order = (s2.trigger > 0 ? verb + " STOP " : verb + " ") + PS(entry);
      Text(PFX + "entryT", right, entry, "ENTER HERE: " + order, ink, ANCHOR_LEFT, 9, "Arial Bold");
      // an arrow on the next candle, pointing at the order level from the side price comes from
      string arrow = PFX + "enter";
      ObjectCreate(0, arrow, OBJ_ARROW, 0, R[t].time + sec, entry);
      ObjectSetInteger(0, arrow, OBJPROP_ARROWCODE, s2.side == 1 ? 233 : 234);   // Wingdings up / down arrow
      ObjectSetInteger(0, arrow, OBJPROP_ANCHOR, s2.side == 1 ? ANCHOR_TOP : ANCHOR_BOTTOM);
      ObjectSetInteger(0, arrow, OBJPROP_COLOR, ink);
      ObjectSetInteger(0, arrow, OBJPROP_WIDTH, 4);
      ObjectSetInteger(0, arrow, OBJPROP_SELECTABLE, false);
      Text(PFX + "slT", right, s2.stop, "SL " + PS(s2.stop), dn, ANCHOR_LEFT);
      Text(PFX + "tpT", right, s2.target, StringFormat("TP %s  R:R %.1f", PS(s2.target), s2.rr), up, ANCHOR_LEFT);
     }
   ChartRedraw(0);
  }

// Panel rows are collected first, then measured with the chart's own font metrics and drawn, so the
// box fits its text whatever the fonts and screen scaling.
string   pnHead[], pnText[];
color    pnColor[];
bool     pnBold[];

void PanelRow(string head, string text, color clr, bool bold = false)
  {
   int k = ArraySize(pnHead);
   ArrayResize(pnHead, k + 1); ArrayResize(pnText, k + 1); ArrayResize(pnColor, k + 1); ArrayResize(pnBold, k + 1);
   pnHead[k] = head; pnText[k] = text; pnColor[k] = clr; pnBold[k] = bold;
  }

int TextWidth(string text, bool bold, int &height)
  {
   TextSetFont(bold ? "Arial Bold" : "Arial", bold ? -100 : -80);
   uint w = 0, h = 0;
   TextGetSize(text, w, h);
   height = (int)h;
   return (int)w;
  }

// Splits text into lines no wider than maxPx (and at most 60 characters: MT5 shows 63 per label).
int WrapPx(string text, int maxPx, bool bold, string &out[])
  {
   ArrayResize(out, 0);
   string words[];
   int nw = StringSplit(text, ' ', words), n = 0, h;
   string line = "";
   for(int i = 0; i < nw; i++)
     {
      if(words[i] == "") continue;
      string next = line == "" ? words[i] : line + " " + words[i];
      if(line != "" && (TextWidth(next, bold, h) > maxPx || StringLen(next) > 60))
        { ArrayResize(out, n + 1); out[n++] = line; next = words[i]; }
      line = next;
     }
   if(line != "") { ArrayResize(out, n + 1); out[n++] = line; }
   return n;
  }

void PanelLabel(string name, int x, int y, string text, color clr, bool bold)
  {
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, bold ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, bold ? 10 : 8);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

void PanelDraw()
  {
   int pad = 10, gap = 12, textMax = 400, h = 0;
   int headW = 0;
   for(int r = 0; r < ArraySize(pnHead); r++)
      if(pnHead[r] != "") headW = MathMax(headW, TextWidth(pnHead[r], false, h) + 4);
   int widest = 0, y;
   // first pass: measure
   int total = 0;
   for(int r = 0; r < ArraySize(pnHead); r++)
     {
      string ls[];
      int n = WrapPx(pnText[r], pnHead[r] == "" ? textMax + headW + gap : textMax, pnBold[r], ls);
      int lh = 0;
      for(int i = 0; i < n; i++) widest = MathMax(widest, TextWidth(ls[i], pnBold[r], lh) + (pnHead[r] == "" ? 0 : headW + gap));
      total += n * (lh + 3) + 4;
     }
   string bg = PFX + "pnbg";                      // the white box first, so the text sits on top
   ObjectCreate(0, bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, bg, OBJPROP_XDISTANCE, 6);
   ObjectSetInteger(0, bg, OBJPROP_YDISTANCE, 12);
   ObjectSetInteger(0, bg, OBJPROP_XSIZE, widest + 2 * pad + 4);
   ObjectSetInteger(0, bg, OBJPROP_YSIZE, total + 2 * pad);
   ObjectSetInteger(0, bg, OBJPROP_BGCOLOR, clrWhite);
   ObjectSetInteger(0, bg, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, bg, OBJPROP_COLOR, C'200,200,200');
   ObjectSetInteger(0, bg, OBJPROP_BACK, false);
   ObjectSetInteger(0, bg, OBJPROP_SELECTABLE, false);
   y = 12 + pad;
   for(int r = 0; r < ArraySize(pnHead); r++)
     {
      string ls[];
      int n = WrapPx(pnText[r], pnHead[r] == "" ? textMax + headW + gap : textMax, pnBold[r], ls), lh = 0;
      TextWidth("Ag", pnBold[r], lh);
      if(pnHead[r] != "") PanelLabel(PFX + "pnh" + IntegerToString(r), 6 + pad, y, pnHead[r], clrDimGray, false);
      for(int i = 0; i < n; i++)
        {
         PanelLabel(PFX + "pn" + IntegerToString(r) + "_" + IntegerToString(i), 6 + pad + (pnHead[r] == "" ? 0 : headW + gap), y,
                    ls[i], pnColor[r], pnBold[r]);
         y += lh + 3;
        }
      y += 4;
     }
  }

// The most recent trendline break still in its retest window, in words ("" if none).
string LastBreak(int t, int dir, string tfE, string tfH)
  {
   int when = -1;
   Line last;
   last.i1 = 0; last.p1 = 0; last.i2 = 1; last.p2 = 0; last.kind = 0; last.broken = -1;
   last.touches = 0; last.lastTouch = -1; last.htf = false;
   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         if(ln.broken < 0 || ln.broken <= when) continue;
         when = ln.broken; last = ln;
        }
     }
   if(when < 0) return "";
   bool up = last.kind == -1;                     // a falling line broken upward, or a rising one downward
   bool isHtf = last.htf;
   int ago = t - when;
   string text = StringFormat("%s %s trendline broken %s at %s %s", up ? "falling" : "rising", isHtf ? tfH : tfE, up ? "up" : "down",
                              PS(LineAt(last, when)), ago == 0 ? "on this candle" : StringFormat("%d candle%s ago", ago, ago == 1 ? "" : "s"));
   for(int i = 0; i < ArraySize(FB); i++)
      if(FB[i].broke == when && FB[i].side == (up ? -1 : 1))
         return text + " - it came back: fake break, not a breakout";
   if(dir == (up ? 1 : -1))
      text += StringFormat(": %s on the breakout or the retest of %s", up ? "buy" : "sell", PS(LineAt(last, t)));
   else if(dir != 0)
      text += ": against the " + tfH + " trend, no trade";
   return text;
  }

// Alerts: a pop-up with sound and a push to the phone, once per event.
datetime lastAlertBar = 0;
int      lastDirection = 99;

void Notify(string msg)
  {
   if(MQLInfoInteger(MQL_TESTER)) return;
   string full = StringFormat("TradeBot %s %s: %s", _Symbol, StringSubstr(EnumToString(Period()), 7), msg);
   if(InpAlertPopup) Alert(full);
   if(InpAlertPush && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED)) SendNotification(full);
  }

void CheckAlerts(const Signal &s, bool haveSignal)
  {
   int t = N - 1;
   if(R[t].time == lastAlertBar) return;
   lastAlertBar = R[t].time;
   string tfE = StringSubstr(EnumToString(Period()), 7), tfH = StringSubstr(EnumToString(C.htf), 7);
   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         if(ln.broken != t) continue;
         bool up = ln.kind == -1;
         Notify(StringFormat("BREAK %s - %s %s trendline broken %s at %s", up ? "UP" : "DOWN", up ? "falling" : "rising",
                             ln.htf ? tfH : tfE, up ? "up" : "down", PS(LineAt(ln, t))));
        }
     }
   for(int i = 0; i < ArraySize(FB); i++)
      if(FB[i].back == t)
         Notify(StringFormat("FAKE BREAK - %s at %s: price came back, the %s are trapped", FakeLabel(FB[i]), PS(FB[i].level),
                             FB[i].side == 1 ? "sellers" : "buyers"));
   if(lastDirection != 99 && direction != lastDirection)
      Notify(StringFormat("TREND CHANGE - %s trend is now %s", NH > 0 ? tfH : tfE, BiasName(direction)));
   lastDirection = direction;
   if(haveSignal)
      Notify(StringFormat("ENTER HERE - %s STOP %s, SL %s, TP %s, R:R %.1f (%s)", s.side == 1 ? "BUY" : "SELL",
                          PS(s.trigger > 0 ? s.trigger : s.entry), PS(s.stop), PS(s.target), s.rr, s.setup));
  }

// The analysis panel, top left on a white box: trend, where price is, the setup, the zones and
// the two scenarios. Green is for buying, red for selling.
void Panel(const Signal &s, bool haveSignal, string status)
  {
   Comment("");
   if(!InpPanel) return;
   int t = N - 1;
   color up = C'42,157,143', dn = C'231,111,81', ink = C'38,70,83';
   string tfE = StringSubstr(EnumToString(Period()), 7), tfH = StringSubstr(EnumToString(C.htf), 7);
   ArrayResize(pnHead, 0); ArrayResize(pnText, 0); ArrayResize(pnColor, 0); ArrayResize(pnBold, 0);
   PanelRow("", "TradeBot  " + _Symbol + " " + tfE + (C.zoneBest ? "  (Swing)" : "  (Scalp)"), ink, true);

   int dir = (NH > 0) ? biasH : biasE;
   string trend = (NH > 0 ? tfH + " " + BiasName(biasH) : "") + (NH > 0 ? ",  " : "") + tfE + " " + BiasName(biasE);
   PanelRow("TREND", trend + (dir == 1 ? "  -  buys only" : (dir == -1 ? "  -  sells only" : "  -  no direction")),
            dir == 1 ? up : (dir == -1 ? dn : ink));

   string loc = "";
   if(ArraySize(ZR) > 0) loc += StringFormat("resistance %s-%s (%.1f ATR above)", PS(ZR[0].lo), PS(ZR[0].hi), (ZR[0].lo - R[t].close) / A[t]);
   if(ArraySize(ZS) > 0)
     {
      double below = (R[t].close - ZS[0].hi) / A[t];
      loc += (loc == "" ? "" : ",  ") + (below <= 0.05 ? StringFormat("inside support %s-%s", PS(ZS[0].lo), PS(ZS[0].hi))
                                                       : StringFormat("support %s-%s (%.1f ATR below)", PS(ZS[0].lo), PS(ZS[0].hi), below));
     }
   PanelRow("PRICE", PS(R[t].close) + "  -  " + (loc == "" ? "no zone nearby" : loc), ink);

   int obs = 0;
   for(int i = 0; i < ArraySize(OB); i++) if(OBActive(OB[i], t)) obs++;
   PanelRow("LEVELS", StringFormat("%d trendlines on %s, %d on %s,  %d order blocks", ArraySize(L), tfE, ArraySize(LH), tfH, obs), ink);

   string pats = "";
   for(int i = 0; i < ArraySize(PAT); i++)
      pats += (pats == "" ? "" : ",  ") + PAT[i].kind + (PAT[i].broken >= 0 ? " (neckline broken)" : " (neckline " + PS(PatNeck(PAT[i], t)) + ")");
   if(Triangle()) pats += (pats == "" ? "" : ",  ") + "triangle";
   if(candleName != "") pats += (pats == "" ? "" : ",  ") + candleName + " candle";
   if(pats != "") PanelRow("PATTERNS", pats + (C.patterns ? "" : "  -  shown only"), ink);

   if(ArraySize(FB) > 0 && t - FB[0].back < 2 * FAKE_BARS)
     {
      int ago = t - FB[0].back;
      PanelRow("FAKE BREAK", StringFormat("%s at %s %s: the %s were trapped, do not chase it", FakeLabel(FB[0]), PS(FB[0].level),
                                          ago == 0 ? "on this candle" : StringFormat("%d candle%s ago", ago, ago == 1 ? "" : "s"),
                                          FB[0].side == 1 ? "sellers" : "buyers"), C'230,140,20');
     }

   string brk = LastBreak(t, dir, tfE, tfH);
   if(brk != "") PanelRow("BREAK", brk, StringFind(brk, "up") >= 0 ? up : dn);

   if(haveSignal)
      PanelRow("SETUP", StringFormat("%s %s:  %s STOP %s,  SL %s,  TP %s,  R:R %.1f", s.side == 1 ? "BUY" : "SELL", s.setup,
                                        s.side == 1 ? "BUY" : "SELL", PS(s.trigger > 0 ? s.trigger : s.entry), PS(s.stop), PS(s.target), s.rr),
               s.side == 1 ? up : dn);
   else if(armed)
      PanelRow("SETUP", StringFormat("waiting: %s STOP %s, %d candles left", armedSig.side == 1 ? "BUY" : "SELL",
                                        PS(armedSig.trigger), armedBarsLeft), armedSig.side == 1 ? up : dn);
   else
      PanelRow("SETUP", "none yet - wait for a rejection candle in a tradable zone", ink);

   ZPlan plans[];
   PlanZones(plans);
   int best = BestPlan(plans), nt = 0;
   for(int i = 0; i < ArraySize(plans); i++) if(plans[i].tradable) nt++;
   if(best < 0)
      PanelRow("ZONES", "no zone near price goes with the trend and pays enough", ink);
   else
     {
      ZPlan b = plans[best];
      string text = StringFormat("%s,  SL %s,  TP %s,  R:R %.1f", PlanLabel(b), PS(b.stop), PS(b.target), b.rr);
      PanelRow(C.zoneBest ? "ENTRY ZONE" : "ZONES", (C.zoneBest ? "" : StringFormat("%d tradable%s, best: ", nt, nt > 2 ? " (nearest 2 drawn)" : "")) + text,
               b.side == 1 ? up : dn);
     }

   string mainS, altS;
   Scenarios(plans, NH > 0 ? tfH : tfE, mainS, altS);
   PanelRow("MAIN", mainS, ink);
   PanelRow("ALTERNATIVE", altS, clrDimGray);
   PanelRow("STATUS", status + (lastNote != "" ? "  -  " + lastNote : ""), clrDimGray);

   PanelDraw();
   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
//| Event handlers                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   LoadConfig();
   trade.SetExpertMagicNumber((ulong)InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(50);
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   tradingPermitted = MQLInfoInteger(MQL_TESTER) || mode == ACCOUNT_TRADE_MODE_DEMO || InpAllowReal;
   if(!tradingPermitted)
      Alert("TradeBot: this is a REAL account and 'Allow trading a REAL account' is off - analysis only, no orders.");
   if(InpPreset == PRESET_SCALP && Period() != PERIOD_M5)
      Print("TradeBot: the Scalp preset is designed for M5 charts (this chart is ", EnumToString(Period()), ").");
   if(InpPreset == PRESET_SWING && Period() != PERIOD_H4)
      Print("TradeBot: the Swing preset is designed for H4 charts (this chart is ", EnumToString(Period()), ").");
   lastBar = 0;
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, PFX);
   Comment("");
  }

void OnTick()
  {
   if(tradingPermitted)
     {
      ManagePartial();
      CheckArmedTick();
     }
   datetime bar0 = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar0 == lastBar) return;
   lastBar = bar0;

   if(!Analyse()) return;
   string status = tradingPermitted ? "trading" : "analysis only (real account)";
   Signal s;
   ClearSignal(s);
   bool haveSignal = Setup(1, s) || Setup(-1, s);
   CheckAlerts(s, haveSignal);

   if(tradingPermitted)
     {
      CheckArmedBar();
      ManageStops();
      string why = "";
      if(MyPosition() > 0)
         status = "in a trade";
      else if(armed)
         status = "waiting for confirmation";
      else if(!DailyLimitsOk(why) || InCooldown(why))
         status = why;
      else if(haveSignal)
        {
         if(NewsBlocked(why) || !SpreadOk(s, why))
           { Journal("skipped", why); status = why; lastNote = why; }
         else if(C.confirm == CONFIRM_NONE)
            OpenMarket(s);
         else
           {
            armed = true;
            armedSig = s;
            armedBarsLeft = C.confirmBars;
            Journal("order placed", StringFormat("%s %s: %s %s, SL %s, TP %s, R:R %.1f - %s",
                                                 s.side == 1 ? "LONG" : "SHORT", s.setup,
                                                 C.confirm == CONFIRM_BREAK ? "enter on a break of" : "enter after a close beyond",
                                                 PS(s.trigger), PS(s.stop), PS(s.target), s.rr, s.reasons));
            Screenshot("setup");
            CheckArmedTick();                               // price may already be through the trigger
           }
        }
     }
   Draw(s, haveSignal);
   Panel(s, haveSignal, status);
  }
//+------------------------------------------------------------------+
