"""AI quality gate for the primary AI signal.
The AI model owns direction; this layer may only abstain when confidence/uncertainty is poor."""
from __future__ import annotations

def filter_signal(strategy_signal, ai_signal, confidence, uncertainty, regime, cost_pass=True):
    # strategy_signal is retained for API compatibility only. The primary AI
    # signal is the sole directional authority; no legacy strategy vote may
    # override or cancel a valid AI BUY/SELL decision.
    if ai_signal not in ("BUY","SELL"): return {"accepted":False,"reason":"ai_abstain"}
    if float(confidence)<0.54: return {"accepted":False,"reason":"confidence_below_gate"}
    if float(uncertainty)>0.75: return {"accepted":False,"reason":"high_uncertainty"}
    if regime in ("TRANSITION",): return {"accepted":False,"reason":"transition_regime"}
    if not cost_pass: return {"accepted":False,"reason":"execution_cost"}
    return {"accepted":True,"reason":"meta_label_pass"}
