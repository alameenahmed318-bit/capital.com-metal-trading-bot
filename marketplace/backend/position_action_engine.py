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

def plan_account_actions(snapshot,decision,council,risk,settings,council_advisory=False):
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
    if str(pm.get("mode","AI")).upper() not in ("AI","MANUAL"):
        result["reason_ar"]="وضع إدارة الصفقات غير صالح";return result
    # Open-position management uses its own live trend decision; it must not
    # inherit the entry council's three-model veto.
    if not risk.get("approved") or not settings.get("trading_enabled"):
        result["reason_ar"]="حالة الحساب أو مفتاح التداول يمنع إجراء إدارة جديد";return result
    if atr<=0 or signal not in ("BUY","SELL"):
        result["reason_ar"]="انتظار ATR واتجاه صالح من المحرك المركزي";return result
    sides={}
    for position in positions:
        if not isinstance(position,dict):continue
        side="BUY" if int(num(position.get("type"),-1))==0 else "SELL" if int(num(position.get("type"),-1))==1 else None
        if not side:continue
        sides.setdefault(side,[]).append(position)
    if not sides:
        result["reason_ar"]="لا توجد مراكز صالحة للتحليل";return result
    max_lots=(num(pm["max_total_lots"],-1) if pm.get("max_total_lots") is not None else None)
    total_lots=sum(max(0,num(p.get("volume"))) for p in all_positions if isinstance(p,dict))
    confidence=max(0,min(1,num(decision.get("confidence"),.5)))
    atr_ratio=max(.5,min(2.5,num((decision.get("context") or {}).get("atr_ratio"),1)))
    addon_band=max(.35,min(1.2,.75-(confidence-.5)*.4+(atr_ratio-1)*.18))
    cooling_band=max(.5,min(1.35,.75+(atr_ratio-1)*.22))
    result["adaptive_atr_bands"]={"addon":round(addon_band,3),
        "cooling":round(cooling_band,3)}
    # Available Qwen/DeepSeek opinions influence management timing without
    # blocking the account while models are offline or slow.
    model_votes=(council or {}).get("votes") or {}
    advisory=[v for v in model_votes.values() if isinstance(v,dict)
              and v.get("status")=="READY" and v.get("model") in ("Qwen","DeepSeek")]

    for side,group in sides.items():
        if signal!=side:
            # Keep a visible HOLD for this side. A sell signal cannot be
            # reinterpreted as authorization to enlarge existing buys.
            result["actions"].append({"side":side,"action":"HOLD","lot_cap":0.0,
                "reason_ar":"اتجاه الاستراتيجيات الحالي لا يؤكد إضافة في اتجاه هذه الصفقات",
                "execution_authorized":False})
            continue
        price=num(tick.get("bid") if side=="BUY" else tick.get("ask"))
        volume=sum(max(0,num(p.get("volume"))) for p in group)
        if volume<=0 or price<=0:continue
        average=sum(num(p.get("open_price"))*max(0,num(p.get("volume"))) for p in group)/volume
        net=sum(num(p.get("profit"))+num(p.get("swap")) for p in group)
        progress=(price-average)/atr*(1 if side=="BUY" else -1)
        supports=sum(num(v.get("confidence")) for v in advisory if v.get("direction")==side)
        opposes=sum(num(v.get("confidence")) for v in advisory if v.get("direction") in ("BUY","SELL") and v.get("direction")!=side)
        # Models are evidence, not extra vetoes. Adjust an existing ATR
        # threshold only when a vote was actually received for this bar.
        advisory_bias=0.15*(supports-opposes)/max(1,len(advisory))
        advice_summary=f"Qwen/DeepSeek {len(advisory)} READY; bias={advisory_bias:.3f} ATR"
        action="HOLD";reason="حركة السعر لا تؤيد زيادة المخاطرة"
        candidate_lot=0.0
        existing_recovery=any(str(p.get("comment") or "").startswith("ALZOOL-RECOVERY") for p in group)
        # Owner loss threshold arms recovery; an additional ATR distance
        # must not silently override their chosen amount.
        if (pm.get("recovery_enabled") is True
            and num(pm.get("recovery_loss_trigger"))>0 and not existing_recovery
            and net<=-num(pm.get("recovery_loss_trigger"))):
            action="RECOVERY_PROPOSAL";reason="خسارة المجموعة بلغت حد المالك وتوافق اتجاه الاستراتيجيات؛ تعافٍ x3"
            ordinary=[num(p.get("volume")) for p in group if not
                str(p.get("comment") or "").startswith("ALZOOL-RECOVERY")]
            # Recovery applies to the SUM of same-symbol, same-side losing
            # non-recovery positions, never the largest individual position.
            # Do not accidentally include profitable/manual positions of other symbols.
            ordinary=[num(p.get("volume")) for p in group
                if num(p.get("profit"))+num(p.get("swap"))<0 and not
                str(p.get("comment") or "").startswith("ALZOOL-RECOVERY")]
            candidate_lot=3*sum(ordinary) if ordinary else 0.0
        elif (str(pm.get("mode","AI")).upper()=="AI" and progress<-(cooling_band+advisory_bias) and pm.get("cooling_enabled") is True
              and (num(pm.get("cooling_max"))<=0 or len(group)<int(num(pm.get("cooling_max"))))):
            action="COOLING_PROPOSAL";reason="تراجع سعري محسوب بالنسبة إلى ATR"
            candidate_lot=num(pm.get("cooling_lot"))
        elif (str(pm.get("mode","AI")).upper()=="AI" and progress>(addon_band-advisory_bias) and pm.get("addon_enabled") is True
              and (num(pm.get("addon_max"))<=0 or len(group)<int(num(pm.get("addon_max"))))):
            action="ADDON_PROPOSAL";reason="استمرار حركة مدعوم بمجلس المحرك"
            candidate_lot=num(pm.get("addon_lot"))
        # The owner-defined per-side position limit is independent from total
        # account exposure. Existing manual positions count toward both.
        side_cap=(int(num(pm["max_positions_per_direction"])) if pm.get("max_positions_per_direction") is not None else None)
        if action!="HOLD" and side_cap is not None and len(group)>=side_cap:
            action="HOLD";reason="عدد المراكز في الاتجاه بلغ الحد المحدد"
        # Manual mode uses fixed distances/lots while all actions still require
        # the same fresh council and risk authorization.
        if action=="HOLD" and str(pm.get("mode","AI")).upper()=="MANUAL" and (side_cap is None or len(group)<side_cap):
            adverse=max(0,-progress)*atr
            favorable=max(0,progress)*atr
            point=max(0,num((snapshot.get("metadata") or {}).get("point_size")))
            if point<=0:point=max(0,num(tick.get("point_size")))
            if point>0 and pm.get("cooling_enabled") is True and adverse/point>=num(pm.get("cooling_distance_points"),1)>0:
                action="COOLING_PROPOSAL";candidate_lot=num(pm.get("cooling_lot"))
                reason="مسافة التبريد اليدوية تحققت مع الموافقة المركزية"
            elif point>0 and pm.get("addon_enabled") is True and favorable/point>=num(pm.get("addon_distance_points"),1)>0:
                action="ADDON_PROPOSAL";candidate_lot=num(pm.get("addon_lot"))
                reason="مسافة التعزيز اليدوية تحققت مع الموافقة المركزية"
        if action=="ADDON_PROPOSAL" and str(pm.get("mode","AI")).upper()=="AI":
            # Incremental lots; never silently multiply a risk setting.
            base=max(0,num(pm.get("addon_lot",0.01)))
            candidate_lot=base*max(1,len(group))
        cap_field=("recovery_max_lot" if action=="RECOVERY_PROPOSAL" else
                   "addon_max_lot" if action=="ADDON_PROPOSAL" else "cooling_max_lot")
        cap=(num(pm[cap_field],-1) if pm.get(cap_field) is not None else None)
        room=(max_lots-total_lots if max_lots is not None else None)
        if action!="HOLD" and (candidate_lot<=0 or
            (cap is not None and candidate_lot>cap+1e-8) or
            (room is not None and candidate_lot>room+1e-8)):
            # Do not convert an exact x3 recovery into a smaller, falsely
            # labeled x3 order. Block and explain insufficient risk budget.
            action="HOLD";reason="حجم الإجراء المطلوب يتجاوز سقف اللوت أو التعرض"
        if action=="HOLD":candidate_lot=0.0
        result[side.lower()]=action
        result["actions"].append({"side":side,"action":action,"lot_cap":round(candidate_lot,3),
            "atr_distance":round(progress,3),"floating_net":round(net,2),
            "reason_ar":reason,"ai_advice":advice_summary,"execution_authorized":False})
    result["reason_ar"]="اقتراحات مركزية فقط؛ التنفيذ يتطلب تصريح إجراء مستقل"
    return result
