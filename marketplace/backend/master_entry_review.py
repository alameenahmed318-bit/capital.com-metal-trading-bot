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
        target=2.0 if exceptional else 5.0 if strong else 10.0 if medium else 15.0
        detail.update(evidence_families=family_support,supporting_families=supporting,
                      exceptional_early_candidate=exceptional,
                      collection_limit_sec=target,quality_threshold=.90 if exceptional else .80 if strong else .68 if medium else .70)
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
