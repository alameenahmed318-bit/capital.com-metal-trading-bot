"""Bounded, nonblocking MASTER entry review. Never places or manages orders."""
import math

MAX_COLLECTION_SECONDS=20.0
MIN_QUALITY=.55
INDEPENDENT=("Qwen","DeepSeek","GPT-OSS 120B")
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

def review(decision,snapshot,settings,now_unix):
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
    base={"policy":"MASTER_REVIEW_V1","age_after_close_sec":round(age,3),
          "collection_limit_sec":MAX_COLLECTION_SECONDS,
          "independent_votes":len(votes),"pending":pending,"statuses":statuses,
          "candidate_direction":side,"quality_threshold":MIN_QUALITY,
          "quality":None,"unanimity_required":False}
    def result(state,reason,approved=False,**extra):
        return {**base,**extra,"state":state,"reason_ar":reason,"approved":approved}
    if side not in ("BUY","SELL"):
        return result("NO_CANDIDATE","لا توجد إشارة اتجاهية صالحة للمراجعة")
    if age<0 or age>=seconds:
        return result("STALE_BAR","نافذة الشمعة غير صالحة")
    if age<MAX_COLLECTION_SECONDS and pending:
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
    context=d.get("context") or {}
    prefix=side.lower()
    market_quality=clamp(number(context.get(prefix+"_quality"),50.0)/100.0)
    trap=context.get(prefix+"_trap") is True
    strength=clamp(number(d.get("confidence")))
    quality=clamp(.40*strength+.25*margin+.20*market_quality+.15*support-(.10 if trap else 0.0))
    detail={"quality":round(quality,4),"direction_margin":round(margin,4),
            "independent_support":round(support,4),"market_quality":round(market_quality,4),
            "trap_flag":trap}
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
    return result("REVIEWED","اكتملت مراجعة الأدلة والسعر؛ دون اشتراط اتفاق النماذج",True,**detail)

def apply_gate(gate,review_result):
    """Preserve every existing rejection and only narrow entry authorization."""
    if not gate.get("approved") or review_result.get("approved"):
        return gate
    return {**gate,"approved":False,"status":"WAIT","authorization_id":None,
            "expires_at_unix":0,"reason_ar":review_result["reason_ar"]}
