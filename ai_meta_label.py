"""Meta-label filter for the existing strategy signal.
It never creates a direction; it only accepts/rejects BUY/SELL proposals."""
from __future__ import annotations

def filter_signal(strategy_signal, ai_signal, confidence, uncertainty, regime, cost_pass=True):
    if strategy_signal not in ("BUY","SELL"): return {"accepted":False,"reason":"no_base_signal"}
    if ai_signal not in ("BUY","SELL"): return {"accepted":False,"reason":"ai_abstain"}
    if ai_signal!=strategy_signal: return {"accepted":False,"reason":"direction_disagreement"}
    if float(confidence)<0.58: return {"accepted":False,"reason":"confidence_below_gate"}
    if float(uncertainty)>0.60: return {"accepted":False,"reason":"high_uncertainty"}
    if regime in ("TRANSITION",): return {"accepted":False,"reason":"transition_regime"}
    if not cost_pass: return {"accepted":False,"reason":"execution_cost"}
    return {"accepted":True,"reason":"meta_label_pass"}
