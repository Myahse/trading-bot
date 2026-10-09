//+------------------------------------------------------------------+
//|                                                 TradeBotCore.mqh |
//|  The analysis shared by the EA (TradeBot.mq5) and the parity     |
//|  check (TradeBotParity.mq5): structure, zones, trendlines, order |
//|  blocks and the two setups. Port of tradebot/strategy.py.        |
//+------------------------------------------------------------------+
#ifndef TRADEBOT_CORE_MQH
#define TRADEBOT_CORE_MQH

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
#define PATTERN_TOL_ATR     0.5
#define PATTERN_DEPTH_ATR   1.5
#define HEAD_ATR            0.5
#define PAT_BREAK_ATR       0.1
#define PAT_FAIL_ATR        0.3
#define FAKE_BARS           3
#define SAME_PLACE_ATR      0.5     // zones closer than this many ATRs are drawn as one place (PlanShown)
#define FAKE_BREAK_ATR      0.1

//--- data types
struct Config
  {
   ENUM_TIMEFRAMES   htf;
   int               pivot, htfPivot, minConfluence, confirm, confirmBars, retest, cooldown, obMaxAge, zoneLookback;
   double            minRR, defaultRR, minStopATR;
   bool              trendFilter, breakouts;
   double            riskPct, minLotMaxRisk, beR, partialR, partialPct, trailStartR, trailATR, maxDailyLoss;
   int               trail, maxTradesDay;
   bool              zoneBest;            // charts: only the best zone (swing) instead of every tradable one (scalp)
   bool              patterns;            // candlestick and chart patterns count as levels (and neckline breaks)
  };

struct Pivot  { int index; double price; int kind; int confirmed; };          // kind +1 swing high, -1 swing low
struct Zone   { double lo; double hi; int touches; };
struct Line   { int i1; double p1; int i2; double p2; int kind; int broken; int touches; int lastTouch; bool htf; };
                                                                              // kind +1 rising support, -1 falling resistance
struct OBlock { int kind; int index; double lo; double hi; int created; int invalid; }; // kind +1 bullish
struct Tag    { string reason; double level; };
struct Signal { int side; string setup; double entry; double stop; double target; double trigger; double rr; string reasons;
                double zoneLo; double zoneHi; double htfLo; double htfHi; };   // zones the rejection came off (0 = none)

// A chart pattern (port of tradebot/patterns.py): its swings, its neckline and whether it broke
struct CPattern { string kind; int side; int np; int pi[5]; double pp[5]; int n1; double p1; int n2; double p2;
                  int complete; int broken; double invalid; double height; };

// A fake break (port of tradebot/fakebreak.py): through a level and back within FAKE_BARS candles.
// side = direction of the trap: +1 when price faked down and came back up.
struct FBreak { string what; int side; double level; int broke; int back; double extreme; };

// A zone the bot could trade from, with the plan if price came back to it (port of tradebot/zoneplan.py)
struct ZPlan  { double lo; double hi; int side; bool htf; bool backed; double entry; double stop; double target;
                double rr; bool tradable; string why; };

void ClearSignal(Signal &s)
  {
   s.side = 0; s.setup = ""; s.entry = 0; s.stop = 0; s.target = 0; s.trigger = 0; s.rr = 0; s.reasons = "";
   s.zoneLo = 0; s.zoneHi = 0; s.htfLo = 0; s.htfHi = 0;
  }

//--- state
Config   C;
MqlRates R[];   int N = 0;   double A[];   Pivot P[];  int NP = 0;            // entry timeframe
MqlRates RH[];  int NH = 0;  double AH[];  Pivot PH[]; int NPH = 0;           // higher timeframe
Zone     ZS[], ZR[], HZS[], HZR[];                                            // support / resistance, nearest first
Line     L[];   Line LH[];
OBlock   OB[];
int      biasE = 0, biasH = 0, direction = 0;
CPattern PAT[];
FBreak   FB[];                                                                // fake breaks, most recent first                                                               // chart patterns alive now
string   candleName = "";  int candleSide = 0;                                // candlestick pattern on the last candle
int      gHistoryBars = 5000, gHTFHistoryBars = 1000;                         // candles analysed

//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Small helpers                                                    |
//+------------------------------------------------------------------+
bool   Valid(double v)          { return v != EMPTY_VALUE && MathIsValidNumber(v) && v > 0; }
double Px(double p)             { return NormalizeDouble(p, _Digits); }
string PS(double p)             { return DoubleToString(p, _Digits); }
string BiasName(int b)          { return b > 0 ? "up" : (b < 0 ? "down" : "neutral"); }
double LineAt(const Line &ln, double x) { return ln.p1 + (ln.p2 - ln.p1) / (ln.i2 - ln.i1) * (x - ln.i1); }

//+------------------------------------------------------------------+
//| Presets (same values as MODES in tradebot/strategy.py)           |
//+------------------------------------------------------------------+
void ApplyPreset(int preset)
  {
   if(preset == PRESET_SCALP)
     {
      C.htf = PERIOD_H1; C.pivot = 3; C.htfPivot = 3; C.minConfluence = 2; C.confirm = CONFIRM_BREAK; C.confirmBars = 3;
      C.retest = 12; C.cooldown = 6; C.obMaxAge = 100; C.zoneLookback = 30; C.minRR = 1.5; C.defaultRR = 1.5;
      C.minStopATR = 1.0; C.trendFilter = true; C.breakouts = true;
      C.riskPct = 0.5; C.minLotMaxRisk = 0.625; C.beR = 1.0; C.partialR = 1.0; C.partialPct = 50.0;
      C.trail = TRAIL_ATR; C.trailStartR = 1.0; C.trailATR = 1.5; C.maxDailyLoss = 3.0; C.maxTradesDay = 8;
      C.zoneBest = false; C.patterns = false;   // shown, not counted: they made 5m entries worse
     }
   else if(preset == PRESET_SWING)
     {
      C.htf = PERIOD_D1; C.pivot = 5; C.htfPivot = 3; C.minConfluence = 2; C.confirm = CONFIRM_BREAK; C.confirmBars = 2;
      C.retest = 15; C.cooldown = 3; C.obMaxAge = 150; C.zoneLookback = 40; C.minRR = 2.0; C.defaultRR = 2.5;
      C.minStopATR = 0.0; C.trendFilter = true; C.breakouts = true;
      C.riskPct = 1.0; C.minLotMaxRisk = 1.25; C.beR = 1.0; C.partialR = 1.5; C.partialPct = 50.0;
      C.trail = TRAIL_SWING; C.trailStartR = 1.5; C.trailATR = 2.0; C.maxDailyLoss = 0.0; C.maxTradesDay = 0;
      C.zoneBest = true; C.patterns = true;
     }
  }

//+------------------------------------------------------------------+
//| Market structure                                                 |
//+------------------------------------------------------------------+
// ATR with Wilder smoothing, computed exactly as pandas' ewm(alpha=1/14, adjust=False) in the Python bot,
// so both give the same bits: zones are built from it, and a last-bit difference can tip a swing into
// a different zone. Valid from bar ATR_PERIOD-1.
void CalcATR(const MqlRates &r[], int n, double &atr[])
  {
   ArrayResize(atr, n);
   double alpha = 1.0 / ATR_PERIOD, keep = 1.0 - alpha;
   double w = 0.0;
   for(int i = 0; i < n; i++)
     {
      double tr = r[i].high - r[i].low;
      if(i > 0)
         tr = MathMax(tr, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
      if(i == 0)
         w = tr;
      else if(w != tr)
         w = (keep * w + alpha * tr) / (keep + alpha);
      atr[i] = (i >= ATR_PERIOD - 1) ? w : EMPTY_VALUE;
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

// First chart candle at or after `when` (N if none).
int FirstBarFrom(datetime when)
  {
   int lo = 0, hi = N;
   while(lo < hi) { int mid = (lo + hi) / 2; if(R[mid].time < when) lo = mid + 1; else hi = mid; }
   return lo;
  }

// Higher-timeframe trendlines, as in the Python bot (TrendlineFinder with htf=True): drawn through the
// HTF swings placed on the chart candle that made each extreme, checked against the chart's candles
// and measured with the HTF ATR, so they break - and can trigger a breakout - on a chart candle's close.
void FindHTFLines(int t)
  {
   int htfSec = PeriodSeconds(C.htf);
   double AE[];                                   // HTF ATR known at each chart candle
   ArrayResize(AE, N);
   int k = 0;
   for(int i = 0; i < N; i++)
     {
      while(k < NH && RH[k].time + htfSec <= R[i].time) k++;
      AE[i] = (k > 0) ? AH[k - 1] : EMPTY_VALUE;
     }
   // chart candles per HTF candle, from the candles seen so far (Python: bars_per_candle)
   int groups = 0, lastStart = 0, g = -1, prevKey = 0;
   for(int i = 0; i < N; i++)
     {
      while(g + 1 < NH && RH[g + 1].time <= R[i].time) g++;
      int key = (g >= 0 && R[i].time < RH[g].time + htfSec) ? g : -2 - g;   // HTF candle (or a gap after it)
      if(i == 0 || key != prevKey) { groups++; lastStart = i; }
      prevKey = key;
     }
   double perCandle = (groups > 1) ? (double)lastStart / (groups - 1) : 1.0;
   Pivot PE[];                                    // HTF swings on the chart's candle axis
   int ne = 0;
   for(int p = 0; p < NPH; p++)
     {
      int first = FirstBarFrom(RH[PH[p].index].time), at = -1;
      for(int i = first; i < N && R[i].time < RH[PH[p].index].time + htfSec; i++)
         if(at < 0 || (PH[p].kind == 1 ? R[i].high > R[at].high : R[i].low < R[at].low)) at = i;
      if(at < 0) continue;                        // older than the chart candles loaded
      ArrayResize(PE, ne + 1);
      PE[ne].index = at; PE[ne].price = PH[p].price; PE[ne].kind = PH[p].kind;
      PE[ne].confirmed = FirstBarFrom(RH[PH[p].confirmed].time + htfSec);
      ne++;
     }
   FindLines(R, N, PE, ne, AE, t, (int)(C.retest * perCandle), (int)(4 * C.htfPivot * perCandle), true, LH);
  }

//+------------------------------------------------------------------+
//| Load candles and analyse (closed candles only)                   |
//+------------------------------------------------------------------+
// Analyse as of the candle `shift` bars back (1 = the last closed one). Only higher-timeframe
// candles that had closed by then are used.
bool Analyse(int shift = 1)
  {
   ArraySetAsSeries(R, false);
   N = CopyRates(_Symbol, PERIOD_CURRENT, shift, MathMax(gHistoryBars, 200), R);
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
      // As in the Python bot (and its backtests), a higher-timeframe candle is used from the first
      // chart candle after it closed: only HTF candles that closed by the open of candle t count.
      datetime closedBy = R[t].time - PeriodSeconds(C.htf);
      NH = CopyRates(_Symbol, C.htf, closedBy, MathMax(gHTFHistoryBars, 100), RH);
      if(NH >= 2 * ATR_PERIOD)
        {
         CalcATR(RH, NH, AH);
         NPH = FindPivots(RH, NH, C.htfPivot, C.htfPivot, PH);
         biasH = Bias(PH, NPH);
         // A zone's width comes from the HTF ATR known while its swing formed, i.e. that of the
         // previous closed HTF candle (as in the Python bot).
         double AHknown[];
         ArrayResize(AHknown, NH);
         AHknown[0] = EMPTY_VALUE;
         for(int i = 1; i < NH; i++) AHknown[i] = AH[i - 1];
         Zone hz[];
         BuildLevels(PH, NPH, AHknown, HTF_ZONE_TOL_ATR, 200, 2, hz);   // all history: old levels count
         SplitZones(hz, c, HZS, HZR);
         FindHTFLines(t);
        }
      else
         NH = 0;
     }
   direction = (NH > 0) ? biasH : biasE;
   candleName = ""; candleSide = 0; ArrayResize(PAT, 0); ArrayResize(FB, 0);
   if(Valid(A[t]))
     {
      CandlePattern(t);
      FindChartPatterns(t);
      FindFakeBreaks(t);
     }
   return Valid(A[t]);
  }


//+------------------------------------------------------------------+
//| Patterns (port of tradebot/patterns.py)                          |
//+------------------------------------------------------------------+
// Candlestick pattern on candle t: engulfing, morning/evening star, hammer/shooting star.
void CandlePattern(int t)
  {
   candleName = ""; candleSide = 0;
   if(t < 2) return;
   double o = R[t].open, h = R[t].high, l = R[t].low, c = R[t].close, rng = h - l;
   if(rng <= 0) return;
   double body = MathAbs(c - o), body1 = MathAbs(R[t - 1].close - R[t - 1].open);
   double o1 = R[t - 1].open, c1 = R[t - 1].close;
   if(c > o && c1 < o1 && c >= o1 && o <= c1 && body > body1) { candleName = "bullish engulfing"; candleSide = 1; return; }
   if(c < o && c1 > o1 && c <= o1 && o >= c1 && body > body1) { candleName = "bearish engulfing"; candleSide = -1; return; }
   double rng2 = R[t - 2].high - R[t - 2].low, body2 = MathAbs(R[t - 2].close - R[t - 2].open);
   if(rng2 > 0 && body2 >= 0.6 * rng2 && body1 <= 0.3 * body2)
     {
      double mid2 = (R[t - 2].open + R[t - 2].close) / 2;
      if(R[t - 2].close < R[t - 2].open && c > o && c > mid2) { candleName = "morning star"; candleSide = 1; return; }
      if(R[t - 2].close > R[t - 2].open && c < o && c < mid2) { candleName = "evening star"; candleSide = -1; return; }
     }
   double lower = MathMin(o, c) - l, upper = h - MathMax(o, c);
   if(lower >= 0.6 * rng && lower >= 2 * body && upper <= 0.25 * rng) { candleName = "hammer"; candleSide = 1; return; }
   if(upper >= 0.6 * rng && upper >= 2 * body && lower <= 0.25 * rng) { candleName = "shooting star"; candleSide = -1; return; }
  }

double PatNeck(const CPattern &p, double i)
  {
   return (p.n2 == p.n1) ? p.p1 : p.p1 + (p.p2 - p.p1) / (p.n2 - p.n1) * (i - p.n1);
  }

// The measured move: the pattern's height beyond the neckline, from the break.
double PatTarget(const CPattern &p)
  {
   double x = (p.broken >= 0) ? p.broken : p.pi[p.np - 1];
   return PatNeck(p, x) + p.side * p.height;
  }

// Follows the pattern from its completion to t: its neckline break, or its failure. False = drop it.
bool TrackPattern(CPattern &p, int t, int maxAge, int retest)
  {
   p.broken = -1;
   if(p.complete > t) return false;
   for(int j = p.complete; j <= t; j++)
     {
      double a = A[j];
      if(!Valid(a)) continue;
      if(p.side * (R[j].close - PatNeck(p, j)) > PAT_BREAK_ATR * a)
        {
         if(t - j > retest) return false;
         p.broken = j;
         return true;
        }
      if(p.side * (p.invalid - R[j].close) > PAT_FAIL_ATR * a) return false;
     }
   return t - p.complete <= maxAge;
  }

void AddPattern(CPattern &p, int t)
  {
   if(!TrackPattern(p, t, C.obMaxAge, C.retest)) return;
   int k = ArraySize(PAT);
   ArrayResize(PAT, k + 1);
   PAT[k] = p;
  }

// The swing (index into P) of kind `kind` between bars i1 and i2 that is furthest in `dir`, or -1.
int ExtremeBetween(int kind, int i1, int i2, int dir)
  {
   int best = -1;
   for(int q = 0; q < NP; q++)
      if(P[q].kind == kind && P[q].index > i1 && P[q].index < i2 && (best < 0 || dir * P[q].price > dir * P[best].price))
         best = q;
   return best;
  }

// Double bottom/top and (inverse) head and shoulders, from the swings confirmed by t.
void FindChartPatterns(int t)
  {
   ArrayResize(PAT, 0);
   for(int side = 1; side >= -1; side -= 2)
     {
      int kindExt = (side == 1) ? -1 : 1;           // long: the lows make it, the highs between make the neckline
      int ext[];
      int ne = 0;
      for(int q = 0; q < NP; q++) if(P[q].kind == kindExt) { ArrayResize(ext, ne + 1); ext[ne++] = q; }
      if(ne >= 2)
        {
         Pivot a = P[ext[ne - 2]], b = P[ext[ne - 1]];
         int mid = ExtremeBetween(-kindExt, a.index, b.index, side);
         double atrB = A[b.index];
         if(mid >= 0 && Valid(atrB))
           {
            double worst = (side == 1) ? MathMin(a.price, b.price) : MathMax(a.price, b.price);
            if(MathAbs(a.price - b.price) <= PATTERN_TOL_ATR * atrB && side * (P[mid].price - worst) >= PATTERN_DEPTH_ATR * atrB)
              {
               CPattern p;
               p.kind = (side == 1) ? "double bottom" : "double top"; p.side = side; p.np = 3;
               p.pi[0] = a.index; p.pp[0] = a.price; p.pi[1] = P[mid].index; p.pp[1] = P[mid].price; p.pi[2] = b.index; p.pp[2] = b.price;
               p.n1 = P[mid].index; p.p1 = P[mid].price; p.n2 = b.index; p.p2 = P[mid].price;
               p.complete = b.confirmed; p.broken = -1; p.invalid = worst; p.height = side * (P[mid].price - worst);
               AddPattern(p, t);
              }
           }
        }
      if(ne >= 3)
        {
         Pivot s1 = P[ext[ne - 3]], hd = P[ext[ne - 2]], s2 = P[ext[ne - 1]];
         double atrS = A[s2.index];
         int m1 = ExtremeBetween(-kindExt, s1.index, hd.index, side), m2 = ExtremeBetween(-kindExt, hd.index, s2.index, side);
         double shoulder = (side == 1) ? MathMin(s1.price, s2.price) : MathMax(s1.price, s2.price);
         if(m1 >= 0 && m2 >= 0 && Valid(atrS) && MathAbs(s1.price - s2.price) <= 2 * PATTERN_TOL_ATR * atrS &&
            side * shoulder - side * hd.price >= HEAD_ATR * atrS)
           {
            CPattern p;
            p.kind = (side == 1) ? "inverse head and shoulders" : "head and shoulders"; p.side = side; p.np = 5;
            p.pi[0] = s1.index; p.pp[0] = s1.price; p.pi[1] = P[m1].index; p.pp[1] = P[m1].price; p.pi[2] = hd.index; p.pp[2] = hd.price;
            p.pi[3] = P[m2].index; p.pp[3] = P[m2].price; p.pi[4] = s2.index; p.pp[4] = s2.price;
            p.n1 = P[m1].index; p.p1 = P[m1].price; p.n2 = P[m2].index; p.p2 = P[m2].price;
            p.complete = s2.confirmed; p.broken = -1; p.invalid = hd.price;
            p.height = side * (PatNeck(p, hd.index) - hd.price);
            if(p.height > 0) AddPattern(p, t);
           }
        }
     }
  }


//+------------------------------------------------------------------+
//| Fake breaks (port of tradebot/fakebreak.py)                      |
//+------------------------------------------------------------------+
// First candle after the break at b, within FAKE_BARS, that closes back on the trap side (-1 if none).
// The level is a line (ln), a neckline (pat) or a flat price (flat).
int FakeBack(int b, int t, int side, int src, const Line &ln, const CPattern &pt, double flat)
  {
   for(int j = b + 1; j <= MathMin(b + FAKE_BARS, t); j++)
     {
      double a = A[j];
      if(!Valid(a)) continue;
      double v = (src == 0) ? LineAt(ln, j) : (src == 1 ? PatNeck(pt, j) : flat);
      if(side == 1 ? R[j].close > v + FAKE_BREAK_ATR * a : R[j].close < v - FAKE_BREAK_ATR * a) return j;
     }
   return -1;
  }

void AddFake(string what, int side, double level, int b, int j)
  {
   double ext = (side == 1) ? R[b].low : R[b].high;
   for(int i = b; i <= j; i++) ext = (side == 1) ? MathMin(ext, R[i].low) : MathMax(ext, R[i].high);
   FBreak f;
   f.what = what; f.side = side; f.level = level; f.broke = b; f.back = j; f.extreme = ext;
   int k = ArraySize(FB);
   ArrayResize(FB, k + 1);
   FB[k] = f;
  }

void FakeZones(const Zone &z[], bool htf, int t)
  {
   for(int k = 0; k < ArraySize(z); k++)
      for(int side = 1; side >= -1; side -= 2)
        {
         double edge = (side == 1) ? z[k].lo : z[k].hi;
         for(int i = MathMax(1, t - 2 * FAKE_BARS); i <= t; i++)
           {
            double a = A[i];
            if(!Valid(a)) continue;
            bool beyond = (side == 1) ? R[i].close < edge - FAKE_BREAK_ATR * a : R[i].close > edge + FAKE_BREAK_ATR * a;
            bool cameFrom = (side == 1) ? R[i - 1].close >= edge : R[i - 1].close <= edge;
            if(!(beyond && cameFrom)) continue;
            Line nl; CPattern np;
            nl.i1 = 0; nl.p1 = 0; nl.i2 = 1; nl.p2 = 0; nl.kind = 0; nl.broken = -1; nl.touches = 0; nl.lastTouch = -1; nl.htf = false;
            int j = FakeBack(i, t, side, 2, nl, np, edge);
            if(j >= 0 && t - j < FAKE_BARS)
               AddFake(StringFormat("%s%s zone", htf ? "HTF " : "", side == 1 ? "support" : "resistance"), side, edge, i, j);
            break;
           }
        }
  }

// The fake breaks up to t: every one on a live trendline or neckline, and zone sweeps that came back
// within the last FAKE_BARS candles. Most recent first.
void FindFakeBreaks(int t)
  {
   ArrayResize(FB, 0);
   CPattern np;
   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         if(ln.broken < 0 || ln.broken >= t) continue;
         int side = (ln.kind == 1) ? 1 : -1;            // a rising line broken down, then back up: long
         int j = FakeBack(ln.broken, t, side, 0, ln, np, 0);
         if(j >= 0)
            AddFake(StringFormat("%s%s trendline", ln.htf ? "HTF " : "", ln.kind == 1 ? "rising" : "falling"), side, LineAt(ln, j), ln.broken, j);
        }
     }
   Line nl;
   nl.i1 = 0; nl.p1 = 0; nl.i2 = 1; nl.p2 = 0; nl.kind = 0; nl.broken = -1; nl.touches = 0; nl.lastTouch = -1; nl.htf = false;
   for(int i = 0; i < ArraySize(PAT); i++)
     {
      if(PAT[i].broken < 0 || PAT[i].broken >= t) continue;
      int side = -PAT[i].side;                         // a bullish neckline broken up, then back down: short
      int j = FakeBack(PAT[i].broken, t, side, 1, nl, PAT[i], 0);
      if(j >= 0) AddFake(PAT[i].kind + " neckline", side, PatNeck(PAT[i], j), PAT[i].broken, j);
     }
   FakeZones(ZS, false, t); FakeZones(ZR, false, t); FakeZones(HZS, true, t); FakeZones(HZR, true, t);
   for(int i = 1; i < ArraySize(FB); i++)                // stable sort, most recent return first
      for(int j = i; j > 0 && FB[j].back > FB[j - 1].back; j--)
        { FBreak x = FB[j]; FB[j] = FB[j - 1]; FB[j - 1] = x; }
  }

string FakeLabel(const FBreak &f)
  {
   return StringFormat("fake break %s %s", f.side == 1 ? "below" : "above", f.what);
  }

// A rising and a falling entry-chart trendline, both intact: price is coiling into a triangle.
bool Triangle()
  {
   bool sup = false, res = false;
   for(int i = 0; i < ArraySize(L); i++)
      if(L[i].broken < 0) { if(L[i].kind == 1) sup = true; else res = true; }
   return sup && res;
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

bool Finish(int side, string setup, double stop, string reasons, Signal &s, double targetOverride = EMPTY_VALUE)
  {
   int t = N - 1;
   double c = R[t].close, a = A[t];
   double floorDist = C.minStopATR * a;
   stop = (side == 1) ? MathMin(stop, c - floorDist) : MathMax(stop, c + floorDist);

   bool have = (targetOverride != EMPTY_VALUE);      // a chart pattern brings its measured move
   double target = have ? targetOverride : 0;
   if(have) {}
   else if(side == 1)
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
            double v = LineAt(ln, t);
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
            double v = LineAt(ln, t);
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
   double zLo = 0, zHi = 0, hLo = 0, hHi = 0;     // the zones tagged, for the chart

   if(side == 1)
     {
      for(int i = 0; i < ArraySize(ZS); i++)
         if(lo <= ZS[i].hi + buf && c > ZS[i].lo)
           { AddTag(tags, StringFormat("support zone %s-%s (x%d)", PS(ZS[i].lo), PS(ZS[i].hi), ZS[i].touches), ZS[i].lo); zLo = ZS[i].lo; zHi = ZS[i].hi; break; }
      for(int i = 0; i < ArraySize(HZS); i++)
         if(lo <= HZS[i].hi + buf && c > HZS[i].lo)
           { AddTag(tags, StringFormat("HTF support zone %s-%s (x%d)", PS(HZS[i].lo), PS(HZS[i].hi), HZS[i].touches), HZS[i].lo); hLo = HZS[i].lo; hHi = HZS[i].hi; break; }
     }
   else
     {
      for(int i = 0; i < ArraySize(ZR); i++)
         if(h >= ZR[i].lo - buf && c < ZR[i].hi)
           { AddTag(tags, StringFormat("resistance zone %s-%s (x%d)", PS(ZR[i].lo), PS(ZR[i].hi), ZR[i].touches), ZR[i].hi); zLo = ZR[i].lo; zHi = ZR[i].hi; break; }
      for(int i = 0; i < ArraySize(HZR); i++)
         if(h >= HZR[i].lo - buf && c < HZR[i].hi)
           { AddTag(tags, StringFormat("HTF resistance zone %s-%s (x%d)", PS(HZR[i].lo), PS(HZR[i].hi), HZR[i].touches), HZR[i].hi); hLo = HZR[i].lo; hHi = HZR[i].hi; break; }
     }

   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         double v = LineAt(ln, t);
         string tf = ln.htf ? "HTF " : "";
         bool brokeBefore = ln.broken >= 0 && ln.broken < t;   // a retest comes after the break
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

   if(C.patterns)                                 // patterns as levels: the candle, a neckline retest, a double bottom/top
     {
      if(candleSide == side) AddTag(tags, candleName, side == 1 ? lo : h);
      for(int i = 0; i < ArraySize(PAT); i++)
        {
         if(PAT[i].side != side) continue;
         double v;
         string what;
         if(PAT[i].broken >= 0 && PAT[i].broken < t) { v = PatNeck(PAT[i], t); what = "retest of " + PAT[i].kind + " neckline"; }
         else if(PAT[i].broken < 0 && (PAT[i].kind == "double bottom" || PAT[i].kind == "double top")) { v = PAT[i].invalid; what = PAT[i].kind; }
         else continue;
         if(side == 1 ? (lo <= v + buf && c > v) : (h >= v - buf && c < v)) AddTag(tags, what + " at " + PS(v), v);
        }
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
      if(!Finish(side, "rejection", stop, reasons, s)) return false;
      s.zoneLo = zLo; s.zoneHi = zHi; s.htfLo = hLo; s.htfHi = hHi;
      return true;
     }

   // trendline breakout: a strong candle closes through a trendline (either timeframe) on this very candle
   if(C.breakouts && side * (c - o) >= BREAKOUT_BODY * (h - lo))
     {
      for(int k = 0; k < 2; k++)
        {
         int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
         for(int i = 0; i < cnt; i++)
           {
            Line ln;
            if(k == 0) ln = L[i]; else ln = LH[i];
            if(ln.kind != -side || ln.broken != t) continue;
            double v = LineAt(ln, t);
            double stop = (side == 1) ? MathMin(lo, v) - STOP_BUF_ATR * a : MathMax(h, v) + STOP_BUF_ATR * a;
            string why = StringFormat("close %s %strendline at %s", side == 1 ? "above falling" : "below rising",
                                      ln.htf ? "HTF " : "", PS(v));
            if(!Finish(side, "breakout", stop, why, s)) return false;
            s.zoneLo = 0; s.zoneHi = 0; s.htfLo = 0; s.htfHi = 0;
            return true;
           }
        }
      for(int i = 0; i < ArraySize(PAT) && C.patterns; i++)   // chart pattern: a close through its neckline
        {
         if(PAT[i].side != side || PAT[i].broken != t) continue;
         double v = PatNeck(PAT[i], t);
         double stop = (side == 1) ? MathMin(lo, v) - STOP_BUF_ATR * a : MathMax(h, v) + STOP_BUF_ATR * a;
         if(!Finish(side, "breakout", stop, StringFormat("close %s %s neckline at %s", side == 1 ? "above" : "below", PAT[i].kind, PS(v)),
                    s, PatTarget(PAT[i]))) return false;
         s.zoneLo = 0; s.zoneHi = 0; s.htfLo = 0; s.htfHi = 0;
         return true;
        }
     }
   return false;
  }


//+------------------------------------------------------------------+
//| Zone plans and scenarios (port of tradebot/zoneplan.py)          |
//+------------------------------------------------------------------+
bool Overlaps(const Zone &a, const Zone &b) { return a.lo <= b.hi && b.lo <= a.hi; }

// The nearest obstacle beyond the entry, as for a live setup (Finish).
double PlanTarget(int side, double entry, double stop, int t)
  {
   bool have = false;
   double target = 0;
   if(side == 1)
     {
      for(int i = 0; i < ArraySize(ZR); i++)  if(ZR[i].lo > entry  && (!have || ZR[i].lo < target))  { target = ZR[i].lo;  have = true; }
      for(int i = 0; i < ArraySize(HZR); i++) if(HZR[i].lo > entry && (!have || HZR[i].lo < target)) { target = HZR[i].lo; have = true; }
      for(int i = 0; i < ArraySize(OB); i++)
         if(OB[i].kind == -1 && OBActive(OB[i], t) && OB[i].lo > entry && (!have || OB[i].lo < target)) { target = OB[i].lo; have = true; }
     }
   else
     {
      for(int i = 0; i < ArraySize(ZS); i++)  if(ZS[i].hi < entry  && (!have || ZS[i].hi > target))  { target = ZS[i].hi;  have = true; }
      for(int i = 0; i < ArraySize(HZS); i++) if(HZS[i].hi < entry && (!have || HZS[i].hi > target)) { target = HZS[i].hi; have = true; }
      for(int i = 0; i < ArraySize(OB); i++)
         if(OB[i].kind == 1 && OBActive(OB[i], t) && OB[i].hi < entry && (!have || OB[i].hi > target)) { target = OB[i].hi; have = true; }
     }
   for(int k = 0; k < 2; k++)
     {
      int cnt = (k == 0) ? ArraySize(L) : ArraySize(LH);
      for(int i = 0; i < cnt; i++)
        {
         Line ln;
         if(k == 0) ln = L[i]; else ln = LH[i];
         if(ln.kind != -side || ln.broken >= 0) continue;
         double v = LineAt(ln, t);
         if(side == 1 ? v > entry && (!have || v < target) : v < entry && (!have || v > target)) { target = v; have = true; }
        }
     }
   if(!have) target = entry + side * C.defaultRR * MathAbs(entry - stop);
   return target;
  }

void AddPlan(ZPlan &out[], const Zone &z, int side, bool htf, bool backed, int t, double maxATR)
  {
   double a = A[t], c = R[t].close;
   double entry = (side == 1) ? z.hi : z.lo;
   if(MathAbs(c - entry) > maxATR * a) return;
   double stop = (side == 1) ? z.lo - STOP_BUF_ATR * a : z.hi + STOP_BUF_ATR * a;
   double floorDist = C.minStopATR * a;
   stop = (side == 1) ? MathMin(stop, entry - floorDist) : MathMax(stop, entry + floorDist);
   ZPlan p;
   p.lo = z.lo; p.hi = z.hi; p.side = side; p.htf = htf; p.backed = backed;
   p.entry = entry; p.stop = stop; p.target = PlanTarget(side, entry, stop, t);
   p.rr = MathAbs(p.target - entry) / MathAbs(entry - stop);
   p.why = "";
   if(C.trendFilter && direction == -side)
      p.why = "against the " + BiasName(direction) + "trend";
   else if(p.rr < C.minRR)
      p.why = StringFormat("R:R %.1f (min %g)", p.rr, C.minRR);
   p.tradable = (p.why == "");
   int k = ArraySize(out);
   ArrayResize(out, k + 1);
   out[k] = p;
  }

// Plans for the zones within maxATR ATRs of price, best first: tradable, HTF-backed, nearest.
int PlanZones(ZPlan &out[], double maxATR = 8.0)
  {
   ArrayResize(out, 0);
   int t = N - 1;
   if(!Valid(A[t])) return 0;
   for(int side = 1; side >= -1; side -= 2)
     {
      int nz = (side == 1) ? ArraySize(ZS) : ArraySize(ZR), nh = (side == 1) ? ArraySize(HZS) : ArraySize(HZR);
      for(int i = 0; i < nz; i++)
        {
         Zone z;
         if(side == 1) z = ZS[i]; else z = ZR[i];
         bool backed = false;
         for(int j = 0; j < nh && !backed; j++) { Zone h; if(side == 1) h = HZS[j]; else h = HZR[j]; backed = Overlaps(z, h); }
         AddPlan(out, z, side, false, backed, t, maxATR);
        }
      for(int j = 0; j < nh; j++)
        {
         Zone h;
         if(side == 1) h = HZS[j]; else h = HZR[j];
         bool covered = false;
         for(int i = 0; i < nz && !covered; i++) { Zone z; if(side == 1) z = ZS[i]; else z = ZR[i]; covered = Overlaps(h, z); }
         if(!covered) AddPlan(out, h, side, true, false, t, maxATR);
        }
     }
   int n = ArraySize(out);
   double c = R[t].close;
   for(int i = 1; i < n; i++)                     // stable sort: tradable, HTF-backed, nearest
      for(int j = i; j > 0; j--)
        {
         ZPlan x = out[j], y = out[j - 1];
         int kx = (x.tradable ? 0 : 2) + (x.backed ? 0 : 1), ky = (y.tradable ? 0 : 2) + (y.backed ? 0 : 1);
         if(kx < ky || (kx == ky && MathAbs(c - x.entry) < MathAbs(c - y.entry))) { out[j] = y; out[j - 1] = x; }
         else break;
        }
   return n;
  }

int BestPlan(const ZPlan &plans[])
  {
   for(int i = 0; i < ArraySize(plans); i++) if(plans[i].tradable) return i;
   return -1;
  }

// Whether a chart draws plan i: one of the `limit` tradable zones nearest to price, each at a
// different place (port of zoneplan.shown_plans for the "all" view): a zone closer than
// SAME_PLACE_ATR to one already shown is the same place and is skipped.
bool PlanShown(const ZPlan &plans[], int i, double price, double atr, int limit = 2)
  {
   int n = ArraySize(plans), shown[];
   bool used[];
   ArrayResize(shown, 0);
   ArrayResize(used, n);
   ArrayInitialize(used, false);
   while(ArraySize(shown) < limit)
     {
      int k = -1;
      for(int j = 0; j < n; j++)                    // the nearest tradable plan not looked at yet
         if(plans[j].tradable && !used[j] && (k < 0 || MathAbs(price - plans[j].entry) < MathAbs(price - plans[k].entry))) k = j;
      if(k < 0) break;
      used[k] = true;
      bool same = false;
      for(int q = 0; q < ArraySize(shown); q++)
         if(MathMax(plans[k].lo, plans[shown[q]].lo) - MathMin(plans[k].hi, plans[shown[q]].hi) < SAME_PLACE_ATR * atr) same = true;
      if(same) continue;
      if(k == i) return true;
      int m = ArraySize(shown);
      ArrayResize(shown, m + 1);
      shown[m] = k;
     }
   return false;
  }

string PlanLabel(const ZPlan &p)
  {
   return StringFormat("%s ZONE%s %s-%s", p.side == 1 ? "BUY" : "SELL", p.htf ? " (HTF)" : (p.backed ? " + HTF" : ""), PS(p.lo), PS(p.hi));
  }

// Main and alternative scenario: how the market may evolve from here, in levels to watch.
void Scenarios(const ZPlan &plans[], string htfName, string &mainS, string &altS)
  {
   int t = N - 1, b = BestPlan(plans);
   double price = R[t].close;
   if(direction != 0 && b >= 0)
     {
      bool up = direction == 1;
      ZPlan best = plans[b];
      mainS = StringFormat("%s %strend: expect a %s into the %s zone %s-%s, a rejection there, then a move to %s. "
                           "The plan fails on a close %s %s.", htfName, BiasName(direction), up ? "pullback" : "rally",
                           up ? "buy" : "sell", PS(best.lo), PS(best.hi), PS(best.target), up ? "below" : "above", PS(best.stop));
      int brk = -1, ext = -1;
      for(int i = 0; i < ArraySize(plans); i++)
        {
         ZPlan p = plans[i];
         if(i != b && p.side == direction && (up ? p.entry < best.stop : p.entry > best.stop) &&
            (brk < 0 || MathAbs(price - p.entry) < MathAbs(price - plans[brk].entry))) brk = i;
         if(p.side == -direction && (up ? p.lo > price : p.hi < price) &&
            (ext < 0 || MathAbs(price - p.entry) < MathAbs(price - plans[ext].entry))) ext = i;
        }
      altS = StringFormat("A close %s %s breaks the %strend structure: stand aside", up ? "below" : "above", PS(best.stop), BiasName(direction));
      altS += (brk >= 0) ? StringFormat("; the next %s is %s-%s.", up ? "support" : "resistance", PS(plans[brk].lo), PS(plans[brk].hi)) : ".";
      if(ext >= 0) altS += StringFormat(" %s %s the move can extend.", up ? "Above" : "Below", PS(up ? plans[ext].hi : plans[ext].lo));
      return;
     }
   if(direction != 0)
     {
      mainS = StringFormat("%s %strend, but no %s zone near price pays enough: wait for a new swing to form a zone.",
                           htfName, BiasName(direction), direction == 1 ? "buy" : "sell");
      altS = "Do not trade against the trend from the zones on the other side.";
      return;
     }
   int s = -1, r = -1;
   for(int i = 0; i < ArraySize(plans); i++)
     {
      if(plans[i].side == 1 && (s < 0 || MathAbs(price - plans[i].entry) < MathAbs(price - plans[s].entry))) s = i;
      if(plans[i].side == -1 && (r < 0 || MathAbs(price - plans[i].entry) < MathAbs(price - plans[r].entry))) r = i;
     }
   mainS = "No clear trend: range trading only, from " + (s >= 0 ? "support " + PS(plans[s].lo) + "-" + PS(plans[s].hi) : "-")
           + " and " + (r >= 0 ? "resistance " + PS(plans[r].lo) + "-" + PS(plans[r].hi) : "-") + ", with a confirmed rejection.";
   altS = "A close outside the range sets the next trend" + (r >= 0 ? ": above " + PS(plans[r].hi) + " favours buys" : "")
          + (s >= 0 ? ", below " + PS(plans[s].lo) + " favours sells" : "") + ".";
  }

#endif
