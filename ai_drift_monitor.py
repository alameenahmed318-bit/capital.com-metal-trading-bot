"""Feature/prediction/outcome drift monitoring.
Uses PSI for numerical distributions and a simple prediction-rate shift.
"""
from __future__ import annotations
import numpy as np

def _clean(x):
    a=np.asarray(x,dtype=float); return a[np.isfinite(a)]

def psi(reference,current,bins=10):
    r=_clean(reference); c=_clean(current)
    if len(r)<20 or len(c)<20: return None
    edges=np.unique(np.quantile(r,np.linspace(0,1,bins+1)))
    if len(edges)<3: return 0.0
    edges[0],edges[-1]=-np.inf,np.inf
    rh,_=np.histogram(r,bins=edges); ch,_=np.histogram(c,bins=edges)
    rp=np.maximum(rh/rh.sum(),1e-6); cp=np.maximum(ch/ch.sum(),1e-6)
    return float(np.sum((cp-rp)*np.log(cp/rp)))

def prediction_shift(reference,current):
    r=_clean(reference); c=_clean(current)
    if len(r)==0 or len(c)==0: return None
    return float(abs(r.mean()-c.mean()))

def assess(reference_features,current_features,reference_predictions=None,current_predictions=None):
    feature_scores={}
    for k in set(reference_features or {}) & set(current_features or {}):
        v=psi(reference_features[k],current_features[k])
        if v is not None: feature_scores[k]=v
    pred=prediction_shift(reference_predictions,current_predictions) if reference_predictions is not None and current_predictions is not None else None
    max_psi=max(feature_scores.values()) if feature_scores else 0.0
    return {"feature_psi":feature_scores,"max_feature_psi":float(max_psi),"prediction_shift":pred,"drift":bool(max_psi>=0.20 or (pred is not None and pred>=0.15))}

def drift_score(report):
    return float(np.clip(max(float(report.get("max_feature_psi",0.0))/0.50, float(report.get("prediction_shift") or 0.0)/0.30),0,1))
