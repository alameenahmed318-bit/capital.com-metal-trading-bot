"""Bounded persistent closed-trade memory, read from existing Supabase history.

Memory is a performance input, not a permit to trade. A database outage never
changes the fail-closed multi-model entry gate.
"""
import threading
import time
import urllib.parse

_MEMORY={}
_LOADING=set()
_LOCK=threading.RLock()
_TTL=180
_LIMIT=5000
_PAGE=1000

def _refresh(client_id,request):
    try:
        q=urllib.parse.quote(client_id,safe="")
        # Paginate. A single PostgREST query can silently stop at the
        # server's max_rows (commonly 1000), hiding earlier real trades.
        deals=[]
        for offset in range(0,_LIMIT,_PAGE):
            rows=request("GET","/rest/v1/alzool_engine_closed_deals?client_id=eq."+q+
                         "&select=payload&order=deal_time.desc,deal_id.desc"+
                         "&limit="+str(_PAGE)+"&offset="+str(offset),admin=True)
            if not isinstance(rows,list):raise ValueError("Closed-deal page is invalid")
            deals.extend(r["payload"] for r in rows if isinstance(r,dict)
                         and isinstance(r.get("payload"),dict))
            if len(rows)<_PAGE:break
        with _LOCK:
            _MEMORY[client_id]={"deals":deals,"loaded_at":time.monotonic(),
                                "status":"READY","count":len(deals)}
    except Exception:
        with _LOCK:
            old=_MEMORY.get(client_id) or {"deals":[],"count":0}
            _MEMORY[client_id]={**old,"loaded_at":time.monotonic(),
                "status":"STALE" if old["deals"] else "UNAVAILABLE"}
    finally:
        with _LOCK:_LOADING.discard(client_id)

def memory_snapshot(client_id,request):
    with _LOCK:
        item=_MEMORY.get(client_id)
        age=time.monotonic()-item["loaded_at"] if item else _TTL+1
        if age>_TTL and client_id not in _LOADING:
            _LOADING.add(client_id)
            threading.Thread(target=_refresh,args=(client_id,request),
                             daemon=True,name="alzool-memory").start()
        item=dict(item or {"deals":[],"count":0,"status":"LOADING"})
    return item

def combine_deals(persisted,live):
    """Deduplicate stored and live deal records by deal ticket."""
    combined={}
    for source in ((persisted or [])+(live or [])):
        if not isinstance(source,dict):continue
        key=str(source.get("ticket") or "").strip()
        if not key:continue
        # Keep a real strategy link from persisted history if a newer,
        # incomplete EA snapshot carries only strategy_id=0 or an empty tag.
        previous=combined.get(key)
        if previous:
            merged={**previous,**source}
            for field in ("strategy_id","strategy_comment","comment"):
                old=previous.get(field)
                new=source.get(field)
                if (new is None or new=="" or
                    (field=="strategy_id" and str(new) in ("0","0.0"))) and old not in (None,"",0,"0"):
                    merged[field]=old
            combined[key]=merged
        else:
            combined[key]=dict(source)
    return list(combined.values())
