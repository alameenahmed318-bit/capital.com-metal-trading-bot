//+------------------------------------------------------------------+
//|                                               AL_ZOOL_GOLD.mq5   |
//|                         AL-ZOOL | AZ                             |
//+------------------------------------------------------------------+
#property copyright "AL-ZOOL | AZ"
#property link      "https://github.com/alameenahmed318-bit"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
CTrade trade;

//--- Risk & Lot Management: original values preserved
input group "--- Risk & Lot Management ---"
input double StartLot             = 0.01;
input double LotMultiplier        = 1.5;
input bool   CapMartingaleLot     = true;
input double MaxLotCap            = 5.0;

//--- Grid Settings: original values preserved
input group "--- Grid Settings ---"
input int  GridDistancePoints     = 1400;
input int  MaxGridDepth           = 14;
input bool VolatilityTierActive   = true;
input int  StepQuiet              = 1200;
input int  StepVolatile           = 1750;

//--- Alerts
input group "--- Alerts ---"
input bool   EnableVisualAlerts   = true;
input bool   EnableSoundAlerts    = true;
input string AlertSoundFile       = "alert.wav";

//--- Trade identification
input group "--- Trade Identification ---"
input ulong MagicNumber           = 101101;

//--- Dashboard
input group "--- AL-ZOOL Dashboard ---"
input bool ShowAZDashboard        = true;

datetime lastBarTime = 0;
string PANEL_PREFIX = "AZ_ZOL_";

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   if(ShowAZDashboard)
      CreateDashboard();

   UpdateDashboard();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinitialization                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, PANEL_PREFIX);
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Count managed positions                                          |
//+------------------------------------------------------------------+
int CountPositions(const int typeFilter = -1)
{
   int count = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      int type = (int)PositionGetInteger(POSITION_TYPE);
      if(typeFilter == -1 || type == typeFilter)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Find latest position on a direction                              |
//+------------------------------------------------------------------+
bool GetLatestSidePosition(const ENUM_POSITION_TYPE side,
                           double &openPrice,
                           double &latestVolume,
                           datetime &latestTime,
                           int &sideCount)
{
   bool found = false;
   openPrice = 0.0;
   latestVolume = 0.0;
   latestTime = 0;
   sideCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      ENUM_POSITION_TYPE type =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      if(type != side)
         continue;

      sideCount++;
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

      if(!found || opened >= latestTime)
      {
         found = true;
         latestTime = opened;
         openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         latestVolume = PositionGetDouble(POSITION_VOLUME);
      }
   }
   return found;
}

//+------------------------------------------------------------------+
//| Normalize volume to broker rules                                 |
//+------------------------------------------------------------------+
double NormalizeLot(double requestedLot)
{
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      return 0.0;

   double allowedMax = maxLot;
   if(CapMartingaleLot)
      allowedMax = MathMin(allowedMax, MaxLotCap);

   requestedLot = MathMin(requestedLot, allowedMax);
   if(requestedLot < minLot)
      return 0.0;

   double lot = MathFloor(requestedLot / step + 1e-8) * step;
   if(lot < minLot)
      return 0.0;

   int digits = 0;
   double scaledStep = step;
   while(digits < 8 && MathAbs(scaledStep - MathRound(scaledStep)) > 1e-8)
   {
      scaledStep *= 10.0;
      digits++;
   }
   return NormalizeDouble(MathMin(lot, allowedMax), digits);
}

//+------------------------------------------------------------------+
//| Grid step based on recent candle ranges                          |
//+------------------------------------------------------------------+
int GetGridStepPoints()
{
   if(!VolatilityTierActive)
      return GridDistancePoints;

   double range1 = iHigh(_Symbol, _Period, 1) - iLow(_Symbol, _Period, 1);
   double range2 = iHigh(_Symbol, _Period, 2) - iLow(_Symbol, _Period, 2);

   if(range1 <= 0.0 || range2 <= 0.0)
      return GridDistancePoints;

   if(range1 < range2)
      return StepQuiet;

   if(range1 > range2)
      return StepVolatile;

   return GridDistancePoints;
}

//+------------------------------------------------------------------+
//| Alerts and order result                                          |
//+------------------------------------------------------------------+
void ReportTradeResult(const bool success, const string side)
{
   if(success)
   {
      string message = "AL-ZOOL AZ: " + side + " accepted on " + _Symbol;
      if(EnableVisualAlerts)
         Alert(message);
      if(EnableSoundAlerts)
         PlaySound(AlertSoundFile);
   }
   else
   {
      Print("AL-ZOOL order failed: ", side,
            " | Retcode=", trade.ResultRetcode(),
            " | ", trade.ResultRetcodeDescription());
   }
}

bool OpenBuy(double lot, const string comment)
{
   lot = NormalizeLot(lot);
   if(lot <= 0.0)
      return false;

   bool ok = trade.Buy(lot, _Symbol, 0.0, 0.0, 0.0, comment);
   ReportTradeResult(ok, "BUY");
   return ok;
}

bool OpenSell(double lot, const string comment)
{
   lot = NormalizeLot(lot);
   if(lot <= 0.0)
      return false;

   bool ok = trade.Sell(lot, _Symbol, 0.0, 0.0, 0.0, comment);
   ReportTradeResult(ok, "SELL");
   return ok;
}

//+------------------------------------------------------------------+
//| Start hedge                                                      |
//+------------------------------------------------------------------+
void StartHedge()
{
   ENUM_ACCOUNT_MARGIN_MODE mode =
      (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);

   if(mode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("AL-ZOOL: This strategy requires a hedging MT5 account.");
      return;
   }

   double lot = NormalizeLot(StartLot);
   if(lot <= 0.0)
   {
      Print("AL-ZOOL: StartLot is below broker minimum volume.");
      return;
   }

   if(!OpenBuy(lot, "AL-ZOOL AZ Hedge Buy"))
      return;

   OpenSell(lot, "AL-ZOOL AZ Hedge Sell");
}

//+------------------------------------------------------------------+
//| Grid management                                                  |
//+------------------------------------------------------------------+
void ManageGridSide(const ENUM_POSITION_TYPE side)
{
   double lastPrice, lastLot;
   datetime lastTime;
   int count;

   if(!GetLatestSidePosition(side, lastPrice, lastLot, lastTime, count))
      return;

   // MaxGridDepth is the maximum number of added positions per side.
   if(count >= MaxGridDepth + 1)
      return;

   int stepPoints = GetGridStepPoints();
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(stepPoints <= 0 || point <= 0.0)
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool adverseMove = false;

   if(side == POSITION_TYPE_BUY)
      adverseMove = (lastPrice - bid) >= stepPoints * point;
   else
      adverseMove = (ask - lastPrice) >= stepPoints * point;

   if(!adverseMove)
      return;

   double nextLot = StartLot * MathPow(LotMultiplier, count);
   if(CapMartingaleLot)
      nextLot = MathMin(nextLot, MaxLotCap);

   if(side == POSITION_TYPE_BUY)
      OpenBuy(nextLot, "AL-ZOOL AZ Grid Buy");
   else
      OpenSell(nextLot, "AL-ZOOL AZ Grid Sell");
}

//+------------------------------------------------------------------+
//| Main tick                                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   UpdateDashboard();

   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime == 0 || currentBarTime == lastBarTime)
      return;

   lastBarTime = currentBarTime;

   if(CountPositions() == 0)
   {
      StartHedge();
      return;
   }

   ManageGridSide(POSITION_TYPE_BUY);
   ManageGridSide(POSITION_TYPE_SELL);
}

//+------------------------------------------------------------------+
//| Sum closed deal P/L for symbol and magic number                  |
//+------------------------------------------------------------------+
double ClosedProfit(datetime fromTime, datetime toTime)
{
   if(!HistorySelect(fromTime, toTime))
      return 0.0;

   double profit = 0.0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber)
         continue;

      ENUM_DEAL_ENTRY entry =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);

      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY ||
         entry == DEAL_ENTRY_INOUT)
      {
         profit += HistoryDealGetDouble(deal, DEAL_PROFIT);
         profit += HistoryDealGetDouble(deal, DEAL_SWAP);
         profit += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      }
   }
   return profit;
}

//+------------------------------------------------------------------+
//| Dashboard helper                                                 |
//+------------------------------------------------------------------+
void CreateLabel(string name, int x, int y, int fontSize,
                 color textColor, string caption, string font = "Arial")
{
   string id = PANEL_PREFIX + name;
   if(ObjectFind(0, id) < 0)
      ObjectCreate(0, id, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, id, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, id, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, id, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, id, OBJPROP_FONTSIZE, fontSize);
   ObjectSetInteger(0, id, OBJPROP_COLOR, textColor);
   ObjectSetString(0, id, OBJPROP_FONT, font);
   ObjectSetString(0, id, OBJPROP_TEXT, caption);
   ObjectSetInteger(0, id, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, id, OBJPROP_HIDDEN, true);
}

void CreateDashboard()
{
   string bg = PANEL_PREFIX + "BG";
   if(ObjectFind(0, bg) < 0)
      ObjectCreate(0, bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);

   ObjectSetInteger(0, bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, bg, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, bg, OBJPROP_YDISTANCE, 18);
   ObjectSetInteger(0, bg, OBJPROP_XSIZE, 300);
   ObjectSetInteger(0, bg, OBJPROP_YSIZE, 220);
   ObjectSetInteger(0, bg, OBJPROP_BGCOLOR, C'20,35,30');
   ObjectSetInteger(0, bg, OBJPROP_COLOR, clrGoldenrod);
   ObjectSetInteger(0, bg, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, bg, OBJPROP_BACK, false);
   ObjectSetInteger(0, bg, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, bg, OBJPROP_HIDDEN, true);

   CreateLabel("LOGO",      24,  26, 17, clrGold,  "AZ");
   CreateLabel("BRAND",     76,  28, 12, clrWhite, "AL-ZOOL GOLD");
   CreateLabel("LINE",      24,  52,  9, clrGold,  "--------------------------------");
   CreateLabel("BALANCE",   24,  65, 10, clrWhite, "Balance:");
   CreateLabel("EQUITY",    24,  86, 10, clrWhite, "Equity:");
   CreateLabel("DAY",       24, 107, 10, clrWhite, "Today closed:");
   CreateLabel("DAYPCT",    24, 128, 10, clrWhite, "Today return:");
   CreateLabel("MONTH",     24, 149, 10, clrWhite, "Month closed:");
   CreateLabel("MONTHPCT",  24, 170, 10, clrWhite, "Month return:");
   CreateLabel("POSITIONS", 24, 191, 10, clrWhite, "Positions:");
   CreateLabel("STATUS",    24, 213,  9, clrGold,  "AL-ZOOL | AZ");
}

//+------------------------------------------------------------------+
//| Dashboard update                                                 |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   if(!ShowAZDashboard)
      return;

   if(ObjectFind(0, PANEL_PREFIX + "BG") < 0)
      CreateDashboard();

   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);

   MqlDateTime dayStart = dt;
   dayStart.hour = 0;
   dayStart.min = 0;
   dayStart.sec = 0;
   datetime dayFrom = StructToTime(dayStart);

   MqlDateTime monthStart = dt;
   monthStart.day = 1;
   monthStart.hour = 0;
   monthStart.min = 0;
   monthStart.sec = 0;
   datetime monthFrom = StructToTime(monthStart);

   double dayProfit = ClosedProfit(dayFrom, now);
   double monthProfit = ClosedProfit(monthFrom, now);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   // Approximate period-start balance from current balance and closed P/L.
   // Deposits/withdrawals during the period can affect this percentage.
   double dayBase = balance - dayProfit;
   double monthBase = balance - monthProfit;
   double dayPct = (dayBase > 0.0) ? (dayProfit / dayBase) * 100.0 : 0.0;
   double monthPct = (monthBase > 0.0) ? (monthProfit / monthBase) * 100.0 : 0.0;

   string currency = AccountInfoString(ACCOUNT_CURRENCY);

   ObjectSetString(0, PANEL_PREFIX + "BALANCE", OBJPROP_TEXT,
      "Balance: " + DoubleToString(balance, 2) + " " + currency);
   ObjectSetString(0, PANEL_PREFIX + "EQUITY", OBJPROP_TEXT,
      "Equity:  " + DoubleToString(equity, 2) + " " + currency);
   ObjectSetString(0, PANEL_PREFIX + "DAY", OBJPROP_TEXT,
      "Today closed: " + DoubleToString(dayProfit, 2) + " " + currency);
   ObjectSetString(0, PANEL_PREFIX + "DAYPCT", OBJPROP_TEXT,
      "Today return: " + DoubleToString(dayPct, 2) + "%");
   ObjectSetString(0, PANEL_PREFIX + "MONTH", OBJPROP_TEXT,
      "Month closed: " + DoubleToString(monthProfit, 2) + " " + currency);
   ObjectSetString(0, PANEL_PREFIX + "MONTHPCT", OBJPROP_TEXT,
      "Month return: " + DoubleToString(monthPct, 2) + "%");
   ObjectSetString(0, PANEL_PREFIX + "POSITIONS", OBJPROP_TEXT,
      "Positions: " + IntegerToString(CountPositions()));
   ObjectSetString(0, PANEL_PREFIX + "STATUS", OBJPROP_TEXT,
      "Symbol: " + _Symbol + " | AZ");

   ObjectSetInteger(0, PANEL_PREFIX + "DAY", OBJPROP_COLOR,
      dayProfit >= 0.0 ? clrLimeGreen : clrTomato);
   ObjectSetInteger(0, PANEL_PREFIX + "DAYPCT", OBJPROP_COLOR,
      dayPct >= 0.0 ? clrLimeGreen : clrTomato);
   ObjectSetInteger(0, PANEL_PREFIX + "MONTH", OBJPROP_COLOR,
      monthProfit >= 0.0 ? clrLimeGreen : clrTomato);
   ObjectSetInteger(0, PANEL_PREFIX + "MONTHPCT", OBJPROP_COLOR,
      monthPct >= 0.0 ? clrLimeGreen : clrTomato);

   ChartRedraw();
}
//+------------------------------------------------------------------+
