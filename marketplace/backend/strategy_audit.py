"""Read-only, no-lookahead, directional audit of ALL 200 strategies and 1000 groups.

This is NOT a fill-level trading backtest. A winner means the next closed
candle moved in the predicted direction, not that a broker order made money.
Training and validation are distinct chronological windows. No profile is
modified and no orders are authorized.
"""
from collections import defaultdict
from strategy_engine import STRATEGIES,COMBINATIONS,evaluate_market
from strategy_experiments import _candle

def _report_item(item,measure,minimum=5):
    training=measure["training"]
    validation=measure["validation"]
    n=validation["signals"]
    hits=validation["hits"]
    rate=hits/n if n else None
    enough=(training["signals"]>=minimum and n>=3)
    return {
        "id":item["id"],"name":item["name"],"signals":training["signals"]+n,
        "training_signals":training["signals"],"training_hit_rate":
            round(training["hits"]/training["signals"],3) if training["signals"] else None,
        "validation_signals":n,"validation_hits":hits,
        "validation_hit_rate":round(rate,3) if rate is not None else None,
        "evidence":"DIRECTIONAL_CANDIDATE" if enough and rate>=.55 else
                   "WEAK_VALIDATION" if enough and rate<.45 else "INSUFFICIENT_EVIDENCE",
        "trade_profit_proven":False,
    }

def audit_strategies(candles,max_samples=18):
    """Evaluate each member and group on the next candle of historical data.

    Use 66+ previous candles for every signal; the target candle is NEVER part
    of the evaluation window. With sparse data return insufficient evidence.
    """
    clean=[c for row in candles if (c:=_candle(row)) is not None]
    if len(clean)<75:
        return {"status":"INSUFFICIENT_HISTORY","candles":len(clean),
                "strategies_total":200,"combinations_total":1000,
                "strategy_leaders":[],"combination_leaders":[],
                "training_points":0,"validation_points":0,"auto_activate":False}
    clean=clean[-260:]
    start=66
    indices=list(range(start,len(clean)-1))
    if len(indices)>max_samples:
        step=(len(indices)-1)/max(1,max_samples-1)
        indices=sorted(set(indices[round(i*step)] for i in range(max_samples)))
    n_train=max(1,int(len(indices)*.60))
    if n_train>=len(indices):n_train=len(indices)-1
    tally=defaultdict(lambda:{"training":{"signals":0,"hits":0},"validation":{"signals":0,"hits":0}})
    for j,idx in enumerate(indices):
        target=clean[idx+1]["close"]-clean[idx]["close"]
        if not target:continue
        expected="BUY" if target>0 else "SELL"
        try:
            evaluated=evaluate_market(clean[max(0,idx-120):idx+1])
        except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError):
            continue
        segment="training" if j<n_train else "validation"
        for item in evaluated["votes"]:
            direction=item["signal"]
            if direction not in ("BUY","SELL"):continue
            bucket=tally[("strategy",item["id"])][segment]
            bucket["signals"]+=1
            bucket["hits"]+=int(direction==expected)
        for item in evaluated["combination_votes"]:
            direction=item["signal"]
            if direction not in ("BUY","SELL"):continue
            bucket=tally[("combination",item["id"])][segment]
            bucket["signals"]+=1
            bucket["hits"]+=int(direction==expected)
    def rating(item):
        return (1 if item["evidence"]=="DIRECTIONAL_CANDIDATE" else 0,
                item["validation_signals"],
                item["validation_hit_rate"] if item["validation_hit_rate"] is not None else -1)
    strategies=[_report_item(x,tally[("strategy",x["id"])]) for x in STRATEGIES]
    combinations=[_report_item(x,tally[("combination",x["id"])]) for x in COMBINATIONS]
    strategies.sort(key=rating,reverse=True)
    combinations.sort(key=rating,reverse=True)
    return {"status":"DIAGNOSTIC_ONLY","candles":len(clean),
        "strategies_total":len(STRATEGIES),"combinations_total":len(COMBINATIONS),
        "strategy_candidates":sum(x["evidence"]=="DIRECTIONAL_CANDIDATE" for x in strategies),
        "combination_candidates":sum(x["evidence"]=="DIRECTIONAL_CANDIDATE" for x in combinations),
        "training_points":n_train,"validation_points":len(indices)-n_train,
        "strategy_leaders":strategies[:30],"combination_leaders":combinations[:30],
        "weight_changes_applied":0,"auto_activate":False,
        "validation_rule":"Chronological 60/40; >=5 training signals and >=3 validation signals; directional hit >=55%",
        "limitations":"Not profitable-trade evidence. Next-close direction only; no entry/exit, spread, slippage, stop loss or fees. Small samples. Reused members are correlated."
    }
