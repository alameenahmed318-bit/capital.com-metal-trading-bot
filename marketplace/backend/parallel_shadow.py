"""Independent owner/market shadow store. No import by decision, permit, or execution modules."""
import math,time,threading
from collections import OrderedDict
NAMES=("TCN","PatchTST","LSTM Multi","Patch Transformer","LSTM","Ensemble","Isolation Forest")
DIRECTION=NAMES[:5]
FAMILIES={"CONV":("TCN",),"RECURRENT":("LSTM Multi","LSTM"),"PATCH":("PatchTST","Patch Transformer")}
CACHE=OrderedDict();LOCK=threading.Lock()
def number(v,lo,hi):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:return None
    return float(v)
def accept(uid,market,ctx,data,now=None):
    now=time.time() if now is None else now
    if not isinstance(data,dict) or ctx.get("status")!="READY" or data.get("client")!=ctx.get("client") or data.get("symbol")!=ctx.get("symbol") or data.get("bar_time")!=ctx.get("bar_time"):
        return {"accepted":False,"reason":"STALE_OR_WRONG_CONTEXT","execution_authorized":False}
    bar=number(data.get("bar_time"),0,1e12)
    if bar is None or not 60<=now-bar<120:return {"accepted":False,"reason":"STALE","execution_authorized":False}
    incoming=data.get("models")
    if not isinstance(incoming,dict):return {"accepted":False,"execution_authorized":False}
    rows={}
    for name in DIRECTION+("Isolation Forest",):
        m=incoming.get(name)
        if not isinstance(m,dict):continue
        forecast=number(m.get("forecast"),-6,6)
        if forecast is None:continue
        samples=number(m.get("samples"),0,100000)
        accuracy=number(m.get("accuracy"),0,1)
        net=number(m.get("net_atr"),-100000,100000)
        rows[name]={"forecast":forecast,"samples":int(samples or 0),"accuracy":accuracy,"net_atr":net,
                    "status":"SHADOW_ONLY","weight":0.}
    if not rows:return {"accepted":False,"reason":"NO_VALID_MODELS","execution_authorized":False}
    # Diagnostic family budgets only. Never allow ensemble to double count its components.
    for names in FAMILIES.values():
        usable=[n for n in names if n in rows]
        if not usable:continue
        factors={n:(1.2 if rows[n]["samples"]>=100 and (rows[n]["net_atr"] or 0)>0 else .8 if rows[n]["samples"]>=100 and (rows[n]["net_atr"] or 0)<0 else 1.) for n in usable}
        # Missing models do not inflate surviving families.
        for n in usable:rows[n]["weight"]=round(factors[n]/max(len(names),sum(factors.values()))/len(FAMILIES),6)
    signed=sum(rows[n]["forecast"]*rows[n]["weight"] for n in DIRECTION if n in rows)
    rows["Ensemble"]={"forecast":round(signed,5),"status":"DERIVED_SHADOW_ONLY","weight":0.,"samples":0,"accuracy":None,"net_atr":None}
    key=(str(uid),market)
    value={"status":"SHADOW_READY","market":market,"symbol":ctx["symbol"],"bar_time":bar,"received_at":now,
           "models":rows,"direction":"BUY" if signed>.05 else "SELL" if signed<-.05 else "WAIT","execution_authorized":False}
    with LOCK:
        old=CACHE.get(key)
        if old and old["bar_time"]>bar:return {"accepted":False,"reason":"OUT_OF_ORDER","execution_authorized":False}
        CACHE[key]=value;CACHE.move_to_end(key)
        while len(CACHE)>64:CACHE.popitem(last=False)
    return {"accepted":True,"execution_authorized":False}
def snapshot(uid,market,now=None):
    now=time.time() if now is None else now
    with LOCK:r=CACHE.get((str(uid),market))
    if r and 60<=now-r["bar_time"]<120 and 0<=now-r["received_at"]<=60:return r
    return {"status":"NO_FRESH_SHADOW","market":market,"models":{n:{"status":"NOT_RECEIVED","weight":0.} for n in NAMES},"execution_authorized":False}
