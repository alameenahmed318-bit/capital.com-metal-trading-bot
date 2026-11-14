"""Central position-management planner for AL-ZOOL.

Returns non-executable proposals only. MT5 must require a separate, short-lived,
action-specific authorization before placing add-on, cooling or recovery orders.
This prevents legacy local strategies from bypassing the model council.
"""
import math

def num(value,default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (ValueError,TypeError,OverflowError):return default

def plan_account_actions(snapshot,decision,council,risk,settings):
    all_positions=snapshot.get("positions") or []
    symbol=str(snapshot.get("symbol") or "")
    positions=[p for p in all_positions if isinstance(p,dict) and
        str(p.get("symbol") or "")==symbol and symbol.upper().startswith("XAUUSD")]
    pm=(settings or {}).get("position_management") or {}
    atr=num(decision.get("entry_atr") or (decision.get("context") or {}).get("atr"))
    tick=snapshot.get("tick") or {}
    signal=str(decision.get("decision","WAIT")).upper()
    result={"mode":"DIAGNOSTIC_ONLY","execution_authorized":False,
            "bar_time":int(num(decision.get("bar_time"))),"buy":"HOLD","sell":"HOLD",
            "actions":[],"reason_ar":"لا يوجد قرار إدارة صالح"}
    if not positions:
        result["reason_ar"]="لا توجد صفقات مفتوحة للحساب";return result
    if not (council.get("approved") and risk.get("approved") and settings.get("trading_enabled")):
        result["reason_ar"]="المجلس أو حارس المخاطر أو مفتاح التداول منع إجراءات جديدة";return result
    if atr<=0 or signal not in ("BUY","SELL"):
        result["reason_ar"]="انتظار ATR واتجاه صالح من المحرك المركزي";return result
    sides={}
    for position in positions:
        if not isinstance(position,dict):continue
        side="BUY" if int(num(position.get("type"),-1))==0 else "SELL" if int(num(position.get("type"),-1))==1 else None
        if not side:continue
        sides.setdefault(side,[]).append(position)
    if len(sides)>1:
        result["reason_ar"]="صفقات BUY وSELL متعاكسة موجودة؛ لا تعزيز ولا تبريد";return result
    if not sides:
        result["reason_ar"]="لا توجد مراكز صالحة للتحليل";return result
    max_lots=max(0,num(pm.get("max_total_lots",0)))
    total_lots=sum(max(0,num(p.get("volume"))) for p in all_positions if isinstance(p,dict))
    confidence=max(0,min(1,num(decision.get("confidence"),.5)))
    atr_ratio=max(.5,min(2.5,num((decision.get("context") or {}).get("atr_ratio"),1)))
    addon_band=max(.35,min(1.2,.75-(confidence-.5)*.4+(atr_ratio-1)*.18))
    cooling_band=max(.5,min(1.35,.75+(atr_ratio-1)*.22))
    recovery_band=max(1.0,min(2.4,1.4+(atr_ratio-1)*.3))
    result["adaptive_atr_bands"]={"addon":round(addon_band,3),
        "cooling":round(cooling_band,3),"recovery":round(recovery_band,3)}
    for side,group in sides.items():
        if signal!=side:
            result["reason_ar"]="اتجاه المجلس عكس المراكز المفتوحة؛ لا إضافة تعرض";continue
        price=num(tick.get("bid") if side=="BUY" else tick.get("ask"))
        volume=sum(max(0,num(p.get("volume"))) for p in group)
        if volume<=0 or price<=0:continue
        average=sum(num(p.get("open_price"))*max(0,num(p.get("volume"))) for p in group)/volume
        net=sum(num(p.get("profit"))+num(p.get("swap")) for p in group)
        progress=(price-average)/atr*(1 if side=="BUY" else -1)
        action="HOLD";reason="حركة السعر لا تؤيد زيادة المخاطرة"
        candidate_lot=0.0
        existing_recovery=any(str(p.get("comment") or "").startswith("ALZOOL-RECOVERY") for p in group)
        if (progress<-recovery_band and pm.get("recovery_enabled") is True
            and num(pm.get("recovery_loss_trigger"))>0 and not existing_recovery
            and net<=-num(pm.get("recovery_loss_trigger"))):
            action="RECOVERY_PROPOSAL";reason="تعافٍ x3 مشروط بالمجلس وبقيود اللوت والحساب"
            ordinary=[num(p.get("volume")) for p in group if not
                str(p.get("comment") or "").startswith("ALZOOL-RECOVERY")]
            candidate_lot=3*max(ordinary) if ordinary else 0.0
        elif (progress<-cooling_band and pm.get("cooling_enabled") is True
              and len(group)<int(num(pm.get("cooling_max",0)))):
            action="COOLING_PROPOSAL";reason="تراجع سعري محسوب بالنسبة إلى ATR"
            candidate_lot=num(pm.get("cooling_lot"))
        elif (progress>addon_band and pm.get("addon_enabled") is True
              and len(group)<int(num(pm.get("addon_max",0)))):
            action="ADDON_PROPOSAL";reason="استمرار حركة مدعوم بمجلس المحرك"
            candidate_lot=num(pm.get("addon_lot"))
        cap=max(0,num(pm.get("addon_max_lot" if action=="ADDON_PROPOSAL" else "cooling_max_lot",0)))
        room=max(0,max_lots-total_lots)
        candidate_lot=min(max(0,candidate_lot),cap,room)
        if candidate_lot<=0 and action!="HOLD":
            action="HOLD";reason="حدود اللوت تمنع زيادة التعرض"
        result[side.lower()]=action
        result["actions"].append({"side":side,"action":action,"lot_cap":round(candidate_lot,3),
            "atr_distance":round(progress,3),"floating_net":round(net,2),
            "reason_ar":reason,"execution_authorized":False})
    result["reason_ar"]="اقتراحات مركزية فقط؛ التنفيذ يتطلب تصريح إجراء مستقل"
    return result
