"""Automatic research feedback, isolated per owner/market. No trading permission.
Uses settled forecasts and explicitly attributed broker deal records. Candidate
confidence calibration is chronological and never applied to live orders.
"""
import math
import threading
import time
import urllib.parse
from market_symbols import market_family, same_market_symbol
from learning_memory import memory_snapshot, combine_deals
import adaptive_memory

VERSION="FEEDBACK_V1"
MODELS={"LOCAL_AI":{"qwen2.5:1.5b","deepseek-r1:1.5b"},
        "FAST_AI":{"Fast AI"},"CLOUD_AI":{"GPT-OSS 120B"}}
ACTIONS={"ALZOOL-ADD":"ADDON","ALZOOL-COOL":"COOLING","ALZOOL-RECOVERY":"RECOVERY"}
_LOCK=threading.RLock()
_CACHE={}
_LOADING=set()
_TTL=180

def number(x,default=None):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except (TypeError,ValueError,OverflowError):return default

def model_feedback(rows,symbol):
    """Train only on earlier closed-bar outcomes, test on later unseen outcomes."""
    groups={}
    seen=set()
    for row in rows or []:
        p=row.get("payload") if isinstance(row,dict) else None
        if not isinstance(p,dict) or p.get("version")!="AZ_ACCURACY_V4":continue
        if not same_market_symbol(p.get("symbol"),symbol):continue
        model=p.get("model");kind=p.get("kind")
        if model not in MODELS.get(kind,set()) or p.get("analysis_mode")!="UNIFIED":continue
        bar=number(p.get("bar_time"));at=number(p.get("forecast_time_unix"))
        end=number(p.get("next_bar_time"));confidence=number(p.get("reported_confidence"))
        positive=p.get("model_positive")
        if (bar is None or bar<=0 or at is None or not bar+60<=at<bar+120 or
            end!=bar+60 or not isinstance(positive,bool) or p.get("direction") not in ("BUY","SELL") or
            confidence is None or not 0<=confidence<=1):continue
        key=(model,bar)
        if key in seen:continue
        seen.add(key)
        groups.setdefault(model,[]).append((bar,confidence,int(positive)))
    result=[]
    for model in sorted(set().union(*MODELS.values())):
        samples=sorted(groups.get(model,[]))[-300:]
        n=len(samples)
        out={"model":model,"samples":n,"positive":sum(s[2] for s in samples),
             "status":"INSUFFICIENT_EVIDENCE","candidate_confidence_factor":1.0,
             "live_weight_changed":False}
        if n>=60:
            cut=int(n*.7)
            train,test=samples[:cut],samples[cut:]
            # Conservative shrinkage toward observed performance, with a
            # neutral prior. Calibration is not proof of increased profit.
            observed=(sum(s[2] for s in train)+10)/(len(train)+20)
            avg=sum(s[1] for s in train)/len(train)
            factor=max(.35,min(1.0,observed/max(avg,.01)))
            base=sum((c-y)**2 for _,c,y in test)/len(test)
            candidate=sum((c*factor-y)**2 for _,c,y in test)/len(test)
            improved=candidate+0.005<base
            out.update({"training":len(train),"validation":len(test),
                "baseline_brier":round(base,5),"candidate_brier":round(candidate,5),
                "status":"CALIBRATION_CANDIDATE" if improved else "NOT_VALIDATED",
                "candidate_confidence_factor":round(factor,4) if improved else 1.0})
        result.append(out)
    return result

def action_tag(comment):
    value=str(comment or "").upper()
    return next((action for prefix,action in ACTIONS.items() if value.startswith(prefix)),None)

def action_feedback(deals,symbol,now=None):
    """Only exact entry-position links; skip incomplete/ambiguous positions.
    Full net requires recorded entry and exit volume and fees. Exit-only
    linked tags are reported separately, never treated as complete cash P&L.
    """
    now=time.time() if now is None else now
    positions={};seen=set()
    for d in deals or []:
        if not isinstance(d,dict) or not same_market_symbol(d.get("symbol"),symbol):continue
        ticket=str(d.get("ticket") or "");pid=str(d.get("position_id") or "")
        stamp=number(d.get("time"));entry=number(d.get("entry"));volume=number(d.get("volume"))
        if not ticket or not pid or ticket in seen or stamp is None or stamp>now or volume is None or volume<=0:continue
        if entry not in (0,1,2,3):continue
        seen.add(ticket)
        g=positions.setdefault(pid,{"entries":0.0,"exits":0.0,"net":0.0,"last_exit":0,
            "tags":set(),"entry_tags":set(),"unknown_entry":False,"ambiguous":False})
        tag=action_tag(d.get("strategy_comment")) or action_tag(d.get("comment"))
        if tag:g["tags"].add(tag)
        if entry==0:
            g["entries"]+=volume
            if tag:g["entry_tags"].add(tag)
            else:g["unknown_entry"]=True
        elif entry in (1,3):
            g["exits"]+=volume;g["last_exit"]=max(g["last_exit"],stamp)
        else:g["ambiguous"]=True
        values=[number(d.get(k)) for k in ("profit","commission","swap","fee")]
        if any(v is None for v in values):g["ambiguous"]=True
        else:g["net"]+=sum(values)
    output={a:{"action":a,"complete_positions":0,"positive":0,"net":0.0,"volume":0.0,
        "incomplete_positions":0,"status":"INSUFFICIENT_EVIDENCE","timing_candidate_atr_shift":0.0}
        for a in ACTIONS.values()}
    for g in positions.values():
        if len(g["tags"])!=1 or g["last_exit"]<now-30*86400:continue
        action=next(iter(g["tags"]));r=output[action]
        complete=(not g["ambiguous"] and not g["unknown_entry"] and g["entry_tags"]=={action} and
                  g["entries"]>0 and abs(g["entries"]-g["exits"])<1e-7)
        if not complete:r["incomplete_positions"]+=1;continue
        r["complete_positions"]+=1;r["positive"]+=int(g["net"]>0)
        r["net"]+=g["net"];r["volume"]+=g["entries"]
    for r in output.values():
        r["net"]=round(r["net"],4);r["volume"]=round(r["volume"],8)
        r["net_per_lot"]=round(r["net"]/r["volume"],4) if r["volume"] else None
        if r["complete_positions"]>=30:
            r["status"]="OUTCOME_EVIDENCE_ONLY"
            # Realized outcomes alone do not prove a different timing improves
            # results. No timing or lot changes without counterfactual replay.
    return list(output.values())

def _refresh(key,request,live_deals):
    client,symbol=key
    try:
        query="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client,safe="") + "&event_type=eq.AI_ACCURACY_V4_OUTCOME&select=payload&order=created_at.desc&limit=500"
        rows=request("GET",query,admin=True)
        if not isinstance(rows,list):raise ValueError("outcomes")
        memory=memory_snapshot(client,request)
        deals=combine_deals(memory["deals"],live_deals)
        report={"version":VERSION,"status":"RESEARCH_ONLY","symbol":symbol,
            "market":market_family(symbol),"updated_at_unix":time.time(),
            "models":model_feedback(rows,symbol),"actions":action_feedback(deals,symbol),
            "adaptive_memory":{"version":adaptive_memory.VERSION,"mode":"SHADOW_ONLY",
                "model_performance":adaptive_memory.performance_factors(rows,symbol),
                "action_sides":adaptive_memory.action_side_memory(deals,symbol,action_feedback),
                "execution_authorized":False},
            "memory_status":memory["status"],"live_weights_changed":False,"execution_authorized":False}
        # Publish live V1 feedback before the extra research query.
        # A slow comparison database must not delay V1 cache availability.
        with _LOCK:
            previous=_CACHE.get(key)
            prior=(previous[1] if previous else {}).get("master_v2")
            _CACHE[key]=(time.monotonic(),{**report,**({"master_v2":prior} if prior else {})})
        # Separate research query; failures do not alter V1 feedback.
        try:
            import master_v2
            q="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client,safe="")+"&event_type=eq.MASTER_V2_COMPARISON&select=payload&order=created_at.desc&limit=1000"
            comparison=request("GET",q,admin=True)
            if not isinstance(comparison,list):raise ValueError("comparison")
            report["master_v2"]=master_v2.summarize(comparison,symbol)
        except Exception:
            report["master_v2"]={"mode":"SHADOW_ONLY","status":"UNAVAILABLE","profiles":{},"lanes":{}}
        # Preserve V1/V2 availability before the optional V3 research read.
        with _LOCK:
            old=_CACHE.get(key)
            prior=(old[1] if old else {}).get("master_v3")
            _CACHE[key]=(time.monotonic(),{**report,**({"master_v3":prior} if prior else {})})
        try:
            import master_v3
            q="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client,safe="")+"&event_type=eq.MASTER_V3_OUTCOME&payload->>symbol=eq."+urllib.parse.quote(symbol,safe="")+"&select=payload&order=created_at.desc&limit=1000"
            rows=request("GET",q,admin=True)
            if not isinstance(rows,list):raise ValueError("v3 outcomes")
            report["master_v3"]=master_v3.summarize(rows,symbol)
        except Exception:
            report["master_v3"]={"mode":"SHADOW_ONLY","status":"UNAVAILABLE","lanes":{}}
        with _LOCK:_CACHE[key]=(time.monotonic(),report)
    except Exception:
        with _LOCK:
            old=_CACHE.get(key)
            _CACHE[key]=(time.monotonic(),{**(old[1] if old else {}),"status":"STALE_OR_UNAVAILABLE",
                "symbol":symbol,"live_weights_changed":False,"execution_authorized":False})
    finally:
        with _LOCK:_LOADING.discard(key)

def snapshot(client,symbol,request,live_deals=None):
    if not client or not market_family(symbol):return {"status":"NO_MARKET"}
    key=(str(client),str(symbol).upper())
    with _LOCK:
        old=_CACHE.get(key)
        if (not old or time.monotonic()-old[0]>=_TTL) and key not in _LOADING and len(_LOADING)<16:
            if len(_CACHE)>=128 and key not in _CACHE:
                _CACHE.pop(min(_CACHE,key=lambda k:_CACHE[k][0]),None)
            _LOADING.add(key)
            threading.Thread(target=_refresh,args=(key,request,list(live_deals or [])),
                daemon=True,name="alzool-feedback").start()
        return dict(old[1]) if old else {"version":VERSION,"status":"LOADING","symbol":symbol,
            "live_weights_changed":False,"execution_authorized":False}
