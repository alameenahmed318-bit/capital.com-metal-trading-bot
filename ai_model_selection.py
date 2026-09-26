"""Champion/challenger model governance.

This module never promotes a model merely because it has more samples.
Promotion requires explicit validation metrics and the registry policy.
"""
def promotion_decision(candidate: dict, champion: dict | None, policy: dict) -> dict:
    reasons=[]
    folds=int(candidate.get("walk_forward_folds",0) or 0)
    if folds < int(policy.get("min_walk_forward_folds",5)):
        reasons.append("insufficient_walk_forward_folds")
    if policy.get("require_calibration",True) and not candidate.get("calibration_ok",False):
        reasons.append("calibration_required")
    if policy.get("require_drift_check",True) and not candidate.get("drift_ok",False):
        reasons.append("drift_check_required")
    if policy.get("require_execution_cost_check",True) and not candidate.get("execution_cost_ok",False):
        reasons.append("execution_cost_check_required")
    if policy.get("require_realized_broker_labels",True) and not candidate.get("realized_labels_ok",False):
        reasons.append("realized_broker_labels_required")

    if champion:
        cexp=candidate.get("expectancy")
        hexp=champion.get("expectancy")
        if cexp is not None and hexp is not None and float(cexp) <= float(hexp):
            reasons.append("no_expectancy_improvement")
        cdd=candidate.get("max_drawdown")
        hdd=champion.get("max_drawdown")
        if cdd is not None and hdd is not None and float(cdd) > float(hdd):
            reasons.append("drawdown_degradation")

    return {
        "promote": len(reasons)==0,
        "reasons": reasons,
        "decision": "PROMOTE" if not reasons else "HOLD"
    }
