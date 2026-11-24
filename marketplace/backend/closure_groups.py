"""Read-only temporal grouping of MT5 exit deals. No execution authority."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
def money(v):
    try:
        n=Decimal(str(v or 0))
        return n if n.is_finite() else Decimal(0)
    except (InvalidOperation,ValueError,TypeError):
        return Decimal(0)
def closure_groups(records, accepts_symbol, window=5):
    rows=[]; seen=set()
    for record in records:
        p=record.get("payload") or {}
        symbol=str(p.get("symbol") or "")
        if not accepts_symbol(symbol) or str(p.get("entry")) not in ("1","3"):
            continue
        kind=str(p.get("deal_type",p.get("type")))
        side={"1":"BUY","0":"SELL"}.get(kind)
        if not side:continue
        ticket=str(p.get("ticket") or record.get("deal_id") or "")
        if not ticket or ticket in seen:continue
        try:stamp=int(p.get("time") or datetime.fromisoformat(record["deal_time"].replace("Z","+00:00")).timestamp())
        except (ValueError,TypeError,KeyError,OverflowError):continue
        if stamp<=0:continue
        seen.add(ticket)
        net=sum((money(p.get(k)) for k in ("profit","commission","swap","fee")),Decimal(0))
        rows.append((stamp,symbol,side,str(p.get("reason","")),ticket,str(p.get("position_id") or ticket),net,money(p.get("volume"))))
    groups=[]; active={}
    for stamp,symbol,side,reason,ticket,pid,net,vol in sorted(rows):
        key=(symbol,side,reason)
        g=active.get(key)
        if g is None or stamp-g["start"]>window:
            g={"symbol":symbol,"side":side,"start":stamp,"end":stamp,"reason":reason,"positions":set(),"net":Decimal(0),"volume":Decimal(0),"deals":[]}
            groups.append(g);active[key]=g
        g["end"]=stamp;g["positions"].add(pid);g["net"]+=net;g["volume"]+=vol
        g["deals"].append({"ticket":ticket,"position_id":pid,"time":stamp,"net":float(net),"volume":float(vol)})
    for g in groups:
        g["count"]=len(g.pop("positions"));g["deal_count"]=len(g["deals"])
        g["net"]=float(g["net"]);g["volume"]=float(g["volume"])
        g["grouping"]="TIME_ESTIMATE" if g["count"]>1 else "SINGLE_OR_PARTIAL"
    return sorted(groups,key=lambda g:g["end"],reverse=True)
