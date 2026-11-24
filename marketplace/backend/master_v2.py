"""MASTER V2 research only. No order, authorization or settings writes.
Compare policies at identical observed quotes on closed M1 bars.
"""
import math
import threading
import time
from master_ai import decide as v1_decide, learned_factors, MODEL_NAMES
from hybrid_decision import fuse_live_decision
from market_symbols import same_market_symbol

VERSION="MASTER_V2_SHADOW"
BASE={"Fast AI":0.0,"Qwen":0.30,"DeepSeek":0.30,"GPT-OSS 120B":0.40}
REGIMES={"اتجاه صاعد","اتجاه هابط","تذبذب جانبي","تقلب مرتفع"}
SIDES=("BUY","SELL")
LOCK=threading.RLock()
PENDING={}
SEEN={}
MAX_PENDING=512

def num(x, default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except (ValueError,TypeError,OverflowError):
        return default

def clamp(v,lo,hi):return max(lo,min(hi,v))

def regime_of(base):
    context=(base or {}).get("context") or {}
    regime=context.get("regime")
    return regime if regime in REGIMES else "غير محدد"

def market_votes(council):
    votes=dict((council or {}).get("votes") or {})
    votes["GPT-OSS 120B"]=dict((council or {}).get("groq_advisory") or {})
    return votes

def valid_vote(v):
    return (isinstance(v,dict) and v.get("status")=="READY" and
            v.get("direction") in SIDES and 0<num(v.get("confidence"),-1)<=1)

def profile(rows,symbol):
    """Bounded evidence by market/regime/lane; validate on later observations."""
    groups={}; seen=set()
    for row in rows or []:
        p=row.get("payload") if isinstance(row,dict) else None
        if not isinstance(p,dict) or p.get("version")!=VERSION or not same_market_symbol(p.get("symbol"),symbol):continue
        bar=int(num(p.get("bar_time")))
        regime=p.get("regime")
        lane=p.get("lane")
        if bar<=0 or regime not in REGIMES or lane not in ("EARLY","REVIEW"):continue
        for name,result in (p.get("model_results") or {}).items():
            if name not in BASE or not isinstance(result,dict):continue
            edge=num(result.get("net_atr"),None)
            if edge is None:continue
            key=(regime,lane,name,bar)
            if key in seen:continue
            seen.add(key)
            groups.setdefault(key[:3],[]).append((bar,clamp(edge,-3,3)))
    profiles={}
    for (regime,lane,name),samples in groups.items():
        values=[v for _,v in sorted(samples)[-200:]]
        cut=int(len(values)*.7)
        train,test=values[:cut],values[cut:]
        tr=sum(train)/len(train) if train else 0
        va=sum(test)/len(test) if test else 0
        # Both disjoint chronological windows must agree on direction of edge.
        enough=len(train)>=30 and len(test)>=15
        consistent=enough and tr*va>0
        multiplier=clamp(1+.30*clamp(min(abs(tr),abs(va)),0,1)*(1 if tr>0 else -1),.70,1.30) if consistent else 1.0
        profiles.setdefault(regime,{}).setdefault(lane,{})[name]={
            "samples":len(values),"training":len(train),"validation":len(test),
            "train_net_atr":round(tr,5),"validation_net_atr":round(va,5),
            "factor":round(multiplier,5),"status":"CONSISTENT" if consistent else "NEUTRAL"}
    return profiles

def candidate(base,council,feedback,lane):
    """Research candidate, not a live policy. Never consumes future results."""
    regime=regime_of(base)
    votes=market_votes(council)
    bar=int(num(base.get("bar_time")))
    current=bar>0 and int(num((council or {}).get("bar_time")))==bar
    factors=learned_factors(feedback,base.get("symbol"))
    report=(feedback or {}).get("master_v2") or {}
    fresh=((feedback or {}).get("status")=="RESEARCH_ONLY" and
           same_market_symbol((feedback or {}).get("symbol"),base.get("symbol")) and
           0<=time.time()-num((feedback or {}).get("updated_at_unix"))<=600)
    evidence=(report.get("profiles") or {}).get(regime,{}).get(lane,{}) if fresh else {}
    weights={name:weight*clamp(num((evidence.get(name) or {}).get("factor"),1),.70,1.30) for name,weight in BASE.items()}
    adjusted={}
    for name in BASE:
        v=dict(votes.get(name) or {})
        raw=num(v.get("confidence"),-1)
        factor=factors.get(MODEL_NAMES[name],1.0)
        if name in ("Qwen","DeepSeek") and v.get("model_id")!=MODEL_NAMES[name]:factor=1.0
        if 0<=raw<=1:v["confidence"]=raw*factor
        adjusted[name]=v
    # Fast AI repeats quantitative evidence. The cloud reviewer also sees
    # local votes, so corroboration is discounted rather than counted fully.
    cloud=adjusted["GPT-OSS 120B"]
    dependent=current and valid_vote(cloud) and any(valid_vote(adjusted[n]) and adjusted[n]["direction"]==cloud["direction"] for n in ("Qwen","DeepSeek"))
    if dependent:weights["GPT-OSS 120B"]*=.8
    result=fuse_live_decision(base,{**(council or {}),"votes":adjusted},model_weights=weights)
    scores=(result.get("hybrid") or {}).get("combined_scores") or {}
    choices=base.get("directional_candidates") or {}
    eligible=[s for s in SIDES if isinstance(choices.get(s),dict) and 1<=num(choices[s].get("strategy_id"))<=200]
    margin=abs(num(scores.get("BUY"))-num(scores.get("SELL")))
    # Small relative margin is a research WAIT, not an added live gate.
    relative=margin/max(.000001,abs(num(scores.get("BUY")))+abs(num(scores.get("SELL"))))
    direction=result.get("decision","WAIT")
    if len(eligible)==2 and relative<.04:direction="WAIT"
    return {"direction":direction,"confidence":result.get("confidence") if direction in SIDES else 0.0,
        "strategy_id":result.get("strategy_id") if direction in SIDES else 0,
        "combination_id":result.get("combination_id") if direction in SIDES else 0,
        "weights":{n:round(w,5) for n,w in weights.items()},
        "regime":regime,"lane":lane,"eligible":eligible,"relative_margin":round(relative,6),
        "models":(result.get("hybrid") or {}).get("model_contributions") or [],
        "profile_evidence":evidence,"dependent_cloud_discount":bool(dependent),
        "execution_authorized":False}

def observe(client,symbol,timeframe,base,council,feedback,tick,now):
    """At most two paired observations per bar; no I/O, no execution."""
    if str(timeframe).upper() not in ("M1","PERIOD_M1"):return None
    bar=int(num(base.get("bar_time")))
    age=now-bar-60
    if bar<=0 or not 0<=age<60:return None
    if int(num((council or {}).get("bar_time")))!=bar:return None
    bid,ask=num(tick.get("bid")),num(tick.get("ask"))
    atr=num((base.get("context") or {}).get("atr"))
    if not 0<bid<=ask or atr<=0:return None
    lane="EARLY" if age<20 else "REVIEW"
    key=(str(client),str(symbol).upper(),bar,lane)
    with LOCK:
        if key in SEEN:return None
        SEEN[key]=now
        if len(SEEN)>2048:
            for k in sorted(SEEN,key=SEEN.get)[:512]:SEEN.pop(k,None)
    old=v1_decide(base,council,feedback)
    new=candidate(base,council,feedback,lane)
    votes=market_votes(council)
    payload={"version":VERSION,"symbol":symbol,"timeframe":"M1","bar_time":bar,"lane":lane,
        "regime":new["regime"],"forecast_time_unix":now,"delay_sec":round(age,3),
        "bid":bid,"ask":ask,"atr":atr,
        "policies":{"QUANT":base.get("decision","WAIT"),"V1":old.get("decision","WAIT"),"V2":new["direction"]},
        "candidate":new,"votes":{n:{"direction":v.get("direction"),"confidence":v.get("confidence")}
            for n,v in votes.items() if n in BASE and valid_vote(v)},
        "execution_authorized":False,"metric":"NEXT_CLOSE_QUOTE_PROXY_NOT_BROKER_FILL"}
    eid="master-v2:"+":".join(map(str,key))
    with LOCK:
        if len(PENDING)>=MAX_PENDING:PENDING.pop(next(iter(PENDING)),None)
        PENDING[key]=(eid,payload)
    return {"event_id":eid,"client_id":str(client),"event_type":"MASTER_V2_FORECAST","payload":payload}

def settle(client,symbol,timeframe,candles):
    if str(timeframe).upper() not in ("M1","PERIOD_M1"):return []
    closed={int(num(c.get("time"))):c for c in candles or [] if isinstance(c,dict)}
    if not closed:return []
    latest=max(closed);result=[]
    with LOCK:
        for key,(eid,p) in list(PENDING.items()):
            if key[:2]!=(str(client),str(symbol).upper()):continue
            target=p["bar_time"]+60
            if latest<target:continue
            PENDING.pop(key,None)
            close=num((closed.get(target) or {}).get("close"))
            if close<=0:continue
            def payoff(side):
                if side=="BUY":return close-p["ask"]
                if side=="SELL":return p["bid"]-close-(p["ask"]-p["bid"])
                return 0.0 if side=="WAIT" else None
            policies={}
            for name,side in p["policies"].items():
                v=payoff(side)
                if v is not None:policies[name]={"direction":side,"net_atr":v/p["atr"]}
            model_results={}
            for name,v in p["votes"].items():
                net=payoff(v["direction"])
                if net is not None:model_results[name]={"net_atr":net/p["atr"],"direction":v["direction"]}
            eligible=p["candidate"]["eligible"]
            missed=p["policies"]["V2"]=="WAIT" and any(payoff(s)>0 for s in eligible)
            payload={**p,"next_bar_time":target,"next_close":close,"policy_results":policies,
                     "model_results":model_results,"v2_wait_missed_proxy":bool(missed),"actual_trade_proven":False}
            result.append({"event_id":eid+":outcome","client_id":str(client),"event_type":"MASTER_V2_COMPARISON","payload":payload})
    return result

def summarize(rows,symbol):
    lanes={};seen=set()
    for row in rows or []:
        p=row.get("payload") if isinstance(row,dict) else None
        if not isinstance(p,dict) or p.get("version")!=VERSION or not same_market_symbol(p.get("symbol"),symbol):continue
        lane=p.get("lane");bar=int(num(p.get("bar_time")))
        if lane not in ("EARLY","REVIEW") or bar<=0 or (lane,bar) in seen:continue
        results=p.get("policy_results") or {}
        if not all(n in results for n in ("QUANT","V1","V2")):continue
        seen.add((lane,bar))
        g=lanes.setdefault(lane,{"paired":0,"v2_wait_missed_proxy":0,"policies":{}})
        g["paired"]+=1;g["v2_wait_missed_proxy"]+=int(p.get("v2_wait_missed_proxy") is True)
        for name in ("QUANT","V1","V2"):
            r=results[name];v=num(r.get("net_atr"));side=r.get("direction")
            q=g["policies"].setdefault(name,{"signals":0,"positive":0,"wait":0,"net_atr":0.0})
            q["signals"]+=int(side in SIDES);q["wait"]+=int(side=="WAIT")
            q["positive"]+=int(side in SIDES and v>0);q["net_atr"]+=v
    for g in lanes.values():
        for q in g["policies"].values():
            q["net_atr"]=round(q["net_atr"],5)
            q["positive_rate"]=round(q["positive"]/q["signals"],5) if q["signals"] else None
    return {"version":VERSION,"mode":"SHADOW_ONLY","profiles":profile(rows,symbol),"lanes":lanes,
            "metric":"NEXT_CLOSE_QUOTE_PROXY_NOT_BROKER_FILL","live_execution_changed":False}
