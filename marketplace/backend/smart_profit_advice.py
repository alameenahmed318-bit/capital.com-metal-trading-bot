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


def closure_feedback(deals,symbol):
    """Learn observed closure outcomes separately by market and entry side.
    Missing fills/fees or ambiguous volume do not enter the learning sample.
    This is outcome evidence, not proof of an alternative exit or SMART mode.
    """
    from market_symbols import same_market_symbol
    groups={}
    seen=set()
    for d in deals or []:
        if not isinstance(d,dict) or not same_market_symbol(d.get("symbol"),symbol):continue
        ticket=str(d.get("ticket") or "");pid=str(d.get("position_id") or "")
        if not ticket or not pid or ticket in seen:continue
        seen.add(ticket);groups.setdefault(pid,[]).append(d)
    samples={"BUY":[],"SELL":[]}
    incomplete=0
    for ds in groups.values():
        ins=[d for d in ds if number(d.get("entry"))==0]
        outs=[d for d in ds if number(d.get("entry")) in (1,3)]
        if not outs:continue
        vols=[number(d.get("volume")) for d in ds]
        costs=[number(d.get(k)) for d in ds for k in ("profit","commission","swap","fee")]
        times=[number(d.get("time")) for d in ds]
        sides={number(d.get("type",d.get("deal_type"))) for d in ins}
        if (not ins or len(ins)+len(outs)!=len(ds) or any(v is None or v<=0 for v in vols)
            or None in costs or any(t is None or t<=0 for t in times) or sides not in ({0.},{1.})
            or abs(sum(number(d["volume"]) for d in ins)-sum(number(d["volume"]) for d in outs))>1e-7
            or max(number(d["time"]) for d in ins)>min(number(d["time"]) for d in outs)):
            incomplete+=1;continue
        side="BUY" if sides=={0.} else "SELL"
        samples[side].append((max(number(d["time"]) for d in outs),sum(costs)))
    result={}
    for side,values in samples.items():
        values.sort();cut=int(len(values)*.7)
        def stats(items):
            nets=[n for _,n in items]
            return {"positions":len(nets),"positive":sum(n>0 for n in nets),
                "net":round(sum(nets),4),"mean_net":round(sum(nets)/len(nets),4) if nets else None}
        result[side]={"training":stats(values[:cut]),"validation":stats(values[cut:]),
            "all":stats(values),"status":"OUTCOME_EVIDENCE" if cut>=30 and len(values)-cut>=15 else "INSUFFICIENT"}
    return {"symbol":symbol,"mode":"RESEARCH_ONLY","metric":"COMPLETE_POSITION_NET_NOT_SMART_EXIT_CAUSAL",
            "sides":result,"incomplete_positions":incomplete,"execution_authorized":False}
