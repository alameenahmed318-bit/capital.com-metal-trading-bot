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
input group "الأرشيف التاريخي للاختبار"
input int InpHistoricalCandlesToSend = 5000;
input int InpHistoryUploadMinutes = 15;

bool g_online=false;
datetime g_lastHistoryAttempt=0;
string g_status="بدء الاتصال";
bool g_singleCloseEnabled=false,g_groupCloseEnabled=false;
double g_singleProfitTarget=1.0,g_groupProfitTarget=5.0;
ulong g_lastSnapshotMs=0,g_lastSettingsPollMs=0,g_lastDecisionPollMs=0;
string g_aiSignal="WAIT",g_aiReason="بانتظار إشارة المحرك المركزي";
double g_aiConfidence=0.0;
long g_aiBarTime=0;

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
   g_singleCloseEnabled=JsonBool(response,"single_enabled");
   g_groupCloseEnabled=JsonBool(response,"group_enabled");
   g_singleProfitTarget=JsonDouble(response,"single_profit_target",1.0);
   g_groupProfitTarget=JsonDouble(response,"group_profit_target",5.0);
   if(g_singleProfitTarget<=0) g_singleCloseEnabled=false;
   if(g_groupProfitTarget<=0) g_groupCloseEnabled=false;
   return true;
}
bool PollCentralDecision()
{
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20) return false;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Authorization: Bearer "+InpEngineToken+"\\r\\n";
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
   StringReplace(signal,"\\\"","");
   StringReplace(reason,"\\\"","");
   if(signal!="BUY" && signal!="SELL") signal="WAIT";
   double parsed_confidence=StringToDouble(confidence);
   long parsed_bar=(long)StringToInteger(bar_time);
   if(parsed_bar!=g_aiBarTime || signal!=g_aiSignal)
      Print("AL-ZOOL CENTRAL AI: ",signal," | confidence=",DoubleToString(parsed_confidence*100.0,1),
            "% | ",reason);
   g_aiSignal=signal;
   g_aiReason=reason;
   g_aiConfidence=MathMax(0.0,MathMin(1.0,parsed_confidence));
   g_aiBarTime=parsed_bar;
   return true;
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

   // Closed deals for the last 30 days; telemetry only.
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
         if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) continue;
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
      InpHistoricalCandlesToSend<20 || InpHistoricalCandlesToSend>10000 || InpHistoryUploadMinutes<5)
   {
      Print("AL-ZOOL: heartbeat >=5s, live candles 20..120, history 20..10000, history interval >=5 minutes");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_trade.SetAsyncMode(false);
   EventSetTimer(1);
   g_status="تم تشغيل بيانات MT5 وحماية الأرباح المحلية";
   Print("AL-ZOOL MT5 client started: local profit protection is OFF until enabled in Trade Management.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}
void OnTimer()
{
   EvaluateAutoClose();
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
   Comment("AL-ZOOL MT5 LIVE CLIENT\\n",g_status,
           "\\nCentral AI: ",g_aiSignal," (",DoubleToString(g_aiConfidence*100.0,1),"%)",
           "\\n",g_aiReason,
           "\\nProfit close: single ",(g_singleCloseEnabled?"ON":"OFF"),
           " / group ",(g_groupCloseEnabled?"ON":"OFF"),
           "\\nPositions: ",IntegerToString(PositionsTotal()),
           " | Pending: ",IntegerToString(OrdersTotal()));
}
void OnTick()
{
   EvaluateAutoClose();
}
// Profit targets are evaluated locally on market ticks and a one-second timer.
