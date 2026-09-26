"""Research-grade AI validation helpers: purged walk-forward, cost stress and stability.
This file is offline-only and never places orders.
"""
from __future__ import annotations
import numpy as np

def purged_splits(n_samples, n_splits=5, embargo=4, min_train=100):
    if n_samples<=min_train+embargo+n_splits: return []
    test=max(1,(n_samples-min_train)//n_splits)
    out=[]
    for k in range(n_splits):
        vs=min_train+k*test; ve=min(n_samples,vs+test)
        if ve<=vs: continue
        train_end=max(0,vs-embargo)
        if train_end<min_train: continue
        out.append((np.arange(0,train_end),np.arange(vs,ve)))
    return out

def cost_stress(returns, costs=(0.0,0.0001,0.00025,0.0005,0.001)):
    r=np.asarray(returns,dtype=float); r=r[np.isfinite(r)]
    return [{"cost":float(c),"net_mean":float(np.mean(r-c)) if len(r) else None,"net_positive_rate":float(np.mean((r-c)>0)) if len(r) else None} for c in costs]

def bootstrap_mean(returns, runs=2000, seed=123):
    r=np.asarray(returns,dtype=float); r=r[np.isfinite(r)]
    if len(r)==0: return {"samples":0,"mean":None,"ci95":[None,None]}
    rng=np.random.default_rng(seed)
    means=np.empty(runs)
    for i in range(runs): means[i]=rng.choice(r,size=len(r),replace=True).mean()
    return {"samples":int(len(r)),"mean":float(r.mean()),"ci95":[float(np.quantile(means,.025)),float(np.quantile(means,.975))]}

def summary(returns, probabilities=None, outcomes=None):
    out={"cost_stress":cost_stress(returns),"bootstrap":bootstrap_mean(returns)}
    if probabilities is not None and outcomes is not None:
        from ai_calibration import summarize
        out["calibration"]=summarize(probabilities,outcomes)
    return out
