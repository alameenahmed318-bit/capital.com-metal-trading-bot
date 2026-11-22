// AL-ZOOL read-only archive uploader. No trading functions.
// Run once from a separate chart of the matching market. The normal EA can
// remain installed. Use that market's owner connection ID and pairing key.
#property strict
#property script_show_inputs
#property version "1.00"

input string InpOwnerConnectionId="";
input string InpOwnerPairingKey="";
input int InpHistoricalBars=50000;
input int InpHttpTimeoutMs=20000;

string EscapeJson(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r"," ");
   StringReplace(s,"\n"," ");
   return s;
}
string Num(double value,int digits)
{
   return DoubleToString(value,digits);
}

void OnStart()
{
   string symbol=_Symbol;
   string upper=symbol; StringToUpper(upper);
   string lane="";
   if(StringFind(upper,"XAUUSD")==0) lane="XAUUSD";
   else if(StringFind(upper,"BTCUSD")==0) lane="BTCUSD";
   else {Print("AL-ZOOL ARCHIVE: only XAUUSD/BTCUSD charts are supported."); return;}
   if(StringLen(InpOwnerConnectionId)<8 || StringLen(InpOwnerPairingKey)<32)
   {Print("AL-ZOOL ARCHIVE: enter your market's existing owner pairing fields."); return;}
   for(int i=0;i<StringLen(InpOwnerConnectionId);i++)
   {
      ushort ch=StringGetCharacter(InpOwnerConnectionId,i);
      if(!((ch>=48 && ch<=57)||(ch>=65 && ch<=90)||(ch>=97 && ch<=122)||ch==45||ch==95))
      {Print("AL-ZOOL ARCHIVE: invalid connection ID.");return;}
   }
   if(StringFind(InpOwnerPairingKey,"\r")>=0 || StringFind(InpOwnerPairingKey,"\n")>=0)
   {Print("AL-ZOOL ARCHIVE: invalid pairing key.");return;}
   int wanted=(int)MathMax(500,MathMin(100000,InpHistoricalBars));
   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied=-1;
   // Load a fixed snapshot of closed M1 bars. Never shift indices as time passes.
   for(int attempt=0;attempt<8 && !IsStopped();attempt++)
   {
      copied=CopyRates(symbol,PERIOD_M1,1,wanted,rates);
      if(copied>=wanted) break;
      Sleep(1500);
   }
   if(copied<20 || IsStopped())
   {Print("AL-ZOOL ARCHIVE: broker history unavailable. Available bars: ",copied);return;}
   string url="https://al-zool-meeno-engine.onrender.com/market/"+lane+"/v1/terminal-history/batch/"+InpOwnerConnectionId;
   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   string prefix="{\"symbol\":\""+EscapeJson(symbol)+"\",\"timeframe\":\"PERIOD_M1\",\"metadata\":{\"digits\":"+IntegerToString(digits);
   prefix+=",\"point_size\":"+Num(SymbolInfoDouble(symbol,SYMBOL_POINT),10);
   prefix+=",\"tick_size\":"+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE),10);
   prefix+=",\"tick_value\":"+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE),10);
   prefix+=",\"contract_size\":"+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE),8);
   prefix+=",\"account_currency\":\""+EscapeJson(AccountInfoString(ACCOUNT_CURRENCY))+"\"},\"candles\":[";
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+InpOwnerPairingKey+"\r\n";
   int uploaded=0;
   for(int start=0;start<copied && !IsStopped();start+=500)
   {
      int end=(int)MathMin(copied,start+500);
      string body=prefix;
      for(int i=start;i<end;i++)
      {
         if(i>start)body+=",";
         body+="{\"time\":"+IntegerToString((long)rates[i].time);
         body+=",\"open\":"+Num(rates[i].open,digits);
         body+=",\"high\":"+Num(rates[i].high,digits);
         body+=",\"low\":"+Num(rates[i].low,digits);
         body+=",\"close\":"+Num(rates[i].close,digits);
         body+=",\"tick_volume\":"+IntegerToString((long)rates[i].tick_volume);
         body+=",\"spread_points\":"+IntegerToString((long)rates[i].spread)+"}";
      }
      body+="]}";
      char data[],result[];
      StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
      if(ArraySize(data)>0)ArrayResize(data,ArraySize(data)-1);
      int code=-1;
      string responseHeaders;
      for(int retry=0;retry<3 && !IsStopped();retry++)
      {
         ResetLastError();
         code=WebRequest("POST",url,headers,InpHttpTimeoutMs,data,result,responseHeaders);
         if(code==200)break;
         if(code==400||code==401||code==403||code==409)break;
         Sleep(2000);
      }
      if(code!=200)
      {Print("AL-ZOOL ARCHIVE: upload stopped, HTTP=",code," err=",GetLastError(),"; acknowledged=",uploaded);return;}
      uploaded+=end-start;
      Print("AL-ZOOL ARCHIVE: ",symbol," acknowledged ",uploaded," / ",copied," closed bars.");
      Sleep(250);
   }
   Print("AL-ZOOL ARCHIVE: finished; acknowledged ",uploaded," of ",copied," available MT5 bars. Requested ",wanted,". No orders sent.");
}
