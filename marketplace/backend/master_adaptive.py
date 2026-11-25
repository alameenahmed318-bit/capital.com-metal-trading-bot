"""MASTER ADAPTIVE research only: no permissions, no execution, no settings writes.
Use current-bar evidence and actual observed quotes; never count correlated
models as seven independent confirmations. All thresholds are pilot hypotheses.
"""
import math
import re
from collections import defaultdict
from hybrid_decision import fuse_live_decision
from master_ai import ARENA_WEIGHTS, WEIGHTS
from market_symbols import same_market_symbol

VERSION="MASTER_ADAPTIVE_SHADOW_V1"
SIDES=("BUY","SELL")
FAMILIES={**{n:"TREES" for n in ("LightGBM","XGBoost","CatBoost")},
          **{n:"FORECAST" for n in ("TiRex-2","Chronos-2","TimesFM-2.5","Chronos-Bolt Tiny")},
          **{n:"LANGUAGE" for n in ("Qwen","DeepSeek","GPT-OSS 120B")}}
MODELS=tuple(ARENA_WEIGHTS)

def num(x,default=0.):
    try:
        if isinstance(x,bool):return default
        v=float(x)
        return v if math.isfinite(v) else default
    except (TypeError,ValueError,OverflowError):return default

def clamp(x,lo=0.,hi=1.):return max(lo,min(hi,x))

def profile(rows,symbol,now):
    """Use first valid directional observation per model/bar, horizon 180 sec.
    Require two chronological partitions with the same sign; never learn from
    an outcome not yet mature. Keep budget unchanged and changes in research.
    """
    groups=defaultdict(dict)
    for row in rows or []:
        p=row.get("payload") or {}
        if not same_market_symbol(p.get("symbol"),symbol):continue
        c=(p.get("candidate") or {}).get("adaptive") or {}
        if c.get("version")!=VERSION:continue
        o=(p.get("outcomes") or {}).get("180")
        if not isinstance(o,dict):continue
        bar=num(p.get("bar_time"));at=num(p.get("observed_at"));end=num(o.get("quote_time"))
        if not (bar>0 and bar+60<=at<bar+120 and bar+240<=end<=min(now,bar+248)):continue
        if num(o.get("max_gap_sec"),999)>12:continue
        for name,v in (c.get("forecasts") or {}).items():
            if name not in MODELS:continue
            v=num(v)
            if abs(v)<=.05:continue
            side="BUY" if v>0 else "SELL"
            net=num((o.get("payoffs") or {}).get(side),None)
            if net is None:continue
            old=groups[name].get(bar)
            if old is None or at<old[0]:groups[name][bar]=(at,clamp(net-.10,-3.,3.))
    output={}
    for n in MODELS:
        # Non-overlapping three-minute origins for validation.
        data=[];last=0
        for bar,(_,value) in sorted(groups[n].items()):
            if bar-last>=180:data.append(value);last=bar
        data=data[-300:];cut=int(len(data)*.7);tr=data[:cut];va=data[cut:]
        a=sum(tr)/len(tr) if tr else 0.;b=sum(va)/len(va) if va else 0.
        factor=1.;status="INSUFFICIENT"
        if len(tr)>=80 and len(va)>=40:
            status="NEUTRAL"
            if a>.03 and b>.03:factor=1.2;status="POSITIVE_BOTH"
            elif a<-.03 and b<-.03:factor=.8;status="NEGATIVE_BOTH"
        output[n]={"samples":len(data),"train":len(tr),"validation":len(va),"train_mean":round(a,5),
                   "validation_mean":round(b,5),"factor":factor,"status":status}
    return output

def candidate_weights(feedback,symbol,now):
    p=(feedback or {}).get("master_adaptive") or {}
    fresh=same_market_symbol((feedback or {}).get("symbol"),symbol) and 0<=now-num(p.get("updated_at_unix"))<=600
    evidence=p.get("model_profiles") or {} if fresh else {}
    weights={n:w*clamp(num((evidence.get(n) or {}).get("factor"),1.),.8,1.2) for n,w in ARENA_WEIGHTS.items()}
    total=sum(weights.values())
    return {n:round(w*sum(ARENA_WEIGHTS.values())/total,8) for n,w in weights.items()}

def evaluate(base,current,arena,feedback,tick,settings,now):
    """Parallel candidate. Identical strategy eligibility, no forced waits
    for absent legacy models, price guard preserved. No authorization.
    """
    bar=num(base.get("bar_time"));age=now-bar-60
    master=current.get("master") or {}
    rows={r["model"]:r for r in master.get("models",[]) if isinstance(r,dict) and "model" in r}
    weights={**WEIGHTS,**candidate_weights(feedback,base.get("symbol"),now)}
    votes={n:{"status":r.get("status"),"direction":r.get("direction"),"confidence":r.get("confidence"),
             "reason_ar":r.get("reason_ar","")} for n,r in rows.items() if r.get("used") is True}
    fused=fuse_live_decision(base,{"bar_time":bar,"votes":votes},model_weights=weights)
    side=fused.get("decision","WAIT");opposite="SELL" if side=="BUY" else "BUY"
    ctx=base.get("context") or {};scores=fused["hybrid"]["combined_scores"]
    valid=[r for r in fused["hybrid"]["model_contributions"] if r.get("used") and r["model"] in FAMILIES]
    groups=defaultdict(lambda:{"BUY":0.,"SELL":0.})
    for r in valid:groups[FAMILIES[r["model"]]][r["direction"]]+=num(r["points"])
    supporting=sum(g.get(side,0)>g.get(opposite,0) for g in groups.values())
    # Equal influence per evidence family in agreement metric, irrespective
    # of number of correlated models. Fusion weights remain bounded.
    support=sum(g.get(side,0)/max(1e-12,sum(g.values())) for g in groups.values())/len(groups) if groups else 0.
    margin=clamp((num(scores.get(side))-num(scores.get(opposite)))/max(1e-9,sum(abs(num(v)) for v in scores.values())))
    mq=clamp(num(ctx.get(str(side).lower()+"_quality"),50.)/100.)
    trap=ctx.get(str(side).lower()+"_trap") is True
    pressure=(1 if side=="BUY" else -1)*num(ctx.get("last_closed_candle_pressure"))
    quality=clamp(.40*num(fused.get("confidence"))+.25*margin+.20*mq+.15*support-(.10 if trap else 0.))
    atr=num(base.get("entry_atr") or ctx.get("atr"));ref=num(base.get("entry_reference_price"))
    bid=num(tick.get("bid"));ask=num(tick.get("ask"));price=ask if side=="BUY" else bid
    deviation=abs(price-ref)/atr if atr>0 and ref>0 and 0<bid<=ask else None
    pm=(settings or {}).get("position_management") or {}
    price_ok=deviation is not None and (pm.get("entry_guard_enabled",True) is not True or 0<num(pm.get("entry_max_deviation_atr"),.5)>=deviation)
    strong=quality>=.80 and support>=.75 and supporting>=2 and pressure>=.25 and not trap
    medium=quality>=.68 and support>=.65 and supporting>=2 and not trap
    target=2 if strong else 5 if medium else 10
    reason="READY_RESEARCH"
    if side not in SIDES:reason="NO_STRATEGY"
    elif not 0<=age<60:reason="STALE_BAR"
    elif not price_ok:reason="PRICE_MOVED_OR_MISSING"
    elif trap or quality<.55 or support<.55 or supporting<1:reason="WEAK_OR_TRAP"
    elif age<target:reason="COLLECTING"
    elif age>15:reason="RESEARCH_WINDOW_EXPIRED"
    forecasts={}
    if isinstance(arena,dict) and arena.get("symbol")==base.get("symbol") and arena.get("bar_time")==base.get("bar_time") and 0<=now-num(arena.get("received_at"))<=60:
        forecasts={n:num(v) for n,v in (arena.get("models") or {}).items() if n in MODELS and num(v,None) is not None}
    return {"version":VERSION,"direction":side if reason=="READY_RESEARCH" else "WAIT","candidate_direction":side,
            "strategy_id":fused.get("strategy_id"),"combination_id":fused.get("combination_id"),
            "quality":round(quality,4),"support":round(support,4),"supporting_families":supporting,
            "families":list(groups),"delay_target_sec":target,"age_sec":round(age,3),"reason":reason,
            "price_deviation_atr":round(deviation,4) if deviation is not None else None,
            "weights":weights,"scores":scores,"forecasts":forecasts,"execution_authorized":False}

def management(candidate,arena,positions,symbol):
    """Action-specific evidence plus current direction. Research only, not a
    basket P&L prediction; recovery remains dependent on existing settings.
    """
    out={};scores=candidate.get("scores") or {}
    buy=num(scores.get("BUY"));sell=num(scores.get("SELL"))
    bias=clamp((buy-sell)/max(1.,abs(buy)+abs(sell)),-1,1)
    for side,typ in (("BUY",0),("SELL",1)):
        group=[p for p in positions or [] if p.get("symbol")==symbol and num(p.get("type"),-1)==typ]
        if not group:continue
        directional=bias if side=="BUY" else -bias
        actions={}
        for action in ("ADDON","COOLING","RECOVERY"):
            value=num(((arena or {}).get("actions") or {}).get(action,{}).get(side),None)
            if value is None:continue
            score=.75*clamp(value,-3,3)+.25*directional
            actions[action]={"specialist_atr":value,"candidate_score":round(score,5),
                             "assessment":"SUPPORT" if score>.10 else "CAUTION" if score<-.10 else "NEUTRAL"}
        net=sum(num(p.get("profit"))+num(p.get("swap")) for p in group)
        out[side]={"actions":actions,"floating_before_unrecorded_fees":round(net,4),
                   "exit_research":"REVIEW_PROFIT" if net>0 and directional<-.25 else "OBSERVE",
                   "direction_support":round(directional,4),"execution_authorized":False}
    return out

def attribution(deals,symbol,audits):
    """Exact broker position identifier joins, complete volume/fees. Unique
    same-side strategy-tag/time audit is a candidate link, not order-ID proof.
    """
    groups=defaultdict(dict)
    for d in deals or []:
        if not isinstance(d,dict) or not same_market_symbol(d.get("symbol"),symbol):continue
        pid=str(d.get("position_id") or "");ticket=str(d.get("ticket") or "")
        if pid and ticket:groups[pid][ticket]=d
    by_tag=defaultdict(list)
    for row in audits or []:
        p=row.get("payload") or {}
        if p.get("status")!="AUTHORIZED" or not same_market_symbol(p.get("symbol"),symbol):continue
        from datetime import datetime
        try:at=datetime.fromisoformat(str(p.get("decision_at")).replace("Z","+00:00")).timestamp()
        except (ValueError,TypeError,OverflowError):continue
        key=(num(p.get("strategy_id")),num(p.get("combination_id")),p.get("direction"))
        by_tag[key].append((at,p))
    result={"closed_position_ids":0,"complete_positions":0,"strategy_linked":0,
            "unique_audit_candidates":0,"complete_net":0.,"by_action":{},"models":{},
            "execution_authorized":False,"metric":"COMPLETE_BROKER_POSITION_NET_AUDIT_MATCH_NOT_CAUSAL"}
    for pid,ds in groups.items():
        rows=list(ds.values());entries=[d for d in rows if num(d.get("entry"),-1)==0];exits=[d for d in rows if num(d.get("entry"),-1) in (1,3)]
        if not exits:continue
        result["closed_position_ids"]+=1
        if not entries or len(entries)+len(exits)!=len(rows):continue
        vols=[num(d.get("volume"),None) for d in rows]
        if any(v is None or v<=0 for v in vols):continue
        if abs(sum(num(d["volume"]) for d in entries)-sum(num(d["volume"]) for d in exits))>1e-7:continue
        values=[num(d.get(k),None) for d in rows for k in ("profit","commission","swap","fee")]
        if any(v is None for v in values):continue
        sides={num(d.get("type"),-1) for d in entries}
        if sides not in ({0.},{1.}):continue
        if max(num(d.get("time")) for d in entries)>min(num(d.get("time")) for d in exits):continue
        side="BUY" if sides=={0.} else "SELL"
        net=sum(values);result["complete_positions"]+=1;result["complete_net"]+=net
        tags={str(d.get("comment") or d.get("strategy_comment") or "").upper() for d in entries}
        if len(tags)!=1:continue
        tag=next(iter(tags))
        action=next((a for prefix,a in (("ALZOOL-ADD","ADDON"),("ALZOOL-COOL","COOLING"),("ALZOOL-RECOVERY","RECOVERY")) if tag.startswith(prefix)),None)
        match=re.fullmatch(r"ALZOOL-S(\d{1,3})-C(\d{1,4})",tag)
        if match:
            sid,cid=map(int,match.groups())
            if 1<=sid<=200 and 1<=cid<=1000:
                result["strategy_linked"]+=1;action="ENTRY"
                if len(entries)==1:
                    start=num(entries[0].get("time"))
                    options=[p for t,p in by_tag.get((sid,cid,side),[]) if -1<=start-t<=3]
                    if len(options)==1:
                        result["unique_audit_candidates"]+=1
                        for n in set((options[0].get("arena_advisory") or {}).get("used") or []):
                            if n not in MODELS:continue
                            v=result["models"].setdefault(n,{"positions":0,"net":0.})
                            v["positions"]+=1;v["net"]+=net
        if action:
            v=result["by_action"].setdefault(action+":"+side,{"positions":0,"net":0.})
            v["positions"]+=1;v["net"]+=net
    result["complete_net"]=round(result["complete_net"],4)
    for g in (result["models"],result["by_action"]):
        for v in g.values():v["net"]=round(v["net"],4)
    return result

def summarize(rows,symbol,now):
    comparisons=defaultdict(dict)
    for row in rows or []:
        p=row.get("payload") or {}
        if not same_market_symbol(p.get("symbol"),symbol):continue
        c=p.get("candidate") or {};a=c.get("adaptive") or {}
        o=(p.get("outcomes") or {}).get("180")
        if a.get("version")!=VERSION or not isinstance(o,dict) or num(o.get("max_gap_sec"),999)>12:continue
        lane=p.get("lane");bar=num(p.get("bar_time"));pay=o.get("payoffs") or {}
        old=c.get("adaptive_baseline","WAIT");new=a.get("direction","WAIT")
        if old not in (*SIDES,"WAIT") or new not in (*SIDES,"WAIT") or not all(s in pay for s in SIDES):continue
        comparisons[lane].setdefault(bar,(num(pay.get(old))-.10 if old in SIDES else 0.,num(pay.get(new))-.10 if new in SIDES else 0.,old,new))
    out={}
    for lane,items in comparisons.items():
        samples=[];last=0
        for b,r in sorted(items.items()):
            if b-last>=180:samples.append(r);last=b
        cut=int(len(samples)*.7)
        def stats(ds):
            return {"n":len(ds),"baseline_net":round(sum(x[0] for x in ds),4),"adaptive_net":round(sum(x[1] for x in ds),4),
                    "baseline_signals":sum(x[2] in SIDES for x in ds),"adaptive_signals":sum(x[3] in SIDES for x in ds)}
        out[str(lane)]={"train":stats(samples[:cut]),"validation":stats(samples[cut:]),"all":stats(samples),"ready_for_review":cut>=80 and len(samples)-cut>=40}
    return {"version":VERSION,"updated_at_unix":now,"model_profiles":profile(rows,symbol,now),
            "comparisons":out,"mode":"SHADOW_ONLY","execution_authorized":False}
