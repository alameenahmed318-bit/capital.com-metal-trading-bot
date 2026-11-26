"""Owner-scoped research evidence; no order routes, no changes to the live master."""
import math,re,urllib.parse
from datetime import datetime,timezone,timedelta
def num(v):
    try:
        f=float(v)
        return f if math.isfinite(f) and not isinstance(v,bool) else None
    except (ValueError,TypeError,OverflowError):return None
def enc(v):return urllib.parse.quote(str(v),safe="")
def complete_positions(rows,symbol):
    groups={};seen=set()
    for row in rows:
        d=row.get("payload") or {}
        if not isinstance(d,dict) or d.get("symbol")!=symbol:continue
        pid=str(d.get("position_id") or "");ticket=str(d.get("ticket") or "")
        if not pid or not ticket or ticket in seen:continue
        seen.add(ticket)
        g=groups.setdefault(pid,{"open":0.,"close":0.,"net":0.,"sides":set(),"start":None,"end":0.,"bad":False})
        at=num(d.get("time"));v=num(d.get("volume"));kind=num(d.get("entry"))
        costs=[num(d.get(k)) for k in ("profit","commission","swap","fee")]
        if at is None or at<=0 or v is None or v<=0 or None in costs:
            g["bad"]=True;continue
        g["net"]+=sum(costs)
        if kind==0:
            side=num(d.get("deal_type",d.get("type")))
            if side not in (0,1):g["bad"]=True;continue
            g["sides"].add(int(side));g["open"]+=v;g["start"]=min(g["start"] or at,at)
        elif kind in (1,3):g["close"]+=v;g["end"]=max(g["end"],at)
        else:g["bad"]=True
    return sorted([{"opened":int(g["start"]),"closed":int(g["end"]),"side":next(iter(g["sides"])),"positive":g["net"]>0}
        for g in groups.values() if not g["bad"] and g["start"] and g["end"]>=g["start"] and len(g["sides"])==1
        and g["open"]>0 and abs(g["open"]-g["close"])<1e-7],key=lambda x:(x["opened"],x["closed"]))
def read_evidence(sb,cid,symbol):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}",symbol):raise ValueError("invalid symbol")
    scope="client_id=eq."+enc(cid)
    runs=sb("GET","/rest/v1/alzool_engine_learning_runs?"+scope+"&symbol=eq."+enc(symbol)+"&select=run_key,slippage_points,created_at,validation_points&order=created_at.desc&limit=100",admin=True)
    if not isinstance(runs,list):raise ValueError("invalid backtests")
    # Only cost settings from saved, validated runs are applicable to the new models.
    # Existing strategy-level results are not model-specific labels.
    slips=[num(r.get("slippage_points")) for r in runs if (num(r.get("validation_points")) or 0)>0]
    slips=[x for x in slips if x is not None and 0<=x<=100000]
    rows=[];cut=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
    q="/rest/v1/alzool_engine_closed_deals?"+scope+"&"+enc("payload->>symbol")+"=eq."+enc(symbol)+"&deal_time=gte."+enc(cut)+"&select=payload&order=deal_time.desc,deal_id.desc"
    for offset in range(0,5000,1000):
        page=sb("GET",q+"&limit=1000&offset="+str(offset),admin=True)
        if not isinstance(page,list):raise ValueError("invalid trade data")
        rows.extend(page)
        if len(page)<1000:break
    return {"symbol":symbol,"backtests":len(runs),"slippage_points":sorted(slips)[len(slips)//2] if slips else None,
            "positions":complete_positions(rows,symbol),"execution_authorized":False,
            "note":"Saved backtest costs + completed-position outcome labels, not causal model attribution"}
def sanitize_report(r):
    if not isinstance(r,dict):return {}
    out={}
    for k in ("backtests","trade_samples","persisted_samples"):
        v=num(r.get(k))
        if v is not None and 0<=v<=100000:out[k]=int(v)
    for k in ("trade_accuracy","trade_probability"):
        v=num(r.get(k))
        if v is not None and 0<=v<=1:out[k]=v
    if r.get("trade_status") in ("INSUFFICIENT","VALIDATED","NOT_VALIDATED","UNAVAILABLE"):out["trade_status"]=r["trade_status"]
    if isinstance(r.get("paused"),list):out["paused"]=[x for x in r["paused"][:6] if x in ("TCN","PatchTST","LSTM Multi","Patch Transformer","LSTM","Isolation Forest")]
    return out
