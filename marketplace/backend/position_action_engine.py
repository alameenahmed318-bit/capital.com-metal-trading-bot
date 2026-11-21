from market_symbols import supported_market_symbol
"""Central position-management planner for AL-ZOOL.

Returns non-executable proposals only. MT5 must require a separate, short-lived,
action-specific authorization before placing add-on, cooling or recovery orders.
This prevents legacy local strategies from bypassing the model council.
"""
import math
from recovery_logic import recovery_intent
from recovery_candle_guard import observe_recovery_arm
from position_sizing import count_action, next_action_lot

def num(value,default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (ValueError,TypeError,OverflowError):return default

def plan_account_actions(snapshot,decision,council,risk,settings,council_advisory=False):
    all_positions=snapshot.get("positions") or []
    symbol=str(snapshot.get("symbol") or "")
    positions=[p for p in all_positions if isinstance(p,dict) and
        str(p.get("symbol") or "")==symbol and supported_market_symbol(symbol)]
    pm=(settings or {}).get("position_management") or {}
    atr=num(decision.get("entry_atr") or (decision.get("context") or {}).get("atr"))
    tick=snapshot.get("tick") or {}
    signal=str(decision.get("decision","WAIT")).upper()
    result={"mode":"DIAGNOSTIC_ONLY","execution_authorized":False,
            "bar_time":int(num(decision.get("bar_time"))),"buy":"HOLD","sell":"HOLD",
            "actions":[],"reason_ar":"لا يوجد قرار إدارة صالح"}
    if not all_positions:
        result["reason_ar"]="لا توجد صفقات مفتوحة للحساب";return result
    if not positions:
        result["reason_ar"]="لا توجد مراكز للرمز الحالي يمكن تنفيذ التعافي في اتجاهها";return result
    # Pre-existing mixed-side exposure on the active symbol: don't add either side, including
    # x3 smart recovery. Existing positions are never automatically closed.
    open_directions={int(num(p.get("type"),-1)) for p in positions}
    if 0 in open_directions and 1 in open_directions:
        result["reason_ar"]="حماية الاتجاه: توجد مراكز BUY وSELL للرمز الحالي معاً؛ إيقاف التعزيز والتبريد والتعافي الجديد"
        return result
    if str(pm.get("mode","AI")).upper() not in ("AI","MANUAL"):
        result["reason_ar"]="وضع إدارة الصفقات غير صالح";return result
    # Open-position management uses its own live trend decision; it must not
    # inherit the entry council's three-model veto.
    if not risk.get("approved") or not settings.get("trading_enabled"):
        result["reason_ar"]="حالة الحساب أو مفتاح التداول يمنع إجراء إدارة جديد";return result
    # Account-wide recovery takes priority over entry/add-on signals.
    # The recovery direction comes from losing positions on the active symbol;
    # a Qwen/strategy WAIT does not become a second veto.
    observe_recovery_arm(snapshot,settings)
    recovery=recovery_intent(snapshot,decision,settings)
    result["recovery"]=recovery
    if recovery.get("active_cycle"):
        result["reason_ar"]=recovery["reason_ar"]
        return result
    if recovery["armed"]:
        action=recovery.get("action")
        side=recovery.get("side")
        lot=num(recovery.get("lot_cap"))
        reason=recovery.get("reason_ar","")
        if action=="RECOVERY_PROPOSAL":
            same_side=sum(1 for p in positions if
                (int(num(p.get("type"),-1))==0 if side=="BUY"
                 else int(num(p.get("type"),-1))==1))
            raw_side_cap=pm.get("max_positions_per_direction")
            raw_action_cap=pm.get("recovery_max_lot")
            raw_total_cap=pm.get("max_total_lots")
            total_open=sum(max(0,num(p.get("volume"))) for p in all_positions
                           if isinstance(p,dict))
            if raw_side_cap is not None and same_side>=int(num(raw_side_cap)):
                action="HOLD";reason="عدد مراكز الاتجاه بلغ الحد الذي حدده المالك"
            elif raw_action_cap is not None and lot>num(raw_action_cap)+1e-8:
                action="HOLD";reason="حجم التعافي ×3 تجاوز حد لوت التعافي الذي حدده المالك"
            elif raw_total_cap is not None and total_open+lot>num(raw_total_cap)+1e-8:
                action="HOLD";reason="حجم التعافي تجاوز سقف اللوت الإجمالي الذي حدده المالك"
        if action!="RECOVERY_PROPOSAL":lot=0.0
        result[side.lower() if side in ("BUY","SELL") else "buy"]=action
        confirmation=recovery.get("candle_confirmation") or {}
        result["actions"]=[{"side":side,"action":action,"lot_cap":round(lot,3),
            "floating_net":recovery.get("account_net"),"recovery_armed":True,
            "candidate_lot":recovery.get("candidate_lot"),
            "candle_confirmed":confirmation.get("approved",False),
            "candle_state":confirmation.get("state","WAIT_ARM"),
            "candle_score":confirmation.get("candle_score",0.0),
            "recovery_armed_at":confirmation.get("armed_at"),
            "confirmation_candle_open":confirmation.get("confirmed_candle_time"),
            "confirmation_eligible_at":confirmation.get("eligible_at"),
            "candle_reason_ar":confirmation.get("reason_ar",reason),
            "reason_ar":reason,"execution_authorized":False}]
        result["reason_ar"]=reason
        return result
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
        # Existing-position management has its own price and model evidence;
        # a new-entry BUY/SELL/WAIT is a timing bias, not a hard veto.
        price=num(tick.get("bid") if side=="BUY" else tick.get("ask"))
        volume=sum(max(0,num(p.get("volume"))) for p in group)
        if volume<=0 or price<=0:continue
        average=sum(num(p.get("open_price"))*max(0,num(p.get("volume"))) for p in group)/volume
        net=sum(num(p.get("profit"))+num(p.get("swap")) for p in group)
        progress=((price-average)/atr*(1 if side=="BUY" else -1)) if atr>0 else 0.0
        supports=sum(num(v.get("confidence")) for v in advisory if v.get("direction")==side)
        opposes=sum(num(v.get("confidence")) for v in advisory if v.get("direction") in ("BUY","SELL") and v.get("direction")!=side)
        # Models are evidence, not extra vetoes. Adjust an existing ATR
        # threshold only when a vote was actually received for this bar.
        advisory_bias=0.15*(supports-opposes)/max(1,len(advisory))
        strategy_bias=(0.12 if signal==side else -0.12 if signal in ("BUY","SELL") else 0.0)
        advice_summary=(f"Qwen/DeepSeek {len(advisory)} READY; model_bias={advisory_bias:.3f} ATR; "
                        f"entry_signal={signal}; strategy_bias={strategy_bias:+.2f} ATR")
        action="HOLD";reason="حركة السعر لا تؤيد زيادة المخاطرة"
        candidate_lot=0.0
        addons=count_action(group,"ADDON_PROPOSAL")
        coolings=count_action(group,"COOLING_PROPOSAL")
        addon_limit=int(num(pm.get("addon_max")))
        cooling_limit=int(num(pm.get("cooling_max")))
        addon_available=addon_limit<=0 or addons<addon_limit
        cooling_available=cooling_limit<=0 or coolings<cooling_limit
        mode=str(pm.get("mode","AI")).upper()
        # Recovery is exclusively planned above from ACCOUNT net.
        if (mode=="AI" and atr>0 and
            progress<-(cooling_band-advisory_bias-strategy_bias) and
            pm.get("cooling_enabled") is True and cooling_available):
            action="COOLING_PROPOSAL"
            reason="السعر تراجع عن متوسط مراكز الاتجاه؛ تم ترجيح التوقيت بتحليل ATR وHybrid AI"
        elif (mode=="AI" and atr>0 and
              progress>(addon_band-advisory_bias-strategy_bias) and
              pm.get("addon_enabled") is True and addon_available):
            action="ADDON_PROPOSAL"
            reason="السعر يتحرك لصالح المركز؛ تم ترجيح التوقيت بتحليل ATR وHybrid AI"
        if action=="HOLD" and mode=="AI":
            if pm.get("cooling_enabled") is True and not cooling_available and progress<0:
                reason=f"تم بلوغ عدد التبريد الذي حدده المالك ({cooling_limit}) لهذا الاتجاه"
            elif pm.get("addon_enabled") is True and not addon_available and progress>0:
                reason=f"تم بلوغ عدد التعزيز الذي حدده المالك ({addon_limit}) لهذا الاتجاه"
        # The owner-defined per-side position limit is independent from total
        # account exposure. Existing manual positions count toward both.
        side_cap=(int(num(pm["max_positions_per_direction"])) if pm.get("max_positions_per_direction") is not None else None)
        if action!="HOLD" and side_cap is not None and len(group)>=side_cap:
            action="HOLD";reason="عدد المراكز في الاتجاه بلغ الحد المحدد"
        # Manual mode uses fixed distances/lots while all actions still require
        # the same fresh council and risk authorization.
        if action=="HOLD" and mode=="MANUAL" and (side_cap is None or len(group)<side_cap):
            # Manual mode depends on points, not on ATR being available.
            movement=(price-average)*(1 if side=="BUY" else -1)
            adverse=max(0.0,-movement)
            favorable=max(0.0,movement)
            point=max(0,num((snapshot.get("metadata") or {}).get("point_size")))
            if point<=0:point=max(0,num(tick.get("point_size")))
            if (point>0 and pm.get("cooling_enabled") is True and cooling_available and
                adverse/point>=num(pm.get("cooling_distance_points"),1)>0):
                action="COOLING_PROPOSAL";reason="تحققت مسافة التبريد اليدوي المختارة"
            elif (point>0 and pm.get("addon_enabled") is True and addon_available and
                  favorable/point>=num(pm.get("addon_distance_points"),1)>0):
                action="ADDON_PROPOSAL";reason="تحققت مسافة التعزيز اليدوي المختارة"
        sizing={}
        if action in ("ADDON_PROPOSAL","COOLING_PROPOSAL"):
            sizing=next_action_lot(group,action,pm,tick)
            candidate_lot=sizing["lot"]
            if candidate_lot<=0:
                action="HOLD";reason=sizing.get("reason_ar","اللوت غير صالح لدى الوسيط")
            else:
                reason+=(f" | الإضافة #{sizing['index']} | الأساس {sizing['base_lot']:.4f}"
                         f" × {sizing['multiplier']:.4f}^{sizing['count']} (AI فقط)"
                         f" = {sizing['raw_lot']:.5f} | لوت التنفيذ {candidate_lot:.5f}")
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
        result["actions"].append({"side":side,"action":action,"lot_cap":round(candidate_lot,8),
            "base_lot":sizing.get("base_lot"),"multiplier":sizing.get("multiplier"),
            "addition_index":sizing.get("index"),"existing_addons":addons,
            "existing_coolings":coolings,"raw_lot":sizing.get("raw_lot"),
            "broker_volume_step":sizing.get("volume_step"),
            "recovery_armed":bool(recovery.get("armed")),
            "atr_distance":round(progress,3),"floating_net":round(net,2),
            "reason_ar":reason,"ai_advice":advice_summary,"execution_authorized":False})
    # One-use central claim authorizes one action per bar. Prioritize the
    # largest ATR movement when both sides qualify rather than vetoing both.
    proposals=[a for a in result["actions"] if a["action"]!="HOLD" and a["lot_cap"]>0]
    if len(proposals)>1:
        proposals.sort(key=lambda a:(abs(num(a.get("atr_distance"))),abs(num(a.get("floating_net")))),
                       reverse=True)
        for a in proposals[1:]:
            a["action"]="HOLD"
            a["lot_cap"]=0.0
            a["reason_ar"]="تأجيل الإجراء: يوجد إجراء أكثر إلحاحاً على الجانب الآخر في هذه الشمعة"
            result[a["side"].lower()]="HOLD"
    active=[a for a in result["actions"] if a["action"]!="HOLD"]
    result["reason_ar"]=(active[0]["reason_ar"] if active else
        " | ".join(f"{a['side']}: {a['reason_ar']}" for a in result["actions"]))
    return result
