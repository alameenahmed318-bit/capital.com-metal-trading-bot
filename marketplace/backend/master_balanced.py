"""AI-first entry-only demo pilot; retain balanced V2 for research comparison."""
import math
from market_symbols import same_market_symbol

LEGACY_VERSION="MASTER_BALANCED_DEMO_V2"
VERSION="MASTER_AI_FIRST_DEMO_V3"
THRESHOLD=.72
SIDES=("BUY","SELL")
FAMILIES={"TREES":("LightGBM","XGBoost","CatBoost"),
          "FORECAST":("TiRex-2","Chronos-2","TimesFM-2.5","Chronos-Bolt Tiny")}
MODELS=tuple(n for names in FAMILIES.values() for n in names)

def num(v,default=0.):
    try:
        if isinstance(v,bool):return default
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError,ValueError,OverflowError):return default

def clamp(v,lo=0.,hi=1.):
    return max(lo,min(hi,v))

def weight_profile(feedback,symbol,now):
    """Only use matured, market-specific chronological research profiles.
    Equal family budgets prevent correlated models from dominating by count.
    Do not renormalize across missing models or borrow another market's data.
    """
    f=feedback or {};p=f.get("master_adaptive") or {}
    fresh=bool(symbol and same_market_symbol(f.get("symbol"),symbol)
        and 0<=now-num(p.get("updated_at_unix"),-1)<=1200)
    evidence=p.get("model_profiles") or {} if fresh else {}
    factors={};statuses={}
    for n in MODELS:
        e=evidence.get(n) or {}
        tr=num(e.get("train"));va=num(e.get("validation"))
        a=num(e.get("train_mean"));b=num(e.get("validation_mean"))
        factor=1.;status="INSUFFICIENT"
        if tr>=80 and va>=40:
            status="NEUTRAL"
            if e.get("status")=="POSITIVE_BOTH" and a>.03 and b>.03:
                factor=1.2;status="POSITIVE_BOTH"
            elif e.get("status")=="NEGATIVE_BOTH" and a<-.03 and b<-.03:
                factor=.8;status="NEGATIVE_BOTH"
        factors[n]=factor;statuses[n]=status
    weights={n:.5*factors[n]/sum(factors[k] for k in names)
             for names in FAMILIES.values() for n in names}
    return {"symbol":symbol,"computed_at":now,"weights":weights,
            "factors":factors,"statuses":statuses,"profile_fresh":fresh,
            "metric":"MATURE_180S_PRICE_PROXY_NOT_PROFITABILITY"}

def evaluate(d,item,settings,now,*,legacy=False):
    """Rank BUY/SELL on the same scale, only for actual eligible strategies.
    AI-first uses 60% of the score and requires current, material directional
    model evidence. No required model count or unanimity. V2 is research only.
    """
    report=d.get("master") or {};symbol=d.get("symbol")
    bar=num(d.get("bar_time"));age=now-bar-60
    arena=report.get("arena_shadow") or {}
    fresh=(arena.get("status")=="SHADOW_READY" and symbol and
        arena.get("symbol")==symbol==item.get("symbol") and
        arena.get("bar_time")==d.get("bar_time") and
        0<=now-num(arena.get("received_at"),-1)<=60 and 0<=age<60)
    p=report.get("balanced_weight_profile") or {}
    # The profile is generated server-side from a market-scoped cache.
    profile=p if p.get("symbol")==symbol and 0<=now-num(p.get("computed_at"),-1)<=1200 else weight_profile({},symbol,now)
    weights=profile["weights"]
    forecasts={}
    if fresh:
        for n,v in (arena.get("models") or {}).items():
            if n in weights and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and (legacy or abs(v)>.05):
                forecasts[n]=clamp(v,-1.,1.)
    signed=sum(weights[n]*v for n,v in forecasts.items())
    evidence_mass=sum(weights[n]*abs(v) for n,v in forecasts.items())
    # Signed ATR forecasts are not probabilities. Use bounded directional
    # agreement, softly shrink weak aggregate evidence instead of granting a
    # full vote to a tiny prediction or renormalizing absent models.
    conviction=clamp(signed/max(.10,evidence_mass),-1.,1.)
    model_direction="BUY" if signed>1e-9 else "SELL" if signed< -1e-9 else "WAIT"
    ctx=d.get("context") or {};raw=d.get("directional_scores") or {}
    tick=item.get("tick") or {};bid=num(tick.get("bid"));ask=num(tick.get("ask"))
    ref=num(d.get("entry_reference_price"));atr=num(d.get("entry_atr") or ctx.get("atr"))
    valid_price=0<bid<=ask and ref>0 and atr>0
    pm=(settings or {}).get("position_management") or {}
    cap=num(pm.get("entry_max_deviation_atr"),.5)
    sides={}
    for side in SIDES:
        other="SELL" if side=="BUY" else "BUY";sign=1 if side=="BUY" else -1
        own=num(raw.get(side));opp=num(raw.get(other))
        strength=clamp(own)
        margin=clamp((own-opp)/max(abs(own)+abs(opp),1e-9))
        strategy=.5*strength+.5*margin
        quality=clamp(num(ctx.get(side.lower()+"_quality"))/100.)
        move=sign*((bid+ask)*.5-ref)/atr if valid_price else 0.
        momentum=.5+.5*clamp(move/.35,-1.,1.)
        market=.5*quality+.5*momentum
        model=.5+.5*sign*(signed if legacy else conviction)
        # Missing evidence has no usable entry points; the direction check
        # below also prevents strategies overriding the signed AI direction.
        if not legacy and not forecasts:model=0.
        score=.40*strategy+.40*model+.20*market if legacy else .25*strategy+.60*model+.15*market
        candidate=(d.get("directional_candidates") or {}).get(side)
        deviation=abs((ask if side=="BUY" else bid)-ref)/atr if valid_price else None
        checks={"strategy":isinstance(candidate,dict) and 1<=num(candidate.get("strategy_id"))<=200,
            "fresh":0<=age<60,"symbol":bool(symbol) and symbol==item.get("symbol"),
            "no_trap":ctx.get(side.lower()+"_trap") is False,
            "price":valid_price and (pm.get("entry_guard_enabled",True) is not True or 0<cap and deviation<=cap)}
        if not legacy:
            checks["ai_evidence"]=bool(forecasts)
            checks["ai_direction"]=model_direction==side
        failed=[k for k,v in checks.items() if not v]
        sides[side]={"score":round(score,8),"eligible":not failed,"failed_checks":failed,
            "components":{"strategy":strategy,"models":model,"market":market,
                "strength":strength,"margin":margin,"market_quality":quality,"live_momentum":momentum,
                "live_move_atr":move},"price_deviation_atr":deviation,
            "strategy_id":candidate.get("strategy_id") if isinstance(candidate,dict) else None,
            "combination_id":candidate.get("combination_id") if isinstance(candidate,dict) else None}
    ranking=sorted((s for s in SIDES if sides[s]["eligible"]),key=lambda s:sides[s]["score"],reverse=True)
    selected=ranking[0] if ranking else "WAIT"
    tied=len(ranking)==2 and abs(sides[ranking[0]]["score"]-sides[ranking[1]]["score"])<1e-8
    approved=bool(selected in SIDES and not tied and sides[selected]["score"]>=THRESHOLD)
    reason="READY" if approved else "TIE" if tied else "NO_ELIGIBLE_SIDE" if not ranking else "WEIGHTED_SCORE"
    if not legacy and not forecasts:reason="NO_FRESH_AI_EVIDENCE"
    elif not legacy and model_direction=="WAIT":reason="AI_DIRECTION_UNCLEAR"
    elif not legacy and not ranking:reason="AI_SIDE_NOT_ELIGIBLE"
    result={"version":LEGACY_VERSION if legacy else VERSION,"mode":"DEMO_ONLY","selected":selected,
        "direction":selected if approved else "WAIT","approved":approved,"threshold":THRESHOLD,
        "scores":sides,"weights":weights,"weight_statuses":profile["statuses"],
        "used_models":list(forecasts),"signed_forecast":signed,"profile_fresh":profile["profile_fresh"],
        "reason":reason,"model_direction":model_direction,"evidence_mass":evidence_mass,"model_conviction":conviction,
        "allocation":{"strategy":.4,"seven_models":.4,"market":.2} if legacy else {"strategy":.25,"seven_models":.60,"market":.15}}


    if not legacy and str(symbol or "").upper().startswith("XAUUSD"):
        result["gold_strategy_advisory_shadow"]=gold_strategy_advisory_shadow(result,age)
    return result

def gold_strategy_advisory_shadow(trial,age):
    """Gold-only comparison. Never grants authorization or mutates the V3 decision.
    A weak strategy is neutral, not affirmative; stronger strategy evidence
    can still help. Additional candidates require price confirmation, material
    AI evidence and the unchanged eligibility checks. Early 15s lane only.
    """
    side=trial.get("model_direction","WAIT")
    detail=(trial.get("scores") or {}).get(side,{})
    parts=detail.get("components") or {}
    m=clamp(num(parts.get("models")))
    st=clamp(num(parts.get("strategy")))
    market=clamp(num(parts.get("market")))
    momentum=clamp(num(parts.get("live_momentum")))
    mass=num(trial.get("evidence_mass"))
    names=trial.get("used_models") or []
    score=.60*m+.25*max(st,.5)+.15*market
    early=0<=num(age,-1)<=15
    eligible=side in SIDES and detail.get("eligible") is True
    baseline=early and eligible and num(detail.get("score"))>=THRESHOLD
    additional=(early and eligible and score>=THRESHOLD and m>=.95
                and market>=.50 and momentum>=.60 and mass>=.12 and len(names)>=2)
    return {"version":"GOLD_STRATEGY_ADVISORY_SHADOW_V1","mode":"SHADOW_ONLY",
            "execution_authorized":False,"direction":side if baseline or additional else "WAIT",
            "baseline_direction":side if baseline else "WAIT",
            "additional_signal":bool(additional and not baseline),
            "score":round(score,8),"threshold":THRESHOLD,"age_after_close_sec":round(num(age),3),
            "strategy_raw":st,"strategy_advisory":max(st,.5),
            "strategy_id":detail.get("strategy_id"),"combination_id":detail.get("combination_id"),
            "ai_score":m,"market_score":market,"momentum":momentum,"evidence_mass":mass,
            "used_models":list(names),"eligible":bool(eligible)}


def select_demo(d,item,settings,now):
    """Entry only. Keep existing master report and management scores unchanged."""
    from master_entry_review import direct_demo_enabled
    frame=str(item.get("timeframe","M1")).upper().replace("PERIOD_","")
    if frame!="M1" or not direct_demo_enabled(item):return d
    report=d.get("master") or {}
    trial=evaluate(d,item,settings,now)
    # The old V1 candidate is logged separately by the research worker.
    out={**d,"master":{**report,"balanced_entry":trial}}
    side=trial["selected"]
    if side in SIDES:
        c=(d.get("directional_candidates") or {})[side]
        out.update(decision=side,strategy_id=c["strategy_id"],strategy_name=c.get("strategy_name",""),
            combination_id=c.get("combination_id",0),confidence=trial["scores"][side]["score"],
            reason_ar="MASTER AI FIRST DEMO: الذكاء يحدد الاتجاه والاستراتيجية تختار نقطة الدخول")
    else:
        out.update(decision="WAIT",confidence=0.,strategy_id=0,combination_id=0,strategy_name="",
                   reason_ar="MASTER AI FIRST: "+trial["reason"])
    return out


DIRECT_TIMING_VERSION="MASTER_AI_DIRECT_TIMING_SHADOW_V1"

def direct_timing_shadow(trial):
    """Research only: replace absent strategy evidence with observed timing.
    Never assigns a strategy ID or authorizes execution. All other V3 checks stay.
    """
    side=trial.get("model_direction","WAIT")
    detail=(trial.get("scores") or {}).get(side,{})
    parts=detail.get("components") or {}
    baseline=trial.get("direction","WAIT")
    score=None;direction="WAIT";reason="NO_DIRECT_OPPORTUNITY"
    if trial.get("version")==VERSION:
        if baseline in SIDES and trial.get("approved") is True:
            direction=baseline;score=detail.get("score");reason="BASELINE_READY"
        elif side in SIDES and trial.get("used_models") and detail.get("failed_checks")==["strategy"]:
            timing=clamp(num(parts.get("live_move_atr"))/.35)
            score=.60*clamp(num(parts.get("models")))+.25*timing+.15*clamp(num(parts.get("market")))
            if score>=THRESHOLD:
                direction=side;reason="AI_TIMING_CANDIDATE"
            else:reason="TIMING_SCORE_LOW"
    return {"version":DIRECT_TIMING_VERSION,"mode":"SHADOW_ONLY","execution_authorized":False,
            "direction":direction,"baseline_direction":baseline,"baseline_version":VERSION,
            "model_direction":side,"score":score,"threshold":THRESHOLD,"reason":reason,
            "additional_signal":direction in SIDES and baseline=="WAIT",
            "used_models":list(trial.get("used_models") or [])}
