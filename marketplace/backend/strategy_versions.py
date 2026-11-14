"""AL-ZOOL versioned, owner-controlled strategy weights.

Version activation never enables trading and never changes risk settings.
Profiles are stored in Supabase via the privileged backend only.
"""
import threading
import time
import urllib.parse
from strategy_experiments import sanitize_profile

_LOCK=threading.RLock()
_ACTIVE={}
_TTL=20

def _query(client_id):
    return urllib.parse.quote(client_id,safe="")

def versions(client_id,sb):
    return sb("GET","/rest/v1/alzool_engine_strategy_versions?client_id=eq."+
        _query(client_id)+"&select=id,version_label,status,profile,test_summary,created_at&order=created_at.desc&limit=40",admin=True)

def get_active(client_id,sb):
    with _LOCK:
        item=_ACTIVE.get(client_id)
        if item and time.monotonic()-item["at"]<_TTL:return item["profile"]
    rows=sb("GET","/rest/v1/alzool_engine_strategy_versions?client_id=eq."+
        _query(client_id)+"&status=eq.ACTIVE&select=profile&limit=1",admin=True)
    profile=sanitize_profile((rows[0]["profile"] or {}).get("strategy_weights",{}),
        (rows[0]["profile"] or {}).get("combination_weights",{})) if rows else None
    with _LOCK:_ACTIVE[client_id]={"at":time.monotonic(),"profile":profile}
    return profile

def _insert(client_id,label,profile,test,status,sb):
    payload={"client_id":client_id,"version_label":label,"profile":profile,
        "test_summary":test,"status":status}
    result=sb("POST","/rest/v1/alzool_engine_strategy_versions",payload,
        admin=True,prefer="return=representation")
    if not result:raise RuntimeError("Version insert returned no record")
    return result[0]

def save_candidate(client_id,profile,test,sb):
    profile=sanitize_profile(profile.get("strategy_weights",{}),profile.get("combination_weights",{}))
    # Preserve a safe initial rollback target with default weights, no trading.
    existing=versions(client_id,sb)
    if not existing:
        _insert(client_id,"BASELINE_DEFAULT",{"strategy_weights":{},"combination_weights":{}},
            {"source":"default_weights","auto_activate":False},"ACTIVE",sb)
    label="EXP_"+str(int(time.time()))+"_"+str(time.monotonic_ns()%1000000)
    return _insert(client_id,label,profile,test,"CANDIDATE",sb)

def activate(client_id,version_id,sb):
    """Switch a stored profile, restoring the previous record if second update fails."""
    try:
        version_id=int(version_id)
    except (TypeError,ValueError):raise ValueError("Invalid version ID")
    if version_id<=0:raise ValueError("Invalid version ID")
    client=_query(client_id)
    path="/rest/v1/alzool_engine_strategy_versions?"
    target=sb("GET",path+"id=eq."+str(version_id)+"&client_id=eq."+client+
        "&select=id,status,profile&limit=1",admin=True)
    if not target:raise ValueError("Version does not belong to this owner")
    if target[0]["status"]=="ACTIVE":
        return {"id":version_id,"status":"ACTIVE","message":"Already active"}
    if target[0]["status"] not in ("CANDIDATE","ARCHIVED"):
        raise ValueError("Version cannot be activated")
    # The database allows at most one ACTIVE row. Always archive first.
    current=sb("GET",path+"client_id=eq."+client+
        "&status=eq.ACTIVE&select=id&limit=1",admin=True)
    previous=current[0]["id"] if current else None
    if previous:
        sb("PATCH",path+"id=eq."+str(previous)+"&client_id=eq."+client,
            {"status":"ARCHIVED"},admin=True,prefer="return=representation")
    try:
        changed=sb("PATCH",path+"id=eq."+str(version_id)+"&client_id=eq."+client,
            {"status":"ACTIVE"},admin=True,prefer="return=representation")
        if not changed:raise RuntimeError("Version activation returned no record")
    except Exception:
        if previous:
            try:sb("PATCH",path+"id=eq."+str(previous)+"&client_id=eq."+client,
                {"status":"ACTIVE"},admin=True,prefer="return=representation")
            except Exception:pass
        raise
    with _LOCK:_ACTIVE.pop(client_id,None)
    return {"id":version_id,"previous_id":previous,"status":"ACTIVE",
        "message":"Weights selected; trading state unchanged"}
