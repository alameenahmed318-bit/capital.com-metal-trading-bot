"""Advanced AI safety pipeline.
Combines regime, uncertainty, execution-cost, drift and meta-label diagnostics.
Default mode is SHADOW: it records diagnostics but cannot change V1-V4 trades.
Set AI_ADVANCED_GATES_MODE=enforce only after Demo validation.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
from ai_regime import detect_regime
from ai_uncertainty import assess as assess_uncertainty
from ai_execution_cost import estimate_cost
from ai_drift_monitor import drift_score
from ai_meta_label import filter_signal
from ai_risk_overlay import compute_multiplier

MODE=os.environ.get("AI_ADVANCED_GATES_MODE","shadow").strip().lower()
if MODE not in {"shadow","enforce"}: MODE="shadow"

def evaluate(df, ai_decision, strategy_signal=None, bid=None, ask=None, reference_features=None, current_features=None, reference_predictions=None, current_predictions=None, portfolio_multiplier=1.0):
    regime=detect_regime(df)
    unc=assess_uncertainty(
        ai_decision.get("confidence",0.0),
        ai_decision.get("buy_probability",0.0),
        ai_decision.get("sell_probability",0.0),
        ai_decision.get("wait_probability",1.0),
        regime.get("confidence",0.0),
        0.0
    )
    cost=estimate_cost(ask if ai_decision.get("signal")=="BUY" else bid,bid,ask,regime.get("atr"))
    drift={"max_feature_psi":0.0,"prediction_shift":0.0,"drift":False}
    if reference_features and current_features:
        from ai_drift_monitor import assess
        drift=assess(reference_features,current_features,reference_predictions,current_predictions)
    ds=drift_score(drift)
    cost_pass=True
    # No fabricated expected edge: cost is diagnostic unless an explicit
    # expected move is supplied by a future validated model.
    meta=filter_signal(strategy_signal,ai_decision.get("signal"),ai_decision.get("confidence",0.0),unc["uncertainty"],regime["regime"],cost_pass)
    mult=compute_multiplier(confidence=ai_decision.get("confidence",0.0),uncertainty=unc["uncertainty"],regime=regime["regime"],regime_confidence=regime["confidence"],drift_score=ds,cost_pass=cost_pass,portfolio_multiplier=portfolio_multiplier)
    enforced_signal=ai_decision.get("signal")
    if MODE=="enforce" and not meta["accepted"]:
        enforced_signal=None
    return {
        "mode":MODE,
        "regime":regime,
        "uncertainty":unc,
        "execution_cost":cost,
        "drift":drift,
        "drift_score":ds,
        "meta_label":meta,
        "risk_multiplier":mult,
        "signal_before":ai_decision.get("signal"),
        "signal_after":enforced_signal,
    }
