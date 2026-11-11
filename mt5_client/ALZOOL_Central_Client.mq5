//+------------------------------------------------------------------+
//| AL-ZOOL MT5 LIVE DATA CLIENT                                      |
//| Sends account, positions, pending orders, tick and candles only.  |
//| Keeps the existing licence/connection protocol. NO trade actions.|
//+------------------------------------------------------------------+
#property strict
#property version "2.20"
#property description "AL-ZOOL MT5 live telemetry client - no trade execution"

input group "بيانات الاتصال بالخادم"
input string InpServerUrl = "https://al-zool-gold-sniper.onrender.com";
input string InpEngineId = "";
input string InpEngineToken = "";
input int InpHttpTimeoutMs = 8000;
input int InpHeartbeatSeconds = 5;
input int InpCandlesToSend = 120;

bool g_online=false;
string g_status="بدء الاتصال";

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
void AppendCandle(string &out,const MqlRates &bar,int digits)
{
   out+="{\"time\":"+IntegerToString((long)bar.time);
   out+=",\"open\":"+JNum(bar.open,digits);
   out+=",\"high\":"+JNum(bar.high,digits);
   out+=",\"low\":"+JNum(bar.low,digits);
   out+=",\"close\":"+JNum(bar.close,digits);
   out+=",\"tick_volume\":"+IntegerToString((long)bar.tick_volume)+"}";
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
      for(int h=total_deals-1;h>=0 && deals_sent<500;h--)
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
   if(InpHeartbeatSeconds<5 || InpCandlesToSend<20 || InpCandlesToSend>120)
   {
      Print("AL-ZOOL: heartbeat must be >=5 seconds and candles 20..120");
      return INIT_PARAMETERS_INCORRECT;
   }
   EventSetTimer(InpHeartbeatSeconds);
   g_status="تم تشغيل عميل البيانات المباشرة";
   Print("AL-ZOOL LIVE TELEMETRY: account/positions/orders/candles only. No trading functions.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}
void OnTimer()
{
   static ulong lastHealth=0;
   ulong now=GetTickCount64();
   if(lastHealth==0 || now-lastHealth>=60000)
   {
      g_online=HttpGet(BaseUrl()+"/health");
      lastHealth=now;
   }
   bool sent=SendLiveSnapshot();
   if(sent) g_online=true;
   Comment("AL-ZOOL MT5 LIVE DATA CLIENT\n",g_status,
           "\nServer: ",BaseUrl(),
           "\nSymbol: ",_Symbol," | TF: ",EnumToString((ENUM_TIMEFRAMES)_Period),
           "\nOpen positions: ",IntegerToString(PositionsTotal()),
           " | Pending orders: ",IntegerToString(OrdersTotal()),
           "\nTrading execution: DISABLED");
}
void OnTick() {}
// This client reports data only. It cannot open, close, or modify trades.
