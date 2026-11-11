//+------------------------------------------------------------------+
//| AL-ZOOL Central MT5 Client                                       |
//| MT5 connector + broker execution adapter.                         |
//| All trading controls and settings are owned by the central tool.  |
//+------------------------------------------------------------------+
#property strict
#property version   "1.10"
#property description "AL-ZOOL central decision client for MetaTrader 5"

#include <Trade/Trade.mqh>

input group "Central server"
input string InpServerUrl = "https://al-zool-gold-sniper.onrender.com";
input string InpEngineId = "";
input string InpEngineToken = "";
input int    InpHttpTimeoutMs = 12000;
// Technical identifiers only; not user trading controls.
#define ALZOOL_MAGIC 26110801
#define ALZOOL_REQUEST_SECONDS 1
#define ALZOOL_BARS_TO_SEND 120

CTrade trade;
datetime g_last_bar_time = 0;
ulong g_last_health_check_ms = 0;
ulong g_last_market_poll_ms = 0;
ulong g_last_terminal_state_ms = 0;
string g_last_decision_id = "";
bool g_server_online = false;
bool g_decision_verified = false;
string g_status = "Starting";
string g_last_reason = "";
string g_last_strategy_name = "";
double g_last_confidence = 0.0;
int g_last_strategy_id = 0;
string g_last_decision = "WAIT";
string g_global_bar_key = "";
double g_last_market_bid=0.0,g_last_market_ask=0.0;
ulong g_decision_received_ms=0;

// Live dashboard settings, fetched through the authenticated central API.
string g_context_regime="";

void SendSignalEvent(const string event_id,const string event_type,const string payload);
void RecordBlockedSignal(const string decision_id,const string key,const string reason);
string JsonSafe(string value);

string ServerBaseUrl()
{
   string url=InpServerUrl;
   while(StringLen(url)>0 && StringGetCharacter(url,StringLen(url)-1)==47)
      url=StringSubstr(url,0,StringLen(url)-1);
   return url;
}

//--- small JSON reader for the flat fields returned by service.py
string JsonString(const string source,const string key)
{
   // Accept standard JSON whitespace around the colon (Python json.dumps
   // emits ": " by default). The old exact-match parser rejected valid
   // server responses and caused "Decision signature invalid".
   string needle="\"" + key + "\"";
   int p=StringFind(source,needle);
   if(p<0) return "";
   p+=StringLen(needle);

   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\t' || ch=='\r' || ch=='\n') p++;
      else break;
   }
   if(p>=StringLen(source) || StringGetCharacter(source,p)!=':') return "";
   p++;

   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\t' || ch=='\r' || ch=='\n') p++;
      else break;
   }
   if(p>=StringLen(source) || StringGetCharacter(source,p)!='"') return "";
   p++;

   string value="";
   bool escaped=false;
   for(int i=p;i<StringLen(source);i++)
   {
      ushort ch=StringGetCharacter(source,i);
      if(escaped)
      {
         if(ch=='"' || ch=='\\' || ch=='/') value+=ShortToString(ch);
         else if(ch=='n') value+="\n";
         else if(ch=='r') value+="\r";
         else if(ch=='t') value+="\t";
         else value+=ShortToString(ch);
         escaped=false;
         continue;
      }
      if(ch=='\\') { escaped=true; continue; }
      if(ch=='"') return value;
      value+=ShortToString(ch);
   }
   return "";
}

string BytesToHex(const uchar &bytes[])
{
   string out="";
   for(int i=0;i<ArraySize(bytes);i++)
      out+=StringFormat("%02x",(int)bytes[i]);
   return out;
}

bool SHA256(const uchar &source_bytes[],uchar &digest[])
{
   uchar empty[];
   ArrayResize(empty,0);
   return CryptEncode(CRYPT_HASH_SHA256,source_bytes,empty,digest)==32;
}

bool HMAC_SHA256_HEX(const string message,const string secret,string &hex)
{
   uchar key[],msg[];
   StringToCharArray(secret,key,0,WHOLE_ARRAY,CP_UTF8);
   StringToCharArray(message,msg,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(key)>0) ArrayResize(key,ArraySize(key)-1);
   if(ArraySize(msg)>0) ArrayResize(msg,ArraySize(msg)-1);
   if(ArraySize(key)==0 || ArraySize(msg)==0) return false;

   if(ArraySize(key)>64)
   {
      uchar hashed[];
      if(!SHA256(key,hashed)) return false;
      ArrayCopy(key,hashed);
   }
   uchar ipad[],opad[];
   ArrayResize(ipad,64);
   ArrayResize(opad,64);
   for(int i=0;i<64;i++)
   {
      uchar b=(i<ArraySize(key) ? key[i] : 0);
      ipad[i]=(uchar)(b ^ 0x36);
      opad[i]=(uchar)(b ^ 0x5c);
   }
   uchar inner[];
   ArrayResize(inner,64+ArraySize(msg));
   ArrayCopy(inner,ipad,0,0,64);
   ArrayCopy(inner,msg,64,0,ArraySize(msg));
   uchar inner_hash[];
   if(!SHA256(inner,inner_hash)) return false;

   uchar outer[];
   ArrayResize(outer,64+ArraySize(inner_hash));
   ArrayCopy(outer,opad,0,0,64);
   ArrayCopy(outer,inner_hash,64,0,ArraySize(inner_hash));
   uchar final_hash[];
   if(!SHA256(outer,final_hash)) return false;
   hex=BytesToHex(final_hash);
   return (StringLen(hex)==64);
}

double JsonNumber(const string source,const string key,double fallback=0.0);

bool VerifyDecisionSignature(const string response)
{
   string signed_payload=JsonString(response,"signed_payload");
   string supplied=JsonString(response,"signature");
   string client_id=JsonString(response,"client_id");
   string decision=JsonString(response,"decision");
   string decision_id=JsonString(response,"decision_id");
   int strategy_id=(int)JsonNumber(response,"strategy_id",-1.0);
   double confidence=JsonNumber(response,"confidence",-1.0);
   double expires_at=JsonNumber(response,"expires_at",0.0);
   if(signed_payload=="" || supplied=="" || client_id!=InpEngineId ||
      decision_id=="" || (decision!="BUY" && decision!="SELL" && decision!="WAIT") ||
      strategy_id<0 || confidence<0.0 || expires_at<=0.0 || TimeGMT()>=(datetime)expires_at)
      return false;

   string fields[];
   ushort separator=StringGetCharacter("|",0);
   if(StringSplit(signed_payload,separator,fields)!=6) return false;
   if(fields[0]!=client_id || fields[1]!=decision_id || fields[2]!=decision ||
      (int)StringToInteger(fields[3])!=strategy_id ||
      MathAbs(StringToDouble(fields[4])-confidence)>0.00000002 ||
      MathAbs(StringToDouble(fields[5])-expires_at)>0.000001)
      return false;

   string expected="";
   if(!HMAC_SHA256_HEX(signed_payload,InpEngineToken,expected)) return false;
   if(StringLen(expected)!=StringLen(supplied)) return false;
   StringToLower(expected);
   StringToLower(supplied);
   return (expected==supplied);
}

bool JsonBool(const string source,const string key,bool fallback=false)
{
   string needle="\"" + key + "\"";
   int p=StringFind(source,needle);
   if(p<0) return fallback;
   p+=StringLen(needle);
   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\\t' || ch=='\\r' || ch=='\\n') p++;
      else break;
   }
   if(p>=StringLen(source) || StringGetCharacter(source,p)!=':') return fallback;
   p++;
   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\\t' || ch=='\\r' || ch=='\\n') p++;
      else break;
   }
   if(StringSubstr(source,p,4)=="true") return true;
   if(StringSubstr(source,p,5)=="false") return false;
   return fallback;
}

double JsonNumber(const string source,const string key,double fallback)
{
   string needle="\"" + key + "\"";
   int p=StringFind(source,needle);
   if(p<0) return fallback;
   p+=StringLen(needle);
   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\\t' || ch=='\\r' || ch=='\\n') p++;
      else break;
   }
   if(p>=StringLen(source) || StringGetCharacter(source,p)!=':') return fallback;
   p++;
   while(p<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,p);
      if(ch==' ' || ch=='\\t' || ch=='\\r' || ch=='\\n') p++;
      else break;
   }
   int e=p;
   while(e<StringLen(source))
   {
      ushort ch=StringGetCharacter(source,e);
      if((ch>='0' && ch<='9') || ch=='-' || ch=='+' || ch=='.' || ch=='e' || ch=='E') e++;
      else break;
   }
   if(e<=p) return fallback;
   return StringToDouble(StringSubstr(source,p,e-p));
}

bool BuildMarketPayload(string &body,datetime &bar_time)
{
   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied=CopyRates(_Symbol,PERIOD_CURRENT,1,ALZOOL_BARS_TO_SEND,rates);
   if(copied<65) return false;
   bar_time=rates[copied-1].time;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return false;
   g_last_market_bid=tick.bid; g_last_market_ask=tick.ask;

   // Strategy mode and selection are enforced by the central dashboard.
   body="{\"symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",";
   body+="\"strategy_mode\":\"AUTO\",\"selected_strategy_id\":0,";
   body+="\"bid\":"+DoubleToString(tick.bid,_Digits)+",";
   body+="\"ask\":"+DoubleToString(tick.ask,_Digits)+",";
   body+="\"candles\":[";
   for(int i=0;i<copied;i++)
   {
      if(i>0) body+=",";
      body+="{\"time\":"+IntegerToString((long)rates[i].time);
      body+=",\"open\":"+DoubleToString(rates[i].open,_Digits);
      body+=",\"high\":"+DoubleToString(rates[i].high,_Digits);
      body+=",\"low\":"+DoubleToString(rates[i].low,_Digits);
      body+=",\"close\":"+DoubleToString(rates[i].close,_Digits);
      body+=",\"tick_volume\":"+IntegerToString((long)rates[i].tick_volume)+"}";
   }
   body+="]}";
   return true;
}

bool CheckCentralHealth()
{
   ulong now_ms=GetTickCount64();
   ulong interval_ms=10000;
   if(g_last_health_check_ms>0 && now_ms-g_last_health_check_ms<interval_ms)
      return g_server_online;
   g_last_health_check_ms=now_ms;

   if(StringLen(InpServerUrl)<10 || StringFind(InpServerUrl,"YOUR-SERVER")>=0)
   {
      g_server_online=false;
      g_decision_verified=false;
      g_status="Set server URL";
      return false;
   }

   char data[],result[];
   ArrayResize(data,0);
   string response_headers;
   string headers="Accept: application/json\r\n";
   ResetLastError();
   int http=WebRequest("GET",ServerBaseUrl()+"/health",headers,InpHttpTimeoutMs,
                       data,result,response_headers);
   if(http!=200)
   {
      g_server_online=false;
      g_decision_verified=false;
      g_status=(http==-1 ? "Health check failed / WebRequest error "+IntegerToString(GetLastError())
                         : "Health check HTTP "+IntegerToString(http));
      return false;
   }

   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(JsonString(response,"status")!="online")
   {
      g_server_online=false;
      g_decision_verified=false;
      g_status="Health response invalid";
      return false;
   }

   g_server_online=true;
   g_status="Server online / waiting for next closed candle";
   return true;
}

bool RequestCentralDecision(string &decision,string &decision_id,string &reason,
                             double &confidence,int &strategy_id)
{
   decision="";
   decision_id="";
   reason="";
   confidence=0.0;
   strategy_id=0;

   if(StringLen(InpServerUrl)<10 || StringFind(InpServerUrl,"YOUR-SERVER")>=0)
   {
      g_status="Set server URL";
      return false;
   }
   if(InpEngineId=="" || InpEngineToken=="")
   {
      g_status="Set owner engine credentials";
      return false;
   }

   string body;
   datetime bar_time=0;
   if(!BuildMarketPayload(body,bar_time))
   {
      g_status="Waiting for 65+ closed candles";
      return false;
   }
   if(bar_time==g_last_bar_time)
   {
      g_status="Waiting for next closed candle";
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return false;

   string url=ServerBaseUrl()+"/v1/market/"+InpEngineId;
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   char data[];
   char result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string response_headers;
   ResetLastError();
   ulong request_started_ms=GetTickCount64();
   int http=WebRequest("POST",url,headers,InpHttpTimeoutMs,data,result,response_headers);
   ulong market_http_ms=GetTickCount64()-request_started_ms;
   double candle_close_lag_sec=MathMax(0.0,(double)(TimeCurrent()-(bar_time+PeriodSeconds((ENUM_TIMEFRAMES)_Period))));
   Print("AL-ZOOL LATENCY market_http_ms=",market_http_ms,
         " candle_close_lag_sec=",DoubleToString(candle_close_lag_sec,2),
         " http=",http);
   if(http==-1)
   {
      g_decision_verified=false;
      g_server_online=false;
      g_status="Server offline / WebRequest error "+IntegerToString(GetLastError());
      return false; // fail closed: no new entries
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(http!=200)
   {
      g_decision_verified=false;
      g_server_online=true;
      g_status="Server reachable / Market HTTP "+IntegerToString(http);
      g_last_reason=response;
      return false;
   }

   if(!VerifyDecisionSignature(response))
   {
      g_decision_verified=false;
      g_server_online=true;
      g_status="Server reachable / Decision signature invalid";
      return false; // never trade on an unsigned or altered server response
   }
   decision=JsonString(response,"decision");
   decision_id=JsonString(response,"decision_id");
   reason=JsonString(response,"reason_ar");
   confidence=JsonNumber(response,"confidence",0.0);
   strategy_id=(int)JsonNumber(response,"strategy_id",0);
   g_last_strategy_name=JsonString(response,"strategy_name");
   g_last_confidence=confidence;
   g_context_regime=JsonString(response,"regime");
   g_last_strategy_id=strategy_id;
   g_last_decision=decision;
   if(decision!="BUY" && decision!="SELL" && decision!="WAIT")
   {
      g_decision_verified=false;
      g_server_online=false;
      g_status="Invalid server response";
      return false;
   }
   if(decision_id=="")
   {
      g_decision_verified=false;
      g_server_online=false;
      g_status="Missing decision id";
      return false;
   }
   g_server_online=true;
   g_decision_verified=true;
   g_decision_received_ms=GetTickCount64();
   g_last_bar_time=bar_time;
   // Persist as soon as a fresh server decision is received. This favors
   // duplicate prevention over retrying a rejected order on the same candle.
   GlobalVariableSet(g_global_bar_key,(double)g_last_bar_time);
   g_status="Server online / "+decision;
   g_last_reason=reason;
   return true;
}

string OrderStrategyKey(const ulong order)
{
   return "AZR_"+(string)ALZOOL_MAGIC+"_"+(string)order;
}

string PositionStrategyKey(const ulong position_id)
{
   return "AZS_"+(string)ALZOOL_MAGIC+"_"+(string)position_id;
}

string PendingOutcomePrefix()
{
   return "AZO_"+(string)ALZOOL_MAGIC+"_";
}

bool PositionIdentifierIsOpen(const ulong position_id)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==position_id) return true;
   }
   return false;
}

void QueueClosedDealOutcome(const string outcome_id,const int strategy_id,const double pnl)
{
   if(outcome_id=="" || strategy_id<1 || strategy_id>200 || !MathIsValidNumber(pnl)) return;
   string base=PendingOutcomePrefix()+outcome_id;
   GlobalVariableSet(base+"_S",(double)strategy_id);
   GlobalVariableSet(base+"_P",pnl);
}

void FlushPendingOutcomes()
{
   if(InpServerUrl=="" || InpEngineId=="" || InpEngineToken=="") return;
   string prefix=PendingOutcomePrefix();
   string url=ServerBaseUrl()+"/v1/outcome/"+InpEngineId;
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   int outcome_timeout=(int)MathMin(InpHttpTimeoutMs,1000);

   // Send at most one queued result per timer cycle so a backlog cannot
   // monopolize the terminal and delay local stop management.
   for(int i=GlobalVariablesTotal()-1;i>=0;i--)
   {
      string name=GlobalVariableName(i);
      if(StringFind(name,prefix)!=0 || StringFind(name,"_S")>=0 || StringFind(name,"_P")>=0) continue;
      string suffix=StringSubstr(name,StringLen(prefix));
      string skey=name+"_S";
      string pkey=name+"_P";
      if(suffix=="" || !GlobalVariableCheck(skey) || !GlobalVariableCheck(pkey)) continue;

      int strategy_id=(int)GlobalVariableGet(skey);
      double pnl=GlobalVariableGet(pkey);
      string body="{\"deal_id\":\""+JsonSafe(suffix)+"\",\"strategy_id\":"+IntegerToString(strategy_id)+
                  ",\"pnl\":"+DoubleToString(pnl,8)+"}";
      char data[],result[];
      StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
      if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
      string response_headers;
      ResetLastError();
      int http=WebRequest("POST",url,headers,outcome_timeout,data,result,response_headers);
      if(http==200)
      {
         GlobalVariableDel(name);
         GlobalVariableDel(skey);
         GlobalVariableDel(pkey);
         g_status="نتيجة الصفقة محفوظة في Supabase / strategy "+IntegerToString(strategy_id);
         Print("AL-ZOOL LEARNING outcome accepted: id=",suffix," strategy=",strategy_id," pnl=",DoubleToString(pnl,2),
               " response=",CharArrayToString(result,0,-1,CP_UTF8));
      }
      else
      {
         string response_text=CharArrayToString(result,0,-1,CP_UTF8);
         if(http==-1) g_status="Outcome saved locally; server offline";
         else g_status="Outcome pending / HTTP "+IntegerToString(http);
         Print("AL-ZOOL LEARNING upload failed: HTTP=",http," err=",GetLastError()," body=",response_text,
               " outcome_id=",suffix," strategy=",strategy_id);
      }
      return;
   }
}

int StrategyFromDealComment(const ulong deal)
{
   string deal_comment=HistoryDealGetString(deal,DEAL_COMMENT);
   if(StringFind(deal_comment,"AZS")!=0) return 0;
   int strategy_id=(int)StringToInteger(StringSubstr(deal_comment,3));
   if(strategy_id<1 || strategy_id>200) return 0;
   return strategy_id;
}

int ResolveEntryStrategy(const ulong deal,const ulong order)
{
   string order_key=OrderStrategyKey(order);
   if(GlobalVariableCheck(order_key))
   {
      int strategy_id=(int)GlobalVariableGet(order_key);
      GlobalVariableDel(order_key);
      if(strategy_id>=1 && strategy_id<=200) return strategy_id;
   }
   return StrategyFromDealComment(deal);
}

int StrategyForPositionFromHistory(const ulong position_id)
{
   datetime now=TimeCurrent();
   if(!HistorySelect(now-90*86400,now)) return 0;
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if((ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID)!=position_id) continue;
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=ALZOOL_MAGIC) continue;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
      int strategy_id=StrategyFromDealComment(deal);
      if(strategy_id>=1 && strategy_id<=200) return strategy_id;
      ulong entry_order=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
      string order_key=OrderStrategyKey(entry_order);
      if(GlobalVariableCheck(order_key))
      {
         strategy_id=(int)GlobalVariableGet(order_key);
         if(strategy_id>=1 && strategy_id<=200) return strategy_id;
      }
   }
   return 0;
}

void SendSignalEvent(const string event_id,const string event_type,const string payload)
{
   if(InpEngineId=="" || InpEngineToken=="") return;
   string body="{\"events\":[{\"event_id\":\""+JsonSafe(event_id)+
               "\",\"event_type\":\""+JsonSafe(event_type)+
               "\",\"payload\":"+payload+"}]}";
   char data[],result[]; StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n",response_headers;
   int http=WebRequest("POST",ServerBaseUrl()+"/v1/signals/events/"+InpEngineId,
                       headers,MathMin(InpHttpTimeoutMs,1800),data,result,response_headers);
   if(http!=200) Print("AL-ZOOL signal event upload failed. HTTP=",http," err=",GetLastError());
}

void RecordBlockedSignal(const string decision_id,const string key,const string reason)
{
   if(decision_id=="") return;
   string payload="{\"decision_id\":\""+JsonSafe(decision_id)+"\",\"decision\":\""+JsonSafe(g_last_decision)+
      "\",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"strategy_id\":"+IntegerToString(g_last_strategy_id)+
      ",\"confidence\":"+DoubleToString(g_last_confidence,4)+",\"reason_ar\":\""+JsonSafe(reason)+"\"}";
   SendSignalEvent(decision_id+"_blocked_"+key,"BLOCKED",payload);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD || trans.deal==0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetString(trans.deal,DEAL_SYMBOL)!=_Symbol) return;

   ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal,DEAL_ENTRY);
   ulong deal_magic=(ulong)HistoryDealGetInteger(trans.deal,DEAL_MAGIC);
   ulong position_id=(ulong)HistoryDealGetInteger(trans.deal,DEAL_POSITION_ID);
   ulong order=(ulong)HistoryDealGetInteger(trans.deal,DEAL_ORDER);
   string position_key=PositionStrategyKey(position_id);

   // A reversal deal (INOUT) both closes the old exposure and opens a new
   // exposure in the opposite direction. Attribute its realized P/L to the
   // old strategy, then replace the position mapping with the new strategy.
   if(entry==DEAL_ENTRY_IN)
   {
      if(deal_magic!=ALZOOL_MAGIC) return;
      int entry_strategy=ResolveEntryStrategy(trans.deal,order);
      if(entry_strategy>=1 && entry_strategy<=200)
         GlobalVariableSet(position_key,(double)entry_strategy);
      else
         Print("AL-ZOOL: entry deal has no valid strategy mapping. deal=",trans.deal);
      string side=(HistoryDealGetInteger(trans.deal,DEAL_TYPE)==DEAL_TYPE_BUY ? "BUY" : "SELL");
      string entry_comment=HistoryDealGetString(trans.deal,DEAL_COMMENT);
      string entry_name=(entry_strategy>=1 && entry_strategy<=200
                         ? g_last_strategy_name
                         : (StringFind(entry_comment,"AZC_CHASE")>=0 ? "CANDLE CHASE" : "MANAGEMENT ORDER"));
      string entry_decision=(entry_strategy>=1 && entry_strategy<=200 ? g_last_decision_id : "");
      double fill_price=HistoryDealGetDouble(trans.deal,DEAL_PRICE);
      double reference_price=(entry_strategy>=1 && entry_strategy<=200
                              ? (side=="BUY" ? g_last_market_ask : g_last_market_bid) : 0.0);
      double slippage_points=(reference_price>0.0 ? ((side=="BUY" ? fill_price-reference_price : reference_price-fill_price)/_Point) : 0.0);
      ulong decision_to_fill_ms=(entry_strategy>=1 && entry_strategy<=200 && g_decision_received_ms>0
                                 ? GetTickCount64()-g_decision_received_ms : 0);
      string opened_payload="{\"deal_id\":\""+(string)trans.deal+"\",\"ticket\":\""+(string)trans.deal+
         "\",\"position_id\":\""+(string)position_id+"\",\"order\":\""+(string)order+
         "\",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"side\":\""+side+
         "\",\"volume\":"+DoubleToString(HistoryDealGetDouble(trans.deal,DEAL_VOLUME),2)+
         ",\"price\":"+DoubleToString(fill_price,_Digits)+
         ",\"reference_price\":"+DoubleToString(reference_price,_Digits)+
         ",\"slippage_points\":"+DoubleToString(slippage_points,1)+
         ",\"decision_to_fill_ms\":"+IntegerToString((long)decision_to_fill_ms)+
         ",\"market_regime\":\""+JsonSafe(g_context_regime)+"\""+
         ",\"decision_id\":\""+JsonSafe(entry_decision)+"\""+
         ",\"strategy_id\":"+IntegerToString(entry_strategy)+
         ",\"strategy_name\":\""+JsonSafe(entry_name)+
         "\",\"confidence\":"+DoubleToString(g_last_confidence,4)+
         ",\"reason_ar\":\"تأكيد فتح الصفقة من سجل تنفيذ الوسيط MT5\"}";
      SendSignalEvent("mt5-deal-"+(string)trans.deal+"-opened","TRADE_OPENED",opened_payload);
      return;
   }

   if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) return;
   if(deal_magic==ALZOOL_MAGIC)
   {
      double closed_net=HistoryDealGetDouble(trans.deal,DEAL_PROFIT)+HistoryDealGetDouble(trans.deal,DEAL_COMMISSION)+
                        HistoryDealGetDouble(trans.deal,DEAL_SWAP)+HistoryDealGetDouble(trans.deal,DEAL_FEE);
      string learning_id=(entry==DEAL_ENTRY_INOUT ? (string)position_id+"_REV_"+(string)trans.deal : (string)position_id);
      string close_payload="{\"deal_id\":\""+(string)trans.deal+"\",\"learning_id\":\""+JsonSafe(learning_id)+
         "\",\"position_id\":\""+(string)position_id+
         "\",\"order\":\""+(string)order+"\",\"symbol\":\""+JsonSafe(_Symbol)+
         "\",\"magic\":\""+(string)deal_magic+"\",\"entry\":"+IntegerToString((int)entry)+
         ",\"volume\":"+DoubleToString(HistoryDealGetDouble(trans.deal,DEAL_VOLUME),2)+
         ",\"price\":"+DoubleToString(HistoryDealGetDouble(trans.deal,DEAL_PRICE),_Digits)+
         ",\"net_profit\":"+DoubleToString(closed_net,2)+
         ",\"reason_ar\":\"تأكيد إغلاق الصفقة من سجل تنفيذ الوسيط MT5\"}";
      SendSignalEvent("mt5-deal-"+(string)trans.deal+"-closed","TRADE_CLOSED",close_payload);
   }

   int closing_strategy=0;
   if(GlobalVariableCheck(position_key))
      closing_strategy=(int)GlobalVariableGet(position_key);

   // Recover strategy attribution from the broker's opening deal history if
   // terminal Global Variables were lost during a reinstall or restart.
   if((closing_strategy<1 || closing_strategy>200) && deal_magic==ALZOOL_MAGIC)
   {
      closing_strategy=StrategyForPositionFromHistory(position_id);
      if(closing_strategy>=1 && closing_strategy<=200)
      {
         GlobalVariableSet(position_key,(double)closing_strategy);
         Print("AL-ZOOL LEARNING recovered strategy mapping from history. position=",position_id,
               " strategy=",closing_strategy);
      }
   }

   // Fallback for brokers that report the close before the entry mapping.
   if((closing_strategy<1 || closing_strategy>200) && entry!=DEAL_ENTRY_INOUT)
   {
      // For a normal close, an outstanding order mapping can recover a
      // delayed entry event. On INOUT it belongs to the NEW reversed entry,
      // so it must never be used to label the old position's closing P/L.
      if(deal_magic==ALZOOL_MAGIC)
      {
         string order_key=OrderStrategyKey(order);
         if(GlobalVariableCheck(order_key))
            closing_strategy=(int)GlobalVariableGet(order_key);
      }
   }

   if(deal_magic==ALZOOL_MAGIC && closing_strategy>=1 && closing_strategy<=200)
   {
      // Aggregate partial exits and train once when the broker position is fully closed.
      // INOUT closes the old strategy exposure and opens a new one on the same identifier.
      double deal_pnl=HistoryDealGetDouble(trans.deal,DEAL_PROFIT)+
                      HistoryDealGetDouble(trans.deal,DEAL_COMMISSION)+
                      HistoryDealGetDouble(trans.deal,DEAL_SWAP)+
                      HistoryDealGetDouble(trans.deal,DEAL_FEE);
      string aggregate_base="AZP_"+(string)ALZOOL_MAGIC+"_"+(string)position_id;
      string pnl_key=aggregate_base+"_P";
      double accumulated=GlobalVariableCheck(pnl_key) ? GlobalVariableGet(pnl_key) : 0.0;
      accumulated+=deal_pnl;
      GlobalVariableSet(pnl_key,accumulated);
      if(entry==DEAL_ENTRY_INOUT)
      {
         QueueClosedDealOutcome((string)position_id+"_REV_"+(string)trans.deal,closing_strategy,accumulated);
         GlobalVariableDel(pnl_key);
      }
      else if(!PositionIdentifierIsOpen(position_id))
      {
         QueueClosedDealOutcome((string)position_id,closing_strategy,accumulated);
         GlobalVariableDel(pnl_key);
      }
   }
   else if(deal_magic==ALZOOL_MAGIC && entry!=DEAL_ENTRY_INOUT)
      Print("AL-ZOOL: closed deal has no valid strategy mapping; management orders are excluded from strategy learning. deal=",trans.deal);

   if(entry==DEAL_ENTRY_INOUT)
   {
      // The same deal opened the reversed position. Keep its new strategy
      // mapping under the still-active position identifier.
      if(deal_magic==ALZOOL_MAGIC)
      {
         int reversed_strategy=ResolveEntryStrategy(trans.deal,order);
         if(reversed_strategy>=1 && reversed_strategy<=200)
            GlobalVariableSet(position_key,(double)reversed_strategy);
         else if(!PositionIdentifierIsOpen(position_id))
            GlobalVariableDel(position_key);
         else
            Print("AL-ZOOL: reversal opened without a valid new strategy mapping. deal=",trans.deal);
      }
      return;
   }

   if(!PositionIdentifierIsOpen(position_id))
      GlobalVariableDel(position_key);
}

// This runs locally on the terminal timer even while the central server is offline.
// The broker-side SL/TP placed at entry remain active if the terminal later closes.
string JsonSafe(string value)
{
   StringReplace(value,"\\","\\\\"); StringReplace(value,"\"","\\\"");
   StringReplace(value,"\r"," "); StringReplace(value,"\n"," ");
   return value;
}

void SendLiveState()
{
   static ulong last_send_ms=0;
   static ulong sequence=0;
   static long last_tick_time_msc=0;
   ulong now_ms=GetTickCount64();
   if(InpEngineId=="" || InpEngineToken=="") return;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return;
   // Push immediately after a changed MT5 tick (within the 250 ms timer);
   // keep a 1-second heartbeat when the market is quiet.
   if(last_send_ms>0 && tick.time_msc==last_tick_time_msc && now_ms-last_send_ms<1000) return;
   last_send_ms=now_ms;
   last_tick_time_msc=tick.time_msc;
   sequence++;
   MqlRates current[];
   ArraySetAsSeries(current,true);
   bool has_candle=(CopyRates(_Symbol,PERIOD_CURRENT,0,1,current)==1);

   string body="{\"sequence\":"+IntegerToString((long)sequence)+
      ",\"source_time\":"+IntegerToString((long)tick.time)+
      ",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",\"point\":"+DoubleToString(_Point,10)+",\"volume_step\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),8)+",\"volume_min\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),8)+",\"volume_max\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),8)+",\"tick_value\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE),8)+",\"tick_size\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE),10)+",\"stops_level\":"+IntegerToString((int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL))+",";
   body+="\"account\":{\"login\":\""+(string)AccountInfoInteger(ACCOUNT_LOGIN)+
      "\",\"server\":\""+JsonSafe(AccountInfoString(ACCOUNT_SERVER))+
      "\",\"currency\":\""+JsonSafe(AccountInfoString(ACCOUNT_CURRENCY))+
      "\",\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+
      ",\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+
      ",\"margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2)+
      ",\"free_margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+
      ",\"profit\":"+DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),2)+"},\"positions\":[";
   int n=0;
   for(int i=PositionsTotal()-1;i>=0 && n<200;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(n++>0) body+=",";
      body+="{\"ticket\":\""+(string)ticket+"\",\"symbol\":\""+JsonSafe(PositionGetString(POSITION_SYMBOL))+
         "\",\"type\":"+IntegerToString((int)PositionGetInteger(POSITION_TYPE))+
         ",\"volume\":"+DoubleToString(PositionGetDouble(POSITION_VOLUME),2)+
         ",\"open_price\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),_Digits)+
         ",\"price_current\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT),_Digits)+
         ",\"sl\":"+DoubleToString(PositionGetDouble(POSITION_SL),_Digits)+
         ",\"tp\":"+DoubleToString(PositionGetDouble(POSITION_TP),_Digits)+
         ",\"profit\":"+DoubleToString(PositionGetDouble(POSITION_PROFIT),2)+
         ",\"swap\":"+DoubleToString(PositionGetDouble(POSITION_SWAP),2)+
         ",\"magic\":\""+(string)PositionGetInteger(POSITION_MAGIC)+
          "\",\"comment\":\""+JsonSafe(PositionGetString(POSITION_COMMENT))+
          "\",\"time\":"+IntegerToString((long)PositionGetInteger(POSITION_TIME))+"}";
   }
   body+="],\"orders\":[";
   n=0;
   for(int j=OrdersTotal()-1;j>=0 && n<200;j--)
   {
      ulong ticket=OrderGetTicket(j);
      if(ticket==0) continue;
      if(n++>0) body+=",";
      body+="{\"ticket\":\""+(string)ticket+"\",\"symbol\":\""+JsonSafe(OrderGetString(ORDER_SYMBOL))+
         "\",\"type\":"+IntegerToString((int)OrderGetInteger(ORDER_TYPE))+
         ",\"state\":"+IntegerToString((int)OrderGetInteger(ORDER_STATE))+
         ",\"volume\":"+DoubleToString(OrderGetDouble(ORDER_VOLUME_CURRENT),2)+
         ",\"price\":"+DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN),_Digits)+
         ",\"sl\":"+DoubleToString(OrderGetDouble(ORDER_SL),_Digits)+
         ",\"tp\":"+DoubleToString(OrderGetDouble(ORDER_TP),_Digits)+",\"magic\":\""+(string)OrderGetInteger(ORDER_MAGIC)+"\",\"comment\":\""+JsonSafe(OrderGetString(ORDER_COMMENT))+"\",\"time_setup\":"+IntegerToString((long)OrderGetInteger(ORDER_TIME_SETUP))+"}";
   }
   body+="],\"tick\":{\"bid\":"+DoubleToString(tick.bid,_Digits)+
      ",\"ask\":"+DoubleToString(tick.ask,_Digits)+
      ",\"last\":"+DoubleToString(tick.last,_Digits)+
      ",\"spread_points\":"+DoubleToString((tick.ask-tick.bid)/_Point,1)+
      ",\"time\":"+IntegerToString((long)tick.time)+
      ",\"time_msc\":"+IntegerToString((long)tick.time_msc)+"},";
   if(has_candle)
      body+="\"forming_candle\":{\"time\":"+IntegerToString((long)current[0].time)+
         ",\"open\":"+DoubleToString(current[0].open,_Digits)+
         ",\"high\":"+DoubleToString(current[0].high,_Digits)+
         ",\"low\":"+DoubleToString(current[0].low,_Digits)+
         ",\"close\":"+DoubleToString(current[0].close,_Digits)+
         ",\"tick_volume\":"+IntegerToString((long)current[0].tick_volume)+"}";
   else body+="\"forming_candle\":{}";
   body+="}";

   char data[],result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ulong started=GetTickCount64();
   int http=WebRequest("POST",ServerBaseUrl()+"/v1/terminal-state/live/"+InpEngineId,
                       headers,MathMin(InpHttpTimeoutMs,1500),data,result,response_headers);
   ulong elapsed=GetTickCount64()-started;
   if(http!=200) Print("AL-ZOOL live telemetry failed. HTTP=",http," err=",GetLastError()," ms=",elapsed);
   else if(elapsed>700) Print("AL-ZOOL live telemetry slow. ms=",elapsed);
}

void SendTerminalState()
{
   ulong now_ms=GetTickCount64();
   // Account/history payloads are larger; refresh every 5 seconds, not every timer tick.
   if(g_last_terminal_state_ms>0 && now_ms-g_last_terminal_state_ms<5000) return;
   g_last_terminal_state_ms=now_ms;
   if(InpEngineId=="" || InpEngineToken=="") return;
   string body="{\"account\":{\"login\":\""+(string)AccountInfoInteger(ACCOUNT_LOGIN)+"\",\"server\":\""+JsonSafe(AccountInfoString(ACCOUNT_SERVER))+"\",\"currency\":\""+JsonSafe(AccountInfoString(ACCOUNT_CURRENCY))+"\",\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+",\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+",\"margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2)+",\"free_margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+",\"profit\":"+DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),2)+"},\"positions\":[";
   int n=0;
   for(int i=PositionsTotal()-1;i>=0 && n<200;i--){
      ulong t=PositionGetTicket(i); if(t==0 || !PositionSelectByTicket(t)) continue;
      if(n++>0) body+=",";
      body+="{\"ticket\":\""+(string)t+"\",\"symbol\":\""+JsonSafe(PositionGetString(POSITION_SYMBOL))+"\",\"type\":"+IntegerToString((int)PositionGetInteger(POSITION_TYPE))+",\"volume\":"+DoubleToString(PositionGetDouble(POSITION_VOLUME),2)+",\"open_price\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),_Digits)+",\"price_current\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT),_Digits)+",\"sl\":"+DoubleToString(PositionGetDouble(POSITION_SL),_Digits)+",\"tp\":"+DoubleToString(PositionGetDouble(POSITION_TP),_Digits)+",\"profit\":"+DoubleToString(PositionGetDouble(POSITION_PROFIT),2)+",\"swap\":"+DoubleToString(PositionGetDouble(POSITION_SWAP),2)+",\"magic\":\""+(string)PositionGetInteger(POSITION_MAGIC)+"\",\"time\":"+IntegerToString((long)PositionGetInteger(POSITION_TIME))+"}";
          "\",\"comment\":\""+JsonSafe(PositionGetString(POSITION_COMMENT))+"\",
          "\"time\":"+IntegerToString((long)PositionGetInteger(POSITION_TIME))+"}";
   for(int j=OrdersTotal()-1;j>=0 && n<200;j--){
      ulong t=OrderGetTicket(j); if(t==0) continue;
      if(n++>0) body+=",";
      body+="{\"ticket\":\""+(string)t+"\",\"symbol\":\""+JsonSafe(OrderGetString(ORDER_SYMBOL))+"\",\"type\":"+IntegerToString((int)OrderGetInteger(ORDER_TYPE))+",\"state\":"+IntegerToString((int)OrderGetInteger(ORDER_STATE))+",\"volume\":"+DoubleToString(OrderGetDouble(ORDER_VOLUME_CURRENT),2)+",\"price\":"+DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN),_Digits)+",\"sl\":"+DoubleToString(OrderGetDouble(ORDER_SL),_Digits)+",\"tp\":"+DoubleToString(OrderGetDouble(ORDER_TP),_Digits)+",\"magic\":\""+(string)OrderGetInteger(ORDER_MAGIC)+"\",\"time_setup\":"+IntegerToString((long)OrderGetInteger(ORDER_TIME_SETUP))+"}";
   }
   body+="],\"history\":[";
   datetime from_time=TimeCurrent()-86400*30; HistorySelect(from_time,TimeCurrent());
   int deals=HistoryDealsTotal(); n=0;
   for(int k=deals-1;k>=0 && n<200;k--){
      ulong d=HistoryDealGetTicket(k); if(d==0) continue;
      if((ulong)HistoryDealGetInteger(d,DEAL_MAGIC)!=ALZOOL_MAGIC) continue;
      if(HistoryDealGetString(d,DEAL_SYMBOL)!=_Symbol) continue;
      if(n++>0) body+=",";
      double deal_profit=HistoryDealGetDouble(d,DEAL_PROFIT);
      double deal_commission=HistoryDealGetDouble(d,DEAL_COMMISSION);
      double deal_swap=HistoryDealGetDouble(d,DEAL_SWAP);
      double deal_fee=HistoryDealGetDouble(d,DEAL_FEE);
      body+="{\"ticket\":\""+(string)d+"\",\"position_id\":\""+(string)HistoryDealGetInteger(d,DEAL_POSITION_ID)+"\",\"order\":\""+(string)HistoryDealGetInteger(d,DEAL_ORDER)+"\",\"symbol\":\""+JsonSafe(HistoryDealGetString(d,DEAL_SYMBOL))+"\",\"type\":"+IntegerToString((int)HistoryDealGetInteger(d,DEAL_TYPE))+",\"entry\":"+IntegerToString((int)HistoryDealGetInteger(d,DEAL_ENTRY))+",\"reason\":"+IntegerToString((int)HistoryDealGetInteger(d,DEAL_REASON))+",\"volume\":"+DoubleToString(HistoryDealGetDouble(d,DEAL_VOLUME),2)+",\"price\":"+DoubleToString(HistoryDealGetDouble(d,DEAL_PRICE),_Digits)+",\"profit\":"+DoubleToString(deal_profit,2)+",\"commission\":"+DoubleToString(deal_commission,2)+",\"swap\":"+DoubleToString(deal_swap,2)+",\"fee\":"+DoubleToString(deal_fee,2)+",\"net_profit\":"+DoubleToString(deal_profit+deal_commission+deal_swap+deal_fee,2)+",\"magic\":\""+(string)ALZOOL_MAGIC+"\",\"time\":"+IntegerToString((long)HistoryDealGetInteger(d,DEAL_TIME))+"}";
   }
   body+="],\"settings\":{\"chart_symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",\"magic\":\""+(string)ALZOOL_MAGIC+"\",\"server_url\":\""+JsonSafe(ServerBaseUrl())+"\",\"engine_id\":\""+JsonSafe(InpEngineId)+"\",\"engine_status\":\""+JsonSafe(g_status)+"\",\"last_decision\":\""+JsonSafe(g_last_decision)+"\",\"last_strategy\":\""+JsonSafe(g_last_strategy_name)+"\",\"last_strategy_id\":"+IntegerToString(g_last_strategy_id)+",\"confidence\":"+DoubleToString(g_last_confidence,4)+",\"server_online\":"+(g_server_online?"true":"false")+",\"request_seconds\":"+IntegerToString(ALZOOL_REQUEST_SECONDS)+",\"bars_to_send\":"+IntegerToString(ALZOOL_BARS_TO_SEND)+"},\"events\":[]";
   char data[],result[]; StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8); if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n",response_headers;
   int http=WebRequest("POST",ServerBaseUrl()+"/v1/terminal-state/"+InpEngineId,headers,MathMin(InpHttpTimeoutMs,1800),data,result,response_headers);
   if(http!=200) Print("AL-ZOOL telemetry upload failed. HTTP=",http," err=",GetLastError());
}

void PollCentralEngine()
{
   ulong now_ms=GetTickCount64();
   ulong interval_ms=(ulong)ALZOOL_REQUEST_SECONDS*1000;
   if(g_last_market_poll_ms>0 && now_ms-g_last_market_poll_ms<interval_ms) return;
   g_last_market_poll_ms=now_ms;
   if(!CheckCentralHealth()) return;
   string decision,decision_id,reason;
   double confidence=0.0;
   int strategy_id=0;
   if(!RequestCentralDecision(decision,decision_id,reason,confidence,strategy_id)) return;
   // Market signal is telemetry/cache only. Signed management commands execute trades.
   g_last_decision=decision;
   g_last_reason=reason;
   g_last_confidence=confidence;
   g_last_strategy_id=strategy_id;
   g_status="Central decision received: "+decision+" / awaiting execution command";
}

int OnInit()
{
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   trade.SetTypeFillingBySymbol(_Symbol);
   g_global_bar_key="AZ_CENTRAL_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   if(GlobalVariableCheck(g_global_bar_key))
      g_last_bar_time=(datetime)GlobalVariableGet(g_global_bar_key);

   // High-resolution telemetry timer; maintenance tasks remain throttled below.
   EventSetMillisecondTimer(250);
   g_status="Starting central server connection check";
   Print("AL-ZOOL Central Client initialized on ",_Symbol,
         ". Checking server connection...");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

bool VerifyManagementCommand(const string response)
{
   string signed_payload=JsonString(response,"signed_payload");
   string supplied=JsonString(response,"signature");
   string client_id=JsonString(response,"client_id");
   string command_id=JsonString(response,"command_id");
   string action=JsonString(response,"action");
   string ticket=JsonString(response,"ticket");
   string direction=JsonString(response,"direction");
   string comment=JsonString(response,"comment");
   double sl=JsonNumber(response,"sl",0.0);
   double tp=JsonNumber(response,"tp",0.0);
   double lots=JsonNumber(response,"lots",0.0);
   double price=JsonNumber(response,"price",0.0);
   double expires=JsonNumber(response,"expires_at",0.0);
   if(signed_payload=="" || supplied=="" || client_id!=InpEngineId ||
      command_id=="" || expires<=0.0 || (double)TimeGMT()>=expires) return false;
   if(action!="WAIT" && action!="CLOSE_ALL" && action!="CLOSE_POSITION" &&
      action!="MODIFY_SL" && action!="OPEN_MARKET" && action!="PLACE_PENDING" && action!="CANCEL_PENDING") return false;
   if((action=="OPEN_MARKET" || action=="PLACE_PENDING") && ((direction!="BUY" && direction!="SELL") || lots<=0.0 || comment=="")) return false;
   if(action=="PLACE_PENDING" && price<=0.0) return false;
   string expected_payload=client_id+"|"+command_id+"|"+action+"|"+ticket+"|"+
      DoubleToString(sl,8)+"|"+DoubleToString(tp,8)+"|"+direction+"|"+
      DoubleToString(lots,8)+"|"+DoubleToString(price,8)+"|"+comment+"|"+DoubleToString(expires,6);
   if(expected_payload!=signed_payload) return false;
   string expected="";
   if(!HMAC_SHA256_HEX(signed_payload,InpEngineToken,expected)) return false;
   StringToLower(expected); StringToLower(supplied);
   return expected==supplied;
}
void PollCentralManagement()
{
   if(InpEngineId=="" || InpEngineToken=="" || !g_server_online) return;
   char data[],result[];
   ArrayResize(data,0);
   string headers="Accept: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int http=WebRequest("GET",ServerBaseUrl()+"/v1/management/"+InpEngineId,
                       headers,MathMin(InpHttpTimeoutMs,1800),data,result,response_headers);
   if(http!=200)
   {
      if(http==-1) Print("AL-ZOOL central management request failed. err=",GetLastError());
      return;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(!VerifyManagementCommand(response))
   {
      Print("AL-ZOOL rejected unsigned, expired, or malformed central management command");
      return;
   }
   string action=JsonString(response,"action");
   string command_id=JsonString(response,"command_id");
   string ticket_text=JsonString(response,"ticket");
   string direction=JsonString(response,"direction");
   string comment=JsonString(response,"comment");
   double lots=JsonNumber(response,"lots",0.0);
   double sl=JsonNumber(response,"sl",0.0);
   double tp=JsonNumber(response,"tp",0.0);
   double price=JsonNumber(response,"price",0.0);
   string dedupe_key="AZ_CMD_"+command_id;
   if(action=="WAIT" || GlobalVariableCheck(dedupe_key)) return;

   bool executed=false;
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   if(action=="CLOSE_ALL")
   {
      bool all_ok=true;
      int matched=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong position_ticket=PositionGetTicket(i);
         if(position_ticket==0 || !PositionSelectByTicket(position_ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
            (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
         matched++;
         if(!trade.PositionClose(position_ticket))
         {
            all_ok=false;
            Print("AL-ZOOL central CLOSE_ALL failed ticket=",position_ticket,
                  " retcode=",trade.ResultRetcode()," ",trade.ResultRetcodeDescription());
         }
      }
      executed=(matched>0 && all_ok);
   }
    else if(action=="OPEN_MARKET")
    {
       double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
       double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
       double vstep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
       if(lots>=vmin-1e-8 && lots<=vmax+1e-8 && vstep>0.0 &&
          MathAbs(lots/vstep-MathRound(lots/vstep))<0.0001)
       {
          trade.SetDeviationInPoints(20);
          trade.SetTypeFillingBySymbol(_Symbol);
          bool sent=false;
          if(direction=="BUY") sent=trade.Buy(lots,_Symbol,0.0,sl,tp,comment);
          else if(direction=="SELL") sent=trade.Sell(lots,_Symbol,0.0,sl,tp,comment);
          uint rc=trade.ResultRetcode();
          executed=sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED ||
                            rc==TRADE_RETCODE_DONE_PARTIAL);
          if(executed)
          {
             string event_payload="{\"command_id\":\""+JsonSafe(command_id)+
                "\",\"side\":\""+direction+"\",\"symbol\":\""+JsonSafe(_Symbol)+
                "\",\"volume\":"+DoubleToString(lots,4)+
                ",\"reason_ar\":\""+JsonSafe(JsonString(response,"reason"))+"\"}";
             SendSignalEvent(command_id+"_central-order","ORDER_SENT",event_payload);
          }
       }
    }
    else if(action=="CANCEL_PENDING")
    {
       ulong order_ticket=(ulong)StringToInteger(ticket_text);
       if(order_ticket>0 && OrderSelect(order_ticket) &&
          OrderGetString(ORDER_SYMBOL)==_Symbol &&
          (ulong)OrderGetInteger(ORDER_MAGIC)==ALZOOL_MAGIC)
          executed=trade.OrderDelete(order_ticket);
    }

    else if(action=="PLACE_PENDING")
    {
       double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
       double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
       double vstep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
       MqlTick current_tick;
       if(lots>=vmin-1e-8 && lots<=vmax+1e-8 && vstep>0.0 &&
          MathAbs(lots/vstep-MathRound(lots/vstep))<0.0001 &&
          SymbolInfoTick(_Symbol,current_tick))
       {
          trade.SetTypeFillingBySymbol(_Symbol);
          if(direction=="BUY" && price>current_tick.ask)
             executed=trade.BuyStop(lots,NormalizeDouble(price,_Digits),_Symbol,sl,tp,ORDER_TIME_GTC,0,comment);
          else if(direction=="SELL" && price<current_tick.bid)
             executed=trade.SellStop(lots,NormalizeDouble(price,_Digits),_Symbol,sl,tp,ORDER_TIME_GTC,0,comment);
       }
    }
    else
    {
      ulong position_ticket=(ulong)StringToInteger(ticket_text);
      if(position_ticket>0 && PositionSelectByTicket(position_ticket) &&
         PositionGetString(POSITION_SYMBOL)==_Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC)==ALZOOL_MAGIC)
      {
         if(action=="CLOSE_POSITION")
            executed=trade.PositionClose(position_ticket);
         else if(action=="MODIFY_SL" && sl>0.0)
         {
            ENUM_POSITION_TYPE side=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
            double old_sl=PositionGetDouble(POSITION_SL);
            double existing_tp=PositionGetDouble(POSITION_TP);
            bool improves=(side==POSITION_TYPE_BUY ? sl>old_sl+_Point :
                           (old_sl<=0.0 || sl<old_sl-_Point));
            if(improves && ((side==POSITION_TYPE_BUY && sl<SymbolInfoDouble(_Symbol,SYMBOL_BID)) ||
                            (side==POSITION_TYPE_SELL && sl>SymbolInfoDouble(_Symbol,SYMBOL_ASK))))
               executed=trade.PositionModify(position_ticket,NormalizeDouble(sl,_Digits),existing_tp);
         }
      }
   }
   if(executed)
   {
      GlobalVariableSet(dedupe_key,(double)TimeCurrent());
      Print("AL-ZOOL central management executed: ",action," command=",command_id);
   }
   else
      Print("AL-ZOOL central management not completed: ",action,
            " retcode=",trade.ResultRetcode()," ",trade.ResultRetcodeDescription());
}

void OnTimer()
{
   // Live MT5 snapshot is sent first, independently of slower maintenance work.
   SendLiveState();

   // Keep account/history, settings, control and strategy polling at 1-second cadence.
   static ulong last_maintenance_ms=0;
   ulong now_ms=GetTickCount64();
   if(last_maintenance_ms>0 && now_ms-last_maintenance_ms<1000) return;
   last_maintenance_ms=now_ms;

   FlushPendingOutcomes();
   SendTerminalState();
   CheckCentralHealth();
   PollCentralManagement();
   PollCentralEngine();
   Comment("AL-ZOOL CENTRAL CLIENT\n",
           "Status: ",g_status,"\n",
           "Server: ",(g_server_online ? "ONLINE" : "OFFLINE / NOT CONFIRMED"),"\n",
           "Control: dashboard / central server only","\n",
           "Symbol: ",_Symbol," | TF: ",EnumToString((ENUM_TIMEFRAMES)_Period),"\n",
           "Magic: ",(string)ALZOOL_MAGIC,"\n",
           "AI Decision: ",g_last_decision," | Confidence: ",DoubleToString(g_last_confidence*100.0,1),"%\n",
           "Selected Strategy: ",g_last_strategy_name," (ID ",IntegerToString(g_last_strategy_id),")\n",
           "Reason: ",g_last_reason);
}

void OnTick()
{
   // No trading decision is made from the tick handler.
   // Local protection is also serviced by OnTimer.
}
