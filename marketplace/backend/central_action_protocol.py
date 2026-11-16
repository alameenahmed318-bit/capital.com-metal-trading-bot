"""One-time central MT5 position action authorization.

Fail closed by default: owner must explicitly enable central_actions_enabled in
risk settings AND MT5 must independently enable position execution. This module
only authorizes one action after a fresh council, risk and market snapshot.
Claims are recorded in Supabase with unique event IDs to survive Render restart.
"""
import hashlib
import json
import math
import secrets
import threading
import time
from datetime import datetime,timezone
from position_action_engine import plan_account_actions

_LOCK=threading.RLock()
_PENDING={}
_MAX_PENDING=200
_TTL=20

def _num(x,default=0.0):
    try:
        value=float(x)
        return value if math.isfinite(value) else default
    except (ValueError,TypeError,OverflowError):return default

def _age_seconds(snapshot,now):
    seen=snapshot.get("received_at")
    if not isinstance(seen,datetime):return 999
    if seen.tzinfo is None:seen=seen.replace(tzinfo=timezone.utc)
    return (now-seen).total_seconds()

def _fingerprint(snapshot):
    account=snapshot.get("account") or {}
    positions=snapshot.get("positions") or []
    relevant=[]
    for pos in positions:
        relevant.append((str(pos.get("ticket","")),str(pos.get("symbol","")),
            str(pos.get("type","")),round(_num(pos.get("volume")),5),
            round(_num(pos.get("open_price")),5)))
    material=[str(account.get("login","")),sorted(relevant)]
    return hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()[:20]

def _allowed(client_id,snapshot,decision,gate,council,risk,settings,now,council_advisory=False):
    def blocked(reason):return {"execution_ready":False,"reason_ar":reason,"mode":"WAIT"}
    pm=(settings or {}).get("position_management") or {}
    if not pm.get("central_actions_enabled") is True:
        return blocked("تفعيل إجراءات الإدارة المركزية مغلق من إعدادات المالك")
    if not settings.get("trading_enabled"):
        return blocked("زر التداول المركزي متوقف")
    if str(pm.get("mode","AI")).upper() not in ("AI","MANUAL"):
        return blocked("وضع الإدارة غير معروف")
    if not gate.get("approved") or (not council_advisory and not council.get("approved")) or not risk.get("approved"):
        return blocked("قرار الاستراتيجية أو حدود الحساب لا يسمحان بإجراء جديد")
    if not str(snapshot.get("symbol","")).upper().startswith("XAUUSD"):
        return blocked("المركز المدعوم حالياً هو الذهب فقط")
    if not 0<=_age_seconds(snapshot,now)<=15:
        return blocked("بيانات حساب MT5 قديمة")
    bar=int(_num(decision.get("bar_time")))
    if bar<=0 or now.timestamp()-bar>125 or bar>now.timestamp()+15:
        return blocked("قرار الشمعة غير حديث")
    planned=plan_account_actions(snapshot,decision,council,risk,settings,council_advisory=council_advisory)
    proposals=[x for x in planned.get("actions",[]) if
        x.get("action") in ("ADDON_PROPOSAL","COOLING_PROPOSAL","RECOVERY_PROPOSAL")
        and _num(x.get("lot_cap"))>0]
    if not proposals:return blocked(planned.get("reason_ar","لا توجد إجراءات صالحة"))
    if len(proposals)>1:return blocked("قرارات إدارة متضاربة")
    action=proposals[0]
    side=action.get("side")
    if side not in ("BUY","SELL") or side!=str(decision.get("decision","WAIT")):
        return blocked("اتجاه إدارة الصفقات مخالف للمجلس")
    return {"execution_ready":True,"mode":"PROPOSAL",
        "side":side,"action":action["action"],
        "lot_cap":round(_num(action["lot_cap"]),3),
        "bar_time":bar,"fingerprint":_fingerprint(snapshot),
        "reason_ar":action.get("reason_ar","")}

def propose(client_id,snapshot,decision,gate,council,risk,settings,now,council_advisory=False):
    report=_allowed(client_id,snapshot,decision,gate,council,risk,settings,now,council_advisory=council_advisory)
    if not report["execution_ready"]:return report
    with _LOCK:
        old=_PENDING.get(client_id)
        if old and not old["claimed"] and old["expires_at"]>time.time() and all(
            old.get(k)==report.get(k) for k in ("bar_time","side","action","fingerprint")):
            return {**report,"authorization_id":old["nonce"],
                "expires_at_unix":int(old["expires_at"])}
        if len(_PENDING)>_MAX_PENDING:
            expired=[k for k,v in _PENDING.items() if v["expires_at"]<=time.time()]
            for k in expired:_PENDING.pop(k,None)
        nonce=secrets.token_urlsafe(25)
        expires=time.time()+_TTL
        _PENDING[client_id]={**report,"nonce":nonce,"expires_at":expires,
            "claimed":False,"claiming":False}
        return {**report,"authorization_id":nonce,"expires_at_unix":int(expires)}

def claim(client_id,nonce,snapshot,decision,gate,council,risk,settings,db,now,council_advisory=False):
    nonce=str(nonce or "")
    verified=_allowed(client_id,snapshot,decision,gate,council,risk,settings,now,council_advisory=council_advisory)
    if not verified.get("execution_ready"):
        return {"execution_authorized":False,"reason_ar":verified["reason_ar"]}
    with _LOCK:
        state=_PENDING.get(client_id)
        if not state or not secrets.compare_digest(state["nonce"],nonce):
            return {"execution_authorized":False,"reason_ar":"لا يوجد تصريح إجراءات مطابق"}
        if state["claimed"] or state["claiming"] or state["expires_at"]<=time.time():
            return {"execution_authorized":False,"reason_ar":"التصريح مستهلك أو منتهي"}
        if any(state.get(k)!=verified.get(k) for k in
             ("bar_time","side","action","fingerprint","lot_cap")):
            return {"execution_authorized":False,"reason_ar":"تغيّر الحساب أو قرار الإدارة"}
        state["claiming"]=True
    event_id=("central-action:"+client_id+":"+str(verified["bar_time"])+":"+
        verified["side"]+":"+verified["action"])
    row={"event_id":event_id,"client_id":client_id,
        "event_type":"CENTRAL_ACTION_CLAIM",
        "payload":{"side":verified["side"],"action":verified["action"],
            "bar_time":verified["bar_time"],"lot_cap":verified["lot_cap"],
            "account_fingerprint":verified["fingerprint"],"claimed_at":now.isoformat()}}
    try:
        inserted=db("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
            row,admin=True,prefer="resolution=ignore-duplicates,return=representation")
        if not inserted:raise ValueError("التصريح استُخدم سابقاً على شمعة التداول")
    except Exception as exc:
        with _LOCK:
            current=_PENDING.get(client_id)
            if current and current["nonce"]==nonce:current["claiming"]=False
        return {"execution_authorized":False,"reason_ar":"تعذر تسجيل التصريح مرة واحدة: "+type(exc).__name__}
    with _LOCK:
        state=_PENDING.get(client_id)
        if state and state["nonce"]==nonce:state["claimed"]=True
    actual_now=datetime.now(timezone.utc)
    if not 0<=_age_seconds(snapshot,actual_now)<=15:
        return {"execution_authorized":False,"reason_ar":"انتهت صلاحية لقطة MT5 بعد تسجيل التصريح"}
    if actual_now.timestamp()-verified["bar_time"]>125:
        return {"execution_authorized":False,"reason_ar":"انتهت صلاحية قرار الشمعة"}
    return {"execution_authorized":True,"side":verified["side"],
        "action":verified["action"],"lot_cap":verified["lot_cap"],
        "bar_time":verified["bar_time"],"expires_at_unix":int(time.time()+5),
        "reason_ar":"تصريح إجراء فريد ومحفوظ؛ تحقق من حدود MT5 المحلية"}
