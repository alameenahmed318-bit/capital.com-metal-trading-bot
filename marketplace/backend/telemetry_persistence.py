"""Non-blocking, idempotent persistence of MT5 closed-deal snapshots.

A live telemetry POST never waits for a Supabase historical upsert.
Latest snapshot wins; only one worker per client. Failed saves are retried on
the next heartbeat. No license or execution state is modified by this module.
"""
import hashlib
import json
import threading
from datetime import datetime,timezone

_LOCK=threading.RLock()
_STATE={}
_MAX_CLIENTS=256

def _prepare(client_id,deals):
    persisted=[]
    for deal in deals or []:
        deal_id=str(deal.get("ticket") or "")
        stamp=int(deal.get("time") or 0)
        if not deal_id or stamp<=0:continue
        persisted.append({"client_id":client_id,"deal_id":deal_id,
            "deal_time":datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
            "payload":deal})
    persisted.sort(key=lambda x:x["deal_id"])
    return persisted

def _digest(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,
        separators=(",",":"),ensure_ascii=False).encode("utf-8")).hexdigest()

def queue_closed_deals(client_id,deals,sb):
    """Return immediately. Persist only when data actually changed."""
    if not deals:return False
    rows=_prepare(client_id,deals)
    if not rows:return False
    fingerprint=_digest(rows)
    with _LOCK:
        state=_STATE.get(client_id)
        if state is None:
            if len(_STATE)>=_MAX_CLIENTS:
                # Refuse to start unbounded worker threads if many tenants arrive.
                return False
            state={"saved":None,"pending":None,"running":False}
            _STATE[client_id]=state
        if fingerprint==state["saved"]:return False
        already_pending=bool(state["pending"] and state["pending"][0]==fingerprint)
        if not already_pending:
            state["pending"]=(fingerprint,rows)
        if state["running"]:return not already_pending
        state["running"]=True
    threading.Thread(target=_worker,args=(client_id,sb),
        name="alzool-closed-history",daemon=True).start()
    return True

def _worker(client_id,sb):
    while True:
        with _LOCK:
            state=_STATE[client_id]
            job=state["pending"]
            if job is None:
                state["running"]=False
                return
            state["pending"]=None
        fingerprint,rows=job
        try:
            sb("POST","/rest/v1/alzool_engine_closed_deals?on_conflict=client_id,deal_id",
                rows,admin=True,prefer="resolution=merge-duplicates")
        except Exception as exc:
            print("CLOSED_DEALS_ASYNC_RETRY:",type(exc).__name__,flush=True)
            with _LOCK:
                state=_STATE[client_id]
                # Preserve a newer snapshot; otherwise retry on next heartbeat.
                if state["pending"] is None:
                    state["pending"]=(fingerprint,rows)
                state["running"]=False
            return
        with _LOCK:
            _STATE[client_id]["saved"]=fingerprint
