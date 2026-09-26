"""AI risk overlay. It can only reduce risk; it can never increase base risk."""
from __future__ import annotations
import numpy as np

def compute_multiplier(*, confidence, uncertainty, regime, regime_confidence=1.0, drift_score=0.0, cost_pass=True, portfolio_multiplier=1.0, min_multiplier=0.0):
    m=1.0
    c=float(np.clip(confidence,0,1))
    u=float(np.clip(uncertainty,0,1))
    rc=float(np.clip(regime_confidence,0,1))
    ds=float(np.clip(drift_score,0,1))
    if u>=0.60 or ds>=0.85 or not cost_pass: return 0.0
    m*=float(np.clip((c-0.50)/0.25,0.35,1.0))
    m*=float(np.clip(0.55+0.45*rc,0.55,1.0))
    if regime=="HIGH_VOLATILITY": m*=0.65
    elif regime=="TRANSITION": m*=0.75
    m*=float(np.clip(portfolio_multiplier,0,1))
    return float(np.clip(m,min_multiplier,1.0))

def apply(base_size,multiplier):
    return max(0.0,float(base_size))*float(np.clip(multiplier,0,1))
