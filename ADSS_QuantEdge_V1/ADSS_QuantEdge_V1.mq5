#property strict
#property version "1.00"
#include <Trade/Trade.mqh>
CTrade trade;

input ENUM_TIMEFRAMES SignalTF=PERIOD_M5;
input ENUM_TIMEFRAMES TrendTF=PERIOD_H1;
input int FastEMA=20, SlowEMA=50, TrendEMA=200, RSIPeriod=14, ADXPeriod=14, ATRPeriod=14;
input double MinADX=22, RiskPercent=0.50, SL_ATR=1.6, TP_ATR=2.4, Trail_ATR=1.2, BreakEven_R=1.0;
input double BuyRSIMin=52, BuyRSIMax=72, SellRSIMin=28, SellRSIMax=48;
input int MaxSpreadPoints=35, MaxPositions=1, CooldownBars=3;
input bool UseSessionFilter=true, AvoidFridayLate=true;
input int SessionStartHour=7, SessionEndHour=21, FridayCutoffHour=18;
input ulong Magic=26092601;

int hFast,hSlow,hTrend,hRSI,hADX,hATR; datetime lastBar=0,lastEntry=0;

bool B(int h,int buf,int sh,double &v){double a[];ArraySetAsSeries(a,true);if(CopyBuffer(h,buf,sh,1,a)!=1)return false;v=a[0];return true;}
double PV(){double tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE),ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);return ts>0?tv/ts:0;}
double Lots(double e,double s){double risk=AccountInfoDouble(ACCOUNT_EQUITY)*RiskPercent/100.0,d=MathAbs(e-s),pv=PV();if(risk<=0||d<=0||pv<=0)return 0;double v=risk/(d*pv),mn=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),mx=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),st=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);v=MathMax(mn,MathMin(mx,v));return NormalizeDouble(MathFloor(v/st)*st,(int)SymbolInfoInteger(_Symbol,SYMBOL_VOLUME_DIGITS));}
bool Session(){if(!UseSessionFilter)return true;MqlDateTime t;TimeToStruct(TimeTradeServer(),t);if(AvoidFridayLate&&t.day_of_week==5&&t.hour>=FridayCutoffHour)return false;if(SessionStartHour<=SessionEndHour)return t.hour>=SessionStartHour&&t.hour<SessionEndHour;return t.hour>=SessionStartHour||t.hour<SessionEndHour;}
bool Spread(){MqlTick x;if(!SymbolInfoTick(_Symbol,x))return false;return (x.ask-x.bid)/_Point<=MaxSpreadPoints;}
int Pos(){int n=0;for(int i=PositionsTotal()-1;i>=0;i--){ulong t=PositionGetTicket(i);if(t&&PositionGetString(POSITION_SYMBOL)==_Symbol&&(ulong)PositionGetInteger(POSITION_MAGIC)==Magic)n++;}return n;}
bool Bar(){datetime t=iTime(_Symbol,SignalTF,0);if(!t||t==lastBar)return false;lastBar=t;return true;}

bool Signal(bool buy){
 double f,s,tr,rsi,adx,atr,ht; if(!B(hFast,0,1,f)||!B(hSlow,0,1,s)||!B(hTrend,0,1,tr)||!B(hRSI,0,1,rsi)||!B(hADX,0,1,adx)||!B(hATR,0,1,atr))return false;
 int hh=iMA(_Symbol,TrendTF,TrendEMA,0,MODE_EMA,PRICE_CLOSE);if(hh==INVALID_HANDLE)return false;bool ok=B(hh,0,1,ht);IndicatorRelease(hh);if(!ok)return false;
 double c1=iClose(_Symbol,SignalTF,1),c2=iClose(_Symbol,SignalTF,2),fp;if(!B(hFast,0,2,fp))return false;
 if(buy)return c1>tr&&c1>ht&&f>s&&adx>=MinADX&&rsi>=BuyRSIMin&&rsi<=BuyRSIMax&&(c2<=fp||c1>f)&&c1>iHigh(_Symbol,SignalTF,2);
 return c1<tr&&c1<ht&&f<s&&adx>=MinADX&&rsi>=SellRSIMin&&rsi<=SellRSIMax&&(c2>=fp||c1<f)&&c1<iLow(_Symbol,SignalTF,2);
}

void Manage(){
 double atr;if(!B(hATR,0,0,atr)||atr<=0)return;MqlTick x;if(!SymbolInfoTick(_Symbol,x))return;
 for(int i=PositionsTotal()-1;i>=0;i--){ulong t=PositionGetTicket(i);if(!t||PositionGetString(POSITION_SYMBOL)!=_Symbol||(ulong)PositionGetInteger(POSITION_MAGIC)!=Magic)continue;
  long ty=PositionGetInteger(POSITION_TYPE);double op=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL),tp=PositionGetDouble(POSITION_TP),p=ty==POSITION_TYPE_BUY?x.bid:x.ask,r=MathAbs(op-sl),g=ty==POSITION_TYPE_BUY?p-op:op-p;if(r<=0)continue;
  if(g>=BreakEven_R*r){if(ty==POSITION_TYPE_BUY&&(sl==0||op>sl))trade.PositionModify(t,op,tp);if(ty==POSITION_TYPE_SELL&&(sl==0||op<sl))trade.PositionModify(t,op,tp);}
  if(g>=r){double ns=ty==POSITION_TYPE_BUY?p-Trail_ATR*atr:p+Trail_ATR*atr;if(ty==POSITION_TYPE_BUY&&ns>sl&&ns>op)trade.PositionModify(t,ns,tp);if(ty==POSITION_TYPE_SELL&&(sl==0||ns<sl)&&ns<op)trade.PositionModify(t,ns,tp);}
 }
}

bool Open(bool buy){
 MqlTick x;if(!SymbolInfoTick(_Symbol,x))return false;double atr;if(!B(hATR,0,1,atr)||atr<=0)return false;double e=buy?x.ask:x.bid,sl=buy?e-SL_ATR*atr:e+SL_ATR*atr,tp=buy?e+TP_ATR*atr:e-TP_ATR*atr;int st=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);double md=st*_Point;if(buy){if(e-sl<md)sl=e-md;if(tp-e<md)tp=e+md;}else{if(sl-e<md)sl=e+md;if(e-tp<md)tp=e-md;}double v=Lots(e,sl);if(v<=0)return false;trade.SetExpertMagicNumber(Magic);trade.SetDeviationInPoints(10);bool ok=buy?trade.Buy(v,_Symbol,0,sl,tp,"ADSS-QE-LONG"):trade.Sell(v,_Symbol,0,sl,tp,"ADSS-QE-SHORT");if(ok)lastEntry=TimeTradeServer();return ok;
}

int OnInit(){hFast=iMA(_Symbol,SignalTF,FastEMA,0,MODE_EMA,PRICE_CLOSE);hSlow=iMA(_Symbol,SignalTF,SlowEMA,0,MODE_EMA,PRICE_CLOSE);hTrend=iMA(_Symbol,SignalTF,TrendEMA,0,MODE_EMA,PRICE_CLOSE);hRSI=iRSI(_Symbol,SignalTF,RSIPeriod,PRICE_CLOSE);hADX=iADX(_Symbol,SignalTF,ADXPeriod);hATR=iATR(_Symbol,SignalTF,ATRPeriod);if(hFast==INVALID_HANDLE||hSlow==INVALID_HANDLE||hTrend==INVALID_HANDLE||hRSI==INVALID_HANDLE||hADX==INVALID_HANDLE||hATR==INVALID_HANDLE)return INIT_FAILED;trade.SetExpertMagicNumber(Magic);return INIT_SUCCEEDED;}
void OnDeinit(const int r){IndicatorRelease(hFast);IndicatorRelease(hSlow);IndicatorRelease(hTrend);IndicatorRelease(hRSI);IndicatorRelease(hADX);IndicatorRelease(hATR);}
void OnTick(){Manage();if(!Bar()||Pos()>=MaxPositions||!Session()||!Spread())return;if(lastEntry>0){int b=iBarShift(_Symbol,SignalTF,lastEntry,false);if(b>=0&&b<CooldownBars)return;}if(Signal(true))Open(true);else if(Signal(false))Open(false);}
