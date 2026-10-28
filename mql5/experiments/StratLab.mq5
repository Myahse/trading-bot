//+------------------------------------------------------------------+
//| StratLab.mq5 - test EA for three classic strategies, to compare  |
//| them with TradeBot in the Strategy Tester.                       |
//|  1 Opening-range breakout: trade the first close beyond the      |
//|    range of the session's first hours, stop at the other side.   |
//|  2 Donchian breakout: enter on a close beyond the N-bar channel, |
//|    exit on a close beyond the opposite M-bar channel.            |
//|  3 RSI(2) pullback: with the 200 EMA trend, buy an RSI(2) dip /  |
//|    sell an RSI(2) spike, exit on a close beyond the 5 SMA.       |
//| Tester use only: it refuses to trade a real account.             |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

enum EStrat { STRAT_ORB = 1,       // Opening-range breakout
              STRAT_DONCHIAN = 2,  // Donchian breakout
              STRAT_RSI2 = 3       // RSI(2) pullback
            };

input EStrat InpStrategy     = STRAT_ORB;  // Strategy
input double InpRiskPct      = 1.0;        // Risk per trade, % of balance
input double InpMinLotMaxRisk = 5.0;       // Skip if even the minimum lot risks more than this %
input int    InpSides        = 0;          // 0 both, 1 buys only, -1 sells only

input group "Opening-range breakout"
input int    InpOrbStart     = 7;          // Range start, server hour
input int    InpOrbEnd       = 9;          // Range end (exclusive), server hour
input int    InpOrbClose     = 20;         // Close any open trade at this server hour
input double InpOrbRR        = 1.5;        // Target in R (0 = hold to the close hour)
input double InpOrbMaxATR    = 3.0;        // Skip days whose range is wider than this many H1 ATR (0 = off)

input group "Donchian breakout"
input int    InpDonEntry     = 20;         // Entry channel, bars
input int    InpDonExit      = 10;         // Exit channel, bars
input double InpDonStopATR   = 2.0;        // Initial stop, ATR

input group "RSI(2) pullback"
input int    InpRsiTrendMA   = 200;        // Trend EMA
input double InpRsiLow       = 10;         // Buy below this RSI(2)
input double InpRsiHigh      = 90;         // Sell above this RSI(2)
input int    InpRsiMaxBars   = 10;         // Time exit, bars
input double InpRsiStopATR   = 2.0;        // Stop, ATR

input long   InpMagic        = 20261024;   // Magic number

CTrade   trade;
int      hATR, hATRH1, hEMA, hRSI, hSMA;
datetime lastBar = 0;
int      barsHeld = 0;
datetime orbTradedDay = 0;

int OnInit()
  {
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     { Alert("StratLab is a test EA and does not trade real accounts"); return INIT_FAILED; }
   hATR = iATR(_Symbol, PERIOD_CURRENT, 14);
   hATRH1 = iATR(_Symbol, PERIOD_H1, 14);
   hEMA = iMA(_Symbol, PERIOD_CURRENT, InpRsiTrendMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI = iRSI(_Symbol, PERIOD_CURRENT, 2, PRICE_CLOSE);
   hSMA = iMA(_Symbol, PERIOD_CURRENT, 5, 0, MODE_SMA, PRICE_CLOSE);
   if(hATR == INVALID_HANDLE || hATRH1 == INVALID_HANDLE || hEMA == INVALID_HANDLE || hRSI == INVALID_HANDLE || hSMA == INVALID_HANDLE)
      return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   return INIT_SUCCEEDED;
  }

double Buf(int handle, int shift)
  {
   double v[];
   return CopyBuffer(handle, 0, shift, 1, v) == 1 ? v[0] : 0;
  }

// Side of our open position: 1 long, -1 short, 0 none.
int PosSide()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol
         && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
   return 0;
  }

double Lots(double dist)
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(balance <= 0 || tickSize <= 0 || tickValue <= 0 || step <= 0 || dist <= 0) return 0;
   double perLot = dist / tickSize * tickValue;
   double lots = MathFloor(balance * InpRiskPct / 100.0 / perLot / step + 1e-9) * step;
   if(lots < vmin - 1e-12)
     {
      if(vmin * perLot / balance * 100.0 > InpMinLotMaxRisk) return 0;
      lots = vmin;
     }
   return MathMin(lots, vmax);
  }

void Open(int side, double stop, double target)
  {
   if(InpSides != 0 && side != InpSides) return;
   double price = side == 1 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lots = Lots(MathAbs(price - stop));
   if(lots <= 0) return;
   stop = NormalizeDouble(stop, _Digits);
   target = target > 0 ? NormalizeDouble(target, _Digits) : 0;
   bool ok = side == 1 ? trade.Buy(lots, _Symbol, 0, stop, target, "StratLab")
                       : trade.Sell(lots, _Symbol, 0, stop, target, "StratLab");
   if(ok) barsHeld = 0;
  }

void Orb(int pos)
  {
   MqlDateTime t;
   datetime now = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(now, t);
   datetime day = now - (now % 86400);
   if(pos != 0)
     {
      if(t.hour >= InpOrbClose) trade.PositionClose(_Symbol);
      return;
     }
   if(t.hour < InpOrbEnd || t.hour >= InpOrbClose || orbTradedDay == day) return;
   // range of today's bars from InpOrbStart to InpOrbEnd
   double hi = -DBL_MAX, lo = DBL_MAX;
   datetime from = day + InpOrbStart * 3600, to = day + InpOrbEnd * 3600;
   for(int i = 1; i < 500; i++)
     {
      datetime bt = iTime(_Symbol, PERIOD_CURRENT, i);
      if(bt < from) break;
      if(bt >= to) continue;
      hi = MathMax(hi, iHigh(_Symbol, PERIOD_CURRENT, i));
      lo = MathMin(lo, iLow(_Symbol, PERIOD_CURRENT, i));
     }
   if(hi <= lo || hi == -DBL_MAX) return;
   double c = iClose(_Symbol, PERIOD_CURRENT, 1);
   int side = c > hi ? 1 : (c < lo ? -1 : 0);
   if(side == 0) return;
   orbTradedDay = day;                                   // one attempt per day
   double h1atr = Buf(hATRH1, 1);
   if(InpOrbMaxATR > 0 && h1atr > 0 && hi - lo > InpOrbMaxATR * h1atr) return;
   double stop = side == 1 ? lo : hi;
   double entry = c, risk = MathAbs(entry - stop);
   Open(side, stop, InpOrbRR > 0 ? entry + side * InpOrbRR * risk : 0);
  }

void Donchian(int pos)
  {
   double c = iClose(_Symbol, PERIOD_CURRENT, 1);
   int hiE = iHighest(_Symbol, PERIOD_CURRENT, MODE_HIGH, InpDonEntry, 2);
   int loE = iLowest(_Symbol, PERIOD_CURRENT, MODE_LOW, InpDonEntry, 2);
   int hiX = iHighest(_Symbol, PERIOD_CURRENT, MODE_HIGH, InpDonExit, 2);
   int loX = iLowest(_Symbol, PERIOD_CURRENT, MODE_LOW, InpDonExit, 2);
   if(hiE < 0 || loE < 0 || hiX < 0 || loX < 0) return;
   if(pos == 1 && c < iLow(_Symbol, PERIOD_CURRENT, loX)) { trade.PositionClose(_Symbol); pos = 0; }
   if(pos == -1 && c > iHigh(_Symbol, PERIOD_CURRENT, hiX)) { trade.PositionClose(_Symbol); pos = 0; }
   if(pos != 0) return;
   double atr = Buf(hATR, 1);
   if(atr <= 0) return;
   if(c > iHigh(_Symbol, PERIOD_CURRENT, hiE)) Open(1, c - InpDonStopATR * atr, 0);
   else if(c < iLow(_Symbol, PERIOD_CURRENT, loE)) Open(-1, c + InpDonStopATR * atr, 0);
  }

void Rsi2(int pos)
  {
   double c = iClose(_Symbol, PERIOD_CURRENT, 1);
   if(pos != 0)
     {
      double sma = Buf(hSMA, 1);
      if(++barsHeld >= InpRsiMaxBars || (pos == 1 && c > sma) || (pos == -1 && c < sma))
         trade.PositionClose(_Symbol);
      return;
     }
   double ema = Buf(hEMA, 1), rsi = Buf(hRSI, 1), atr = Buf(hATR, 1);
   if(ema <= 0 || atr <= 0) return;
   if(c > ema && rsi < InpRsiLow) Open(1, c - InpRsiStopATR * atr, 0);
   else if(c < ema && rsi > InpRsiHigh) Open(-1, c + InpRsiStopATR * atr, 0);
  }

void OnTick()
  {
   datetime bar0 = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar0 == lastBar) return;
   lastBar = bar0;
   int pos = PosSide();
   if(InpStrategy == STRAT_ORB) Orb(pos);
   else if(InpStrategy == STRAT_DONCHIAN) Donchian(pos);
   else Rsi2(pos);
  }
