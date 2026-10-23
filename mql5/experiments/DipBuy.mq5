//+------------------------------------------------------------------+
//| DipBuy.mq5 - test EA for one mined pattern (gold M5):            |
//| after a fall of more than DropATR x ATR over the last 3 candles, |
//| buy at the next open, close after HoldBars candles.              |
//| Tester use only: it refuses to trade a real account.             |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

input double InpDropATR   = 2.0;       // Fall over the last 3 candles, in ATR(14)
input int    InpHoldBars  = 12;        // Candles to hold
input double InpStopATR   = 2.5;       // Disaster stop below entry, in ATR (0 = none)
input double InpLots      = 0.01;      // Fixed lot size
input long   InpMagic     = 20261023;  // Magic number

CTrade   trade;
int      atrHandle;
datetime lastBar = 0;
int      barsHeld = 0;

int OnInit()
  {
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     { Alert("DipBuy is a test EA and does not trade real accounts"); return INIT_FAILED; }
   atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14);
   if(atrHandle == INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   return INIT_SUCCEEDED;
  }

bool InTrade()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol
         && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
   return false;
  }

void OnTick()
  {
   datetime bar0 = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar0 == lastBar) return;
   lastBar = bar0;

   if(InTrade())
     {
      if(++barsHeld >= InpHoldBars) trade.PositionClose(_Symbol);
      return;
     }
   double atr[];
   if(CopyBuffer(atrHandle, 0, 1, 1, atr) != 1 || atr[0] <= 0) return;
   double c1 = iClose(_Symbol, PERIOD_CURRENT, 1), c4 = iClose(_Symbol, PERIOD_CURRENT, 4);
   if(c1 - c4 >= -InpDropATR * atr[0]) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double sl = InpStopATR > 0 ? NormalizeDouble(ask - InpStopATR * atr[0], _Digits) : 0;
   if(trade.Buy(InpLots, _Symbol, 0, sl, 0, "DipBuy"))
      barsHeld = 0;
  }
