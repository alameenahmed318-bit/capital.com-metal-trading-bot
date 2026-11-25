"""Smart profit advice: direction evidence only; no order authorization."""
import math
def number(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError,OverflowError):
        return None

def master_bias(decision, fresh):
    if not fresh or not isinstance(decision,dict):
        return {"smart_profit_bias":0.0,"smart_profit_bias_valid":False}
    master=decision.get("master") or {}
    scores=master.get("scores") or {}
    buy,sell=number(scores.get("BUY")),number(scores.get("SELL"))
    if master.get("mode")!="MASTER" or buy is None or sell is None:
        return {"smart_profit_bias":0.0,"smart_profit_bias_valid":False}
    bias=max(-1.,min(1.,(buy-sell)/max(1.,abs(buy)+abs(sell))))
    return {"smart_profit_bias":round(bias,6),"smart_profit_bias_valid":True}


def profit_settings_for_terminal(settings, snapshot):
    """Keep fixed settings intact; prevent legacy EA fixed closes in SMART mode."""
    if (settings.get("position_management") or {}).get("profit_close_mode") != "SMART":
        return settings
    if (snapshot or {}).get("smart_profit_capable") is True:
        return settings
    return {**settings,"single_enabled":False,"group_enabled":False}
