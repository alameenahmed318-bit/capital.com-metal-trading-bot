from market_symbols import supported_market_symbol
"""Account-wide recovery intent for AL-ZOOL.

Pure diagnostics only. MT5 executes only after the server's one-use action claim.
Owner threshold is account-wide; direction is chosen independently from new-entry
permission, among existing *losing* gold sides. Model vote is a tie-break, not a veto.
"""
import math
from recovery_candle_guard import recovery_m1_confirmation


def _num(value, default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (ValueError, TypeError, OverflowError):
        return default


def recovery_intent(snapshot, decision, settings):
    all_positions=[p for p in (snapshot.get("positions") or []) if isinstance(p,dict)]
    symbol=str(snapshot.get("symbol") or "")
    positions=[p for p in all_positions if str(p.get("symbol") or "")==symbol]
    # Do not calculate an x3 lot or group P/L from another market.
    if not supported_market_symbol(symbol) or len(positions)!=len(all_positions):
        return {"armed":False,"active_cycle":False,"action":"HOLD",
                "side":None,"lot_cap":0.0,"account_net":0.0,"account_lots":0.0,
                "reason_ar":"فصل الأسواق: صفقات سوق آخر موجودة أو رمز غير مدعوم"}
    pm=(settings or {}).get("position_management") or {}
    enabled=pm.get("recovery_enabled") is True
    trigger=_num(pm.get("recovery_loss_trigger"))
    position_net=sum(_num(p.get("profit"))+_num(p.get("swap"))+_num(p.get("commission"))
                     for p in positions)
    # New EA sends the same account net it uses to close groups, including
    # entry commissions/deal fees. Legacy terminals safely fall back to P/L.
    net=_num(snapshot.get("recovery_group_net"),position_net)
    lots=sum(max(0,_num(p.get("volume"))) for p in positions)
    result={"armed":False,"active_cycle":False,"action":"HOLD","side":None,"lot_cap":0.0,
            "account_net":round(net,2),"account_lots":round(lots,3),
            "profit_target":_num(pm.get("recovery_profit_target")),
            "reason_ar":"التعافي غير مفعّل أو حد الخسارة غير محدد"}
    existing_recovery=(snapshot.get("recovery_cycle_active") is True and bool(positions)) or any(str(p.get("comment") or "").startswith("ALZOOL-RECOVERY") for p in positions)
    if existing_recovery:
        result["active_cycle"]=True
        result["reason_ar"]="صفقة التعافي مفتوحة؛ يمنع تكرار إجراءات إدارة حتى اكتمال إغلاق المجموعة"
        return result
    if not enabled or trigger<=0:return result
    if not positions:
        result["reason_ar"]="لا توجد صفقات مفتوحة في الحساب";return result
    if net>-trigger:
        result["reason_ar"]=f"صافي الحساب {net:.2f} لم يبلغ خسارة {trigger:.2f}"
        return result
    result["armed"]=True
    sides={}
    symbol=str(snapshot.get("symbol") or "")
    for p in positions:
        if str(p.get("symbol") or "")!=symbol or not supported_market_symbol(symbol):
            continue
        typ=int(_num(p.get("type"),-1))
        side="BUY" if typ==0 else "SELL" if typ==1 else None
        if not side:continue
        side_net=_num(p.get("profit"))+_num(p.get("swap"))+_num(p.get("commission"))
        values=sides.setdefault(side,{"loss":0.0,"losing_lots":0.0,"positions":0})
        values["loss"]+=min(0.0,side_net)
        if side_net<0:values["losing_lots"]+=max(0,_num(p.get("volume")))
        values["positions"]+=1
    eligible=[side for side,data in sides.items() if data["loss"]<0 and data["losing_lots"]>0]
    if not eligible:
        result["reason_ar"]="حد خسارة الحساب تحقق، لكن لا توجد صفقات خاسرة للرمز الحالي لتحديد اتجاه تعافٍ مطابق"
        return result
    size=round(3.0*lots,3)
    if size<=0:
        result["reason_ar"]="لا توجد لوتات صالحة لحساب مضاعف التعافي"
        return result
    # Reaching a currency loss limit ARMS a recovery, never OPENS it.
    # A full NEW M1 candle after arming must confirm a losing side.
    # Only a capability-advertising hedging EA can execute an opposite recovery.
    # Manual and legacy clients retain same-side behavior.
    free_direction=(snapshot.get("recovery_bidirectional_capable") is True and
                    str(pm.get("mode","AI")).upper()=="AI" and
                    (decision.get("master") or {}).get("mode")=="MASTER")
    confirmation=recovery_m1_confirmation(snapshot,decision,["BUY","SELL"] if free_direction else eligible)
    result["direction_mode"]="BIDIRECTIONAL_AI" if free_direction else "SAME_SIDE"
    result["losing_sides"]=eligible
    result["candle_confirmation"]=confirmation
    result["candidate_lot"]=size
    result["eligible_sides"]=["BUY","SELL"] if free_direction else eligible
    if not confirmation["approved"]:
        result["reason_ar"]=(f"التعافي مسلح عند صافي {net:.2f} / حد {trigger:.2f}؛ "
                             +confirmation["reason_ar"])
        return result
    selected=confirmation["side"]
    result.update({"action":"RECOVERY_PROPOSAL","side":selected,"lot_cap":size,
        "reason_ar":(f"صافي الحساب {net:.2f} وصل لحد الخسارة {trigger:.2f}؛ "
                     f"تأكيد شمعة M1 الجديدة: {confirmation['reason_ar']}؛ "
                     f"صفقة التعافي {selected} بلوت 3 × مجموع لوتات الحساب "
                     f"{lots:.3f} = {size:.3f}")})
    return result
