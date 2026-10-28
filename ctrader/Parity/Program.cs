// Runs TradeBot.cs's analysis on the candles of the MT5 parity fixture and compares with what the EA saw.
//   zcat ../../tests/data/mt5_parity_GBPUSD_M5_candles.csv.gz > candles.csv
//   zcat ../../tests/data/mt5_parity_GBPUSD_M5.csv.gz > mt5.csv
//   dotnet run -- candles.csv mt5.csv Scalp
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using cAlgo.Robots;

static class Program
{
    const BindingFlags F = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
    static object Get(object o, string n) => o.GetType().GetField(n, F).GetValue(o);
    static void Set(object o, string n, object v) => o.GetType().GetField(n, F).SetValue(o, v);
    static object Call(object o, string n, params object[] a) => o.GetType().GetMethods(F).First(m => m.Name == n && m.GetParameters().Length == a.Length).Invoke(o, a);

    static int Main(string[] args)
    {
        string candles = args[0], mt5 = args[1], preset = args.Length > 2 ? args[2] : "Scalp";
        var ci = CultureInfo.InvariantCulture;
        var bot = (TradeBot)RuntimeHelpers.GetUninitializedObject(typeof(TradeBot));
        // fields with initialisers are skipped by GetUninitializedObject: run the constructor body's work by hand
        foreach (var f in typeof(TradeBot).GetFields(F))
        {
            if (f.IsStatic || f.IsLiteral || f.FieldType.IsValueType || f.GetValue(bot) != null) continue;
            if (f.FieldType == typeof(string)) { f.SetValue(bot, ""); continue; }
            var ctor = f.FieldType.GetConstructor(Type.EmptyTypes);
            if (ctor != null) f.SetValue(bot, ctor.Invoke(null));
            else if (f.FieldType.IsArray) f.SetValue(bot, Array.CreateInstance(f.FieldType.GetElementType(), 0));
        }
        var tb = typeof(TradeBot);
        var presetT = tb.Assembly.GetType("cAlgo.Robots.Preset");
        tb.GetProperty("InpPreset").SetValue(bot, Enum.Parse(presetT, preset));
        tb.GetProperty("InpMinLotRiskCap").SetValue(bot, 0.0);
        Call(bot, "LoadConfig");
        Set(bot, "digits", 5);
        Set(bot, "htfSeconds", preset == "Swing" ? 86400 : 3600);
        var rateT = tb.GetNestedType("Rate", F);

        var rows = File.ReadAllLines(candles).Skip(1).Select(l => l.Split(',')).ToList();
        int n = rows.Count;
        var times = new DateTime[n]; var o = new double[n]; var h = new double[n]; var lo = new double[n]; var c = new double[n];
        for (int i = 0; i < n; i++)
        {
            times[i] = DateTime.ParseExact(rows[i][0], "yyyy.MM.dd HH:mm", ci);
            o[i] = double.Parse(rows[i][1], ci); h[i] = double.Parse(rows[i][2], ci); lo[i] = double.Parse(rows[i][3], ci); c[i] = double.Parse(rows[i][4], ci);
        }
        Array MakeRates(int from, int to)   // inclusive
        {
            var a = Array.CreateInstance(rateT, Math.Max(0, to - from + 1));
            for (int i = from; i <= to; i++)
            {
                var r = Activator.CreateInstance(rateT);
                rateT.GetField("Time").SetValue(r, times[i]); rateT.GetField("Open").SetValue(r, o[i]); rateT.GetField("High").SetValue(r, h[i]);
                rateT.GetField("Low").SetValue(r, lo[i]); rateT.GetField("Close").SetValue(r, c[i]);
                a.SetValue(r, i - from);
            }
            return a;
        }
        // higher timeframe resampled from the chart candles (as the Python bot does)
        int hs = preset == "Swing" ? 86400 : 3600;
        var hT = new List<DateTime>(); var hO = new List<double>(); var hH = new List<double>(); var hL = new List<double>(); var hC = new List<double>();
        for (int i = 0; i < n; i++)
        {
            var key = new DateTime(times[i].Ticks - times[i].Ticks % TimeSpan.FromSeconds(hs).Ticks);
            if (hT.Count == 0 || hT[hT.Count - 1] != key) { hT.Add(key); hO.Add(o[i]); hH.Add(h[i]); hL.Add(lo[i]); hC.Add(c[i]); }
            else { int k = hT.Count - 1; hH[k] = Math.Max(hH[k], h[i]); hL[k] = Math.Min(hL[k], lo[i]); hC[k] = c[i]; }
        }
        Array MakeH(int count)
        {
            var a = Array.CreateInstance(rateT, count);
            for (int i = 0; i < count; i++)
            {
                var r = Activator.CreateInstance(rateT);
                rateT.GetField("Time").SetValue(r, hT[i]); rateT.GetField("Open").SetValue(r, hO[i]); rateT.GetField("High").SetValue(r, hH[i]);
                rateT.GetField("Low").SetValue(r, hL[i]); rateT.GetField("Close").SetValue(r, hC[i]);
                a.SetValue(r, i);
            }
            return a;
        }

        var exp = File.ReadAllLines(mt5);
        var head = exp[0].Split(',');
        int col(string name) => Array.IndexOf(head, name);
        var cols = new[] { "atr", "pivots", "bias", "htf_bias", "n_zones", "sup_lo", "sup_hi", "sup_touches", "res_lo", "res_hi", "res_touches",
                           "n_htf_zones", "hsup_lo", "hsup_hi", "hsup_touches", "hres_lo", "hres_hi", "hres_touches", "n_lines", "n_htf_lines",
                           "n_obs", "side", "setup", "stop", "target", "trigger", "n_plans", "n_tradable", "best_side", "best_lo", "best_hi",
                           "best_stop", "best_target", "candle", "n_patterns" };
        var agree = cols.ToDictionary(x => x, x => 0);
        var firstMiss = new Dictionary<string, string>();
        int total = 0, sigM = 0, sigC = 0, sigSame = 0;
        var sigT = tb.GetNestedType("Signal", F);
        var index = new Dictionary<DateTime, int>();
        for (int i = 0; i < n; i++) index[times[i]] = i;
        foreach (var line in exp.Skip(1))
        {
            var e = line.Split(',');
            var tm = DateTime.ParseExact(e[0], "yyyy.MM.dd HH:mm", ci);
            int t = index[tm];
            var closedBy = tm - TimeSpan.FromSeconds(hs);
            int nh = hT.Count(x => x <= closedBy);
            if (!(bool)Call(bot, "AnalyseRates", MakeRates(0, t), MakeH(nh))) continue;
            total++;
            var s = Activator.CreateInstance(sigT);
            bool have = (bool)Call(bot, "Setup", 1, s) || (bool)Call(bot, "Setup", -1, s);
            int N = (int)Get(bot, "N");
            var A = (double[])Get(bot, "A");
            IList ZS = (IList)Get(bot, "ZS"), ZR = (IList)Get(bot, "ZR"), HZS = (IList)Get(bot, "HZS"), HZR = (IList)Get(bot, "HZR");
            IList OB = (IList)Get(bot, "OB"), L = (IList)Get(bot, "L"), LH = (IList)Get(bot, "LH"), PAT = (IList)Get(bot, "PAT");
            int NH = (int)Get(bot, "NH");
            int obs = 0;
            foreach (var b in OB) if ((bool)Call(bot, "OBActive", b, N - 1)) obs++;
            var plans = (IList)Call(bot, "PlanZones", 8.0);
            int best = -1, nt = 0;
            for (int i = 0; i < plans.Count; i++) { bool tr = (bool)plans[i].GetType().GetField("Tradable").GetValue(plans[i]); if (tr) { nt++; if (best < 0) best = i; } }
            string Z(IList z, string f) { if (z.Count == 0) return ""; var v = z[0].GetType().GetField(f).GetValue(z[0]); return Convert.ToString(v, ci); }
            string P(object pl, string f) => pl == null ? "" : Convert.ToString(pl.GetType().GetField(f).GetValue(pl), ci);
            object bp = best >= 0 ? plans[best] : null;
            int side = (int)sigT.GetField("Side").GetValue(s);
            var mine = new Dictionary<string, string>
            {
                ["atr"] = A[N - 1].ToString("R", ci), ["pivots"] = Get(bot, "NP").ToString(), ["bias"] = Get(bot, "biasE").ToString(),
                ["htf_bias"] = (NH > 0 ? (int)Get(bot, "biasH") : 0).ToString(),
                ["n_zones"] = (ZS.Count + ZR.Count).ToString(), ["sup_lo"] = Z(ZS, "Lo"), ["sup_hi"] = Z(ZS, "Hi"), ["sup_touches"] = Z(ZS, "Touches"),
                ["res_lo"] = Z(ZR, "Lo"), ["res_hi"] = Z(ZR, "Hi"), ["res_touches"] = Z(ZR, "Touches"),
                ["n_htf_zones"] = (HZS.Count + HZR.Count).ToString(), ["hsup_lo"] = Z(HZS, "Lo"), ["hsup_hi"] = Z(HZS, "Hi"), ["hsup_touches"] = Z(HZS, "Touches"),
                ["hres_lo"] = Z(HZR, "Lo"), ["hres_hi"] = Z(HZR, "Hi"), ["hres_touches"] = Z(HZR, "Touches"),
                ["n_lines"] = L.Count.ToString(), ["n_htf_lines"] = LH.Count.ToString(), ["n_obs"] = obs.ToString(),
                ["side"] = have ? (side == 1 ? "long" : "short") : "", ["setup"] = have ? (string)sigT.GetField("Setup").GetValue(s) : "",
                ["stop"] = have ? Convert.ToString(sigT.GetField("Stop").GetValue(s), ci) : "",
                ["target"] = have ? Convert.ToString(sigT.GetField("Target").GetValue(s), ci) : "",
                ["trigger"] = have ? Convert.ToString(sigT.GetField("Trigger").GetValue(s), ci) : "",
                ["n_plans"] = plans.Count.ToString(), ["n_tradable"] = nt.ToString(),
                ["best_side"] = bp == null ? "" : ((int)bp.GetType().GetField("Side").GetValue(bp) == 1 ? "long" : "short"),
                ["best_lo"] = P(bp, "Lo"), ["best_hi"] = P(bp, "Hi"), ["best_stop"] = P(bp, "Stop"), ["best_target"] = P(bp, "Target"),
                ["candle"] = (string)Get(bot, "candleName"), ["n_patterns"] = PAT.Count.ToString(),
            };
            foreach (var k in cols)
            {
                string a = e[col(k)], b = mine[k];
                bool same;
                double da, db;
                if (a == "" || b == "") same = a == b;
                else if (double.TryParse(a, NumberStyles.Float, ci, out da) && double.TryParse(b, NumberStyles.Float, ci, out db))
                    same = Math.Abs(da - db) <= 1e-7 * Math.Max(Math.Abs(da), 1e-12);
                else same = a == b;
                if (same) agree[k]++;
                else if (!firstMiss.ContainsKey(k)) firstMiss[k] = e[0] + " mt5=" + a + " ctrader=" + b;
            }
            bool ms = e[col("side")] != "";
            if (ms) sigM++;
            if (have) sigC++;
            if (ms && have && e[col("side")] == mine["side"] && e[col("setup")] == mine["setup"]) sigSame++;
        }
        Console.WriteLine("candles compared: " + total);
        foreach (var k in cols) Console.WriteLine("{0,-14} {1,6:P2}{2}", k, (double)agree[k] / total, firstMiss.ContainsKey(k) ? "   first diff " + firstMiss[k] : "");
        Console.WriteLine("signals: mt5 {0}, ctrader {1}, same {2}", sigM, sigC, sigSame);
        return 0;
    }
}
