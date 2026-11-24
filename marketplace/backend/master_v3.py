"""V3 research: bounded worker, same-quote counterfactuals, no order authority."""
import math
import queue
import threading
import time
import hashlib
import master_ai
import master_v2
import master_entry_review
from market_symbols import same_market_symbol

VERSION="MASTER_V3_SHADOW"
LANES=(5,10,20,35)
HORIZONS=(60,180,300)
SIDES=("BUY","SELL")
JOBS=queue.Queue(maxsize=32)
_LOCK=threading.Lock()
_WORKER=None
_PENDING={}
_LATEST={}
MAX_PENDING=256

def num(x,default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except (TypeError,ValueError,OverflowError):return default

def lane_for(age):
    return max((x for x in LANES if x<=age<60),default=None)

def evaluate(base,council,feedback,tick,settings,now):
    """Research only: compare current review against adaptive challenger."""
    age=now-num(base.get("bar_time"))-60
    current=master_ai.decide(base,council,feedback)
    snapshot={"timeframe":"M1","tick":tick}
    old=master_entry_review.review(current,snapshot,settings,now)
    v2=master_v2.candidate(base,council,feedback,"EARLY" if age<20 else "REVIEW")
    direction=v2["direction"]
    scores={s:num((base.get("directional_scores") or {}).get(s)) for s in SIDES}
    models=v2.get("models") or []
    for m in models:
        if m.get("used") and m.get("direction") in SIDES:scores[m["direction"]]+=num(m.get("points"))
    proposed={**base,"decision":direction,"confidence":v2["confidence"],
        "strategy_id":v2["strategy_id"],"combination_id":v2["combination_id"],
        "master":{"models":models,"scores":scores}}
    review=master_entry_review.review(proposed,snapshot,settings,now)
    # Early release is a research hypothesis, not a live authorization.
    # It bypasses only remaining model collection, never quality or price checks.
    early=False
    if review["state"]=="COLLECTING":
        softened=[{**m,"status":"DEFERRED"} if m.get("model") in review["pending"] else m for m in models]
        trial=master_entry_review.review({**proposed,"master":{"models":softened,"scores":scores}},snapshot,settings,now)
        if trial["approved"] and trial["independent_votes"]>=2 and num(trial["quality"])>=.75 and num(trial.get("independent_support"))>=.65:
            review=trial;early=True
    return {"current":current["decision"] if old["approved"] else "WAIT",
        "challenger":direction if review["approved"] else "WAIT",
        "candidate_direction":direction,"current_review":old,"review":review,
        "early_candidate":early,"strategy_id":v2["strategy_id"],"combination_id":v2["combination_id"],
        "regime":v2["regime"],"weights":v2["weights"],"profile_evidence":v2["profile_evidence"],
        "eligible":v2["eligible"],"execution_authorized":False}

def quote_payoff(side,bid,ask,exit_bid,exit_ask,atr):
    if side=="BUY":return (exit_bid-ask)/atr
    if side=="SELL":return (bid-exit_ask)/atr
    return 0.0

def process(client,symbol,base,council,feedback,tick,settings,now,emit):
    """Worker-owned state; only observed quotes, no imputed intrabar fills."""
    bid,ask=num(tick.get("bid")),num(tick.get("ask"))
    if not 0<bid<=ask:return
    group=(client,symbol)
    for key,p in list(_PENDING.items()):
        if key[:2]!=group:continue
        if now<=p["last_seen"]:continue
        p["max_gap_sec"]=max(p["max_gap_sec"],now-p["last_seen"]);p["last_seen"]=now
        for side in SIDES:
            value=quote_payoff(side,p["bid"],p["ask"],bid,ask,p["atr"])
            e=p["excursion"][side]
            e["mae_atr"]=min(e["mae_atr"],value);e["mfe_atr"]=max(e["mfe_atr"],value)
        for horizon in HORIZONS:
            if str(horizon) in p["outcomes"]:continue
            target=p["bar_time"]+60+horizon
            if now<target:continue
            p["outcomes"][str(horizon)]=({"quote_time":now,"payoffs":{s:round(quote_payoff(s,p["bid"],p["ask"],bid,ask,p["atr"]),6) for s in SIDES},
                "excursion":{s:dict(e) for s,e in p["excursion"].items()},"max_gap_sec":round(p["max_gap_sec"],3)} if now<=target+8 else None)
        if now>=p["bar_time"]+60+300:
            _PENDING.pop(key,None)
            payload={**p,"actual_trade_proven":False}
            emit({"event_id":p["event_id"]+":outcome","client_id":client,"event_type":"MASTER_V3_OUTCOME","payload":payload})
    bar=int(num(base.get("bar_time")));age=now-bar-60;lane=lane_for(age)
    if not bar or lane is None or num(council.get("bar_time"))!=bar:return
    atr=num((base.get("context") or {}).get("atr"))
    if atr<=0:return
    key=(client,symbol,bar,lane)
    if key in _PENDING:return
    candidate=evaluate(base,council,feedback,tick,settings,now)
    eid="master-v3:"+hashlib.sha256(repr(key).encode()).hexdigest()
    initial={s:quote_payoff(s,bid,ask,bid,ask,atr) for s in SIDES}
    p={"version":VERSION,"event_id":eid,"symbol":symbol,"bar_time":bar,"lane":lane,
       "observed_at":now,"delay_sec":round(age,3),"bid":bid,"ask":ask,"atr":atr,
       "candidate":candidate,"last_seen":now,"max_gap_sec":0.0,"outcomes":{},
       "excursion":{s:{"mae_atr":min(0,initial[s]),"mfe_atr":max(0,initial[s])} for s in SIDES},
       "execution_authorized":False}
    if len(_PENDING)>=MAX_PENDING:_PENDING.pop(next(iter(_PENDING)),None)
    _PENDING[key]=p
    with _LOCK:
        if len(_LATEST)>=128 and group not in _LATEST:_LATEST.pop(next(iter(_LATEST)),None)
        _LATEST[group]={"observed_at":now,"delay_sec":p["delay_sec"],"lane":lane,**candidate}
    emit({"event_id":eid,"client_id":client,"event_type":"MASTER_V3_FORECAST",
          "payload":{k:v for k,v in p.items() if k not in ("outcomes","excursion")}})

def _run():
    while True:
        job=JOBS.get()
        try:process(*job)
        except Exception as exc:print("MASTER_V3_RESEARCH_WARNING:",type(exc).__name__,flush=True)
        finally:JOBS.task_done()

def submit(client,symbol,timeframe,base,council,feedback,tick,settings,now,emit):
    """Bounded nonblocking enqueue; queue full means skip research."""
    global _WORKER
    if str(timeframe).upper() not in ("M1","PERIOD_M1"):return False
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER=threading.Thread(target=_run,daemon=True,name="alzool-master-v3")
            _WORKER.start()
    # Decisions, council and feedback snapshots are read-only in this worker.
    try:JOBS.put_nowait((str(client),str(symbol),base,council,feedback,dict(tick),settings,now,emit));return True
    except queue.Full:return False

def latest(client,symbol):
    with _LOCK:return dict(_LATEST.get((str(client),str(symbol))) or {})

def summarize(rows,symbol):
    """Persisted same-quote paired evidence; compare chronological holdout."""
    groups={};seen=set()
    for row in rows or []:
        p=row.get("payload") or {}
        if p.get("version")!=VERSION or not same_market_symbol(p.get("symbol"),symbol):continue
        lane=p.get("lane");bar=p.get("bar_time")
        if lane not in LANES or not isinstance(bar,int) or (bar,lane) in seen:continue
        seen.add((bar,lane))
        c=p.get("candidate") or {}
        for h in HORIZONS:
            o=(p.get("outcomes") or {}).get(str(h))
            if not isinstance(o,dict):continue
            pay=o.get("payoffs") or {}
            if not all(s in pay for s in SIDES):continue
            a=c.get("current","WAIT");b=c.get("challenger","WAIT")
            if a not in (*SIDES,"WAIT") or b not in (*SIDES,"WAIT"):continue
            x=num(pay.get(a));y=num(pay.get(b))
            missed=b=="WAIT" and any(num(pay.get(s))>0 for s in c.get("eligible",[]))
            groups.setdefault((lane,h),[]).append((bar,x,y,a,b,missed))
    result={}
    for (lane,h),items in groups.items():
        samples=sorted(items);cut=int(len(samples)*.7);train=samples[:cut];valid=samples[cut:]
        def stats(data):
            return {"n":len(data),"current_net_atr":round(sum(x[1] for x in data),4),
                "v3_net_atr":round(sum(x[2] for x in data),4),
                "current_signals":sum(x[3] in SIDES for x in data),"v3_signals":sum(x[4] in SIDES for x in data),
                "v3_missed_proxy":sum(x[5] for x in data)}
        result.setdefault(str(lane),{})[str(h)]={"all":stats(samples),"train":stats(train),"validation":stats(valid),
            "enough_for_review":len(train)>=80 and len(valid)>=40}
    return {"version":VERSION,"mode":"SHADOW_ONLY","lanes":result,"live_execution_changed":False,
            "metric":"OBSERVED_QUOTES_COMMON_HORIZON_NOT_BROKER_TRADES"}
