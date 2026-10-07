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

//--- choices
enum EPreset  { PRESET_SCALP = 0,   // Scalp: M5 chart, H1 structure
                PRESET_SWING = 1,   // Swing: H4 chart, D1 structure
                PRESET_CUSTOM = 2   // Custom: use the values below
              };
enum EConfirm { CONFIRM_BREAK = 0,  // Break of the signal candle's high/low
                CONFIRM_CLOSE = 1,  // A candle closes beyond it
                CONFIRM_NONE = 2    // None: enter at the next open
              };
enum ETrail   { TRAIL_NONE = 0,     // No trailing stop
                TRAIL_ATR = 1,      // ATR behind the best price
                TRAIL_SWING = 2     // Behind each new swing
              };

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
input bool     InpDraw            = true;           // Draw zones, trendlines, order blocks, swings, trade boxes
input bool     InpPanel           = true;           // Show the analysis panel
input bool     InpScreenshots     = true;           // Save a screenshot for every trade (MQL5/Files)

//--- fixed rules, as in the Python bot
#define ATR_PERIOD          14
#define ZONE_TOL_ATR        0.6
#define HTF_ZONE_TOL_ATR    0.3
#define TOUCH_BUF_ATR       0.25
#define STOP_BUF_ATR        0.3
#define OB_DISP_ATR         1.0
#define OB_LOOKBACK         10
#define BREAKOUT_BODY       0.5
#define LINE_CANDIDATES     8
#define LINE_TOUCH_ATR      0.25
#define LINE_WICK_ATR       0.1
#define LINE_BREAK_ATR      0.1
#define LINE_MAX_SLOPE_ATR  0.5
#define PFX                 "TB_"

//--- data types
struct Config
  {
   ENUM_TIMEFRAMES   htf;
   int               pivot, htfPivot, minConfluence, confirm, confirmBars, retest, cooldown, obMaxAge, zoneLookback;
   double            minRR, defaultRR, minStopATR;
   bool              trendFilter, breakouts;
   double            riskPct, minLotMaxRisk, beR, partialR, partialPct, trailStartR, trailATR, maxDailyLoss;
   int               trail, maxTradesDay;
  };

struct Pivot  { int index; double price; int kind; int confirmed; };          // kind +1 swing high, -1 swing low
struct Zone   { double lo; double hi; int touches; };
struct Line   { int i1; double p1; int i2; double p2; int kind; int broken; int touches; int lastTouch; bool htf; };
                                                                              // kind +1 rising support, -1 falling resistance
struct OBlock { int kind; int index; double lo; double hi; int created; int invalid; }; // kind +1 bullish
struct Tag    { string reason; double level; };
struct Signal { int side; string setup; double entry; double stop; double target; double trigger; double rr; string reasons; };

//--- state
Config   C;
CTrade   trade;
MqlRates R[];   int N = 0;   double A[];   Pivot P[];  int NP = 0;            // entry timeframe
MqlRates RH[];  int NH = 0;  double AH[];  Pivot PH[]; int NPH = 0;           // higher timeframe
Zone     ZS[], ZR[], HZS[], HZR[];                                            // support / resistance, nearest first
Line     L[];   Line LH[];
OBlock   OB[];
int      biasE = 0, biasH = 0, direction = 0;
datetime lastBar = 0;
bool     tradingPermitted = false;
string   lastNote = "";

// a setup waiting for its confirmation
bool     armed = false;
Signal   armedSig;
int      armedBarsLeft = 0;
datetime armedAt = 0;                                                         // the signal candle, where its box starts

//+------------------------------------------------------------------+
//| Small helpers                                                    |
//+------------------------------------------------------------------+
bool   Valid(double v)          { return v != EMPTY_VALUE && MathIsValidNumber(v) && v > 0; }
double Px(double p)             { return NormalizeDouble(p, _Digits); }
string PS(double p)             { return DoubleToString(p, _Digits); }
string BiasName(int b)          { return b > 0 ? "up" : (b < 0 ? "down" : "neutral"); }
double LineAt(const Line &ln, double x) { return ln.p1 + (ln.p2 - ln.p1) / (ln.i2 - ln.i1) * (x - ln.i1); }

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

   if(InpPreset == PRESET_SCALP)
     {
      C.htf = PERIOD_H1; C.pivot = 3; C.htfPivot = 3; C.minConfluence = 2; C.confirm = CONFIRM_BREAK; C.confirmBars = 3;
      C.retest = 12; C.cooldown = 6; C.obMaxAge = 100; C.zoneLookback = 30; C.minRR = 1.5; C.defaultRR = 1.5;
      C.minStopATR = 1.0; C.trendFilter = true; C.breakouts = true;
      C.riskPct = 0.5; C.minLotMaxRisk = 5.0; C.beR = 1.0; C.partialR = 1.0; C.partialPct = 50.0;
      C.trail = TRAIL_ATR; C.trailStartR = 1.0; C.trailATR = 1.5; C.maxDailyLoss = 3.0; C.maxTradesDay = 8;
     }
   else if(InpPreset == PRESET_SWING)
     {
      C.htf = PERIOD_D1; C.pivot = 5; C.htfPivot = 3; C.minConfluence = 2; C.confirm = CONFIRM_BREAK; C.confirmBars = 2;
      C.retest = 15; C.cooldown = 3; C.obMaxAge = 150; C.zoneLookback = 40; C.minRR = 2.0; C.defaultRR = 2.5;
      C.minStopATR = 0.0; C.trendFilter = true; C.breakouts = true;
      C.riskPct = 1.0; C.minLotMaxRisk = 5.0; C.beR = 1.0; C.partialR = 1.5; C.partialPct = 50.0;
      C.trail = TRAIL_SWING; C.trailStartR = 1.5; C.trailATR = 2.0; C.maxDailyLoss = 0.0; C.maxTradesDay = 0;
     }
  }

//+------------------------------------------------------------------+
//| Market structure                                                 |
//+------------------------------------------------------------------+
// ATR with Wilder smoothing (same as the Python bot): valid from bar ATR_PERIOD-1.
void CalcATR(const MqlRates &r[], int n, double &atr[])
  {
   ArrayResize(atr, n);
   double prev = 0.0;
   for(int i = 0; i < n; i++)
     {
      double tr = r[i].high - r[i].low;
      if(i > 0)
         tr = MathMax(tr, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
      prev = (i == 0) ? tr : prev + (tr - prev) / ATR_PERIOD;
      atr[i] = (i >= ATR_PERIOD - 1) ? prev : EMPTY_VALUE;
     }
  }

// Fractal swings: a high above the `left` bars before it and not below the `right` bars after it.
int FindPivots(const MqlRates &r[], int n, int left, int right, Pivot &out[])
  {
   ArrayResize(out, 0);
   int k = 0;
   for(int i = left; i < n - right; i++)
     {
      bool isHigh = true, isLow = true;
      for(int j = i - left; j < i; j++)
        {
         if(r[j].high >= r[i].high) isHigh = false;
         if(r[j].low <= r[i].low)   isLow = false;
        }
      for(int j = i + 1; j <= i + right; j++)
        {
         if(r[j].high > r[i].high) isHigh = false;
         if(r[j].low < r[i].low)   isLow = false;
        }
      if(isHigh)
        {
         ArrayResize(out, k + 1);
         out[k].index = i; out[k].price = r[i].high; out[k].kind = 1; out[k].confirmed = i + right;
         k++;
        }
      if(isLow)
        {
         ArrayResize(out, k + 1);
         out[k].index = i; out[k].price = r[i].low; out[k].kind = -1; out[k].confirmed = i + right;
         k++;
        }
     }
   return k;
  }

// Higher highs + higher lows = up (+1), lower highs + lower lows = down (-1).
int Bias(const Pivot &piv[], int np)
  {
   double h2 = 0, h1 = 0, l2 = 0, l1 = 0;
   int nh = 0, nl = 0;
   for(int i = np - 1; i >= 0 && (nh < 2 || nl < 2); i--)
     {
      if(piv[i].kind == 1 && nh < 2)  { if(nh == 0) h2 = piv[i].price; else h1 = piv[i].price; nh++; }
      if(piv[i].kind == -1 && nl < 2) { if(nl == 0) l2 = piv[i].price; else l1 = piv[i].price; nl++; }
     }
   if(nh < 2 || nl < 2) return 0;
   if(h2 > h1 && l2 > l1) return 1;
   if(h2 < h1 && l2 < l1) return -1;
   return 0;
  }

// Zones below price are support (nearest first), above are resistance (nearest first).
void SplitZones(const Zone &z[], double c, Zone &sup[], Zone &res[])
  {
   ArrayResize(sup, 0);
   ArrayResize(res, 0);
   int ns = 0, nr = 0;
   for(int i = 0; i < ArraySize(z); i++)
     {
      double mid = (z[i].lo + z[i].hi) / 2.0;
      if(mid < c) { ArrayResize(sup, ns + 1); sup[ns] = z[i]; ns++; }
      else        { ArrayResize(res, nr + 1); res[nr] = z[i]; nr++; }
     }
   for(int i = 1; i < ns; i++)   // highest support first
      for(int j = i; j > 0 && (sup[j].lo + sup[j].hi) > (sup[j - 1].lo + sup[j - 1].hi); j--)
        { Zone t = sup[j]; sup[j] = sup[j - 1]; sup[j - 1] = t; }
   for(int i = 1; i < nr; i++)   // lowest resistance first
      for(int j = i; j > 0 && (res[j].lo + res[j].hi) < (res[j - 1].lo + res[j - 1].hi); j--)
        { Zone t = res[j]; res[j] = res[j - 1]; res[j - 1] = t; }
  }

// Zones with a memory (port of LevelBook): a zone is born from a swing with its width fixed by the
// ATR at that swing; later swings at the same price add touches and may widen it up to that width,
// never move it. So zones only change when a new swing confirms, not on every candle.
void BuildLevels(const Pivot &piv[], int np, const double &atr[], double tolATR, int maxLevels, int minTouches,
                 Zone &out[])
  {
   double lo[], hi[], cap[];
   int touches[], last[];
   int n = 0;
   for(int i = 0; i < np; i++)
     {
      double a = atr[piv[i].index];
      if(!Valid(a)) continue;
      double p = piv[i].price, tol = tolATR * a;
      int best = -1;
      double bestD = 0;
      for(int k = 0; k < n; k++)
        {
         double d = (lo[k] <= p && p <= hi[k]) ? 0.0 : MathMin(MathAbs(p - lo[k]), MathAbs(p - hi[k]));
         if(d <= cap[k] / 2 && (best < 0 || d < bestD)) { best = k; bestD = d; }
        }
      if(best >= 0)
        {
         double nlo = MathMin(lo[best], p), nhi = MathMax(hi[best], p);
         if(nhi - nlo <= cap[best]) { lo[best] = nlo; hi[best] = nhi; }
         touches[best]++;
         last[best] = piv[i].index;
        }
      else
        {
         ArrayResize(lo, n + 1); ArrayResize(hi, n + 1); ArrayResize(cap, n + 1);
         ArrayResize(touches, n + 1); ArrayResize(last, n + 1);
         lo[n] = p - tol / 4; hi[n] = p + tol / 4; cap[n] = tol; touches[n] = 1; last[n] = piv[i].index;
         n++;
         if(n > maxLevels)                        // forget the zone touched longest ago
           {
            int old = 0;
            for(int k = 1; k < n; k++) if(last[k] < last[old]) old = k;
            for(int k = old; k < n - 1; k++)
              { lo[k] = lo[k + 1]; hi[k] = hi[k + 1]; cap[k] = cap[k + 1]; touches[k] = touches[k + 1]; last[k] = last[k + 1]; }
            n--;
           }
        }
     }
   ArrayResize(out, 0);
   int m = 0;
   for(int k = 0; k < n; k++)
      if(touches[k] >= minTouches)
        {
         ArrayResize(out, m + 1);
         out[m].lo = lo[k]; out[m].hi = hi[k]; out[m].touches = touches[k];
         m++;
        }
  }

bool Better(int t1, int l1, int s1, int t2, int l2, int s2)
  {
   if(t1 != t2) return t1 > t2;
   if(l1 != l2) return l1 > l2;
   return s1 > s2;
  }

// Trendlines drawn the way a trader would: every pair of the last 8 swings is a candidate; it must not
// cut through any candle between its anchors, nor be too steep; ranked by touches, last touch, length.
// Appends the best intact and the best just-broken line per side (an intact near-twin of a broken line
// is dropped).
void FindLines(const MqlRates &r[], int n, const Pivot &piv[], int np, const double &atr[], int t, int retest,
               int minSpan, bool htf, Line &out[])
  {
   for(int side = 0; side < 2; side++)
     {
      int kind = (side == 0) ? 1 : -1;          // +1 support through lows, -1 resistance through highs
      int pk = (kind == 1) ? -1 : 1;
      int sw[];
      int ns = 0;
      for(int i = 0; i < np; i++)
         if(piv[i].kind == pk) { ArrayResize(sw, ns + 1); sw[ns] = i; ns++; }
      int from = MathMax(0, ns - LINE_CANDIDATES);
      bool have[2] = {false, false};            // [0] intact, [1] broken
      Line best[2];
      int bt[2] = {0, 0}, bl[2] = {0, 0}, bs[2] = {0, 0};
      for(int a = from; a < ns; a++)
         for(int b = a + 1; b < ns; b++)
           {
            int ia = piv[sw[a]].index, ib = piv[sw[b]].index;
            double pa = piv[sw[a]].price, pb = piv[sw[b]].price;
            if(ib - ia < minSpan) continue;
            double slope = (pb - pa) / (ib - ia);
            if(kind == 1 ? slope <= 0 : slope >= 0) continue;
            double atrB = atr[ib];
            if(!Valid(atrB) || MathAbs(slope) > LINE_MAX_SLOPE_ATR * atrB) continue;
            bool cut = false;
            double tol = LINE_WICK_ATR * atrB;
            for(int i = ia; i <= ib && !cut; i++)
              {
               double v = pa + slope * (i - ia);
               if(kind == 1 ? r[i].low < v - tol : r[i].high > v + tol) cut = true;
              }
            if(cut) continue;
            int brk = -1;
            for(int j = ib + 1; j <= t && brk < 0; j++)
              {
               double v = pa + slope * (j - ia);
               double aj = Valid(atr[j]) ? atr[j] : atrB;
               if(kind == 1 ? r[j].close < v - LINE_BREAK_ATR * aj : r[j].close > v + LINE_BREAK_ATR * aj) brk = j;
              }
            if(brk >= 0 && t - brk > retest) continue;
            int touches = 0, last = -1;
            for(int q = 0; q < ns; q++)
              {
               int si = piv[sw[q]].index;
               if(si < ia) continue;
               if(brk >= 0 && si >= brk) continue;     // nothing counts after a break
               if(!Valid(atr[si])) continue;
               if(MathAbs(piv[sw[q]].price - (pa + slope * (si - ia))) <= LINE_TOUCH_ATR * atr[si])
                 { touches++; last = si; }
              }
            if(touches < 2) continue;
            int slot = (brk >= 0) ? 1 : 0;
            if(!have[slot] || Better(touches, last, ib - ia, bt[slot], bl[slot], bs[slot]))
              {
               have[slot] = true;
               bt[slot] = touches; bl[slot] = last; bs[slot] = ib - ia;
               best[slot].i1 = ia; best[slot].p1 = pa; best[slot].i2 = ib; best[slot].p2 = pb;
               best[slot].kind = kind; best[slot].broken = brk; best[slot].touches = touches;
               best[slot].lastTouch = last; best[slot].htf = htf;
              }
           }
      if(have[0] && have[1] && Valid(atr[t]) &&
         MathAbs(LineAt(best[0], t) - LineAt(best[1], t)) <= LINE_TOUCH_ATR * atr[t])
         have[0] = false;                         // its near-twin just broke, so in practice it broke too
      for(int s = 0; s < 2; s++)
         if(have[s])
           {
            int k = ArraySize(out);
            ArrayResize(out, k + 1);
            out[k] = best[s];
           }
     }
  }

// Order blocks: the last opposite candle before an impulse (>= 1 ATR) that closes beyond the last swing.
int Origin(const MqlRates &r[], int swingIndex, int t, bool wantBearish)
  {
   for(int i = t - 1; i >= MathMax(swingIndex, t - OB_LOOKBACK); i--)
      if(wantBearish ? r[i].close < r[i].open : r[i].close > r[i].open)
         return i;
   return -1;
  }

int FirstCloseBeyond(const MqlRates &r[], int n, int t, double level, bool below)
  {
   for(int j = t + 1; j < n; j++)
      if(below ? r[j].close < level : r[j].close > level)
         return j;
   return -1;
  }

void FindOrderBlocks(const MqlRates &r[], int n, const Pivot &piv[], int np, const double &atr[], OBlock &out[])
  {
   ArrayResize(out, 0);
   int k = 0, p = 0, lastHigh = -1, lastLow = -1;
   for(int t = 0; t < n; t++)
     {
      while(p < np && piv[p].confirmed <= t)
        {
         if(piv[p].kind == 1) lastHigh = p; else lastLow = p;
         p++;
        }
      if(!Valid(atr[t])) continue;
      if(lastHigh >= 0 && r[t].close > piv[lastHigh].price)
        {
         int ob = Origin(r, piv[lastHigh].index, t, true);
         if(ob >= 0)
           {
            double mx = r[ob + 1].high;
            for(int j = ob + 1; j <= t; j++) mx = MathMax(mx, r[j].high);
            if(mx - r[ob].high >= OB_DISP_ATR * atr[t])
              {
               ArrayResize(out, k + 1);
               out[k].kind = 1; out[k].index = ob; out[k].lo = r[ob].low; out[k].hi = r[ob].high;
               out[k].created = t; out[k].invalid = FirstCloseBeyond(r, n, t, r[ob].low, true);
               k++;
              }
           }
         lastHigh = -1;                           // each swing is broken once
        }
      if(lastLow >= 0 && r[t].close < piv[lastLow].price)
        {
         int ob = Origin(r, piv[lastLow].index, t, false);
         if(ob >= 0)
           {
            double mn = r[ob + 1].low;
            for(int j = ob + 1; j <= t; j++) mn = MathMin(mn, r[j].low);
            if(r[ob].low - mn >= OB_DISP_ATR * atr[t])
              {
               ArrayResize(out, k + 1);
               out[k].kind = -1; out[k].index = ob; out[k].lo = r[ob].low; out[k].hi = r[ob].high;
               out[k].created = t; out[k].invalid = FirstCloseBeyond(r, n, t, r[ob].high, false);
               k++;
              }
           }
         lastLow = -1;
        }
     }
  }

bool OBActive(const OBlock &b, int t)
  {
   return b.created <= t && (b.invalid < 0 || b.invalid > t) && t - b.created <= C.obMaxAge;
  }

// Position of the entry candle t on the higher-timeframe bar axis (for evaluating HTF trendlines).
double HTFx(int t)
  {
   if(NH <= 0) return 0;
   double htfSec = (double)PeriodSeconds(C.htf);
   datetime entryClose = R[t].time + PeriodSeconds(PERIOD_CURRENT);
   datetime htfLastClose = RH[NH - 1].time + PeriodSeconds(C.htf);
   return (NH - 1) + (double)(entryClose - htfLastClose) / htfSec;
  }

double LineValue(const Line &ln, int t)
  {
   return ln.htf ? LineAt(ln, HTFx(t)) : LineAt(ln, t);
  }

//+------------------------------------------------------------------+
//| Load candles and analyse (closed candles only)                   |
//+------------------------------------------------------------------+
bool Analyse()
  {
   ArraySetAsSeries(R, false);
   N = CopyRates(_Symbol, PERIOD_CURRENT, 1, MathMax(InpHistoryBars, 200), R);
   if(N < 100) return false;
   CalcATR(R, N, A);
   NP = FindPivots(R, N, C.pivot, C.pivot, P);
   int t = N - 1;
   double c = R[t].close;

   Zone z[];
   BuildLevels(P, NP, A, ZONE_TOL_ATR, C.zoneLookback, 2, z);
   SplitZones(z, c, ZS, ZR);
   ArrayResize(L, 0);
   FindLines(R, N, P, NP, A, t, C.retest, 2 * (C.pivot + C.pivot), false, L);
   FindOrderBlocks(R, N, P, NP, A, OB);
   biasE = Bias(P, NP);

   NH = 0; NPH = 0;
   ArrayResize(HZS, 0); ArrayResize(HZR, 0); ArrayResize(LH, 0);
   biasH = 0;
   if(C.htf != PERIOD_CURRENT && PeriodSeconds(C.htf) > PeriodSeconds(PERIOD_CURRENT))
     {
      ArraySetAsSeries(RH, false);
      NH = CopyRates(_Symbol, C.htf, 1, MathMax(InpHTFHistoryBars, 100), RH);
      if(NH >= 2 * ATR_PERIOD)
        {
         CalcATR(RH, NH, AH);
         NPH = FindPivots(RH, NH, C.htfPivot, C.htfPivot, PH);
         biasH = Bias(PH, NPH);
         Zone hz[];
         BuildLevels(PH, NPH, AH, HTF_ZONE_TOL_ATR, 200, 2, hz);   // all history: old levels count
         SplitZones(hz, c, HZS, HZR);
         FindLines(RH, NH, PH, NPH, AH, NH - 1, C.retest, 4 * C.htfPivot, true, LH);
        }
      else
         NH = 0;
     }
   direction = (NH > 0) ? biasH : biasE;
   return Valid(A[t]);
  }

//+------------------------------------------------------------------+
//| Setups (port of strategy.py _long/_short/_finish)                |
//+------------------------------------------------------------------+
void AddTag(Tag &tags[], string reason, double level)
  {
   int k = ArraySize(tags);
   ArrayResize(tags, k + 1);
   tags[k].reason = reason;
   tags[k].level = level;
  }

bool Finish(int side, string setup, double stop, string reasons, Signal &s)
  {
   int t = N - 1;
   double c = R[t].close, a = A[t];
   double floorDist = C.minStopATR * a;
   stop = (side == 1) ? MathMin(stop, c - floorDist) : MathMax(stop, c + floorDist);

   bool have = false;
   double target = 0;
   if(side == 1)
     {
      for(int i = 0; i < ArraySize(ZR); i++)  if(ZR[i].lo > c  && (!have || ZR[i].lo < target))  { target = ZR[i].lo;  have = true; }
      for(int i = 0; i < ArraySize(HZR); i++) if(HZR[i].lo > c && (!have || HZR[i].lo < target)) { target = HZR[i].lo; have = true; }
      for(int i = 0; i < ArraySize(OB); i++)
         if(OB[i].kind == -1 && OBActive(OB[i], t) && OB[i].lo > c && (!have || OB[i].lo < target)) { target = OB[i].lo; have = true; }
      for(int k = 0; k < 2; k++)
        {
         int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
         for(int i = 0; i < cnt; i++)
           {
            Line ln;
            if(k == 0) ln = L[i]; else ln = LH[i];
            if(ln.kind != -1 || ln.broken >= 0) continue;
            double v = LineValue(ln, t);
            if(v > c && (!have || v < target)) { target = v; have = true; }
           }
        }
      if(!have) target = c + C.defaultRR * (c - stop);
     }
   else
     {
      for(int i = 0; i < ArraySize(ZS); i++)  if(ZS[i].hi < c  && (!have || ZS[i].hi > target))  { target = ZS[i].hi;  have = true; }
      for(int i = 0; i < ArraySize(HZS); i++) if(HZS[i].hi < c && (!have || HZS[i].hi > target)) { target = HZS[i].hi; have = true; }
      for(int i = 0; i < ArraySize(OB); i++)
         if(OB[i].kind == 1 && OBActive(OB[i], t) && OB[i].hi < c && (!have || OB[i].hi > target)) { target = OB[i].hi; have = true; }
      for(int k = 0; k < 2; k++)
        {
         int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
         for(int i = 0; i < cnt; i++)
           {
            Line ln;
            if(k == 0) ln = L[i]; else ln = LH[i];
            if(ln.kind != 1 || ln.broken >= 0) continue;
            double v = LineValue(ln, t);
            if(v < c && (!have || v > target)) { target = v; have = true; }
           }
        }
      if(!have) target = c - C.defaultRR * (stop - c);
     }

   double trigger = (C.confirm == CONFIRM_NONE) ? 0.0 : (side == 1 ? R[t].high : R[t].low);
   double entry = (C.confirm == CONFIRM_NONE) ? c : trigger;   // judge reward:risk from the fill level
   double risk = MathAbs(entry - stop);
   if(risk <= 0) return false;
   bool beyond = (side == 1) ? entry >= target : entry <= target;
   double rr = MathAbs(target - entry) / risk;
   if(beyond || rr < C.minRR) return false;

   s.side = side; s.setup = setup; s.entry = entry; s.stop = stop; s.target = target; s.trigger = trigger; s.rr = rr;
   s.reasons = reasons + "; " + (NH > 0 ? "HTF bias " + BiasName(biasH) : "bias " + BiasName(biasE));
   return true;
  }

bool Setup(int side, Signal &s)
  {
   int t = N - 1;
   double o = R[t].open, h = R[t].high, lo = R[t].low, c = R[t].close, a = A[t];
   if(C.trendFilter && direction == -side) return false;
   // rejection candle: closes in the direction of the trade, in the far half of its range
   if(side == 1 && !(c > o && c >= lo + 0.5 * (h - lo))) return false;
   if(side == -1 && !(c < o && c <= h - 0.5 * (h - lo))) return false;
   double buf = TOUCH_BUF_ATR * a;
   Tag tags[];

   if(side == 1)
     {
      for(int i = 0; i < ArraySize(ZS); i++)
         if(lo <= ZS[i].hi + buf && c > ZS[i].lo)
           { AddTag(tags, StringFormat("support zone %s-%s (x%d)", PS(ZS[i].lo), PS(ZS[i].hi), ZS[i].touches), ZS[i].lo); break; }
      for(int i = 0; i < ArraySize(HZS); i++)
         if(lo <= HZS[i].hi + buf && c > HZS[i].lo)
           { AddTag(tags, StringFormat("HTF support zone %s-%s (x%d)", PS(HZS[i].lo), PS(HZS[i].hi), HZS[i].touches), HZS[i].lo); break; }
     }
   else
     {
      for(int i = 0; i < ArraySize(ZR); i++)
         if(h >= ZR[i].lo - buf && c < ZR[i].hi)
           { AddTag(tags, StringFormat("resistance zone %s-%s (x%d)", PS(ZR[i].lo), PS(ZR[i].hi), ZR[i].touches), ZR[i].hi); break; }
      for(int i = 0; i < ArraySize(HZR); i++)
         if(h >= HZR[i].lo - buf && c < HZR[i].hi)
           { AddTag(tags, StringFormat("HTF resistance zone %s-%s (x%d)", PS(HZR[i].lo), PS(HZR[i].hi), HZR[i].touches), HZR[i].hi); break; }
     }

   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         double v = LineValue(ln, t);
         string tf = ln.htf ? "HTF " : "";
         // a broken entry-chart line must have broken before this candle; HTF candles are always earlier
         bool brokeBefore = ln.broken >= 0 && (ln.htf || ln.broken < t);
         if(side == 1)
           {
            if(ln.kind == 1 && ln.broken < 0 && lo <= v + buf && c > v)
               AddTag(tags, StringFormat("%srising trendline at %s (%d touches)", tf, PS(v), ln.touches), v);
            if(ln.kind == -1 && brokeBefore && lo <= v + buf && c > v)
               AddTag(tags, StringFormat("retest of broken %sfalling trendline at %s", tf, PS(v)), v);
           }
         else
           {
            if(ln.kind == -1 && ln.broken < 0 && h >= v - buf && c < v)
               AddTag(tags, StringFormat("%sfalling trendline at %s (%d touches)", tf, PS(v), ln.touches), v);
            if(ln.kind == 1 && brokeBefore && h >= v - buf && c < v)
               AddTag(tags, StringFormat("retest of broken %srising trendline at %s", tf, PS(v)), v);
           }
        }
     }

   for(int i = ArraySize(OB) - 1; i >= 0; i--)    // most recent first
     {
      if(OB[i].kind != side || !OBActive(OB[i], t)) continue;
      if(side == 1 && lo <= OB[i].hi + buf && c > OB[i].lo)
        { AddTag(tags, StringFormat("bullish order block %s-%s", PS(OB[i].lo), PS(OB[i].hi)), OB[i].lo); break; }
      if(side == -1 && h >= OB[i].lo - buf && c < OB[i].hi)
        { AddTag(tags, StringFormat("bearish order block %s-%s", PS(OB[i].lo), PS(OB[i].hi)), OB[i].hi); break; }
     }

   int nt = ArraySize(tags);
   if(nt >= C.minConfluence)
     {
      double stop = (side == 1) ? lo : h;
      string reasons = "";
      for(int i = 0; i < nt; i++)
        {
         stop = (side == 1) ? MathMin(stop, tags[i].level) : MathMax(stop, tags[i].level);
         reasons += (i > 0 ? "; " : "") + tags[i].reason;
        }
      stop += -side * STOP_BUF_ATR * a;
      return Finish(side, "rejection", stop, reasons, s);
     }

   // trendline breakout: a strong candle closes through an entry-chart line on this very candle
   if(C.breakouts && side * (c - o) >= BREAKOUT_BODY * (h - lo))
      for(int i = 0; i < ArraySize(L); i++)
        {
         if(L[i].kind != -side || L[i].broken != t) continue;
         double v = LineAt(L[i], t);
         double stop = (side == 1) ? MathMin(lo, v) - STOP_BUF_ATR * a : MathMax(h, v) + STOP_BUF_ATR * a;
         string why = StringFormat("close %s trendline at %s", side == 1 ? "above falling" : "below rising", PS(v));
         return Finish(side, "breakout", stop, why, s);
        }
   return false;
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

//--- TradingView-style position: green box entry -> take-profit, red box entry -> stop-loss
void PositionBox(string nm, datetime t1, datetime t2, double entry, double sl, double tp, bool text)
  {
   color up = C'42,157,143', dn = C'231,111,81', ink = C'38,70,83';
   double risk = MathAbs(entry - sl);
   datetime mid = (datetime)(t1 + (t2 - t1) / 2);
   if(tp > 0)
     {
      Rect(nm + "tpF", t1, entry, t2, tp, C'204,234,228', true, 1);
      Rect(nm + "tpB", t1, entry, t2, tp, up, false, 1);
      if(text)
         Text(nm + "tpT", mid, tp, StringFormat("Take profit %.2f%%", MathAbs(tp - entry) / entry * 100)
              + (risk > 0 ? StringFormat("  %.1fR", MathAbs(tp - entry) / risk) : ""), up, tp > entry ? ANCHOR_LOWER : ANCHOR_UPPER);
     }
   if(sl > 0)
     {
      Rect(nm + "slF", t1, entry, t2, sl, C'250,215,205', true, 1);
      Rect(nm + "slB", t1, entry, t2, sl, dn, false, 1);
      if(text)
         Text(nm + "slT", mid, sl, StringFormat("Stop loss %.2f%%", risk / entry * 100), dn, sl > entry ? ANCHOR_LOWER : ANCHOR_UPPER);
     }
   Segment(nm + "e", t1, entry, t2, entry, ink, 2, STYLE_SOLID, false);
  }

//--- this EA's trades opened since `since` as boxes from entry to exit; the open one runs up to `right`
void DrawTrades(datetime since, datetime right)
  {
   color up = C'42,157,143', dn = C'231,111,81', ink = C'38,70,83';
   ulong openId = 0;
   ulong tk = MyPosition();
   if(tk > 0 && PositionSelectByTicket(tk)) openId = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
   if(!HistorySelect(since, TimeCurrent() + 86400)) return;
   int total = HistoryDealsTotal(), drawn = 0;
   for(int i = total - 1; i >= 0 && drawn < 30; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic || HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
      ulong id = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
      datetime t1 = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
      double entry = HistoryDealGetDouble(d, DEAL_PRICE);
      double sl = HistoryDealGetDouble(d, DEAL_SL), tp = HistoryDealGetDouble(d, DEAL_TP);   // as the trade was opened
      string nm = PFX + "tr" + IntegerToString((long)id);
      if(id == openId)
        {
         PositionBox(nm, t1, right, entry, sl, tp, true);
         double slNow = PositionGetDouble(POSITION_SL);
         if(slNow > 0 && Px(slNow) != Px(sl))                   // break-even / trailing: where the stop is now
           {
            Segment(nm + "sl", t1, slNow, right, slNow, dn, 1, STYLE_DASH, false);
            Text(nm + "slN", right, slNow, "SL now " + PS(slNow), dn, ANCHOR_LEFT);
           }
         Text(nm + "eT", right, entry, (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "LONG open " : "SHORT open ")
              + PS(entry), ink, ANCHOR_LEFT);
         drawn++;
         continue;
        }
      datetime t2 = 0;                                       // the position's last closing deal
      double exitPx = 0, pnl = 0;
      for(int j = i + 1; j < total; j++)
        {
         ulong o = HistoryDealGetTicket(j);
         if(o == 0 || (ulong)HistoryDealGetInteger(o, DEAL_POSITION_ID) != id) continue;
         pnl += HistoryDealGetDouble(o, DEAL_PROFIT) + HistoryDealGetDouble(o, DEAL_SWAP) + HistoryDealGetDouble(o, DEAL_COMMISSION);
         if(HistoryDealGetInteger(o, DEAL_ENTRY) == DEAL_ENTRY_OUT)
           { t2 = (datetime)HistoryDealGetInteger(o, DEAL_TIME); exitPx = HistoryDealGetDouble(o, DEAL_PRICE); }
        }
      if(t2 == 0) continue;                                  // not closed (or closed outside this history window)
      pnl += HistoryDealGetDouble(d, DEAL_COMMISSION);
      PositionBox(nm, t1, t2, entry, sl, tp, false);
      Segment(nm + "x", t1, entry, t2, exitPx, ink, 1, STYLE_DOT, false);
      double top = MathMax(entry, MathMax(sl, tp));
      Text(nm + "pl", (datetime)(t1 + (t2 - t1) / 2), top, StringFormat("%+.2f %s", pnl, AccountInfoString(ACCOUNT_CURRENCY)),
           pnl >= 0 ? up : dn, ANCHOR_LOWER);
      drawn++;
     }
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
   for(int i = 0; i < ArraySize(LH); i++)
     {
      string nm = PFX + "lh" + IntegerToString(i);
      datetime t1 = RH[LH[i].i1].time, t2 = RH[LH[i].i2].time;
      if(LH[i].broken < 0)
         Segment(nm, t1, LH[i].p1, t2, LH[i].p2, htf, 3, STYLE_SOLID, true);
      else
         Segment(nm, t1, LH[i].p1, RH[LH[i].broken].time, LineAt(LH[i], LH[i].broken), htf, 3, STYLE_SOLID, false);
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

   DrawTrades(left, right);

   Signal shown = s;
   bool show = haveSignal && MyPosition() == 0;
   datetime boxFrom = R[t].time;
   if(armed) { shown = armedSig; show = true; boxFrom = armedAt; }   // a waiting setup stays on the chart until filled/cancelled
   if(show)
     {
      Signal s2 = shown;
      double entry = (s2.trigger > 0) ? s2.trigger : s2.entry;
      PositionBox(PFX + "setup", boxFrom, right, entry, s2.stop, s2.target, true);
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
            armedAt = R[N - 1].time;
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
