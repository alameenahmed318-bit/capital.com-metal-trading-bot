"""Bounded, nonblocking MASTER entry review. Never places or manages orders."""
import math

MAX_COLLECTION_SECONDS=20.0
MIN_QUALITY=.55
INDEPENDENT=("Qwen","DeepSeek","GPT-OSS 120B")
ARENA_FAMILIES={**{n:"TREES" for n in ("LightGBM","XGBoost","CatBoost")},
                **{n:"FORECAST" for n in ("TiRex-2","Chronos-2","TimesFM-2.5","Chronos-Bolt Tiny")}}
PENDING={"PENDING","AWAITING_LOCAL_VOTE","NOT_RECEIVED","MISSING"}
PERIODS={"M1":60,"M5":300,"M15":900,"M30":1800,"H1":3600,"H4":14400}

def number(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError,OverflowError):
        return default

def clamp(v):
    return min(1.0,max(0.0,v))

UNIFIED_SCORE_VERSION="MASTER_WEIGHTED_M1_V1"
UNIFIED_SCORE_THRESHOLD=.75

def unified_score(d,item,side,age,arena_rows,settings):
    """Research candidate: one weighted score, not a calibrated probability.
    No order authority. Missing/stale models do not count as agreement.
    Only an eligible strategy, valid fresh price and no explicit trap are
    prerequisites; market quality and forecast magnitude are soft evidence.
    """
    opposite="SELL" if side=="BUY" else "BUY"
    if side not in ("BUY","SELL"):return {"version":UNIFIED_SCORE_VERSION,"direction":"WAIT","reason":"NO_DIRECTION","execution_authorized":False}
    c=d.get("context") or {}; m=d.get("master") or {}
    scores=m.get("scores") or {}
    own=number(scores.get(side)); other=number(scores.get(opposite))
    margin=clamp((own-other)/max(abs(own)+abs(other),1e-9))
    quality=clamp(number(c.get(side.lower()+"_quality"))/100.)
    strength=clamp(number(d.get("confidence")))
    groups={}
    for row in arena_rows:
        family=ARENA_FAMILIES.get(row.get("model"))
        if family:
            groups.setdefault(family,[]).append(clamp(number(row.get("confidence")))*(1 if row.get("direction")==side else -1))
    family_strength={k:sum(v)/len(v) for k,v in groups.items() if v}
    mass=sum(family_strength.values())/len(family_strength) if family_strength else 0.
    score=.40*strength+.20*margin+.25*quality+.15*(.5+.5*mass)
    candidate=(d.get("directional_candidates") or {}).get(side)
    tick=item.get("tick") or {}
    bid=number(tick.get("bid"));ask=number(tick.get("ask"))
    ref=number(d.get("entry_reference_price"));atr=number(d.get("entry_atr") or c.get("atr"))
    price=ask if side=="BUY" else bid
    deviation=abs(price-ref)/atr if 0<bid<=ask and ref>0 and atr>0 else None
    pm=(settings or {}).get("position_management") or {}
    cap=number(pm.get("entry_max_deviation_atr"),.5)
    # Retain existing owner price guard; the replay uses the same guard.
    checks={"strategy":isinstance(candidate,dict) and 1<=number(candidate.get("strategy_id"))<=200 and number(candidate.get("strategy_id"))==number(d.get("strategy_id")),
        "fresh":5<=age<60,"symbol":bool(d.get("symbol")) and d.get("symbol")==item.get("symbol"),
        "no_trap":c.get(side.lower()+"_trap") is False,"price":deviation is not None and (pm.get("entry_guard_enabled",True) is not True or 0<cap and deviation<=cap),
        "fresh_forecast":bool(family_strength)}
    failures=[k for k,v in checks.items() if not v]
    if score<UNIFIED_SCORE_THRESHOLD:failures.append("weighted_score")
    return {"version":UNIFIED_SCORE_VERSION,"mode":"SHADOW_ONLY","score":round(score,6),
        "threshold":UNIFIED_SCORE_THRESHOLD,"candidate_direction":side,
        "direction":side if not failures else "WAIT","failed_checks":failures,
        "components":{"strategy_strength":strength,"direction_margin":margin,"market_quality":quality,"forecast_family_strength":family_strength,"forecast_mass":mass},
        "price_deviation_atr":round(deviation,6) if deviation is not None else None,
        "execution_authorized":False}

DIRECT_DEMO_VERSION="MASTER_DIRECT_DEMO_V1"
DIRECT_DEMO_THRESHOLD=.72

def direct_demo_enabled(item):
    # Only an explicitly versioned terminal reporting the native MT5 demo
    # account enumeration can use the pilot. Unknown/contest/real keep V2.
    return (item.get("master_direct_demo_capable") is True
            and (item.get("account") or {}).get("trade_mode")=="DEMO")

def direct_demo_score(d,item,side,age,arena_rows,language_rows,settings):
    """Single score for the explicitly demo-only pilot, not a probability.
    No count, unanimity, or model collection wait. Missing votes are neutral.
    Keep the same closed-bar, real strategy and owner price requirements.
    """
    other="SELL" if side=="BUY" else "BUY"; sign=1 if side=="BUY" else -1
    ctx=d.get("context") or {}
    qs=d.get("directional_scores") or {}
    raw=number(qs.get(side)); opp=number(qs.get(other))
    strength=clamp(raw)
    margin=clamp((raw-opp)/max(abs(raw)+abs(opp),1e-9))
    quality=clamp(number(ctx.get(side.lower()+"_quality"))/100.)
    tick=item.get("tick") or {}; bid=number(tick.get("bid"));ask=number(tick.get("ask"))
    ref=number(d.get("entry_reference_price"));atr=number(d.get("entry_atr") or ctx.get("atr"))
    valid_price=0<bid<=ask and ref>0 and atr>0
    move=sign*((bid+ask)*.5-ref)/atr if valid_price else 0.
    momentum=.5+.5*max(-1.,min(1.,move/.35))
    groups={}
    for row in list(arena_rows)+list(language_rows):
        family=ARENA_FAMILIES.get(row.get("model"))
        if family is None and row.get("model") in INDEPENDENT:family="LANGUAGE"
        if family and row.get("direction") in ("BUY","SELL"):
            groups.setdefault(family,[]).append(clamp(number(row.get("confidence")))*(1 if row.get("direction")==side else -1))
    mass={k:sum(v)/len(v) for k,v in groups.items() if v}
    # Each known family gets an equal share; absent evidence is neutral,
    # not agreement and not an increased weight on the remaining models.
    advice=sum(mass.values())/3.
    score=.40*strength+.20*margin+.20*quality+.10*(.5+.5*advice)+.10*momentum
    candidate=(d.get("directional_candidates") or {}).get(side)
    pm=(settings or {}).get("position_management") or {}
    deviation=abs((ask if side=="BUY" else bid)-ref)/atr if valid_price else None
    cap=number(pm.get("entry_max_deviation_atr"),.5)
    checks={"demo":direct_demo_enabled(item),"fresh":0<=age<60,
        "symbol":bool(d.get("symbol")) and d.get("symbol")==item.get("symbol"),
        "strategy":isinstance(candidate,dict) and 1<=number(candidate.get("strategy_id"))<=200 and number(candidate.get("strategy_id"))==number(d.get("strategy_id")),
        "no_trap":ctx.get(side.lower()+"_trap") is False,
        "price":valid_price and (pm.get("entry_guard_enabled",True) is not True or 0<cap and deviation<=cap)}
    failed=[k for k,v in checks.items() if not v]
    if score<DIRECT_DEMO_THRESHOLD:failed.append("weighted_score")
    return {"version":DIRECT_DEMO_VERSION,"mode":"DEMO_ONLY","score":round(score,6),
        "threshold":DIRECT_DEMO_THRESHOLD,"approved":not failed,"failed_checks":failed,
        "components":{"strategy_strength":strength,"direction_margin":margin,"market_quality":quality,
            "live_momentum":momentum,"live_move_atr":move,"forecast_family_strength":mass,"advice":advice},
        "price_deviation_atr":round(deviation,6) if deviation is not None else None}

def local_continuation(d,item,side,age,arena_rows,language_rows):
    """Strict, price-confirmed M1 route; no dependency on model availability.

    This is a separate quantitative route, not fabricated independent votes.
    Weak three-minute forecasts do not become a full veto. A materially
    opposing family still prevents this route. Thresholds are policy
    hypotheses, not a calibrated probability or demonstrated profitability.
    """
    other="SELL" if side=="BUY" else "BUY"
    sign=1 if side=="BUY" else -1
    ctx=d.get("context") or {}
    candidate=(d.get("directional_candidates") or {}).get(side)
    scores=d.get("directional_scores") or {}
    own=number(scores.get(side),-1.)
    opposite=number(scores.get(other),1.)
    margin=(own-opposite)/max(abs(own)+abs(opposite),1e-9)
    families=list((d.get("family_votes") or {}).values())
    supporting=families.count(side)
    total=supporting+families.count(other)
    quality=number(ctx.get(side.lower()+"_quality"))/100.
    pressure=sign*number(ctx.get("last_closed_candle_pressure"))
    tick=item.get("tick") or {}
    bid=number(tick.get("bid"));ask=number(tick.get("ask"))
    ref=number(d.get("entry_reference_price"));atr=number(d.get("entry_atr") or ctx.get("atr"))
    move=sign*((bid+ask)*.5-ref)/atr if 0<bid<=ask and ref>0 and atr>0 else -1.
    spread=(ask-bid)/atr if 0<bid<=ask and atr>0 else 1.
    # Expected movement is in ATR, not probability. Count each forecast
    # family once, by average signed amplitude (not number of model names).
    mass={}
    for row in arena_rows:
        family=ARENA_FAMILIES.get(row.get("model"))
        if family:
            v=clamp(number(row.get("confidence")))
            mass.setdefault(family,[]).append(v if row.get("direction")==side else -v)
    strengths={k:sum(v)/len(v) for k,v in mass.items() if v}
    material=max(.20,2*spread)
    opposing=any(v<=-material for v in strengths.values()) or any(
        row.get("direction")==other and number(row.get("confidence"))>=.60
        for row in language_rows)
    eligible=(isinstance(candidate,dict) and 1<=number(candidate.get("strategy_id"))<=200
        and number(candidate.get("strategy_id"))==number(d.get("strategy_id"))
        and number(candidate.get("combination_id"))>0
        and own>=.85 and margin>=.75 and supporting>=5 and total>0
        and supporting/total>=.80 and quality>=.80 and pressure>=.55
        and ctx.get(side.lower()+"_trap") is False and 0<=spread<=.15
        and .08<=move<=.35 and 5<=age<=20 and not opposing)
    return {"eligible":bool(eligible),"quant_score":round(own,4),"quant_margin":round(margin,4),
        "strategy_families":supporting,"strategy_family_count":total,
        "market_quality":round(quality,4),"candle_pressure":round(pressure,4),
        "live_move_atr":round(move,4),"spread_atr":round(spread,4),
        "forecast_family_strength":strengths,"material_opposition_threshold":round(material,4),
        "material_opposition":bool(opposing)}

def review(decision,snapshot,settings,now_unix,*,live_arena=False):
    """Review current-bar evidence without sleeps, requests, or unanimity.
    Threshold is a provisional quality score, NOT a calibrated win probability.
    The existing MT5 price-deviation setting is respected, not replaced.
    """
    d=decision or {}; item=snapshot or {}; report=d.get("master") or {}
    side=str(d.get("decision","WAIT")).upper()
    frame=str(item.get("timeframe","M1")).upper().replace("PERIOD_","")
    seconds=PERIODS.get(frame,0)
    bar=number(d.get("bar_time"))
    age=number(now_unix)-bar-seconds if bar>0 and seconds else -1
    rows={r.get("model"):r for r in report.get("models",[]) if isinstance(r,dict)}
    statuses={n:str(rows.get(n,{}).get("status","NOT_RECEIVED")) for n in INDEPENDENT}
    votes=[rows[n] for n in INDEPENDENT if n in rows and
           rows[n].get("used") is True and rows[n].get("status")=="READY" and
           rows[n].get("direction") in ("BUY","SELL")]
    pending=[n for n,s in statuses.items() if s in PENDING]
    # New admission path only uses the server-side, owner-scoped current-bar
    # cache already verified by master_ai; never accept browser/EA votes here.
    arena=report.get("arena_shadow") or {}
    advisory=report.get("arena_advisory") or {}
    fresh_arena=(live_arena and frame=="M1" and arena.get("status")=="SHADOW_READY"
        and arena.get("symbol")==d.get("symbol")==item.get("symbol")
        and arena.get("bar_time")==d.get("bar_time")
        and 0<=number(now_unix)-number(arena.get("received_at"))<=60)
    extra=[]
    if fresh_arena:
        used=set(advisory.get("used") or [])
        for n in ARENA_FAMILIES:
            r=rows.get(n,{})
            v=number((arena.get("models") or {}).get(n))
            if (n in used and r.get("used") is True and r.get("status")=="READY"
                and abs(v)>.05 and r.get("direction")==("BUY" if v>0 else "SELL")
                and number(r.get("points"))>0):
                extra.append(r)
    adaptive=bool(extra)
    if adaptive:
        votes=votes+extra
        statuses.update({n:("READY" if any(r.get("model")==n for r in extra) else "OPTIONAL_UNAVAILABLE") for n in ARENA_FAMILIES})
    base={"policy":"MASTER_REVIEW_V2" if live_arena else "MASTER_REVIEW_V1","age_after_close_sec":round(age,3),
          "collection_limit_sec":MAX_COLLECTION_SECONDS,
          "independent_votes":len(votes),"pending":pending,"statuses":statuses,
          "candidate_direction":side,"quality_threshold":MIN_QUALITY,
          "quality":None,"unanimity_required":False,"arena_models_used":[r["model"] for r in extra],
          "legacy_pending_optional":bool(adaptive or (live_arena and votes))}
    def result(state,reason,approved=False,**extra):
        return {**base,**extra,"state":state,"reason_ar":reason,"approved":approved}
    if side not in ("BUY","SELL"):
        return result("NO_CANDIDATE","لا توجد إشارة اتجاهية صالحة للمراجعة")
    if age<0 or age>=seconds:
        return result("STALE_BAR","نافذة الشمعة غير صالحة")
    if live_arena and frame=="M1":
        base["unified_score"]=unified_score(d,item,side,age,extra,settings)
    if live_arena and frame=="M1" and direct_demo_enabled(item):
        trial=direct_demo_score(d,item,side,age,extra,[r for r in votes if r.get("model") in INDEPENDENT],settings)
        base.update(policy=DIRECT_DEMO_VERSION,route="DIRECT_DEMO",direct_demo=trial,
                    quality=trial["score"],quality_threshold=trial["threshold"],collection_limit_sec=0.0,
                    legacy_pending_optional=True,price_deviation_atr=trial["price_deviation_atr"])
        if trial["approved"]:
            return result("REVIEWED","MASTER التجريبي: تقييم موحد للأدلة والسعر دون انتظار النماذج",True)
        return result("PRICE_MOVED" if "price" in trial["failed_checks"] else "WEAK_EVIDENCE",
                      "MASTER التجريبي: التقييم الموحد لم يكتمل — "+", ".join(trial["failed_checks"]))
    local=local_continuation(d,item,side,age,extra,[r for r in votes if r.get("model") in INDEPENDENT]) if live_arena and frame=="M1" else {"eligible":False}
    if live_arena:
        base["local_continuation"]=local
    if local["eligible"]:
        # Use the same price validity/deviation check as the usual gate.
        tick=item.get("tick") or {}
        price=number(tick.get("ask" if side=="BUY" else "bid"))
        reference=number(d.get("entry_reference_price"))
        atr=number(d.get("entry_atr") or (d.get("context") or {}).get("atr"))
        deviation=abs(price-reference)/atr
        pm=(settings or {}).get("position_management") or {}
        cap=number(pm.get("entry_max_deviation_atr"),.5)
        if pm.get("entry_guard_enabled",True) is True and (cap<=0 or deviation>cap):
            return result("PRICE_MOVED","السعر ابتعد عن نقطة الدخول وفق حد الانحراف الحالي",price_deviation_atr=round(deviation,4))
        return result("REVIEWED","MASTER: استراتيجية قوية مدعومة باستمرار السعر الفعلي دون معارضة نموذجية معتبرة",
                      True,route="LOCAL_PRICE_CONFIRMED",price_deviation_atr=round(deviation,4),
                      quality=None,collection_limit_sec=5.0)
    # Legacy local advisers are optional in the live path. Do not hold a
    # valid independent vote hostage to a missing Qwen/DeepSeek response.
    # Preserve the existing quality, independent-evidence and price checks.
    if not adaptive and age<MAX_COLLECTION_SECONDS and pending and (not live_arena or not votes):
        return result("COLLECTING","جارٍ جمع تحليلات النماذج المستقلة للشمعة الحالية")
    if not votes:
        return result("NO_INDEPENDENT_EVIDENCE","لا دخول اعتماداً على Fast AI وحده؛ لا يوجد تحليل اتجاهي مستقل صالح")
    scores=report.get("scores") or {}
    opposite="SELL" if side=="BUY" else "BUY"
    own=number(scores.get(side)); other=number(scores.get(opposite))
    margin=clamp((own-other)/max(abs(own)+abs(other),1e-9))
    # Opposing votes contribute to the evidence, not an individual veto.
    weights=[max(0.0,number(r.get("points"))) for r in votes]
    total=sum(weights)
    support=sum(w for w,r in zip(weights,votes) if r.get("direction")==side)/total if total>0 else 0.0
    family_support={}
    if adaptive:
        groups={}
        for r in votes:
            family=ARENA_FAMILIES.get(r.get("model"),"LANGUAGE")
            g=groups.setdefault(family,{"BUY":0.,"SELL":0.})
            g[r["direction"]]+=max(0.,number(r.get("points")))
        family_support={k:g.get(side,0)/sum(g.values()) for k,g in groups.items() if sum(g.values())>0}
        support=sum(family_support.values())/len(family_support) if family_support else 0.
    context=d.get("context") or {}
    prefix=side.lower()
    market_quality=clamp(number(context.get(prefix+"_quality"),50.0)/100.0)
    trap=context.get(prefix+"_trap") is True
    strength=clamp(number(d.get("confidence")))
    quality=clamp(.40*strength+.25*margin+.20*market_quality+.15*support-(.10 if trap else 0.0))
    detail={"quality":round(quality,4),"direction_margin":round(margin,4),
            "independent_support":round(support,4),"market_quality":round(market_quality,4),
            "trap_flag":trap}
    if adaptive:
        supporting=sum(v>.5 for v in family_support.values())
        pressure=(1 if side=="BUY" else -1)*number(context.get("last_closed_candle_pressure"))
        # Correlated trees/forecasters count as one family each. Do not
        # confuse seven forecasts with seven independent confirmations.
        strong=quality>=.80 and support>=.75 and supporting>=2 and pressure>=.25 and not trap
        medium=quality>=.68 and support>=.65 and supporting>=2 and not trap
        single=quality>=.70 and support>=.75 and supporting>=1 and margin>=.35 and market_quality>=.35 and not trap
        # Only exceptionally strong, diverse evidence may accelerate the
        # existing five-second lane. Keep all normal quality thresholds.
        exceptional=(strong and supporting>=3 and all(v>=.75 for v in family_support.values())
                     and quality>=.90 and margin>=.60 and market_quality>=.70 and pressure>=.50)
        # A very strong, strategy-backed single-family signal may use the
        # eight-second lane instead of an unconditional fifteen-second wait.
        # Never waive the single-family quality/support requirements.
        early_single=(single and supporting==1 and len(family_support)==1
                      and quality>=.85 and support>=.95 and margin>=.75
                      and market_quality>=.45 and pressure>=.35)
        target=2.0 if exceptional else 5.0 if strong else 8.0 if early_single else 10.0 if medium else 15.0
        detail.update(evidence_families=family_support,supporting_families=supporting,
                      exceptional_early_candidate=exceptional,early_single_candidate=early_single,
                      collection_limit_sec=target,quality_threshold=.90 if exceptional else .80 if strong else .85 if early_single else .68 if medium else .70)
        if not (strong or medium or single):
            return result("WEAK_EVIDENCE","أدلة النماذج المتاحة أو اتجاه السوق غير كافية؛ انتظار فرصة أوضح",**detail)
        if age<target:
            return result("COLLECTING","مراجعة ديناميكية للأدلة المتاحة؛ لا انتظار إجباري للنماذج القديمة",**detail)
    # When accelerating a legacy-only candidate, require at least the same
    # single-family quality and directional support used for arena evidence.
    # The normal post-collection policy is unchanged.
    if live_arena and not adaptive and pending and age<MAX_COLLECTION_SECONDS:
        early_ok=(quality>=.70 and support>=.75 and margin>=.35 and market_quality>=.35 and not trap)
        detail.update(early_legacy_quality_threshold=.70,early_legacy_eligible=early_ok)
        if not early_ok:
            return result("COLLECTING","الأدلة الحالية لا تحقق جودة الدخول المبكر؛ مراجعة عند وصول دليل إضافي",**detail)
        if age<5:
            return result("COLLECTING","مراجعة مبكرة للسعر والأدلة المستقلة",**detail)
    if quality<MIN_QUALITY:
        return result("WEAK_EVIDENCE","جودة الأدلة المجمعة ضعيفة؛ انتظار فرصة أفضل",**detail)
    tick=item.get("tick") or {}
    bid=number(tick.get("bid")); ask=number(tick.get("ask"))
    price=ask if side=="BUY" else bid
    reference=number(d.get("entry_reference_price"))
    atr=number(d.get("entry_atr") or context.get("atr"))
    if not (0<bid<=ask and reference>0 and atr>0):
        return result("PRICE_UNAVAILABLE","سعر التنفيذ أو ATR غير متاح للتحقق الأخير",**detail)
    deviation=abs(price-reference)/atr
    detail["price_deviation_atr"]=round(deviation,4)
    pm=(settings or {}).get("position_management") or {}
    if pm.get("entry_guard_enabled",True) is True:
        cap=number(pm.get("entry_max_deviation_atr"),.5)
        if cap<=0 or deviation>cap:
            return result("PRICE_MOVED","السعر ابتعد عن نقطة الدخول وفق حد الانحراف الحالي",**detail)
    return result("REVIEWED",("MASTER V2: اكتملت مراجعة أدلة النماذج المتاحة والسعر؛ لا انتظار إجباري للنماذج القديمة" if adaptive else "اكتملت مراجعة الأدلة والسعر؛ دون اشتراط اتفاق النماذج"),True,**detail)

def apply_gate(gate,review_result):
    """Preserve every existing rejection and only narrow entry authorization."""
    if not gate.get("approved") or review_result.get("approved"):
        return gate
    return {**gate,"approved":False,"status":"WAIT","authorization_id":None,
            "expires_at_unix":0,"reason_ar":review_result["reason_ar"]}
