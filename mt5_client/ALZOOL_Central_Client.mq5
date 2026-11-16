//+------------------------------------------------------------------+
//| AL-ZOOL MT5 LIVE DATA CLIENT                                      |
//| Sends MT5 telemetry and applies user-configured profit closes.    |
//| Existing licence protocol retained; closes are evaluated locally. |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

CTrade g_trade;
#property version "2.40"
#property description "AL-ZOOL central AI voting and single-use action authorization"

input group "بيانات الاتصال بالخادم"
input string InpServerUrl = "https://al-zool-central-engine.onrender.com";
input bool InpOwnerMode = true;
input string InpOwnerPairingKey = "";
input string InpOwnerConnectionId = "";
input string InpEngineId = "";
input string InpEngineToken = "";
input int InpHttpTimeoutMs = 8000;
input int InpHeartbeatSeconds = 2;
input int InpCandlesToSend = 120;
input group "إعدادات تنفيذ الدخول المركزي"
input bool InpEnableCentralTrading = false;
input bool InpEnableCentralPositionManagement = false;
input double InpMaxCentralActionLots = 0.0; // 0 = no optional EA lot cap
input double InpMaxCentralTotalLots = 0.0; // 0 = no optional EA exposure cap
input double InpTradeLots = 0.01;
input ulong InpTradeMagic = 26110801;
input int InpMaxOpenPositions = 0; // 0 = no optional EA position cap
input bool InpBlockOppositePositions = false; // Optional owner choice, not an implicit restriction
input int InpATRPeriod = 14;
input double InpStopLossATR = 1.8;
input bool InpUseTrailing = false; // Opt-in; never add a stop without explicit preference
input double InpTrailStartATR = 1.0;
input double InpTrailDistanceATR = 0.8;
input int InpDeviationPoints = 20;
input group "الأرشيف التاريخي للاختبار"
input int InpHistoricalCandlesToSend = 500;
input int InpHistoryUploadMinutes = 15;

struct StrategyPositionLink
{
   long position_id;
   int strategy_id;
   string strategy_comment;
};

bool g_online=false;
bool g_serverTradingEnabled=false;
datetime g_lastHistoryAttempt=0;
string g_cachedClosedDealsJson=",\"closed_deals\":[]";
ulong g_lastClosedDealsBuildMs=0;
bool g_closedDealsDirty=true;
string g_status="بدء الاتصال";
bool g_singleCloseEnabled=false,g_groupCloseEnabled=false;
bool g_groupClosePending=false;
string g_groupCloseKey="";
double g_singleProfitTarget=1.0,g_groupProfitTarget=5.0;
bool g_serverStopLossEnabled=false;
bool g_recoveryEnabled=false;
double g_recoveryLossTrigger=0.0;
double g_recoveryProfitTarget=0.0; // Account-currency net group profit selected by owner; 0 = break-even
bool g_recoveryCycleActive=false;
double g_recoveryReferencePrice=0.0;
string g_recoveryActiveKey="",g_recoveryPriceKey="";
string g_serverStopLossMode="ATR";
double g_serverStopLossValue=1.8;
string g_pmMode="AI";
bool g_addonEnabled=false,g_coolingEnabled=false;
int g_addonMax=0,g_coolingMax=0;
double g_addonManualLot=0.01,g_coolingManualLot=0.01;
double g_addonManualDistance=0.0,g_coolingManualDistance=0.0;
double g_addonMultiplier=1.5,g_coolingMultiplier=1.2;
double g_addonMaxLot=0.05,g_coolingMaxLot=0.05,g_maxTotalAddCoolingLots=0.10;
bool g_entryGuardEnabled=true;
int g_entryMaxDelayBars=1;
double g_entryMaxDeviationATR=0.5;
int g_emaFastHandle=INVALID_HANDLE,g_emaSlowHandle=INVALID_HANDLE;
int g_m5Fast=INVALID_HANDLE,g_m5Slow=INVALID_HANDLE,g_m15Fast=INVALID_HANDLE,g_m15Slow=INVALID_HANDLE,g_h1Fast=INVALID_HANDLE,g_h1Slow=INVALID_HANDLE;
ulong g_lastSnapshotMs=0,g_lastSettingsPollMs=0,g_lastDecisionPollMs=0,g_lastActionPollMs=0;
string g_aiSignal="WAIT",g_aiReason="بانتظار إشارة المحرك المركزي";
double g_aiConfidence=0.0;
long g_aiBarTime=0;
int g_aiStrategyId=0,g_aiCombinationId=0;
int g_atrHandle=INVALID_HANDLE;
string g_executionBarKey="";
void ExecuteCentralEntry(const string direction,const long decisionBar,const double confidence,const int strategyId,const int combinationId,const double referencePrice,const double signalATR,const string reason);
bool ClosePositionTicket(const ulong ticket,const string scope);

string MtfDirection(const ENUM_TIMEFRAMES tf,const int fastHandle,const int slowHandle)
{
   double fast[],slow[]; ArraySetAsSeries(fast,true); ArraySetAsSeries(slow,true);
   if(fastHandle==INVALID_HANDLE || slowHandle==INVALID_HANDLE) return "WAIT";
   if(CopyBuffer(fastHandle,0,1,1,fast)!=1 || CopyBuffer(slowHandle,0,1,1,slow)!=1) return "WAIT";
   double close=iClose(_Symbol,tf,1);
   if(close<=0.0) return "WAIT";
   if(close>fast[0] && fast[0]>slow[0]) return "BUY";
   if(close<fast[0] && fast[0]<slow[0]) return "SELL";
   return "WAIT";
}

string ConnectionId()
{
   return InpOwnerMode ? InpOwnerConnectionId : InpEngineId;
}
string ConnectionKey()
{
   return InpOwnerMode ? InpOwnerPairingKey : InpEngineToken;
}
bool ConnectionReady()
{
   return InpOwnerMode ? (StringLen(InpOwnerPairingKey)>=32 && StringLen(InpOwnerConnectionId)>=8) :
      (StringLen(InpEngineId)>=8 && StringLen(InpEngineToken)>=20);
}
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
   if(!ConnectionReady()) return false;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Authorization: Bearer "+ConnectionKey()+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",BaseUrl()+"/v1/terminal-control/settings/"+ConnectionId(),
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
   g_recoveryEnabled=JsonBool(response,"recovery_enabled");
   g_recoveryLossTrigger=JsonDouble(response,"recovery_loss_trigger",0.0);
   g_recoveryProfitTarget=JsonDouble(response,"recovery_profit_target",0.0);
   if(!MathIsValidNumber(g_recoveryProfitTarget) || g_recoveryProfitTarget<0.0) g_recoveryProfitTarget=0.0;
   if(g_recoveryLossTrigger<=0.0) g_recoveryEnabled=false;
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
   g_addonMultiplier=MathMax(1.0,MathMin(3.0,JsonDouble(response,"addon_multiplier",1.5)));
   g_coolingMultiplier=MathMax(1.0,MathMin(3.0,JsonDouble(response,"cooling_multiplier",1.2)));
   g_addonMaxLot=JsonDouble(response,"addon_max_lot",0.05);
   g_coolingMaxLot=JsonDouble(response,"cooling_max_lot",0.05);
   g_maxTotalAddCoolingLots=JsonDouble(response,"max_total_lots",0.10);
   g_entryGuardEnabled=JsonBool(response,"entry_guard_enabled");
   g_entryMaxDelayBars=(int)MathMax(1,MathMin(10,JsonDouble(response,"entry_max_delay_bars",1)));
   g_entryMaxDeviationATR=MathMax(0.05,MathMin(20.0,JsonDouble(response,"entry_max_deviation_atr",0.5)));
   return true;
}
bool ExecuteOneCentralAction(const string side,const string action,const double requestedLots,const long barTime)
{
   if(g_groupClosePending) return false;
   if(!InpEnableCentralTrading || !InpEnableCentralPositionManagement || !g_serverTradingEnabled)
      return false;
   if(side!="BUY" && side!="SELL") return false;
   if(action!="ADDON_PROPOSAL" && action!="COOLING_PROPOSAL" && action!="RECOVERY_PROPOSAL")
      return false;
   if(StringFind(_Symbol,"XAUUSD")!=0 || barTime<=0) return false;
   if((InpMaxOpenPositions>0 && PositionsTotal()>=InpMaxOpenPositions) || !IsAccountDirectionAllowed(side))
      return false;
   if(requestedLots<=0.0 || (InpMaxCentralActionLots>0 && requestedLots>InpMaxCentralActionLots+1e-8))
      return false;
   double totalLots=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      totalLots+=PositionGetDouble(POSITION_VOLUME);
   }
   if(InpMaxCentralTotalLots>0 && totalLots+requestedLots>InpMaxCentralTotalLots+1e-8) return false;
   double lots=NormalizeTradeLots(requestedLots);
   if(lots<=0.0 || lots>requestedLots+1e-8 || (InpMaxCentralActionLots>0 && lots>InpMaxCentralActionLots+1e-8))
      return false; // Never round up beyond either central or local risk cap.
   if(action=="RECOVERY_PROPOSAL" && MathAbs(lots-requestedLots)>1e-8)
   {
      Print("AL-ZOOL RECOVERY BLOCKED: broker volume step cannot represent exact x3 lot.");
      return false;
   }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.ask<=0.0 || tick.bid<=0.0)
      return false;
   double sl=0.0; // No stop loss when the owner has disabled it in the dashboard.
   if(g_serverStopLossEnabled)
   {
      int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
      double stopLevel=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
      double stopDistance=g_serverStopLossValue*_Point;
      if(g_serverStopLossMode=="ATR")
      {
         double atr[];
         ArraySetAsSeries(atr,true);
         if(g_atrHandle==INVALID_HANDLE || CopyBuffer(g_atrHandle,0,1,1,atr)!=1 || atr[0]<=0.0)
            return false;
         stopDistance=atr[0]*g_serverStopLossValue;
      }
      stopDistance=MathMax(stopDistance,stopLevel+_Point);
      sl=side=="BUY"?NormalizeDouble(tick.ask-stopDistance,digits):
                      NormalizeDouble(tick.bid+stopDistance,digits);
      if(sl<=0.0) return false;
   }
   ENUM_ORDER_TYPE orderType=(side=="BUY"?ORDER_TYPE_BUY:ORDER_TYPE_SELL);
   double intendedPrice=(side=="BUY"?tick.ask:tick.bid);
   double requiredMargin=0.0;
   if(!OrderCalcMargin(orderType,_Symbol,lots,intendedPrice,requiredMargin) ||
      !MathIsValidNumber(requiredMargin) || requiredMargin<0.0 ||
      requiredMargin>AccountInfoDouble(ACCOUNT_MARGIN_FREE))
   {
      Print("AL-ZOOL CENTRAL ACTION BLOCKED: insufficient or unknown free margin.");
      return false;
   }
   string tag=action=="ADDON_PROPOSAL"?"ALZOOL-ADD":
              (action=="COOLING_PROPOSAL"?"ALZOOL-COOL":"ALZOOL-RECOVERY");
   g_trade.SetExpertMagicNumber(InpTradeMagic);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   bool sent=side=="BUY"?
      g_trade.Buy(lots,_Symbol,0.0,sl,0.0,tag):
      g_trade.Sell(lots,_Symbol,0.0,sl,0.0,tag);
   uint code=g_trade.ResultRetcode();
   bool accepted=sent && (code==TRADE_RETCODE_DONE ||
                            code==TRADE_RETCODE_DONE_PARTIAL ||
                            code==TRADE_RETCODE_PLACED);
   if(accepted)
   {
      string key="AZ_CENTRAL_PM_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"_"+_Symbol;
      GlobalVariableSet(key,(double)barTime);
      Print("AL-ZOOL CENTRAL ACTION ",action," side=",side," lot=",DoubleToString(lots,3),
            " bar=",barTime," retcode=",code);
      if(action=="RECOVERY_PROPOSAL")
      {
         // Persist the recovery cycle so restarts and missing broker comments
         // cannot silently reset the group-profit close condition.
         g_recoveryCycleActive=true;
         if(StringLen(g_recoveryActiveKey)>0) GlobalVariableSet(g_recoveryActiveKey,1.0);
         Print("AL-ZOOL RECOVERY OPENED: side=",side,
               " lot=",DoubleToString(lots,3),
               " profit target=",DoubleToString(g_recoveryProfitTarget,2),
               " ",AccountInfoString(ACCOUNT_CURRENCY));
      }
   }
   else Print("AL-ZOOL CENTRAL ACTION REJECTED ",action," ",g_trade.ResultRetcodeDescription());
   return accepted;
}
bool PollCentralActions()
{
   if(!InpEnableCentralTrading || !InpEnableCentralPositionManagement ||
      !g_serverTradingEnabled || !ConnectionReady()) return false;
   if(StringFind(_Symbol,"XAUUSD")!=0) return false;
   string headers="Authorization: Bearer "+ConnectionKey()+"\r\n";
   char empty[],result[];
   ArrayResize(empty,0);
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",BaseUrl()+"/v1/terminal-actions/"+ConnectionId(),
                       headers,3000,empty,result,response_headers);
   if(code!=200)
   {
      Print("AL-ZOOL central action poll failed: HTTP=",code," err=",GetLastError());
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(!JsonBool(response,"execution_ready")) return false;
   string side=JsonField(response,"side");
   string action=JsonField(response,"action");
   string nonce=JsonField(response,"authorization_id");
   StringReplace(side,"\"","");
   StringReplace(action,"\"","");
   StringReplace(nonce,"\"","");
   long bar=(long)StringToInteger(JsonField(response,"bar_time"));
   long expires=(long)StringToInteger(JsonField(response,"expires_at_unix"));
   double lots=JsonDouble(response,"lot_cap",0.0);
   long now=(long)TimeGMT();
   if(StringLen(nonce)<20 || bar<=0 || expires<=now || expires-now>30) return false;
   string key="AZ_CENTRAL_PM_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"_"+_Symbol;
   if(GlobalVariableCheck(key) && (long)GlobalVariableGet(key)==bar) return false;
   if((InpMaxOpenPositions>0 && PositionsTotal()>=InpMaxOpenPositions) || !IsAccountDirectionAllowed(side) ||
      lots<=0.0 || (InpMaxCentralActionLots>0 && lots>InpMaxCentralActionLots+1e-8)) return false;
   // Claim the unique central authorization before submitting any broker order.
   string body="{\"authorization_id\":\""+JsonEscape(nonce)+"\"}";
   char data[],claimResult[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string claimHeaders="Content-Type: application/json\r\nAuthorization: Bearer "+ConnectionKey()+"\r\n";
   ResetLastError();
   code=WebRequest("POST",BaseUrl()+"/v1/terminal-action-claim/"+ConnectionId(),
                   claimHeaders,5000,data,claimResult,response_headers);
   string confirmed=CharArrayToString(claimResult,0,-1,CP_UTF8);
   if(code!=200 || !JsonBool(confirmed,"execution_authorized"))
   {
      Print("AL-ZOOL central action not authorized: HTTP=",code," err=",GetLastError());
      return false;
   }
   string claimSide=JsonField(confirmed,"side");
   string claimAction=JsonField(confirmed,"action");
   StringReplace(claimSide,"\"","");
   StringReplace(claimAction,"\"","");
   double confirmedLots=JsonDouble(confirmed,"lot_cap",0.0);
   long claimExpires=(long)StringToInteger(JsonField(confirmed,"expires_at_unix"));
   if(claimSide!=side || claimAction!=action || MathAbs(confirmedLots-lots)>1e-6 ||
      claimExpires<=(long)TimeGMT()) return false;
   return ExecuteOneCentralAction(side,action,lots,bar);
}

bool PollCentralDecision()
{
   if(!ConnectionReady()) return false;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Authorization: Bearer "+ConnectionKey()+"\r\n";
   string response_headers;
   ResetLastError();
   ulong startedMs=GetTickCount64();
   int code=WebRequest("GET",BaseUrl()+"/v1/terminal-decision/"+ConnectionId(),
                       headers,1500,data,result,response_headers);
   ulong elapsedMs=GetTickCount64()-startedMs;
   if(code!=200)
   {
      if(code!=409) Print("AL-ZOOL central decision poll failed: HTTP=",code," err=",GetLastError());
      return false;
   }
   static ulong lastDecisionLatencyLog=0;
   ulong metricAt=GetTickCount64();
   if(elapsedMs>1200 || lastDecisionLatencyLog==0 ||
      metricAt-lastDecisionLatencyLog>=30000)
   {
      Print("AL-ZOOL LATENCY decision_http_ms=",elapsedMs);
      lastDecisionLatencyLog=metricAt;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   string signal=JsonField(response,"signal");
   string reason=JsonField(response,"reason_ar");
   string confidence=JsonField(response,"confidence");
   string bar_time=JsonField(response,"bar_time");
   string strategy_id=JsonField(response,"strategy_id");
   string execution_ready=JsonField(response,"execution_ready");
   string authorization_id=JsonField(response,"authorization_id");
   string authorization_expires=JsonField(response,"authorization_expires_at");
   string combination_id=JsonField(response,"combination_id");
   double entry_reference_price=JsonDouble(response,"entry_reference_price",0.0);
   double entry_atr=JsonDouble(response,"entry_atr",0.0);
   StringReplace(signal,"\"","");
   StringReplace(reason,"\"","");
   if(signal!="BUY" && signal!="SELL") signal="WAIT";
   double parsed_confidence=StringToDouble(confidence);
   long parsed_bar=(long)StringToInteger(bar_time);
   int parsed_strategy=(int)StringToInteger(strategy_id);
   int parsed_combination=(int)StringToInteger(combination_id);
   long authorization_expiry=(long)StringToInteger(authorization_expires);
   long authorization_now=(long)TimeGMT();
   bool authorization_valid=(StringLen(authorization_id)>=8 &&
      authorization_expiry>authorization_now &&
      authorization_expiry-authorization_now<=20);
   bool ready=(StringFind(execution_ready,"true")==0 && authorization_valid);
   if(StringFind(execution_ready,"true")==0 && !authorization_valid)
      Print("AL-ZOOL ENTRY BLOCKED: council authorization missing or expired.");
   if(parsed_bar!=g_aiBarTime || signal!=g_aiSignal)
      Print("AL-ZOOL CENTRAL AI: ",signal," | confidence=",DoubleToString(parsed_confidence*100.0,1),
            "% | ",reason);
   g_aiSignal=signal;
   g_aiReason=reason;
   g_aiConfidence=MathMax(0.0,MathMin(1.0,parsed_confidence));
   g_aiBarTime=parsed_bar;
   g_aiStrategyId=parsed_strategy;
   g_aiCombinationId=parsed_combination;
   if(ready && InpEnableCentralTrading && g_serverTradingEnabled) ExecuteCentralEntry(signal,parsed_bar,parsed_confidence,parsed_strategy,parsed_combination,entry_reference_price,entry_atr,reason);
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
                         const int strategyId,const int combinationId,const double referencePrice,
                         const double signalATR,const string reason)
{
   if(g_groupClosePending) return; // Never reopen exposure during group closure.
   if(!InpEnableCentralTrading || (direction!="BUY" && direction!="SELL") ||
      (confidence<0.0 || confidence>1.0) || strategyId<1 || strategyId>200 || decisionBar<=0) return;
   if(!IsAccountDirectionAllowed(direction))
   { g_status="قفل الاتجاه الواحد: توجد صفقات في الاتجاه المعاكس"; return; }
   datetime currentBar=iTime(_Symbol,(ENUM_TIMEFRAMES)_Period,0);
   if(currentBar<=0 || (long)currentBar<=decisionBar) return;
   if(g_executionBarKey!="" && GlobalVariableCheck(g_executionBarKey) &&
      (long)GlobalVariableGet(g_executionBarKey)==decisionBar) return;
   int periodSeconds=PeriodSeconds((ENUM_TIMEFRAMES)_Period);
   long decisionAge=(long)currentBar-decisionBar;
   if(g_entryGuardEnabled && (periodSeconds<=0 || decisionAge>(long)periodSeconds*g_entryMaxDelayBars))
   {
      g_status="تم رفض الدخول: إشارة قديمة تجاوزت حد التأخير";
      Print("AL-ZOOL ENTRY BLOCKED: stale signal | age_seconds=",decisionAge,
            " max_seconds=",periodSeconds*g_entryMaxDelayBars," bar=",decisionBar);
      return;
   }
   if(InpMaxOpenPositions>0 && CountAlzoolPositions()>=InpMaxOpenPositions) { g_status="الحد الأقصى للصفقات مفتوح"; return; }
   double lots=NormalizeTradeLots(InpTradeLots);
   if(lots<=0.0) { g_status="حجم اللوت غير صالح"; return; }
   double atr[];
   ArraySetAsSeries(atr,true);
   if(g_atrHandle==INVALID_HANDLE || CopyBuffer(g_atrHandle,0,1,1,atr)!=1 || atr[0]<=0.0)
   { g_status="بيانات ATR غير متاحة؛ تم إلغاء الدخول"; return; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<tick.bid) return;
   if(g_entryGuardEnabled)
   {
      if(referencePrice<=0.0 || signalATR<=0.0)
      {
         g_status="تم رفض الدخول: سعر مرجع الإشارة أو ATR غير متاح";
         Print("AL-ZOOL ENTRY BLOCKED: missing reference price or ATR | bar=",decisionBar);
         return;
      }
      double executionPrice=(direction=="BUY"?tick.ask:tick.bid);
      double maxDeviation=signalATR*g_entryMaxDeviationATR;
      double actualDeviation=MathAbs(executionPrice-referencePrice);
      if(actualDeviation>maxDeviation)
      {
         g_status="تم رفض الدخول: السعر ابتعد عن نقطة الإشارة";
         Print("AL-ZOOL ENTRY BLOCKED: price deviation | actual=",DoubleToString(actualDeviation,_Digits),
               " max=",DoubleToString(maxDeviation,_Digits)," ATR=",DoubleToString(signalATR,_Digits),
               " reference=",DoubleToString(referencePrice,_Digits)," current=",DoubleToString(executionPrice,_Digits),
               " bar=",decisionBar);
         return;
      }
   }
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
      sent=g_trade.Buy(lots,_Symbol,0.0,sl,tp,"ALZOOL-S"+IntegerToString(strategyId)+"-C"+IntegerToString(combinationId));
   }
   else
   {
      if(g_serverStopLossEnabled) sl=NormalizeDouble(tick.bid+slDist,digits);
      sent=g_trade.Sell(lots,_Symbol,0.0,sl,tp,"ALZOOL-S"+IntegerToString(strategyId)+"-C"+IntegerToString(combinationId));
   }
   uint rc=g_trade.ResultRetcode();
   if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED || rc==TRADE_RETCODE_DONE_PARTIAL))
   {
       // Consume signal only after broker confirms execution/placement.
       if(g_executionBarKey!="") GlobalVariableSet(g_executionBarKey,(double)decisionBar);
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
   // Legacy local add-on/cooling code was removed deliberately.
   // Only PollCentralActions may claim an action-specific signed-off nonce
   // and then call ExecuteOneCentralAction; no independent order path.
}
void ManageEntryTrailing()
{
   if(!InpUseTrailing || !g_serverStopLossEnabled || g_atrHandle==INVALID_HANDLE) return;
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
double RecoveryPositionCosts(const long positionIdentifier)
{
   double costs=0.0;
   if(positionIdentifier<=0 || !HistorySelectByPosition((ulong)positionIdentifier)) return costs;
   int deals=HistoryDealsTotal();
   for(int i=0;i<deals;i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      costs+=HistoryDealGetDouble(deal,DEAL_COMMISSION)+HistoryDealGetDouble(deal,DEAL_FEE);
   }
   return costs;
}
double RecoveryGroupNet(double &totalLots,int &totalPositions)
{
   double net=0.0;totalLots=0.0;totalPositions=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      totalPositions++;
      totalLots+=PositionGetDouble(POSITION_VOLUME);
      net+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
      net+=RecoveryPositionCosts((long)PositionGetInteger(POSITION_IDENTIFIER));
   }
   return net;
}
void ResetRecoveryCycle()
{
   g_recoveryCycleActive=false;
   g_recoveryReferencePrice=0.0;
   if(StringLen(g_recoveryActiveKey)>0 && GlobalVariableCheck(g_recoveryActiveKey)) GlobalVariableDel(g_recoveryActiveKey);
   if(StringLen(g_recoveryPriceKey)>0 && GlobalVariableCheck(g_recoveryPriceKey)) GlobalVariableDel(g_recoveryPriceKey);
}
bool RecoveryHasOpenTrade()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(StringFind(PositionGetString(POSITION_COMMENT),"ALZOOL-RECOVERY")==0) return true;
   }
   return false;
}
bool RecoveryCloseAll()
{
   bool allAccepted=true;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(!ClosePositionTicket(ticket,"smart-recovery")) allAccepted=false;
   }
   if(PositionsTotal()==0)
   {
      Print("AL-ZOOL SMART RECOVERY: all account positions closed after group recovery.");
      ResetRecoveryCycle();
      return true;
   }
   if(!allAccepted) Print("AL-ZOOL SMART RECOVERY: some positions remain open; retrying close on next tick.");
   return false;
}
bool EvaluateSmartRecovery()
{
   if(g_recoveryCycleActive || RecoveryHasOpenTrade())
   {
      g_recoveryCycleActive=true;
      double lots=0.0;int count=0;
      double net=RecoveryGroupNet(lots,count);
      if(count==0){ResetRecoveryCycle();return false;}
      if(net>=g_recoveryProfitTarget)
      {
         // Latch the requested group close across partial fills. Otherwise a
         // price retracement after closing one leg would stop the remaining closes.
         g_groupClosePending=true;
         if(StringLen(g_groupCloseKey)>0) GlobalVariableSet(g_groupCloseKey,1.0);
         Print("AL-ZOOL RECOVERY TARGET REACHED: group net=",
               DoubleToString(net,2)," target=",DoubleToString(g_recoveryProfitTarget,2),
               " ",AccountInfoString(ACCOUNT_CURRENCY),
               " | closing ALL account positions.");
         RecoveryCloseAll();
      }
      return true;
   }
   if(!g_recoveryEnabled || g_recoveryLossTrigger<=0.0) return false;
   double totalLots=0.0;int totalPositions=0;
   double groupNet=RecoveryGroupNet(totalLots,totalPositions);
   if(totalPositions<=0){g_recoveryReferencePrice=0.0;return false;}
   if(groupNet>-g_recoveryLossTrigger) return false;
   // The loss threshold has been reached. The central position-action
   // planner decides whether a compatible x3 recovery can be authorized.
   // This local monitor must not open another, duplicate recovery trade.
   static datetime lastRecoveryCouncilBlockLog=0;
   if(TimeCurrent()-lastRecoveryCouncilBlockLog>=60)
   {
      Print("AL-ZOOL RECOVERY ARMED: net=",DoubleToString(groupNet,2),
            " trigger=",DoubleToString(g_recoveryLossTrigger,2),
            " | awaiting central position-action proposal / execution diagnostics.");
      lastRecoveryCouncilBlockLog=TimeCurrent();
   }
   return false;
}
void EvaluateAutoClose()
{
   int total=PositionsTotal();
   if(total<=0)
   {
      if(g_groupClosePending && StringLen(g_groupCloseKey)>0)
         GlobalVariableDel(g_groupCloseKey);
      g_groupClosePending=false;
      EvaluateSmartRecovery(); // Reset an old recovery cycle if all positions closed.
      return;
   }
   // Once a group profit target has been hit, retry closing EVERY remaining
   // position, even after the first success leaves only one position.
   // Never mistake that remaining position for a new "single TP" case.
   if(g_groupClosePending)
   {
      bool allAccepted=true;
      for(int k=PositionsTotal()-1;k>=0;k--)
      {
         ulong ticket=PositionGetTicket(k);
         if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
         if(!ClosePositionTicket(ticket,"group-pending")) allAccepted=false;
      }
      if(PositionsTotal()==0)
      {
         g_groupClosePending=false;
         if(StringLen(g_groupCloseKey)>0) GlobalVariableDel(g_groupCloseKey);
         Print("AL-ZOOL GROUP CLOSE COMPLETE: all open positions closed.");
      }
      else if(!allAccepted)
         Print("AL-ZOOL GROUP CLOSE PARTIAL: remaining=",PositionsTotal(),
               ". Retrying at next tick/timer.");
      return;
   }
   if(EvaluateSmartRecovery()) return;
   double groupNet=0.0;
   for(int i=total-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      groupNet+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
   }
   if(total>=2)
   {
      if(g_groupCloseEnabled && groupNet>=g_groupProfitTarget)
      {
         g_groupClosePending=true;
         if(StringLen(g_groupCloseKey)>0) GlobalVariableSet(g_groupCloseKey,1.0);
         Print("AL-ZOOL GROUP CLOSE START: ",DoubleToString(groupNet,2),
               " target=",DoubleToString(g_groupProfitTarget,2),
               " positions=",total,". All tickets will be retried until closed.");
         EvaluateAutoClose();
      }
      return;
   }
   if(total==1 && g_singleCloseEnabled)
   {
      ulong ticket=PositionGetTicket(0);
      if(ticket>0 && PositionSelectByTicket(ticket))
      {
         double net=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
         if(net>=g_singleProfitTarget)
            ClosePositionTicket(ticket,"single");
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
   if(!ConnectionReady()) return false;
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   // Historical sync is lower priority than live trading/telemetry.
   // Cap batches even when an old chart preset still requests 5000+ bars.
   int wanted=(int)MathMax(20,MathMin(500,InpHistoricalCandlesToSend));
   int copied=CopyRates(_Symbol,(ENUM_TIMEFRAMES)_Period,1,wanted,rates);
   if(copied<20)
   {
      Print("AL-ZOOL history upload skipped: only ",copied," closed bars are available.");
      return false;
   }
   static datetime lastUploadedClosedBar=0;
   if(lastUploadedClosedBar>0 && rates[0].time<=lastUploadedClosedBar)
      return true; // Do not resend unchanged historical ranges every 15 minutes.

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
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+ConnectionKey()+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("POST",BaseUrl()+"/v1/terminal-history/batch/"+ConnectionId(),
                       headers,6000,data,result,response_headers);
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(code!=200)
   {
      Print("AL-ZOOL historical upload failed: HTTP=",code," err=",GetLastError(),
            " body=",StringSubstr(response,0,400));
      return false;
   }
   lastUploadedClosedBar=rates[0].time;
   Print("AL-ZOOL historical candles uploaded: ",sent," bars (bounded noncritical sync).");
   return true;
}

int StrategyIdFromComment(const string comment)
{
   int p=StringFind(comment,"ALZOOL-S");
   if(p<0) return 0;
   int sid=(int)StringToInteger(StringSubstr(comment,p+8));
   return (sid>=1 && sid<=200 ? sid : 0);
}

bool SendLiveSnapshot()
{
   if(!ConnectionReady())
   {
      g_status=InpOwnerMode ? "أدخل مفتاح اقتران جهاز المالك" : "أدخل معرّف الاتصال ورمز العميل";
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
   body+=",\"mtf_bias\":{\"M5\":\""+MtfDirection(PERIOD_M5,g_m5Fast,g_m5Slow)+"\",\"M15\":\""+MtfDirection(PERIOD_M15,g_m15Fast,g_m15Slow)+"\",\"H1\":\""+MtfDirection(PERIOD_H1,g_h1Fast,g_h1Slow)+"\"}";
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

   ulong historyNow=GetTickCount64();
   bool historyRefreshed=(g_closedDealsDirty || g_lastClosedDealsBuildMs==0 ||
      historyNow-g_lastClosedDealsBuildMs>=30000);
   if(historyRefreshed)
   {
   // Cache 30-day deal history; avoid re-selecting and scanning it on every live heartbeat.
   string historyJson="";
   datetime history_from=TimeCurrent()-30*86400;
   if(HistorySelect(history_from,TimeCurrent()))
   {
      // Resolve each position's original ALZOOL strategy tag once from its entry deal.
      // This metadata travels with the closing deal so live learning can attribute outcomes.
      StrategyPositionLink strategy_links[];
      int strategy_link_count=0;
      int history_total=HistoryDealsTotal();
      for(int h=0;h<history_total;h++)
      {
         ulong entry_ticket=HistoryDealGetTicket(h);
         if(entry_ticket==0) continue;
         long entry_kind=HistoryDealGetInteger(entry_ticket,DEAL_ENTRY);
         if(entry_kind!=DEAL_ENTRY_IN && entry_kind!=DEAL_ENTRY_INOUT) continue;
         long position_id=HistoryDealGetInteger(entry_ticket,DEAL_POSITION_ID);
         string entry_comment=HistoryDealGetString(entry_ticket,DEAL_COMMENT);
         if(StringLen(entry_comment)==0)
         {
            ulong entry_order=(ulong)HistoryDealGetInteger(entry_ticket,DEAL_ORDER);
            if(entry_order>0) entry_comment=HistoryOrderGetString(entry_order,ORDER_COMMENT);
         }
         int strategy_id=StrategyIdFromComment(entry_comment);
         if(position_id<=0 || strategy_id<1) continue;
         int found=-1;
         for(int q=0;q<strategy_link_count;q++)
            if(strategy_links[q].position_id==position_id){found=q;break;}
         if(found<0)
         {
            ArrayResize(strategy_links,strategy_link_count+1);
            found=strategy_link_count++;
         }
         strategy_links[found].position_id=position_id;
         strategy_links[found].strategy_id=strategy_id;
         strategy_links[found].strategy_comment=entry_comment;
      }
      historyJson+=",\"closed_deals\":[";
      int deals_sent=0;
      int total_deals=HistoryDealsTotal();
      for(int h=total_deals-1;h>=0 && deals_sent<200;h--)
      {
         ulong deal_ticket=HistoryDealGetTicket(h);
         if(deal_ticket==0) continue;
         long entry=HistoryDealGetInteger(deal_ticket,DEAL_ENTRY);
         if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) continue;
         string deal_symbol=HistoryDealGetString(deal_ticket,DEAL_SYMBOL);
         if(deals_sent>0) historyJson+=",";
         long closed_position_id=HistoryDealGetInteger(deal_ticket,DEAL_POSITION_ID);
         int linked_strategy_id=0;
         string linked_strategy_comment="";
         for(int q=0;q<strategy_link_count;q++)
         {
            if(strategy_links[q].position_id==closed_position_id)
            {
               linked_strategy_id=strategy_links[q].strategy_id;
               linked_strategy_comment=strategy_links[q].strategy_comment;
               break;
            }
         }
         historyJson+="{\"ticket\":\""+(string)deal_ticket+"\"";
         historyJson+=",\"position_id\":\""+(string)closed_position_id+"\"";
         historyJson+=",\"strategy_id\":"+IntegerToString(linked_strategy_id);
         historyJson+=",\"strategy_comment\":\""+JsonEscape(linked_strategy_comment)+"\"";
         historyJson+=",\"symbol\":\""+JsonEscape(deal_symbol)+"\"";
         historyJson+=",\"type\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_TYPE));
         historyJson+=",\"deal_type\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_TYPE));
         historyJson+=",\"entry\":"+IntegerToString((int)entry);
         historyJson+=",\"reason\":"+IntegerToString((int)HistoryDealGetInteger(deal_ticket,DEAL_REASON));
         historyJson+=",\"volume\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_VOLUME),2);
         historyJson+=",\"price\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_PRICE),(int)SymbolInfoInteger(deal_symbol,SYMBOL_DIGITS));
         historyJson+=",\"profit\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_PROFIT));
         historyJson+=",\"commission\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_COMMISSION));
         historyJson+=",\"swap\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_SWAP));
         historyJson+=",\"fee\":"+JNum(HistoryDealGetDouble(deal_ticket,DEAL_FEE));
         historyJson+=",\"magic\":\""+IntegerToString((long)HistoryDealGetInteger(deal_ticket,DEAL_MAGIC))+"\"";
         historyJson+=",\"comment\":\""+JsonEscape(HistoryDealGetString(deal_ticket,DEAL_COMMENT))+"\"";
         historyJson+=",\"time\":"+IntegerToString((long)HistoryDealGetInteger(deal_ticket,DEAL_TIME))+"}";
         deals_sent++;
      }
      historyJson+="]";
   }
   else historyJson+=",\"closed_deals\":[]";

      g_cachedClosedDealsJson=historyJson;
      g_lastClosedDealsBuildMs=historyNow;
      g_closedDealsDirty=false;
   }
   // Only send the complete closed-deal archive on a change or refresh.
   // Account balance, positions, orders, ticks and candles remain live.
   body+=(historyRefreshed ? g_cachedClosedDealsJson :
      ",\"closed_deals\":[],\"closed_deals_unchanged\":true");

   MqlTick tick;
   bool have_tick=SymbolInfoTick(_Symbol,tick);
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   body+=",\"tick\":{\"bid\":"+JNum(have_tick?tick.bid:0.0,digits);
   body+=",\"point_size\":"+JNum(_Point,10);
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
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+ConnectionKey()+"\r\n";
   string response_headers;
   ResetLastError();
   ulong requestStartedMs=GetTickCount64();
   int code=WebRequest("POST",BaseUrl()+"/v1/terminal-state/live/"+ConnectionId(),
                       headers,InpHttpTimeoutMs,data,result,response_headers);
   ulong totalRequestMs=GetTickCount64()-requestStartedMs;
   if(code!=200)
   {
      string response=CharArrayToString(result,0,-1,CP_UTF8);
      g_status="فشل إرسال بيانات الحساب؛ HTTP="+IntegerToString(code)+
               " err="+IntegerToString(GetLastError());
      Print("AL-ZOOL telemetry error: HTTP=",code," body=",StringSubstr(response,0,500));
      g_closedDealsDirty=true; // Resend history if the previous POST failed.
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   g_status="متصل — تم إرسال بيانات الحساب والصفقات والشموع";
   // Distinguish server CPU time from end-to-end network time.
   // Report every 30 seconds (or when slow), without printing pairing keys.
   static ulong lastMetricLogMs=0;
   ulong stamp=GetTickCount64();
   if(totalRequestMs>1500 || lastMetricLogMs==0 || stamp-lastMetricLogMs>=30000)
   {
      double serverMs=JsonDouble(response,"server_processing_ms",-1.0);
      Print("AL-ZOOL LATENCY telemetry_http_ms=",totalRequestMs,
            " server_processing_ms=",DoubleToString(serverMs,1),
            " payload_bytes=",ArraySize(data));
      lastMetricLogMs=stamp;
   }
   return true;
}

int OnInit()
{
   if(InpHeartbeatSeconds<2 || InpCandlesToSend<20 || InpCandlesToSend>120 ||
      InpHistoricalCandlesToSend<20 || InpHistoricalCandlesToSend>10000 || InpHistoryUploadMinutes<5 ||
      InpTradeLots<=0 || InpMaxCentralActionLots<0 || InpMaxCentralTotalLots<0 ||
      InpMaxOpenPositions<0 || InpATRPeriod<2 ||
      InpStopLossATR<=0 || InpTrailStartATR<=0 || InpTrailDistanceATR<=0)
   {
      Print("AL-ZOOL: invalid heartbeat, candles, lot, position cap or protection settings");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_groupCloseKey="AZ_GROUP_CLOSE_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   g_groupClosePending=(GlobalVariableCheck(g_groupCloseKey) && GlobalVariableGet(g_groupCloseKey)>0.5);
   g_recoveryActiveKey="AZ_REC_ACTIVE_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   g_recoveryPriceKey="AZ_REC_PRICE_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   g_recoveryCycleActive=(GlobalVariableCheck(g_recoveryActiveKey) && GlobalVariableGet(g_recoveryActiveKey)>0.5) || RecoveryHasOpenTrade();
   if(GlobalVariableCheck(g_recoveryPriceKey)) g_recoveryReferencePrice=GlobalVariableGet(g_recoveryPriceKey);
   if(g_recoveryCycleActive && !GlobalVariableCheck(g_recoveryActiveKey)) GlobalVariableSet(g_recoveryActiveKey,1.0);
   g_trade.SetAsyncMode(false);
   g_trade.SetExpertMagicNumber(InpTradeMagic);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_atrHandle=iATR(_Symbol,(ENUM_TIMEFRAMES)_Period,InpATRPeriod);
   if(g_atrHandle==INVALID_HANDLE) { Print("AL-ZOOL ATR init failed: ",GetLastError()); return INIT_FAILED; }
   g_emaFastHandle=iMA(_Symbol,(ENUM_TIMEFRAMES)_Period,9,0,MODE_EMA,PRICE_CLOSE);
   g_emaSlowHandle=iMA(_Symbol,(ENUM_TIMEFRAMES)_Period,21,0,MODE_EMA,PRICE_CLOSE);
   g_m5Fast=iMA(_Symbol,PERIOD_M5,9,0,MODE_EMA,PRICE_CLOSE); g_m5Slow=iMA(_Symbol,PERIOD_M5,21,0,MODE_EMA,PRICE_CLOSE);
   g_m15Fast=iMA(_Symbol,PERIOD_M15,9,0,MODE_EMA,PRICE_CLOSE); g_m15Slow=iMA(_Symbol,PERIOD_M15,21,0,MODE_EMA,PRICE_CLOSE);
   g_h1Fast=iMA(_Symbol,PERIOD_H1,9,0,MODE_EMA,PRICE_CLOSE); g_h1Slow=iMA(_Symbol,PERIOD_H1,21,0,MODE_EMA,PRICE_CLOSE);
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
   if(g_m5Fast!=INVALID_HANDLE) IndicatorRelease(g_m5Fast); if(g_m5Slow!=INVALID_HANDLE) IndicatorRelease(g_m5Slow);
   if(g_m15Fast!=INVALID_HANDLE) IndicatorRelease(g_m15Fast); if(g_m15Slow!=INVALID_HANDLE) IndicatorRelease(g_m15Slow);
   if(g_h1Fast!=INVALID_HANDLE) IndicatorRelease(g_h1Fast); if(g_h1Slow!=INVALID_HANDLE) IndicatorRelease(g_h1Slow);
   Comment("");
}
void OnTimer()
{
   EvaluateAutoClose();
   ManageEntryTrailing();
   ManagePositionAddons();
   ulong now=GetTickCount64();
   // Publish fresh prices and closed-bar data BEFORE asking for a decision.
   // Previously the decision poll ran first and often saw the previous heartbeat.
   if(g_lastSnapshotMs==0 || now-g_lastSnapshotMs>=((ulong)InpHeartbeatSeconds*1000))
   {
      bool sent=SendLiveSnapshot();
      g_online=sent;
      if(!sent) g_serverTradingEnabled=false;
      g_lastSnapshotMs=GetTickCount64();
   }
   if(g_lastSettingsPollMs==0 || now-g_lastSettingsPollMs>=1000)
   {
      PollTradeSettings();
      g_lastSettingsPollMs=now;
   }
   if(g_lastDecisionPollMs==0 || now-g_lastDecisionPollMs>=2000)
   {
      PollCentralDecision();
      g_lastDecisionPollMs=GetTickCount64();
   }
   if(InpEnableCentralPositionManagement &&
      (g_lastActionPollMs==0 || now-g_lastActionPollMs>=3000))
   {
      PollCentralActions();
      g_lastActionPollMs=now;
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
           "\nRecovery target: ",DoubleToString(g_recoveryProfitTarget,2)," ",AccountInfoString(ACCOUNT_CURRENCY),
           "\nPositions: ",IntegerToString(PositionsTotal()),
           " | Pending: ",IntegerToString(OrdersTotal()));
}
void OnTrade()
{
   // A newly closed deal should appear immediately in the next live snapshot.
   g_closedDealsDirty=true;
}
void OnTick()
{
   EvaluateAutoClose();
}
// Profit targets are evaluated locally on market ticks and a one-second timer.
