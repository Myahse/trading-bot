//+------------------------------------------------------------------+
//|                                                      TradeBot.cs |
//|  cTrader (cAlgo) port of the MT5 Expert Advisor                  |
//|  (mql5/TradeBot.mq5 + mql5/TradeBotCore.mqh): the same zones,    |
//|  trendlines, order blocks, patterns, setups and money management.|
//|  One file: paste it into a new cBot in cTrader and Build.        |
//+------------------------------------------------------------------+
using System;
using System.Collections.Generic;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum Preset  { Scalp = 0, Swing = 1, Custom = 2 }   // Scalp: M5 chart, H1 structure; Swing: H4 chart, D1 structure
    public enum Confirm { Break = 0, Close = 1, None = 2 }     // break of the signal candle / a close beyond it / next open
    public enum Trail   { None = 0, Atr = 1, Swing = 2 }       // no trailing / ATR behind the best price / behind each new swing

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class TradeBot : Robot
    {
        //--- inputs (same names and defaults as the EA)
        [Parameter("Preset (Custom uses every value below)", Group = "Preset", DefaultValue = Preset.Scalp)]
        public Preset InpPreset { get; set; }

        [Parameter("Higher timeframe", Group = "Strategy (Custom preset)", DefaultValue = "Hour")]
        public TimeFrame InpHTF { get; set; }
        [Parameter("Swing size: bars each side", Group = "Strategy (Custom preset)", DefaultValue = 3, MinValue = 1)]
        public int InpPivot { get; set; }
        [Parameter("Swing size on the higher timeframe", Group = "Strategy (Custom preset)", DefaultValue = 3, MinValue = 1)]
        public int InpHTFPivot { get; set; }
        [Parameter("Levels that must be tagged together", Group = "Strategy (Custom preset)", DefaultValue = 2, MinValue = 1)]
        public int InpMinConfluence { get; set; }
        [Parameter("Minimum reward:risk", Group = "Strategy (Custom preset)", DefaultValue = 1.5)]
        public double InpMinRR { get; set; }
        [Parameter("Target in R when nothing is in the way", Group = "Strategy (Custom preset)", DefaultValue = 1.5)]
        public double InpDefaultRR { get; set; }
        [Parameter("Entry confirmation", Group = "Strategy (Custom preset)", DefaultValue = Confirm.Break)]
        public Confirm InpConfirm { get; set; }
        [Parameter("Candles the confirmation may take", Group = "Strategy (Custom preset)", DefaultValue = 3, MinValue = 1)]
        public int InpConfirmBars { get; set; }
        [Parameter("Only trade with the higher-timeframe trend", Group = "Strategy (Custom preset)", DefaultValue = true)]
        public bool InpTrendFilter { get; set; }
        [Parameter("Trade trendline breakouts", Group = "Strategy (Custom preset)", DefaultValue = true)]
        public bool InpBreakouts { get; set; }
        [Parameter("Bars a broken trendline stays valid for its retest", Group = "Strategy (Custom preset)", DefaultValue = 12)]
        public int InpRetestBars { get; set; }
        [Parameter("Bars to stand aside after a losing trade", Group = "Strategy (Custom preset)", DefaultValue = 6)]
        public int InpCooldownBars { get; set; }
        [Parameter("Minimum stop distance in ATR", Group = "Strategy (Custom preset)", DefaultValue = 1.0)]
        public double InpMinStopATR { get; set; }
        [Parameter("Bars an order block stays valid", Group = "Strategy (Custom preset)", DefaultValue = 100)]
        public int InpOBMaxAge { get; set; }
        [Parameter("Recent swings used to build zones", Group = "Strategy (Custom preset)", DefaultValue = 30)]
        public int InpZoneLookback { get; set; }
        [Parameter("Chart: only the best zone", Group = "Strategy (Custom preset)", DefaultValue = false)]
        public bool InpZoneBest { get; set; }
        [Parameter("Candlestick/chart patterns count as levels", Group = "Strategy (Custom preset)", DefaultValue = true)]
        public bool InpPatterns { get; set; }

        [Parameter("Risk per trade, % of balance", Group = "Money management (Custom preset)", DefaultValue = 0.5)]
        public double InpRiskPct { get; set; }
        [Parameter("Skip if even the minimum volume risks more than this %", Group = "Money management (Custom preset)", DefaultValue = 5.0)]
        public double InpMinLotMaxRisk { get; set; }
        [Parameter("Skip when the spread is more than this % of the risk (0 = off)", Group = "Money management (Custom preset)", DefaultValue = 20.0)]
        public double InpMaxSpreadRisk { get; set; }
        [Parameter("Move stop to entry at this many R (0 = off)", Group = "Money management (Custom preset)", DefaultValue = 1.0)]
        public double InpBreakevenR { get; set; }
        [Parameter("Partial profit at this many R (0 = off)", Group = "Money management (Custom preset)", DefaultValue = 1.0)]
        public double InpPartialR { get; set; }
        [Parameter("% of the position closed at the partial", Group = "Money management (Custom preset)", DefaultValue = 50.0)]
        public double InpPartialPct { get; set; }
        [Parameter("Trailing stop", Group = "Money management (Custom preset)", DefaultValue = Trail.Atr)]
        public Trail InpTrail { get; set; }
        [Parameter("Start trailing at this many R", Group = "Money management (Custom preset)", DefaultValue = 1.0)]
        public double InpTrailStartR { get; set; }
        [Parameter("ATR multiple for the ATR trailing stop", Group = "Money management (Custom preset)", DefaultValue = 1.5)]
        public double InpTrailATR { get; set; }
        [Parameter("No new trades after losing this % today (0 = off)", Group = "Money management (Custom preset)", DefaultValue = 3.0)]
        public double InpMaxDailyLossPct { get; set; }
        [Parameter("Max trades per day (0 = off)", Group = "Money management (Custom preset)", DefaultValue = 8)]
        public int InpMaxTradesDay { get; set; }

        [Parameter("Allow the minimum volume up to this % risk on any preset (0 = preset's own limit)", Group = "Small accounts", DefaultValue = 0.0)]
        public double InpMinLotRiskCap { get; set; }

        [Parameter("Allow trading a LIVE account", Group = "Safety", DefaultValue = false)]
        public bool InpAllowReal { get; set; }
        [Parameter("Label (marks this bot's positions)", Group = "Safety", DefaultValue = "TradeBot")]
        public string InpLabel { get; set; }

        [Parameter("Chart candles analysed", Group = "History", DefaultValue = 5000, MinValue = 200)]
        public int InpHistoryBars { get; set; }
        [Parameter("Higher-timeframe candles", Group = "History", DefaultValue = 1000, MinValue = 100)]
        public int InpHTFHistoryBars { get; set; }

        [Parameter("Draw zones, trendlines and the setup", Group = "Display", DefaultValue = true)]
        public bool InpDraw { get; set; }
        [Parameter("Show the panel", Group = "Display", DefaultValue = true)]
        public bool InpPanel { get; set; }

        //--- fixed rules, as in the Python bot and the EA
        const int    ATR_PERIOD = 14;
        const double ZONE_TOL_ATR = 0.6, HTF_ZONE_TOL_ATR = 0.3, TOUCH_BUF_ATR = 0.25, STOP_BUF_ATR = 0.3;
        const double OB_DISP_ATR = 1.0;
        const int    OB_LOOKBACK = 10;
        const double BREAKOUT_BODY = 0.5;
        const int    LINE_CANDIDATES = 8;
        const double LINE_TOUCH_ATR = 0.25, LINE_WICK_ATR = 0.1, LINE_BREAK_ATR = 0.1, LINE_MAX_SLOPE_ATR = 0.5;
        const string PFX = "TB_";
        const double PATTERN_TOL_ATR = 0.5, PATTERN_DEPTH_ATR = 1.5, HEAD_ATR = 0.5, PAT_BREAK_ATR = 0.1, PAT_FAIL_ATR = 0.3;
        const double SAME_PLACE_ATR = 0.5;

        //--- data types
        class Config
        {
            public TimeFrame htf;
            public int pivot, htfPivot, minConfluence, confirmBars, retest, cooldown, obMaxAge, zoneLookback, maxTradesDay;
            public Confirm confirm;
            public Trail trail;
            public double minRR, defaultRR, minStopATR, riskPct, minLotMaxRisk, beR, partialR, partialPct, trailStartR, trailATR, maxDailyLoss;
            public bool trendFilter, breakouts, zoneBest, patterns;
        }
        struct Rate   { public DateTime Time; public double Open, High, Low, Close; }
        struct Pivot  { public int Index; public double Price; public int Kind; public int Confirmed; }   // kind +1 swing high, -1 swing low
        struct Zone   { public double Lo, Hi; public int Touches; }
        struct Line   { public int I1; public double P1; public int I2; public double P2; public int Kind, Broken, Touches, LastTouch; public bool Htf; }
                                                                                                         // kind +1 rising support, -1 falling resistance
        struct OBlock { public int Kind, Index; public double Lo, Hi; public int Created, Invalid; }      // kind +1 bullish
        struct Tag    { public string Reason; public double Level; }
        class Signal
        {
            public int Side; public string Setup = ""; public double Entry, Stop, Target, Trigger, RR; public string Reasons = "";
            public double ZoneLo, ZoneHi, HtfLo, HtfHi;
        }
        // A chart pattern: its swings, its neckline and whether it broke
        class CPattern
        {
            public string Kind; public int Side, Np; public int[] Pi = new int[5]; public double[] Pp = new double[5];
            public int N1; public double P1; public int N2; public double P2; public int Complete, Broken; public double Invalid, Height;
        }
        // A zone the bot could trade from, with the plan if price came back to it
        struct ZPlan  { public double Lo, Hi; public int Side; public bool Htf, Backed; public double Entry, Stop, Target, RR; public bool Tradable; public string Why; }

        //--- analysis state
        Config C = new Config();
        Rate[] R = new Rate[0];  int N; double[] A = new double[0];  Pivot[] P = new Pivot[0];  int NP;    // entry timeframe
        Rate[] RH = new Rate[0]; int NH; double[] AH = new double[0]; Pivot[] PH = new Pivot[0]; int NPH;  // higher timeframe
        List<Zone> ZS = new List<Zone>(), ZR = new List<Zone>(), HZS = new List<Zone>(), HZR = new List<Zone>();   // nearest first
        List<Line> L = new List<Line>(), LH = new List<Line>();
        List<OBlock> OB = new List<OBlock>();
        List<CPattern> PAT = new List<CPattern>();
        int biasE, biasH, direction;
        string candleName = ""; int candleSide;

        //--- bot state
        Bars htfBars;
        int htfSeconds;                                        // the higher timeframe's length, 0 = none above the chart's
        bool tradingPermitted;
        string lastNote = "", tfWarning = "";
        bool armed; Signal armedSig; int armedBarsLeft;
        readonly Dictionary<long, double> riskOf = new Dictionary<long, double>();   // 1R per position, kept after the stop moves
        readonly HashSet<long> partDone = new HashSet<long>(), historyChecked = new HashSet<long>();
        DateTime lastTakenBar = DateTime.MinValue;

        //+------------------------------------------------------------------+
        //| Small helpers                                                    |
        //+------------------------------------------------------------------+
        static bool Valid(double v) { return !double.IsNaN(v) && !double.IsInfinity(v) && v > 0; }
        int digits = 5;                                        // the symbol's price digits, set in OnStart
        double Px(double p) { return Math.Round(p, digits); }
        string PS(double p) { return p.ToString("F" + digits); }
        static string BiasName(int b) { return b > 0 ? "up" : (b < 0 ? "down" : "neutral"); }
        static double LineAt(Line ln, double x) { return ln.P1 + (ln.P2 - ln.P1) / (ln.I2 - ln.I1) * (x - ln.I1); }
        bool Drawing { get { return RunningMode == RunningMode.RealTime || RunningMode == RunningMode.VisualBacktesting; } }

        static readonly Dictionary<TimeFrame, int> Seconds = new Dictionary<TimeFrame, int>
        {
            { TimeFrame.Minute, 60 }, { TimeFrame.Minute2, 120 }, { TimeFrame.Minute3, 180 }, { TimeFrame.Minute4, 240 },
            { TimeFrame.Minute5, 300 }, { TimeFrame.Minute10, 600 }, { TimeFrame.Minute15, 900 }, { TimeFrame.Minute20, 1200 },
            { TimeFrame.Minute30, 1800 }, { TimeFrame.Minute45, 2700 }, { TimeFrame.Hour, 3600 }, { TimeFrame.Hour2, 7200 },
            { TimeFrame.Hour3, 10800 }, { TimeFrame.Hour4, 14400 }, { TimeFrame.Hour6, 21600 }, { TimeFrame.Hour8, 28800 },
            { TimeFrame.Hour12, 43200 }, { TimeFrame.Daily, 86400 }, { TimeFrame.Day2, 172800 }, { TimeFrame.Day3, 259200 },
            { TimeFrame.Weekly, 604800 }
        };
        static int PeriodSeconds(TimeFrame tf) { int s; return Seconds.TryGetValue(tf, out s) ? s : 0; }

        //+------------------------------------------------------------------+
        //| Presets (same values as the EA and MODES in tradebot/strategy.py)|
        //+------------------------------------------------------------------+
        void LoadConfig()
        {
            C.htf = InpHTF; C.pivot = InpPivot; C.htfPivot = InpHTFPivot; C.minConfluence = InpMinConfluence;
            C.confirm = InpConfirm; C.confirmBars = InpConfirmBars; C.retest = InpRetestBars; C.cooldown = InpCooldownBars;
            C.obMaxAge = InpOBMaxAge; C.zoneLookback = InpZoneLookback; C.minRR = InpMinRR; C.defaultRR = InpDefaultRR;
            C.minStopATR = InpMinStopATR; C.trendFilter = InpTrendFilter; C.breakouts = InpBreakouts;
            C.riskPct = InpRiskPct; C.minLotMaxRisk = InpMinLotMaxRisk; C.beR = InpBreakevenR; C.partialR = InpPartialR;
            C.partialPct = InpPartialPct; C.trail = InpTrail; C.trailStartR = InpTrailStartR; C.trailATR = InpTrailATR;
            C.maxDailyLoss = InpMaxDailyLossPct; C.maxTradesDay = InpMaxTradesDay; C.zoneBest = InpZoneBest; C.patterns = InpPatterns;

            if (InpPreset == Preset.Scalp)
            {
                C.htf = TimeFrame.Hour; C.pivot = 3; C.htfPivot = 3; C.minConfluence = 2; C.confirm = Confirm.Break; C.confirmBars = 3;
                C.retest = 12; C.cooldown = 6; C.obMaxAge = 100; C.zoneLookback = 30; C.minRR = 1.5; C.defaultRR = 1.5;
                C.minStopATR = 1.0; C.trendFilter = true; C.breakouts = true;
                C.riskPct = 0.5; C.minLotMaxRisk = 5.0; C.beR = 1.0; C.partialR = 1.0; C.partialPct = 50.0;
                C.trail = Trail.Atr; C.trailStartR = 1.0; C.trailATR = 1.5; C.maxDailyLoss = 3.0; C.maxTradesDay = 8;
                C.zoneBest = false; C.patterns = false;   // shown, not counted: they made 5m entries worse
            }
            else if (InpPreset == Preset.Swing)
            {
                C.htf = TimeFrame.Daily; C.pivot = 5; C.htfPivot = 3; C.minConfluence = 2; C.confirm = Confirm.Break; C.confirmBars = 2;
                C.retest = 15; C.cooldown = 3; C.obMaxAge = 150; C.zoneLookback = 40; C.minRR = 2.0; C.defaultRR = 2.5;
                C.minStopATR = 0.0; C.trendFilter = true; C.breakouts = true;
                C.riskPct = 1.0; C.minLotMaxRisk = 5.0; C.beR = 1.0; C.partialR = 1.5; C.partialPct = 50.0;
                C.trail = Trail.Swing; C.trailStartR = 1.5; C.trailATR = 2.0; C.maxDailyLoss = 0.0; C.maxTradesDay = 0;
                C.zoneBest = true; C.patterns = true;
            }
            if (InpMinLotRiskCap > 0) C.minLotMaxRisk = InpMinLotRiskCap;   // small accounts: the minimum volume may risk more
        }

        //+------------------------------------------------------------------+
        //| Market structure                                                 |
        //+------------------------------------------------------------------+
        // ATR with Wilder smoothing, computed exactly as in the EA and the Python bot. Valid from bar ATR_PERIOD-1.
        static double[] CalcATR(Rate[] r, int n)
        {
            var atr = new double[n];
            double alpha = 1.0 / ATR_PERIOD, keep = 1.0 - alpha, w = 0.0;
            for (int i = 0; i < n; i++)
            {
                double tr = r[i].High - r[i].Low;
                if (i > 0)
                    tr = Math.Max(tr, Math.Max(Math.Abs(r[i].High - r[i - 1].Close), Math.Abs(r[i].Low - r[i - 1].Close)));
                if (i == 0) w = tr;
                else if (w != tr) w = (keep * w + alpha * tr) / (keep + alpha);
                atr[i] = (i >= ATR_PERIOD - 1) ? w : double.NaN;
            }
            return atr;
        }

        // Fractal swings: a high above the `left` bars before it and not below the `right` bars after it.
        static Pivot[] FindPivots(Rate[] r, int n, int left, int right)
        {
            var o = new List<Pivot>();
            for (int i = left; i < n - right; i++)
            {
                bool isHigh = true, isLow = true;
                for (int j = i - left; j < i; j++)
                {
                    if (r[j].High >= r[i].High) isHigh = false;
                    if (r[j].Low <= r[i].Low) isLow = false;
                }
                for (int j = i + 1; j <= i + right; j++)
                {
                    if (r[j].High > r[i].High) isHigh = false;
                    if (r[j].Low < r[i].Low) isLow = false;
                }
                if (isHigh) o.Add(new Pivot { Index = i, Price = r[i].High, Kind = 1, Confirmed = i + right });
                if (isLow)  o.Add(new Pivot { Index = i, Price = r[i].Low, Kind = -1, Confirmed = i + right });
            }
            return o.ToArray();
        }

        // Higher highs + higher lows = up (+1), lower highs + lower lows = down (-1).
        static int Bias(Pivot[] piv, int np)
        {
            double h2 = 0, h1 = 0, l2 = 0, l1 = 0;
            int nh = 0, nl = 0;
            for (int i = np - 1; i >= 0 && (nh < 2 || nl < 2); i--)
            {
                if (piv[i].Kind == 1 && nh < 2)  { if (nh == 0) h2 = piv[i].Price; else h1 = piv[i].Price; nh++; }
                if (piv[i].Kind == -1 && nl < 2) { if (nl == 0) l2 = piv[i].Price; else l1 = piv[i].Price; nl++; }
            }
            if (nh < 2 || nl < 2) return 0;
            if (h2 > h1 && l2 > l1) return 1;
            if (h2 < h1 && l2 < l1) return -1;
            return 0;
        }

        // Zones below price are support (nearest first), above are resistance (nearest first). Stable sorts, as in the EA.
        static void SplitZones(List<Zone> z, double c, List<Zone> sup, List<Zone> res)
        {
            sup.Clear(); res.Clear();
            foreach (var x in z) { if ((x.Lo + x.Hi) / 2.0 < c) sup.Add(x); else res.Add(x); }
            for (int i = 1; i < sup.Count; i++)   // highest support first
                for (int j = i; j > 0 && (sup[j].Lo + sup[j].Hi) > (sup[j - 1].Lo + sup[j - 1].Hi); j--)
                { var t = sup[j]; sup[j] = sup[j - 1]; sup[j - 1] = t; }
            for (int i = 1; i < res.Count; i++)   // lowest resistance first
                for (int j = i; j > 0 && (res[j].Lo + res[j].Hi) < (res[j - 1].Lo + res[j - 1].Hi); j--)
                { var t = res[j]; res[j] = res[j - 1]; res[j - 1] = t; }
        }

        // Zones with a memory: born from a swing with its width fixed by the ATR at that swing; later swings at
        // the same price add touches and may widen it up to that width, never move it.
        static List<Zone> BuildLevels(Pivot[] piv, int np, double[] atr, double tolATR, int maxLevels, int minTouches)
        {
            var lo = new List<double>(); var hi = new List<double>(); var cap = new List<double>();
            var touches = new List<int>(); var last = new List<int>();
            for (int i = 0; i < np; i++)
            {
                double a = atr[piv[i].Index];
                if (!Valid(a)) continue;
                double p = piv[i].Price, tol = tolATR * a;
                int best = -1;
                double bestD = 0;
                for (int k = 0; k < lo.Count; k++)
                {
                    double d = (lo[k] <= p && p <= hi[k]) ? 0.0 : Math.Min(Math.Abs(p - lo[k]), Math.Abs(p - hi[k]));
                    if (d <= cap[k] / 2 && (best < 0 || d < bestD)) { best = k; bestD = d; }
                }
                if (best >= 0)
                {
                    double nlo = Math.Min(lo[best], p), nhi = Math.Max(hi[best], p);
                    if (nhi - nlo <= cap[best]) { lo[best] = nlo; hi[best] = nhi; }
                    touches[best]++;
                    last[best] = piv[i].Index;
                }
                else
                {
                    lo.Add(p - tol / 4); hi.Add(p + tol / 4); cap.Add(tol); touches.Add(1); last.Add(piv[i].Index);
                    if (lo.Count > maxLevels)                   // forget the zone touched longest ago
                    {
                        int old = 0;
                        for (int k = 1; k < lo.Count; k++) if (last[k] < last[old]) old = k;
                        lo.RemoveAt(old); hi.RemoveAt(old); cap.RemoveAt(old); touches.RemoveAt(old); last.RemoveAt(old);
                    }
                }
            }
            var o = new List<Zone>();
            for (int k = 0; k < lo.Count; k++)
                if (touches[k] >= minTouches) o.Add(new Zone { Lo = lo[k], Hi = hi[k], Touches = touches[k] });
            return o;
        }

        static bool Better(int t1, int l1, int s1, int t2, int l2, int s2)
        {
            if (t1 != t2) return t1 > t2;
            if (l1 != l2) return l1 > l2;
            return s1 > s2;
        }

        // Trendlines drawn the way a trader would: every pair of the last 8 swings is a candidate; it must not cut
        // through any candle between its anchors, nor be too steep; ranked by touches, last touch, length.
        // Appends the best intact and the best just-broken line per side (an intact near-twin of a broken line is dropped).
        static void FindLines(Rate[] r, int n, Pivot[] piv, int np, double[] atr, int t, int retest, int minSpan, bool htf, List<Line> o)
        {
            for (int side = 0; side < 2; side++)
            {
                int kind = (side == 0) ? 1 : -1;               // +1 support through lows, -1 resistance through highs
                int pk = (kind == 1) ? -1 : 1;
                var sw = new List<int>();
                for (int i = 0; i < np; i++) if (piv[i].Kind == pk) sw.Add(i);
                int ns = sw.Count, from = Math.Max(0, ns - LINE_CANDIDATES);
                var have = new bool[2];                        // [0] intact, [1] broken
                var best = new Line[2];
                var bt = new int[2]; var bl = new int[2]; var bs = new int[2];
                for (int a = from; a < ns; a++)
                    for (int b = a + 1; b < ns; b++)
                    {
                        int ia = piv[sw[a]].Index, ib = piv[sw[b]].Index;
                        double pa = piv[sw[a]].Price, pb = piv[sw[b]].Price;
                        if (ib - ia < minSpan) continue;
                        double slope = (pb - pa) / (ib - ia);
                        if (kind == 1 ? slope <= 0 : slope >= 0) continue;
                        double atrB = atr[ib];
                        if (!Valid(atrB) || Math.Abs(slope) > LINE_MAX_SLOPE_ATR * atrB) continue;
                        bool cut = false;
                        double tol = LINE_WICK_ATR * atrB;
                        for (int i = ia; i <= ib && !cut; i++)
                        {
                            double v = pa + slope * (i - ia);
                            if (kind == 1 ? r[i].Low < v - tol : r[i].High > v + tol) cut = true;
                        }
                        if (cut) continue;
                        int brk = -1;
                        for (int j = ib + 1; j <= t && brk < 0; j++)
                        {
                            double v = pa + slope * (j - ia);
                            double aj = Valid(atr[j]) ? atr[j] : atrB;
                            if (kind == 1 ? r[j].Close < v - LINE_BREAK_ATR * aj : r[j].Close > v + LINE_BREAK_ATR * aj) brk = j;
                        }
                        if (brk >= 0 && t - brk > retest) continue;
                        int touches = 0, last = -1;
                        for (int q = 0; q < ns; q++)
                        {
                            int si = piv[sw[q]].Index;
                            if (si < ia) continue;
                            if (brk >= 0 && si >= brk) continue;   // nothing counts after a break
                            if (!Valid(atr[si])) continue;
                            if (Math.Abs(piv[sw[q]].Price - (pa + slope * (si - ia))) <= LINE_TOUCH_ATR * atr[si]) { touches++; last = si; }
                        }
                        if (touches < 2) continue;
                        int slot = (brk >= 0) ? 1 : 0;
                        if (!have[slot] || Better(touches, last, ib - ia, bt[slot], bl[slot], bs[slot]))
                        {
                            have[slot] = true;
                            bt[slot] = touches; bl[slot] = last; bs[slot] = ib - ia;
                            best[slot] = new Line { I1 = ia, P1 = pa, I2 = ib, P2 = pb, Kind = kind, Broken = brk, Touches = touches, LastTouch = last, Htf = htf };
                        }
                    }
                if (have[0] && have[1] && Valid(atr[t]) && Math.Abs(LineAt(best[0], t) - LineAt(best[1], t)) <= LINE_TOUCH_ATR * atr[t])
                    have[0] = false;                           // its near-twin just broke, so in practice it broke too
                for (int s = 0; s < 2; s++) if (have[s]) o.Add(best[s]);
            }
        }

        // Order blocks: the last opposite candle before an impulse (>= 1 ATR) that closes beyond the last swing.
        static int Origin(Rate[] r, int swingIndex, int t, bool wantBearish)
        {
            for (int i = t - 1; i >= Math.Max(swingIndex, t - OB_LOOKBACK); i--)
                if (wantBearish ? r[i].Close < r[i].Open : r[i].Close > r[i].Open) return i;
            return -1;
        }

        static int FirstCloseBeyond(Rate[] r, int n, int t, double level, bool below)
        {
            for (int j = t + 1; j < n; j++) if (below ? r[j].Close < level : r[j].Close > level) return j;
            return -1;
        }

        static List<OBlock> FindOrderBlocks(Rate[] r, int n, Pivot[] piv, int np, double[] atr)
        {
            var o = new List<OBlock>();
            int p = 0, lastHigh = -1, lastLow = -1;
            for (int t = 0; t < n; t++)
            {
                while (p < np && piv[p].Confirmed <= t) { if (piv[p].Kind == 1) lastHigh = p; else lastLow = p; p++; }
                if (!Valid(atr[t])) continue;
                if (lastHigh >= 0 && r[t].Close > piv[lastHigh].Price)
                {
                    int ob = Origin(r, piv[lastHigh].Index, t, true);
                    if (ob >= 0)
                    {
                        double mx = r[ob + 1].High;
                        for (int j = ob + 1; j <= t; j++) mx = Math.Max(mx, r[j].High);
                        if (mx - r[ob].High >= OB_DISP_ATR * atr[t])
                            o.Add(new OBlock { Kind = 1, Index = ob, Lo = r[ob].Low, Hi = r[ob].High, Created = t, Invalid = FirstCloseBeyond(r, n, t, r[ob].Low, true) });
                    }
                    lastHigh = -1;                             // each swing is broken once
                }
                if (lastLow >= 0 && r[t].Close < piv[lastLow].Price)
                {
                    int ob = Origin(r, piv[lastLow].Index, t, false);
                    if (ob >= 0)
                    {
                        double mn = r[ob + 1].Low;
                        for (int j = ob + 1; j <= t; j++) mn = Math.Min(mn, r[j].Low);
                        if (r[ob].Low - mn >= OB_DISP_ATR * atr[t])
                            o.Add(new OBlock { Kind = -1, Index = ob, Lo = r[ob].Low, Hi = r[ob].High, Created = t, Invalid = FirstCloseBeyond(r, n, t, r[ob].High, false) });
                    }
                    lastLow = -1;
                }
            }
            return o;
        }

        bool OBActive(OBlock b, int t) { return b.Created <= t && (b.Invalid < 0 || b.Invalid > t) && t - b.Created <= C.obMaxAge; }

        // First chart candle at or after `when` (N if none).
        int FirstBarFrom(DateTime when)
        {
            int lo = 0, hi = N;
            while (lo < hi) { int mid = (lo + hi) / 2; if (R[mid].Time < when) lo = mid + 1; else hi = mid; }
            return lo;
        }

        // Higher-timeframe trendlines: drawn through the HTF swings placed on the chart candle that made each
        // extreme, checked against the chart's candles and measured with the HTF ATR.
        void FindHTFLines(int t)
        {
            var htfSpan = TimeSpan.FromSeconds(htfSeconds);
            var AE = new double[N];                            // HTF ATR known at each chart candle
            int k = 0;
            for (int i = 0; i < N; i++)
            {
                while (k < NH && RH[k].Time + htfSpan <= R[i].Time) k++;
                AE[i] = (k > 0) ? AH[k - 1] : double.NaN;
            }
            int groups = 0, lastStart = 0, g = -1, prevKey = 0;   // chart candles per HTF candle
            for (int i = 0; i < N; i++)
            {
                while (g + 1 < NH && RH[g + 1].Time <= R[i].Time) g++;
                int key = (g >= 0 && R[i].Time < RH[g].Time + htfSpan) ? g : -2 - g;
                if (i == 0 || key != prevKey) { groups++; lastStart = i; }
                prevKey = key;
            }
            double perCandle = (groups > 1) ? (double)lastStart / (groups - 1) : 1.0;
            var PE = new List<Pivot>();                        // HTF swings on the chart's candle axis
            for (int p = 0; p < NPH; p++)
            {
                DateTime ht = RH[PH[p].Index].Time;
                int first = FirstBarFrom(ht), at = -1;
                for (int i = first; i < N && R[i].Time < ht + htfSpan; i++)
                    if (at < 0 || (PH[p].Kind == 1 ? R[i].High > R[at].High : R[i].Low < R[at].Low)) at = i;
                if (at < 0) continue;                          // older than the chart candles loaded
                PE.Add(new Pivot { Index = at, Price = PH[p].Price, Kind = PH[p].Kind, Confirmed = FirstBarFrom(RH[PH[p].Confirmed].Time + htfSpan) });
            }
            var pe = PE.ToArray();
            FindLines(R, N, pe, pe.Length, AE, t, (int)(C.retest * perCandle), (int)(4 * C.htfPivot * perCandle), true, LH);
        }

        //+------------------------------------------------------------------+
        //| Load candles and analyse (closed candles only)                   |
        //+------------------------------------------------------------------+
        static Rate[] Copy(Bars b, int lastIndex, int count)
        {
            int first = Math.Max(0, lastIndex - count + 1), n = lastIndex - first + 1;
            if (n <= 0) return new Rate[0];
            var r = new Rate[n];
            for (int i = 0; i < n; i++)
            {
                var x = b[first + i];
                r[i] = new Rate { Time = x.OpenTime, Open = x.Open, High = x.High, Low = x.Low, Close = x.Close };
            }
            return r;
        }

        // Analyse as of the last closed candle. Only higher-timeframe candles that had closed by then are used:
        // as in the Python bot, a higher-timeframe candle counts from the first chart candle after it closed.
        bool Analyse()
        {
            var r = Copy(Bars, Bars.Count - 2, Math.Max(InpHistoryBars, 200));
            var rh = new Rate[0];
            if (htfBars != null && r.Length > 0)
            {
                DateTime closedBy = r[r.Length - 1].Time - TimeSpan.FromSeconds(htfSeconds);
                int last = htfBars.Count - 1;
                while (last >= 0 && htfBars[last].OpenTime > closedBy) last--;
                rh = Copy(htfBars, last, Math.Max(InpHTFHistoryBars, 100));
            }
            return AnalyseRates(r, rh);
        }

        // The analysis itself, on chart candles r (the last one just closed) and the closed higher-timeframe candles rh.
        bool AnalyseRates(Rate[] r, Rate[] rh)
        {
            R = r;
            N = R.Length;
            if (N < 100) return false;
            A = CalcATR(R, N);
            P = FindPivots(R, N, C.pivot, C.pivot); NP = P.Length;
            int t = N - 1;
            double c = R[t].Close;

            SplitZones(BuildLevels(P, NP, A, ZONE_TOL_ATR, C.zoneLookback, 2), c, ZS, ZR);
            L.Clear();
            FindLines(R, N, P, NP, A, t, C.retest, 2 * (C.pivot + C.pivot), false, L);
            OB = FindOrderBlocks(R, N, P, NP, A);
            biasE = Bias(P, NP);

            NH = 0; NPH = 0; HZS.Clear(); HZR.Clear(); LH.Clear(); biasH = 0;
            if (htfSeconds > 0)
            {
                RH = rh;
                NH = RH.Length;
                if (NH >= 2 * ATR_PERIOD)
                {
                    AH = CalcATR(RH, NH);
                    PH = FindPivots(RH, NH, C.htfPivot, C.htfPivot); NPH = PH.Length;
                    biasH = Bias(PH, NPH);
                    // A zone's width comes from the HTF ATR known while its swing formed (the previous closed HTF candle).
                    var known = new double[NH];
                    known[0] = double.NaN;
                    for (int i = 1; i < NH; i++) known[i] = AH[i - 1];
                    SplitZones(BuildLevels(PH, NPH, known, HTF_ZONE_TOL_ATR, 200, 2), c, HZS, HZR);   // all history: old levels count
                    FindHTFLines(t);
                }
                else NH = 0;
            }
            direction = (NH > 0) ? biasH : biasE;
            candleName = ""; candleSide = 0; PAT.Clear();
            if (Valid(A[t])) { CandlePattern(t); FindChartPatterns(t); }
            return Valid(A[t]);
        }

        //+------------------------------------------------------------------+
        //| Patterns                                                         |
        //+------------------------------------------------------------------+
        // Candlestick pattern on candle t: engulfing, morning/evening star, hammer/shooting star.
        void CandlePattern(int t)
        {
            candleName = ""; candleSide = 0;
            if (t < 2) return;
            double o = R[t].Open, h = R[t].High, l = R[t].Low, c = R[t].Close, rng = h - l;
            if (rng <= 0) return;
            double body = Math.Abs(c - o), body1 = Math.Abs(R[t - 1].Close - R[t - 1].Open);
            double o1 = R[t - 1].Open, c1 = R[t - 1].Close;
            if (c > o && c1 < o1 && c >= o1 && o <= c1 && body > body1) { candleName = "bullish engulfing"; candleSide = 1; return; }
            if (c < o && c1 > o1 && c <= o1 && o >= c1 && body > body1) { candleName = "bearish engulfing"; candleSide = -1; return; }
            double rng2 = R[t - 2].High - R[t - 2].Low, body2 = Math.Abs(R[t - 2].Close - R[t - 2].Open);
            if (rng2 > 0 && body2 >= 0.6 * rng2 && body1 <= 0.3 * body2)
            {
                double mid2 = (R[t - 2].Open + R[t - 2].Close) / 2;
                if (R[t - 2].Close < R[t - 2].Open && c > o && c > mid2) { candleName = "morning star"; candleSide = 1; return; }
                if (R[t - 2].Close > R[t - 2].Open && c < o && c < mid2) { candleName = "evening star"; candleSide = -1; return; }
            }
            double lower = Math.Min(o, c) - l, upper = h - Math.Max(o, c);
            if (lower >= 0.6 * rng && lower >= 2 * body && upper <= 0.25 * rng) { candleName = "hammer"; candleSide = 1; return; }
            if (upper >= 0.6 * rng && upper >= 2 * body && lower <= 0.25 * rng) { candleName = "shooting star"; candleSide = -1; return; }
        }

        static double PatNeck(CPattern p, double i) { return (p.N2 == p.N1) ? p.P1 : p.P1 + (p.P2 - p.P1) / (p.N2 - p.N1) * (i - p.N1); }

        // The measured move: the pattern's height beyond the neckline, from the break.
        static double PatTarget(CPattern p)
        {
            double x = (p.Broken >= 0) ? p.Broken : p.Pi[p.Np - 1];
            return PatNeck(p, x) + p.Side * p.Height;
        }

        // Follows the pattern from its completion to t: its neckline break, or its failure. False = drop it.
        bool TrackPattern(CPattern p, int t, int maxAge, int retest)
        {
            p.Broken = -1;
            if (p.Complete > t) return false;
            for (int j = p.Complete; j <= t; j++)
            {
                double a = A[j];
                if (!Valid(a)) continue;
                if (p.Side * (R[j].Close - PatNeck(p, j)) > PAT_BREAK_ATR * a)
                {
                    if (t - j > retest) return false;
                    p.Broken = j;
                    return true;
                }
                if (p.Side * (p.Invalid - R[j].Close) > PAT_FAIL_ATR * a) return false;
            }
            return t - p.Complete <= maxAge;
        }

        void AddPattern(CPattern p, int t) { if (TrackPattern(p, t, C.obMaxAge, C.retest)) PAT.Add(p); }

        // The swing (index into P) of kind `kind` between bars i1 and i2 that is furthest in `dir`, or -1.
        int ExtremeBetween(int kind, int i1, int i2, int dir)
        {
            int best = -1;
            for (int q = 0; q < NP; q++)
                if (P[q].Kind == kind && P[q].Index > i1 && P[q].Index < i2 && (best < 0 || dir * P[q].Price > dir * P[best].Price)) best = q;
            return best;
        }

        // Double bottom/top and (inverse) head and shoulders, from the swings confirmed by t.
        void FindChartPatterns(int t)
        {
            PAT.Clear();
            for (int side = 1; side >= -1; side -= 2)
            {
                int kindExt = (side == 1) ? -1 : 1;            // long: the lows make it, the highs between make the neckline
                var ext = new List<int>();
                for (int q = 0; q < NP; q++) if (P[q].Kind == kindExt) ext.Add(q);
                int ne = ext.Count;
                if (ne >= 2)
                {
                    Pivot a = P[ext[ne - 2]], b = P[ext[ne - 1]];
                    int mid = ExtremeBetween(-kindExt, a.Index, b.Index, side);
                    double atrB = A[b.Index];
                    if (mid >= 0 && Valid(atrB))
                    {
                        double worst = (side == 1) ? Math.Min(a.Price, b.Price) : Math.Max(a.Price, b.Price);
                        if (Math.Abs(a.Price - b.Price) <= PATTERN_TOL_ATR * atrB && side * (P[mid].Price - worst) >= PATTERN_DEPTH_ATR * atrB)
                        {
                            var p = new CPattern { Kind = (side == 1) ? "double bottom" : "double top", Side = side, Np = 3 };
                            p.Pi[0] = a.Index; p.Pp[0] = a.Price; p.Pi[1] = P[mid].Index; p.Pp[1] = P[mid].Price; p.Pi[2] = b.Index; p.Pp[2] = b.Price;
                            p.N1 = P[mid].Index; p.P1 = P[mid].Price; p.N2 = b.Index; p.P2 = P[mid].Price;
                            p.Complete = b.Confirmed; p.Broken = -1; p.Invalid = worst; p.Height = side * (P[mid].Price - worst);
                            AddPattern(p, t);
                        }
                    }
                }
                if (ne >= 3)
                {
                    Pivot s1 = P[ext[ne - 3]], hd = P[ext[ne - 2]], s2 = P[ext[ne - 1]];
                    double atrS = A[s2.Index];
                    int m1 = ExtremeBetween(-kindExt, s1.Index, hd.Index, side), m2 = ExtremeBetween(-kindExt, hd.Index, s2.Index, side);
                    double shoulder = (side == 1) ? Math.Min(s1.Price, s2.Price) : Math.Max(s1.Price, s2.Price);
                    if (m1 >= 0 && m2 >= 0 && Valid(atrS) && Math.Abs(s1.Price - s2.Price) <= 2 * PATTERN_TOL_ATR * atrS &&
                        side * shoulder - side * hd.Price >= HEAD_ATR * atrS)
                    {
                        var p = new CPattern { Kind = (side == 1) ? "inverse head and shoulders" : "head and shoulders", Side = side, Np = 5 };
                        p.Pi[0] = s1.Index; p.Pp[0] = s1.Price; p.Pi[1] = P[m1].Index; p.Pp[1] = P[m1].Price; p.Pi[2] = hd.Index; p.Pp[2] = hd.Price;
                        p.Pi[3] = P[m2].Index; p.Pp[3] = P[m2].Price; p.Pi[4] = s2.Index; p.Pp[4] = s2.Price;
                        p.N1 = P[m1].Index; p.P1 = P[m1].Price; p.N2 = P[m2].Index; p.P2 = P[m2].Price;
                        p.Complete = s2.Confirmed; p.Broken = -1; p.Invalid = hd.Price;
                        p.Height = side * (PatNeck(p, hd.Index) - hd.Price);
                        if (p.Height > 0) AddPattern(p, t);
                    }
                }
            }
        }

        //+------------------------------------------------------------------+
        //| Setups (port of strategy.py _long/_short/_finish)                |
        //+------------------------------------------------------------------+
        IEnumerable<Line> AllLines() { return L.Concat(LH); }

        bool Finish(int side, string setup, double stop, string reasons, Signal s, double? targetOverride = null)
        {
            int t = N - 1;
            double c = R[t].Close, a = A[t];
            double floorDist = C.minStopATR * a;
            stop = (side == 1) ? Math.Min(stop, c - floorDist) : Math.Max(stop, c + floorDist);

            bool have = targetOverride.HasValue;               // a chart pattern brings its measured move
            double target = have ? targetOverride.Value : 0;
            if (have) { }
            else if (side == 1)
            {
                foreach (var z in ZR)  if (z.Lo > c && (!have || z.Lo < target)) { target = z.Lo; have = true; }
                foreach (var z in HZR) if (z.Lo > c && (!have || z.Lo < target)) { target = z.Lo; have = true; }
                foreach (var b in OB)
                    if (b.Kind == -1 && OBActive(b, t) && b.Lo > c && (!have || b.Lo < target)) { target = b.Lo; have = true; }
                foreach (var ln in AllLines())
                {
                    if (ln.Kind != -1 || ln.Broken >= 0) continue;
                    double v = LineAt(ln, t);
                    if (v > c && (!have || v < target)) { target = v; have = true; }
                }
                if (!have) target = c + C.defaultRR * (c - stop);
            }
            else
            {
                foreach (var z in ZS)  if (z.Hi < c && (!have || z.Hi > target)) { target = z.Hi; have = true; }
                foreach (var z in HZS) if (z.Hi < c && (!have || z.Hi > target)) { target = z.Hi; have = true; }
                foreach (var b in OB)
                    if (b.Kind == 1 && OBActive(b, t) && b.Hi < c && (!have || b.Hi > target)) { target = b.Hi; have = true; }
                foreach (var ln in AllLines())
                {
                    if (ln.Kind != 1 || ln.Broken >= 0) continue;
                    double v = LineAt(ln, t);
                    if (v < c && (!have || v > target)) { target = v; have = true; }
                }
                if (!have) target = c - C.defaultRR * (stop - c);
            }

            double trigger = (C.confirm == Confirm.None) ? 0.0 : (side == 1 ? R[t].High : R[t].Low);
            double entry = (C.confirm == Confirm.None) ? c : trigger;   // judge reward:risk from the fill level
            double risk = Math.Abs(entry - stop);
            if (risk <= 0) return false;
            bool beyond = (side == 1) ? entry >= target : entry <= target;
            double rr = Math.Abs(target - entry) / risk;
            if (beyond || rr < C.minRR) return false;

            s.Side = side; s.Setup = setup; s.Entry = entry; s.Stop = stop; s.Target = target; s.Trigger = trigger; s.RR = rr;
            s.Reasons = reasons + "; " + (NH > 0 ? "HTF bias " + BiasName(biasH) : "bias " + BiasName(biasE));
            return true;
        }

        bool Setup(int side, Signal s)
        {
            int t = N - 1;
            double o = R[t].Open, h = R[t].High, lo = R[t].Low, c = R[t].Close, a = A[t];
            if (C.trendFilter && direction == -side) return false;
            // rejection candle: closes in the direction of the trade, in the far half of its range
            if (side == 1 && !(c > o && c >= lo + 0.5 * (h - lo))) return false;
            if (side == -1 && !(c < o && c <= h - 0.5 * (h - lo))) return false;
            double buf = TOUCH_BUF_ATR * a;
            var tags = new List<Tag>();
            double zLo = 0, zHi = 0, hLo = 0, hHi = 0;        // the zones tagged, for the chart

            if (side == 1)
            {
                foreach (var z in ZS)
                    if (lo <= z.Hi + buf && c > z.Lo) { tags.Add(new Tag { Reason = string.Format("support zone {0}-{1} (x{2})", PS(z.Lo), PS(z.Hi), z.Touches), Level = z.Lo }); zLo = z.Lo; zHi = z.Hi; break; }
                foreach (var z in HZS)
                    if (lo <= z.Hi + buf && c > z.Lo) { tags.Add(new Tag { Reason = string.Format("HTF support zone {0}-{1} (x{2})", PS(z.Lo), PS(z.Hi), z.Touches), Level = z.Lo }); hLo = z.Lo; hHi = z.Hi; break; }
            }
            else
            {
                foreach (var z in ZR)
                    if (h >= z.Lo - buf && c < z.Hi) { tags.Add(new Tag { Reason = string.Format("resistance zone {0}-{1} (x{2})", PS(z.Lo), PS(z.Hi), z.Touches), Level = z.Hi }); zLo = z.Lo; zHi = z.Hi; break; }
                foreach (var z in HZR)
                    if (h >= z.Lo - buf && c < z.Hi) { tags.Add(new Tag { Reason = string.Format("HTF resistance zone {0}-{1} (x{2})", PS(z.Lo), PS(z.Hi), z.Touches), Level = z.Hi }); hLo = z.Lo; hHi = z.Hi; break; }
            }

            foreach (var ln in AllLines())
            {
                double v = LineAt(ln, t);
                string tf = ln.Htf ? "HTF " : "";
                bool brokeBefore = ln.Broken >= 0 && ln.Broken < t;   // a retest comes after the break
                if (side == 1)
                {
                    if (ln.Kind == 1 && ln.Broken < 0 && lo <= v + buf && c > v)
                        tags.Add(new Tag { Reason = string.Format("{0}rising trendline at {1} ({2} touches)", tf, PS(v), ln.Touches), Level = v });
                    if (ln.Kind == -1 && brokeBefore && lo <= v + buf && c > v)
                        tags.Add(new Tag { Reason = string.Format("retest of broken {0}falling trendline at {1}", tf, PS(v)), Level = v });
                }
                else
                {
                    if (ln.Kind == -1 && ln.Broken < 0 && h >= v - buf && c < v)
                        tags.Add(new Tag { Reason = string.Format("{0}falling trendline at {1} ({2} touches)", tf, PS(v), ln.Touches), Level = v });
                    if (ln.Kind == 1 && brokeBefore && h >= v - buf && c < v)
                        tags.Add(new Tag { Reason = string.Format("retest of broken {0}rising trendline at {1}", tf, PS(v)), Level = v });
                }
            }

            for (int i = OB.Count - 1; i >= 0; i--)            // most recent first
            {
                var b = OB[i];
                if (b.Kind != side || !OBActive(b, t)) continue;
                if (side == 1 && lo <= b.Hi + buf && c > b.Lo)
                { tags.Add(new Tag { Reason = string.Format("bullish order block {0}-{1}", PS(b.Lo), PS(b.Hi)), Level = b.Lo }); break; }
                if (side == -1 && h >= b.Lo - buf && c < b.Hi)
                { tags.Add(new Tag { Reason = string.Format("bearish order block {0}-{1}", PS(b.Lo), PS(b.Hi)), Level = b.Hi }); break; }
            }

            if (C.patterns)                                    // patterns as levels: the candle, a neckline retest, a double bottom/top
            {
                if (candleSide == side) tags.Add(new Tag { Reason = candleName, Level = side == 1 ? lo : h });
                foreach (var p in PAT)
                {
                    if (p.Side != side) continue;
                    double v;
                    string what;
                    if (p.Broken >= 0 && p.Broken < t) { v = PatNeck(p, t); what = "retest of " + p.Kind + " neckline"; }
                    else if (p.Broken < 0 && (p.Kind == "double bottom" || p.Kind == "double top")) { v = p.Invalid; what = p.Kind; }
                    else continue;
                    if (side == 1 ? (lo <= v + buf && c > v) : (h >= v - buf && c < v)) tags.Add(new Tag { Reason = what + " at " + PS(v), Level = v });
                }
            }

            if (tags.Count >= C.minConfluence)
            {
                double stop = (side == 1) ? lo : h;
                foreach (var tg in tags) stop = (side == 1) ? Math.Min(stop, tg.Level) : Math.Max(stop, tg.Level);
                stop += -side * STOP_BUF_ATR * a;
                if (!Finish(side, "rejection", stop, string.Join("; ", tags.Select(x => x.Reason)), s)) return false;
                s.ZoneLo = zLo; s.ZoneHi = zHi; s.HtfLo = hLo; s.HtfHi = hHi;
                return true;
            }

            // trendline breakout: a strong candle closes through a trendline (either timeframe) on this very candle
            if (C.breakouts && side * (c - o) >= BREAKOUT_BODY * (h - lo))
            {
                foreach (var ln in AllLines())
                {
                    if (ln.Kind != -side || ln.Broken != t) continue;
                    double v = LineAt(ln, t);
                    double stop = (side == 1) ? Math.Min(lo, v) - STOP_BUF_ATR * a : Math.Max(h, v) + STOP_BUF_ATR * a;
                    string why = string.Format("close {0} {1}trendline at {2}", side == 1 ? "above falling" : "below rising", ln.Htf ? "HTF " : "", PS(v));
                    if (!Finish(side, "breakout", stop, why, s)) return false;
                    s.ZoneLo = 0; s.ZoneHi = 0; s.HtfLo = 0; s.HtfHi = 0;
                    return true;
                }
                if (C.patterns)
                    foreach (var p in PAT)                     // chart pattern: a close through its neckline
                    {
                        if (p.Side != side || p.Broken != t) continue;
                        double v = PatNeck(p, t);
                        double stop = (side == 1) ? Math.Min(lo, v) - STOP_BUF_ATR * a : Math.Max(h, v) + STOP_BUF_ATR * a;
                        if (!Finish(side, "breakout", stop, string.Format("close {0} {1} neckline at {2}", side == 1 ? "above" : "below", p.Kind, PS(v)), s, PatTarget(p)))
                            return false;
                        s.ZoneLo = 0; s.ZoneHi = 0; s.HtfLo = 0; s.HtfHi = 0;
                        return true;
                    }
            }
            return false;
        }

        //+------------------------------------------------------------------+
        //| Zone plans, for the chart and the panel                          |
        //+------------------------------------------------------------------+
        static bool Overlaps(Zone a, Zone b) { return a.Lo <= b.Hi && b.Lo <= a.Hi; }

        double PlanTarget(int side, double entry, double stop, int t)
        {
            bool have = false;
            double target = 0;
            if (side == 1)
            {
                foreach (var z in ZR)  if (z.Lo > entry && (!have || z.Lo < target)) { target = z.Lo; have = true; }
                foreach (var z in HZR) if (z.Lo > entry && (!have || z.Lo < target)) { target = z.Lo; have = true; }
                foreach (var b in OB) if (b.Kind == -1 && OBActive(b, t) && b.Lo > entry && (!have || b.Lo < target)) { target = b.Lo; have = true; }
            }
            else
            {
                foreach (var z in ZS)  if (z.Hi < entry && (!have || z.Hi > target)) { target = z.Hi; have = true; }
                foreach (var z in HZS) if (z.Hi < entry && (!have || z.Hi > target)) { target = z.Hi; have = true; }
                foreach (var b in OB) if (b.Kind == 1 && OBActive(b, t) && b.Hi < entry && (!have || b.Hi > target)) { target = b.Hi; have = true; }
            }
            foreach (var ln in AllLines())
            {
                if (ln.Kind != -side || ln.Broken >= 0) continue;
                double v = LineAt(ln, t);
                if (side == 1 ? v > entry && (!have || v < target) : v < entry && (!have || v > target)) { target = v; have = true; }
            }
            if (!have) target = entry + side * C.defaultRR * Math.Abs(entry - stop);
            return target;
        }

        void AddPlan(List<ZPlan> o, Zone z, int side, bool htf, bool backed, int t, double maxATR)
        {
            double a = A[t], c = R[t].Close;
            double entry = (side == 1) ? z.Hi : z.Lo;
            if (Math.Abs(c - entry) > maxATR * a) return;
            double stop = (side == 1) ? z.Lo - STOP_BUF_ATR * a : z.Hi + STOP_BUF_ATR * a;
            double floorDist = C.minStopATR * a;
            stop = (side == 1) ? Math.Min(stop, entry - floorDist) : Math.Max(stop, entry + floorDist);
            var p = new ZPlan { Lo = z.Lo, Hi = z.Hi, Side = side, Htf = htf, Backed = backed, Entry = entry, Stop = stop };
            p.Target = PlanTarget(side, entry, stop, t);
            p.RR = Math.Abs(p.Target - entry) / Math.Abs(entry - stop);
            p.Why = "";
            if (C.trendFilter && direction == -side) p.Why = "against the " + BiasName(direction) + " trend";
            else if (p.RR < C.minRR) p.Why = string.Format("R:R {0:F1} (min {1})", p.RR, C.minRR);
            p.Tradable = p.Why == "";
            o.Add(p);
        }

        // Plans for the zones within maxATR ATRs of price, best first: tradable, HTF-backed, nearest.
        List<ZPlan> PlanZones(double maxATR = 8.0)
        {
            var o = new List<ZPlan>();
            int t = N - 1;
            if (!Valid(A[t])) return o;
            for (int side = 1; side >= -1; side -= 2)
            {
                var zs = (side == 1) ? ZS : ZR;
                var hs = (side == 1) ? HZS : HZR;
                foreach (var z in zs) AddPlan(o, z, side, false, hs.Any(h => Overlaps(z, h)), t, maxATR);
                foreach (var h in hs) if (!zs.Any(z => Overlaps(h, z))) AddPlan(o, h, side, true, false, t, maxATR);
            }
            double c = R[t].Close;
            for (int i = 1; i < o.Count; i++)                  // stable sort: tradable, HTF-backed, nearest
                for (int j = i; j > 0; j--)
                {
                    ZPlan x = o[j], y = o[j - 1];
                    int kx = (x.Tradable ? 0 : 2) + (x.Backed ? 0 : 1), ky = (y.Tradable ? 0 : 2) + (y.Backed ? 0 : 1);
                    if (kx < ky || (kx == ky && Math.Abs(c - x.Entry) < Math.Abs(c - y.Entry))) { o[j] = y; o[j - 1] = x; }
                    else break;
                }
            return o;
        }

        static int BestPlan(List<ZPlan> plans) { return plans.FindIndex(p => p.Tradable); }

        // Whether the chart draws plan i: one of the `limit` tradable zones nearest to price, each at a different place.
        static bool PlanShown(List<ZPlan> plans, int i, double price, double atr, int limit = 2)
        {
            var shown = new List<int>();
            var used = new bool[plans.Count];
            while (shown.Count < limit)
            {
                int k = -1;
                for (int j = 0; j < plans.Count; j++)
                    if (plans[j].Tradable && !used[j] && (k < 0 || Math.Abs(price - plans[j].Entry) < Math.Abs(price - plans[k].Entry))) k = j;
                if (k < 0) break;
                used[k] = true;
                if (shown.Any(q => Math.Max(plans[k].Lo, plans[q].Lo) - Math.Min(plans[k].Hi, plans[q].Hi) < SAME_PLACE_ATR * atr)) continue;
                if (k == i) return true;
                shown.Add(k);
            }
            return false;
        }

        string PlanLabel(ZPlan p)
        {
            return string.Format("{0} ZONE{1} {2}-{3}", p.Side == 1 ? "BUY" : "SELL", p.Htf ? " (HTF)" : (p.Backed ? " + HTF" : ""), PS(p.Lo), PS(p.Hi));
        }

        //+------------------------------------------------------------------+
        //| Account checks                                                   |
        //+------------------------------------------------------------------+
        void Journal(string evt, string detail) { Print("TradeBot {0}: {1} - {2}", SymbolName, evt, detail); }

        bool Mine(string label, string symbol) { return label == InpLabel && symbol == SymbolName; }

        // Daily loss and trade-count limits, over all symbols traded with this label.
        bool DailyLimitsOk(out string why)
        {
            why = "";
            DateTime start = Server.Time.Date;
            double closedToday = History.Where(h => h.Label == InpLabel && h.ClosingTime >= start).Sum(h => h.NetProfit);
            var ids = new HashSet<long>(History.Where(h => h.Label == InpLabel && h.EntryTime >= start).Select(h => (long)h.PositionId));
            foreach (var p in Positions) if (p.Label == InpLabel && p.EntryTime >= start) ids.Add(p.Id);
            double balance = Account.Balance, startBalance = balance - closedToday;
            if (C.maxDailyLoss > 0 && startBalance > 0 && balance <= startBalance * (1 - C.maxDailyLoss / 100.0))
            { why = string.Format("daily loss limit reached ({0:F2} today)", closedToday); return false; }
            if (C.maxTradesDay > 0 && ids.Count >= C.maxTradesDay)
            { why = string.Format("{0} trades today (limit {1})", ids.Count, C.maxTradesDay); return false; }
            return true;
        }

        // The spread must not eat more than InpMaxSpreadRisk % of the setup's risk.
        bool SpreadOk(Signal s, out string why)
        {
            why = "";
            if (InpMaxSpreadRisk <= 0) return true;
            double spread = Symbol.Ask - Symbol.Bid, risk = Math.Abs((s.Trigger > 0 ? s.Trigger : s.Entry) - s.Stop);
            if (risk <= 0 || spread <= InpMaxSpreadRisk / 100.0 * risk) return true;
            why = string.Format("skipped: the spread ({0}) is {1:F0}% of the risk (max {2:F0}%)", PS(spread), 100 * spread / risk, InpMaxSpreadRisk);
            return false;
        }

        // Candles back from the forming one (0) to the one that holds `time`.
        int BarShift(DateTime time)
        {
            int lo = 0, hi = Bars.Count - 1;                   // the last candle that opened at or before `time`
            while (lo < hi) { int mid = (lo + hi + 1) / 2; if (Bars.OpenTimes[mid] <= time) lo = mid; else hi = mid - 1; }
            return Bars.Count - 1 - lo;
        }

        // Stand aside for C.cooldown bars after a losing trade on this symbol.
        bool InCooldown(out string why)
        {
            why = "";
            if (C.cooldown <= 0) return false;
            var last = History.Where(h => Mine(h.Label, h.SymbolName)).OrderBy(h => h.ClosingTime).LastOrDefault();
            if (last == null) return false;
            double total = History.Where(h => h.PositionId == last.PositionId).Sum(h => h.NetProfit);
            if (total >= 0) return false;
            int barsSince = BarShift(last.ClosingTime) - 1;
            if (barsSince <= C.cooldown) { why = string.Format("cooldown after a loss ({0}/{1} bars)", barsSince, C.cooldown); return true; }
            return false;
        }

        Position MyPosition() { return Positions.FirstOrDefault(p => Mine(p.Label, p.SymbolName)); }

        // 1R of a position: kept from the fill; after a restart, from the stop written in its comment at entry.
        double RiskOf(Position p)
        {
            double r;
            if (riskOf.TryGetValue(p.Id, out r)) return r;
            string c = p.Comment ?? "";
            int k = c.IndexOf("SL=");
            double sl0;
            if (k >= 0 && double.TryParse(c.Substring(k + 3), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out sl0))
                return Math.Abs(p.EntryPrice - sl0);
            return p.StopLoss.HasValue ? Math.Abs(p.EntryPrice - p.StopLoss.Value) : 0;
        }

        //+------------------------------------------------------------------+
        //| Position size from risk                                          |
        //+------------------------------------------------------------------+
        string Lots(double units) { return Symbol.VolumeInUnitsToQuantity(units).ToString("0.###"); }

        // Volume in units for the setup's risk (cTrader trades units; a lot is Symbol.LotSize units).
        double CalcVolume(int side, double entry, double stop, out string note)
        {
            note = "";
            double balance = Account.Balance, tickSize = Symbol.TickSize, tickValue = Symbol.TickValue;   // tick value per unit
            double vmin = Symbol.VolumeInUnitsMin, vmax = Symbol.VolumeInUnitsMax, step = Symbol.VolumeInUnitsStep;
            double dist = Math.Abs(entry - stop);
            if (balance <= 0 || tickSize <= 0 || tickValue <= 0 || step <= 0 || dist <= 0) { note = "symbol data unavailable"; return 0; }
            double perUnit = dist / tickSize * tickValue;      // account currency lost per unit at the stop
            double units = Math.Floor(balance * C.riskPct / 100.0 / perUnit / step + 1e-9) * step;
            if (units < vmin - 1e-9)
            {
                double minRisk = vmin * perUnit / balance * 100.0;
                if (minRisk > C.minLotMaxRisk)
                {
                    note = string.Format("skipped: the minimum {0} lot would risk {1:F1}% of the account (limit {2:F1}%)", Lots(vmin), minRisk, C.minLotMaxRisk);
                    return 0;
                }
                units = vmin;
                note = string.Format("minimum volume: risking {0:F1}% instead of {1:F1}%", minRisk, C.riskPct);
            }
            units = Math.Min(units, vmax);
            var type = (side == 1) ? TradeType.Buy : TradeType.Sell;
            double margin = Symbol.GetEstimatedMargin(type, units), free = Account.FreeMargin;
            if (margin > free * 0.95 && margin > 0)
            {
                double perUnitMargin = margin / units;
                units = Math.Floor(free * 0.95 / perUnitMargin / step + 1e-9) * step;
                if (units < vmin - 1e-9) { note = string.Format("skipped: not enough free margin ({0:F2}) for the minimum volume", free); return 0; }
                note = "reduced to fit the free margin";
            }
            return Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
        }

        //+------------------------------------------------------------------+
        //| Orders                                                           |
        //+------------------------------------------------------------------+
        bool OpenMarket(Signal s)
        {
            double price = (s.Side == 1) ? Symbol.Ask : Symbol.Bid;
            if (s.Side == 1 ? (price <= s.Stop || price >= s.Target) : (price >= s.Stop || price <= s.Target))
            { Journal("skipped", "price already beyond the stop or the target"); return false; }
            double sl = Px(s.Stop), tp = Px(s.Target);
            string note;
            double units = CalcVolume(s.Side, price, sl, out note);
            if (units <= 0) { Journal("skipped", note); lastNote = note; return false; }

            double slPips = Math.Abs(price - sl) / Symbol.PipSize, tpPips = Math.Abs(tp - price) / Symbol.PipSize;
            string comment = "TradeBot " + s.Setup + " SL=" + sl.ToString(System.Globalization.CultureInfo.InvariantCulture);
            var res = ExecuteMarketOrder(s.Side == 1 ? TradeType.Buy : TradeType.Sell, SymbolName, units, InpLabel, slPips, tpPips, comment);
            if (!res.IsSuccessful || res.Position == null)
            {
                Journal("order failed", res.Error.HasValue ? res.Error.Value.ToString() : "unknown error");
                return false;
            }
            var pos = res.Position;
            // the protection was sent in pips from the quote: pin it to the exact levels of the setup
            if (!pos.StopLoss.HasValue || Math.Abs(pos.StopLoss.Value - sl) > Symbol.TickSize / 2) pos.ModifyStopLossPrice(sl);
            if (!pos.TakeProfit.HasValue || Math.Abs(pos.TakeProfit.Value - tp) > Symbol.TickSize / 2) pos.ModifyTakeProfitPrice(tp);
            riskOf[pos.Id] = Math.Abs(pos.EntryPrice - sl);
            lastNote = note;
            Journal("filled", string.Format("{0} {1} {2} lots at {3}, SL {4}, TP {5}, R:R {6:F1}{7} - {8}",
                                            s.Side == 1 ? "BUY" : "SELL", s.Setup, Lots(units), PS(pos.EntryPrice), PS(sl), PS(tp), s.RR,
                                            note == "" ? "" : " (" + note + ")", s.Reasons));
            return true;
        }

        // Confirmation: checked on every tick for "break", at each candle close for "close".
        void CheckArmedTick()
        {
            if (!armed || armedSig.Trigger <= 0 || C.confirm != Confirm.Break) return;
            if (armedSig.Side == 1 ? Symbol.Bid <= armedSig.Stop : Symbol.Ask >= armedSig.Stop)
            { armed = false; Journal("order cancelled", "stop level traded before the confirmation"); return; }
            if (armedSig.Side == 1 ? Symbol.Ask >= armedSig.Trigger : Symbol.Bid <= armedSig.Trigger)
            { armed = false; OpenMarket(armedSig); }
        }

        void CheckArmedBar()
        {
            if (!armed) return;
            int t = N - 1;
            if (C.confirm == Confirm.Close)
            {
                if (armedSig.Side == 1 ? R[t].Low <= armedSig.Stop : R[t].High >= armedSig.Stop)
                { armed = false; Journal("order cancelled", "stop level traded before the confirmation"); return; }
                if (armedSig.Side == 1 ? R[t].Close > armedSig.Trigger : R[t].Close < armedSig.Trigger)
                { armed = false; OpenMarket(armedSig); return; }
            }
            armedBarsLeft--;
            if (armed && armedBarsLeft <= 0) { armed = false; Journal("order cancelled", "not confirmed in time"); }
        }

        //+------------------------------------------------------------------+
        //| Position management                                              |
        //+------------------------------------------------------------------+
        // Partial profit: on every tick, once price has gone partialR x 1R in favour.
        void ManagePartial()
        {
            if (C.partialR <= 0 || C.partialPct <= 0) return;
            var pos = MyPosition();
            if (pos == null || partDone.Contains(pos.Id)) return;
            if (historyChecked.Add(pos.Id) && History.Any(h => h.PositionId == pos.Id))
            { partDone.Add(pos.Id); return; }                  // already partly closed (before a restart)
            int side = pos.TradeType == TradeType.Buy ? 1 : -1;
            double risk = RiskOf(pos);
            if (risk <= 0) return;
            double level = pos.EntryPrice + side * C.partialR * risk;
            if (side == 1 ? Symbol.Bid < level : Symbol.Ask > level) return;
            double vol = pos.VolumeInUnits, step = Symbol.VolumeInUnitsStep, vmin = Symbol.VolumeInUnitsMin;
            double part = Math.Floor(vol * C.partialPct / 100.0 / step + 1e-9) * step;
            partDone.Add(pos.Id);
            if (part < vmin - 1e-9 || vol - part < vmin - 1e-9) { Journal("partial skipped", Lots(vol) + " lots can't be split"); return; }
            if (ClosePosition(pos, part).IsSuccessful)
                Journal("partial", string.Format("closed {0} of {1} lots at +{2:F1}R", Lots(part), Lots(vol), C.partialR));
        }

        // Break-even and trailing stop: at each candle close, only ever tightening.
        void ManageStops()
        {
            var pos = MyPosition();
            if (pos == null || !pos.StopLoss.HasValue) return;
            int t = N - 1;
            int side = pos.TradeType == TradeType.Buy ? 1 : -1;
            double openPrice = pos.EntryPrice, sl = pos.StopLoss.Value;
            double risk = RiskOf(pos);
            if (risk <= 0) return;
            int shift = BarShift(pos.EntryTime);
            if (shift < 1 || shift > N) return;                 // no closed candle since the entry yet
            int entryIdx = N - shift;                          // the entry candle in R[] (R[N-1] is shift 1)
            double best = side == 1 ? R[entryIdx].High : R[entryIdx].Low;
            for (int i = entryIdx; i <= t; i++) best = side == 1 ? Math.Max(best, R[i].High) : Math.Min(best, R[i].Low);
            double progress = side * (best - openPrice) / risk;
            double lastClose = R[t].Close, market = (side == 1) ? Symbol.Bid : Symbol.Ask;
            double newSL = sl;
            string kind = "";

            if (C.beR > 0 && progress >= C.beR && (side == 1 ? openPrice > newSL && openPrice < lastClose : openPrice < newSL && openPrice > lastClose))
            { newSL = openPrice; kind = "break-even"; }
            if (C.trail != Trail.None && progress >= C.trailStartR)
            {
                double level = 0;
                bool haveLevel = false;
                if (C.trail == Trail.Atr) { level = best - side * C.trailATR * A[t]; haveLevel = true; }
                else
                    for (int i = NP - 1; i >= 0; i--)
                        if (P[i].Kind == -side && P[i].Index >= entryIdx) { level = P[i].Price - side * STOP_BUF_ATR * A[t]; haveLevel = true; break; }
                if (haveLevel && (side == 1 ? level > newSL && level < lastClose : level < newSL && level > lastClose))
                { newSL = level; kind = "trailing stop"; }
            }
            newSL = Px(newSL);
            if (newSL == Px(sl) || kind == "") return;
            if (side == 1 ? newSL >= market : newSL <= market) return;   // price is already there
            if (pos.ModifyStopLossPrice(newSL).IsSuccessful)
                Journal("stop moved", string.Format("{0} to {1} (+{2:F1}R reached)", kind, PS(newSL), progress));
        }

        //+------------------------------------------------------------------+
        //| Chart and panel                                                  |
        //+------------------------------------------------------------------+
        static readonly Color Up = Color.FromArgb(255, 42, 157, 143), Dn = Color.FromArgb(255, 231, 111, 81);
        static readonly Color Ink = Color.FromArgb(255, 38, 70, 83), HtfInk = Color.FromArgb(255, 109, 89, 122);

        void ClearDrawings()
        {
            foreach (var o in Chart.Objects.Where(o => o.Name.StartsWith(PFX)).ToList()) Chart.RemoveObject(o.Name);
        }

        void Draw(Signal s, bool haveSignal)
        {
            ClearDrawings();
            if (!InpDraw || N < 2) return;
            int t = N - 1;
            var sec = TimeSpan.FromSeconds(Math.Max(PeriodSeconds(TimeFrame), 60));
            DateTime left = R[Math.Max(0, N - 200)].Time, right = R[t].Time + TimeSpan.FromTicks(sec.Ticks * 12);
            Signal shown = armed ? armedSig : s;
            bool show = armed || haveSignal;

            if (!show)                                         // the zones to wait for, with their plan
            {
                var plans = PlanZones();
                int best = BestPlan(plans);
                for (int i = 0; i < plans.Count; i++)
                {
                    var p = plans[i];
                    if (C.zoneBest ? i != best : !PlanShown(plans, i, R[t].Close, A[t])) continue;
                    var clr = p.Side == 1 ? Up : Dn;
                    var rect = Chart.DrawRectangle(PFX + "pz" + i, left, p.Lo, right, p.Hi, Color.FromArgb(60, clr.R, clr.G, clr.B));
                    rect.IsFilled = true;
                    Chart.DrawText(PFX + "pzT" + i, string.Format("{0}  TP {1}  R:R {2:F1}", PlanLabel(p), PS(p.Target), p.RR), R[t].Time + sec + sec, (p.Lo + p.Hi) / 2, clr);
                }
            }
            else
            {
                if (shown.ZoneHi > 0)
                {
                    var c = shown.Side == 1 ? Up : Dn;
                    var rect = Chart.DrawRectangle(PFX + "ez", left, shown.ZoneLo, right, shown.ZoneHi, Color.FromArgb(90, c.R, c.G, c.B));
                    rect.IsFilled = true;
                }
                if (shown.HtfHi > 0) Chart.DrawRectangle(PFX + "ehz", left, shown.HtfLo, right, shown.HtfHi, HtfInk, 2);
                double entry = shown.Trigger > 0 ? shown.Trigger : shown.Entry;
                string verb = shown.Side == 1 ? "BUY" : "SELL";
                Chart.DrawTrendLine(PFX + "entry", R[t].Time, entry, right, entry, Ink, 2, LineStyle.LinesDots);
                Chart.DrawTrendLine(PFX + "sl", R[t].Time, shown.Stop, right, shown.Stop, Dn, 2, LineStyle.Solid);
                Chart.DrawTrendLine(PFX + "tp", R[t].Time, shown.Target, right, shown.Target, Up, 2, LineStyle.Solid);
                Chart.DrawText(PFX + "entryT", "ENTER HERE: " + (shown.Trigger > 0 ? verb + " STOP " : verb + " ") + PS(entry), right, entry, Ink);
                Chart.DrawText(PFX + "slT", "SL " + PS(shown.Stop), right, shown.Stop, Dn);
                Chart.DrawText(PFX + "tpT", string.Format("TP {0}  R:R {1:F1}", PS(shown.Target), shown.RR), right, shown.Target, Up);
            }

            int n = 0;                                         // trendlines: entry chart, then HTF
            foreach (var ln in AllLines())
            {
                var clr = ln.Htf ? HtfInk : Ink;
                int width = ln.Htf ? 3 : 2;
                string nm = PFX + "l" + (n++);
                if (ln.Broken < 0)
                {
                    var tl = Chart.DrawTrendLine(nm, R[ln.I1].Time, ln.P1, R[ln.I2].Time, ln.P2, clr, width, LineStyle.Solid);
                    tl.ExtendToInfinity = true;
                    continue;
                }
                double at = LineAt(ln, ln.Broken);
                Chart.DrawTrendLine(nm, R[ln.I1].Time, ln.P1, R[ln.Broken].Time, at, clr, width, LineStyle.Solid);
                Chart.DrawTrendLine(nm + "x", R[ln.Broken].Time, at, right, LineAt(ln, t + 12), clr, 1, LineStyle.Dots);
                bool up = ln.Kind == -1;
                Chart.DrawText(nm + "brk", up ? "BREAK ▲" : "BREAK ▼", R[ln.Broken].Time, at, up ? Up : Dn);
            }
        }

        void Panel(Signal s, bool haveSignal, string status)
        {
            if (!InpPanel) { Chart.RemoveObject(PFX + "panel"); return; }
            string tfH = NH > 0 ? C.htf.ShortName : TimeFrame.ShortName;
            int dir = (NH > 0) ? biasH : biasE;
            var lines = new List<string>
            {
                "TradeBot  " + SymbolName + (C.zoneBest ? "  Swing" : "  Scalp"),
                "TREND   " + (dir == 1 ? tfH + " up - buys only" : (dir == -1 ? tfH + " down - sells only" : "no trend - no trade"))
            };
            if (haveSignal || armed)
            {
                var g = armed ? armedSig : s;
                lines.Add(string.Format("ENTER   {0} STOP {1},  SL {2},  TP {3},  R:R {4:F1}{5}", g.Side == 1 ? "BUY" : "SELL",
                                        PS(g.Trigger > 0 ? g.Trigger : g.Entry), PS(g.Stop), PS(g.Target), g.RR,
                                        armed ? string.Format("  ({0} candles left)", armedBarsLeft) : ""));
            }
            else
            {
                var plans = PlanZones();
                int best = BestPlan(plans);
                if (dir != 0 && best >= 0)
                {
                    var b = plans[best];
                    lines.Add(string.Format("PLAN    {0} at {1}-{2} after a rejection candle.  SL {3},  TP {4}  (R:R {5:F1})",
                                            b.Side == 1 ? "Buy" : "Sell", PS(b.Lo), PS(b.Hi), PS(b.Stop), PS(b.Target), b.RR));
                }
                else lines.Add("PLAN    " + (dir == 0 ? "no trade - wait for a trend" : "no trade now - wait for a zone"));
            }
            if (status != "trading" && status != "waiting for confirmation") lines.Add("STATUS  " + status);
            else if (lastNote.Contains("skipped")) lines.Add("STATUS  " + lastNote);
            var txt = Chart.DrawStaticText(PFX + "panel", string.Join("\n", lines), VerticalAlignment.Top, HorizontalAlignment.Left, Ink);
            txt.FontSize = 11;
        }

        //+------------------------------------------------------------------+
        //| Events                                                           |
        //+------------------------------------------------------------------+
        protected override void OnStart()
        {
            LoadConfig();
            digits = Symbol.Digits;
            tradingPermitted = !Account.IsLive || InpAllowReal;
            if (!tradingPermitted)
                Print("TradeBot: this is a LIVE account and 'Allow trading a LIVE account' is off - analysis only, no orders.");
            var want = (InpPreset == Preset.Swing) ? TimeFrame.Hour4 : TimeFrame.Minute5;
            if (InpPreset != Preset.Custom && TimeFrame != want)
            {
                tfWarning = string.Format("wrong timeframe: {0} is built for {1} charts, this one is {2} - results untested",
                                          InpPreset, want.ShortName, TimeFrame.ShortName);
                Print("TradeBot: " + tfWarning);
            }
            while (Bars.Count < InpHistoryBars + 2 && Bars.LoadMoreHistory() > 0) { }
            if (PeriodSeconds(C.htf) > PeriodSeconds(TimeFrame))
            {
                htfSeconds = PeriodSeconds(C.htf);
                htfBars = MarketData.GetBars(C.htf);
                while (htfBars.Count < InpHTFHistoryBars + 2 && htfBars.LoadMoreHistory() > 0) { }
            }
            Print("TradeBot {0}: minimum volume {1} lot ({2} units), step {3} units, lot size {4} units",
                  SymbolName, Lots(Symbol.VolumeInUnitsMin), Symbol.VolumeInUnitsMin, Symbol.VolumeInUnitsStep, Symbol.LotSize);
        }

        protected override void OnStop()
        {
            if (Drawing) ClearDrawings();
        }

        protected override void OnTick()
        {
            if (!tradingPermitted) return;
            ManagePartial();
            CheckArmedTick();
        }

        // A new candle opened: analyse the one that just closed.
        protected override void OnBar()
        {
            if (!Analyse()) return;
            string status = tradingPermitted ? "trading" : "analysis only (live account)";
            var s = new Signal();
            bool haveSignal = Setup(1, s) || Setup(-1, s);

            if (tradingPermitted)
            {
                CheckArmedBar();
                ManageStops();
                string why;
                DateTime bar = R[N - 1].Time;
                if (MyPosition() != null) status = "in a trade";
                else if (armed) status = "waiting for confirmation";
                else if (!DailyLimitsOk(out why) || InCooldown(out why)) status = why;
                else if (haveSignal && bar == lastTakenBar) status = "this setup was already taken";
                else if (haveSignal)
                {
                    lastTakenBar = bar;
                    if (!SpreadOk(s, out why)) { Journal("skipped", why); status = why; lastNote = why; }
                    else if (C.confirm == Confirm.None) OpenMarket(s);
                    else
                    {
                        armed = true;
                        armedSig = s;
                        armedBarsLeft = C.confirmBars;
                        Journal("setup armed", string.Format("{0} {1}: {2} {3}, SL {4}, TP {5}, R:R {6:F1} - {7}",
                                                             s.Side == 1 ? "LONG" : "SHORT", s.Setup,
                                                             C.confirm == Confirm.Break ? "enter on a break of" : "enter after a close beyond",
                                                             PS(s.Trigger), PS(s.Stop), PS(s.Target), s.RR, s.Reasons));
                        CheckArmedTick();                      // price may already be through the trigger
                    }
                }
            }
            if (tfWarning != "" && (status == "trading" || status == "waiting for confirmation")) status = tfWarning;
            if (Drawing)
            {
                Draw(s, haveSignal);
                Panel(s, haveSignal, status);
            }
        }
    }
}
