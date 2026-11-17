"""Versioned strategy experiments: diagnostic walk-forward directional evaluation.

Every proposal remains inactive until the authenticated owner chooses it.
This is NOT a profit backtest: it omits spreads, slippage, commissions and
fills, and must never authorize trades or adjust owner risk limits.
"""
import math
from datetime import datetime,timezone
from strategy_engine import evaluate_market

MAX_WEIGHTS=200
MAX_COMBO_WEIGHTS=1000

def _finite(value,default=0.0):
    try:
        val=float(value)
        return val if math.isfinite(val) else default
    except (ValueError,TypeError,OverflowError):return default

def sanitize_profile(weights,combinations=None):
    """Accept only bounded finite performance weights, never risk settings."""
    if not isinstance(weights,dict):raise ValueError("strategy weights must be an object")
    if combinations is None:combinations={}
    if not isinstance(combinations,dict):raise ValueError("combination weights must be an object")
    if len(weights)>MAX_WEIGHTS or len(combinations)>MAX_COMBO_WEIGHTS:
        raise ValueError("too many strategy weights")
    cleaned={}
    for k,v in weights.items():
        idx=int(k) if str(k).isdigit() else 0
        value=_finite(v,float("nan"))
        if not 1<=idx<=MAX_WEIGHTS or not math.isfinite(value) or not .5<=value<=1.5:
            raise ValueError("invalid strategy weight")
        cleaned[str(idx)]=round(value,6)
    combos={}
    for k,v in combinations.items():
        idx=int(k) if str(k).isdigit() else 0
        value=_finite(v,float("nan"))
        if not 1<=idx<=MAX_COMBO_WEIGHTS or not math.isfinite(value) or not .85<=value<=1.15:
            raise ValueError("invalid combination weight")
        combos[str(idx)]=round(value,6)
    return {"strategy_weights":cleaned,"combination_weights":combos}

def _candle(row):
    if not isinstance(row,dict):return None
    candle={"time":row.get("candle_time")}
    for output,key in (("open","candle_open"),("high","candle_high"),("low","candle_low"),("close","candle_close"),("tick_volume","tick_volume")):
        val=_finite(row.get(key),float("nan"))
        if not math.isfinite(val):return None
        candle[output]=val
    if candle["high"]<max(candle["open"],candle["close"]) or candle["low"]>min(candle["open"],candle["close"]):return None
    return candle

def replay_directional(candles,profile,max_samples=16):
    """Predict the NEXT candle after a complete historical closed-bar window."""
    clean=[x for row in candles if (x:=_candle(row)) is not None]
    if len(clean)<75:return {"samples":0,"reason":"at least 75 historical candles required","hits":0,"signals":0}
    clean=clean[-220:]
    indices=list(range(66,len(clean)-1))
    if len(indices)>max_samples:
        step=(len(indices)-1)/max(1,max_samples-1)
        indices=sorted(set(indices[round(i*step)] for i in range(max_samples)))
    hits=0;signals=0;details=[]
    for idx in indices:
        observed=clean[idx+1]["close"]-clean[idx]["close"]
        if observed==0:continue
        try:
            result=evaluate_market(clean[max(0,idx-120):idx+1],
                weights=profile.get("strategy_weights") or {},
                combination_weights=profile.get("combination_weights") or {})
        except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError):
            continue
        signal=result.get("decision","WAIT")
        if signal not in ("BUY","SELL"):continue
        signals+=1
        correct=(signal=="BUY" and observed>0) or (signal=="SELL" and observed<0)
        hits+=int(correct)
        details.append({"bar":str(clean[idx]["time"]),"signal":signal,"correct":correct})
    return {"samples":len(indices),"signals":signals,"hits":hits,
        "hit_rate":round(hits/signals,4) if signals else 0.0,
        "coverage":round(signals/len(indices),4) if indices else 0.0,
        "method":"NEXT_CANDLE_DIRECTION_ONLY_NO_EXECUTION_COSTS"}

def compare_profiles(candles,current,candidate):
    baseline=replay_directional(candles,current)
    proposed=replay_directional(candles,candidate)
    return {"baseline":baseline,"candidate":proposed,
        "delta_hit_rate":round(proposed.get("hit_rate",0)-baseline.get("hit_rate",0),4),
        "recommendation":"REVIEW" if proposed["signals"]>=6 and baseline["signals"]>=6 else "INSUFFICIENT_EVIDENCE",
        "auto_activate":False,
        "limitations":"Direction only; not a profitability backtest; trading costs and execution omitted."}
