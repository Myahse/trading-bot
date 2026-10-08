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

input group "Money management (Custom preset)"
input double   InpRiskPct         = 0.5;            // Risk per trade, % of balance
input double   InpMinLotMaxRisk   = 5.0;            // Skip if even the minimum lot risks more than this %
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
   C.maxDailyLoss = InpMaxDailyLossPct; C.maxTradesDay = InpMaxTradesDay;

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

void Text(string name, datetime t, double p, string txt, color clr, ENUM_ANCHOR_POINT anchor)
  {
   ObjectCreate(0, name, OBJ_TEXT, 0, t, p);
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 7);
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

   for(int i = 0; i < MathMin(2, ArraySize(ZS)); i++) Rect(PFX + "zs" + IntegerToString(i), left, ZS[i].lo, right, ZS[i].hi, C'200,232,228', true, 1);
   for(int i = 0; i < MathMin(2, ArraySize(ZR)); i++) Rect(PFX + "zr" + IntegerToString(i), left, ZR[i].lo, right, ZR[i].hi, C'250,218,208', true, 1);
   if(ArraySize(HZS) > 0) Rect(PFX + "hzs", left, HZS[0].lo, right, HZS[0].hi, htf, false, 2);
   if(ArraySize(HZR) > 0) Rect(PFX + "hzr", left, HZR[0].lo, right, HZR[0].hi, htf, false, 2);

   for(int i = 0; i < ArraySize(L); i++)
     {
      string nm = PFX + "l" + IntegerToString(i);
      if(L[i].broken < 0)
         Segment(nm, R[L[i].i1].time, L[i].p1, R[L[i].i2].time, L[i].p2, ink, 2, STYLE_SOLID, true);
      else
        {
         Segment(nm, R[L[i].i1].time, L[i].p1, R[L[i].broken].time, LineAt(L[i], L[i].broken), ink, 2, STYLE_SOLID, false);
         Segment(nm + "x", R[L[i].broken].time, LineAt(L[i], L[i].broken), right, LineAt(L[i], t + 12), ink, 1, STYLE_DOT, false);
        }
     }
   for(int i = 0; i < ArraySize(LH); i++)                  // HTF lines sit on the chart's candles too
     {
      string nm = PFX + "lh" + IntegerToString(i);
      if(LH[i].broken < 0)
         Segment(nm, R[LH[i].i1].time, LH[i].p1, R[LH[i].i2].time, LH[i].p2, htf, 3, STYLE_SOLID, true);
      else
         Segment(nm, R[LH[i].i1].time, LH[i].p1, R[LH[i].broken].time, LineAt(LH[i], LH[i].broken), htf, 3, STYLE_SOLID, false);
     }

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

   Signal shown = s;
   bool show = haveSignal;
   if(armed) { shown = armedSig; show = true; }        // a waiting setup stays on the chart until filled/cancelled
   if(show)
     {
      Signal s2 = shown;
      double entry = (s2.trigger > 0) ? s2.trigger : s2.entry;
      Segment(PFX + "entry", R[t].time, entry, right, entry, ink, 2, STYLE_DASH, false);
      Segment(PFX + "sl", R[t].time, s2.stop, right, s2.stop, dn, 2, STYLE_SOLID, false);
      Segment(PFX + "tp", R[t].time, s2.target, right, s2.target, up, 2, STYLE_SOLID, false);
      string verb = (s2.side == 1) ? "BUY" : "SELL";
      Text(PFX + "entryT", right, entry, (s2.trigger > 0 ? verb + " STOP " : verb + " ") + PS(entry), ink, ANCHOR_LEFT);
      Text(PFX + "slT", right, s2.stop, "SL " + PS(s2.stop), dn, ANCHOR_LEFT);
      Text(PFX + "tpT", right, s2.target, StringFormat("TP %s  R:R %.1f", PS(s2.target), s2.rr), up, ANCHOR_LEFT);
     }
   ChartRedraw(0);
  }

void Panel(const Signal &s, bool haveSignal, string status)
  {
   if(!InpPanel) { Comment(""); return; }
   int t = N - 1;
   string txt = "TradeBot - how it reads " + _Symbol + "\n";
   if(NH > 0) txt += StringFormat("1. Trend (%s): %s\n", EnumToString(C.htf), BiasName(biasH));
   txt += StringFormat("2. Structure (%s): %s\n", EnumToString(Period()), BiasName(biasE));
   string loc = "";
   if(ArraySize(ZR) > 0) loc += StringFormat("resistance %s-%s (%.1f ATR above)", PS(ZR[0].lo), PS(ZR[0].hi), (ZR[0].lo - R[t].close) / A[t]);
   if(ArraySize(ZS) > 0) loc += (loc == "" ? "" : ", ") + StringFormat("support %s-%s (%.1f ATR below)", PS(ZS[0].lo), PS(ZS[0].hi), (R[t].close - ZS[0].hi) / A[t]);
   txt += "3. Levels: " + (loc == "" ? "none nearby" : loc) + "\n";
   txt += StringFormat("4. Trendlines: %d on %s, %d on %s\n", ArraySize(L), EnumToString(Period()), ArraySize(LH), EnumToString(C.htf));
   int obs = 0;
   for(int i = 0; i < ArraySize(OB); i++) if(OBActive(OB[i], t)) obs++;
   txt += StringFormat("5. Order blocks active: %d\n", obs);
   if(haveSignal)
      txt += StringFormat("6. SETUP %s (%s): entry %s, SL %s, TP %s, R:R %.1f\n   %s\n", s.side == 1 ? "LONG" : "SHORT",
                          s.setup, PS(s.trigger > 0 ? s.trigger : s.entry), PS(s.stop), PS(s.target), s.rr, s.reasons);
   else if(armed)
      txt += StringFormat("6. Waiting for confirmation at %s (%d candles left)\n", PS(armedSig.trigger), armedBarsLeft);
   else
      txt += "6. Setup: none\n";
   txt += "Status: " + status + (lastNote != "" ? "\nLast note: " + lastNote : "");
   Comment(txt);
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
   s.side = 0; s.setup = ""; s.entry = 0; s.stop = 0; s.target = 0; s.trigger = 0; s.rr = 0; s.reasons = "";
   bool haveSignal = Setup(1, s) || Setup(-1, s);

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
         if(NewsBlocked(why))
           { Journal("skipped", why); status = why; }
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
