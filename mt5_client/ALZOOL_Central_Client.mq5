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
input string InpServerSymbol = "XAUUSD";
input int    InpHttpTimeoutMs = 12000;
// Technical identifiers only; not user trading controls.
#define ALZOOL_MAGIC 26110801
#define ALZOOL_ATR_PERIOD 14
#define ALZOOL_REQUEST_SECONDS 1
#define ALZOOL_BARS_TO_SEND 120

CTrade trade;
datetime g_last_bar_time = 0;
ulong g_last_health_check_ms = 0;
ulong g_last_market_poll_ms = 0;
ulong g_last_control_poll_ms = 0;
ulong g_last_terminal_state_ms = 0;
bool g_trading_enabled = false;
string g_last_decision_id = "";
int g_atr_handle = INVALID_HANDLE;
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
bool g_settings_loaded=false;
ulong g_last_settings_poll_ms=0;
double g_cfg_lot=0.01, g_cfg_profit_target=1.0, g_cfg_per_trade_target=2.0;
double g_cfg_loss_limit=50.0, g_cfg_global_loss=50.0;
double g_cfg_fixed_sl_distance=3.0, g_cfg_trail_start=3.0, g_cfg_trail_distance=3.0;
double g_cfg_pending_distance=2.0;
double g_cfg_addon_step=2.0,g_cfg_addon_multiplier=1.2,g_cfg_cooling_step=3.0,g_cfg_cooling_multiplier=1.2;
double g_cfg_addon_max_lot=0.05,g_cfg_cooling_max_lot=0.05;
double g_cfg_recovery_manual_lot=0.01,g_cfg_recovery_max_lot=0.05;
double g_cfg_recovery_trigger_loss=10.0,g_cfg_recovery_target_profit=0.5;
double g_cfg_recovery_atr_distance=1.0,g_cfg_recovery_min_confidence=0.65;
int g_cfg_addon_max=3,g_cfg_cooling_max=2;
int g_cfg_recovery_min_positions=3,g_cfg_recovery_max_entries=0;
datetime g_last_recovery_bar_time=0;
ulong g_last_recovery_entry_ms=0;
bool g_cfg_addon_enabled=false,g_cfg_addon_smart=false,g_cfg_addon_multiplier_enabled=true;
bool g_cfg_recovery_enabled=false;
string g_cfg_recovery_mode="AUTO";
bool g_cfg_cooling_enabled=false,g_cfg_cooling_smart=false,g_cfg_cooling_multiplier_enabled=true;
double g_last_addon_buy_price=0.0,g_last_addon_sell_price=0.0,g_last_cooling_buy_price=0.0,g_last_cooling_sell_price=0.0;
double g_decision_expires_at=0.0;
int g_cfg_max_positions=0;
bool g_cfg_global_profit=true, g_cfg_per_trade_profit=true, g_cfg_global_loss_enabled=true;
bool g_cfg_fixed_sl_enabled=true, g_cfg_trailing=true, g_cfg_one_direction=true,g_cfg_ai_loss_guard=false;
bool g_cfg_allow_buy=true,g_cfg_allow_sell=true,g_cfg_require_broker_stops=true;
bool g_cfg_ai_risk_enabled=false,g_cfg_smart_profit=false;
bool g_cfg_candle_chase_enabled=false,g_cfg_candle_chase_sl_enabled=false,g_cfg_reverse_protection_enabled=false;
double g_cfg_max_spread_points=0.0,g_cfg_ai_risk_percent=0.5,g_cfg_ai_max_lot=0.05;
double g_cfg_reverse_trigger=5.0,g_cfg_reverse_continue=2.0;
double g_cfg_candle_chase_min_move=0.1,g_cfg_candle_chase_buy_target=1.0;
double g_cfg_candle_chase_sell_target=1.0,g_cfg_candle_chase_single_target=0.5,g_cfg_candle_chase_sl_distance=3.0;
int g_cfg_candle_chase_cooldown=2,g_cfg_candle_chase_max_orders=1;
ulong g_last_chase_buy_ms=0,g_last_chase_sell_ms=0;
datetime g_last_chase_bar_time=0;
string g_recovery_count_key="",g_recovery_low_key="",g_recovery_high_key="";
string g_reversal_trigger_key="",g_reversal_side_key="";
double g_context_buy_quality=0.0,g_context_sell_quality=0.0;
bool g_context_buy_trap=false,g_context_sell_trap=false;
string g_context_regime="";
string g_cfg_order_mode="Market Order", g_cfg_strategy_mode="AUTO — Best current fit";
bool g_cfg_adaptive_strategy_switch=false;
bool g_daily_loss_paused=false;
string g_daily_loss_date="";

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

double CalculateRiskLots(const double stop_distance)
{
   if(stop_distance<=0.0 || g_cfg_ai_risk_percent<=0.0 || g_cfg_ai_max_lot<=0.0) return 0.0;
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_value<=0.0) tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tick_size<=0.0 || tick_value<=0.0) return 0.0;
   double risk_money=AccountInfoDouble(ACCOUNT_BALANCE)*g_cfg_ai_risk_percent/100.0;
   double loss_per_lot=(stop_distance/tick_size)*tick_value;
   if(risk_money<=0.0 || loss_per_lot<=0.0) return 0.0;
   double raw=MathMin(g_cfg_ai_max_lot,risk_money/loss_per_lot);
   double minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   if(raw<minv-1e-8) return 0.0;
   return NormalizeLots(raw);
}

double NormalizeCappedLots(const double requested,const double cap)
{
   if(requested<=0.0 || cap<=0.0) return 0.0;
   double bounded=MathMin(requested,cap);
   double normalized=NormalizeLots(bounded);
   if(normalized<=0.0 || normalized>cap+1e-8) return 0.0;
   return normalized;
}

bool GetSmartCandleMetrics(const string direction,double &strength,double &volatility,
                           bool &trend_support,bool &reversal_confirmed,
                           const bool include_forming=false)
{
   strength=0.0; volatility=1.0; trend_support=false; reversal_confirmed=false;
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int start_shift=(include_forming ? 0 : 1);
   if(CopyRates(_Symbol,PERIOD_CURRENT,start_shift,3,rates)<3) return false;
   double atr=0.0;
   if(!GetATR(atr)) return false;
   double range_sum=0.0,body_ratio_sum=0.0;
   int aligned=0;
   for(int i=0;i<3;i++)
   {
      double range=rates[i].high-rates[i].low;
      if(range<=0.0) continue;
      double body=MathAbs(rates[i].close-rates[i].open);
      double body_ratio=body/range;
      range_sum+=range;
      body_ratio_sum+=body_ratio;
      bool candle_buy=(rates[i].close>rates[i].open);
      if((direction=="BUY" && candle_buy) || (direction=="SELL" && !candle_buy && rates[i].close<rates[i].open))
         aligned++;
      if(i==0)
      {
         double directional_wick=(direction=="BUY"
            ? MathMin(rates[i].open,rates[i].close)-rates[i].low
            : rates[i].high-MathMax(rates[i].open,rates[i].close));
         bool body_reversal=(direction=="BUY" ? rates[i].close>rates[i].open : rates[i].close<rates[i].open);
         reversal_confirmed=body_reversal && (body_ratio>=0.30 || directional_wick>=body*0.50);
      }
   }
   strength=body_ratio_sum/3.0;
   double average_range=range_sum/3.0;
   volatility=MathMax(0.75,MathMin(1.50,average_range/atr));
   trend_support=(aligned>=2 && strength>=0.25);
   return true;
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
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
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
   int copied=CopyRates(_Symbol,PERIOD_CURRENT,1,ALZOOL_BARS_TO_SEND,rates);
   if(copied<65) return false;
   bar_time=rates[copied-1].time;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return false;
   g_last_market_bid=tick.bid; g_last_market_ask=tick.ask;

   string strategy_mode="AUTO";
   int selected_strategy_id=0;
   if(StringFind(g_cfg_strategy_mode,"HUNTER")==0) strategy_mode="HUNTER";
   else if(StringFind(g_cfg_strategy_mode,"SNIPER")==0) strategy_mode="SNIPER";
   else if(StringLen(g_cfg_strategy_mode)>0 && StringGetCharacter(g_cfg_strategy_mode,0)>='0' && StringGetCharacter(g_cfg_strategy_mode,0)<='9')
      selected_strategy_id=(int)StringToInteger(g_cfg_strategy_mode);
   int open_buys=0,open_sells=0;
   if(g_cfg_adaptive_strategy_switch && CountOurPositions(open_buys,open_sells)>0)
   {
      strategy_mode="AUTO"; selected_strategy_id=0;
   }
   body="{\"symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",";
   body+="\"strategy_mode\":\""+strategy_mode+"\",\"selected_strategy_id\":"+IntegerToString(selected_strategy_id)+",";
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
   double spread=(tick.ask-tick.bid)/_Point;
   if(g_cfg_max_spread_points>0 && spread>g_cfg_max_spread_points)
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
   g_decision_expires_at=JsonNumber(response,"expires_at",0.0);
   g_context_buy_quality=JsonNumber(response,"buy_quality",0.0);
   g_context_sell_quality=JsonNumber(response,"sell_quality",0.0);
   g_context_buy_trap=JsonBool(response,"buy_trap",false);
   g_context_sell_trap=JsonBool(response,"sell_trap",false);
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

int CountOurPendingOrders()
{
   int total=0;
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong ticket=OrderGetTicket(i);
      if(ticket==0) continue;
      if(OrderGetString(ORDER_SYMBOL)!=_Symbol || (ulong)OrderGetInteger(ORDER_MAGIC)!=ALZOOL_MAGIC) continue;
      total++;
   }
   return total;
}

void CancelOurPendingOrders()
{
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong ticket=OrderGetTicket(i);
      if(ticket==0) continue;
      if(OrderGetString(ORDER_SYMBOL)!=_Symbol || (ulong)OrderGetInteger(ORDER_MAGIC)!=ALZOOL_MAGIC) continue;
      if(!trade.OrderDelete(ticket)) Print("AL-ZOOL pending cancellation failed: ",ticket," ",trade.ResultRetcodeDescription());
   }
}

bool CheckTradingControl(const bool force=false)
{
   ulong now_ms=GetTickCount64();
   // Share the 1-second timer poll across all checks in the same cycle.
   // Actual order placement always forces a fresh server permission check.
   if(!force && g_last_control_poll_ms>0 && now_ms-g_last_control_poll_ms<1000)
      return g_trading_enabled;
   g_last_control_poll_ms=now_ms;
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20)
   {
      g_trading_enabled=false;
      return false;
   }
   char data[],result[];
   ArrayResize(data,0);
   string response_headers;
   string headers="Accept: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   ResetLastError();
   int http=WebRequest("GET",ServerBaseUrl()+"/v1/control/"+InpEngineId,headers,
                       InpHttpTimeoutMs,data,result,response_headers);
   if(http!=200)
   {
      g_trading_enabled=false;
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   // Dashboard pause blocks new entries only. Existing positions, SL/TP and
   // pending orders remain untouched until an explicit server command changes them.
   g_trading_enabled=JsonBool(response,"trading_enabled",false);
   return g_trading_enabled;
}

bool PollCentralSettings()
{
   ulong now_ms=GetTickCount64();
   if(g_last_settings_poll_ms>0 && now_ms-g_last_settings_poll_ms<5000) return g_settings_loaded;
   g_last_settings_poll_ms=now_ms;
   if(StringLen(InpEngineId)<8 || StringLen(InpEngineToken)<20) { g_settings_loaded=false; return false; }
   char data[],result[]; ArrayResize(data,0);
   string headers="Accept: application/json\r\nAuthorization: Bearer "+InpEngineToken+"\r\n";
   string response_headers;
   ResetLastError();
   int http=WebRequest("GET",ServerBaseUrl()+"/v1/settings/"+InpEngineId,headers,InpHttpTimeoutMs,data,result,response_headers);
   if(http!=200)
   {
      g_settings_loaded=false;
      g_status="Settings unavailable / HTTP "+IntegerToString(http);
      return false;
   }
   string response=CharArrayToString(result,0,-1,CP_UTF8);
   double lot=JsonNumber(response,"lot",0.01);
   double maxpos=JsonNumber(response,"max_positions",0);
   if(lot<=0.0 || maxpos<0.0) { g_settings_loaded=false; g_status="Invalid dashboard settings"; return false; }
   g_cfg_lot=lot; g_cfg_max_positions=(int)maxpos;
   g_cfg_profit_target=JsonNumber(response,"profit_target",1.0);
   g_cfg_per_trade_target=JsonNumber(response,"per_trade_profit_target",2.0);
   g_cfg_loss_limit=JsonNumber(response,"loss_limit",50.0);
   g_cfg_global_loss=JsonNumber(response,"global_loss_amount",50.0);
   g_cfg_fixed_sl_distance=JsonNumber(response,"fixed_sl_distance",3.0);
   g_cfg_trail_start=JsonNumber(response,"trail_start",3.0);
   g_cfg_trail_distance=JsonNumber(response,"trail_distance",3.0);
   g_cfg_pending_distance=JsonNumber(response,"pending_distance",2.0);
   g_cfg_addon_step=JsonNumber(response,"addon_step",2.0);
   g_cfg_addon_multiplier=JsonNumber(response,"addon_multiplier",1.2);
   g_cfg_addon_max=(int)JsonNumber(response,"addon_max",3);
   g_cfg_addon_max_lot=JsonNumber(response,"addon_max_lot",0.05);
   g_cfg_cooling_step=JsonNumber(response,"cooling_step",3.0);
   g_cfg_cooling_multiplier=JsonNumber(response,"cooling_multiplier",1.2);
   g_cfg_cooling_max=(int)JsonNumber(response,"cooling_max",2);
   g_cfg_cooling_max_lot=JsonNumber(response,"cooling_max_lot",0.05);
   g_cfg_recovery_manual_lot=JsonNumber(response,"recovery_manual_lot",0.01);
   g_cfg_recovery_max_lot=JsonNumber(response,"recovery_max_lot",0.05);
   g_cfg_recovery_trigger_loss=JsonNumber(response,"recovery_trigger_loss",10.0);
   g_cfg_recovery_target_profit=JsonNumber(response,"recovery_target_profit",0.5);
   g_cfg_recovery_min_positions=(int)JsonNumber(response,"recovery_min_positions",3);
   g_cfg_recovery_atr_distance=JsonNumber(response,"recovery_atr_distance",1.0);
   g_cfg_recovery_min_confidence=JsonNumber(response,"recovery_min_confidence",0.65);
   g_cfg_recovery_max_entries=(int)JsonNumber(response,"recovery_max_entries",0);
   g_cfg_addon_enabled=JsonBool(response,"addon_enabled",false);
   g_cfg_addon_smart=JsonBool(response,"addon_smart_enabled",false);
   g_cfg_addon_multiplier_enabled=JsonBool(response,"addon_multiplier_enabled",true);
   g_cfg_cooling_enabled=JsonBool(response,"cooling_enabled",false);
   g_cfg_cooling_smart=JsonBool(response,"cooling_smart_enabled",false);
   g_cfg_cooling_multiplier_enabled=JsonBool(response,"cooling_multiplier_enabled",true);
   g_cfg_recovery_enabled=JsonBool(response,"recovery_enabled",false);
   g_cfg_recovery_mode=JsonString(response,"recovery_mode");
   if(g_cfg_recovery_mode!="AUTO" && g_cfg_recovery_mode!="MANUAL") g_cfg_recovery_mode="AUTO";
   g_cfg_allow_buy=JsonBool(response,"allow_buy",true);
   g_cfg_allow_sell=JsonBool(response,"allow_sell",true);
   g_cfg_max_spread_points=JsonNumber(response,"max_spread_points",0.0);
   g_cfg_require_broker_stops=JsonBool(response,"require_broker_stops",true);
   g_cfg_ai_risk_enabled=JsonBool(response,"ai_risk_enabled",false);
   g_cfg_ai_risk_percent=JsonNumber(response,"ai_risk_percent",0.5);
   g_cfg_ai_max_lot=JsonNumber(response,"ai_max_lot",0.05);
   g_cfg_smart_profit=JsonBool(response,"smart_profit_enabled",false);
   g_cfg_reverse_protection_enabled=JsonBool(response,"reverse_protection_enabled",false);
   g_cfg_reverse_trigger=JsonNumber(response,"reverse_trigger",5.0);
   g_cfg_reverse_continue=JsonNumber(response,"reverse_continue",2.0);
   g_cfg_candle_chase_enabled=JsonBool(response,"candle_chase_enabled",false);
   g_cfg_candle_chase_min_move=JsonNumber(response,"candle_chase_min_move",0.1);
   g_cfg_candle_chase_cooldown=(int)JsonNumber(response,"candle_chase_cooldown",2);
   g_cfg_candle_chase_buy_target=JsonNumber(response,"candle_chase_buy_target",1.0);
   g_cfg_candle_chase_sell_target=JsonNumber(response,"candle_chase_sell_target",1.0);
   g_cfg_candle_chase_single_target=JsonNumber(response,"candle_chase_single_target",0.5);
   g_cfg_candle_chase_sl_enabled=JsonBool(response,"candle_chase_sl_enabled",false);
   g_cfg_candle_chase_sl_distance=JsonNumber(response,"candle_chase_sl_distance",3.0);
   g_cfg_candle_chase_max_orders=(int)JsonNumber(response,"candle_chase_max_orders",1);
   g_cfg_global_profit=JsonBool(response,"global_profit_enabled",true);
   g_cfg_per_trade_profit=JsonBool(response,"per_trade_profit_enabled",true);
   g_cfg_global_loss_enabled=JsonBool(response,"global_loss_enabled",true);
   g_cfg_fixed_sl_enabled=JsonBool(response,"fixed_sl_enabled",true);
   g_cfg_trailing=JsonBool(response,"trailing_enabled",true);
   g_cfg_one_direction=JsonBool(response,"one_direction_only",true);
   g_cfg_ai_loss_guard=JsonBool(response,"ai_loss_guard_enabled",false);
   g_cfg_order_mode=JsonString(response,"order_mode");
   if(g_cfg_order_mode=="") g_cfg_order_mode="Market Order";
   g_cfg_adaptive_strategy_switch=JsonBool(response,"adaptive_strategy_switch",false);
   g_cfg_strategy_mode=JsonString(response,"strategy_mode");
   if(g_cfg_strategy_mode=="") g_cfg_strategy_mode="AUTO — Best current fit";
   if(g_cfg_max_spread_points<0 || g_cfg_ai_risk_percent<0.05 || g_cfg_ai_risk_percent>2.0 || g_cfg_ai_max_lot<=0 ||
      g_cfg_profit_target<0 || g_cfg_per_trade_target<0 || g_cfg_loss_limit<0 || g_cfg_global_loss<0 ||
      g_cfg_fixed_sl_distance<=0 || g_cfg_trail_start<0 || g_cfg_trail_distance<=0 || g_cfg_pending_distance<=0 ||
      g_cfg_addon_step<=0 || g_cfg_cooling_step<=0 || g_cfg_addon_multiplier<1 || g_cfg_cooling_multiplier<1 ||
      g_cfg_addon_max<0 || g_cfg_cooling_max<0 ||
      g_cfg_addon_max_lot<=0 || g_cfg_cooling_max_lot<=0 ||
      g_cfg_recovery_manual_lot<=0 || g_cfg_recovery_max_lot<=0 || g_cfg_recovery_trigger_loss<=0 ||
      g_cfg_recovery_target_profit<0 || g_cfg_recovery_min_positions<2 || g_cfg_recovery_atr_distance<=0 ||
      g_cfg_recovery_min_confidence<0.50 || g_cfg_recovery_min_confidence>0.95 || g_cfg_recovery_max_entries<0 ||
      g_cfg_reverse_trigger<=0 || g_cfg_reverse_continue<=0 ||
      g_cfg_candle_chase_min_move<=0 || g_cfg_candle_chase_cooldown<0 ||
      g_cfg_candle_chase_buy_target<0 || g_cfg_candle_chase_sell_target<0 ||
      g_cfg_candle_chase_single_target<0 || g_cfg_candle_chase_sl_distance<=0 ||
      g_cfg_candle_chase_max_orders<1 || g_cfg_candle_chase_max_orders>10)
   { g_settings_loaded=false; g_status="Invalid risk/protection settings"; return false; }
   g_settings_loaded=true;
   return true;
}

bool PlaceCentralTrade(const string direction,const string decision_id,
                       const string reason,const int strategy_id,const double lot_override=0.0,
                       const bool recovery_order=false,const bool management_order=false,
                       const string management_tag="")
{
   // All orders require live server health, dashboard permission and a
   // previously verified signed decision. Ordinary entries also require that
   // decision to be unexpired; recovery additionally requires a live reversal.
   if(!g_settings_loaded) { g_status="Entry blocked / dashboard settings not loaded"; RecordBlockedSignal(decision_id,"settings","إعدادات لوحة التحكم لم تصل بعد"); return false; }
   if(g_daily_loss_paused) { g_status="Entry blocked / daily loss limit reached"; RecordBlockedSignal(decision_id,"daily-loss","تم بلوغ حد الخسارة اليومي"); return false; }
   if(!recovery_order && !management_order && (g_decision_expires_at<=0.0 || (double)TimeGMT()>=g_decision_expires_at))
   {
      g_status="Entry blocked / signed decision expired";
      RecordBlockedSignal(decision_id,"expired","انتهت صلاحية الإشارة الموقعة قبل التنفيذ");
      return false;
   }
   if(!g_server_online || (!g_decision_verified && !management_order))
   {
      g_status="Entry blocked / central server or signed decision not confirmed";
      RecordBlockedSignal(decision_id,"server","السيرفر أو توقيع القرار غير مؤكد");
      return false;
   }
   if(!CheckTradingControl(true))
   {
      g_status="Trading disabled from dashboard / no new entries";
      RecordBlockedSignal(decision_id,"trading-off","التداول الآلي متوقف من لوحة التحكم");
      return false;
   }
   // Recovery is intentionally allowed to follow the confirmed market reversal
   // even when ordinary BUY/SELL entry permissions are disabled in the dashboard.
   if(!recovery_order && direction=="BUY" && !g_cfg_allow_buy) { g_status="BUY disabled from dashboard"; RecordBlockedSignal(decision_id,"buy-disabled","الشراء معطل من لوحة التحكم"); return false; }
   if(!recovery_order && direction=="SELL" && !g_cfg_allow_sell) { g_status="SELL disabled from dashboard"; RecordBlockedSignal(decision_id,"sell-disabled","البيع معطل من لوحة التحكم"); return false; }
   if(direction!="BUY" && direction!="SELL") return false;
   if((strategy_id<1 || strategy_id>200) && !recovery_order && !management_order) { g_status="Invalid strategy id"; return false; }
   if(decision_id=="" || decision_id==g_last_decision_id) return false;
   MqlTick fresh_tick;
   if(!SymbolInfoTick(_Symbol,fresh_tick)) return false;
   double current_spread=(fresh_tick.ask-fresh_tick.bid)/_Point;
   if(!recovery_order && g_cfg_max_spread_points>0 && current_spread>g_cfg_max_spread_points)
   {
      g_status="Spread widened after server decision";
      RecordBlockedSignal(decision_id,"spread","اتسع السبريد بعد صدور الإشارة وتجاوز الحد المسموح");
      return false;
   }
   if(!recovery_order && g_cfg_one_direction && HasOppositePosition(direction))
   {
      g_status="Opposite position exists";
      RecordBlockedSignal(decision_id,"opposite","توجد صفقة في الاتجاه المعاكس؛ تم منع الدخول");
      return false;
   }

   int buys=0,sells=0;
   int current_positions=CountOurPositions(buys,sells)+CountOurPendingOrders();
   if(!recovery_order && g_cfg_max_positions>0 && current_positions>=g_cfg_max_positions)
   {
      g_status="Maximum positions reached (dashboard setting)";
      RecordBlockedSignal(decision_id,"max-positions","تم بلوغ الحد الأقصى لعدد الصفقات");
      return false;
   }

   double atr=0.0;
   // Recovery already validates ATR/rebound before reaching this executor;
   // do not add a second ATR gate that could suppress the recovery order.
   if(!GetATR(atr) && !recovery_order && !management_order) { g_status="ATR unavailable"; return false; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return false;

   double stop_distance=g_cfg_fixed_sl_distance;
   if(lot_override<=0.0 && g_cfg_ai_risk_enabled && !g_cfg_fixed_sl_enabled)
   {
      g_status="AI risk sizing requires fixed stop loss to be enabled in dashboard";
      RecordBlockedSignal(decision_id,"ai-risk-no-stop","حساب اللوت حسب المخاطرة يحتاج تفعيل وقف الخسارة الثابت من لوحة التحكم");
      return false;
   }
   double requested_lots=(lot_override>0.0 ? lot_override : g_cfg_lot);
   if(lot_override<=0.0 && g_cfg_ai_risk_enabled)
      requested_lots=CalculateRiskLots(stop_distance);
   double lots=NormalizeLots(requested_lots);
   if(lots<=0.0) { g_status=(g_cfg_ai_risk_enabled && lot_override<=0.0 ? "AI risk lot below broker minimum or unavailable tick value" : "Invalid lot size"); return false; }
   double target_distance=0.0;
   long stops_level=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double min_distance=(double)stops_level*_Point;
   if(g_cfg_require_broker_stops && g_cfg_fixed_sl_enabled && stop_distance<min_distance)
   {
      g_status="Broker stop distance too small";
      RecordBlockedSignal(decision_id,"stops","مسافة وقف الخسارة أقل من الحد الذي يسمح به الوسيط");
      return false;
   }

   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double sl=0.0,tp=0.0,price=0.0;
   bool sent=false;
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(_Symbol);

   ulong broker_order_started_ms=GetTickCount64();
   if(direction=="BUY")
   {
      price=tick.ask;
      if(!recovery_order && !management_order && g_cfg_order_mode=="Pending Order")
      {
         price=NormalizeDouble(tick.ask+MathMax(g_cfg_pending_distance,min_distance),digits);
         sl=(g_cfg_fixed_sl_enabled ? NormalizeDouble(price-stop_distance,digits) : 0.0);
         sent=trade.BuyStop(lots,price,_Symbol,sl,0.0,ORDER_TIME_GTC,0,(recovery_order ? "AZR_RECOVERY" : (management_order ? (management_tag!="" ? management_tag : "AZS_MANAGEMENT") : "AZS"+IntegerToString(strategy_id))));
      }
      else
      {
         sl=(g_cfg_fixed_sl_enabled ? NormalizeDouble(price-stop_distance,digits) : 0.0);
         sent=trade.Buy(lots,_Symbol,0.0,sl,0.0,(recovery_order ? "AZR_RECOVERY" : (management_order ? (management_tag!="" ? management_tag : "AZS_MANAGEMENT") : "AZS"+IntegerToString(strategy_id))));
      }
   }
   else
   {
      price=tick.bid;
      if(!recovery_order && !management_order && g_cfg_order_mode=="Pending Order")
      {
         price=NormalizeDouble(tick.bid-MathMax(g_cfg_pending_distance,min_distance),digits);
         sl=(g_cfg_fixed_sl_enabled ? NormalizeDouble(price+stop_distance,digits) : 0.0);
         sent=trade.SellStop(lots,price,_Symbol,sl,0.0,ORDER_TIME_GTC,0,(recovery_order ? "AZR_RECOVERY" : (management_order ? (management_tag!="" ? management_tag : "AZS_MANAGEMENT") : "AZS"+IntegerToString(strategy_id))));
      }
      else
      {
         sl=(g_cfg_fixed_sl_enabled ? NormalizeDouble(price+stop_distance,digits) : 0.0);
         sent=trade.Sell(lots,_Symbol,0.0,sl,0.0,(recovery_order ? "AZR_RECOVERY" : (management_order ? (management_tag!="" ? management_tag : "AZS_MANAGEMENT") : "AZS"+IntegerToString(strategy_id))));
      }
   }

   uint rc=trade.ResultRetcode();
   Print("AL-ZOOL LATENCY broker_order_ms=",GetTickCount64()-broker_order_started_ms,
         " retcode=",rc," description=",trade.ResultRetcodeDescription());
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
      string sent_payload="{\"decision_id\":\""+JsonSafe(decision_id)+"\",\"side\":\""+direction+
         "\",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"order_id\":\""+(string)order_ticket+
         "\",\"volume\":"+DoubleToString(lots,2)+",\"price\":"+DoubleToString(price,digits)+
         ",\"strategy_id\":"+IntegerToString(strategy_id)+",\"reason_ar\":\""+JsonSafe(reason)+"\"}";
      SendSignalEvent(decision_id+"_order-sent","ORDER_SENT",sent_payload);
      if(management_order && management_tag=="AZS_ADDON")
         SendSignalEvent(decision_id+"_addon-opened","ADDON_OPENED",sent_payload);
      else if(management_order && management_tag=="AZS_COOLING")
         SendSignalEvent(decision_id+"_cooling-opened","COOLING_OPENED",sent_payload);
      return true;
   }
   g_status="Order rejected: "+trade.ResultRetcodeDescription();
   string reject_payload="{\"decision_id\":\""+JsonSafe(decision_id)+"\",\"side\":\""+direction+
      "\",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"volume\":"+DoubleToString(lots,2)+
      ",\"retcode\":"+IntegerToString((int)rc)+",\"reason_ar\":\""+JsonSafe(trade.ResultRetcodeDescription())+"\"}";
   SendSignalEvent(decision_id+"_order-rejected","ORDER_REJECTED",reject_payload);
   return false;
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
double CurrentOurFloatingPnl(int &count)
{
   double total=0.0; count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      total+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
      count++;
   }
   return total;
}

void CloseOurPositions(const string reason)
{
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      if(!trade.PositionClose(ticket)) Print("AL-ZOOL close failed: ",ticket," ",trade.ResultRetcodeDescription());
   }
   CancelOurPendingOrders();
   g_status=reason;
}

double TodayClosedPnl()
{
   datetime now=TimeCurrent();
   datetime midnight=StringToTime(TimeToString(now,TIME_DATE));
   if(!HistorySelect(midnight,now)) return 0.0;
   double pnl=0.0;
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=ALZOOL_MAGIC) continue;
      ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) continue;
      pnl+=HistoryDealGetDouble(deal,DEAL_PROFIT)+HistoryDealGetDouble(deal,DEAL_SWAP)+HistoryDealGetDouble(deal,DEAL_COMMISSION);
   }
   return pnl;
}

void ManageSettingsProfitLoss()
{
   if(!g_settings_loaded) return;
   string today=TimeToString(TimeCurrent(),TIME_DATE);
   if(g_daily_loss_date!=today) { g_daily_loss_date=today; g_daily_loss_paused=false; }
   int count=0; double floating=CurrentOurFloatingPnl(count);
   if(g_cfg_loss_limit>0.0 && TodayClosedPnl()+floating<=-MathAbs(g_cfg_loss_limit))
   {
      if(count>0) CloseOurPositions("Daily loss limit reached");
      else CancelOurPendingOrders();
      g_daily_loss_paused=true;
      return;
   }
   if(count<=0) return;
   if(g_cfg_global_loss_enabled && g_cfg_global_loss>0.0 && floating<=-MathAbs(g_cfg_global_loss))
   {
      CloseOurPositions("Global loss protection reached / dashboard settings");
      g_daily_loss_paused=true;
      return;
   }
   double profit_factor=1.0;
   if(g_cfg_smart_profit)
   {
      string side="BUY";
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong ticket=PositionGetTicket(i);
         if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
         side=(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY ? "BUY" : "SELL");
         break;
      }
      double strength=0.0,volatility=1.0; bool trend=false,reversal=false;
      if(GetSmartCandleMetrics(side,strength,volatility,trend,reversal,true))
         profit_factor=MathMax(0.75,MathMin(1.50,volatility));
   }
   double single_target=g_cfg_per_trade_target*profit_factor;
   double basket_target=g_cfg_profit_target*profit_factor;
   if(count==1 && g_cfg_per_trade_profit && single_target>0.0 && floating>=single_target)
   {
      CloseOurPositions("Single-position profit target reached / smart factor "+DoubleToString(profit_factor,2));
      return;
   }
   if(count>=2 && g_cfg_global_profit && basket_target>0.0 && floating>=basket_target)
   {
      CloseOurPositions("Combined profit target reached / smart factor "+DoubleToString(profit_factor,2));
      return;
   }
}

void ManageLocalProtection()
{
   if(!g_settings_loaded || !g_cfg_trailing) return;
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
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;

      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open_price=PositionGetDouble(POSITION_PRICE_OPEN);
      double old_sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      double new_sl=old_sl;

      if(!g_cfg_trailing) continue;
      if(type==POSITION_TYPE_BUY)
      {
         if(tick.bid-open_price<g_cfg_trail_start) continue;
         double candidate=NormalizeDouble(tick.bid-g_cfg_trail_distance,digits);
         if(tick.bid-candidate<min_distance) candidate=NormalizeDouble(tick.bid-min_distance,digits);
         if(old_sl==0.0 || candidate>old_sl+_Point) new_sl=candidate;
      }
      else if(type==POSITION_TYPE_SELL)
      {
         if(open_price-tick.ask<g_cfg_trail_start) continue;
         double candidate=NormalizeDouble(tick.ask+g_cfg_trail_distance,digits);
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
      ",\"symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",\"point\":"+DoubleToString(_Point,10)+",\"volume_step\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),8)+",\"volume_min\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),8)+",\"volume_max\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),8)+",";
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
         ",\"tp\":"+DoubleToString(OrderGetDouble(ORDER_TP),_Digits)+",\"magic\":\""+(string)OrderGetInteger(ORDER_MAGIC)+"\",\"comment\":\""+JsonSafe(OrderGetString(ORDER_COMMENT))+"\"}";
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
   body+="],\"settings\":{\"chart_symbol\":\""+JsonSafe(_Symbol)+"\",\"timeframe\":\""+EnumToString((ENUM_TIMEFRAMES)_Period)+"\",\"magic\":\""+(string)ALZOOL_MAGIC+"\",\"server_url\":\""+JsonSafe(ServerBaseUrl())+"\",\"engine_id\":\""+JsonSafe(InpEngineId)+"\",\"engine_status\":\""+JsonSafe(g_status)+"\",\"last_decision\":\""+JsonSafe(g_last_decision)+"\",\"last_strategy\":\""+JsonSafe(g_last_strategy_name)+"\",\"last_strategy_id\":"+IntegerToString(g_last_strategy_id)+",\"confidence\":"+DoubleToString(g_last_confidence,4)+",\"trading_enabled\":"+(g_trading_enabled?"true":"false")+",\"server_online\":"+(g_server_online?"true":"false")+",\"request_seconds\":"+IntegerToString(ALZOOL_REQUEST_SECONDS)+",\"bars_to_send\":"+IntegerToString(ALZOOL_BARS_TO_SEND)+"},\"events\":[]";
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

   // Keep market data and AI analysis flowing even while dashboard trading is OFF.
   // The actual order path re-checks CheckTradingControl() inside PlaceCentralTrade(),
   // so analysis remains live without allowing new orders while trading is disabled.
   CheckTradingControl();

   // Do not request a signal while health is offline.
   // The health check is throttled internally and returns the last confirmed state.
   if(!CheckCentralHealth()) return;

   string decision,decision_id,reason;
   double confidence=0.0;
   int strategy_id=0;
   if(!PollCentralSettings()) return;
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
   trade.SetExpertMagicNumber(ALZOOL_MAGIC);
   trade.SetTypeFillingBySymbol(_Symbol);
   g_atr_handle=iATR(_Symbol,PERIOD_CURRENT,ALZOOL_ATR_PERIOD);
   if(g_atr_handle==INVALID_HANDLE)
   {
      Print("AL-ZOOL: cannot create ATR handle. error=",GetLastError());
      return INIT_FAILED;
   }

   g_global_bar_key="AZ_CENTRAL_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   g_recovery_count_key="AZ_REC_COUNT_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   g_recovery_low_key="AZ_REC_LOW_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   g_recovery_high_key="AZ_REC_HIGH_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   g_reversal_trigger_key="AZ_REV_PRICE_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   g_reversal_side_key="AZ_REV_SIDE_"+_Symbol+"_"+IntegerToString((long)ALZOOL_MAGIC);
   if(!GlobalVariableCheck(g_recovery_count_key)) GlobalVariableSet(g_recovery_count_key,0.0);
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
   if(g_atr_handle!=INVALID_HANDLE) IndicatorRelease(g_atr_handle);
   Comment("");
}

void ManageAiLossGuard()
{
   if(!g_settings_loaded || !g_trading_enabled || !g_cfg_ai_loss_guard || !g_decision_verified ||
      g_decision_expires_at<=0.0 || (double)TimeGMT()>=g_decision_expires_at || g_last_confidence<0.72) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      double pnl=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
      if(pnl>=0.0) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      bool is_buy=(type==POSITION_TYPE_BUY);
      string opposite=(is_buy ? "SELL" : "BUY");
      double same_quality=(is_buy ? g_context_buy_quality : g_context_sell_quality);
      double opposite_quality=(is_buy ? g_context_sell_quality : g_context_buy_quality);
      bool trap_against=(is_buy ? g_context_buy_trap : g_context_sell_trap);
      bool regime_opposes=(is_buy ? g_context_regime=="اتجاه هابط" : g_context_regime=="اتجاه صاعد");
      bool evidence=(g_last_decision==opposite && opposite_quality>same_quality &&
                     g_last_confidence>=0.72 && (trap_against || regime_opposes));
      if(!evidence) continue;
      trade.SetExpertMagicNumber(ALZOOL_MAGIC);
      if(trade.PositionClose(ticket))
         Print("AL-ZOOL AI loss guard closed losing position ",ticket,"; pnl=",DoubleToString(pnl,2),"; opposite quality=",DoubleToString(opposite_quality,1));
      else
         Print("AL-ZOOL AI loss guard close failed ",ticket,"; ",trade.ResultRetcodeDescription());
   }
}

void RecoveryWait(const string reason)
{
   string message="Smart recovery waiting / "+reason;
   if(g_status!=message) Print("AL-ZOOL SMART RECOVERY: ",reason);
   g_status=message;
}

bool SendRecoveryVolume(const string direction,const double requested_lots,const string decision_id,const string reason)
{
   double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(vmax<=0.0 || vmin<=0.0 || step<=0.0) return false;

   int digits=(step<0.001 ? 4 : (step<0.01 ? 3 : 2));
   double lots=NormalizeDouble(MathCeil(requested_lots/step-1e-8)*step,digits);
   // Recovery is deliberately ONE market order. Never split a large recovery
   // volume into multiple orders; if the broker's per-order limit is lower,
   // report that fact and wait rather than changing the requested lot.
   if(lots<vmin-1e-8 || lots>vmax+1e-8)
   {
      RecoveryWait("حجم التعافي المطلوب خارج حدود الصفقة الواحدة لدى الوسيط؛ المطلوب="+DoubleToString(lots,digits)+
                   " والحد الأقصى="+DoubleToString(vmax,digits));
      return false;
   }

   MqlTick q;
   if(!SymbolInfoTick(_Symbol,q)) return false;
   ENUM_ORDER_TYPE order_type=(direction=="BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double price=(direction=="BUY" ? q.ask : q.bid),margin=0.0;
   if(!OrderCalcMargin(order_type,_Symbol,lots,price,margin) ||
      margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE))
   {
      RecoveryWait("الهامش الحر لا يكفي لحجم التعافي المطلوب "+DoubleToString(lots,digits));
      return false;
   }
   return PlaceCentralTrade(direction,decision_id,reason,0,lots,true);
}

void ManageSmartRecovery()
{
   int buys=0,sells=0;
   int total=CountOurPositions(buys,sells);
   if(total<=0)
   {
      g_last_recovery_bar_time=0;
      if(g_recovery_count_key!="") GlobalVariableSet(g_recovery_count_key,0.0);
      if(g_recovery_low_key!="") GlobalVariableDel(g_recovery_low_key);
      if(g_recovery_high_key!="") GlobalVariableDel(g_recovery_high_key);
      return;
   }
   if(!g_cfg_recovery_enabled) return;

   int recovery_open=0;
   double floating=0.0,total_open_lots=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      floating+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
      total_open_lots+=PositionGetDouble(POSITION_VOLUME);
      if(StringFind(PositionGetString(POSITION_COMMENT),"AZR_RECOVERY")>=0) recovery_open++;
   }

   if(g_cfg_recovery_target_profit>0.0 && floating>=g_cfg_recovery_target_profit)
   {
      Print("AL-ZOOL recovery basket target reached: ",DoubleToString(floating,2));
      CloseOurPositions("Smart recovery basket target reached");
      return;
   }
   if(!g_settings_loaded) { RecoveryWait("إعدادات اللوحة لم تصل"); return; }
   if(!g_trading_enabled) { RecoveryWait("التداول متوقف من اللوحة"); return; }
   if(!g_server_online || !g_decision_verified) { RecoveryWait("لا يوجد قرار مركزي موثّق"); return; }
   if(!CheckTradingControl()) { RecoveryWait("تعذر تأكيد صلاحية التداول"); return; }
   if(total<g_cfg_recovery_min_positions) { RecoveryWait("عدد صفقات السلة أقل من الحد الأدنى"); return; }
   if(floating>-g_cfg_recovery_trigger_loss) { RecoveryWait("الخسارة لم تصل إلى حد التفعيل"); return; }
   if(total_open_lots<=0.0) { RecoveryWait("لا توجد لوتات مفتوحة لحساب التعافي"); return; }

   // Exactly one recovery order per basket: do not compound another recovery
   // entry while the first recovery position is still open.
   if(recovery_open>0) { RecoveryWait("صفقة التعافي موجودة بالفعل؛ ننتظر إغلاق السلة على الهدف"); return; }
   double recovery_count=(g_recovery_count_key!="" && GlobalVariableCheck(g_recovery_count_key) ? GlobalVariableGet(g_recovery_count_key) : 0.0);
   if(g_cfg_recovery_max_entries>0 && recovery_count>=g_cfg_recovery_max_entries)
   { RecoveryWait("تم بلوغ عدد محاولات التعافي المحدد لهذه السلة"); return; }

   // Recovery follows the current market direction, independently of basket side.
   string direction=g_last_decision;
   if(direction!="BUY" && direction!="SELL")
      direction=(g_context_buy_quality>=g_context_sell_quality ? "BUY" : "SELL");

   MqlTick recovery_tick;
   double recovery_atr=0.0;
   if(!SymbolInfoTick(_Symbol,recovery_tick) || !GetATR(recovery_atr))
   { RecoveryWait("بيانات السعر أو ATR غير متاحة للتعافي"); return; }
   double adverse_extreme=(direction=="BUY"
      ? (g_recovery_low_key!="" && GlobalVariableCheck(g_recovery_low_key) ? GlobalVariableGet(g_recovery_low_key) : recovery_tick.bid)
      : (g_recovery_high_key!="" && GlobalVariableCheck(g_recovery_high_key) ? GlobalVariableGet(g_recovery_high_key) : recovery_tick.ask));
   if(direction=="BUY")
   {
      adverse_extreme=MathMin(adverse_extreme,recovery_tick.bid);
      if(g_recovery_low_key!="") GlobalVariableSet(g_recovery_low_key,adverse_extreme);
      if(recovery_tick.bid-adverse_extreme<recovery_atr*g_cfg_recovery_atr_distance)
      { RecoveryWait("لم تكتمل مسافة الارتداد المحددة بالـ ATR"); return; }
   }
   else
   {
      adverse_extreme=MathMax(adverse_extreme,recovery_tick.ask);
      if(g_recovery_high_key!="") GlobalVariableSet(g_recovery_high_key,adverse_extreme);
      if(adverse_extreme-recovery_tick.ask<recovery_atr*g_cfg_recovery_atr_distance)
      { RecoveryWait("لم تكتمل مسافة الارتداد المحددة بالـ ATR"); return; }
   }
   double strength=0.0,volatility=1.0;
   bool trend=false,reversal=false;
   if(!GetSmartCandleMetrics(direction,strength,volatility,trend,reversal,true) || !reversal)
   { RecoveryWait("شمعة السوق لم تؤكد اتجاه التعافي"); return; }
   bool strong=(strength>=0.45);
   double quality=(direction=="BUY" ? g_context_buy_quality : g_context_sell_quality);
   double other=(direction=="BUY" ? g_context_sell_quality : g_context_buy_quality);
   if(g_last_decision!=direction && !strong && quality<=other)
   { RecoveryWait("اتجاه السوق غير مؤكد للتعافي"); return; }
   if(g_last_confidence<g_cfg_recovery_min_confidence && !strong)
   { RecoveryWait("ثقة السوق منخفضة"); return; }

   ulong now=GetTickCount64();
   if(g_last_recovery_entry_ms>0 && now-g_last_recovery_entry_ms<3000)
   { RecoveryWait("فترة تهدئة قصيرة بين أوامر التعافي"); return; }

   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   if(step<=0.0 || vmin<=0.0 || vmax<=0.0) { RecoveryWait("إعدادات حجم اللوت لدى الوسيط غير صالحة"); return; }
   int volume_digits=(step<0.001 ? 4 : (step<0.01 ? 3 : 2));

   // User-defined rule: sum the lots of ALL open basket positions, then add
   // exactly one broker volume step (normally 0.01 lot).
   double requested=(g_cfg_recovery_mode=="MANUAL"
      ? g_cfg_recovery_manual_lot
      : NormalizeDouble(MathCeil((total_open_lots+step)/step-1e-8)*step,volume_digits));
   if(g_cfg_recovery_mode=="MANUAL" && g_cfg_recovery_max_lot>0.0)
      requested=MathMin(requested,g_cfg_recovery_max_lot);
   string id=g_last_decision_id+"_REC_"+IntegerToString((long)TimeCurrent())+"_"+
             IntegerToString((long)GetTickCount64());
   string why=(g_cfg_recovery_mode=="MANUAL"
      ? "تعافٍ يدوي "+direction+"؛ اللوت المحدد="+DoubleToString(requested,volume_digits)
      : "تعافٍ آلي "+direction+"؛ مجموع لوتات السلة="+DoubleToString(total_open_lots,volume_digits)+
        "؛ لوت التعافي="+DoubleToString(requested,volume_digits)+" (مجموع اللوتات + خطوة واحدة)");
   if(SendRecoveryVolume(direction,requested,id,why))
   {
      g_last_recovery_entry_ms=GetTickCount64();
      g_last_recovery_bar_time=iTime(_Symbol,PERIOD_CURRENT,0);
      if(g_recovery_count_key!="") GlobalVariableSet(g_recovery_count_key,recovery_count+1.0);
      g_status="Smart recovery opened / "+direction+" / lot "+DoubleToString(requested,volume_digits);
      Print("AL-ZOOL recovery opened ",direction," basket_lots=",DoubleToString(total_open_lots,volume_digits),
            " recovery_lot=",DoubleToString(requested,volume_digits));
   }
}
int CountOurManagementPositions(const string tag)
{
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      string comment=PositionGetString(POSITION_COMMENT);
      if(StringFind(comment,tag)>=0) count++;
   }
   return count;
}

void AddonCoolingWait(const string reason)
{
   string message="تعزيز/تبريد ينتظر: "+reason;
   if(g_status!=message) Print("AL-ZOOL ADDON/COOLING: ",reason);
   g_status=message;
}

void ManageAddonCooling()
{
   if(!g_settings_loaded) { AddonCoolingWait("إعدادات اللوحة غير محمّلة"); return; }
   if(!g_trading_enabled) { AddonCoolingWait("التداول الآلي متوقف من اللوحة"); return; }
   if(!g_server_online)
   { AddonCoolingWait("السيرفر غير متاح؛ تم إيقاف أوامر الإدارة الجديدة"); return; }
   if(!g_cfg_addon_enabled && !g_cfg_cooling_enabled) return;
   if(!CheckTradingControl()) { AddonCoolingWait("صلاحية التداول غير مؤكدة"); return; }

   int buys=0,sells=0;
   int group_count=CountOurPositions(buys,sells);
   if(buys>0 && sells>0)
   { AddonCoolingWait("توجد صفقات شراء وبيع معاً؛ لن نضيف صفقة عكسية"); return; }

   // Use the actual open basket direction, not the latest AI decision.
   // The AI may say WAIT or its short-lived signal may expire while the
   // existing basket is still moving and needs add-on/cooling management.
   if(group_count<=0)
   {
      g_last_addon_buy_price=0.0; g_last_addon_sell_price=0.0;
      g_last_cooling_buy_price=0.0; g_last_cooling_sell_price=0.0;
      return;
   }
   string direction=(buys>0 ? "BUY" : "SELL");
   // Separate add-on and cooling quotas; legacy management trades count toward both.
   int legacy_management=CountOurManagementPositions("AZS_MANAGEMENT");
   int addon_count=CountOurManagementPositions("AZS_ADDON")+legacy_management;
   int cooling_count=CountOurManagementPositions("AZS_COOLING")+legacy_management;
   double latest_price=0.0;
   datetime latest_time=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if((direction=="BUY" && type!=POSITION_TYPE_BUY) ||
         (direction=="SELL" && type!=POSITION_TYPE_SELL)) continue;
      datetime opened=(datetime)PositionGetInteger(POSITION_TIME);
      if(opened>=latest_time)
      { latest_time=opened; latest_price=PositionGetDouble(POSITION_PRICE_OPEN); }
   }
   if(latest_price<=0.0) { AddonCoolingWait("تعذر تحديد سعر آخر صفقة في السلة"); return; }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) { AddonCoolingWait("لا يوجد سعر حي من MT5"); return; }
   double quote=(direction=="BUY" ? tick.bid : tick.ask);
   double addon_anchor=(direction=="BUY" ? g_last_addon_buy_price : g_last_addon_sell_price);
   double cooling_anchor=(direction=="BUY" ? g_last_cooling_buy_price : g_last_cooling_sell_price);
   if(addon_anchor<=0.0) addon_anchor=latest_price;
   if(cooling_anchor<=0.0) cooling_anchor=latest_price;

   double candle_strength=0.0,volatility_factor=1.0;
   bool candle_trend_support=false,candle_reversal=false;
   bool candle_data_ok=GetSmartCandleMetrics(direction,candle_strength,volatility_factor,
                                              candle_trend_support,candle_reversal);

   if(g_cfg_addon_enabled && addon_count<g_cfg_addon_max)
   {
      double step=g_cfg_addon_step*(g_cfg_addon_smart && candle_data_ok ? volatility_factor : 1.0);
      double favorable=(direction=="BUY" ? quote-addon_anchor : addon_anchor-quote);
      bool smart_ok=!g_cfg_addon_smart ||
                    (candle_data_ok && candle_trend_support && g_last_confidence>=0.60);
      if(favorable>=step && smart_ok)
      {
         double lot_factor=(g_cfg_addon_multiplier_enabled
                            ? MathPow(g_cfg_addon_multiplier,addon_count+1) : 1.0);
         if(g_cfg_addon_smart)
            lot_factor*=((0.80+0.40*g_last_confidence)*(0.85+0.30*candle_strength));
         double lot=NormalizeCappedLots(g_cfg_lot*lot_factor,g_cfg_addon_max_lot);
         string unique_id=g_last_decision_id+"_ADD_"+IntegerToString((long)TimeCurrent());
         string reason="تعزيز: تحرك السعر لصالح السلة بمقدار "+
                       DoubleToString(favorable,2)+"؛ المسافة المطلوبة "+
                       DoubleToString(step,2)+"؛ اللوت "+DoubleToString(lot,2);
         if(lot>0.0 && PlaceCentralTrade(direction,unique_id,reason,0,lot,false,true,"AZS_ADDON"))
         {
            if(direction=="BUY") g_last_addon_buy_price=quote;
            else g_last_addon_sell_price=quote;
            g_status="Add-on opened / "+direction+" / lot "+DoubleToString(lot,2);
            Print("AL-ZOOL ADDON OPENED: ",direction," lot=",DoubleToString(lot,2),
                  " favorable=",DoubleToString(favorable,2)," step=",DoubleToString(step,2));
            return;
         }
         if(lot<=0.0) AddonCoolingWait("لوت التعزيز تجاوز السقف أو أقل من الحد الأدنى");
      }
      else if(favorable>=step && !smart_ok)
         AddonCoolingWait("شرط التعزيز الذكي لم يكتمل: توافق الشمعة/الثقة");
   }
   else if(g_cfg_addon_enabled && addon_count>=g_cfg_addon_max)
      AddonCoolingWait("تم بلوغ الحد الأقصى للصفقات الإضافية");

   if(g_cfg_cooling_enabled && cooling_count<g_cfg_cooling_max)
   {
      double step=g_cfg_cooling_step*(g_cfg_cooling_smart && candle_data_ok ? volatility_factor : 1.0);
      double adverse=(direction=="BUY" ? cooling_anchor-quote : quote-cooling_anchor);
      bool smart_ok=!g_cfg_cooling_smart ||
                    (candle_data_ok && candle_reversal && g_last_confidence>=0.68);
      if(adverse>=step && smart_ok)
      {
         double lot_factor=(g_cfg_cooling_multiplier_enabled
                            ? MathPow(g_cfg_cooling_multiplier,cooling_count+1) : 1.0);
         if(g_cfg_cooling_smart)
            lot_factor*=((0.75+0.35*g_last_confidence)*(0.85+0.30*candle_strength));
         double lot=NormalizeCappedLots(g_cfg_lot*lot_factor,g_cfg_cooling_max_lot);
         string unique_id=g_last_decision_id+"_COOL_"+IntegerToString((long)TimeCurrent());
         string reason="تبريد: تحرك السعر ضد السلة بمقدار "+
                       DoubleToString(adverse,2)+"؛ المسافة المطلوبة "+
                       DoubleToString(step,2)+"؛ اللوت "+DoubleToString(lot,2);
         if(lot>0.0 && PlaceCentralTrade(direction,unique_id,reason,0,lot,false,true,"AZS_COOLING"))
         {
            if(direction=="BUY") g_last_cooling_buy_price=quote;
            else g_last_cooling_sell_price=quote;
            g_status="Cooling opened / "+direction+" / lot "+DoubleToString(lot,2);
            Print("AL-ZOOL COOLING OPENED: ",direction," lot=",DoubleToString(lot,2),
                  " adverse=",DoubleToString(adverse,2)," step=",DoubleToString(step,2));
            return;
         }
         if(lot<=0.0) AddonCoolingWait("لوت التبريد تجاوز السقف أو أقل من الحد الأدنى");
      }
      else if(adverse>=step && !smart_ok)
         AddonCoolingWait("شرط التبريد الذكي لم يكتمل: انعكاس الشمعة/الثقة");
   }
   else if(g_cfg_cooling_enabled && cooling_count>=g_cfg_cooling_max)
      AddonCoolingWait("تم بلوغ الحد الأقصى للصفقات الإضافية");
}

void ManageConfirmedReversal()
{
   if(!g_settings_loaded || !g_trading_enabled) return;
   if(!g_cfg_reverse_protection_enabled)
   {
      if(g_reversal_trigger_key!="") GlobalVariableDel(g_reversal_trigger_key);
      if(g_reversal_side_key!="") GlobalVariableDel(g_reversal_side_key);
      return;
   }
   int buys=0,sells=0;
   int total=CountOurPositions(buys,sells);
   if(total<=0)
   {
      if(g_reversal_trigger_key!="") GlobalVariableDel(g_reversal_trigger_key);
      if(g_reversal_side_key!="") GlobalVariableDel(g_reversal_side_key);
      return;
   }
   if(buys>0 && sells>0) return;
   // A dashboard stop blocks new trading actions; it must not trigger a reversal close.
   if(!g_trading_enabled || !g_server_online || !g_decision_verified || g_decision_expires_at<=0.0 ||
      (double)TimeGMT()>=g_decision_expires_at || g_last_confidence<0.65)
   {
      if(g_reversal_trigger_key!="") GlobalVariableDel(g_reversal_trigger_key);
      if(g_reversal_side_key!="") GlobalVariableDel(g_reversal_side_key);
      return;
   }

   string basket_side=(buys>0 ? "BUY" : "SELL");
   string opposite=(basket_side=="BUY" ? "SELL" : "BUY");
   if(g_last_decision!=opposite)
   {
      if(g_reversal_trigger_key!="") GlobalVariableDel(g_reversal_trigger_key);
      if(g_reversal_side_key!="") GlobalVariableDel(g_reversal_side_key);
      return;
   }

   double weighted=0.0,volume=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if((basket_side=="BUY" && type!=POSITION_TYPE_BUY) || (basket_side=="SELL" && type!=POSITION_TYPE_SELL)) continue;
      double v=PositionGetDouble(POSITION_VOLUME);
      weighted+=PositionGetDouble(POSITION_PRICE_OPEN)*v;
      volume+=v;
   }
   if(volume<=0.0) return;
   double average=weighted/volume;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return;
   double current=(basket_side=="BUY" ? tick.bid : tick.ask);
   double adverse=(basket_side=="BUY" ? average-current : current-average);
   double trigger_price=0.0;
   int stored_side=(g_reversal_side_key!="" && GlobalVariableCheck(g_reversal_side_key)
                   ? (int)GlobalVariableGet(g_reversal_side_key) : 0);
   if(g_reversal_trigger_key!="" && GlobalVariableCheck(g_reversal_trigger_key) && stored_side==(basket_side=="BUY" ? 1 : -1))
      trigger_price=GlobalVariableGet(g_reversal_trigger_key);

   if(trigger_price<=0.0)
   {
      if(adverse<g_cfg_reverse_trigger) return;
      trigger_price=current;
      if(g_reversal_trigger_key!="") GlobalVariableSet(g_reversal_trigger_key,trigger_price);
      if(g_reversal_side_key!="") GlobalVariableSet(g_reversal_side_key,(basket_side=="BUY" ? 1.0 : -1.0));
      g_status="انعكاس محتمل: تم بلوغ مسافة التفعيل؛ ننتظر استمرار الحركة";
      Print("AL-ZOOL reversal watch started. side=",basket_side," trigger_price=",DoubleToString(trigger_price,_Digits));
      return;
   }

   bool continued=(basket_side=="BUY"
                   ? current<=trigger_price-g_cfg_reverse_continue
                   : current>=trigger_price+g_cfg_reverse_continue);
   if(!continued) return;

   string reason="إغلاق سلة "+basket_side+" بعد تأكيد استمرار الانعكاس المعاكس "+opposite;
   Print("AL-ZOOL confirmed reversal: ",reason,"; trigger=",DoubleToString(g_cfg_reverse_trigger,2),
         "; continuation=",DoubleToString(g_cfg_reverse_continue,2));
   CloseOurPositions(reason);
   if(g_reversal_trigger_key!="") GlobalVariableDel(g_reversal_trigger_key);
   if(g_reversal_side_key!="") GlobalVariableDel(g_reversal_side_key);
}

void ManageCandleChase()
{
   if(!g_settings_loaded || !g_cfg_candle_chase_enabled) return;
   if(g_daily_loss_paused || !g_trading_enabled || !g_server_online || !CheckTradingControl()) return;

   MqlRates candle[];
   ArraySetAsSeries(candle,true);
   if(CopyRates(_Symbol,PERIOD_CURRENT,0,1,candle)!=1) return;
   if(g_last_chase_bar_time!=candle[0].time)
   {
      // Remove stale GTC fallback orders from the previous candle before
      // staging the new candle's high/low breakout orders.
      for(int j=OrdersTotal()-1;j>=0;j--)
      {
         ulong old_ticket=OrderGetTicket(j);
         if(old_ticket==0) continue;
         if(OrderGetString(ORDER_SYMBOL)!=_Symbol || (ulong)OrderGetInteger(ORDER_MAGIC)!=ALZOOL_MAGIC) continue;
         string old_comment=OrderGetString(ORDER_COMMENT);
         if(StringFind(old_comment,"AZC_CHASE_")==0 && !trade.OrderDelete(old_ticket))
            Print("AL-ZOOL candle chase stale-order delete failed: ",old_ticket," ",trade.ResultRetcodeDescription());
      }
      g_last_chase_bar_time=candle[0].time;
      g_last_chase_buy_ms=0;
      g_last_chase_sell_ms=0;
   }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<=tick.bid) return;
   double spread=(tick.ask-tick.bid)/_Point;
   if(g_cfg_max_spread_points>0.0 && spread>g_cfg_max_spread_points) return;

   int buys=0,sells=0;
   int positions=CountOurPositions(buys,sells);
   int pending=CountOurPendingOrders();
   if(g_cfg_max_positions>0 && positions+pending>=g_cfg_max_positions) return;
   if(g_cfg_one_direction && buys>0 && sells>0) return;

   int chase_buy=0,chase_sell=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=ALZOOL_MAGIC) continue;
      string comment=PositionGetString(POSITION_COMMENT);
      if(StringFind(comment,"AZC_CHASE_BUY")>=0) chase_buy++;
      if(StringFind(comment,"AZC_CHASE_SELL")>=0) chase_sell++;
   }
   for(int j=OrdersTotal()-1;j>=0;j--)
   {
      ulong ticket=OrderGetTicket(j);
      if(ticket==0) continue;
      if(OrderGetString(ORDER_SYMBOL)!=_Symbol || (ulong)OrderGetInteger(ORDER_MAGIC)!=ALZOOL_MAGIC) continue;
      string comment=OrderGetString(ORDER_COMMENT);
      if(StringFind(comment,"AZC_CHASE_BUY")>=0) chase_buy++;
      if(StringFind(comment,"AZC_CHASE_SELL")>=0) chase_sell++;
   }

   long stops_level=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double min_distance=(double)stops_level*_Point;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double total_before=(double)(positions+pending);
   double single_target=(total_before==0.0 ? g_cfg_candle_chase_single_target : 0.0);
   datetime expiration=candle[0].time+PeriodSeconds(_Period);
   if(expiration<=TimeCurrent()) expiration=0;

   bool allow_buy=g_cfg_allow_buy && !(g_cfg_one_direction && sells>0);
   bool allow_sell=g_cfg_allow_sell && !(g_cfg_one_direction && buys>0);
   if(allow_buy && chase_buy<g_cfg_candle_chase_max_orders &&
      (g_last_chase_buy_ms==0 || GetTickCount64()-g_last_chase_buy_ms>=(ulong)g_cfg_candle_chase_cooldown*1000))
   {
      double price=NormalizeDouble(MathMax(candle[0].high+g_cfg_candle_chase_min_move,tick.ask+min_distance),digits);
      double sl=(g_cfg_candle_chase_sl_enabled ? NormalizeDouble(price-g_cfg_candle_chase_sl_distance,digits) : 0.0);
      double target=(single_target>0.0 ? single_target : g_cfg_candle_chase_buy_target);
      double tp=(target>0.0 ? NormalizeDouble(price+target,digits) : 0.0);
      double lots=(g_cfg_ai_risk_enabled
                   ? (g_cfg_candle_chase_sl_enabled ? CalculateRiskLots(g_cfg_candle_chase_sl_distance) : 0.0)
                   : NormalizeLots(g_cfg_lot));
      bool stops_ok=(!g_cfg_require_broker_stops ||
                     ((!g_cfg_candle_chase_sl_enabled || price-sl>=min_distance) &&
                      (tp==0.0 || tp-price>=min_distance)));
      if(lots>0.0 && stops_ok)
      {
         trade.SetExpertMagicNumber(ALZOOL_MAGIC);
         bool sent=(expiration>0
            ? trade.BuyStop(lots,price,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,expiration,"AZC_CHASE_BUY")
            : trade.BuyStop(lots,price,_Symbol,sl,tp,ORDER_TIME_GTC,0,"AZC_CHASE_BUY"));
         uint rc=trade.ResultRetcode();
         if(!sent && expiration>0 && rc==TRADE_RETCODE_INVALID_EXPIRATION)
         {
            sent=trade.BuyStop(lots,price,_Symbol,sl,tp,ORDER_TIME_GTC,0,"AZC_CHASE_BUY");
            rc=trade.ResultRetcode();
         }
         if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED))
         {
            g_last_chase_buy_ms=GetTickCount64();
            string id="AZC_BUY_"+IntegerToString((long)candle[0].time)+"_"+IntegerToString((long)trade.ResultOrder());
            string payload="{\"decision_id\":\""+id+"\",\"side\":\"BUY\",\"symbol\":\""+JsonSafe(_Symbol)+
               "\",\"order_id\":\""+(string)trade.ResultOrder()+"\",\"volume\":"+DoubleToString(lots,2)+
               ",\"price\":"+DoubleToString(price,digits)+",\"tp\":"+DoubleToString(tp,digits)+
               ",\"reason_ar\":\"أمر متابعة شمعة شراء من إعدادات لوحة التحكم\"}";
            SendSignalEvent(id,"ORDER_SENT",payload);
         }
         else Print("AL-ZOOL candle chase BUY rejected: ",trade.ResultRetcodeDescription());
      }
      else if(lots<=0.0 && g_cfg_ai_risk_enabled)
         g_status="Candle chase BUY waiting / AI risk requires SL and valid broker volume";
   }

   // Recount after a possible buy placement so the dashboard maximum is shared.
   pending=CountOurPendingOrders();
   if(g_cfg_max_positions>0 && positions+pending>=g_cfg_max_positions) return;
   if(allow_sell && chase_sell<g_cfg_candle_chase_max_orders &&
      (g_last_chase_sell_ms==0 || GetTickCount64()-g_last_chase_sell_ms>=(ulong)g_cfg_candle_chase_cooldown*1000))
   {
      double price=NormalizeDouble(MathMin(candle[0].low-g_cfg_candle_chase_min_move,tick.bid-min_distance),digits);
      double sl=(g_cfg_candle_chase_sl_enabled ? NormalizeDouble(price+g_cfg_candle_chase_sl_distance,digits) : 0.0);
      double target=(single_target>0.0 ? single_target : g_cfg_candle_chase_sell_target);
      double tp=(target>0.0 ? NormalizeDouble(price-target,digits) : 0.0);
      double lots=(g_cfg_ai_risk_enabled
                   ? (g_cfg_candle_chase_sl_enabled ? CalculateRiskLots(g_cfg_candle_chase_sl_distance) : 0.0)
                   : NormalizeLots(g_cfg_lot));
      bool stops_ok=(!g_cfg_require_broker_stops ||
                     ((!g_cfg_candle_chase_sl_enabled || sl-price>=min_distance) &&
                      (tp==0.0 || price-tp>=min_distance)));
      if(lots>0.0 && stops_ok)
      {
         trade.SetExpertMagicNumber(ALZOOL_MAGIC);
         bool sent=(expiration>0
            ? trade.SellStop(lots,price,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,expiration,"AZC_CHASE_SELL")
            : trade.SellStop(lots,price,_Symbol,sl,tp,ORDER_TIME_GTC,0,"AZC_CHASE_SELL"));
         uint rc=trade.ResultRetcode();
         if(!sent && expiration>0 && rc==TRADE_RETCODE_INVALID_EXPIRATION)
         {
            sent=trade.SellStop(lots,price,_Symbol,sl,tp,ORDER_TIME_GTC,0,"AZC_CHASE_SELL");
            rc=trade.ResultRetcode();
         }
         if(sent && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED))
         {
            g_last_chase_sell_ms=GetTickCount64();
            string id="AZC_SELL_"+IntegerToString((long)candle[0].time)+"_"+IntegerToString((long)trade.ResultOrder());
            string payload="{\"decision_id\":\""+id+"\",\"side\":\"SELL\",\"symbol\":\""+JsonSafe(_Symbol)+
               "\",\"order_id\":\""+(string)trade.ResultOrder()+"\",\"volume\":"+DoubleToString(lots,2)+
               ",\"price\":"+DoubleToString(price,digits)+",\"tp\":"+DoubleToString(tp,digits)+
               ",\"reason_ar\":\"أمر متابعة شمعة بيع من إعدادات لوحة التحكم\"}";
            SendSignalEvent(id,"ORDER_SENT",payload);
         }
         else Print("AL-ZOOL candle chase SELL rejected: ",trade.ResultRetcodeDescription());
      }
      else if(lots<=0.0 && g_cfg_ai_risk_enabled)
         g_status="Candle chase SELL waiting / AI risk requires SL and valid broker volume";
   }
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
   double sl=JsonNumber(response,"sl",0.0);
   double tp=JsonNumber(response,"tp",0.0);
    double sl=JsonNumber(response,"sl",0.0);
    double tp=JsonNumber(response,"tp",0.0);
   double price=JsonNumber(response,"price",0.0);
   double expires=JsonNumber(response,"expires_at",0.0);
   if(signed_payload=="" || supplied=="" || client_id!=InpEngineId ||
      command_id=="" || expires<=0.0 || (double)TimeGMT()>=expires) return false;
   if(action!="WAIT" && action!="CLOSE_ALL" && action!="CLOSE_POSITION" &&
      action!="MODIFY_SL" && action!="OPEN_MARKET" && action!="PLACE_PENDING") return false;
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
            double tp=PositionGetDouble(POSITION_TP);
            bool improves=(side==POSITION_TYPE_BUY ? sl>old_sl+_Point :
                           (old_sl<=0.0 || sl<old_sl-_Point));
            if(improves && ((side==POSITION_TYPE_BUY && sl<SymbolInfoDouble(_Symbol,SYMBOL_BID)) ||
                            (side==POSITION_TYPE_SELL && sl>SymbolInfoDouble(_Symbol,SYMBOL_ASK))))
               executed=trade.PositionModify(position_ticket,NormalizeDouble(sl,_Digits),tp);
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

   PollCentralSettings();
   FlushPendingOutcomes();
   SendTerminalState();
   CheckCentralHealth();
   CheckTradingControl();
   PollCentralManagement();
   PollCentralEngine();
   ManageAiLossGuard();
   ManageConfirmedReversal();
   ManageCandleChase();
   Comment("AL-ZOOL CENTRAL CLIENT\n",
           "Status: ",g_status,"\n",
           "Server: ",(g_server_online ? "ONLINE" : "OFFLINE / NOT CONFIRMED"),"\n",
           "Settings: ",(g_settings_loaded ? "LOADED FROM DASHBOARD" : "WAITING / INVALID"),"\n",
           "Trading control: ",(g_trading_enabled ? "ENABLED" : "DISABLED"),"\n",
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
