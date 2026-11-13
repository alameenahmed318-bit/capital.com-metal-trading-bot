//+------------------------------------------------------------------+
//| AL-ZOOL MT5 LIVE DATA CLIENT                                      |
//| Sends MT5 telemetry and applies user-configured profit closes.    |
//| Existing licence protocol retained; closes are evaluated locally. |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

CTrade g_trade;
#property version "2.31"
#property description "AL-ZOOL MT5 telemetry and local profit protection"

input group "بيانات الاتصال بالخادم"
input string InpServerUrl = "https://al-zool-gold-sniper.onrender.com";
input string InpEngineId = "";
input string InpEngineToken = "";
input int InpHttpTimeoutMs = 8000;
input int InpHeartbeatSeconds = 5;
input int InpCandlesToSend = 120;
input group "إعدادات تنفيذ الدخول المركزي"
input bool InpEnableCentralTrading = true;
input double InpTradeLots = 0.01;
input ulong InpTradeMagic = 26110801;
input int InpMaxOpenPositions = 10;
input bool InpBlockOppositePositions = true;
input int InpATRPeriod = 14;
input double InpStopLossATR = 1.8;
input bool InpUseTrailing = true;
input double InpTrailStartATR = 1.0;
input double InpTrailDistanceATR = 0.8;
input int InpDeviationPoints = 20;
input group "الأرشيف التاريخي للاختبار"
input int InpHistoricalCandlesToSend = 5000;
input int InpHistoryUploadMinutes = 15;

bool g_online=false;
bool g_serverTradingEnabled=false;
datetime g_lastHistoryAttempt=0;
string g_status="بدء الاتصال";
bool g_singleCloseEnabled=false,g_groupCloseEnabled=false;
double g_singleProfitTarget=1.0,g_groupProfitTarget=5.0;
bool g_serverStopLossEnabled=true;
string g_serverStopLossMode="ATR";
double g_serverStopLossValue=1.8;
string g_pmMode="AI";
bool g_addonEnabled=false,g_coolingEnabled=false;
int g_addonMax=0,g_coolingMax=0;
double g_addonManualLot=0.01,g_coolingManualLot=0.01;
double g_addonManualDistance=0.0,g_coolingManualDistance=0.0;
int g_emaFastHandle=INVALID_HANDLE,g_emaSlowHandle=INVALID_HANDLE;
ulong g_lastSnapshotMs=0,g_lastSettingsPollMs=0,g_lastDecisionPollMs=0;
string g_aiSignal="WAIT",g_aiReason="بانتظار إشارة المحرك المركزي";
double g_aiConfidence=0.0;
long g_aiBarTime=0;
int g_aiStrategyId=0;
int g_atrHandle=INVALID_HANDLE;
string g_executionBarKey="";
void ExecuteCentralEntry(const string direction,const long decisionBar,const double confidence,const int strategyId,const string reason);

string BaseUrl()
{
   string u=InpServerUrl;
   while(StringLen(u)>0 && StringGetCharacter(u,StringLen(u)-1)==47)
      u=StringSubstr(u,0,StringLen(u)-1);
   return u;
}
string JsonEscape(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r"," ");
   StringReplace(s,"\n"," ");
   StringReplace(s,"\t"," ");
   return s;
}
string JNum(double value,int digits=2)
{
   if(!MathIsValidNumber(value)) value=0.0;
   return DoubleToString(value,digits);
}
bool HttpGet(const string url)
{
   char data[],result[];
   string headers,response_headers;
   ArrayResize(data,0);
   ResetLastError();
   int code=WebRequest("GET",url,headers,InpHttpTimeoutMs,data,result,response_headers);
   if(code!=200)
   {
      g_status="الخادم غير متاح؛ HTTP="+IntegerToString(code)+" err="+IntegerToString(GetLastError());
      return false;
   }
   return true;
}
string JsonField(const string json,const string key)
{
   // Accept normal JSON spacing (for example: "single_enabled": false)
   // as well as compact JSON. The backend serializer inserts a space after ':'.
   string needle="\""+key+"\"";
   int p=StringFind(json,needle);
   if(p<0) return "";
   p+=StringLen(needle);
   while(p<StringLen(json) &&
         (StringGetCharacter(json,p)==32 || StringGetCharacter(json,p)==9 ||
          StringGetCharacter(json,p)==10 || StringGetCharacter(json,p)==13)) p++;
   if(p>=StringLen(json) || StringGetCharacter(json,p)!=58) return "";
   p++;
   while(p<StringLen(json) &&
         (StringGetCharacter(json,p)==32 || StringGetCharacter(json,p)==9 ||
          StringGetCharacter(json,p)==10 || StringGetCharacter(json,p)==13)) p++;
   int e=p;
   while(e<StringLen(json) && StringGetCharacter(json,e)!=44 &&
         StringGetCharacter(json,e)!=125 && StringGetCharacter(json,e)!=93) e++;
   return StringSubstr(json,p,e-p);
}
bool JsonBool(const string json,const string key)
{
   string v=JsonField(json,key);
   return (StringFind(v,"true")==0 || StringFind(v,"1")==0);
}
double JsonDouble(const string json,const string key,double fallback)
{
   string v=JsonField(json,key);
   if(StringLen(v)==0) return fallback;
   double n=StringToDouble(v);
   return (MathIsValidNumber(n) ? n : fallback);
}
bool PollTradeSettings()
{
   g_serverTradingEnabled=false;
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20) return false;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Authorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",BaseUrl()+"/v1/terminal-control/settings/"+InpEngineId,
                       headers,1500,data,result,response_headers);
   if(code!=200)
   {
      Print("AL-ZOOL trade settings poll failed: HTTP=",code," err=",GetLastError());
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   g_serverTradingEnabled=JsonBool(response,"trading_enabled");
   g_singleCloseEnabled=JsonBool(response,"single_enabled");
   g_groupCloseEnabled=JsonBool(response,"group_enabled");
   g_singleProfitTarget=JsonDouble(response,"single_profit_target",1.0);
   g_groupProfitTarget=JsonDouble(response,"group_profit_target",5.0);
   if(g_singleProfitTarget<=0) g_singleCloseEnabled=false;
   if(g_groupProfitTarget<=0) g_groupCloseEnabled=false;
   string slEnabled=JsonField(response,"stop_loss_enabled");
   if(StringLen(slEnabled)>0) g_serverStopLossEnabled=(StringFind(slEnabled,"true")==0 || StringFind(slEnabled,"1")==0);
   string slMode=JsonField(response,"stop_loss_mode");
   StringReplace(slMode,"\"","");
   if(slMode=="POINTS" || slMode=="ATR") g_serverStopLossMode=slMode;
   double slValue=JsonDouble(response,"stop_loss_value",g_serverStopLossValue);
   if(slValue>0.0) g_serverStopLossValue=slValue;
   string pmMode=JsonField(response,"mode");
   StringReplace(pmMode,CharToString(34),"");
   if(pmMode=="AI" || pmMode=="MANUAL") g_pmMode=pmMode;
   g_addonEnabled=JsonBool(response,"addon_enabled");
   g_coolingEnabled=JsonBool(response,"cooling_enabled");
   g_addonMax=(int)MathMax(0,MathMin(100,JsonDouble(response,"addon_max",0)));
   g_coolingMax=(int)MathMax(0,MathMin(100,JsonDouble(response,"cooling_max",0)));
   g_addonManualLot=JsonDouble(response,"addon_lot",0.01);
   g_coolingManualLot=JsonDouble(response,"cooling_lot",0.01);
   g_addonManualDistance=JsonDouble(response,"addon_distance_points",0);
   g_coolingManualDistance=JsonDouble(response,"cooling_distance_points",0);
   return true;
}
bool PollCentralDecision()
{
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20) return false;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Authorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",BaseUrl()+"/v1/terminal-decision/"+InpEngineId,
                       headers,1500,data,result,response_headers);
   if(code!=200)
   {
      if(code!=409) Print("AL-ZOOL central decision poll failed: HTTP=",code," err=",GetLastError());
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   string signal=JsonField(response,"signal");
   string reason=JsonField(response,"reason_ar");
   string confidence=JsonField(response,"confidence");
   string bar_time=JsonField(response,"bar_time");
   string strategy_id=JsonField(response,"strategy_id");
   string execution_ready=JsonField(response,"execution_ready");
   StringReplace(signal,"\"","");
   StringReplace(reason,"\"","");
   if(signal!="BUY" && signal!="SELL") signal="WAIT";
   double parsed_confidence=StringToDouble(confidence);
   long parsed_bar=(long)StringToInteger(bar_time);
   int parsed_strategy=(int)StringToInteger(strategy_id);
   bool ready=(StringFind(execution_ready,"true")==0);
   if(parsed_bar!=g_aiBarTime || signal!=g_aiSignal)
      Print("AL-ZOOL CENTRAL AI: ",signal," | confidence=",DoubleToString(parsed_confidence*100.0,1),
            "% | ",reason);
   g_aiSignal=signal;
   g_aiReason=reason;
   g_aiConfidence=MathMax(0.0,MathMin(1.0,parsed_confidence));
   g_aiBarTime=parsed_bar;
   g_aiStrategyId=parsed_strategy;
   if(ready && InpEnableCentralTrading && g_serverTradingEnabled) ExecuteCentralEntry(signal,parsed_bar,parsed_confidence,parsed_strategy,reason);
   return true;
}

double NormalizeTradeLots(double lots)
{
   double minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),maxv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0.0) return 0.0;
   lots=MathMax(minv,MathMin(maxv,lots)); lots=MathFloor(lots/step+1e-8)*step;
   int digits=2; if(step<0.01) digits=3; if(step<0.001) digits=4;
   return NormalizeDouble(lots,digits);
}
int CountAlzoolPositions()
{
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC)==InpTradeMagic) count++;
   }
   return count;
}
// Returns account-wide open BUY/SELL counts, regardless of symbol or magic number.
void GetAccountDirectionCounts(int &buyPositions,int &sellPositions)
{
   buyPositions=0; sellPositions=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(type==POSITION_TYPE_BUY) buyPositions++;
      else if(type==POSITION_TYPE_SELL) sellPositions++;
   }
}
bool IsAccountDirectionAllowed(const string direction)
{
   if(!InpBlockOppositePositions) return true;
   int buys=0,sells=0;
   GetAccountDirectionCounts(buys,sells);
   // If both sides already exist, fail closed: do not add any new exposure.
   if(buys>0 && sells>0) return false;
   if(direction=="BUY") return (sells==0);
   if(direction=="SELL") return (buys==0);
   return false;
}
void ExecuteCentralEntry(const string direction,const long decisionBar,const double confidence,
                         const int strategyId,const string reason)
{
   if(!InpEnableCentralTrading || (direction!="BUY" && direction!="SELL") ||
      confidence<0.55 || strategyId<1 || strategyId>200 || decisionBar<=0) return;
   if(!IsAccountDirectionAllowed(direction))
   { g_status="قفل الاتجاه الواحد: توجد صفقات في الاتجاه المعاكس"; return; }
   datetime currentBar=iTime(_Symbol,(ENUM_TIMEFRAMES)_Period,0);
   if(currentBar<=0 || (long)currentBar<=decisionBar) return;
   if(g_executionBarKey!="" && GlobalVariableCheck(g_executionBarKey) &&
      (long)GlobalVariableGet(g_executionBarKey)==decisionBar) return;
   if(g_executionBarKey!="") GlobalVariableSet(g_executionBarKey,(double)decisionBar);
   if(CountAlzoolPositions()>=InpMaxOpenPositions) { g_status="الحد الأقصى للصفقات مفتوح"; return; }
   double lots=NormalizeTradeLots(InpTradeLots);
   if(lots<=0.0) { g_status="حجم اللوت غير صالح"; return; }
   double atr[];
   ArraySetAsSeries(atr,true);
   if(g_atrHandle==INVALID_HANDLE || CopyBuffer(g_atrHandle,0,1,1,atr)!=1 || atr[0]<=0.0)
   { g_status="بيانات ATR غير متاحة؛ تم إلغاء الدخول"; return; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<tick.bid) return;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double minDist=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double slDist=0.0;
   if(g_serverStopLossEnabled)
   {
      slDist=(g_serverStopLossMode=="POINTS")
             ? g_serverStopLossValue*_Point
             : atr[0]*g_serverStopLossValue;
      slDist=MathMax(slDist,minDist+_Point);
   }
   double sl=0.0,tp=0.0;
   g_trade.SetExpertMagicNumber(InpTradeMagic);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   bool sent=false;
   if(direction=="BUY")
   {
      if(g_serverStopLossEnabled) sl=NormalizeDouble(tick.ask-slDist,digits);
      sent=g_trade.Buy(lots,_Symbol,0.0,sl,tp,"ALZOOL-S"+IntegerToString(strategyId));
   }
   else
   {
      if(g_serverStopLossEnabled) sl=NormalizeDouble(tick.bid+slDist,digits);
      sent=g_trade.Sell(lots,_Symbol,0.0,sl,tp,"ALZOOL-S"+IntegerToString(strategyId));
   }
   uint rc=g_trade.ResultRetcode();
   if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED || rc==TRADE_RETCODE_DONE_PARTIAL))
   {
      g_status=direction+" تم فتح الصفقة | استراتيجية "+IntegerToString(strategyId);
      Print("AL-ZOOL ENTRY ",direction," strategy=",strategyId," confidence=",DoubleToString(confidence*100,1),
            "% bar=",decisionBar," reason=",reason);
   }
   else
   {
      g_status="رفض الوسيط أمر الدخول: "+g_trade.ResultRetcodeDescription();
      Print("AL-ZOOL ENTRY REJECTED: ",g_trade.ResultRetcode()," ",g_trade.ResultRetcodeDescription());
   }
}
void ManagePositionAddons()
{
   if(!g_serverTradingEnabled || !InpEnableCentralTrading ||
      (!g_addonEnabled && !g_coolingEnabled) || g_atrHandle==INVALID_HANDLE) return;
   double atr[]; ArraySetAsSeries(atr,true);
   if(CopyBuffer(g_atrHandle,0,1,1,atr)!=1 || atr[0]<=0.0) return;
   MqlTick tick; if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<tick.bid) return;

   int buyCount=0,sellCount=0,addonCount=0,coolingCount=0;
   int buyAddonCount=0,sellAddonCount=0;
   double buyNet=0.0,sellNet=0.0,lastBuyPrice=0.0,lastSellPrice=0.0;
   datetime lastBuyTime=0,lastSellTime=0;
   double baseBuyLot=0.0,baseSellLot=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=InpTradeMagic) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      string comment=PositionGetString(POSITION_COMMENT);
      double volume=PositionGetDouble(POSITION_VOLUME);
      datetime opened=(datetime)PositionGetInteger(POSITION_TIME);
      if(StringFind(comment,"ALZOOL-ADD")==0)
      {
         addonCount++;
         if(type==POSITION_TYPE_BUY) buyAddonCount++;
         else if(type==POSITION_TYPE_SELL) sellAddonCount++;
      }
      if(StringFind(comment,"ALZOOL-COOL")==0) coolingCount++;
      if(type==POSITION_TYPE_BUY)
      {
         buyCount++; buyNet+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
         if(opened>=lastBuyTime){lastBuyTime=opened;lastBuyPrice=PositionGetDouble(POSITION_PRICE_OPEN);}
         if(baseBuyLot==0.0 && StringFind(comment,"ALZOOL-ADD")<0 && StringFind(comment,"ALZOOL-COOL")<0) baseBuyLot=volume;
      }
      else if(type==POSITION_TYPE_SELL)
      {
         sellCount++; sellNet+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
         if(opened>=lastSellTime){lastSellTime=opened;lastSellPrice=PositionGetDouble(POSITION_PRICE_OPEN);}
         if(baseSellLot==0.0 && StringFind(comment,"ALZOOL-ADD")<0 && StringFind(comment,"ALZOOL-COOL")<0) baseSellLot=volume;
      }
   }
   if(PositionsTotal()>=InpMaxOpenPositions) return;
   // Add-ons/cooling must follow the one-way direction lock too.
   int accountBuys=0,accountSells=0;
   GetAccountDirectionCounts(accountBuys,accountSells);
   if(InpBlockOppositePositions && accountBuys>0 && accountSells>0) return;
   double emaFast[],emaSlow[]; ArraySetAsSeries(emaFast,true); ArraySetAsSeries(emaSlow,true);
   bool haveTrend=(g_emaFastHandle!=INVALID_HANDLE && g_emaSlowHandle!=INVALID_HANDLE &&
                   CopyBuffer(g_emaFastHandle,0,1,1,emaFast)==1 &&
                   CopyBuffer(g_emaSlowHandle,0,1,1,emaSlow)==1);
   string chosen=""; ENUM_POSITION_TYPE side=POSITION_TYPE_BUY;
   double lots=0.0,distancePoints=0.0,lastPrice=0.0,baseLot=0.0;
   int sideCount=0;
   if(g_pmMode=="AI")
   {
      if(!haveTrend) return;
      bool trendUp=emaFast[0]>emaSlow[0];
      // AI classifies continuation pullbacks as add-ons and counter-trend drawdown as cooling.
      if(buyCount>0 && buyNet<0.0 && tick.ask<lastBuyPrice && trendUp && g_addonEnabled && addonCount<g_addonMax)
      { chosen="ADD";side=POSITION_TYPE_BUY;lastPrice=lastBuyPrice;baseLot=baseBuyLot;sideCount=buyCount;distancePoints=(atr[0]*0.8)/_Point; }
      else if(sellCount>0 && sellNet<0.0 && tick.bid>lastSellPrice && !trendUp && g_addonEnabled && addonCount<g_addonMax)
      { chosen="ADD";side=POSITION_TYPE_SELL;lastPrice=lastSellPrice;baseLot=baseSellLot;sideCount=sellCount;distancePoints=(atr[0]*0.8)/_Point; }
      else if(buyCount>0 && buyNet<0.0 && tick.ask<lastBuyPrice && !trendUp && g_coolingEnabled && coolingCount<g_coolingMax)
      { chosen="COOL";side=POSITION_TYPE_BUY;lastPrice=lastBuyPrice;baseLot=baseBuyLot;sideCount=buyCount;distancePoints=(atr[0]*1.2)/_Point; }
      else if(sellCount>0 && sellNet<0.0 && tick.bid>lastSellPrice && trendUp && g_coolingEnabled && coolingCount<g_coolingMax)
      { chosen="COOL";side=POSITION_TYPE_SELL;lastPrice=lastSellPrice;baseLot=baseSellLot;sideCount=sellCount;distancePoints=(atr[0]*1.2)/_Point; }
      if(chosen!="")
      {
         if(chosen=="ADD")
         {
            // Linear AI add-on sizing: 0.01, 0.02, 0.03, 0.04 ... per direction.
            int nextAddon=(side==POSITION_TYPE_BUY?buyAddonCount:sellAddonCount)+1;
            lots=NormalizeTradeLots(0.01*nextAddon);
         }
         else
         {
            double volatilityRatio=atr[0]/MathMax(1.0,(tick.bid+tick.ask)*0.5);
            double factor=(volatilityRatio>0.001?0.40:(volatilityRatio>0.0005?0.55:0.70));
            lots=NormalizeTradeLots(baseLot*factor);
         }
      }
   }
   else
   {
      if(buyCount>0 && buyNet<0.0 && tick.ask<lastBuyPrice)
      {
         double adverseBuy=(lastBuyPrice-tick.ask)/_Point;
         if(g_addonEnabled && addonCount<g_addonMax && adverseBuy>=g_addonManualDistance)
         {chosen="ADD";side=POSITION_TYPE_BUY;lastPrice=lastBuyPrice;lots=NormalizeTradeLots(g_addonManualLot);distancePoints=g_addonManualDistance;}
         else if(g_coolingEnabled && coolingCount<g_coolingMax && adverseBuy>=g_coolingManualDistance)
         {chosen="COOL";side=POSITION_TYPE_BUY;lastPrice=lastBuyPrice;lots=NormalizeTradeLots(g_coolingManualLot);distancePoints=g_coolingManualDistance;}
      }
      if(chosen=="" && sellCount>0 && sellNet<0.0 && tick.bid>lastSellPrice)
      {
         double adverseSell=(tick.bid-lastSellPrice)/_Point;
         if(g_addonEnabled && addonCount<g_addonMax && adverseSell>=g_addonManualDistance)
         {chosen="ADD";side=POSITION_TYPE_SELL;lastPrice=lastSellPrice;lots=NormalizeTradeLots(g_addonManualLot);distancePoints=g_addonManualDistance;}
         else if(g_coolingEnabled && coolingCount<g_coolingMax && adverseSell>=g_coolingManualDistance)
         {chosen="COOL";side=POSITION_TYPE_SELL;lastPrice=lastSellPrice;lots=NormalizeTradeLots(g_coolingManualLot);distancePoints=g_coolingManualDistance;}
      }
   }
   if(chosen=="" || lots<=0.0 || distancePoints<=0.0 || lastPrice<=0.0) return;
   if(InpBlockOppositePositions && side==POSITION_TYPE_BUY && accountSells>0) return;
   if(InpBlockOppositePositions && side==POSITION_TYPE_SELL && accountBuys>0) return;
   double adverse=(side==POSITION_TYPE_BUY?(lastPrice-tick.ask)/_Point:(tick.bid-lastPrice)/_Point);
   if(adverse<distancePoints) return;
   double minDist=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double slDist=0.0,sl=0.0,tp=0.0;
   if(g_serverStopLossEnabled)
   {
      slDist=(g_serverStopLossMode=="POINTS")?g_serverStopLossValue*_Point:atr[0]*g_serverStopLossValue;
      slDist=MathMax(slDist,minDist+_Point);
   }
   g_trade.SetExpertMagicNumber(InpTradeMagic);g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   bool sent=false;
   if(side==POSITION_TYPE_BUY)
   {
      if(g_serverStopLossEnabled) sl=NormalizeDouble(tick.ask-slDist,digits);
      sent=g_trade.Buy(lots,_Symbol,0.0,sl,tp,"ALZOOL-"+chosen);
   }
   else
   {
      if(g_serverStopLossEnabled) sl=NormalizeDouble(tick.bid+slDist,digits);
      sent=g_trade.Sell(lots,_Symbol,0.0,sl,tp,"ALZOOL-"+chosen);
   }
   uint rc=g_trade.ResultRetcode();
   if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED || rc==TRADE_RETCODE_DONE_PARTIAL))
      Print("AL-ZOOL ",chosen," accepted direction=",(side==POSITION_TYPE_BUY?"BUY":"SELL"),
            " lot=",DoubleToString(lots,2)," adverse_points=",DoubleToString(adverse,1),
            " dynamic_distance=",DoubleToString(distancePoints,1));
   else Print("AL-ZOOL ",chosen," rejected: ",g_trade.ResultRetcodeDescription());
}
void ManageEntryTrailing()
{
   if(!InpUseTrailing || g_atrHandle==INVALID_HANDLE) return;
   double atr[]; ArraySetAsSeries(atr,true);
   if(CopyBuffer(g_atrHandle,0,1,1,atr)!=1 || atr[0]<=0.0) return;
   MqlTick tick; if(!SymbolInfoTick(_Symbol,tick)) return;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double minDist=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=InpTradeMagic) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open=PositionGetDouble(POSITION_PRICE_OPEN),oldSL=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP),nextSL=oldSL;
      if(type==POSITION_TYPE_BUY && tick.bid-open>=atr[0]*InpTrailStartATR)
      {
         nextSL=NormalizeDouble(tick.bid-atr[0]*InpTrailDistanceATR,digits);
         if(tick.bid-nextSL<minDist) nextSL=NormalizeDouble(tick.bid-minDist,digits);
         if(oldSL>0.0 && nextSL<=oldSL+_Point) continue;
      }
      else if(type==POSITION_TYPE_SELL && open-tick.ask>=atr[0]*InpTrailStartATR)
      {
         nextSL=NormalizeDouble(tick.ask+atr[0]*InpTrailDistanceATR,digits);
         if(nextSL-tick.ask<minDist) nextSL=NormalizeDouble(tick.ask+minDist,digits);
         if(oldSL>0.0 && nextSL>=oldSL-_Point) continue;
      }
      else continue;
      if(!g_trade.PositionModify(ticket,nextSL,tp))
         Print("AL-ZOOL trailing modify failed ticket=",ticket," retcode=",g_trade.ResultRetcode());
   }
}

bool ClosePositionTicket(const ulong ticket,const string scope)
{
   bool sent=g_trade.PositionClose(ticket);
   uint retcode=g_trade.ResultRetcode();
   if(!sent || (retcode!=TRADE_RETCODE_DONE && retcode!=TRADE_RETCODE_DONE_PARTIAL))
   {
      Print("AL-ZOOL ",scope," close failed ticket ",ticket,
            " retcode=",retcode," ",g_trade.ResultRetcodeDescription());
      return false;
   }
   Print("AL-ZOOL ",scope," close accepted ticket ",ticket," retcode=",retcode);
   return true;
}
void EvaluateAutoClose()
{
   int total=PositionsTotal();
   if(total<=0) return;
   double groupNet=0.0;
   for(int i=total-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      groupNet+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
   }
   // With 2+ open positions, ONLY the combined group target is evaluated.
   // Never fall through to the single-position rule in a multi-position account.
   if(total>=2)
   {
      if(g_groupCloseEnabled && groupNet>=g_groupProfitTarget)
      {
         Print("AL-ZOOL GROUP PROFIT TARGET reached: ",DoubleToString(groupNet,2),
               " >= ",DoubleToString(g_groupProfitTarget,2),". Closing all open positions.");
         for(int j=PositionsTotal()-1;j>=0;j--)
         {
            ulong ticket=PositionGetTicket(j);
            if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
            ClosePositionTicket(ticket,"group");
         }
      }
      return;
   }
   // Exactly one open position: apply the single-position target to that position only.
   if(total==1 && g_singleCloseEnabled)
   {
      ulong ticket=PositionGetTicket(0);
      if(ticket>0 && PositionSelectByTicket(ticket))
      {
         double net=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
         if(net>=g_singleProfitTarget)
         {
            Print("AL-ZOOL SINGLE PROFIT TARGET reached ticket ",ticket,": ",
                  DoubleToString(net,2)," >= ",DoubleToString(g_singleProfitTarget,2));
            ClosePositionTicket(ticket,"single");
         }
      }
   }
}
void AppendCandle(string &out,const MqlRates &bar,int digits)
{
   out+="{\"time\":"+IntegerToString((long)bar.time);
   out+=",\"open\":"+JNum(bar.open,digits);
   out+=",\"high\":"+JNum(bar.high,digits);
   out+=",\"low\":"+JNum(bar.low,digits);
   out+=",\"close\":"+JNum(bar.close,digits);
   out+=",\"tick_volume\":"+IntegerToString((long)bar.tick_volume)+"}";
}
bool SendHistoricalBatch()
{
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20) return false;
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int wanted=(int)MathMax(20,MathMin(10000,InpHistoricalCandlesToSend));
   int copied=CopyRates(_Symbol,(ENUM_TIMEFRAMES)_Period,1,wanted,rates);
   if(copied<20)
   {
      Print("AL-ZOOL history upload skipped: only ",copied," closed bars are available.");
      return false;
   }

   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double contract_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_CONTRACT_SIZE);
   string body="{\"symbol\":\""+JsonEscape(_Symbol)+"\"";
   body+=",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\"";
   body+=",\"metadata\":{\"digits\":"+IntegerToString(_Digits);
   body+=",\"point_size\":"+JNum(point,10);
   body+=",\"tick_size\":"+JNum(tick_size,10);
   body+=",\"tick_value\":"+JNum(tick_value,10);
   body+=",\"contract_size\":"+JNum(contract_size,8);
   body+=",\"account_currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\"}";
   body+=",\"candles\":[";
   int sent=0;
   // MQL5 series indexing: highest index is oldest, so upload chronologically.
   for(int i=copied-1;i>=0;i--)
   {
      if(sent>0) body+=",";
      body+="{\"time\":"+IntegerToString((long)rates[i].time);
      body+=",\"open\":"+JNum(rates[i].open,_Digits);
      body+=",\"high\":"+JNum(rates[i].high,_Digits);
      body+=",\"low\":"+JNum(rates[i].low,_Digits);
      body+=",\"close\":"+JNum(rates[i].close,_Digits);
      body+=",\"tick_volume\":"+IntegerToString((long)rates[i].tick_volume);
      body+=",\"spread_points\":"+IntegerToString((int)rates[i].spread)+"}";
      sent++;
   }
   body+="]}";

   char data[],result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("POST",BaseUrl()+"/v1/terminal-history/batch/"+InpEngineId,
                       headers,30000,data,result,response_headers);
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(code!=200)
   {
      Print("AL-ZOOL historical upload failed: HTTP=",code," err=",GetLastError(),
            " body=",StringSubstr(response,0,400));
      return false;
   }
   Print("AL-ZOOL historical candles uploaded: ",sent," bars. ",StringSubstr(response,0,250));
   return true;
}

bool SendLiveSnapshot()
{
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20)
   {
      g_status="أدخل معرّف الاتصال ورمز الترخيص";
      return false;
   }

   string body="{\"account\":{";
   body+="\"login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\"";
   body+=",\"server\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\"";
   body+=",\"company\":\""+JsonEscape(AccountInfoString(ACCOUNT_COMPANY))+"\"";
   body+=",\"currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\"";
   body+=",\"leverage\":"+IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE));
   body+=",\"balance\":"+JNum(AccountInfoDouble(ACCOUNT_BALANCE));
   body+=",\"equity\":"+JNum(AccountInfoDouble(ACCOUNT_EQUITY));
   body+=",\"margin\":"+JNum(AccountInfoDouble(ACCOUNT_MARGIN));
   body+=",\"free_margin\":"+JNum(AccountInfoDouble(ACCOUNT_MARGIN_FREE));
   body+=",\"profit\":"+JNum(AccountInfoDouble(ACCOUNT_PROFIT))+"}";

   body+=",\"symbol\":\""+JsonEscape(_Symbol)+"\"";
   body+=",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\"";
   body+=",\"terminal_build\":"+IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD));

   body+=",\"positions\":[";
   int count=0;
   for(int i=PositionsTotal()-1;i>=0 && count<200;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      string sym=PositionGetString(POSITION_SYMBOL);
      int digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
      if(count>0) body+=",";
      body+="{\"ticket\":\""+(string)ticket+"\"";
      body+=",\"symbol\":\""+JsonEscape(sym)+"\"";
      body+=",\"type\":"+IntegerToString((int)PositionGetInteger(POSITION_TYPE));
      body+=",\"volume\":"+JNum(PositionGetDouble(POSITION_VOLUME),2);
      body+=",\"open_price\":"+JNum(PositionGetDouble(POSITION_PRICE_OPEN),digits);
      body+=",\"price_current\":"+JNum(PositionGetDouble(POSITION_PRICE_CURRENT),digits);
      body+=",\"sl\":"+JNum(PositionGetDouble(POSITION_SL),digits);
      body+=",\"tp\":"+JNum(PositionGetDouble(POSITION_TP),digits);
      body+=",\"profit\":"+JNum(PositionGetDouble(POSITION_PROFIT));
      body+=",\"swap\":"+JNum(PositionGetDouble(POSITION_SWAP));
      body+=",\"magic\":\""+IntegerToString((long)PositionGetInteger(POSITION_MAGIC))+"\"";
      body+=",\"comment\":\""+JsonEscape(PositionGetString(POSITION_COMMENT))+"\"";
      body+=",\"time\":"+IntegerToString((long)PositionGetInteger(POSITION_TIME))+"}";
      count++;
   }
   body+="]";

   body+=",\"orders\":[";
   count=0;
   for(int j=OrdersTotal()-1;j>=0 && count<200;j--)
   {
      ulong ticket=OrderGetTicket(j);
      if(ticket==0) continue;
      string sym=OrderGetString(ORDER_SYMBOL);
      int digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
      if(count>0) body+=",";
      body+="{\"ticket\":\""+(string)ticket+"\"";
      body+=",\"symbol\":\""+JsonEscape(sym)+"\"";
      body+=",\"type\":"+IntegerToString((int)OrderGetInteger(ORDER_TYPE));
      body+=",\"state\":"+IntegerToString((int)OrderGetInteger(ORDER_STATE));
      body+=",\"volume\":"+JNum(OrderGetDouble(ORDER_VOLUME_CURRENT),2);
      body+=",\"price\":"+JNum(OrderGetDouble(ORDER_PRICE_OPEN),digits);
      body+=",\"sl\":"+JNum(OrderGetDouble(ORDER_SL),digits);
      body+=",\"tp\":"+JNum(OrderGetDouble(ORDER_TP),digits);
      body+=",\"magic\":\""+IntegerToString((long)OrderGetInteger(ORDER_MAGIC))+"\"";
      body+=",\"comment\":\""+JsonEscape(OrderGetString(ORDER_COMMENT))+"\"";
      body+=",\"time_setup\":"+IntegerToString((long)OrderGetInteger(ORDER_TIME_SETUP))+"}";
      count++;
   }
   body+="]";

   // Entry and exit deals for the last 30 days; telemetry only. Entry comments preserve the strategy ID.
   datetime history_from=TimeCurrent()-30*86400;
   if(HistorySelect(history_from,TimeCurrent()))
   {
      body+=",\"closed_deals\":[";
      int deals_sent=0;
      int total_deals=HistoryDealsTotal();
      for(int h=total_deals-1;h>=0 && deals_sent<200;h--)
      {
         ulong deal_ticket=HistoryDealGetTicket(h);
         if(deal_ticket==0) continue;
         long entry=HistoryDealGetInteger(deal_ticket,DEAL_ENTRY);
         if(entry!=DEAL_ENTRY_IN && entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) continue;
         string deal_symbol=HistoryDealGetString(deal_ticket,DEAL_SYMBOL);
         if(deals_sent>0) body+=",";
         body+="{\"ticket\":\""+(string)deal_ticket+"\"";
         body+=",\"position_id\":\""+(string)HistoryDealGetInteger(deal_ticket,DEAL_POSITION_ID)+"\"";
         body+=",\"symbol\":\""+JsonEscape(deal_symbol)+"\"";
         body+=",\"type\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_TYPE));
         body+=",\"deal_type\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_TYPE));
         body+=",\"entry\":"+IntegerToString((int)entry);
         body+=",\"reason\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_REASON));
         body+=",\"volume\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_VOLUME),2);
         body+=",\"price\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_PRICE),(int)SymbolInfoInteger(deal_symbol,SYMBOL_DIGITS));
         body+=",\"profit\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_PROFIT));
         body+=",\"commission\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_COMMISSION));
         body+=",\"swap\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_SWAP));
         body+=",\"fee\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_FEE));
         body+=",\"magic\":\""+IntegerToString((long)HistoryDealGetInteger(deal_ticket,DEAL_MAGIC))+"\"";
         body+=",\"comment\":\""+JsonEscape(HistoryDealGetString(deal_ticket,DEAL_COMMENT))+"\"";
         body+=",\"time\":"+IntegerToString((long)HistoryDealGetInteger(deal_ticket,DEAL_TIME))+"}";
         deals_sent++;
      }
      body+="]";
   }
   else body+=",\"closed_deals\":[]";

   MqlTick tick;
   bool have_tick=SymbolInfoTick(_Symbol,tick);
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   body+=",\"tick\":{\"bid\":"+JNum(have_tick?tick.bid:0.0,digits);
   body+=",\"ask\":"+JNum(have_tick?tick.ask:0.0,digits);
   body+=",\"spread\":"+JNum(have_tick?(tick.ask-tick.bid)/_Point:0.0,1)+"}";

   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int wanted=MathMax(2,MathMin(121,InpCandlesToSend+1));
   int copied=CopyRates(_Symbol,(ENUM_TIMEFRAMES)_Period,0,wanted,rates);
   body+=",\"forming_candle\":";
   if(copied>0)
   {
      string forming="";
      AppendCandle(forming,rates[0],digits);
      body+=forming;
   }
   else body+="null";

   body+=",\"closed_candles\":[";
   int sent=0;
   for(int k=1;k<copied && sent<InpCandlesToSend;k++)
   {
      if(sent>0) body+=",";
      AppendCandle(body,rates[k],digits);
      sent++;
   }
   body+="]}";

   char data[],result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("POST",BaseUrl()+"/v1/terminal-state/live/"+InpEngineId,
                       headers,InpHttpTimeoutMs,data,result,response_headers);
   if(code!=200)
   {
      string response=CharArrayToString(result,0,-1,CP_UTF8);
      g_status="فشل إرسال بيانات الحساب؛ HTTP="+IntegerToString(code)+
               " err="+IntegerToString(GetLastError());
      Print("AL-ZOOL telemetry error: HTTP=",code," body=",StringSubstr(response,0,500));
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   g_status="متصل — تم إرسال بيانات الحساب والصفقات والشموع";
   Print("AL-ZOOL telemetry accepted: ",StringSubstr(response,0,300));
   return true;
}

int OnInit()
{
   if(InpHeartbeatSeconds<5 || InpCandlesToSend<20 || InpCandlesToSend>120 ||
      InpHistoricalCandlesToSend<20 || InpHistoricalCandlesToSend>10000 || InpHistoryUploadMinutes<5 ||
      InpTradeLots<=0 || InpMaxOpenPositions<1 || InpATRPeriod<2 ||
      InpStopLossATR<=0 || InpTrailStartATR<=0 || InpTrailDistanceATR<=0)
   {
      Print("AL-ZOOL: invalid heartbeat, candles, lot, position cap or protection settings");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_trade.SetAsyncMode(false);
   g_trade.SetExpertMagicNumber(InpTradeMagic);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_atrHandle=iATR(_Symbol,(ENUM_TIMEFRAMES)_Period,InpATRPeriod);
   if(g_atrHandle==INVALID_HANDLE) { Print("AL-ZOOL ATR init failed: ",GetLastError()); return INIT_FAILED; }
   g_emaFastHandle=iMA(_Symbol,(ENUM_TIMEFRAMES)_Period,9,0,MODE_EMA,PRICE_CLOSE);
   g_emaSlowHandle=iMA(_Symbol,(ENUM_TIMEFRAMES)_Period,21,0,MODE_EMA,PRICE_CLOSE);
   if(g_emaFastHandle==INVALID_HANDLE || g_emaSlowHandle==INVALID_HANDLE)
   { Print("AL-ZOOL EMA handles unavailable; AI add-on mode will wait for valid data."); }
   g_executionBarKey="ALZOOL_EXEC_"+_Symbol+"_"+EnumToString((ENUM_TIMEFRAMES)_Period)+"_"+(string)InpTradeMagic;
   EventSetTimer(1);
   g_status="تم تشغيل بيانات MT5 وحماية الأرباح المحلية";
   Print("AL-ZOOL MT5 client started: central trading=",InpEnableCentralTrading,
         " lot=",DoubleToString(InpTradeLots,2)," max positions=",InpMaxOpenPositions,
         "; account-wide profit-close logic preserved.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason)
{
   EventKillTimer();
   if(g_atrHandle!=INVALID_HANDLE) IndicatorRelease(g_atrHandle);
   if(g_emaFastHandle!=INVALID_HANDLE) IndicatorRelease(g_emaFastHandle);
   if(g_emaSlowHandle!=INVALID_HANDLE) IndicatorRelease(g_emaSlowHandle);
   Comment("");
}
void OnTimer()
{
   EvaluateAutoClose();
   ManageEntryTrailing();
   ManagePositionAddons();
   ulong now=GetTickCount64();
   if(g_lastSettingsPollMs==0 || now-g_lastSettingsPollMs>=1000)
   {
      PollTradeSettings();
      g_lastSettingsPollMs=now;
   }
   if(g_lastDecisionPollMs==0 || now-g_lastDecisionPollMs>=2000)
   {
      PollCentralDecision();
      g_lastDecisionPollMs=now;
   }
   if(g_lastSnapshotMs==0 || now-g_lastSnapshotMs>=((ulong)InpHeartbeatSeconds*1000))
   {
      bool sent=SendLiveSnapshot();
      if(sent) g_online=true;
      g_lastSnapshotMs=now;
   }
   if(g_lastHistoryAttempt==0 || TimeCurrent()-g_lastHistoryAttempt>=InpHistoryUploadMinutes*60)
   {
      g_lastHistoryAttempt=TimeCurrent();
      SendHistoricalBatch();
   }
   Comment("AL-ZOOL MT5 LIVE CLIENT\n",g_status,
           "\nCentral AI: ",g_aiSignal," (",DoubleToString(g_aiConfidence*100.0,1),"%)",
           "\n",g_aiReason,
           "\nProfit close: single ",(g_singleCloseEnabled?"ON":"OFF"),
           " / group ",(g_groupCloseEnabled?"ON":"OFF"),
           "\nPositions: ",IntegerToString(PositionsTotal()),
           " | Pending: ",IntegerToString(OrdersTotal()));
}
void OnTick()
{
   EvaluateAutoClose();
}
// Profit targets are evaluated locally on market ticks and a one-second timer.
