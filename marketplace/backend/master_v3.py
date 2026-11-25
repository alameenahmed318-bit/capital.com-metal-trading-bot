"""V3 research: bounded worker, same-quote counterfactuals, no order authority."""
import math
import queue
import threading
import time
import hashlib
import json
import master_ai
import master_v2
import master_entry_review
import master_adaptive
import master_balanced
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

def speed_trap_candidate(proposed,snapshot,settings,now,review):
    """Research-only: earlier candidate and adverse candle/price context."""
    side=proposed.get("decision");ctx=proposed.get("context") or {}
    if side not in SIDES:
        return {"direction":"WAIT","reason":"NO_DIRECTION","execution_authorized":False}
    sign=1 if side=="BUY" else -1
    pressure=sign*num(ctx.get("last_closed_candle_pressure"))
    quality=max(0.,min(1.,num(ctx.get(side.lower()+"_quality"),50.)/100.))
    flagged=ctx.get(side.lower()+"_trap") is True
    ref=num(proposed.get("entry_reference_price"));atr=num(proposed.get("entry_atr") or ctx.get("atr"))
    price=num((snapshot.get("tick") or {}).get("ask" if side=="BUY" else "bid"))
    adverse=max(0.,min(1.,-sign*(price-ref)/atr)) if price>0 and ref>0 and atr>0 else 0.
    risk=min(1.,.5*flagged+.25*max(0.,-pressure)+.15*(1.-quality)+.1*adverse)
    trial=review
    if review.get("state")=="COLLECTING":
        master=proposed.get("master") or {}
        models=[{**m,"status":"DEFERRED"} if m.get("model") in review.get("pending",[]) else m for m in master.get("models",[])]
        trial=master_entry_review.review({**proposed,"master":{**master,"models":models}},snapshot,settings,now)
        if not (trial.get("approved") and trial.get("independent_votes",0)>=1 and num(trial.get("quality"))>=.80 and num(trial.get("independent_support"))>=.85 and pressure>=.35 and risk<.25):
            return {"direction":"WAIT","reason":"EARLY_EVIDENCE_WEAK","trap_risk":round(risk,4),"execution_authorized":False}
    approved=trial.get("approved") is True and risk<.50
    return {"direction":side if approved else "WAIT","reason":"RESEARCH_CANDIDATE" if approved else "TRAP_RISK" if risk>=.50 else trial.get("state","WAIT"),"trap_risk":round(risk,4),"flagged":flagged,"pressure":round(pressure,4),"execution_authorized":False}

def evaluate(base,council,feedback,tick,settings,now,arena=None,positions=None):
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
    speed_trap=speed_trap_candidate(proposed,snapshot,settings,now,review)
    # Early release is a research hypothesis, not a live authorization.
    # It bypasses only remaining model collection, never quality or price checks.
    early=False
    if review["state"]=="COLLECTING":
        softened=[{**m,"status":"DEFERRED"} if m.get("model") in review["pending"] else m for m in models]
        trial=master_entry_review.review({**proposed,"master":{"models":softened,"scores":scores}},snapshot,settings,now)
        if trial["approved"] and trial["independent_votes"]>=2 and num(trial["quality"])>=.75 and num(trial.get("independent_support"))>=.65:
            review=trial;early=True
    live=master_ai.decide(base,council,feedback,arena_evidence=arena)
    # Mirror the actual live entry-review inputs; comparing against a legacy
    # language-only gate would exaggerate the adaptive challenger's benefit.
    live["master"]={**(live.get("master") or {}),"arena_shadow":arena or {}}
    live_review=master_entry_review.review(live,{**snapshot,"symbol":base.get("symbol")},
                                          settings,now,live_arena=True)
    demo_snapshot={**snapshot,"symbol":base.get("symbol"),"account":{"trade_mode":"DEMO"},"master_direct_demo_capable":True}
    demo_v1=master_entry_review.review(live,demo_snapshot,settings,now,live_arena=True)
    balanced=master_balanced.evaluate(live,demo_snapshot,settings,now)
    balanced={**balanced,"mode":"SHADOW_ONLY","execution_authorized":False,"baseline_direction":live.get("decision") if demo_v1.get("approved") else "WAIT"}
    adaptive=master_adaptive.evaluate(base,live,arena,feedback,tick,settings,now)
    adaptive["management"]=master_adaptive.management(adaptive,arena,positions,base.get("symbol"))
    return {"balanced_demo_comparison":balanced,"adaptive":adaptive,"adaptive_baseline":live["decision"] if live_review["approved"] else "WAIT",
        "adaptive_baseline_review":live_review,
        "current":current["decision"] if old["approved"] else "WAIT",
        "challenger":direction if review["approved"] else "WAIT",
        "candidate_direction":direction,"current_review":old,"review":review,
        "speed_trap_shadow":speed_trap,
        "early_candidate":early,"strategy_id":v2["strategy_id"],"combination_id":v2["combination_id"],
        "regime":v2["regime"],"weights":v2["weights"],"profile_evidence":v2["profile_evidence"],
        "eligible":v2["eligible"],"execution_authorized":False}

def quote_payoff(side,bid,ask,exit_bid,exit_ask,atr):
    if side=="BUY":return (exit_bid-ask)/atr
    if side=="SELL":return (bid-exit_ask)/atr
    return 0.0

def process(client,symbol,base,council,feedback,tick,settings,now,emit,arena=None,positions=None):
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
    bar=int(num(base.get("bar_time")));age=now-bar-60
    # Preserve legacy lane_for and summary; add 2s/15s samples for adaptive research.
    lane=max((x for x in (2,5,10,15,20,35) if x<=age<60),default=None)
    if not bar or lane is None or num(council.get("bar_time"))!=bar:return
    atr=num((base.get("context") or {}).get("atr"))
    if atr<=0:return
    key=(client,symbol,bar,lane)
    # First observation remains the paired outcome baseline. Record changed
    # evidence separately; never attribute a late vote to an earlier quote.
    raw_votes={**(council.get("votes") or {}),"GPT-OSS 120B":council.get("groq_advisory") or {}}
    evidence={"votes":{str(n):{k:v.get(k) for k in ("status","direction","confidence","model_id")}
                       for n,v in raw_votes.items() if isinstance(v,dict)},
              "arena":{k:(arena or {}).get(k) for k in ("status","bar_time","symbol","models")},
              "strategy_id":base.get("strategy_id"),"combination_id":base.get("combination_id"),
              "direction":base.get("decision")}
    digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    existing=_PENDING.get(key)
    if existing is not None:
        if digest==existing.get("last_evidence_hash") or existing.get("evidence_changes",0)>=8:return
        candidate=evaluate(base,council,feedback,tick,settings,now,arena,positions)
        revision=existing.get("evidence_changes",0)+1
        existing["last_evidence_hash"]=digest
        existing["evidence_changes"]=revision
        emit({"event_id":existing["event_id"]+":evidence:"+str(revision),
              "client_id":client,"event_type":"MASTER_V3_EVIDENCE_CHANGE",
              "payload":{"version":VERSION,"symbol":symbol,"bar_time":bar,"lane":lane,
                         "observed_at":now,"bid":bid,"ask":ask,"revision":revision,
                         "baseline_event_id":existing["event_id"],"evidence":evidence,
                         "candidate":candidate,"execution_authorized":False,
                         "actual_trade_proven":False}})
        return
    candidate=evaluate(base,council,feedback,tick,settings,now,arena,positions)
    eid="master-v3:"+hashlib.sha256(repr(key).encode()).hexdigest()
    initial={s:quote_payoff(s,bid,ask,bid,ask,atr) for s in SIDES}
    p={"version":VERSION,"event_id":eid,"symbol":symbol,"bar_time":bar,"lane":lane,
       "observed_at":now,"delay_sec":round(age,3),"bid":bid,"ask":ask,"atr":atr,
       "candidate":candidate,"last_seen":now,"max_gap_sec":0.0,"outcomes":{},
       "last_evidence_hash":digest,"evidence_changes":0,
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

def submit(client,symbol,timeframe,base,council,feedback,tick,settings,now,emit,arena=None,positions=None):
    """Bounded nonblocking enqueue; queue full means skip research."""
    global _WORKER
    if str(timeframe).upper() not in ("M1","PERIOD_M1"):return False
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER=threading.Thread(target=_run,daemon=True,name="alzool-master-v3")
            _WORKER.start()
    # Decisions, council and feedback snapshots are read-only in this worker.
    try:JOBS.put_nowait((str(client),str(symbol),base,council,feedback,dict(tick),settings,now,emit,arena,list(positions or [])));return True
    except queue.Full:return False

def latest(client,symbol):
    with _LOCK:return dict(_LATEST.get((str(client),str(symbol))) or {})

def summarize(rows,symbol):
    """Persisted same-quote paired evidence; compare chronological holdout."""
    groups={};seen=set();regimes={};strategies={};speed_groups={}
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
            trial=c.get("speed_trap_shadow") or {}
            side=trial.get("direction")
            if side in (*SIDES,"WAIT") and trial.get("execution_authorized") is False:
                speed_groups.setdefault((lane,h),[]).append((bar,y,num(pay.get(side)),b,side))
            if h==180 and b in SIDES:
                ex=(o.get("excursion") or {}).get(b) or {}
                sample=(bar,y,num(ex.get("mae_atr")),num(ex.get("mfe_atr")))
                regimes.setdefault((str(c.get("regime")),lane),[]).append(sample)
                strategies.setdefault((str(c.get("strategy_id")),lane),[]).append(sample)
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
    def memory(groups):
        out=[]
        for (name,lane),items in groups.items():
            items=sorted(items);cut=int(len(items)*.7);train=items[:cut];valid=items[cut:]
            n=len(items)
            out.append({"name":name,"lane":lane,"samples":n,
                "mean_net_atr":round(sum(t[1] for t in items)/n,4),
                "mean_mae_atr":round(sum(t[2] for t in items)/n,4),
                "mean_mfe_atr":round(sum(t[3] for t in items)/n,4),
                "training":len(train),"validation":len(valid),
                "status":"REVIEW_ONLY" if len(train)>=30 and len(valid)>=15 else "INSUFFICIENT"})
        return sorted(out,key=lambda x:x["samples"],reverse=True)[:30]
    speed_report={}
    for (lane,h),items in speed_groups.items():
        items=sorted(items);cut=int(len(items)*.7)
        def paired(data):
            return {"samples":len(data),"baseline_net_atr":round(sum(x[1] for x in data),4),
                    "candidate_net_atr":round(sum(x[2] for x in data),4),
                    "baseline_signals":sum(x[3] in SIDES for x in data),
                    "candidate_signals":sum(x[4] in SIDES for x in data)}
        speed_report.setdefault(str(lane),{})[str(h)]={"train":paired(items[:cut]),
            "validation":paired(items[cut:]),"enough_for_review":cut>=80 and len(items)-cut>=40}
    return {"version":VERSION,"mode":"SHADOW_ONLY","lanes":result,"speed_trap_shadow":speed_report,"unified_score_validation":summarize_unified(rows,symbol),"balanced_demo_validation":summarize_unified(rows,symbol,balanced=True),"regime_memory":memory(regimes),
            "strategy_memory":memory(strategies),"live_execution_changed":False,
            "metric":"OBSERVED_QUOTES_COMMON_HORIZON_NOT_BROKER_TRADES"}


def summarize_unified(rows,symbol,balanced=False):
    """Same observed quote, chronological split and nonoverlapping 180s
    outcome windows. Never auto-promote a research candidate."""
    samples={}
    for row in rows or []:
        p=row.get("payload") or {}
        if p.get("version")!=VERSION or not same_market_symbol(p.get("symbol"),symbol) or p.get("lane")!=10:continue
        c=p.get("candidate") or {}
        review=c.get("adaptive_baseline_review") or {}
        trial=(c.get("balanced_demo_comparison") if balanced else review.get("unified_score")) or {}
        version=master_balanced.VERSION if balanced else master_entry_review.UNIFIED_SCORE_VERSION
        if trial.get("version")!=version or trial.get("execution_authorized") is not False:continue
        o=(p.get("outcomes") or {}).get("180")
        if not isinstance(o,dict) or num(o.get("max_gap_sec"),999)>20:continue
        pay=o.get("payoffs") or {}
        if not all(s in pay and math.isfinite(num(pay[s],float("nan"))) for s in SIDES):continue
        bar=p.get("bar_time")
        if not isinstance(bar,int):continue
        a=trial.get("baseline_direction","WAIT") if balanced else c.get("adaptive_baseline","WAIT");b=trial.get("direction","WAIT")
        if a not in (*SIDES,"WAIT") or b not in (*SIDES,"WAIT"):continue
        observed=num(p.get("observed_at"));end=num(o.get("quote_time"))
        if not 0<observed<end:continue
        samples.setdefault(bar,(observed,end,a,b,num(pay.get(a)),num(pay.get(b))))
    distinct=[];end=0.
    for _,v in sorted(samples.items()):
        if v[0]<=end:continue
        distinct.append(v);end=v[1]
    cut=int(len(distinct)*.7)
    def stats(part):
        return {"windows":len(part),"baseline_signals":sum(x[2] in SIDES for x in part),
            "candidate_signals":sum(x[3] in SIDES for x in part),
            "baseline_net_atr":round(sum(x[4] for x in part),4),
            "candidate_net_atr":round(sum(x[5] for x in part),4),
            "candidate_positive":sum(x[3] in SIDES and x[5]>0 for x in part),
            "baseline_positive":sum(x[2] in SIDES and x[4]>0 for x in part)}
    train=stats(distinct[:cut]);valid=stats(distinct[cut:])
    enough=train["windows"]>=80 and valid["windows"]>=40 and train["candidate_signals"]>=30 and valid["candidate_signals"]>=20
    improves=enough and all(x["candidate_net_atr"]>max(0.,x["baseline_net_atr"]) for x in (train,valid))
    return {"version":master_balanced.VERSION if balanced else master_entry_review.UNIFIED_SCORE_VERSION,"mode":"SHADOW_ONLY","lane":10,"horizon":180,
        "train":train,"validation":valid,"enough_for_review":enough,"candidate_improves_proxy":improves,
        "auto_promoted":False,"execution_authorized":False,"metric":"OBSERVED_QUOTES_NOT_BROKER_TRADES"}
