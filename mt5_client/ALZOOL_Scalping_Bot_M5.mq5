//+------------------------------------------------------------------+
//| AL-ZOOL Independent Gold Scalper - M5                            |
//| Standalone MT5 Expert Advisor. Closed-candle decisions only.     |
//| No grid, martingale, averaging, or forced fixed TP/SL by default. |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "AL-ZOOL independent XAUUSD M5 scalping EA"

#include <Trade/Trade.mqh>
CTrade trade;

input group "General"
input string InpTradeSymbol = "";          // Empty = chart symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M5;
input ulong InpMagic = 26111301;
input double InpLots = 0.01;
input int InpMaxPositions = 1;
input int InpMinVotes = 3;                  // Required votes out of 4
input bool InpBlockOppositePositions = true;
input bool InpAllowBuy = true;
input bool InpAllowSell = true;
input int InpDeviationPoints = 20;

input group "Scalping Strategy"
input int InpFastEMA = 9;
input int InpSlowEMA = 21;
input int InpRSIPeriod = 7;
input double InpRSIBuyLevel = 52.0;
input double InpRSISellLevel = 48.0;
input int InpMACDFast = 12;
input int InpMACDSlow = 26;
input int InpMACDSignal = 9;
input int InpVolumeLookback = 20;
input double InpVolumeMultiplier = 1.10;

input group "Optional Risk Controls (OFF by default)"
input bool InpUseStopLoss = false;
input int InpStopLossPoints = 0;
input bool InpUseTakeProfit = false;
input int InpTakeProfitPoints = 0;

int hFast=INVALID_HANDLE, hSlow=INVALID_HANDLE, hRSI=INVALID_HANDLE;
int hMACD=INVALID_HANDLE;
datetime lastBar=0;
string symbolName;

double NormalizeLots(double lots)
{
   double vmin=SymbolInfoDouble(symbolName,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(symbolName,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(symbolName,SYMBOL_VOLUME_STEP);
   if(step<=0) return 0;
   lots=MathMax(vmin,MathMin(vmax,lots));
   lots=MathFloor(lots/step+1e-8)*step;
   int d=2;
   if(step<0.01) d=3;
   if(step<0.001) d=4;
   return NormalizeDouble(lots,d);
}

int CountPositions(int &buys,int &sells)
{
   buys=0; sells=0; int own=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(type==POSITION_TYPE_BUY) buys++;
      if(type==POSITION_TYPE_SELL) sells++;
      if(PositionGetString(POSITION_SYMBOL)==symbolName &&
         (ulong)PositionGetInteger(POSITION_MAGIC)==InpMagic) own++;
   }
   return own;
}

bool ReadValue(const int handle,const int buffer,const int shift,double &value)
{
   double a[];
   ArraySetAsSeries(a,true);
   if(handle==INVALID_HANDLE || CopyBuffer(handle,buffer,shift,1,a)!=1) return false;
   value=a[0];
   return MathIsValidNumber(value);
}

bool IsNewBar()
{
   datetime t=iTime(symbolName,InpTimeframe,0);
   if(t<=0 || t==lastBar) return false;
   lastBar=t;
   return true;
}

void EvaluateAndTrade()
{
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   if(CopyRates(symbolName,InpTimeframe,0,InpVolumeLookback+3,rates)<InpVolumeLookback+3)
   { Print("AL-ZOOL Scalper: waiting for candle history"); return; }

   // All signal inputs use the last fully closed candle (shift 1).
   double fast,slow,rsi,macdMain,macdSignal;
   if(!ReadValue(hFast,0,1,fast) || !ReadValue(hSlow,0,1,slow) ||
      !ReadValue(hRSI,0,1,rsi) || !ReadValue(hMACD,0,1,macdMain) ||
      !ReadValue(hMACD,1,1,macdSignal))
   { Print("AL-ZOOL Scalper: indicator data unavailable"); return; }

   int buyVotes=0,sellVotes=0;
   // Vote 1: EMA trend
   if(fast>slow) buyVotes++; else if(fast<slow) sellVotes++;
   // Vote 2: RSI momentum
   if(rsi>=InpRSIBuyLevel) buyVotes++; else if(rsi<=InpRSISellLevel) sellVotes++;
   // Vote 3: MACD momentum
   if(macdMain>macdSignal) buyVotes++; else if(macdMain<macdSignal) sellVotes++;
   // Vote 4: closed candle body and relative tick volume
   double avgVol=0;
   for(int i=2;i<InpVolumeLookback+2;i++) avgVol+=(double)rates[i].tick_volume;
   avgVol/=InpVolumeLookback;
   bool volumeOK=((double)rates[1].tick_volume>=avgVol*InpVolumeMultiplier);
   if(volumeOK && rates[1].close>rates[1].open) buyVotes++;
   else if(volumeOK && rates[1].close<rates[1].open) sellVotes++;

   string direction="";
   if(InpAllowBuy && buyVotes>=InpMinVotes && buyVotes>sellVotes) direction="BUY";
   if(InpAllowSell && sellVotes>=InpMinVotes && sellVotes>buyVotes) direction="SELL";
   if(direction=="")
   {
      Print("AL-ZOOL Scalper M5: WAIT | BUY votes=",buyVotes,
            " SELL votes=",sellVotes," RSI=",DoubleToString(rsi,1));
      return;
   }

   int accountBuys=0,accountSells=0;
   int ownPositions=CountPositions(accountBuys,accountSells);
   if(ownPositions>=InpMaxPositions)
   { Print("AL-ZOOL Scalper: max positions reached"); return; }

   if(InpBlockOppositePositions)
   {
      if(accountBuys>0 && accountSells>0) { Print("AL-ZOOL: mixed account exposure; entry blocked"); return; }
      if(direction=="BUY" && accountSells>0) { Print("AL-ZOOL: opposite SELL exists; BUY blocked"); return; }
      if(direction=="SELL" && accountBuys>0) { Print("AL-ZOOL: opposite BUY exists; SELL blocked"); return; }
   }

   MqlTick tick;
   if(!SymbolInfoTick(symbolName,tick) || tick.ask<=0 || tick.bid<=0) return;
   double point=SymbolInfoDouble(symbolName,SYMBOL_POINT);
   int digits=(int)SymbolInfoInteger(symbolName,SYMBOL_DIGITS);
   double sl=0,tp=0;
   if(InpUseStopLoss && InpStopLossPoints>0)
      sl=NormalizeDouble(direction=="BUY" ? tick.ask-InpStopLossPoints*point : tick.bid+InpStopLossPoints*point,digits);
   if(InpUseTakeProfit && InpTakeProfitPoints>0)
      tp=NormalizeDouble(direction=="BUY" ? tick.ask+InpTakeProfitPoints*point : tick.bid-InpTakeProfitPoints*point,digits);

   double lots=NormalizeLots(InpLots);
   if(lots<=0) { Print("AL-ZOOL Scalper: invalid lot size"); return; }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   trade.SetTypeFillingBySymbol(symbolName);
   bool sent=false;
   if(direction=="BUY") sent=trade.Buy(lots,symbolName,0,sl,tp,"ALZOOL-SCALPER-M5");
   else sent=trade.Sell(lots,symbolName,0,sl,tp,"ALZOOL-SCALPER-M5");

   if(sent && (trade.ResultRetcode()==TRADE_RETCODE_DONE ||
               trade.ResultRetcode()==TRADE_RETCODE_PLACED))
      Print("AL-ZOOL Scalper ",direction," opened | votes ",
            direction=="BUY" ? buyVotes : sellVotes,"/4 | RSI=",DoubleToString(rsi,1));
   else
      Print("AL-ZOOL Scalper order failed | retcode=",trade.ResultRetcode(),
            " | ",trade.ResultRetcodeDescription());
}

int OnInit()
{
   symbolName=(InpTradeSymbol=="") ? _Symbol : InpTradeSymbol;
   if(!SymbolSelect(symbolName,true)) return INIT_FAILED;
   if(InpFastEMA<2 || InpSlowEMA<=InpFastEMA || InpRSIPeriod<2 ||
      InpMinVotes<1 || InpMinVotes>4 || InpVolumeLookback<2 ||
      InpMaxPositions<1 || InpLots<=0) return INIT_PARAMETERS_INCORRECT;

   hFast=iMA(symbolName,InpTimeframe,InpFastEMA,0,MODE_EMA,PRICE_CLOSE);
   hSlow=iMA(symbolName,InpTimeframe,InpSlowEMA,0,MODE_EMA,PRICE_CLOSE);
   hRSI=iRSI(symbolName,InpTimeframe,InpRSIPeriod,PRICE_CLOSE);
   hMACD=iMACD(symbolName,InpTimeframe,InpMACDFast,InpMACDSlow,InpMACDSignal,PRICE_CLOSE);
   if(hFast==INVALID_HANDLE || hSlow==INVALID_HANDLE ||
      hRSI==INVALID_HANDLE || hMACD==INVALID_HANDLE) return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   lastBar=iTime(symbolName,InpTimeframe,0);
   Print("AL-ZOOL Independent Gold Scalper M5 initialized on ",symbolName,
         ". Signals use closed candles; SL/TP are OFF unless enabled.");
   return INIT_SUCCEEDED;
}

void OnTick()
{
   if(IsNewBar()) EvaluateAndTrade();
}

void OnDeinit(const int reason)
{
   if(hFast!=INVALID_HANDLE) IndicatorRelease(hFast);
   if(hSlow!=INVALID_HANDLE) IndicatorRelease(hSlow);
   if(hRSI!=INVALID_HANDLE) IndicatorRelease(hRSI);
   if(hMACD!=INVALID_HANDLE) IndicatorRelease(hMACD);
}
