"""Advanced AI diagnostic pipeline.

AI has broad analytical authority, while validation layers remain advisory.
They NEVER become extra entry blockers. Hard broker/risk protections stay
outside this module.
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
from ai_expected_edge import estimate as estimate_expected_edge
import ai_outcomes
import ai_calibration

MODE = os.environ.get("AI_ADVANCED_GATES_MODE", "enforce").strip().lower()
if MODE not in {"shadow", "enforce"}:
    MODE = "shadow"


def _realized_diagnostics(realized_frame):
    """Return learning diagnostics without turning them into trade gates."""
    result = {
        "samples": 0,
        "calibration": None,
        "drift": None,
        "learning_ready": False,
    }
    if realized_frame is None or realized_frame.empty:
        return result

    result["samples"] = int(len(realized_frame))

    if {"ai_confidence", "outcome_label"}.issubset(realized_frame.columns):
        p = pd.to_numeric(realized_frame["ai_confidence"], errors="coerce")
        y = (pd.to_numeric(realized_frame["outcome_label"], errors="coerce") > 0).astype(float)
        mask = p.notna() & y.notna()
        if int(mask.sum()) >= 20:
            result["calibration"] = ai_calibration.summarize(
                p[mask].to_numpy(), y[mask].to_numpy()
            )

    feature_cols = [
        c for c in (
            "ret1", "ret3", "ret8", "rsi", "atr_pct", "ema9_gap",
            "ema21_gap", "ema50_gap", "ema200_gap", "bb_z", "range_pct",
            "body_pct", "close_pos", "vol_ratio", "htf20_gap", "htf50_gap",
            "htf200_gap"
        )
        if c in realized_frame.columns
    ]
    if len(realized_frame) >= 40 and feature_cols:
        split = max(20, len(realized_frame) // 2)
        ref = {
            c: pd.to_numeric(realized_frame.iloc[:split][c], errors="coerce").dropna().to_numpy()
            for c in feature_cols
        }
        cur = {
            c: pd.to_numeric(realized_frame.iloc[split:][c], errors="coerce").dropna().to_numpy()
            for c in feature_cols
        }
        from ai_drift_monitor import assess
        result["drift"] = assess(ref, cur)

    # Learning readiness is informational only. It never suppresses a trade.
    result["learning_ready"] = bool(result["samples"] >= 200)
    return result


def evaluate(
    df, ai_decision, strategy_signal=None, bid=None, ask=None,
    reference_features=None, current_features=None,
    reference_predictions=None, current_predictions=None,
    portfolio_multiplier=1.0, htf_df=None
):
    regime = detect_regime(df)
    unc = assess_uncertainty(
        ai_decision.get("confidence", 0.0),
        ai_decision.get("buy_probability", 0.0),
        ai_decision.get("sell_probability", 0.0),
        ai_decision.get("wait_probability", 1.0),
        regime.get("confidence", 0.0),
        0.0,
    )

    cost = estimate_cost(
        ask if ai_decision.get("signal") == "BUY" else bid,
        bid, ask, regime.get("atr")
    )

    drift = {"max_feature_psi": 0.0, "prediction_shift": 0.0, "drift": False}
    if reference_features and current_features:
        from ai_drift_monitor import assess
        drift = assess(
            reference_features, current_features,
            reference_predictions, current_predictions
        )
    ds = drift_score(drift)

    atr_pct = None
    if df is not None and len(df) >= 20 and all(k in df.columns for k in ("high", "low", "close")):
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        prev = pd.to_numeric(df["close"], errors="coerce").shift(1)
        tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
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

    realized_frame = None
    try:
        realized_frame = ai_outcomes.realized_training_frame()
    except Exception:
        realized_frame = None

    learning = _realized_diagnostics(realized_frame)
    expected_edge = estimate_expected_edge(
        ai_decision, realized_frame=realized_frame
    )

    # IMPORTANT: execution cost remains advisory. We do not fabricate monetary
    # edge and we do not reject an otherwise valid trade because of diagnostics.
    cost_pass = True

    meta = filter_signal(
        strategy_signal,
        ai_decision.get("signal"),
        ai_decision.get("confidence", 0.0),
        enhanced_uncertainty,
        regime["regime"],
        cost_pass,
    )

    mult = compute_multiplier(
        confidence=ai_decision.get("confidence", 0.0),
        uncertainty=enhanced_uncertainty,
        regime=regime["regime"],
        regime_confidence=regime["confidence"],
        drift_score=ds,
        cost_pass=cost_pass,
        portfolio_multiplier=portfolio_multiplier,
    )

    # Validation remains SHADOW by default. Even when a diagnostic says
    # "bad", it does not become a new trade-count limiter.
    enforced_signal = ai_decision.get("signal")
    if MODE == "enforce" and not meta["accepted"]:
        enforced_signal = None

    # Position-management guidance is advisory here; bot.py combines it with
    # the actual open position and broker-reported P/L before closing anything.
    edge_negative = bool(
        expected_edge.get("available")
        and expected_edge.get("expected_gross_pnl") is not None
        and float(expected_edge.get("expected_gross_pnl")) < 0.0
    )
    position_management = {
        "action_bias": "WAIT",
        "reversal": bool(
            enforced_signal in {"BUY", "SELL"}
            and ai_decision.get("signal") in {"BUY", "SELL"}
            and enforced_signal != ai_decision.get("signal")
        ),
        "high_uncertainty": bool(enhanced_uncertainty >= 0.85),
        "negative_expected_edge": edge_negative,
        "market_regime": str(regime.get("regime") or "UNKNOWN").upper(),
        "multihorizon_agreement": float(multihorizon.get("agreement", 0.0) or 0.0),
        "reason": "AI_MARKET_STATE",
    }
    if position_management["reversal"]:
        position_management["action_bias"] = "EXIT_REVERSAL"
    elif edge_negative and float(ai_decision.get("confidence", 0.0) or 0.0) >= 0.65:
        position_management["action_bias"] = "EXIT_NEGATIVE_EDGE"
    elif position_management["high_uncertainty"]:
        position_management["action_bias"] = "PROTECT"
    elif enforced_signal in {"BUY", "SELL"}:
        position_management["action_bias"] = "HOLD"

    return {
        "mode": MODE,
        "regime": regime,
        "uncertainty": unc,
        "execution_cost": cost,
        "drift": drift,
        "drift_score": ds,
        "multihorizon": multihorizon,
        "conformal": conformal,
        "enhanced_uncertainty": enhanced_uncertainty,
        "expected_edge": expected_edge,
        "learning": learning,
        "meta_label": meta,
        "risk_multiplier": mult,
        "signal_before": ai_decision.get("signal"),
        "signal_after": enforced_signal,
        "entry_filter_policy": "AI_PRIMARY_DIRECTION_ENFORCED",
    }
