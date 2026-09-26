"""Probability calibration diagnostics for AI predictions.
No training side effects and no broker interaction.
"""
from __future__ import annotations
import numpy as np

def reliability_bins(probabilities, outcomes, bins=10):
    p=np.asarray(probabilities,dtype=float); y=np.asarray(outcomes,dtype=float)
    mask=np.isfinite(p)&np.isfinite(y)
    p=p[mask]; y=y[mask]
    rows=[]
    edges=np.linspace(0,1,bins+1)
    for lo,hi in zip(edges[:-1],edges[1:]):
        m=(p>=lo)&((p<hi) if hi<1 else (p<=hi))
        if not m.any(): continue
        rows.append({"lower":float(lo),"upper":float(hi),"count":int(m.sum()),"mean_probability":float(p[m].mean()),"empirical_rate":float(y[m].mean())})
    return rows

def brier_score(probabilities,outcomes):
    p=np.asarray(probabilities,dtype=float); y=np.asarray(outcomes,dtype=float)
    m=np.isfinite(p)&np.isfinite(y)
    return float(np.mean((p[m]-y[m])**2)) if m.any() else None

def expected_calibration_error(probabilities,outcomes,bins=10):
    rows=reliability_bins(probabilities,outcomes,bins)
    n=sum(r["count"] for r in rows)
    return float(sum(r["count"]/n*abs(r["mean_probability"]-r["empirical_rate"]) for r in rows)) if n else None

def summarize(probabilities,outcomes):
    return {"samples":int(len(probabilities)),"brier_score":brier_score(probabilities,outcomes),"ece":expected_calibration_error(probabilities,outcomes),"reliability":reliability_bins(probabilities,outcomes)}

def confidence_multiplier(confidence, ece, min_mult=0.5, max_mult=1.0):
    c=float(np.clip(confidence,0,1))
    if ece is None: return 1.0
    penalty=min(0.5,max(0.0,float(ece)*1.5))
    return float(np.clip(1.0-penalty,min_mult,max_mult))
