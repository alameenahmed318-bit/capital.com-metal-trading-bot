"""Bounded, automatic closed-trade feedback over owner-selected strategy profiles.

Versioned owner profile remains the base. Newly observed attributed closed
positions supply a conservative *online* adjustment, never a trade permit,
risk setting change, model re-training claim or unreviewed version activation.
"""
import math

STRATEGY_IDS=200
COMBINATION_IDS=1000
STRATEGY_OVERLAY=0.40  # Live weights 0.5..1.5 => at most 20% adjustment
COMBINATION_OVERLAY=0.40 # Live weights 0.85..1.15 => at most 6% adjustment


def _bounded(value, default, low, high):
    try:
        x=float(value)
        if not math.isfinite(x):return default
        return max(low,min(high,x))
    except (ValueError,TypeError,OverflowError):
        return default


def _merged_weights(base,live,stats,key,maximum,lo,hi,blend):
    base=base if isinstance(base,dict) else {}
    live=live if isinstance(live,dict) else {}
    items=stats if isinstance(stats,list) else []
    eligible={str(int(x[key])) for x in items if isinstance(x,dict) and
        x.get("evidence")=="ELIGIBLE" and
        str(x.get(key,"")).isdigit() and 1<=int(x[key])<=maximum}
    results={}
    changed=[]
    for ident in range(1,maximum+1):
        sid=str(ident)
        baseline=_bounded(base.get(sid),1.0,lo,hi)
        observed=_bounded(live.get(sid),1.0,lo,hi)
        effective=baseline*(1.0+blend*(observed-1.0)) if sid in eligible else baseline
        effective=round(_bounded(effective,1.0,lo,hi),6)
        if abs(effective-1.0)>1e-7:results[sid]=effective
        if sid in eligible and abs(effective-baseline)>1e-6:
            changed.append({"id":ident,"base":round(baseline,6),
                            "learned":round(observed,6),"effective":effective,
                            "closed_trades":next((int(x.get("trades",0)) for x in items
                                if str(x.get(key))==sid),0)})
    changed.sort(key=lambda x:x["closed_trades"],reverse=True)
    return results,changed,len(eligible)


def apply_live_learning(active_profile,learning_profile):
    """Use new attributed closed trades every bar; no new WAIT threshold."""
    active=active_profile if isinstance(active_profile,dict) else {}
    learned=learning_profile if isinstance(learning_profile,dict) else {}
    strategies,changed_s,eligible_s=_merged_weights(
        active.get("strategy_weights"),learned.get("weights"),
        learned.get("strategies"),"strategy_id",STRATEGY_IDS,.5,1.5,STRATEGY_OVERLAY)
    combos,changed_c,eligible_c=_merged_weights(
        active.get("combination_weights"),learned.get("combination_weights"),
        learned.get("combinations"),"combination_id",COMBINATION_IDS,.85,1.15,
        COMBINATION_OVERLAY)
    changed=bool(changed_s or changed_c)
    return {"strategy_weights":strategies,"combination_weights":combos,
        "diagnostics":{
            "status":"APPLIED" if changed else
                     "INSUFFICIENT_EVIDENCE" if not (eligible_s or eligible_c)
                     else "NEUTRAL_NO_CHANGE",
            "method":"BOUNDED_LIVE_CLOSED_TRADES_OVER_ACTIVE_VERSION",
            "active_version_preserved":True,
            "processed_positions":int(learned.get("processed_positions") or 0),
            "closed_positions":int(learned.get("closed_positions") or 0),
            "unmatched_positions":int(learned.get("unmatched_positions") or 0),
            "coverage_percent":float(learned.get("coverage_percent") or 0),
            "eligible_strategies":eligible_s,"eligible_combinations":eligible_c,
            "applied_strategies":len(changed_s),"applied_combinations":len(changed_c),
            "last_closed_at":learned.get("last_closed_at"),
            "last_attributed_at":learned.get("last_attributed_at"),
            "changed_strategies":changed_s[:12],"changed_combinations":changed_c[:12],
            "risk_settings_changed":False,
            "reason_ar":("تُطبّق أوزان التعلم من صفقات MT5 المغلقة تلقائياً بحدود محسوبة"
                         if changed else
                         "تم تحليل الصفقات، لكن الأدلة غير كافية أو أثر الأوزان ما زال محايداً")}}
