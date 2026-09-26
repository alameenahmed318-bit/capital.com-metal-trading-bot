"""Uncertainty and abstention diagnostics for the trading AI."""
from __future__ import annotations
import numpy as np

def probability_entropy(probabilities):
    p=np.asarray(probabilities,dtype=float); p=p[np.isfinite(p)]
    if len(p)==0: return None
    p=np.clip(p,1e-9,1); p=p/p.sum()
    return float(-np.sum(p*np.log(p))/np.log(len(p))) if len(p)>1 else 0.0

def model_disagreement(probabilities):
    p=np.asarray(probabilities,dtype=float); p=p[np.isfinite(p)]
    return float(np.std(p)) if len(p) else None

def assess(confidence,buy_probability,sell_probability,wait_probability,regime_confidence=1.0,drift_score=0.0):
    ps=[buy_probability,sell_probability,wait_probability]
    entropy=probability_entropy(ps)
    margin=abs(float(buy_probability)-float(sell_probability))
    uncertainty=0.0
    uncertainty += 0.45*(1.0-float(np.clip(confidence,0,1)))
    uncertainty += 0.25*(float(entropy) if entropy is not None else 1.0)
    uncertainty += 0.15*(1.0-float(np.clip(regime_confidence,0,1)))
    uncertainty += 0.15*float(np.clip(drift_score,0,1))
    return {"uncertainty":float(np.clip(uncertainty,0,1)),"entropy":entropy,"direction_margin":float(margin),"abstain":bool(uncertainty>=0.60 or margin<0.08)}

def should_abstain(result,max_uncertainty=0.60,min_margin=0.08):
    return bool(result.get("uncertainty",1.0)>max_uncertainty or result.get("direction_margin",0.0)<min_margin or result.get("abstain",False))
