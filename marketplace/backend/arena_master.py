"""Read-only advisory bridge. Never changes an entry or position permit."""
import math,time,threading
from collections import OrderedDict
from market_symbols import supported_market_symbol
from model_arena_api import LIVE_MODELS,HYBRID_ONLY_MODELS
from hybrid_demo_pilot import allowed as hybrid_allowed
ACTIONS=("ADDON","COOLING","RECOVERY")
LOCK=threading.Lock()
CACHE=OrderedDict()
def num(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) else default
    except (ValueError,TypeError,OverflowError):return default
def context(cid,item):
    d=item.get("strategy_decision") or {}
    bar=int(num(d.get("bar_time")))
    symbol=item.get("symbol")
    stamp=item.get("received_at")
    if not supported_market_symbol(symbol) or str(item.get("timeframe")).upper() not in ("M1","PERIOD_M1") or not stamp or not 0<=time.time()-stamp.timestamp()<=30 or not 60<=time.time()-bar<=120:
        return {"status":"NO_CURRENT_M1"}
    tick=item.get("tick") or {}
    bid=num(tick.get("bid"));ask=num(tick.get("ask"));point=num(tick.get("point_size") or (item.get("metadata") or {}).get("point_size"))
    if bid<=0 or ask<bid or point<=0:return {"status":"NO_VALID_QUOTE"}
    bars=sorted((c for c in item.get("closed_candles") or [] if isinstance(c,dict) and num(c.get("time"))<=bar),key=lambda c:num(c.get("time")))[-61:]
    if len(bars)!=61 or int(num(bars[-1].get("time")))!=bar:return {"status":"NO_HISTORY"}
    if any(num(b["time"])-num(a["time"])!=60 for a,b in zip(bars,bars[1:])):return {"status":"GAP"}
    # Historical spread in telemetry is not available per candle. Only the
    # most recent feature uses the current quote spread, explicitly a proxy.
    bars=[{**c,"spread_points":(ask-bid)/point,"point_size":point} for c in bars]
    return {"status":"READY","client":cid,"symbol":symbol,"bar_time":bar,"bars":bars,"spread_source":"CURRENT_QUOTE_PROXY"}
def accept(cid,item,body):
    ctx=context(cid,item)
    if ctx.get("status")!="READY" or body.get("symbol")!=ctx["symbol"] or body.get("bar_time")!=ctx["bar_time"]:
        return {"accepted":False,"reason":"STALE_OR_WRONG_BAR"}
    models={}
    accepted_names=LIVE_MODELS+(HYBRID_ONLY_MODELS if hybrid_allowed(item) else ())
    for name,v in (body.get("models") or {}).items():
        if name in accepted_names and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
            models[name]=round(max(-6,min(6,v)),5)
    actions={}
    for action in ACTIONS:
        sides=(body.get("actions") or {}).get(action,{})
        if not isinstance(sides,dict):continue
        valid={}
        for side in ("BUY","SELL"):
            v=sides.get(side)
            if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                valid[side]=round(max(-6,min(6,v)),5)
        if valid:actions[action]=valid
    if not models and not any(actions.values()):return {"accepted":False,"reason":"NO_PREDICTIONS"}
    key=(cid,ctx["symbol"],ctx["bar_time"])
    with LOCK:
        previous=CACHE.get(key,{})
        merged={**previous.get("models",{}),**models}
        # Partial model reports must never erase action evidence for this bar.
        # Merge by action AND side; empty or invalid updates are no-ops.
        merged_actions={action:dict(sides) for action,sides in previous.get("actions",{}).items()}
        for action,sides in actions.items():
            merged_actions[action]={**merged_actions.get(action,{}),**sides}
        CACHE[key]={"status":"SHADOW_READY","symbol":ctx["symbol"],"bar_time":ctx["bar_time"],"models":merged,"actions":merged_actions,"received_at":time.time(),"execution_authorized":False}
        CACHE.move_to_end(key)
        while len(CACHE)>128:CACHE.popitem(last=False)
    return {"accepted":True,"execution_authorized":False}
def evidence(cid,decision):
    key=(cid,decision.get("symbol"),int(num(decision.get("bar_time"))))
    with LOCK:r=CACHE.get(key)
    if not r or not 60<=time.time()-key[2]<=120 or time.time()-r["received_at"]>60:
        return {"status":"NO_FRESH_EVIDENCE","execution_authorized":False}
    vals=list(r["models"].values())
    avg=sum(vals)/len(vals) if vals else None
    return {**r,"direction":"BUY" if avg is not None and avg>.05 else "SELL" if avg is not None and avg<-.05 else "WAIT","mean_atr":round(avg,4) if avg is not None else None}
def fused_shadow(cid,decision,base):
    r=evidence(cid,decision)
    if r.get("status")!="SHADOW_READY":return r
    scores=(base.get("master") or {}).get("scores") or {}
    buy=num(scores.get("BUY"));sell=num(scores.get("SELL"))
    # Bounded independent research blend, never a new entry permission.
    margin=(buy-sell)/max(1,abs(buy)+abs(sell))
    avg=r.get("mean_atr")
    delta=.15*max(-1,min(1,num(avg))) if avg is not None else 0.0
    combined=margin+delta
    candidate="BUY" if combined>.03 else "SELL" if combined<-.03 else "WAIT"
    return {**r,"master_direction":base.get("decision","WAIT"),"candidate_direction":candidate,
        "master_margin":round(margin,4),"arena_delta":round(delta,4),"combined_margin":round(combined,4)}
def management(snapshot,decision,plan):
    r=(decision.get("master") or {}).get("arena_shadow") or {}
    out={"status":r.get("status","NO_FRESH_EVIDENCE"),"mode":"SHADOW_ONLY","execution_authorized":False,"sides":{}}
    if r.get("status")!="SHADOW_READY":return out
    atr=num(decision.get("entry_atr"));tick=snapshot.get("tick") or {}
    for side in ("BUY","SELL"):
        group=[p for p in snapshot.get("positions") or [] if p.get("symbol")==snapshot.get("symbol") and int(num(p.get("type"),-1))==(0 if side=="BUY" else 1)]
        vol=sum(num(p.get("volume")) for p in group)
        if vol<=0 or atr<=0:continue
        avg=sum(num(p.get("open_price"))*num(p.get("volume")) for p in group)/vol
        price=num(tick.get("bid" if side=="BUY" else "ask"))
        if price<=0:continue
        distance=(price-avg)/atr*(1 if side=="BUY" else -1)
        net=sum(num(p.get("profit"))+num(p.get("swap")) for p in group)
        armed=bool((plan.get("recovery") or {}).get("armed"))
        metrics={a:(r.get("actions") or {}).get(a,{}).get(side) for a in ACTIONS}
        active=next((v for v in plan.get("actions",[]) if v.get("side")==side and v.get("action")!="HOLD"),None)
        relevant=(active["action"].replace("_PROPOSAL","") if active else "RECOVERY" if armed and not plan.get("recovery_wait") else "COOLING" if distance<0 else "ADDON")
        out["sides"][side]={"distance_atr":round(distance,4),"floating_net":round(net,2),"recovery_armed":armed,"scores":metrics,
          "relevant_action":relevant,"recovery_waiting":bool(plan.get("recovery_wait")),
          "note":"Historical scenario proxy, not basket profit or execution authorization"}
    return out
