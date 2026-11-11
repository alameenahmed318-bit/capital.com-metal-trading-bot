//+------------------------------------------------------------------+
//| AL-ZOOL MT5 CONNECTION CLIENT                                    |
//| Connection and terminal identity heartbeat ONLY.                 |
//| No trading, no positions, no orders, no candles, no signals.     |
//+------------------------------------------------------------------+
#property strict
#property version "2.00"
#property description "AL-ZOOL MT5 connection-only client"

input group "بيانات الاتصال بالخادم"
input string InpServerUrl = "https://al-zool-gold-sniper.onrender.com";
input string InpEngineId = "";
input string InpEngineToken = "";
input int InpHttpTimeoutMs = 8000;
input int InpHeartbeatSeconds = 10;

bool g_online=false;
string g_status="بدء الاتصال";

string BaseUrl()
{
   string u=InpServerUrl;
   while(StringLen(u)>0 && StringGetCharacter(u,StringLen(u)-1)==47) u=StringSubstr(u,0,StringLen(u)-1);
   return u;
}
string JsonEscape(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r"," ");
   StringReplace(s,"\n"," ");
   return s;
}
bool HttpGet(const string url)
{
   char data[],result[];
   string headers;
   ResetLastError();
   int code=WebRequest("GET",url,"",InpHttpTimeoutMs,data,result,headers);
   if(code!=200)
   {
      g_status="الخادم غير متاح؛ HTTP="+IntegerToString(code)+" err="+IntegerToString(GetLastError());
      return false;
   }
   return true;
}
bool SendHeartbeat()
{
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20)
   {
      g_status="أدخل معرّف الاتصال ورمز الترخيص";
      return false;
   }
   string url=BaseUrl()+"/v1/terminal-state/live/"+InpEngineId;
   string body="{\"account\":{\"login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+
      "\",\"server\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+
      "\",\"company\":\""+JsonEscape(AccountInfoString(ACCOUNT_COMPANY))+
      "\",\"currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+
      "\",\"leverage\":"+IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE))+
      "},\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+
      "\",\"terminal_build\":"+IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD))+"}";
   char data[],result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("POST",url,headers,InpHttpTimeoutMs,data,result,response_headers);
   if(code!=200)
   {
      g_status="تعذر إرسال نبضة الاتصال؛ HTTP="+IntegerToString(code)+" err="+IntegerToString(GetLastError());
      return false;
   }
   g_status="متصل بالخادم — اتصال فقط";
   return true;
}
int OnInit()
{
   if(InpHeartbeatSeconds<5) { Print("AL-ZOOL: heartbeat must be 5 seconds or more"); return INIT_PARAMETERS_INCORRECT; }
   EventSetTimer(InpHeartbeatSeconds);
   g_status="تم تشغيل عميل الاتصال فقط";
   Print("AL-ZOOL CONNECTION ONLY: no trade functions are included.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer()
{
   static ulong lastHealth=0;
   ulong now=GetTickCount64();
   if(now-lastHealth>60000)
   {
      g_online=HttpGet(BaseUrl()+"/health");
      lastHealth=now;
   }
   bool sent=SendHeartbeat();
   if(sent && !g_online) g_online=true;
   Comment("AL-ZOOL MT5 CONNECTION ONLY\n",g_status,"\nServer: ",BaseUrl());
}
void OnTick() {}
// No Trade.mqh include, no CTrade, and no order/position modification APIs.
