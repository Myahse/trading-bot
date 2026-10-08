//+------------------------------------------------------------------+
//|                                               TradeBotParity.mq5 |
//|  Writes what the EA's analysis sees at every closed candle, and  |
//|  the candles themselves, to MQL5/Files, for tradebot/parity.py   |
//|  to compare with the Python bot. Places no orders.               |
//+------------------------------------------------------------------+
#property copyright "Myahse"
#property version   "1.00"
#property description "Dumps the TradeBot analysis (zones, trendlines, order blocks, bias, setups) of every"
#property description "closed candle on this chart to MQL5/Files, to check the EA against the Python bot."
#property script_show_inputs

#include "TradeBotCore.mqh"

input EPreset InpPreset   = PRESET_SCALP;   // Preset (Scalp or Swing)
input int     InpBars     = 3000;           // Closed candles to evaluate (the most recent)
input int     InpHistory  = 100000;         // Candles of history behind each one (large = all, as Python)
input string  InpOut      = "";             // Output file (default TradeBot_parity_<symbol>_<tf>.csv)

// The chart's candles, with every price written exactly (17 significant digits), so the Python
// bot analyses the very same numbers.
bool WriteCandles(string file)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, PERIOD_CURRENT, 1, InpHistory, r);
   int h = FileOpen(file, FILE_WRITE | FILE_ANSI | FILE_TXT);
   if(n <= 0 || h == INVALID_HANDLE) return false;
   FileWriteString(h, "time,open,high,low,close\n");
   for(int i = 0; i < n; i++)
      FileWriteString(h, StringFormat("%s,%.17g,%.17g,%.17g,%.17g\n", TimeToString(r[i].time, TIME_DATE | TIME_MINUTES),
                                      r[i].open, r[i].high, r[i].low, r[i].close));
   FileClose(h);
   return true;
  }

string ZoneCols(const Zone &z[])
  {
   if(ArraySize(z) == 0) return ",,";
   return StringFormat("%.8g,%.8g,%d", z[0].lo, z[0].hi, z[0].touches);
  }

void OnStart()
  {
   ApplyPreset((int)InpPreset);
   gHistoryBars = InpHistory;
   gHTFHistoryBars = InpHistory;
   string file = InpOut != "" ? InpOut : StringFormat("TradeBot_parity_%s_%s.csv", _Symbol, StringSubstr(EnumToString(Period()), 7));
   int h = FileOpen(file, FILE_WRITE | FILE_ANSI | FILE_TXT);
   if(h == INVALID_HANDLE) { Print("TradeBotParity: cannot write ", file, ", error ", GetLastError()); return; }
   FileWriteString(h, "time,bars,atr,pivots,bias,htf_bias,n_zones,sup_lo,sup_hi,sup_touches,res_lo,res_hi,res_touches,"
                      "n_htf_zones,hsup_lo,hsup_hi,hsup_touches,hres_lo,hres_hi,hres_touches,n_lines,n_htf_lines,"
                      "n_obs,side,setup,stop,target,trigger,n_plans,n_tradable,best_side,best_lo,best_hi,best_stop,best_target,candle,n_patterns\n");
   int total = Bars(_Symbol, PERIOD_CURRENT);
   int done = 0;
   for(int shift = MathMin(InpBars, total - 1); shift >= 1; shift--)
     {
      if(IsStopped()) break;
      if(!Analyse(shift)) continue;
      int t = N - 1;
      Signal s;
      ClearSignal(s);
      bool have = Setup(1, s) || Setup(-1, s);
      int obs = 0;
      for(int i = 0; i < ArraySize(OB); i++) if(OBActive(OB[i], t)) obs++;
      ZPlan plans[];
      int np = PlanZones(plans), b = BestPlan(plans), nt = 0;
      for(int i = 0; i < np; i++) if(plans[i].tradable) nt++;
      string bestCols = (b < 0) ? ",,,," : StringFormat("%s,%.8g,%.8g,%.8g,%.8g", plans[b].side == 1 ? "long" : "short",
                                                         plans[b].lo, plans[b].hi, plans[b].stop, plans[b].target);
      FileWriteString(h, StringFormat("%s,%d,%.8g,%d,%d,%d,%d,%s,%s,%d,%s,%s,%d,%d,%d,%s,%s,%s,%s,%s,%d,%d,%s,%s,%d\n",
                      TimeToString(R[t].time, TIME_DATE | TIME_MINUTES), N, A[t], NP, biasE, NH > 0 ? biasH : 0,
                      ArraySize(ZS) + ArraySize(ZR), ZoneCols(ZS), ZoneCols(ZR),
                      ArraySize(HZS) + ArraySize(HZR), ZoneCols(HZS), ZoneCols(HZR),
                      ArraySize(L), ArraySize(LH), obs,
                      have ? (s.side == 1 ? "long" : "short") : "", have ? s.setup : "",
                      have ? StringFormat("%.8g", s.stop) : "", have ? StringFormat("%.8g", s.target) : "",
                      have ? StringFormat("%.8g", s.trigger) : "", np, nt, bestCols, candleName, ArraySize(PAT)));
      done++;
     }
   FileClose(h);
   string candles = file;
   StringReplace(candles, ".csv", "_candles.csv");
   if(!WriteCandles(candles)) Print("TradeBotParity: cannot write ", candles);
   PrintFormat("TradeBotParity: %d candles analysed, written to MQL5/Files/%s and %s", done, file, candles);
  }
