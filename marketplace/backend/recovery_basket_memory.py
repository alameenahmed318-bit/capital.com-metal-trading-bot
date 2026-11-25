"""Observed recovery baskets: exact identifiers, no guessed trade memberships.
Diagnostic only; never grants an order or changes owner controls.
"""
from datetime import datetime
import math
from market_symbols import same_market_symbol

def num(x):
    try:
        if isinstance(x,bool): return None
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError,OverflowError): return None

def timestamp(x):
    try:
        d=datetime.fromisoformat(str(x).replace("Z","+00:00"))
        return d.timestamp() if d.tzinfo else None
    except (ValueError,TypeError,OverflowError): return None

def claim_members(snapshot):
    result=[]
    seen=set()
    symbol=snapshot.get("symbol")
    for p in snapshot.get("positions") or []:
        pid=str(p.get("position_id") or "")
        lot=num(p.get("volume"))
        side=num(p.get("type"))
        if (not pid.isdigit() or int(pid)<=0 or pid in seen or
            not same_market_symbol(p.get("symbol"),symbol) or
            lot is None or lot<=0 or side not in (0,1)):
            return []
        seen.add(pid)
        result.append({"position_id":pid,"volume":lot,"side":"BUY" if side==0 else "SELL"})
    return result

def summarize(claims,deals,symbol):
    by_id={};seen=set();ambiguous=set()
    for d in deals or []:
        if not isinstance(d,dict) or not same_market_symbol(d.get("symbol"),symbol):continue
        pid=str(d.get("position_id") or "");ticket=str(d.get("ticket") or "")
        if not pid or not ticket or ticket in seen:continue
        seen.add(ticket);by_id.setdefault(pid,[]).append(d)
    reports=[]
    for row in claims or []:
        p=row.get("payload") or {}
        if p.get("action")!="RECOVERY_PROPOSAL" or not same_market_symbol(p.get("symbol"),symbol):continue
        t=timestamp(p.get("claimed_at"));lot=num(p.get("lot_cap"));members=p.get("basket_members") or []
        side=p.get("side"); expected_side=0 if side=="BUY" else 1 if side=="SELL" else None
        if t is None or not members or lot is None or lot<=0 or expected_side is None:continue
        ids={str(x.get("position_id")):num(x.get("volume")) for x in members}
        if len(ids)!=len(members) or any(v is None or v<=0 for v in ids.values()):continue
        # Only unique actual broker recovery entries near the recorded one-use
        # claim, matching its side and exact authorized volume. Ambiguity stays unknown.
        candidates=[]
        for pid,ds in by_id.items():
            if pid in ids:continue
            for d in ds:
                stamp=num(d.get("time"));vol=num(d.get("volume"))
                if (num(d.get("entry"))==0 and str(d.get("comment") or "").startswith("ALZOOL-RECOVERY")
                    and num(d.get("type",d.get("deal_type")))==expected_side
                    and stamp is not None and t-1<=stamp<=t+15 and vol is not None and abs(vol-lot)<1e-7):
                    candidates.append(pid)
        report={"claimed_at":p.get("claimed_at"),"side":side,"lot":lot,"members":len(ids)+1,
                "status":"UNVERIFIED_ENTRY","full_net":None,"exit_net":None}
        if len(candidates)!=1:
            reports.append(report);continue
        ids[candidates[0]]=lot
        complete=True;exit_complete=True;exit_net=0.;full_net=0.
        for pid,volume in ids.items():
            ds=by_id.get(pid,[])
            ins=outs=0.;net=0.;exit_total=0.;prior_exit=False;valid=True
            for d in ds:
                e=num(d.get("entry"));v=num(d.get("volume"));stamp=num(d.get("time"))
                vals=[num(d.get(k)) for k in ("profit","swap","commission","fee")]
                if e not in (0,1,3) or v is None or v<=0 or stamp is None or None in vals:
                    valid=False;continue
                value=sum(vals);net+=value
                if e==0:
                    ins+=v
                    # Extra entry after claim for a pre-existing position is not an exact basket.
                    if pid!=candidates[0] and stamp>t:valid=False
                elif stamp>=t-1:
                    outs+=v;exit_total+=value
                else:prior_exit=True
            if not valid or abs(outs-volume)>1e-7:exit_complete=False
            if not valid or prior_exit or abs(ins-volume)>1e-7 or abs(outs-volume)>1e-7:complete=False
            exit_net+=exit_total;full_net+=net
        if exit_complete:
            report.update(status="EXIT_EVIDENCE_ONLY",exit_net=round(exit_net,4))
        if complete and exit_complete:
            report.update(status="COMPLETE_LINKED_BASKET",full_net=round(full_net,4))
        reports.append(report)
    full=[r["full_net"] for r in reports if r["full_net"] is not None]
    return {"mode":"RESEARCH_ONLY","metric":"EXACT_POSITION_LINKS_NOT_COUNTERFACTUAL",
            "complete_baskets":len(full),"positive":sum(x>0 for x in full),
            "net":round(sum(full),4),"reports":reports[:30],"execution_authorized":False}
