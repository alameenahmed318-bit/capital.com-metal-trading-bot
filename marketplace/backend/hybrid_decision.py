"""AL-ZOOL live hybrid decision: quantitative strategies plus current-bar AI evidence.

This module only returns decisions. It never places broker orders or changes
owner controls. Unavailable/late model votes are omitted, not treated as vetoes.
"""
import math
from entry_evidence import model_market_fit

DIRECTIONS=("BUY","SELL")
# Relative influence on the directional ranking, not trade limits or approvals.
# Strategy/family/combination/bot evidence remains the main signal.
MODEL_WEIGHTS={"Fast AI":0.10,"Qwen":0.30,"DeepSeek":0.30}


def _num(value,default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (ValueError,TypeError,OverflowError):
        return default


def fuse_live_decision(base,council,model_weights=None):
    """Merge available, authenticated, same-closed-bar AI votes with strategy evidence.

    Requires a real quant strategy for the final side. Never infers trades from
    LLM output alone, never uses prior-bar votes, and never waits for missing AI.
    Returns a *copy* of the decision with explainable fusion diagnostics.
    """
    original=dict(base or {})
    result=dict(original)
    bar=int(_num(original.get("bar_time")))
    quant=str(original.get("decision","WAIT")).upper()
    raw_scores=original.get("directional_scores") or {}
    scores={side:_num(raw_scores.get(side)) for side in DIRECTIONS}
    choices=original.get("directional_candidates") or {}
    eligible={}
    for side in DIRECTIONS:
        candidate=choices.get(side)
        if (isinstance(candidate,dict) and
            1<=int(_num(candidate.get("strategy_id")))<=200):
            eligible[side]=candidate
    # Older cached decisions may not contain the new evidence fields.
    if not eligible and quant in DIRECTIONS and 1<=int(_num(original.get("strategy_id")))<=200:
        eligible={quant:{"strategy_id":int(_num(original["strategy_id"])),
                         "strategy_name":original.get("strategy_name",""),
                         "combination_id":int(_num(original.get("combination_id"))),
                         "reason_ar":original.get("reason_ar","")}}
        scores[quant]=max(scores[quant],max(0.0,_num(original.get("confidence"))))
    combined=dict(scores)
    market_context=original.get("context") if isinstance(original.get("context"),dict) else {}
    # Good supporting candle structure reinforces a fresh vote. A trap or
    # weak market quality reduces its influence rather than imposing a veto.
    market_factors={side:model_market_fit(market_context,side) for side in DIRECTIONS}
    same_bar=isinstance(council,dict) and int(_num(council.get("bar_time"))) == bar and bar>0
    raw_votes=(council.get("votes") or {}) if same_bar else {}
    contributions=[]
    active_weight=0.0
    for model,weight in (MODEL_WEIGHTS if model_weights is None else model_weights).items():
        v=raw_votes.get(model) or {}
        status=str(v.get("status","UNAVAILABLE"))
        side=str(v.get("direction","WAIT")).upper()
        confidence=_num(v.get("confidence"),-1)
        used=(same_bar and status=="READY" and side in DIRECTIONS and
              0<=confidence<=1 and confidence>0)
        effective_weight=weight*market_factors[side] if used else 0.0
        points=round(effective_weight*confidence,6) if used else 0.0
        if used:
            combined[side]+=points
            active_weight+=effective_weight
        contributions.append({"model":model,"status":status,"direction":side,
                              "confidence":confidence if 0<=confidence<=1 else None,
                              "relative_weight":weight,
                              "market_quality_factor":market_factors.get(side) if side in DIRECTIONS else None,
                              "effective_weight":round(effective_weight,6),
                              "points":points,"used":bool(used),
                              "reason_ar":str(v.get("reason_ar",""))[:180]})
    ranking=sorted(eligible,key=lambda side:combined[side],reverse=True)
    if not ranking:
        winner="WAIT"
        why="لا توجد استراتيجية اتجاهية مؤهلة؛ تصويت AI لا يفتح صفقة بمفرده"
    elif len(ranking)==2 and abs(combined[ranking[0]]-combined[ranking[1]])<1e-9:
        winner="WAIT"
        why="تعادل أدلة BUY وSELL بعد دمج أصوات الذكاء"
    else:
        winner=ranking[0]
        why=("ترجيح هجين من الاستراتيجيات والتراكيب والبوتات مع أصوات "
             "النماذج المتاحة لهذه الشمعة")
    if winner in DIRECTIONS:
        chosen=eligible[winner]
        result["decision"]=winner
        result["strategy_id"]=chosen["strategy_id"]
        result["strategy_name"]=chosen.get("strategy_name","")
        result["combination_id"]=chosen.get("combination_id",0)
        result["confidence"]=round(max(0.0,min(1.0,combined[winner]/(1.0+active_weight))),6)
        result["reason_ar"]=(f"HYBRID {winner} | {why} | BUY={combined['BUY']:.3f}, "
                             f"SELL={combined['SELL']:.3f} | "
                             f"{chosen.get('reason_ar','')[:150]}")
    else:
        result["decision"]="WAIT"
        result["confidence"]=0.0
        result["strategy_id"]=0
        result["strategy_name"]=""
        result["combination_id"]=0
        result["reason_ar"]=why
    result["hybrid"]={
        "bar_time":bar,
        "base_direction":quant,"effective_direction":winner,
        "direction_changed":winner!=quant,
        "quantitative_scores":{side:round(scores[side],6) for side in DIRECTIONS},
        "combined_scores":{side:round(combined[side],6) for side in DIRECTIONS},
        "quality_factors":market_factors,
        "closed_candle_pressure":market_context.get("last_closed_candle_pressure",0.0),
        "score_margin":round(abs(combined["BUY"]-combined["SELL"]),6),
        "reversal_guard":"SOFT_SCORE_NO_EXTRA_WAIT",
        "model_contributions":contributions,
        "model_votes_used":sum(x["used"] for x in contributions),
        "model_votes_missing":sum(not x["used"] for x in contributions),
        "reason_ar":why,
        "mode":"HYBRID_SOFT_EVIDENCE"
    }
    return result
