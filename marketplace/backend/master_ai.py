"""Unified AL-ZOOL master policy, not an additional unanimity gate.

Quantitative strategy eligibility stays authoritative. A bounded local
controller uses available current-bar evidence and the GPT-OSS reviewer,
without blocking on network calls. Historical confidence calibration is
used only with sufficient chronological validation, not as profit proof.
"""
from hybrid_decision import fuse_live_decision
from market_symbols import supported_market_symbol, same_market_symbol
import math
import time
import adaptive_memory
from model_arena_api import LIVE_MODELS

# Equal, deliberately small initial weights; pilot results are not proof.
ARENA_WEIGHTS={name:.02 for name in LIVE_MODELS}

def arena_votes(base,evidence):
    if not isinstance(evidence,dict) or evidence.get('status')!='SHADOW_READY':return {}
    if evidence.get('symbol')!=base.get('symbol') or evidence.get('bar_time')!=base.get('bar_time'):return {}
    if not 60<=time.time()-num(base.get('bar_time'))<=120 or not 0<=time.time()-num(evidence.get('received_at'))<=60:return {}
    out={}
    for name,v in (evidence.get('models') or {}).items():
        if name not in ARENA_WEIGHTS or isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or abs(v)<=.05:continue
        out[name]={'status':'READY','direction':'BUY' if v>0 else 'SELL','confidence':min(1.,abs(v)),'reason_ar':'توقع حركة 3 دقائق بوحدة ATR؛ قوة الإشارة وليست احتمال ربح'}
    return out


MODEL_NAMES={"Fast AI":"Fast AI","Qwen":"qwen2.5:1.5b",
             "DeepSeek":"deepseek-r1:1.5b","GPT-OSS 120B":"GPT-OSS 120B"}
WEIGHTS={"Fast AI":.10,"Qwen":.30,"DeepSeek":.30,"GPT-OSS 120B":.40}

def num(value,default=0.0):
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except (ValueError,TypeError,OverflowError):return default

def learned_factors(feedback,symbol):
    if (not isinstance(feedback,dict) or feedback.get("status")!="RESEARCH_ONLY"
        or not same_market_symbol(feedback.get("symbol"),symbol)
        or not 0<=time.time()-num(feedback.get("updated_at_unix"))<=600):
        return {}
    result={}
    for row in feedback.get("models") or []:
        if (isinstance(row,dict) and row.get("status")=="CALIBRATION_CANDIDATE"
            and num(row.get("training"))>=80 and num(row.get("validation"))>=40
            and num(row.get("baseline_brier"),-1)>num(row.get("candidate_brier"),2)+.005):
            # Brier calibration is confidence evidence, not trading-profit
            # evidence. Bound its influence and never boost raw confidence.
            factor=max(.65,min(1.0,num(row.get("candidate_confidence_factor"),1.0)))
            result[row.get("model")]=factor
    return result

def decide(base,council,feedback=None,arena_evidence=None):
    symbol=(base or {}).get("symbol")
    if not supported_market_symbol(symbol):
        return {**(base or {}),"decision":"WAIT","confidence":0.0,
                "strategy_id":0,"combination_id":0,
                "reason_ar":"MASTER AI: لا يوجد رمز سوق صالح",
                "master":{"mode":"MASTER","source":"NO_MARKET","direction":"WAIT"}}
    council=dict(council or {})
    votes=dict(council.get("votes") or {})
    cloud=council.get("groq_advisory")
    if isinstance(cloud,dict):votes["GPT-OSS 120B"]=dict(cloud)
    factors=learned_factors(feedback,symbol)
    original={}
    adjusted={}
    for label,model in MODEL_NAMES.items():
        vote=dict(votes.get(label) or {})
        original[label]=vote.get("confidence")
        factor=factors.get(model,1.0) if label not in ("Qwen","DeepSeek") or vote.get("model_id")==model else 1.0
        raw=num(vote.get("confidence"),-1)
        if 0<=raw<=1:vote["confidence"]=raw*factor
        adjusted[label]=vote
    extra=arena_votes(base,arena_evidence)
    fusion_council={**council,"votes":adjusted}
    if extra:
        same_bar=num(council.get("bar_time"))==num(base.get("bar_time"))
        fusion_council={**council,"bar_time":base.get("bar_time"),"votes":{**(adjusted if same_bar else {}),**extra}}
    live_weights={**WEIGHTS,**{n:ARENA_WEIGHTS[n] for n in extra}}
    result=fuse_live_decision(base,fusion_council,model_weights=live_weights)
    fusion=result.get("hybrid") or {}
    inputs=fusion.get("model_contributions") or []
    for row in inputs:
        label=row["model"]
        row["original_confidence"]=original.get(label)
        raw=num(original.get(label),-1)
        row["calibration_factor"]=round(num(adjusted.get(label,{}).get("confidence"))/raw,4) if raw>0 else 1.0
        if label in extra:row["original_confidence"]=extra[label]["confidence"]
    cloud_used=any(x["used"] and x["model"]=="GPT-OSS 120B" for x in inputs)
    result["reason_ar"]="MASTER AI | "+str(result.get("reason_ar",""))
    result["master"]={
        "mode":"MASTER","version":"MASTER_V1","direction":result["decision"],
        "source":"CLOUD_ASSISTED" if cloud_used else "LOCAL_CONTROLLER",
        "cloud_status":(cloud or {}).get("status","NOT_RECEIVED"),
        "unanimity_required":False,"reason_ar":result["reason_ar"],
        "models":inputs,"scores":fusion.get("combined_scores",{}),
        "quant_direction":fusion.get("base_direction"),
        "strategy_id":result.get("strategy_id"),"combination_id":result.get("combination_id"),
        "confidence":result.get("confidence"),"bar_time":result.get("bar_time"),
        "calibration_applied":any(x["used"] and x["calibration_factor"]<1 for x in inputs),
        "arena_advisory":{"mode":"BOUNDED_LIVE_ADVICE","configured":list(LIVE_MODELS),"received":list((arena_evidence or {}).get("models",{})) if isinstance(arena_evidence,dict) else [],"used":[x["model"] for x in inputs if x["model"] in extra and x["used"]],"total_points":round(sum(x["points"] for x in inputs if x["model"] in extra),6),"max_weight":sum(ARENA_WEIGHTS.values()),"execution_authorized":False},
        "execution_authorized":False}
    # Research comparison only: never replace live decision, confidence or lot.
    if (isinstance(feedback,dict) and feedback.get("status")=="RESEARCH_ONLY"
        and same_market_symbol(feedback.get("symbol"),symbol)
        and 0<=time.time()-num(feedback.get("updated_at_unix"))<=600):
        performance=(feedback.get("adaptive_memory") or {}).get("model_performance") or {}
        weights=adaptive_memory.candidate_weights(WEIGHTS,performance,votes)
        changed=any(abs(weights[k]-WEIGHTS[k])>1e-8 for k in WEIGHTS)
        shadow=fuse_live_decision(base,fusion_council,model_weights={**weights,**{n:ARENA_WEIGHTS[n] for n in extra}}) if changed else result
        result["master"]["adaptive_shadow"]={"mode":"SHADOW_ONLY","version":adaptive_memory.VERSION,
            "weights":weights,"candidate_direction":shadow.get("decision"),
            "candidate_confidence":shadow.get("confidence"),"changed":changed,
            "execution_authorized":False}
    return result

def management_support(decision,side):
    """Same master ranking drives action timing; no double-counting votes."""
    report=decision.get("master") or {}
    scores=report.get("scores") or {}
    if side not in ("BUY","SELL") or not scores:return 0.0
    opposite="SELL" if side=="BUY" else "BUY"
    difference=num(scores.get(side))-num(scores.get(opposite))
    scale=max(1.0,abs(num(scores.get(side)))+abs(num(scores.get(opposite))))
    return max(-1.0,min(1.0,difference/scale))
