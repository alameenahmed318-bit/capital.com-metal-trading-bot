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
from ai_conformal import evaluate as evaluate_conformal
from ai_multihorizon import evaluate as evaluate_multihorizon

MODE=os.environ.get("AI_ADVANCED_GATES_MODE","shadow").strip().lower()
if MODE not in {"shadow","enforce"}: MODE="shadow"

def evaluate(df, ai_decision, strategy_signal=None, bid=None, ask=None, reference_features=None, current_features=None, reference_predictions=None, current_predictions=None, portfolio_multiplier=1.0, htf_df=None):
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

    # New time-safe AI layers. Both operate on closed historical candles only.
    # They remain advisory in shadow mode until Demo validation establishes
    # realized coverage, calibration and execution benefit.
    atr_pct = None
    if df is not None and len(df) >= 20 and all(k in df.columns for k in ("high", "low", "close")):
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        prev = pd.to_numeric(df["close"], errors="coerce").shift(1)
        tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        px = pd.to_numeric(df["close"], errors="coerce")
        if len(atr) >= 2 and pd.notna(atr.iloc[-2]) and pd.notna(px.iloc[-2]) and px.iloc[-2] > 0:
            atr_pct = float(atr.iloc[-2] / px.iloc[-2])

    multihorizon = evaluate_multihorizon(df)
    conformal = evaluate_conformal(df, atr_pct=atr_pct, horizon=4, coverage=0.80)

    enhanced_uncertainty = float(unc["uncertainty"])
    if multihorizon.get("available"):
        enhanced_uncertainty += 0.12 * (1.0 - float(multihorizon.get("agreement", 0.0)))
        enhanced_uncertainty += 0.08 * (1.0 - float(multihorizon.get("directional_coverage", 0.0)))
    if conformal.get("available"):
        enhanced_uncertainty += 0.10 * (1.0 - float(conformal.get("quality", 0.0)))
        if conformal.get("wide"):
            enhanced_uncertainty += 0.12
    enhanced_uncertainty = float(np.clip(enhanced_uncertainty, 0.0, 1.0))

    cost_pass=True
    # No fabricated expected edge: cost is diagnostic unless an explicit
    # expected move is supplied by a future validated model.
    meta=filter_signal(strategy_signal,ai_decision.get("signal"),ai_decision.get("confidence",0.0),enhanced_uncertainty,regime["regime"],cost_pass)
    mult=compute_multiplier(confidence=ai_decision.get("confidence",0.0),uncertainty=enhanced_uncertainty,regime=regime["regime"],regime_confidence=regime["confidence"],drift_score=ds,cost_pass=cost_pass,portfolio_multiplier=portfolio_multiplier)
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
        "multihorizon":multihorizon,
        "conformal":conformal,
        "enhanced_uncertainty":enhanced_uncertainty,
        "meta_label":meta,
        "risk_multiplier":mult,
        "signal_before":ai_decision.get("signal"),
        "signal_after":enforced_signal,
    }
