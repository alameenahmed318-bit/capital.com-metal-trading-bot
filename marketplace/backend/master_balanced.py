"""40/40/20 entry-only demo pilot. Scores are not win probabilities."""
import math
from market_symbols import same_market_symbol

VERSION="MASTER_BALANCED_DEMO_V2"
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

def evaluate(d,item,settings,now):
    """Rank BUY/SELL on the same scale, only for actual eligible strategies.
    Seven advisers contribute 40% in total. Missing/stale values are neutral
    (zero signed movement), never treated as votes of agreement.
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
            if n in weights and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                forecasts[n]=clamp(v,-1.,1.)
    signed=sum(weights[n]*v for n,v in forecasts.items())
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
        model=.5+.5*sign*signed
        score=.40*strategy+.40*model+.20*market
        candidate=(d.get("directional_candidates") or {}).get(side)
        deviation=abs((ask if side=="BUY" else bid)-ref)/atr if valid_price else None
        checks={"strategy":isinstance(candidate,dict) and 1<=num(candidate.get("strategy_id"))<=200,
            "fresh":0<=age<60,"symbol":bool(symbol) and symbol==item.get("symbol"),
            "no_trap":ctx.get(side.lower()+"_trap") is False,
            "price":valid_price and (pm.get("entry_guard_enabled",True) is not True or 0<cap and deviation<=cap)}
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
    return {"version":VERSION,"mode":"DEMO_ONLY","selected":selected,
        "direction":selected if approved else "WAIT","approved":approved,"threshold":THRESHOLD,
        "scores":sides,"weights":weights,"weight_statuses":profile["statuses"],
        "used_models":list(forecasts),"signed_forecast":signed,"profile_fresh":profile["profile_fresh"],
        "reason":"READY" if approved else "TIE" if tied else "NO_ELIGIBLE_SIDE" if not ranking else "WEIGHTED_SCORE",
        "allocation":{"strategy":.4,"seven_models":.4,"market":.2}}

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
            reason_ar="MASTER BALANCED DEMO: تقييم الاستراتيجيات والذكاء السبعة والسعر")
    else:
        out.update(decision="WAIT",confidence=0.)
    return out
