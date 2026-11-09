//+------------------------------------------------------------------+
//| AL-ZOOL Central MT5 Client                                       |
//| Local MT5 execution + broker-side SL/TP + local trailing guard.  |
//| Strategy logic remains on the owner's central server.            |
//+------------------------------------------------------------------+
#property strict
#property version   "1.02"
#property description "AL-ZOOL central decision client for MetaTrader 5"

#include <Trade/Trade.mqh>

input group "Central server"
input string InpServerUrl = "https://al-zool-gold-sniper.onrender.com";
input string InpEngineId = "";
input string InpEngineToken = "";
input string InpServerSymbol = "XAUUSD";
input int    InpHttpTimeoutMs = 5000;
input int    InpRequestEverySeconds = 5;
input int    InpBarsToSend = 120;

input group "Trading safety"
input double InpLots = 0.01;
input ulong  InpMagic = 26110801;
input int    InpMaxPositions = 1;
input int    InpMaxSpreadPoints = 80;
input bool   InpAllowBuy = true;
input bool   InpAllowSell = true;
input bool   InpBlockOppositePositions = true;
input bool   InpRequireBrokerStops = true;

input group "Local protection (continues while MT5 terminal runs)"
input int    InpATRPeriod = 14;
input double InpStopLossATR = 1.8;
input double InpTakeProfitATR = 1.2;
input bool   InpUseTrailing = true;
input double InpTrailStartATR = 1.0;
input double InpTrailDistanceATR = 0.8;

CTrade trade;
datetime g_last_bar_time = 0;
ulong g_last_health_check_ms = 0;
ulong g_last_market_poll_ms = 0;
string g_last_decision_id = "";
int g_atr_handle = INVALID_HANDLE;
bool g_server_online = false;
string g_status = "Starting";
string g_last_reason = "";
string g_last_strategy_name = "";
double g_last_confidence = 0.0;
int g_last_strategy_id = 0;
string g_last_decision = "WAIT";
string g_global_bar_key = "";

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

double JsonNumber(const string source,const string key,double fallback)
{
   string needle="\"" + key + "\":";
   int p=StringFind(source,needle);
   if(p<0) return fallback;
   p+=StringLen(needle);
   while(p<StringLen(source) && StringGetCharacter(source,p)==32) p++;
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

bool GetATR(double &atr)
{
   atr=0.0;
   if(g_atr_handle==INVALID_HANDLE) return false;
   double values[];
   ArraySetAsSeries(values,true);
   if(CopyBuffer(g_atr_handle,0,1,1,values)!=1) return false;
   atr=values[0];
   return (atr>0.0 && MathIsValidNumber(atr));
}

double NormalizeLots(double lots)
{
   double minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0.0) return 0.0;
   lots=MathMax(minv,MathMin(maxv,lots));
   lots=MathFloor(lots/step+1e-8)*step;
   int digits=2;
   if(step<0.01) digits=3;
   if(step<0.001) digits=4;
   return NormalizeDouble(lots,digits);
}

int CountOurPositions(int &buy_count,int &sell_count)
{
   buy_count=0;
   sell_count=0;
   int total=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(type==POSITION_TYPE_BUY) buy_count++;
      if(type==POSITION_TYPE_SELL) sell_count++;
      total++;
   }
   return total;
}

bool HasOppositePosition(const string direction)
{
   int buys=0,sells=0;
   CountOurPositions(buys,sells);
   if(direction=="BUY") return sells>0;
   if(direction=="SELL") return buys>0;
   return false;
}

bool BuildMarketPayload(string &body,datetime &bar_time)
{
   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied=CopyRates(_Symbol,PERIOD_CURRENT,1,InpBarsToSend,rates);
   if(copied<65) return false;
   bar_time=rates[copied-1].time;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return false;

   body="{\"symbol\":\""+InpServerSymbol+"\",";
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
   ulong interval_ms=(ulong)InpRequestEverySeconds*1000;
   if(g_last_health_check_ms>0 && now_ms-g_last_health_check_ms<interval_ms)
      return g_server_online;
   g_last_health_check_ms=now_ms;

   if(StringLen(InpServerUrl)<10 || StringFind(InpServerUrl,"YOUR-SERVER")>=0)
   {
      g_server_online=false;
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
      g_status=(http==-1 ? "Health check failed / WebRequest error "+IntegerToString(GetLastError())
                         : "Health check HTTP "+IntegerToString(http));
      return false;
   }

   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(StringFind(response,"\"status\":\"online\"")<0)
   {
      g_server_online=false;
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
   double spread=(tick.ask-tick.bid)/_Point;
   if(InpMaxSpreadPoints>0 && spread>InpMaxSpreadPoints)
   {
      g_status="Spread protection";
      g_last_bar_time=bar_time;
      return false;
   }

   string url=ServerBaseUrl()+"/v1/market/"+InpEngineId;
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   char data[];
   char result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string response_headers;
   ResetLastError();
   int http=WebRequest("POST",url,headers,InpHttpTimeoutMs,data,result,response_headers);
   if(http==-1)
   {
      g_server_online=false;
      g_status="Server offline / WebRequest error "+IntegerToString(GetLastError());
      return false; // fail closed: no new entries
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   if(http!=200)
   {
      g_server_online=true;
      g_status="Server reachable / Market HTTP "+IntegerToString(http);
      g_last_reason=response;
      return false;
   }

   if(!VerifyDecisionSignature(response))
   {
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
   g_last_strategy_id=strategy_id;
   g_last_decision=decision;
   if(decision!="BUY" && decision!="SELL" && decision!="WAIT")
   {
      g_server_online=false;
      g_status="Invalid server response";
      return false;
   }
   if(decision_id=="")
   {
      g_server_online=false;
      g_status="Missing decision id";
      return false;
   }
   g_server_online=true;
   g_last_bar_time=bar_time;
   // Persist as soon as a fresh server decision is received. This favors
   // duplicate prevention over retrying a rejected order on the same candle.
   GlobalVariableSet(g_global_bar_key,(double)g_last_bar_time);
   g_status="Server online / "+decision;
   g_last_reason=reason;
   return true;
}

bool PlaceCentralTrade(const string direction,const string decision_id,
                       const string reason,const int strategy_id)
{
   if(direction=="BUY" && !InpAllowBuy) { g_status="BUY disabled"; return false; }
   if(direction=="SELL" && !InpAllowSell) { g_status="SELL disabled"; return false; }
   if(direction!="BUY" && direction!="SELL") return false;
   if(strategy_id<1 || strategy_id>200) { g_status="Invalid strategy id"; return false; }
   if(decision_id=="" || decision_id==g_last_decision_id) return false;
   MqlTick fresh_tick;
   if(!SymbolInfoTick(_Symbol,fresh_tick)) return false;
   double current_spread=(fresh_tick.ask-fresh_tick.bid)/_Point;
   if(InpMaxSpreadPoints>0 && current_spread>InpMaxSpreadPoints)
   {
      g_status="Spread widened after server decision";
      return false;
   }
   if(InpBlockOppositePositions && HasOppositePosition(direction))
   {
      g_status="Opposite position exists";
      return false;
   }

   int buys=0,sells=0;
   if(CountOurPositions(buys,sells)>=InpMaxPositions)
   {
      g_status="Maximum positions reached";
      return false;
   }

   double atr=0.0;
   if(!GetATR(atr)) { g_status="ATR unavailable"; return false; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return false;

   double lots=NormalizeLots(InpLots);
   if(lots<=0.0) { g_status="Invalid lot size"; return false; }

   double stop_distance=atr*InpStopLossATR;
   double target_distance=atr*InpTakeProfitATR;
   long stops_level=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double min_distance=(double)stops_level*_Point;
   if(InpRequireBrokerStops && (stop_distance<min_distance || target_distance<min_distance))
   {
      g_status="Broker stop distance too small";
      return false;
   }

   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double sl=0.0,tp=0.0,price=0.0;
   bool sent=false;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(direction=="BUY")
   {
      price=tick.ask;
      sl=NormalizeDouble(price-stop_distance,digits);
      tp=NormalizeDouble(price+target_distance,digits);
      sent=trade.Buy(lots,_Symbol,0.0,sl,tp,"AZS"+IntegerToString(strategy_id));
   }
   else
   {
      price=tick.bid;
      sl=NormalizeDouble(price+stop_distance,digits);
      tp=NormalizeDouble(price-target_distance,digits);
      sent=trade.Sell(lots,_Symbol,0.0,sl,tp,"AZS"+IntegerToString(strategy_id));
   }

   uint rc=trade.ResultRetcode();
   if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED || rc==TRADE_RETCODE_DONE_PARTIAL))
   {
      ulong order_ticket=trade.ResultOrder();
      ulong deal_ticket=trade.ResultDeal();
      if(order_ticket>0)
         GlobalVariableSet(OrderStrategyKey(order_ticket),(double)strategy_id);
      // If the broker reports an immediate deal, persist its position mapping too.
      if(deal_ticket>0 && HistoryDealSelect(deal_ticket))
      {
         ulong position_id=(ulong)HistoryDealGetInteger(deal_ticket,DEAL_POSITION_ID);
         if(position_id>0)
            GlobalVariableSet(PositionStrategyKey(position_id),(double)strategy_id);
      }
      g_last_decision_id=decision_id;
      GlobalVariableSet(g_global_bar_key,(double)g_last_bar_time);
      g_status=direction+" opened / strategy "+IntegerToString(strategy_id);
      g_last_reason=reason;
      return true;
   }
   g_status="Order rejected: "+trade.ResultRetcodeDescription();
   return false;
}

string OrderStrategyKey(const ulong order)
{
   return "AZR_"+(string)InpMagic+"_"+(string)order;
}

string PositionStrategyKey(const ulong position_id)
{
   return "AZS_"+(string)InpMagic+"_"+(string)position_id;
}

string PendingOutcomePrefix()
{
   return "AZO_"+(string)InpMagic+"_";
}

bool PositionIdentifierIsOpen(const ulong position_id)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==position_id) return true;
   }
   return false;
}

void QueueClosedDealOutcome(const ulong deal,const int strategy_id,const double pnl)
{
   if(deal==0 || strategy_id<1 || strategy_id>200 || !MathIsValidNumber(pnl)) return;
   string base=PendingOutcomePrefix()+(string)deal;
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
      ulong deal=(ulong)StringToInteger(suffix);
      string skey=name+"_S";
      string pkey=name+"_P";
      if(deal==0 || !GlobalVariableCheck(skey) || !GlobalVariableCheck(pkey)) continue;

      int strategy_id=(int)GlobalVariableGet(skey);
      double pnl=GlobalVariableGet(pkey);
      string body="{\"deal_id\":\""+(string)deal+"\",\"strategy_id\":"+IntegerToString(strategy_id)+
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
      }
      else
      {
         if(http==-1) g_status="Outcome saved locally; server offline";
         else g_status="Outcome pending / HTTP "+IntegerToString(http);
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
      if(deal_magic!=InpMagic) return;
      int entry_strategy=ResolveEntryStrategy(trans.deal,order);
      if(entry_strategy>=1 && entry_strategy<=200)
         GlobalVariableSet(position_key,(double)entry_strategy);
      else
         Print("AL-ZOOL: entry deal has no valid strategy mapping. deal=",trans.deal);
      return;
   }

   if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) return;

   int closing_strategy=0;
   if(GlobalVariableCheck(position_key))
      closing_strategy=(int)GlobalVariableGet(position_key);

   // Fallback for brokers that report the close before the entry mapping.
   if((closing_strategy<1 || closing_strategy>200) && entry!=DEAL_ENTRY_INOUT)
   {
      // For a normal close, an outstanding order mapping can recover a
      // delayed entry event. On INOUT it belongs to the NEW reversed entry,
      // so it must never be used to label the old position's closing P/L.
      if(deal_magic==InpMagic)
      {
         string order_key=OrderStrategyKey(order);
         if(GlobalVariableCheck(order_key))
            closing_strategy=(int)GlobalVariableGet(order_key);
      }
   }

   if(closing_strategy>=1 && closing_strategy<=200)
   {
      double pnl=HistoryDealGetDouble(trans.deal,DEAL_PROFIT)+
                 HistoryDealGetDouble(trans.deal,DEAL_COMMISSION)+
                 HistoryDealGetDouble(trans.deal,DEAL_SWAP)+
                 HistoryDealGetDouble(trans.deal,DEAL_FEE);
      QueueClosedDealOutcome(trans.deal,closing_strategy,pnl);
   }
   else
   {
      Print("AL-ZOOL: closed deal has no saved strategy mapping; outcome not submitted. deal=",trans.deal);
   }

   if(entry==DEAL_ENTRY_INOUT)
   {
      // The same deal opened the reversed position. Keep its new strategy
      // mapping under the still-active position identifier.
      if(deal_magic==InpMagic)
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
void ManageLocalProtection()
{
   double atr=0.0;
   if(!GetATR(atr)) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   long stops_level=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double min_distance=(double)stops_level*_Point;

   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;

      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open_price=PositionGetDouble(POSITION_PRICE_OPEN);
      double old_sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      double new_sl=old_sl;

      if(!InpUseTrailing) continue;
      if(type==POSITION_TYPE_BUY)
      {
         if(tick.bid-open_price<atr*InpTrailStartATR) continue;
         double candidate=NormalizeDouble(tick.bid-atr*InpTrailDistanceATR,digits);
         if(tick.bid-candidate<min_distance) candidate=NormalizeDouble(tick.bid-min_distance,digits);
         if(old_sl==0.0 || candidate>old_sl+_Point) new_sl=candidate;
      }
      else if(type==POSITION_TYPE_SELL)
      {
         if(open_price-tick.ask<atr*InpTrailStartATR) continue;
         double candidate=NormalizeDouble(tick.ask+atr*InpTrailDistanceATR,digits);
         if(candidate-tick.ask<min_distance) candidate=NormalizeDouble(tick.ask+min_distance,digits);
         if(old_sl==0.0 || candidate<old_sl-_Point) new_sl=candidate;
      }
      if(new_sl!=old_sl)
      {
         if(!trade.PositionModify(ticket,new_sl,tp))
            Print("AL-ZOOL local trailing modify failed. ticket=",ticket,
                  " retcode=",trade.ResultRetcode()," ",trade.ResultRetcodeDescription());
      }
   }
}

void PollCentralEngine()
{
   ulong now_ms=GetTickCount64();
   ulong interval_ms=(ulong)InpRequestEverySeconds*1000;
   if(g_last_market_poll_ms>0 && now_ms-g_last_market_poll_ms<interval_ms) return;
   g_last_market_poll_ms=now_ms;

   string decision,decision_id,reason;
   double confidence=0.0;
   int strategy_id=0;
   if(!RequestCentralDecision(decision,decision_id,reason,confidence,strategy_id)) return;
   if(decision=="WAIT")
   {
      g_status="Central WAIT / "+DoubleToString(confidence*100.0,1)+"%";
      g_last_reason=reason;
      GlobalVariableSet(g_global_bar_key,(double)g_last_bar_time);
      return;
   }
   PlaceCentralTrade(decision,decision_id,reason,strategy_id);
}

int OnInit()
{
   if(InpBarsToSend<65 || InpBarsToSend>300 || InpRequestEverySeconds<1 ||
      InpATRPeriod<2 || InpStopLossATR<=0 || InpTakeProfitATR<=0 ||
      InpMaxPositions<1 || InpLots<=0)
      return INIT_PARAMETERS_INCORRECT;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   g_atr_handle=iATR(_Symbol,PERIOD_CURRENT,InpATRPeriod);
   if(g_atr_handle==INVALID_HANDLE)
   {
      Print("AL-ZOOL: cannot create ATR handle. error=",GetLastError());
      return INIT_FAILED;
   }

   g_global_bar_key="AZ_CENTRAL_"+_Symbol+"_"+IntegerToString((long)InpMagic);
   if(GlobalVariableCheck(g_global_bar_key))
      g_last_bar_time=(datetime)GlobalVariableGet(g_global_bar_key);

   EventSetTimer(2);
   g_status="Starting central server connection check";
   Print("AL-ZOOL Central Client initialized on ",_Symbol,
         ". Checking server connection...");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(g_atr_handle!=INVALID_HANDLE) IndicatorRelease(g_atr_handle);
   Comment("");
}

void OnTimer()
{
   ManageLocalProtection();
   FlushPendingOutcomes();
   CheckCentralHealth();
   PollCentralEngine();
   Comment("AL-ZOOL CENTRAL CLIENT\n",
           "Status: ",g_status,"\n",
           "Server: ",(g_server_online ? "ONLINE" : "OFFLINE / NOT CONFIRMED"),"\n",
           "Symbol: ",_Symbol," | TF: ",EnumToString((ENUM_TIMEFRAMES)_Period),"\n",
           "Magic: ",(string)InpMagic,"\n",
           "AI Decision: ",g_last_decision," | Confidence: ",DoubleToString(g_last_confidence*100.0,1),"%\n",
           "Selected Strategy: ",g_last_strategy_name," (ID ",IntegerToString(g_last_strategy_id),")\n",
           "Reason: ",g_last_reason);
}

void OnTick()
{
   // No trading decision is made from the tick handler.
   // Local protection is also serviced by OnTimer.
}
