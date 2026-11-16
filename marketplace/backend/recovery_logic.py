"""Account-wide recovery intent for AL-ZOOL.

Pure diagnostics only. MT5 executes only after the server's one-use action claim.
Owner threshold is account-wide; direction is chosen independently from new-entry
permission, among existing *losing* gold sides. Model vote is a tie-break, not a veto.
"""
import math


def _num(value, default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (ValueError, TypeError, OverflowError):
        return default


def recovery_intent(snapshot, decision, settings):
    positions=[p for p in (snapshot.get("positions") or []) if isinstance(p,dict)]
    pm=(settings or {}).get("position_management") or {}
    enabled=pm.get("recovery_enabled") is True
    trigger=_num(pm.get("recovery_loss_trigger"))
    net=sum(_num(p.get("profit"))+_num(p.get("swap"))+_num(p.get("commission"))
            for p in positions)
    lots=sum(max(0,_num(p.get("volume"))) for p in positions)
    result={"armed":False,"action":"HOLD","side":None,"lot_cap":0.0,
            "account_net":round(net,2),"account_lots":round(lots,3),
            "profit_target":_num(pm.get("recovery_profit_target")),
            "reason_ar":"التعافي غير مفعّل أو حد الخسارة غير محدد"}
    if not enabled or trigger<=0:return result
    if not positions:
        result["reason_ar"]="لا توجد صفقات مفتوحة في الحساب";return result
    if net>-trigger:
        result["reason_ar"]=f"صافي الحساب {net:.2f} لم يبلغ خسارة {trigger:.2f}"
        return result
    result["armed"]=True
    if any(str(p.get("comment") or "").startswith("ALZOOL-RECOVERY") for p in positions):
        result["reason_ar"]="صفقة تعافٍ مفتوحة بالفعل؛ لا نكرر صفقة ×3"
        return result
    sides={}
    symbol=str(snapshot.get("symbol") or "")
    for p in positions:
        if str(p.get("symbol") or "")!=symbol or not symbol.upper().startswith("XAUUSD"):
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
        result["reason_ar"]="حد خسارة الحساب تحقق، لكن لا توجد صفقات ذهب خاسرة لتحديد اتجاه تعافٍ مطابق"
        return result
    strategy=str((decision or {}).get("decision","WAIT")).upper()
    # Largest losing side is primary; strategy vote breaks equal-loss cases.
    selected=sorted(eligible,key=lambda side:(-abs(sides[side]["loss"]),
                    0 if side==strategy else 1,side))[0]
    size=round(3.0*lots,3)
    if size<=0:
        result["reason_ar"]="لا توجد لوتات صالحة لحساب مضاعف التعافي"
        return result
    result.update({"action":"RECOVERY_PROPOSAL","side":selected,"lot_cap":size,
        "reason_ar":(f"صافي الحساب {net:.2f} وصل إلى حد الخسارة {trigger:.2f}؛ "
                     f"اتجاه التعافي {selected} هو اتجاه مجموعة الذهب الأكثر خسارة؛ "
                     f"حجم التعافي 3 × مجموع لوتات الحساب {lots:.3f} = {size:.3f}؛ "
                     f"إشارة الدخول الحالية {strategy} للاستئناس وليست شرطاً مانعاً")})
    return result
