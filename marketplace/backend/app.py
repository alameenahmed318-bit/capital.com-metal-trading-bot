"""AL-ZOOL: licensed MT5 telemetry, central strategy analysis and account controls."""
import hashlib, hmac, json, math, os, re, secrets, threading, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from market_symbols import supported_market_symbol, same_market_symbol, market_family
from strategy_engine import evaluate_market, analyze_market_context, STRATEGIES, COMBINATIONS
from btc_adaptive_learning import snapshot as btc_learning_snapshot, effective_weights as btc_learning_weights
from decision_guard import evaluate_entry_gate, retain_current_local_ai_advice, build_qwen_vote_event, build_council_event, closed_bar_execution_fresh, entry_council_mode
from multi_model_council import update_council, council_result, risk_guardian, risk_limits_from_settings, accept_local_model_vote
from hybrid_decision import fuse_live_decision
import accuracy_v4
import feedback_learning
import master_ai
import master_entry_review
import master_balanced
import master_v2
import master_v3
import arena_master
from learning_memory import memory_snapshot, combine_deals
from post_close_confirmation import post_close_m1_confirmation,last_exit_from_deals
from live_weight_overlay import apply_live_learning, conservative_outcome_weight
from learning_observability import decision_audit_event, live_learning_health
from position_action_engine import plan_account_actions
from strategy_experiments import sanitize_profile, compare_profiles
from strategy_audit import audit_strategies
from strategy_versions import versions as strategy_versions, get_active as get_active_strategy_profile, save_candidate, activate as activate_strategy_version
from auto_research import schedule as schedule_automatic_research
from central_action_protocol import propose as propose_central_action, claim as claim_central_action
from telemetry_persistence import queue_closed_deals
from connection_resilience import database_call, DiagnosticQueue
from report_cache import archive_read, cutoff as report_cutoff
from system_readiness import owner_readiness, position_management_status, entry_execution_status
from bot_advisors import analyze_advisors
from bot_signal_bus import build_bot_messages, update_history, market_bar_fresh
from offline_m1_smoke import run as run_offline_m1_smoke
from client_onboarding import build_bundle as build_client_bundle, CLIENT_SERVER as CLIENT_ONBOARDING_URL
from client_access import (PUBLIC_UI_ASSETS, PROTECTED_OWNER_ASSETS, OWNER_READ_ENDPOINTS,
                           OWNER_WRITE_ENDPOINTS, public_static, minimal_terminal_decision,
                           basic_settings)
from groq_advisor import verify_credentials as verify_groq_credentials
from smart_profit_advice import master_bias, profit_settings_for_terminal

ROOT=Path(__file__).resolve().parents[2]
SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_ANON_KEY=os.getenv("SUPABASE_ANON_KEY","")
SUPABASE_SERVICE_ROLE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
OWNER_EMAIL=os.getenv("OWNER_EMAIL","Meenosvn1@gmail.com").strip().lower()
PUBLIC_ENGINE_URL=os.getenv("PUBLIC_ENGINE_URL","https://al-zool-meeno-engine.onrender.com").rstrip("/")
LOCAL_AI_REQUIRED=os.getenv("LOCAL_AI_REQUIRED","false").strip().lower() in ("1","true","yes","on")
# The Meeno/Deriv deployment owns BTCUSD only. Gold remains on the untouched
# original Render service and original Supabase project.
DUAL_MARKET_MODE=os.getenv("ALZOOL_MULTI_MARKET","0")=="1"
ENGINE_MARKET=os.getenv("ALZOOL_ENGINE_MARKET","BTCUSD") if DUAL_MARKET_MODE else "BTCUSD"
if ENGINE_MARKET not in ("BTCUSD","XAUUSD"):
    raise RuntimeError("Unsupported market worker")
PAIRING_HASH_FIELD="alzool_owner_mt5_hash" if ENGINE_MARKET=="BTCUSD" else "alzool_owner_gold_mt5_hash"
EXPECTED_BTC_DB_HOST="egtofwardrkkzflniikp.supabase.co"
def btc_market(symbol):
    return supported_market_symbol(symbol) and market_family(symbol)==ENGINE_MARKET
def btc_database_ready():
    """Fail closed if the Meeno server was accidentally pointed at gold's DB."""
    parsed=urllib.parse.urlparse(SUPABASE_URL)
    return (parsed.scheme=="https" and parsed.netloc==EXPECTED_BTC_DB_HOST
            and parsed.path in ("","/"))

CONNECTIONS={}
LICENSE_CACHE={}
OWNER_TERMINAL_ID_CACHE={}
MARKET_ACCOUNT_BINDINGS={}
MARKET_ACCOUNT_LOCK=threading.RLock()
TRADE_SETTINGS_CACHE={}
SETTINGS_REFRESHING=set()
AI_EVENT_CACHE={}
DIAGNOSTIC_QUEUE=DiagnosticQueue()
CACHE_LOCK=threading.RLock()
LICENSE_CACHE_TTL_SECONDS=60.0
SETTINGS_CACHE_REFRESH_SECONDS=2.0
# Report-only cache; never used for authentication, entry, settings or telemetry.
HISTORY_CACHE={}
HISTORY_LOCK=threading.Lock()
HISTORY_BUSY=set()
HISTORY_FAILURE_UNTIL=0.

def report_events_read(query):
    global HISTORY_FAILURE_UNTIL
    now=time.monotonic()
    with HISTORY_LOCK:
        cached=HISTORY_CACHE.get(query)
        if cached and now-cached[0]<60:return cached[1]
        if now<HISTORY_FAILURE_UNTIL or HISTORY_BUSY:raise RuntimeError("سجل التقارير مؤجل مؤقتاً لتخفيف ضغط قاعدة البيانات")
        HISTORY_BUSY.add(query)
    try:
        rows=sb("GET",query,admin=True)
        with HISTORY_LOCK:
            if len(HISTORY_CACHE)>=64 and query not in HISTORY_CACHE:HISTORY_CACHE.pop(min(HISTORY_CACHE,key=lambda k:HISTORY_CACHE[k][0]),None)
            HISTORY_CACHE[query]=(time.monotonic(),rows)
        return rows
    except Exception:
        with HISTORY_LOCK:HISTORY_FAILURE_UNTIL=time.monotonic()+120
        raise
    finally:
        with HISTORY_LOCK:HISTORY_BUSY.discard(query)


def sb(method,path,payload=None,bearer=None,admin=False,prefer=None):
    if DUAL_MARKET_MODE:
        if path.split("?",1)[0]=="/rest/v1/alzool_engine_clients":
            if method in ("GET","PATCH","DELETE"):
                path+=("&" if "?" in path else "?")+"market=eq."+ENGINE_MARKET
            elif method=="POST":
                if not isinstance(payload,dict):raise ValueError("Market-scoped identity required")
                payload={**payload,"market":ENGINE_MARKET}
        if ENGINE_MARKET=="XAUUSD":
            path=path.replace("/rest/v1/alzool_btc_trade_settings","/rest/v1/alzool_engine_trade_settings")
    if not SUPABASE_URL: raise RuntimeError("SUPABASE_URL is not configured")
    if not btc_database_ready():
        raise RuntimeError("BTC_SUPABASE_PROJECT_MISMATCH: refusing to access the gold database")
    key=SUPABASE_SERVICE_ROLE_KEY if admin else SUPABASE_ANON_KEY
    if not key: raise RuntimeError("Supabase key is not configured")
    headers={"apikey":key,"Accept":"application/json","Authorization":"Bearer "+(SUPABASE_SERVICE_ROLE_KEY if admin else (bearer or key))}
    data=None
    if payload is not None:
        headers["Content-Type"]="application/json"; data=json.dumps(payload,separators=(',',':')).encode()
    if prefer: headers["Prefer"]=prefer
    req=urllib.request.Request(SUPABASE_URL+path,data=data,headers=headers,method=method)
    def send(timeout):
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read(); return json.loads(raw.decode()) if raw else {}
    try:
        return database_call(send,path,method,bearer=bearer,admin=admin)
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")
        try:
            obj=json.loads(detail); detail=obj.get("msg") or obj.get("message") or detail
        except Exception: pass
        raise ValueError(str(detail)[:300])


def send_client_password_setup(email):
    """Request a normal Supabase password-recovery email; no keys/passwords emailed.

    Delivery requires Supabase Auth SMTP and CLIENT_SERVER in Auth redirect allowlist.
    A rejected request never destroys the newly created account.
    """
    email=str(email or "").strip().lower()
    try:
        target=urllib.parse.quote(CLIENT_ONBOARDING_URL+"/",safe="")
        sb("POST","/auth/v1/recover?redirect_to="+target,{"email":email})
        return "REQUEST_ACCEPTED"
    except Exception as exc:
        # Never log email, auth tokens or full provider errors.
        print("CLIENT_WELCOME_EMAIL_WARNING:",type(exc).__name__,flush=True)
        return "NOT_SENT"


import hybrid_demo_pilot
hybrid_demo_pilot.configure_memory(sb)

def persist_ai_diagnostic_event(events):
    sb("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
       events,admin=True,prefer="resolution=merge-duplicates,return=minimal")

def queue_ai_diagnostic_event(event):
    """Bound and batch research logs without blocking the decision path."""
    if not isinstance(event,dict) or not event.get("event_id"):return False
    now=time.monotonic();event_id=str(event["event_id"])
    with CACHE_LOCK:
        previous=AI_EVENT_CACHE.get(event_id,0.0)
        if now-previous<60.0:return False
        if not DIAGNOSTIC_QUEUE.submit(event,persist_ai_diagnostic_event):return False
        AI_EVENT_CACHE[event_id]=now
        if len(AI_EVENT_CACHE)>4000:
            for key,_ in sorted(AI_EVENT_CACHE.items(),key=lambda pair:pair[1])[:1000]:
                AI_EVENT_CACHE.pop(key,None)
    return True


def bootstrap_owner():
    password=os.getenv("OWNER_PASSWORD","")
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and password): return
    try:
        users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
        if any((u.get("email") or "").lower()==OWNER_EMAIL for u in users): return
        sb("POST","/auth/v1/admin/users",{"email":OWNER_EMAIL,"password":password,"email_confirm":True,
            "user_metadata":{"name":"AL-ZOOL OWNER","role":"owner"}},admin=True)
        print("Owner account created.")
    except Exception as e: print("OWNER_BOOTSTRAP_WARNING:",str(e),flush=True)

def token_of(h):
    v=h.headers.get("Authorization","")
    return v[7:].strip() if v.lower().startswith("bearer ") else ""

def current_user(token):
    if not token: raise PermissionError("سجّل الدخول أولاً")
    try: u=sb("GET","/auth/v1/user",bearer=token)
    except Exception: raise PermissionError("جلسة الدخول غير صالحة")
    if not u.get("id"): raise PermissionError("جلسة الدخول غير صالحة")
    return u

def owner(token):
    u=current_user(token)
    if (u.get("email") or "").lower()!=OWNER_EMAIL: raise PermissionError("هذه العملية للمالك فقط")
    return u

def admin_user(uid):
    result=sb("GET","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),admin=True)
    return result.get("user",result) if isinstance(result,dict) else result

def banned(u):
    val=u.get("banned_until")
    if not val: return False
    try: return datetime.fromisoformat(val.replace("Z","+00:00"))>datetime.now(timezone.utc)
    except Exception: return False

def connection_for_authenticated_user(user):
    """Never let owner requests borrow a different customer's MT5 connection."""
    identity=license_for_user(user["id"])
    client_id=(identity or {}).get("client_id","")
    return client_id, (CONNECTIONS.get(client_id) if client_id else None)

def license_for_user(user_id):
    rows=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+"&select=client_id,expires_at,revoked&limit=1",admin=True)
    return rows[0] if rows else None

def issue_license(user_id):
    if not user_id: raise ValueError("حساب العميل غير صالح")
    token=secrets.token_urlsafe(40)
    expires=(datetime.now(timezone.utc)+timedelta(days=3650)).isoformat()
    existing=license_for_user(user_id)
    client_id=existing["client_id"] if existing else secrets.token_hex(16)
    row={"client_id":client_id,"user_id":user_id,"token_hash":hashlib.sha256(token.encode()).hexdigest(),
         "expires_at":expires,"revoked":False}
    if existing:
        sb("PATCH","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe=""),row,admin=True)
    else:
        sb("POST","/rest/v1/alzool_engine_clients",row,admin=True)
    # Rotated credentials must invalidate any cached prior token immediately.
    with CACHE_LOCK:
        LICENSE_CACHE.pop(client_id,None)
    return {"client_id":client_id,"client_token":token,"expires_at":expires}

def issue_owner_pairing(user):
    """Pair the owner's terminal without creating a client licence."""
    uid=user["id"]
    record=admin_user(uid)
    metadata=dict(record.get("app_metadata") or {})
    secret=secrets.token_urlsafe(40)
    metadata[PAIRING_HASH_FIELD]=hashlib.sha256(secret.encode()).hexdigest()
    sb("PUT","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),
       {"app_metadata":metadata},admin=True)
    LICENSE_CACHE.pop("owner",None)
    identity=license_for_user(uid)
    if not identity and DUAL_MARKET_MODE and ENGINE_MARKET=="XAUUSD":
        issue_license(uid)
        identity=license_for_user(uid)
    if not identity: raise ValueError("معرّف التخزين الخاص بالمالك غير موجود")
    return {"engine_id":identity["client_id"],"pairing_key":secret,"engine_server_url":PUBLIC_ENGINE_URL}

def validate_owner_terminal(token):
    if not token or len(token)<32: return False
    digest=hashlib.sha256(token.encode()).hexdigest()
    now=time.monotonic()
    cached=LICENSE_CACHE.get("owner")
    if cached and cached.get("valid_until",0)>now:
        return hmac.compare_digest(cached.get("token_hash",""),digest)
    users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
    matched=next((u for u in users if (u.get("email") or "").lower()==OWNER_EMAIL),None)
    expected=((matched or {}).get("app_metadata") or {}).get(PAIRING_HASH_FIELD,"")
    LICENSE_CACHE["owner"]={"token_hash":expected,"valid_until":now+30}
    return bool(expected and hmac.compare_digest(expected,digest))

def validate_license(client_id,token):
    if client_id!="owner":
        # The owner device previously triggered two Supabase lookups on every
        # heartbeat, settings poll and decision poll. Cache ONLY the verified
        # client-id -> owner mapping briefly. The separate owner pairing key is
        # still validated on every request and retains its own revocation TTL.
        try:
            now=time.monotonic()
            with CACHE_LOCK:
                owner_entry=OWNER_TERMINAL_ID_CACHE.get(client_id)
            if owner_entry and owner_entry["until"]>now:
                is_owner=owner_entry["is_owner"]
            else:
                owner_rows=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+
                    urllib.parse.quote(client_id,safe="")+"&select=user_id&limit=1",admin=True)
                is_owner=False
                if owner_rows:
                    owner_record=admin_user(owner_rows[0]["user_id"])
                    is_owner=(owner_record.get("email") or "").lower()==OWNER_EMAIL
                with CACHE_LOCK:
                    if len(OWNER_TERMINAL_ID_CACHE)>256:OWNER_TERMINAL_ID_CACHE.clear()
                    OWNER_TERMINAL_ID_CACHE[client_id]={"is_owner":is_owner,"until":now+10}
            if is_owner:return validate_owner_terminal(token)
        except Exception: return False
    if client_id=="owner": return False
    if not client_id or not token: return False
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    now=time.monotonic()
    cached=LICENSE_CACHE.get(client_id)
    if cached and hmac.compare_digest(cached.get("token_hash",""),token_hash):
        if cached.get("valid_until",0)>now:
            return bool(cached.get("valid"))
        # Keep an already verified, unexpired licence usable during a short Supabase outage.
        try:
            still_valid=datetime.fromisoformat(cached.get("expires_at","").replace("Z","+00:00"))>datetime.now(timezone.utc)
        except Exception: still_valid=False
        if cached.get("valid") and still_valid and now-cached.get("checked_at",0)<300:
            return True
    rows=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&select=token_hash,expires_at,revoked&limit=1",admin=True)
    if not rows:
        LICENSE_CACHE[client_id]={"token_hash":token_hash,"valid":False,"expires_at":"","checked_at":now,"valid_until":now+LICENSE_CACHE_TTL_SECONDS}
        return False
    row=rows[0]
    try: expired=datetime.fromisoformat(row["expires_at"].replace("Z","+00:00"))<=datetime.now(timezone.utc)
    except Exception: expired=True
    valid=(not row.get("revoked") and not expired and hmac.compare_digest(token_hash,row.get("token_hash","")))
    LICENSE_CACHE[client_id]={"token_hash":token_hash,"valid":valid,"expires_at":row.get("expires_at",""),
                              "checked_at":now,"valid_until":now+LICENSE_CACHE_TTL_SECONDS}
    return valid

def bind_market_account(client_id,account):
    """One broker account per lane, with database-enforced cross-lane uniqueness."""
    if not DUAL_MARKET_MODE:return True
    login=str(account.get("login") or "").strip()
    broker=str(account.get("server") or "").strip().casefold()
    if not login or not broker:return False
    pair=(login,broker)
    with MARKET_ACCOUNT_LOCK:
        known=MARKET_ACCOUNT_BINDINGS.get(client_id)
        if known is not None:return known==pair
        path="/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")
        rows=sb("GET",path+"&select=account_login,broker_server,revoked&limit=1",admin=True)
        if not rows or rows[0].get("revoked"):return False
        row=rows[0]
        if row.get("account_login") is None and row.get("broker_server") is None:
            try:
                sb("PATCH",path+"&account_login=is.null&broker_server=is.null",
                   {"account_login":login,"broker_server":broker},admin=True,prefer="return=representation")
            except ValueError:
                # Concurrent binding or other lane already owns this broker account.
                return False
            rows=sb("GET",path+"&select=account_login,broker_server,revoked&limit=1",admin=True)
            if not rows or rows[0].get("revoked"):return False
            row=rows[0]
        if (row.get("account_login"),row.get("broker_server"))!=pair:return False
        MARKET_ACCOUNT_BINDINGS[client_id]=pair
        return True

def terminal_is_owner(client_id):
    """Check the authorized terminal's owner mapping with short-lived cache."""
    now=time.monotonic()
    with CACHE_LOCK:
        cached=OWNER_TERMINAL_ID_CACHE.get(client_id)
    if cached and cached.get("until",0)>now:
        return cached.get("is_owner") is True
    rows=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+
            urllib.parse.quote(client_id,safe="")+"&select=user_id&limit=1",admin=True)
    is_owner=bool(rows and (admin_user(rows[0]["user_id"]).get("email") or "").lower()==OWNER_EMAIL)
    with CACHE_LOCK:
        OWNER_TERMINAL_ID_CACHE[client_id]={"is_owner":is_owner,"until":now+10}
    return is_owner


def _owner_trade_settings_view(settings):
    """Ignore owner-unselected legacy caps and implicit stop losses.

    Older releases wrote numeric defaults into persistent settings. Those
    defaults do not constitute an owner choice. Leave clients untouched.
    """
    copy=dict(settings or {})
    pm=dict(copy.get("position_management") or {})
    if not pm.get("optional_limits_user_selected"):
        for key in ("max_drawdown_pct","max_positions","max_positions_per_direction",
                    "max_total_lots","addon_max_lot","cooling_max_lot","recovery_max_lot"):
            pm.pop(key,None)
    if not pm.get("stop_loss_user_configured"):
        copy["stop_loss_enabled"]=False
    copy["position_management"]=pm
    return copy


def _fetch_trade_settings(client_id):
    encoded=urllib.parse.quote(client_id,safe="")
    rows=sb("GET","/rest/v1/alzool_btc_trade_settings?client_id=eq."+encoded+
            "&select=single_enabled,single_profit_target,group_enabled,group_profit_target,stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management&limit=1",admin=True)
    clients=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+encoded+
               "&select=trading_enabled&limit=1",admin=True)
    settings=rows[0] if rows else {"single_enabled":False,"single_profit_target":1,
        "group_enabled":False,"group_profit_target":5,"stop_loss_enabled":False,"stop_loss_mode":"ATR",
        "stop_loss_value":1.8,"position_management":{"mode":"AI","addon_enabled":False,"addon_max":0,
        "addon_lot":0.01,"addon_distance_points":0,"addon_multiplier":1.5,"addon_max_lot":0.05,
        "cooling_enabled":False,"cooling_max":0,"cooling_lot":0.01,"cooling_distance_points":0,
        "cooling_multiplier":1.2,"cooling_max_lot":0.05,"max_total_lots":0.10}}
    if terminal_is_owner(client_id):
        settings=_owner_trade_settings_view(settings)
    pm=settings.get("position_management") if isinstance(settings.get("position_management"),dict) else {}
    if not pm.get("entry_council_mode_user_selected"):
        pm["entry_council_mode"]="ADVISORY"
    pm.setdefault("recovery_enabled",False)
    pm.setdefault("recovery_loss_trigger",0.0)
    pm.setdefault("recovery_profit_target",0.0)
    pm.setdefault("entry_guard_enabled",True)
    pm.setdefault("entry_max_delay_bars",1)
    pm.setdefault("entry_max_deviation_atr",0.5)
    settings["position_management"]=pm
    settings["trading_enabled"]=False if (clients and clients[0].get("trading_enabled",False)==False) else bool(clients and clients[0].get("trading_enabled",False))
    return settings

def resolve_owner_decision(client_id,decision,council,mode):
    if mode=="MASTER":
        # Bounded background cache: no network/model wait on the order path.
        feedback=feedback_learning.snapshot(client_id,decision.get("symbol"),sb)
        evidence=arena_master.evidence(client_id,decision)
        result=master_ai.decide(decision,council,feedback,arena_evidence=evidence)
        result["master"]["arena_shadow"]=arena_master.fused_shadow(client_id,decision,result)
        return result
    return fuse_live_decision(decision,council) if mode=="ADVISORY" else decision

def _store_trade_settings(client_id,settings):
    snapshot=dict(settings)
    with CACHE_LOCK:
        TRADE_SETTINGS_CACHE[client_id]={"settings":snapshot,"updated":time.monotonic()}
    item=CONNECTIONS.get(client_id)
    if item is not None:
        item["trade_settings_cache"]=snapshot

def _refresh_trade_settings_worker(client_id):
    try:
        _store_trade_settings(client_id,_fetch_trade_settings(client_id))
    except Exception as e:
        print("TRADE_SETTINGS_CACHE_REFRESH_WARNING:",str(e)[:220],flush=True)
    finally:
        with CACHE_LOCK: SETTINGS_REFRESHING.discard(client_id)

def get_trade_settings(client_id):
    with CACHE_LOCK:
        cached=TRADE_SETTINGS_CACHE.get(client_id)
        age=(time.monotonic()-cached["updated"]) if cached else None
        if cached and age>=SETTINGS_CACHE_REFRESH_SECONDS and client_id not in SETTINGS_REFRESHING:
            SETTINGS_REFRESHING.add(client_id)
            threading.Thread(target=_refresh_trade_settings_worker,args=(client_id,),daemon=True).start()
        if cached:
            return dict(cached["settings"])
        if client_id in SETTINGS_REFRESHING:
            return None
        SETTINGS_REFRESHING.add(client_id)
    try:
        settings=_fetch_trade_settings(client_id)
        _store_trade_settings(client_id,settings)
        return dict(settings)
    finally:
        with CACHE_LOCK: SETTINGS_REFRESHING.discard(client_id)

def finite_number(value, default=0.0):
    try:
        number=float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default

def entry_direction_capacity(positions, symbol, signal, settings):
    """Enforce per-direction count only when the owner explicitly sets a cap."""
    pm=(settings or {}).get("position_management") or {}
    if signal not in ("BUY","SELL"):
        return {"approved":False,"reason_ar":"لا يوجد اتجاه دخول صالح"}
    position_type=0 if signal=="BUY" else 1
    live_symbol_positions=[p for p in (positions or [])
                           if isinstance(p,dict) and str(p.get("symbol") or "")==symbol]
    count=sum(1 for p in live_symbol_positions
              if int(finite_number(p.get("type"),-1))==position_type)
    opposite_count=sum(1 for p in live_symbol_positions
                       if int(finite_number(p.get("type"),-1))==1-position_type)
    # Gold trades from every EA (including manual positions) count. Never open
    # opposite exposure, regardless of whether a per-direction cap is configured.
    if opposite_count:
        return {"approved":False,"open_same_side":count,
                "open_opposite_side":opposite_count,
                "reason_ar":"حماية الاتجاه: توجد مراكز معاكسة في السوق الحالي؛ منع دخول جديد"}
    raw=pm.get("max_positions_per_direction")
    if raw is None or str(raw).strip()=="":
        return {"approved":True,"open_same_side":count,
                "reason_ar":"لم يحدد المالك حد عدد للاتجاه"}
    cap=int(finite_number(raw,-1))
    if cap<0:
        return {"approved":False,"reason_ar":"حد صفقات الاتجاه غير صالح"}
    return {"approved":count<cap,"open_same_side":count,"max_per_direction":cap,
            "reason_ar":"الحد الذي حدده المالك لصفقات الاتجاه" if count>=cap
                        else "عدد صفقات الاتجاه ضمن إعدادات المالك"}


def clean_telemetry_rows(rows, allowed, limit=200):
    if not isinstance(rows,list): return []
    cleaned=[]
    for item in rows[:limit]:
        if not isinstance(item,dict): continue
        row={}
        for key in allowed:
            if key not in item: continue
            value=item[key]
            if key in ("symbol","comment","strategy_comment","ticket","magic","position_id"):
                row[key]=str(value)[:120]
            elif key in ("type","state","entry","reason","deal_type","strategy_id"):
                try: row[key]=int(value)
                except (TypeError,ValueError,OverflowError): row[key]=0
            elif key=="time":
                try: row[key]=int(value)
                except (TypeError,ValueError,OverflowError): row[key]=0
            else: row[key]=finite_number(value)
        cleaned.append(row)
    return cleaned

def terminal_request_matches_market(request_url, active_symbol):
    """New BTC clients MUST bind their poll to the chart in the snapshot.

    Legacy gold polls without a symbol remain valid for the gold-only engine.
    An older gold EA must never consume a newly added BTC decision.
    """
    if not btc_market(active_symbol):
        return False  # Gold polling is never accepted by the BTC deployment.
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request_url).query)
    requested = (query.get("symbol") or [""])[0]
    if not requested:
        return False
    return supported_market_symbol(requested) and same_market_symbol(requested, active_symbol)


def learning_archive_symbol(client_id,current_symbol=None):
    """Exact broker symbol; reports never substitute BTCUSD for BTCUSDm."""
    if btc_market(current_symbol):return current_symbol
    rows=sb("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+
        urllib.parse.quote(client_id,safe="")+"&symbol=ilike."+ENGINE_MARKET+
        "*&timeframe=eq.PERIOD_M1&select=symbol&order=candle_time.desc&limit=1",admin=True)
    symbol=rows[0].get("symbol") if isinstance(rows,list) and rows else None
    return symbol if btc_market(symbol) else ENGINE_MARKET

def live_learning_profile(deals, symbol="XAUUSD"):
    """Build outcome-based weights only from closed positions on this market symbol."""
    market_symbol = symbol
    grouped={}
    now_utc=datetime.now(timezone.utc)
    cutoff=int((now_utc-timedelta(days=30)).timestamp())
    today=now_utc.astimezone(timezone(timedelta(hours=4))).date()
    today_total=set()
    last_closed_stamp=0
    for deal in deals if isinstance(deals,list) else []:
        if not isinstance(deal,dict): continue
        symbol=str(deal.get("symbol") or "").upper()
        if not same_market_symbol(symbol, market_symbol): continue
        entry=int(finite_number(deal.get("entry"),1))
        if entry not in (1,2,3): continue
        stamp=int(finite_number(deal.get("time")))
        if stamp<=0 or stamp<cutoff:continue
        last_closed_stamp=max(last_closed_stamp,stamp)
        pid=str(deal.get("position_id") or deal.get("ticket") or "")
        if not pid: continue
        if stamp>0 and datetime.fromtimestamp(stamp,timezone.utc).astimezone(timezone(timedelta(hours=4))).date()==today:
            today_total.add(pid)
        row=grouped.setdefault(pid,{"position_id":pid,"net":0.0,"strategy_id":0,"time":stamp})
        row["net"]+=sum(finite_number(deal.get(k)) for k in ("profit","commission","swap","fee"))
        row["time"]=max(row["time"],stamp)
        comment=str(deal.get("strategy_comment") or deal.get("comment") or "")
        sid=int(finite_number(deal.get("strategy_id")))
        if not 1<=sid<=200:
            match=re.search(r"ALZOOL-S(\d{1,3})",comment,re.I)
            sid=int(match.group(1)) if match else 0
        combo_match=re.search(r"ALZOOL-S\d{1,3}-C(\d{1,4})",comment,re.I)
        if combo_match:
            cid=int(combo_match.group(1))
            if 1<=cid<=1000: row["combination_id"]=cid
        if 1<=sid<=200: row["strategy_id"]=sid
    rows=sorted(grouped.values(),key=lambda x:x["time"])
    weights={str(i):1.0 for i in range(1,201)}
    stats={}
    combo_weights={str(i):1.0 for i in range(1,1001)}
    combo_stats={}
    matched_today=set()
    for row in rows:
        sid=int(row["strategy_id"] or 0)
        cid=int(row.get("combination_id") or 0)
        if 1<=cid<=1000:
            ckey=str(cid); citem=combo_stats.setdefault(ckey,{"combination_id":cid,"trades":0,"wins":0,"losses":0,"flat":0,"net":0.0,
                "gross_win":0.0,"gross_loss":0.0})
            citem["trades"]+=1; citem["net"]+=row["net"]
            if row["net"]>0:
                citem["wins"]+=1;citem["gross_win"]+=row["net"]
            elif row["net"]<0:
                citem["losses"]+=1;citem["gross_loss"]+=-row["net"]
            else:citem["flat"]+=1
        if not 1<=sid<=200: continue
        key=str(sid); item=stats.setdefault(key,{"strategy_id":sid,"trades":0,"wins":0,"losses":0,"flat":0,"net":0.0,
            "gross_win":0.0,"gross_loss":0.0})
        item["trades"]+=1; item["net"]+=row["net"]
        if row["net"]>0:
            item["wins"]+=1;item["gross_win"]+=row["net"]
        elif row["net"]<0:
            item["losses"]+=1;item["gross_loss"]+=-row["net"]
        else:item["flat"]+=1
        if row["position_id"] in today_total: matched_today.add(row["position_id"])
    matched=sum(x["trades"] for x in stats.values())
    last_attributed_stamp=max((row["time"] for row in rows if row["strategy_id"]),default=0)
    wins=sum(x["wins"] for x in stats.values()); losses=sum(x["losses"] for x in stats.values())
    coverage=(matched/len(rows)*100.0) if rows else 0.0
    daily_coverage=(len(matched_today)/len(today_total)*100.0) if today_total else 0.0
    # Statistical shrinkage prevents one lucky closed trade from changing votes.
    for key,item in stats.items():
        weights[key]=conservative_outcome_weight(item["wins"],item["losses"],item["net"],
            gross_win=item["gross_win"],gross_loss=item["gross_loss"])
        item["weight"]=weights[key]
        item["evidence"]="ELIGIBLE" if item["wins"]+item["losses"]>=8 else "INSUFFICIENT_TRADES"
    for key,item in combo_stats.items():
        combo_weights[key]=conservative_outcome_weight(item["wins"],item["losses"],item["net"],
            range_size=0.15,gross_win=item["gross_win"],gross_loss=item["gross_loss"])
        item["evidence"]="ELIGIBLE" if item["wins"]+item["losses"]>=8 else "INSUFFICIENT_TRADES"
    return {"processed_positions":matched,"closed_positions":len(rows),"unmatched_positions":max(0,len(rows)-matched),
        "wins":wins,"losses":losses,"flat":sum(x["flat"] for x in stats.values()),
        "net":sum(x["net"] for x in rows if x["strategy_id"]),
        "coverage_percent":round(coverage,1),"daily_closed_positions":len(today_total),
        "daily_processed_positions":len(matched_today),"daily_coverage_percent":round(daily_coverage,1),
        "updated_strategies":sum(1 for x in stats.values() if x["weight"]!=1.0),
        "last_closed_at":(datetime.fromtimestamp(last_closed_stamp,timezone.utc).isoformat()
                          if last_closed_stamp else None),
        "last_attributed_at":(datetime.fromtimestamp(last_attributed_stamp,timezone.utc).isoformat()
                              if last_attributed_stamp else None),
        "eligible_strategies":sum(1 for x in stats.values() if x["evidence"]=="ELIGIBLE"),
        "weight_policy":">=8 attributed NET closed trades; weight is based on net payoff versus total absolute payoff; 12 neutral pseudo-observations",
        "weights":weights,"strategies":sorted(stats.values(),key=lambda x:(x["net"],x["trades"]),reverse=True),
        "combination_weights":combo_weights,
        "updated_combinations":sum(1 for k,x in combo_stats.items() if combo_weights[k]!=1.0),
        "eligible_combinations":sum(1 for x in combo_stats.values() if x["evidence"]=="ELIGIBLE"),
        "processed_combinations":sum(x["trades"] for x in combo_stats.values()),
        "combinations":sorted([{**x,"weight":round(combo_weights[str(x["combination_id"])],4)} for x in combo_stats.values()],key=lambda x:(x["net"],x["trades"]),reverse=True)}


def clean_historical_candles(rows, metadata, client_id, symbol, timeframe):
    if not isinstance(rows,list): return []
    digits=max(0,min(10,int(finite_number(metadata.get("digits",2),2))))
    point=max(0.0,finite_number(metadata.get("point_size")))
    tick_size=max(0.0,finite_number(metadata.get("tick_size")))
    tick_value=max(0.0,finite_number(metadata.get("tick_value")))
    contract_size=max(0.0,finite_number(metadata.get("contract_size")))
    account_currency=str(metadata.get("account_currency") or "")[:12]
    cleaned=[]
    for item in rows[:10000]:
        if not isinstance(item,dict): continue
        stamp=int(finite_number(item.get("time")))
        if stamp<=0: continue
        o=finite_number(item.get("open"),float("nan"))
        h=finite_number(item.get("high"),float("nan"))
        l=finite_number(item.get("low"),float("nan"))
        c=finite_number(item.get("close"),float("nan"))
        if not all(math.isfinite(x) for x in (o,h,l,c)): continue
        if h<l or h<max(o,c) or l>min(o,c): continue
        cleaned.append({
            "client_id":client_id,"symbol":symbol,"timeframe":timeframe,
            "candle_time":datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
            "candle_open":o,"candle_high":h,"candle_low":l,"candle_close":c,
            "tick_volume":max(0,int(finite_number(item.get("tick_volume")))),
            "spread_points":max(0,int(finite_number(item.get("spread_points")))),
            "point_size":point,"tick_size":tick_size,"tick_value":tick_value,
            "contract_size":contract_size,"digits":digits,"account_currency":account_currency
        })
    return cleaned

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,fmt,*args): print("WEB:",fmt%args)
    def send_json(self,status,data):
        body=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
        from bandwidth_payload import negotiate_payload
        body,compressed=negotiate_payload(body,self.path,self.headers.get("Accept-Encoding",""),"application/json")
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Vary","Accept-Encoding")
        if compressed:self.send_header("Content-Encoding","gzip")
        self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass
    def read_json(self):
        n=int(self.headers.get("Content-Length","0"))
        if n>2000000: raise ValueError("حجم الطلب كبير")
        return json.loads(self.rfile.read(n) or b"{}")
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Allow","GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods","GET, POST, PATCH, DELETE, OPTIONS"); self.end_headers()
    def do_HEAD(self):
        path=urllib.parse.urlparse(self.path).path
        if path in ("/health","/api/health"):
            self.send_response(200);self.send_header("Cache-Control","no-store");self.end_headers()
            return
        if path in ("/strategy-engine.js",):
            try: owner(token_of(self))
            except PermissionError: return self.send_error(403,"Forbidden")
            except Exception: return self.send_error(403,"Forbidden")
        elif not public_static(path):
            return self.send_error(404,"Not Found")
        return super().do_HEAD()

    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path in OWNER_READ_ENDPOINTS or path in PROTECTED_OWNER_ASSETS or path=="/strategy-engine.js":
            try: owner(token_of(self))
            except PermissionError: return self.send_json(403,{"error":"هذه الصفحة/الخدمة للمالك فقط"})
            except Exception: return self.send_json(403,{"error":"التحقق من صلاحيات المالك غير متاح"})
        if path=="/strategy-engine.js":
            return super().do_GET()
        if path=="/health":
            return self.send_json(200,{"status":"UP","service":"AL-ZOOL central engine",
                "server_utc":datetime.now(timezone.utc).isoformat(),
                "trading_enabled_by_health_check":False})
        if path=="/api/ai/feedback-learning":
            try:
                u=owner(token_of(self))
                if banned(u):return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"):return self.send_json(200,{"status":"NO_CONNECTION"})
                cid=lic["client_id"]
                current=CONNECTIONS.get(cid) or {}
                symbol=learning_archive_symbol(cid,current.get("symbol"))
                return self.send_json(200,feedback_learning.snapshot(cid,symbol,sb,(current.get("closed_deals") or [])+(current.get("action_entry_deals") or [])))
            except PermissionError:return self.send_json(403,{"error":"Owner only"})
            except Exception:return self.send_json(502,{"error":"Feedback data unavailable"})
        if path=="/api/ai/accuracy-v4":
            try:
                u=owner(token_of(self))
                if banned(u):return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id or (lic or {}).get("revoked"):return self.send_json(200,accuracy_v4.summarize([]))
                query="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&event_type=eq.AI_ACCURACY_V4_OUTCOME&select=payload,created_at&order=created_at.desc&limit=500"
                return self.send_json(200,accuracy_v4.summarize(archive_read(sb,query)))
            except PermissionError:return self.send_json(403,{"error":"Owner only"})
            except Exception:return self.send_json(502,{"error":"Accuracy data unavailable"})
        if path=="/api/ai/stress-learning":
            try:
                u=owner(token_of(self))
                if banned(u):return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"):
                    return self.send_json(200,{"market":ENGINE_MARKET,"status":"NO_SAVED_REPORT"})
                from stress_learning import latest_report
                result=latest_report(sb,lic["client_id"])
                return self.send_json(200,{"market":ENGINE_MARKET,**result})
            except PermissionError:return self.send_json(403,{"error":"هذه الخدمة للمالك فقط"})
            except Exception:return self.send_json(503,{"error":"تعذر قراءة سجل اختبارات الضغط"})
        if path=="/api/ai/strategy-comparison":
            try:
                u=owner(token_of(self))
                if banned(u):return self.send_json(403,{"error":"الحساب موقوف"})
                from strategy_research import report
                return self.send_json(200,{"market":ENGINE_MARKET,**report(ENGINE_MARKET)})
            except PermissionError:return self.send_json(403,{"error":"هذه الخدمة للمالك فقط"})
        if path=="/api/multi-market/owner-state":
            try:
                u=owner(token_of(self))
                if banned(u):return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                cid=(lic or {}).get("client_id","")
                snap=CONNECTIONS.get(cid) or {}
                acct=snap.get("account") or {}
                seen=snap.get("received_at")
                settings=get_trade_settings(cid) if cid else {}
                return self.send_json(200,{"market":ENGINE_MARKET,"owner":True,
                    "paired":bool(cid),"symbol":snap.get("symbol"),"login":acct.get("login"),
                    "broker":acct.get("server"),"seen_at":seen.timestamp() if seen else 0,
                    "open_positions":len(snap.get("positions") or []),
                    "ready":bool(lic and not lic.get("revoked") and snap.get("mt5_central_trading_enabled") is True),
                    "trading_enabled":bool((settings or {}).get("trading_enabled"))})
            except PermissionError:return self.send_json(403,{"error":"هذه الخدمة للمالك فقط"})
            except Exception:return self.send_json(503,{"error":"تعذر التحقق من حالة السوق"})
        if path=="/api/owner/readiness":
            try:
                user=owner(token_of(self))
                identity=license_for_user(user["id"])
                cid=(identity or {}).get("client_id","")
                snapshot=CONNECTIONS.get(cid) if cid else None
                settings=get_trade_settings(cid) if cid else {}
                decision=(snapshot or {}).get("strategy_decision") or {}
                council=council_result(cid,decision) if cid else {}
                latest_candle=None
                if cid:
                    rows=sb("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+
                        urllib.parse.quote(cid,safe="")+
                        "&select=candle_time&order=candle_time.desc&limit=1",admin=True)
                    if rows:latest_candle=rows[0].get("candle_time")
                result=owner_readiness(identity,snapshot,settings,council,latest_candle)
                return self.send_json(200,result)
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(503,{"error":"تعذر إتمام فحص الجاهزية",
                "reason_type":type(e).__name__})
        if path.startswith("/v1/local-ai/context/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
            if not terminal_is_owner(client_id):
                return self.send_json(403,{"error":"واجهة النماذج المحلية للمالك فقط"})
            item=CONNECTIONS.get(client_id)
            if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                return self.send_json(409,{"error":"بيانات MT5 غير حديثة"})
            decision=item.get("strategy_decision") or {}
            if not decision:return self.send_json(409,{"error":"التحليل لم يكتمل"})
            context=decision.get("context") if isinstance(decision.get("context"),dict) else {}
            return self.send_json(200,{"client_id":client_id,"symbol":item.get("symbol"),
                "accuracy_protocol":accuracy_v4.PROTOCOL,
                "unified_ai_protocol":accuracy_v4.UNIFIED_PROTOCOL,
                "unified_ai_models":["qwen2.5:1.5b","deepseek-r1:1.5b"],
                "bar_time":int(finite_number(decision.get("bar_time"))),
                # The local relay consumes 'decision'. Keep 'direction' for legacy relays.
                "decision":decision.get("decision","WAIT"),
                "direction":decision.get("decision","WAIT"),
                "context":context,
                "candles":(item.get("closed_candles") or [])[:6],
                "confidence":finite_number(decision.get("confidence")),
                "strategy_id":int(finite_number(decision.get("strategy_id"))),
                "entry_reference_price":finite_number(decision.get("entry_reference_price")),
                "atr":finite_number(context.get("atr")),
                "advisory_bots_consensus":(decision.get("advisory_bots") or {}).get("consensus",{}),
                "advisory_bots":(decision.get("advisory_bots") or {}).get("advisors",[]),
                "family_votes":decision.get("family_votes") or {},
                "combination_summary":decision.get("combination_summary") or {},
                "last_seen":item.get("last_seen")})
        if path.startswith("/v1/terminal-actions/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
            item=CONNECTIONS.get(client_id)
            if not item:return self.send_json(409,{"execution_ready":False,"reason_ar":"MT5 غير متصل"})
            if not terminal_request_matches_market(self.path,item.get("symbol")):
                return self.send_json(409,{"execution_ready":False,"reason_ar":"رمز شارت MT5 لا يطابق المحرك"})
            decision=item.get("strategy_decision") or {}
            settings=get_trade_settings(client_id) or {}
            now=datetime.now(timezone.utc)
            mode=entry_council_mode(settings,terminal_is_owner(client_id))
            advisory_mode=mode!="STRICT"
            council=council_result(client_id,decision)
            if advisory_mode: decision=resolve_owner_decision(client_id,decision,council,mode)
            gate=evaluate_entry_gate(decision,item.get("account") or {},item.get("symbol",""),
                item.get("received_at"),now,bool(settings.get("trading_enabled")),
                local_ai_advice=(dict(item["local_ai_advice"],
                    age_seconds=now.timestamp()-finite_number(item["local_ai_advice"].get("received_at_unix",0)))
                    if isinstance(item.get("local_ai_advice"),dict) else None),
                require_local_ai=LOCAL_AI_REQUIRED and not advisory_mode,
                strategy_first=advisory_mode)
            risk=risk_guardian(item.get("account") or {},item.get("positions") or [],risk_limits_from_settings(settings))
            proposal=propose_central_action(client_id,item,decision,
                gate,council,risk,settings,now,council_advisory=advisory_mode)
            if mode=="MASTER":
                # Record actual terminal poll diagnostics, not just UI previews.
                # At most two events per closed bar (WAIT / PROPOSAL).
                action_bar=int(finite_number(decision.get("bar_time")))
                if action_bar>0:
                    diagnostic={"symbol":item.get("symbol"),"bar_time":action_bar,
                        "execution_ready":bool(proposal.get("execution_ready")),
                        "action":proposal.get("action","HOLD"),"side":proposal.get("side"),
                        "lot_cap":proposal.get("lot_cap",0),"reason_ar":proposal.get("reason_ar",""),
                        "positions":len(item.get("positions") or [])}
                    state="READY" if diagnostic["execution_ready"] else "WAIT"
                    queue_ai_diagnostic_event({"event_id":"management-decision:"+hashlib.sha256((client_id+str(action_bar)+state).encode()).hexdigest(),
                        "client_id":client_id,"event_type":"MASTER_POSITION_ACTION_AUDIT","payload":diagnostic})
            return self.send_json(200,{**proposal,"symbol":item.get("symbol","")})
        if path.startswith("/v1/terminal-control/settings/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
            try:
                settings=get_trade_settings(client_id)
                if settings is None:
                    return self.send_json(503,{"error":"إعدادات التداول قيد التهيئة"})
                return self.send_json(200,profit_settings_for_terminal(settings,CONNECTIONS.get(client_id)))
            except Exception as e:
                cached=TRADE_SETTINGS_CACHE.get(client_id)
                if cached:
                    return self.send_json(200,profit_settings_for_terminal(cached["settings"],CONNECTIONS.get(client_id)))
                return self.send_json(503,{"error":"تعذر تحديث إعدادات التداول مؤقتاً"})
        if path=="/api/bot-advisors":
            # Owner/client authenticated read-only strategy assistants.
            # No historical data from other customers, no order or settings actions.
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                identity=license_for_user(u["id"])
                if not identity or identity.get("revoked"):
                    return self.send_json(403,{"error":"لا يوجد اتصال MT5 مخوّل لهذا الحساب"})
                client_id=identity["client_id"]
                item=CONNECTIONS.get(client_id)
                if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                    return self.send_json(409,{"error":"MT5 غير متصل أو البيانات أقدم من 30 ثانية. لا نعرض توصيات قديمة.","connected":False})
                if not supported_market_symbol(item.get("symbol")):
                    return self.send_json(409,{"error":"مساعدو المكتبة متاحون لشارت XAUUSD أو BTCUSD فقط.","connected":True})
                # A chronological list of CLOSED candles only, not the forming bar.
                current_decision=item.get("strategy_decision") or {}
                analysis=item.get("bot_report")
                current_candle=(item.get("closed_candles") or [{}])[0]
                if not isinstance(analysis,dict) or int(finite_number(analysis.get("bar_time",0)))!=int(finite_number(current_candle.get("time",0))):
                    analysis=analyze_advisors(list(reversed(item.get("closed_candles") or [])),symbol=item.get("symbol"))
                central=(current_decision.get("decision","WAIT")
                         if int(finite_number(current_decision.get("bar_time",0)))==
                            int(finite_number(analysis.get("bar_time",0))) else "WAIT")
                candle_age_sec=int(time.time()-finite_number(analysis.get("bar_time",0)))
                analysis_fresh=(analysis.get("status")=="READY" and
                                market_bar_fresh(analysis.get("bar_time"),item.get("timeframe"),time.time()))
                result={**analysis,"connected":True,
                    "market_data_fresh":analysis_fresh,
                    "candle_age_seconds":candle_age_sec,
                    "data_status":"RECENT" if analysis_fresh else "STALE_OR_UNAVAILABLE",
                    "symbol":item.get("symbol",""),"timeframe":item.get("timeframe",""),
                    "central_decision":central,"received_at":item.get("last_seen"),
                    "signal_journal":(item.get("bot_signal_history") or [])[-15:],
                    "signal_bus":{"processed_in_central_engine":True,
                                  "event_type":"BOT_ANALYSIS_SIGNAL",
                                  "last_bar_dispatch_count":int(item.get("bot_dispatch_count",0)),
                                  "persistence":"async_best_effort_not_confirmed"},
                    "execution_enabled":False,
                    "commercial_bots_connected":False,
                    "central_alignment":(
                        "AGREE" if central==analysis["consensus"]["signal"] and central in ("BUY","SELL")
                        else "CONFLICT" if central in ("BUY","SELL") and analysis["consensus"]["signal"] in ("BUY","SELL")
                        else "NO_DIRECTIONAL_CONSENSUS"
                    )}
                return self.send_json(200,result)
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception: return self.send_json(502,{"error":"تعذر إكمال التحليل الاستشاري"})
        if path=="/api/client/basic-settings":
            try:
                u=current_user(token_of(self))
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    return self.send_json(403,{"error":"إعدادات العميل فقط"})
                if banned(admin_user(u["id"])):
                    return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"):
                    return self.send_json(403,{"error":"اتصال العميل غير مرخص"})
                if datetime.fromisoformat(str(lic["expires_at"]).replace("Z","+00:00"))<=datetime.now(timezone.utc):
                    return self.send_json(403,{"error":"انتهت صلاحية الاتصال"})
                settings=get_trade_settings(lic["client_id"]) or {}
                return self.send_json(200,{"settings":basic_settings(settings),
                    "trading_enabled":bool(settings.get("trading_enabled")),
                    "server_managed_ai":True,"advanced_settings_editable":False})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except (ValueError,KeyError,TypeError):
                return self.send_json(403,{"error":"سجل الترخيص غير صالح"})
            except Exception:return self.send_json(503,{"error":"تعذّر تحميل إعدادات العميل"})
        if path=="/api/auto-trading":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"): return self.send_json(409,{"error":"لا يوجد ترخيص تداول مرتبط بهذا الحساب"})
                rows=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(lic["client_id"],safe="")+
                        "&select=trading_enabled&limit=1",admin=True)
                enabled=bool(rows and rows[0].get("trading_enabled",False))
                return self.send_json(200,{"trading_enabled":enabled,"client_id":lic["client_id"],
                    "connected":bool(CONNECTIONS.get(lic["client_id"]) and (datetime.now(timezone.utc)-CONNECTIONS[lic["client_id"]]["received_at"]).total_seconds()<30)})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/trade-management/settings":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                client_id,item=connection_for_authenticated_user(u)
                if not client_id: return self.send_json(409,{"error":"لا يوجد اتصال MT5 مرخّص لهذا الحساب"})
                rows=sb("GET","/rest/v1/alzool_btc_trade_settings?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                        "&select=single_enabled,single_profit_target,group_enabled,group_profit_target,stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management,updated_at&limit=1",admin=True)
                settings=rows[0] if rows else {"single_enabled":False,"single_profit_target":1,
                    "group_enabled":False,"group_profit_target":5,"stop_loss_enabled":False,
                    "stop_loss_mode":"ATR","stop_loss_value":1.8,"position_management":{"mode":"AI","addon_enabled":False,"addon_max":0,"addon_lot":0.01,"addon_distance_points":0,"addon_multiplier":1.5,"addon_max_lot":0.05,"cooling_enabled":False,"cooling_max":0,"cooling_lot":0.01,"cooling_distance_points":0,"cooling_multiplier":1.2,"cooling_max_lot":0.05,"max_total_lots":0.10},"updated_at":None}
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    settings=_owner_trade_settings_view(settings)
                pm=settings.get("position_management") if isinstance(settings.get("position_management"),dict) else {}
                pm.setdefault("recovery_enabled",False)
                pm.setdefault("recovery_loss_trigger",0.0)
                pm.setdefault("recovery_profit_target",0.0)
                pm.setdefault("entry_guard_enabled",True)
                pm.setdefault("entry_max_delay_bars",1)
                pm.setdefault("entry_max_deviation_atr",0.5)
                settings["position_management"]=pm
                return self.send_json(200,{"client_id":client_id,"settings":settings,
                    "smart_profit_capable":bool(item and item.get("smart_profit_capable") is True and 0<=(datetime.now(timezone.utc)-item["received_at"]).total_seconds()<=30),
                    "smart_profit_runner_version":(item or {}).get("smart_profit_runner_version") if item and 0<=(datetime.now(timezone.utc)-item["received_at"]).total_seconds()<=30 else None})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path in ("/health","/api/health"):
            return self.send_json(200,{"ok":True,"service":"AL-ZOOL "+ENGINE_MARKET+" engine",
                "market":ENGINE_MARKET,"btc_database_ready":btc_database_ready(),
                "mt5_connection_api":True,"trading_engine":True,
                "trading_enabled_by_health_check":False,"execution_location":"isolated Deriv MT5 terminal"})
        if path=="/api/config":
            if not btc_database_ready():
                return self.send_json(503,{"ready":False,"error":
                    "إعداد SUPABASE_URL لا يشير إلى قاعدة البيتكوين الجديدة؛ حُظر الاتصال لحماية بيانات الذهب",
                    "market":ENGINE_MARKET})
            missing=[]
            if not SUPABASE_URL: missing.append("SUPABASE_URL")
            if not SUPABASE_ANON_KEY: missing.append("SUPABASE_ANON_KEY")
            if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
            return self.send_json(200,{"supabase_url":SUPABASE_URL,"supabase_anon_key":SUPABASE_ANON_KEY,
                "owner_email":OWNER_EMAIL,"ready":not missing,"missing":missing})
        if path=="/api/access/check":
            try:
                u=current_user(token_of(self)); fresh=admin_user(u["id"])
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                email=(u.get("email") or "").lower()
                return self.send_json(200,{"ok":True,"role":"owner" if email==OWNER_EMAIL else "client","email":email})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:200]})
        if path=="/api/admin/clients":
            try:
                owner(token_of(self))
                users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
                licenses=sb("GET","/rest/v1/alzool_engine_clients?select=user_id,client_id,expires_at,revoked",admin=True)
                by_user={x.get("user_id"):x for x in licenses}
                rows=[]
                for u in users:
                    email=(u.get("email") or "").lower()
                    if not email or email==OWNER_EMAIL: continue
                    lic=by_user.get(u.get("id")) or {}
                    rows.append({"id":u.get("id"),"email":email,"name":(u.get("user_metadata") or {}).get("name",""),
                                 "created_at":u.get("created_at"),"active":not banned(u),
                                 "licensed":bool(lic and not lic.get("revoked")),"expires_at":lic.get("expires_at")})
                return self.send_json(200,{"clients":rows})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/backtest/history":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                identity=license_for_user(u["id"])
                client_id=(identity or {}).get("client_id","") if not (identity or {}).get("revoked") else ""
                if not client_id:
                    return self.send_json(409,{"error":"لا يوجد سجل تاريخي محفوظ لهذا الحساب بعد. حدّث عميل MT5 لإرسال التاريخ."})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                requested_days=int(finite_number((params.get("days") or ["90"])[0],90))
                days=requested_days if requested_days in (0,7,30,90,180,365,730) else 90
                requested_limit=int(finite_number((params.get("limit") or ["5000"])[0],5000))
                row_limit=max(100,min(10000,requested_limit))
                meta_query=("/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&symbol=ilike."+ENGINE_MARKET+"*&select=symbol,timeframe&order=uploaded_at.desc,candle_time.desc,symbol.asc,timeframe.asc")
                meta_rows=[]; meta_offset=0
                while len(meta_rows)<10000:
                    meta_page=sb("GET",meta_query+"&limit=1000&offset="+str(meta_offset),admin=True)
                    if not isinstance(meta_page,list) or not meta_page: break
                    meta_rows.extend(meta_page); meta_offset+=len(meta_page)
                    if len(meta_page)<1000: break
                available_timeframes=[]; available_symbols=[]
                for row in meta_rows:
                    tf=str(row.get("timeframe") or "")
                    sym=str(row.get("symbol") or "")
                    if not btc_market(sym): continue
                    if tf and tf not in available_timeframes: available_timeframes.append(tf)
                    if sym and sym not in available_symbols: available_symbols.append(sym)
                if not available_timeframes:
                    return self.send_json(409,{"error":"لم تصل شموع تاريخية إلى قاعدة البيانات بعد. شغّل عميل MT5 المحدّث."})
                timeframe=str((params.get("timeframe") or [available_timeframes[0]])[0])[:20]
                symbol=str((params.get("symbol") or [available_symbols[0] if available_symbols else "XAUUSD"])[0])[:40]
                if timeframe not in available_timeframes: return self.send_json(400,{"error":"الفريم المطلوب غير موجود ضمن البيانات المحفوظة","available_timeframes":available_timeframes})
                if symbol not in available_symbols: return self.send_json(400,{"error":"الرمز المطلوب غير موجود ضمن البيانات المحفوظة","available_symbols":available_symbols})
                if (params.get("meta") or ["0"])[0]=="1":
                    return self.send_json(200,{"symbol":symbol,"timeframe":timeframe,"market":ENGINE_MARKET,
                        "available_timeframes":available_timeframes,"available_symbols":available_symbols,
                        "history_source":"MT5 historical rates saved in Supabase"})
                query="/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(client_id,safe="")
                query+="&symbol=eq."+urllib.parse.quote(symbol,safe="")+"&timeframe=eq."+urllib.parse.quote(timeframe,safe="")
                if days:
                    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
                    query+="&candle_time=gte."+urllib.parse.quote(cutoff,safe="")
                query+="&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size,tick_size,tick_value,contract_size,digits,account_currency&order=candle_time.asc"
                rows=[]; offset=0
                while len(rows)<row_limit:
                    page=sb("GET",query+"&limit=1000&offset="+str(offset),admin=True)
                    if not isinstance(page,list) or not page: break
                    rows.extend(page); offset+=len(page)
                    if len(page)<1000: break
                rows=rows[:row_limit]
                candles=[]
                for row in rows:
                    try: stamp=int(datetime.fromisoformat(row["candle_time"].replace("Z","+00:00")).timestamp())
                    except Exception: continue
                    candles.append({"time":stamp,"open":finite_number(row.get("candle_open")),
                        "high":finite_number(row.get("candle_high")),"low":finite_number(row.get("candle_low")),
                        "close":finite_number(row.get("candle_close")),"tick_volume":int(finite_number(row.get("tick_volume"))),
                        "spread_points":int(finite_number(row.get("spread_points"))),
                        "point_size":finite_number(row.get("point_size")),"tick_size":finite_number(row.get("tick_size")),
                        "tick_value":finite_number(row.get("tick_value")),"contract_size":finite_number(row.get("contract_size")),
                        "digits":int(finite_number(row.get("digits"),2)),"currency":str(row.get("account_currency") or "")})
                return self.send_json(200,{"symbol":symbol,"timeframe":timeframe,"days":days,"count":len(candles),
                    "available_timeframes":available_timeframes,"available_symbols":available_symbols,"candles":candles,
                    "history_source":"MT5 historical rates saved in Supabase","max_rows":row_limit})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/backtest/learning":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                # Never select the latest customer's candles or learning run
                # for an owner. The authenticated identity owns exactly one ID.
                lic=license_for_user(u["id"])
                client_id=lic.get("client_id","") if lic and not lic.get("revoked") else ""
                if not client_id: return self.send_json(200,{"runs":[],"count":0})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit=max(1,min(100,int(finite_number((params.get("limit") or ["30"])[0],30))))
                rows=sb("GET","/rest/v1/alzool_engine_learning_runs?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&symbol=ilike."+ENGINE_MARKET+"*&select=id,run_key,symbol,timeframe,period_days,candle_count,test_points,validation_points,lot_size,commission_per_lot,slippage_points,account_currency,summary,strategy_results,combination_results,diagnostics,created_at"+
                    "&order=created_at.desc&limit="+str(limit),admin=True)
                current=CONNECTIONS.get(client_id) or {}
                current_symbol=learning_archive_symbol(client_id,current.get("symbol"))
                current_tf=current.get("timeframe") or "PERIOD_M1"
                cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
                # Filter BEFORE pagination. Older migrated XAUUSD deals must
                # never consume PostgREST's row limit on a BTC-only report.
                query=("/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&"+urllib.parse.quote("payload->>symbol",safe="")+"=eq."+urllib.parse.quote(current_symbol,safe="")+
                    "&deal_time=gte."+urllib.parse.quote(cutoff,safe="")+"&select=payload&order=deal_time.desc,deal_id.desc")
                deals=[]
                for offset in range(0,5000,1000):
                    page=sb("GET",query+"&limit=1000&offset="+str(offset),admin=True)
                    if not isinstance(page,list):raise ValueError("Invalid BTC deal history")
                    deals.extend(x["payload"] for x in page if isinstance(x,dict) and isinstance(x.get("payload"),dict))
                    if len(page)<1000:break
                live=live_learning_profile(combine_deals(deals,current.get("closed_deals") or []),symbol=current_symbol)
                profile_error=None
                history=btc_learning_snapshot(client_id,current_symbol,current_tf,sb)
                regime=((current.get("strategy_decision") or {}).get("context") or {}).get("regime","غير محدد")
                effective=btc_learning_weights(history,live,regime)
                computed=effective["diagnostics"]
                live_only=btc_learning_weights({},live,regime)
                live["updated_strategies"]=live_only["diagnostics"]["live_strategies"]
                for row in live["strategies"]:
                    row["weight"]=live_only["strategy_weights"].get(str(row["strategy_id"]),1.0)
                computed["history_refreshing"]=bool(history.get("refreshing"))
                computed["history_error"]=history.get("error")
                rows=[r for r in rows if btc_market(r.get("symbol"))]
                item=CONNECTIONS.get(client_id) or {}
                recent_decision=item.get("strategy_decision") or {}
                current_application=recent_decision.get("learning") or {}
                return self.send_json(200,{"runs":rows if isinstance(rows,list) else [],
                    "count":len(rows) if isinstance(rows,list) else 0,"live":live,"symbol":current_symbol,
                    "learning_computed":{**computed,
                       "active_version_label":ENGINE_MARKET+" ADAPTIVE V1",
                       "profile_read_error":profile_error},
                    "learning_applied":current_application,
                    "learning_applied_at":recent_decision.get("generated_at"),
                    "learning_decision_bar":recent_decision.get("bar_time")})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path.startswith("/v1/local-ai/context/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
            item=CONNECTIONS.get(client_id)
            if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة"})
            decision=item.get("strategy_decision") or {}
            if not decision:
                return self.send_json(409,{"error":"لا يوجد قرار مركزي لتحليله"})
            candles=item.get("closed_candles") or []
            return self.send_json(200,{
                "symbol":item.get("symbol",""),"timeframe":item.get("timeframe",""),
                "bar_time":int(finite_number(decision.get("bar_time",0))),
                "decision":decision.get("decision","WAIT"),
                "confidence":finite_number(decision.get("confidence",0)),
                "reason_ar":str(decision.get("reason_ar",""))[:500],
                "context":decision.get("context",{}),
                "candles":candles[:24],
                "local_ai_required":LOCAL_AI_REQUIRED,
                "local_ai_vote_current":(isinstance(item.get("local_ai_advice"),dict) and int(finite_number(item["local_ai_advice"].get("bar_time",0)))==int(finite_number(decision.get("bar_time",0))) and datetime.now(timezone.utc).timestamp()-finite_number(item["local_ai_advice"].get("received_at_unix",0))<=20)
            })
        if path.startswith("/v1/terminal-decision/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
            item=CONNECTIONS.get(client_id)
            if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة"})
            if not terminal_request_matches_market(self.path,item.get("symbol")):
                return self.send_json(409,{"execution_ready":False,"error":"رمز شارت MT5 لا يطابق بيانات المحرك"})
            decision=item.get("strategy_decision") or {}
            confidence=finite_number(decision.get("confidence",0.0))
            signal=str(decision.get("decision","WAIT"))
            strategy_id=int(finite_number(decision.get("strategy_id",0)))
            control_settings=get_trade_settings(client_id)
            if control_settings is None:
                control_settings=item.get("trade_settings_cache") or {}
            else:
                item["trade_settings_cache"]=control_settings
            trading_enabled=bool(control_settings.get("trading_enabled",False))
            mode=entry_council_mode(control_settings,terminal_is_owner(client_id))
            advisory_mode=mode!="STRICT"
            ai_council=council_result(client_id,decision)
            if advisory_mode:
                decision=resolve_owner_decision(client_id,decision,ai_council,mode)
                if mode=="MASTER":decision=master_balanced.select_demo(decision,item,control_settings,datetime.now(timezone.utc).timestamp())
                confidence=finite_number(decision.get("confidence",0.0))
                signal=str(decision.get("decision","WAIT"))
                strategy_id=int(finite_number(decision.get("strategy_id",0)))
            gate=evaluate_entry_gate(
                decision=decision,
                account=item.get("account") or {},
                symbol=item.get("symbol",""),
                received_at=item.get("received_at"),
                now=datetime.now(timezone.utc),
                trading_enabled=trading_enabled,
                local_ai_advice=(
                    {**item.get("local_ai_advice",{}),
                     "age_seconds":datetime.now(timezone.utc).timestamp()-finite_number(item["local_ai_advice"].get("received_at_unix",0))}
                    if isinstance(item.get("local_ai_advice"),dict) else None
                ),
                require_local_ai=LOCAL_AI_REQUIRED and not advisory_mode,
                strategy_first=advisory_mode
            )
            review_report={}
            if mode=="MASTER":
                review_report=master_entry_review.review(decision,item,control_settings,datetime.now(timezone.utc).timestamp(),live_arena=True)
                gate=master_entry_review.apply_gate(gate,review_report)
                decision["master"]={**(decision.get("master") or {}),"entry_review":review_report}
            risk_report=risk_guardian(item.get("account") or {},item.get("positions") or [],risk_limits_from_settings(control_settings))
            direction_limit=entry_direction_capacity(item.get("positions") or [],item.get("symbol",""),signal,control_settings)
            # Late model votes must never turn an older closed bar into a new order.
            bar_time=int(finite_number(decision.get("bar_time",0)))
            bar_fresh=closed_bar_execution_fresh(bar_time,datetime.now(timezone.utc),
                                                   item.get("timeframe","M1"))
            confirmation=post_close_m1_confirmation(
                item.get("last_closed_deal_time"),item.get("last_m1_closed_open"),
                decision.get("bar_time"),datetime.now(timezone.utc))
            ready=bool(bar_fresh and gate.get("approved") and
                       (advisory_mode or ai_council["approved"]) and
                       risk_report["approved"] and direction_limit["approved"] and
                       confirmation["approved"])
            if not bar_fresh and signal in ("BUY","SELL"):
                gate={**gate,"approved":False,"status":"WAIT","authorization_id":None,
                      "expires_at_unix":0,"reason_ar":"انتهت نافذة تنفيذ الشمعة المؤكدة؛ انتظار شمعة جديدة"}
            if not advisory_mode and not ai_council["approved"] and bar_fresh:
                gate={**gate,"approved":False,"status":"WAIT","authorization_id":None,
                      "expires_at_unix":0,"reason_ar":ai_council["reason_ar"]}
            if not risk_report["approved"]:
                gate={**gate,"approved":False,"status":"WAIT","authorization_id":None,
                      "expires_at_unix":0,"reason_ar":risk_report["reason_ar"]}
            if not direction_limit["approved"] and signal in ("BUY","SELL"):
                gate={**gate,"approved":False,"status":"WAIT","authorization_id":None,
                      "expires_at_unix":0,"reason_ar":direction_limit["reason_ar"]}
            if not confirmation["approved"]:
                gate={**gate,"approved":False,"status":"WAIT","authorization_id":None,
                      "expires_at_unix":0,"reason_ar":confirmation["reason_ar"]}
            council_event=build_council_event(client_id,decision,gate,trading_enabled)
            queue_ai_diagnostic_event(council_event)
            bar_time=int(finite_number(decision.get("bar_time",0)))
            output_signal=signal if ready else ("WAIT" if signal in ("BUY","SELL") else signal)
            full_decision={"symbol":item.get("symbol",""),"signal":output_signal,
                "confidence":confidence,"reason_ar":(decision.get("reason_ar","بانتظار اكتمال بيانات التحليل") if ready else gate.get("reason_ar","تم رفض الدخول بواسطة المجلس أو الحماية")),
                "decision_reason_ar":decision.get("reason_ar","بانتظار اكتمال بيانات التحليل"),
                "strategy_id":strategy_id,"strategy_name":decision.get("strategy_name",""),
                "combination_id":int(finite_number(decision.get("combination_id",0))),
                "bar_time":bar_time,"entry_reference_price":finite_number(decision.get("entry_reference_price",0.0)),
                "entry_atr":finite_number(decision.get("entry_atr",0.0)),"execution_ready":ready,"trading_enabled":trading_enabled,
                **master_bias(decision,bar_fresh),"minimum_confidence":None,"entry_review":review_report,
                "entry_council_mode":mode,"master":decision.get("master",{}),
                # Retain the old wire key for compatibility, but this route is no longer demo-only.
                "master_direct_demo":False,
                "master_unified_active":review_report.get("route")=="DIRECT_DEMO",
                "authorization":gate,
                "authorization_id":gate.get("authorization_id"),
                "authorization_expires_at":gate.get("expires_at_unix",0),
                "council":decision.get("council",{}),
                "multi_model_council":ai_council,"hybrid":decision.get("hybrid",{}),"risk_guardian":risk_report,
                "direction_limit":direction_limit,
                "post_close_m1_confirmation":confirmation,
                "local_ai_required":LOCAL_AI_REQUIRED,
                "local_ai_vote":({k:item["local_ai_advice"].get(k) for k in ("direction","confidence","reason_ar","model","bar_time")} if isinstance(item.get("local_ai_advice"),dict) else None),
                "message":gate.get("reason_ar","بانتظار قرار المجلس والحماية")}
            # Read-only owner audit: capture the actual response prepared for MT5.
            # This is an authorization record, never a broker execution receipt.
            if mode=="MASTER" and bar_time>0:
                try:
                    report=decision.get("master") or {}
                    audit_status="AUTHORIZED" if ready else "WAIT"
                    # Include model evidence in the idempotency key so a new
                    # adviser response within the same bar is not silently lost.
                    evidence_key=sorted([(str(m.get("model")),str(m.get("status")),
                        str(m.get("direction")),bool(m.get("used")),
                        round(finite_number(m.get("confidence")),2),
                        round(finite_number(m.get("points")),3))
                        for m in report.get("models") or [] if isinstance(m,dict)])
                    audit_key=hashlib.sha256(json.dumps([client_id,item.get("symbol"),bar_time,
                        strategy_id,full_decision["combination_id"],signal,audit_status,
                        gate.get("authorization_id") if ready else None,review_report.get("state"),
                        evidence_key,round(confidence,2),gate.get("reason_ar")],ensure_ascii=False).encode()).hexdigest()
                    queue_ai_diagnostic_event({"event_id":"master-audit:"+audit_key,
                        "client_id":client_id,"event_type":"MASTER_DECISION_AUDIT",
                        "payload":{"status":audit_status,"symbol":item.get("symbol"),
                            "timeframe":item.get("timeframe"),"bar_time":bar_time,
                            "master_protocol_reported":item.get("master_direct_demo_capable") is True,
                            "master_owner_compatible":item.get("owner_master_compatible") is True,
                            "master_hybrid_enabled":hybrid_demo_pilot.allowed(item),
                            "direction":signal,"signal":output_signal,"confidence":confidence,
                            "strategy_id":strategy_id,"strategy_name":full_decision["strategy_name"],
                            "combination_id":full_decision["combination_id"],
                            "reason_ar":full_decision["decision_reason_ar"],
                            "gate_reason_ar":gate.get("reason_ar"),"source":report.get("source"),
                            "models":report.get("models") or [],"scores":report.get("scores") or {},
                            "calibration_applied":report.get("calibration_applied") is True,"entry_review":review_report,
                            "adaptive_shadow":report.get("adaptive_shadow") or {},
                            "arena_advisory":report.get("arena_advisory") or {},
                            "direction_reconciliation":report.get("direction_reconciliation") or {},
                            "decision_at":datetime.now(timezone.utc).isoformat()}})
                except Exception as audit_error:
                    print("MASTER_AUDIT_WARNING:",type(audit_error).__name__,flush=True)
            if not terminal_is_owner(client_id):
                return self.send_json(200,minimal_terminal_decision(full_decision))
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if params.get("compact")==["1"]:
                from client_access import compact_owner_terminal_decision
                return self.send_json(200,compact_owner_terminal_decision(full_decision))
            return self.send_json(200,full_decision)
        if path=="/api/market/context":
            try:
                import time
                t0=time.perf_counter()
                # GET /auth/v1/user returns the current user record from Supabase,
                # including banned_until when a ban is active. Reuse this fresh record
                # instead of making a second, slower admin-user request.
                u=current_user(token_of(self)); t_user=time.perf_counter()
                t_admin=t_user
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                client_id,item=connection_for_authenticated_user(u)
                t_lookup=time.perf_counter()
                connected=bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30)
                if not connected:
                    return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة. تحقق من اتصال الطرفية.",
                        "diagnostics":{"auth_ms":round((t_user-t0)*1000),"ban_check_ms":round((t_admin-t_user)*1000),"ban_check_source":"fresh_auth_user_record",
                        "connection_lookup_ms":round((t_lookup-t_admin)*1000),"server_total_ms":round((t_lookup-t0)*1000)}})
                candles=(item or {}).get("closed_candles",[])
                # MT5 sends series data newest-first; return oldest-first for analysis.
                result={"symbol":item.get("symbol"),"timeframe":item.get("timeframe"),
                    "account_currency":str((item.get("account") or {}).get("currency",""))[:12],
                    "tick":item.get("tick",{}),"forming_candle":item.get("forming_candle"),
                    "candles":list(reversed(candles)),"strategy_decision":item.get("strategy_decision"),
                    "engine_counts":{"strategies":len(STRATEGIES),"combinations":len(COMBINATIONS)},
                    "received_at":item["last_seen"],
                    "diagnostics":{"auth_ms":round((t_user-t0)*1000),"ban_check_ms":round((t_admin-t_user)*1000),"ban_check_source":"fresh_auth_user_record",
                    "connection_lookup_ms":round((t_lookup-t_admin)*1000),"server_total_ms":round((t_lookup-t0)*1000)}}
                return self.send_json(200,result)
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/dashboard/summary":
            try:
                u=current_user(token_of(self)); fresh=admin_user(u["id"])
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                # Owner and client data always come from their own authenticated identity.
                client_id,item=connection_for_authenticated_user(u)
                connected=bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30)
                account=(item or {}).get("account",{}) if connected else {}
                positions=(item or {}).get("positions",[]) if connected else []
                deals=(item or {}).get("closed_deals",[]) if connected else []
                # Durable 30-day history from Supabase. Resolve the latest stored client
                # for the owner as a fallback if the account licence lookup is unavailable.
                history_records=0
                history_error=""
                try:
                    if client_id:
                        cutoff=report_cutoff(30)
                        stored=archive_read(sb,"/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                            "&deal_time=gte."+urllib.parse.quote(cutoff,safe="")+
                            "&select=deal_time,payload&order=deal_time.desc&limit=2000")
                        if isinstance(stored,list):
                            history_records=len(stored)
                            deals=[]
                            for record in stored:
                                if not isinstance(record,dict) or not isinstance(record.get("payload"),dict): continue
                                deal=dict(record["payload"])
                                if not int(deal.get("time") or 0) and record.get("deal_time"):
                                    deal["time"]=int(datetime.fromisoformat(record["deal_time"].replace("Z","+00:00")).timestamp())
                                if btc_market(deal.get("symbol")):
                                    deals.append(deal)
                            history_records=len(deals)
                except Exception as history_error_value:
                    history_error=str(history_error_value)[:180]
                    print("CLOSED_HISTORY_READ_WARNING:",history_error,flush=True)
                uae=timezone(timedelta(hours=4))
                today=datetime.now(uae).date()
                by_position={}
                daily_by_position={}
                daily_profit=daily_loss=0.0
                for deal in deals:
                    if not btc_market(deal.get("symbol")): continue
                    stamp=int(deal.get("time",0) or 0)
                    if stamp<=0: continue
                    closed_at=datetime.fromtimestamp(stamp,timezone.utc)
                    pid=str(deal.get("position_id") or deal.get("ticket") or stamp)
                    net=finite_number(deal.get("profit"))+finite_number(deal.get("commission"))+finite_number(deal.get("swap"))+finite_number(deal.get("fee"))
                    row=by_position.setdefault(pid,{"position_id":pid,"symbol":deal.get("symbol",""),"type":(0 if deal.get("deal_type",deal.get("type",0))==1 else 1),"volume":0.0,"net":0.0,"time":stamp,"ticket":deal.get("ticket","")})
                    row["net"]+=net
                    row["volume"]+=finite_number(deal.get("volume"))
                    row["time"]=max(row["time"],stamp)
                    if closed_at.astimezone(uae).date()==today:
                        daily_profit+=max(net,0.0)
                        daily_loss+=min(net,0.0)
                        drow=daily_by_position.setdefault(pid,{"net":0.0,"time":stamp})
                        drow["net"]+=net
                        drow["time"]=max(drow["time"],stamp)
                closed=list(by_position.values())
                wins=sum(1 for row in closed if row["net"]>0)
                win_rate=(wins/len(closed)*100.0) if closed else None
                daily_net=daily_profit+daily_loss
                daily_count=len(daily_by_position)
                closed.sort(key=lambda row:row["time"],reverse=True)
                return self.send_json(200,{"connected":connected,
                    "balance":account.get("balance"),"equity":account.get("equity"),
                    "floating_pl":account.get("profit"),"currency":account.get("currency"),
                    "open_trades":len(positions),"win_rate":win_rate,
                    "closed_count":len(closed),"closed_trades":closed[:20],
                    "history_records":history_records,"history_client_id":client_id,"history_error":history_error,
                    "daily":{"profit":daily_profit,"loss":daily_loss,"net":daily_net,"trades":daily_count},
                    "account":account,"positions":positions,
                    "orders":(item or {}).get("orders",[]) if connected else [],
                    "symbol":(item or {}).get("symbol") if connected else None,
                    "timeframe":(item or {}).get("timeframe") if connected else None,
                    "last_seen":(item or {}).get("last_seen") if connected else None})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/opportunities/events":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                # Do not fall back to another user's latest connection.
                if not client_id: return self.send_json(200,{"events":[],"count":0})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit=max(1,min(500,int(finite_number((params.get("limit") or ["200"])[0],200))))
                days=int(finite_number((params.get("days") or ["30"])[0],30))
                days=days if days in (0,7,30,90,365) else 30
                kind=str((params.get("kind") or ["opportunities"])[0])
                event_types={
                    "master_v2":["MASTER_V2_FORECAST","MASTER_V2_COMPARISON"],
                    "master_v3":["MASTER_V3_FORECAST","MASTER_V3_OUTCOME","MASTER_V3_EVIDENCE_CHANGE"],
                    "master":["MASTER_DECISION_AUDIT","AI_COUNCIL_DECISION","AI_COUNCIL_VETO","AI_COUNCIL_WAIT"],
                    "learning":["LIVE_LEARNING_APPLIED","LEARNING_DECISION_AUDIT","AI_ACCURACY_V4_OUTCOME"],
                    "opportunities":["SIGNAL_CREATED","WAIT","BLOCKED","AI_QWEN_VOTE","AI_COUNCIL_DECISION","AI_COUNCIL_WAIT","AI_COUNCIL_VETO"],
                    "trades":["TRADE_OPENED","TRADE_CLOSED","ORDER_SENT","ADDON_OPENED","COOLING_OPENED","PROTECTION","BLOCKED","CENTRAL_ACTION_CLAIM","RECOVERY_ARMED","RECOVERY_WAITING","RECOVERY_OPENED","RECOVERY_BLOCKED","RECOVERY_REJECTED","RECOVERY_CLOSED","RECOVERY_CLOSE_RETRY"]
                }.get(kind)
                query="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client_id,safe="")
                if event_types:
                    query+="&event_type=in."+urllib.parse.quote("("+",".join(event_types)+")",safe=",()")
                if days:
                    cutoff=report_cutoff(days)
                    query+="&created_at=gte."+urllib.parse.quote(cutoff,safe="")
                query+="&select=event_id,event_type,payload,created_at&order=created_at.desc&limit="+str(limit)
                rows=archive_read(sb,query)
                return self.send_json(200,{"events":rows if isinstance(rows,list) else [],
                    "count":len(rows) if isinstance(rows,list) else 0,"client_id":client_id,"days":days,"kind":kind})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/trade-history":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id: return self.send_json(200,{"trades":[],"count":0,"currency":""})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                days=int(finite_number((params.get("days") or ["30"])[0],30))
                days=days if days in (0,7,30,90,365) else 30
                query="/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")
                if days:
                    cutoff=report_cutoff(days)
                    query+="&deal_time=gte."+urllib.parse.quote(cutoff,safe="")
                query+="&select=deal_time,payload&order=deal_time.desc"
                records=[]; offset=0
                while len(records)<5000:
                    page=archive_read(sb,query+"&limit=1000&offset="+str(offset))
                    if not isinstance(page,list) or not page: break
                    records.extend(page); offset+=len(page)
                    if len(page)<1000: break
                grouped={}
                for record in records[:5000]:
                    deal=record.get("payload") if isinstance(record.get("payload"),dict) else {}
                    if not btc_market(deal.get("symbol")): continue
                    pid=str(deal.get("position_id") or deal.get("ticket") or record.get("deal_time") or "")
                    if not pid: continue
                    row=grouped.setdefault(pid,{"position_id":pid,"symbol":str(deal.get("symbol") or ""),
                        "side":"غير محدد","volume":0.0,"open_volume":0.0,"close_volume":0.0,
                        "side_priority":0,"net":0.0,"time":0,"ticket":str(deal.get("ticket") or "")})
                    net=(finite_number(deal.get("profit"))+finite_number(deal.get("commission"))+
                         finite_number(deal.get("swap"))+finite_number(deal.get("fee")))
                    row["net"]+=net
                    entry=int(finite_number(deal.get("entry"),-1))
                    deal_type=int(finite_number(deal.get("deal_type",deal.get("type",-1)),-1))
                    volume=finite_number(deal.get("volume"))
                    if entry==0:
                        row["has_entry_deal"]=True
                        row["entry_comment"]=str(deal.get("comment") or "")[:120]
                        if deal.get("time"):row["opened_at"]=int(finite_number(deal.get("time")))
                        row["side"]="شراء" if deal_type==0 else "بيع" if deal_type==1 else row["side"]
                        row["side_priority"]=2
                        row["open_volume"]+=volume
                    elif entry in (1,3):
                        row["has_exit_deal"]=True
                        if deal.get("strategy_comment"):row["entry_comment"]=str(deal["strategy_comment"])[:120]
                        if deal.get("strategy_id"):row["strategy_id"]=int(finite_number(deal["strategy_id"]))
                        if row["side_priority"]<2:
                            row["side"]="شراء" if deal_type==1 else "بيع" if deal_type==0 else row["side"]
                            row["side_priority"]=1
                        row["close_volume"]+=volume
                    stamp=int(finite_number(deal.get("time")))
                    if not stamp and record.get("deal_time"):
                        try: stamp=int(datetime.fromisoformat(record["deal_time"].replace("Z","+00:00")).timestamp())
                        except Exception: stamp=0
                    row["time"]=max(row["time"],stamp)
                    if not row["symbol"]: row["symbol"]=str(deal.get("symbol") or "")
                for row in grouped.values():
                    row["volume"]=row.pop("open_volume") or row.pop("close_volume")
                    row.pop("side_priority",None)
                    row.pop("close_volume",None)
                trades=sorted(grouped.values(),key=lambda x:x["time"],reverse=True)
                item=CONNECTIONS.get(client_id)
                currency=((item or {}).get("account") or {}).get("currency","")
                if not currency:
                    currency_rows=sb("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                        "&select=account_currency&order=uploaded_at.desc&limit=1",admin=True)
                    currency=currency_rows[0].get("account_currency","") if currency_rows else ""
                if (params.get("view") or [""])[0]=="groups":
                    from closure_groups import closure_groups
                    groups=closure_groups(records[:5000],btc_market)
                    return self.send_json(200,{"groups":groups,"currency":currency,"days":days,
                        "market":ENGINE_MARKET,"grouping_window_seconds":5,"limit_reached":len(records)>=5000,
                        "metric":"RECORDED_EXIT_DEAL_NET_ONLY"})
                wins=sum(1 for x in trades if x["net"]>0)
                return self.send_json(200,{"trades":trades,"count":len(trades),"deal_rows":len(records),
                    "win_rate":(wins/len(trades)*100) if trades else None,
                    "net":sum(x["net"] for x in trades),"currency":currency,"days":days})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/ai/actions/control":
            try:
                u=owner(token_of(self))
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"حساب المالك غير مرتبط بالمحرك"})
                settings=get_trade_settings(identity["client_id"]) or {}
                pm=settings.get("position_management") or {}
                return self.send_json(200,{"enabled":pm.get("central_actions_enabled") is True,
                    "trading_enabled":settings.get("trading_enabled") is True,
                    "requires_mt5_opt_in":True,
                    "message":"لا تنفيذ بدون موافقة المجلس، تأكيد المالك، ومفتاح MT5 المحلي"})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/research":
            try:
                u=owner(token_of(self))
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"معرّف التخزين غير موجود"})
                cid=urllib.parse.quote(identity["client_id"],safe="")
                logs=sb("GET","/rest/v1/alzool_engine_signal_events?client_id=eq."+cid+
                    "&event_type=eq.AI_RESEARCH_RESULT&select=created_at,payload"+
                    "&order=created_at.desc&limit=5",admin=True)
                return self.send_json(200,{"auto_activate":False,"runs":logs})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/versions":
            try:
                u=owner(token_of(self))
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"لا يوجد معرّف تخزين مرتبط بحساب المالك"})
                return self.send_json(200,{"versions":[],"market":ENGINE_MARKET,
                    "note":"إصدارات الذهب محفوظة منفصلة؛ لا تُفعّل على البيتكوين حتى يتوفر مخزن إصدارات مخصص."})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:200]})
        if path=="/api/ai/actions":
            try:
                u=current_user(token_of(self))
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                item=CONNECTIONS.get(client_id) if client_id else None
                if not item: return self.send_json(200,{"mode":"DIAGNOSTIC_ONLY",
                    "execution_authorized":False,"actions":[],"reason_ar":"لا توجد بيانات MT5 حديثة",
                    "position_management_status":position_management_status(None,None)})
                decision=item.get("strategy_decision") or {}
                council=council_result(client_id,decision)
                settings=get_trade_settings(client_id) or {}
                mode=entry_council_mode(settings,terminal_is_owner(client_id))
                advisory=mode!="STRICT"
                if advisory:decision=resolve_owner_decision(client_id,decision,council,mode)
                risk=risk_guardian(item.get("account") or {},item.get("positions") or [],risk_limits_from_settings(settings))
                result=plan_account_actions(item,decision,council,risk,settings,council_advisory=advisory)
                result["position_management_status"]=position_management_status(item,settings)
                return self.send_json(200,result)
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/strategy-audit":
            try:
                u=owner(token_of(self))
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id:return self.send_json(409,{"error":"معرّف محرك المالك غير موجود"})
                snapshot=CONNECTIONS.get(client_id) or {}
                symbol=str(snapshot.get("symbol") or "XAUUSD")[:40]
                timeframe=str(snapshot.get("timeframe") or "PERIOD_M1")[:20]
                if not supported_market_symbol(symbol):
                    return self.send_json(409,{"error":"تدقيق الاستراتيجيات متاح لرمز XAUUSD أو BTCUSD فقط"})
                q=urllib.parse.quote
                query=("/rest/v1/alzool_engine_market_candles?client_id=eq."+q(client_id,safe="")+
                    "&symbol=eq."+q(symbol,safe="")+"&timeframe=eq."+q(timeframe,safe="")+
                    "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume"+
                    "&order=candle_time.desc&limit=230")
                rows=sb("GET",query,admin=True)
                if not rows and timeframe!="PERIOD_M1":
                    query=("/rest/v1/alzool_engine_market_candles?client_id=eq."+q(client_id,safe="")+
                        "&symbol=eq."+q(symbol,safe="")+
                        "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume"+
                        "&order=candle_time.desc&limit=230")
                    rows=sb("GET",query,admin=True)
                result=audit_strategies(list(reversed(rows)),max_samples=18)
                result.update({"symbol":symbol,"timeframe":timeframe,
                    "source":"stored_closed_candles","read_only":True})
                return self.send_json(200,result)
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(503,{"error":"تعذر إكمال التدقيق",
                "reason_type":type(e).__name__})
        if path=="/api/ai/learning":
            try:
                u=current_user(token_of(self))
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id: return self.send_json(200,{"status":"NO_CONNECTION","memory_count":0})
                memory=memory_snapshot(client_id,sb)
                current=CONNECTIONS.get(client_id) or {}
                deals=combine_deals(memory["deals"],current.get("closed_deals") or [])
                symbol=learning_archive_symbol(client_id,current.get("symbol"))
                profile=live_learning_profile(deals,symbol=symbol)
                last_decision=(current.get("strategy_decision") or {})
                regime=(last_decision.get("context") or {}).get("regime","غير محدد")
                historical=btc_learning_snapshot(client_id,symbol,current.get("timeframe") or "PERIOD_M1",sb)
                computed=btc_learning_weights(historical,profile,regime)["diagnostics"]
                computed["history_refreshing"]=bool(historical.get("refreshing"))
                computed["history_error"]=historical.get("error")
                btc_deals=[d for d in deals if same_market_symbol(d.get("symbol"),symbol)]
                last_applied=last_decision.get("learning") or {}
                # A calculated weight is NOT evidence of a recent candle analysis or an executed order.
                health=live_learning_health(symbol,current,last_decision,profile)
                persisted_audit=None
                audit_read_error=None
                try:
                    events=archive_read(sb,"/rest/v1/alzool_engine_signal_events?client_id=eq."+
                        urllib.parse.quote(client_id,safe="")+
                        "&event_type=eq.LEARNING_DECISION_AUDIT&select=created_at,payload"+
                        "&order=created_at.desc&limit=80")
                    for record in events if isinstance(events,list) else []:
                        proof=record.get("payload") if isinstance(record,dict) else None
                        if isinstance(proof,dict) and same_market_symbol(proof.get("symbol"),symbol):
                            persisted_audit={"created_at":record.get("created_at"),**proof}
                            break
                except Exception as audit_error:
                    # Read-only observability must never interrupt learning or MT5 decisions.
                    audit_read_error=type(audit_error).__name__
                return self.send_json(200,{"status":memory["status"],"memory_count":len(btc_deals),"symbol":symbol,
                    "closed_positions":profile["closed_positions"],
                    "processed_positions":profile["processed_positions"],"coverage_percent":profile["coverage_percent"],
                    "last_closed_at":profile["last_closed_at"],"last_attributed_at":profile["last_attributed_at"],
                    "eligible_strategies":computed["eligible_strategies"],
                    "eligible_combinations":computed["eligible_combinations"],
                    "learning_computed":computed,
                    "wins":profile["wins"],"losses":profile["losses"],
                    "learning_applied":last_applied,
                    "learning_applied_at":last_decision.get("generated_at"),
                    "live_learning_health":health,
                    "last_persisted_learning_audit":persisted_audit,
                    "learning_audit_read_error":audit_read_error,
                    "top_strategies":profile["strategies"][:10]})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/arena/action-report":
            try:
                u=owner(token_of(self))
                from model_arena_api import ids,enc
                owned=ids(sb,u["id"],ENGINE_MARKET)
                rows=sb("GET","/rest/v1/alzool_engine_signal_events?client_id=in.("+",".join(enc(x) for x in owned)+")&event_type=eq.ARENA_ACTION_TRAINING&order=created_at.desc&limit=1&select=payload,created_at",admin=True) if owned else []
                return self.send_json(200,rows[0] if rows else {"status":"NO_TRAINING"})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception:return self.send_json(502,{"error":"action_report_unavailable"})
        if path=="/api/ai/arena/parallel-learning":
            try:
                u=owner(token_of(self))
                from model_arena_api import ids
                from parallel_learning import read_evidence
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                cid=(params.get("client") or [""])[0];symbol=(params.get("symbol") or [""])[0]
                if cid not in ids(sb,u["id"],ENGINE_MARKET) or not btc_market(symbol):
                    return self.send_json(403,{"error":"invalid owner market/client"})
                return self.send_json(200,read_evidence(sb,cid,symbol))
            except PermissionError:return self.send_json(403,{"error":"owner required"})
            except Exception:return self.send_json(502,{"error":"parallel_learning_unavailable"})
        if path=="/api/ai/arena/parallel-monitor":
            try:
                u=owner(token_of(self))
                from parallel_shadow import snapshot
                return self.send_json(200,snapshot(u["id"],ENGINE_MARKET))
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception:return self.send_json(502,{"error":"parallel_monitor_unavailable"})
        if path=="/api/ai/arena/live-context":
            try:
                u=owner(token_of(self))
                from model_arena_api import ids
                for cid in ids(sb,u["id"],ENGINE_MARKET):
                    item=CONNECTIONS.get(cid) or {}
                    ctx=arena_master.context(cid,item)
                    if ctx.get("status")=="READY":
                        params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                        from bandwidth_payload import unchanged_context
                        return self.send_json(200,unchanged_context(ctx,params))
                return self.send_json(200,{"status":"NO_CURRENT_M1"})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception:return self.send_json(502,{"error":"arena_context_unavailable"})
        if path in ("/api/ai/arena","/api/ai/arena/history"):
            try:
                u=owner(token_of(self))
                import model_arena_api as arena
                result=arena.report(sb,u["id"],ENGINE_MARKET) if path=="/api/ai/arena" else arena.query(sb,u["id"],ENGINE_MARKET,urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))
                return self.send_json(200,result)
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception:return self.send_json(502,{"error":"arena_unavailable"})
        if path=="/api/ai/specialist":
            try:
                owner(token_of(self))
                from specialist_market import report
                data=report(ENGINE_MARKET)
                return self.send_json(200,data.get("report",{"status":"LOADING","execution_authorized":False}))
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception:return self.send_json(502,{"error":"specialist_unavailable"})
        if path=="/api/ai/monitor/details":
            # Large strategy/combination evidence is loaded only on demand.
            try:
                u=owner(token_of(self))
                lic=license_for_user(u["id"])
                cid=(lic or {}).get("client_id","")
                item=CONNECTIONS.get(cid) if cid else None
                if not item:
                    return self.send_json(200,{"connected":False,"strategies":[],"combinations":[]})
                decision=item.get("strategy_decision") or {}
                return self.send_json(200,{"connected":True,"bar_time":decision.get("bar_time"),
                    "strategies":decision.get("votes") or [],
                    "combinations":decision.get("combination_votes") or [],
                    "vote_summary":decision.get("vote_summary") or {},
                    "combination_summary":decision.get("combination_summary") or {}})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/monitor":
            # Owner-only, read-only mirror of the actual decision gates.
            try:
                u=owner(token_of(self))
                lic=license_for_user(u["id"])
                cid=(lic or {}).get("client_id","")
                item=CONNECTIONS.get(cid) if cid else None
                if not item:
                    return self.send_json(200,{"connected":False,"reason_ar":"MT5 غير متصل","signal":"WAIT"})
                now=datetime.now(timezone.utc)
                decision=item.get("strategy_decision") or {}
                settings=get_trade_settings(cid) or {}
                mode=entry_council_mode(settings,True)
                advisory=mode!="STRICT"
                council=council_result(cid,decision)
                if advisory: decision=resolve_owner_decision(cid,decision,council,mode)
                management_decision=decision
                if mode=="MASTER":decision=master_balanced.select_demo(decision,item,settings,now.timestamp())
                signal=str(decision.get("decision","WAIT")).upper()
                local=item.get("local_ai_advice")
                local_advice=({**local,"age_seconds":now.timestamp()-finite_number(local.get("received_at_unix",0))}
                    if isinstance(local,dict) else None)
                gate=evaluate_entry_gate(decision,item.get("account") or {},item.get("symbol",""),
                    item.get("received_at"),now,bool(settings.get("trading_enabled")),
                    local_ai_advice=local_advice,require_local_ai=LOCAL_AI_REQUIRED and not advisory,
                    strategy_first=advisory)
                review_report={}
                if mode=="MASTER":
                    review_report=master_entry_review.review(decision,item,settings,now.timestamp(),live_arena=True)
                    gate=master_entry_review.apply_gate(gate,review_report)
                    decision["master"]={**(decision.get("master") or {}),"entry_review":review_report}
                risk=risk_guardian(item.get("account") or {},item.get("positions") or [],
                    risk_limits_from_settings(settings))
                direction=entry_direction_capacity(item.get("positions") or [],item.get("symbol",""),
                    signal,settings)
                fresh=closed_bar_execution_fresh(decision.get("bar_time"),now,item.get("timeframe","M1"))
                confirmation=post_close_m1_confirmation(
                    item.get("last_closed_deal_time"),item.get("last_m1_closed_open"),
                    decision.get("bar_time"),now)
                ready=bool(fresh and gate.get("approved") and (advisory or council.get("approved"))
                    and risk.get("approved") and direction.get("approved") and
                    confirmation["approved"])
                if not confirmation["approved"]:reason=confirmation["reason_ar"]
                elif not gate.get("approved"):reason=gate.get("reason_ar","رفض بوابة الدخول")
                elif not fresh:reason="انتهت نافذة تنفيذ شمعة التأكيد"
                elif not advisory and not council.get("approved"):reason=council.get("reason_ar","المجلس ينتظر")
                elif not risk.get("approved"):reason=risk.get("reason_ar","حارس الحساب منع الدخول")
                elif not direction.get("approved"):reason=direction.get("reason_ar","حد صفقات الاتجاه")
                else:reason="إشارة جاهزة للعرض على MT5؛ التنفيذ النهائي يتحقق منه EA"
                shadow={}
                if mode=="MASTER":
                    try:
                        base=item.get("strategy_decision") or {}
                        feedback=feedback_learning.snapshot(cid,item.get("symbol"),sb)
                        age=now.timestamp()-finite_number(base.get("bar_time"))-60
                        shadow={"mode":"SHADOW_ONLY","report":feedback.get("master_v2") or {},
                            "feedback_status":feedback.get("status"),"action_evidence":feedback.get("actions") or [],
                            "candidate":master_v2.candidate(base,council,feedback,"EARLY" if age<20 else "REVIEW") if fresh else {}}
                    except Exception:
                        shadow={"mode":"SHADOW_ONLY","status":"UNAVAILABLE"}
                v3_shadow={}
                if mode=="MASTER":
                    try:
                        v3_shadow={"mode":"SHADOW_ONLY","latest":master_v3.latest(cid,item.get("symbol")),
                            "report":feedback_learning.snapshot(cid,item.get("symbol"),sb).get("master_v3") or {},
                            "adaptive_report":feedback_learning.snapshot(cid,item.get("symbol"),sb).get("master_adaptive") or {}}
                    except Exception:v3_shadow={"mode":"SHADOW_ONLY","status":"UNAVAILABLE"}
                bot=item.get("bot_report") or {}
                actions=plan_account_actions(item,management_decision,council,risk,settings,council_advisory=advisory)
                actions["specialist_shadow"]=arena_master.management(item,management_decision,actions)
                return self.send_json(200,{
                    "connected":0<=(now-item.get("received_at",now-timedelta(days=1))).total_seconds()<=30,
                    "last_seen":item.get("last_seen"),"symbol":item.get("symbol"),
                    "timeframe":item.get("timeframe"),"signal":"WAIT" if mode=="MASTER" and not review_report.get("approved") else signal,
                    "candidate_signal":signal,"entry_review":review_report,
                    "entry_execution":entry_execution_status(item,ready,now),
                    "confidence":decision.get("confidence"),"strategy_id":decision.get("strategy_id"),
                    "strategy_name":decision.get("strategy_name"),
                    "reason_ar":reason,"decision_reason_ar":decision.get("reason_ar"),
                    "bar_time":decision.get("bar_time"),"entry_ready":ready,
                    "mode":mode,"master":decision.get("master",{}),"master_v2":shadow,"master_v3":v3_shadow,
                    "trading_enabled":bool(settings.get("trading_enabled")),
                    "central_actions_enabled":(settings.get("position_management") or {}).get("central_actions_enabled") is True,
                    "mt5_position_management_enabled":item.get("mt5_position_management_enabled"),
                    "mt5_central_trading_enabled":item.get("mt5_central_trading_enabled"),
                    "bar_fresh":fresh,"gate":gate,"risk":risk,"direction_limit":direction,
                    "post_close_m1_confirmation":confirmation,
                    "council":council,"hybrid":decision.get("hybrid",{}),"local_ai":local_advice,
                    "family_votes":decision.get("family_votes") or {},
                    "combination_summary":decision.get("combination_summary") or {},
                    "bot_evidence":decision.get("bot_evidence") or {},
                    "bot_report":bot,"actions":actions,
                    "learning":decision.get("learning") or {},
                    "learning_applied_at":decision.get("generated_at"),
                    "strategy_context":decision.get("context") or {}})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/ai/council":
            try:
                u=current_user(token_of(self))
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                item=CONNECTIONS.get(client_id) if client_id else None
                if not item: return self.send_json(200,{"connected":False,"status":"WAIT","reason_ar":"MT5 غير متصل"})
                decision=item.get("strategy_decision") or {}
                settings=get_trade_settings(client_id) or {}
                mode=entry_council_mode(settings,terminal_is_owner(client_id))
                is_hybrid=mode!="STRICT"
                council=council_result(client_id,decision)
                if is_hybrid:decision=resolve_owner_decision(client_id,decision,council,mode)
                return self.send_json(200,{"connected":True,"client_id":client_id,
                    "bar_time":decision.get("bar_time"),"signal":decision.get("decision","WAIT"),
                    "mode":"MASTER" if mode=="MASTER" else "HYBRID" if is_hybrid else "STRICT",
                    "master":decision.get("master",{}),
                    "hybrid":decision.get("hybrid",{}),"council":council,
                    "risk_guardian":risk_guardian(item.get("account") or {},item.get("positions") or [],
                        risk_limits_from_settings(settings))})
            except PermissionError as e:return self.send_json(403,{"error":str(e)})
            except Exception as e:return self.send_json(502,{"error":str(e)[:180]})
        if path=="/api/connection/status":
            try:
                u=current_user(token_of(self))
                if (u.get("email") or "").lower()!=OWNER_EMAIL:
                    lic=license_for_user(u["id"])
                    client_id=lic.get("client_id") if lic else ""
                    item=CONNECTIONS.get(client_id) if client_id else None
                else:
                    item=CONNECTIONS.get((license_for_user(u["id"]) or {}).get("client_id",""))
                
                connected=bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30)
                if (u.get("email") or "").lower()!=OWNER_EMAIL:
                    a=(item or {}).get("account") or {}
                    safe_account={key:a.get(key) for key in
                                  ("login","server","balance","equity","profit","free_margin","currency")}
                    status={"account":safe_account,"symbol":(item or {}).get("symbol"),
                            "timeframe":(item or {}).get("timeframe"),
                            "last_seen":(item or {}).get("last_seen")}
                    return self.send_json(200,{"connected":connected,"connection":status if item else None})
                return self.send_json(200,{"connected":connected,
                                           "connection":({k:v for k,v in item.items() if k!="received_at"} if item else None)})
            except Exception as e: return self.send_json(403,{"error":str(e)[:180]})
        if not public_static(path):
            return self.send_json(404,{"error":"not_found"})
        return super().do_GET()
    def do_POST(self):
        try:
            data=self.read_json(); token=token_of(self)
            path=urllib.parse.urlparse(self.path).path
            if path in OWNER_WRITE_ENDPOINTS:
                owner(token)
            if path=="/api/ai/arena/action-training":
                u=owner(token)
                from model_arena_api import ids
                cid=data.get("client")
                if cid not in ids(sb,u["id"],ENGINE_MARKET):return self.send_json(403,{"error":"invalid owner client"})
                report={}
                for key,row in (data.get("reports") or {}).items():
                    if key not in tuple(a+":"+s for a in arena_master.ACTIONS for s in ("BUY","SELL")) or not isinstance(row,dict):continue
                    report[key]={k:arena_master.num(row.get(k)) for k in ("train","test","signals","positive","net_proxy")}
                queue_ai_diagnostic_event({"event_id":"action-ai:"+hashlib.sha256((cid+str(int(time.time()//3600))).encode()).hexdigest(),
                    "client_id":cid,"event_type":"ARENA_ACTION_TRAINING","payload":{"market":ENGINE_MARKET,"candles":int(arena_master.num(data.get("candles"))),"reports":report,"metric":"HISTORICAL_PATH_PROXY_NOT_BASKET_PROFIT","execution_authorized":False}})
                return self.send_json(200,{"accepted":True})
            if path=="/api/ai/arena/parallel-monitor":
                u=owner(token)
                from model_arena_api import ids
                from parallel_shadow import accept
                cid=data.get("client")
                if cid not in ids(sb,u["id"],ENGINE_MARKET):return self.send_json(403,{"error":"invalid owner client"})
                return self.send_json(200,accept(u["id"],ENGINE_MARKET,arena_master.context(cid,CONNECTIONS.get(cid) or {}),data))
            if path=="/api/ai/arena/live-forecast":
                u=owner(token)
                from model_arena_api import ids
                cid=data.get("client")
                if cid not in ids(sb,u["id"],ENGINE_MARKET):return self.send_json(403,{"error":"invalid owner client"})
                item=CONNECTIONS.get(cid) or {}
                result=arena_master.accept(cid,item,data)
                if result.get("accepted"):
                    evidence=arena_master.evidence(cid,item.get("strategy_decision") or {})
                    digest=hashlib.sha256(json.dumps({"models":evidence.get("models"),"monitor_models":evidence.get("monitor_models"),"actions":evidence.get("actions")},sort_keys=True).encode()).hexdigest()[:16]
                    queue_ai_diagnostic_event({"event_id":"arena-live:"+hashlib.sha256((cid+str(data.get("bar_time"))+digest).encode()).hexdigest(),
                        "client_id":cid,"event_type":"ARENA_LIVE_FORECAST","payload":{**evidence,"market":ENGINE_MARKET}})
                return self.send_json(200,result)
            if path=="/api/ai/arena/report":
                u=owner(token)
                import model_arena_api as arena
                return self.send_json(200,arena.save(sb,u["id"],ENGINE_MARKET,data))
            if path.startswith("/v1/terminal-action-claim/"):
                client_id=path.rsplit("/",1)[-1]
                if not validate_license(client_id,token):
                    return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
                item=CONNECTIONS.get(client_id)
                if not item:return self.send_json(409,{"execution_authorized":False,"reason_ar":"MT5 غير متصل"})
                req_symbol=str(data.get("symbol") or "")
                if not req_symbol or not same_market_symbol(req_symbol,item.get("symbol")):
                    return self.send_json(409,{"execution_authorized":False,"reason_ar":"رمز الشارت غير مطابق؛ رفض المطالبة"})
                decision=item.get("strategy_decision") or {}
                settings=get_trade_settings(client_id) or {}
                now=datetime.now(timezone.utc)
                mode=entry_council_mode(settings,terminal_is_owner(client_id))
                advisory_mode=mode!="STRICT"
                council=council_result(client_id,decision)
                if advisory_mode: decision=resolve_owner_decision(client_id,decision,council,mode)
                gate=evaluate_entry_gate(decision,item.get("account") or {},item.get("symbol",""),
                    item.get("received_at"),now,bool(settings.get("trading_enabled")),
                    local_ai_advice=(dict(item["local_ai_advice"],
                        age_seconds=now.timestamp()-finite_number(item["local_ai_advice"].get("received_at_unix",0)))
                        if isinstance(item.get("local_ai_advice"),dict) else None),
                    require_local_ai=LOCAL_AI_REQUIRED and not advisory_mode,
                    strategy_first=advisory_mode)
                risk=risk_guardian(item.get("account") or {},item.get("positions") or [],risk_limits_from_settings(settings))
                response=claim_central_action(client_id,data.get("authorization_id"),
                    item,decision,gate,council,risk,settings,sb,now,
                    council_advisory=advisory_mode)
                return self.send_json(200 if response.get("execution_authorized") else 409,{**response,"symbol":item.get("symbol","")})
            if path.startswith(("/v1/local-ai/advice/","/v1/local-ai/shadow-v4/","/v1/local-ai/unified-v4/")):
                shadow_route=path.startswith("/v1/local-ai/shadow-v4/")
                unified_route=path.startswith("/v1/local-ai/unified-v4/")
                expected_protocol=(accuracy_v4.PROTOCOL if shadow_route else
                    accuracy_v4.UNIFIED_PROTOCOL if unified_route else None)
                if (data.get("analysis_protocol") or None)!=expected_protocol:
                    return self.send_json(400,{"error":"Invalid V4 route or protocol","execution_authorized":False})
                client_id=path.rsplit("/",1)[-1]
                if not validate_license(client_id,token):
                    return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
                if not terminal_is_owner(client_id):
                    return self.send_json(403,{"error":"تصويت النماذج المحلية للمالك فقط"})
                item=CONNECTIONS.get(client_id)
                if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                    return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة"})
                decision=item.get("strategy_decision") or {}
                if DUAL_MARKET_MODE and (ENGINE_MARKET=="XAUUSD" or data.get("symbol")) and not same_market_symbol(data.get("symbol"),item.get("symbol")):
                    return self.send_json(409,{"error":"تصويت النموذج لا يطابق رمز السوق الحالي"})
                bar_time=int(finite_number(data.get("bar_time",0)))
                direction=str(data.get("direction","WAIT")).upper()
                confidence=finite_number(data.get("confidence",-1),-1)
                model=str(data.get("model",""))[:80]
                reason=str(data.get("reason_ar",""))[:500]
                original_reason=str(data.get("reason_original",""))[:350]
                current_bar=int(finite_number(decision.get("bar_time",0)))
                if bar_time<=0 or bar_time!=current_bar:
                    return self.send_json(409,{"error":"تصويت النموذج لا يخص الشمعة الحالية"})
                if not closed_bar_execution_fresh(current_bar,datetime.now(timezone.utc),
                                                   item.get("timeframe","M1")):
                    return self.send_json(409,{"error":"انتهت صلاحية الشمعة الحالية للتنفيذ"})
                if direction not in ("BUY","SELL","WAIT") or confidence<0 or confidence>1:
                    return self.send_json(400,{"error":"صيغة تصويت النموذج غير صالحة"})
                if model not in ("qwen3.5:4b","qwen2.5:1.5b","deepseek-r1:1.5b"):
                    return self.send_json(400,{"error":"موديل AI غير معتمد لهذا الربط"})
                # Independent shadow votes never overwrite the live council.
                if unified_route and (not same_market_symbol(data.get("symbol"),item.get("symbol"))
                    or str(item.get("timeframe","")).upper() not in ("M1","PERIOD_M1")
                    or model not in ("qwen2.5:1.5b","deepseek-r1:1.5b")):
                    return self.send_json(409,{"error":"Unified V4 requires matching M1 market and supported local model"})
                if shadow_route:
                    if not same_market_symbol(data.get("symbol"),item.get("symbol")):
                        return self.send_json(409,{"error":"V4 market mismatch"})
                    event=accuracy_v4.record(client_id,item.get("symbol"),item.get("timeframe"),
                        decision,item.get("tick") or {},"LOCAL_AI",direction,confidence,time.time(),model)
                    if event:queue_ai_diagnostic_event(event)
                    return self.send_json(200,{"accepted":bool(event),"shadow_only":True,
                        "analysis_protocol":accuracy_v4.PROTOCOL,"execution_authorized":False,
                        "bar_time":bar_time,"direction":direction,
                        "reason_ar":"V4: تقييم مستقل فقط؛ لا يغير قرارات التداول"})
                advice={"bar_time":bar_time,"direction":direction,
                    "confidence":confidence,"model":model,"reason_ar":reason,
                    "received_at_unix":time.time()}
                if original_reason and model=="qwen2.5:1.5b":
                    advice["reason_original"]=original_reason
                if not accept_local_model_vote(client_id,advice,decision):
                    return self.send_json(409,{"error":"مجلس النماذج غير جاهز لهذه الشمعة"})
                if model in ("qwen3.5:4b","qwen2.5:1.5b"):
                    item["local_ai_advice"]=advice
                    event=build_qwen_vote_event(client_id,advice,decision)
                else:
                    event={"event_id":f"deepseek:{client_id}:{bar_time}","client_id":client_id,
                        "event_type":"AI_DEEPSEEK_VOTE","payload":advice}
                diagnostic_queued=queue_ai_diagnostic_event(event) if event else False
                # Reuse the exact accepted prediction for forward measurement.
                # No second model call, no order authorization, no changed gates.
                accuracy_recorded=False
                if unified_route:
                    try:
                        forecast=accuracy_v4.record(client_id,item.get("symbol"),item.get("timeframe"),
                            decision,item.get("tick") or {},"LOCAL_AI",direction,confidence,time.time(),
                            model,analysis_mode="UNIFIED")
                        if forecast:accuracy_recorded=queue_ai_diagnostic_event(forecast)
                    except Exception as exc:
                        print("UNIFIED_AI_AUDIT_WARNING:",type(exc).__name__,flush=True)
                # Re-evaluate as soon as either late vote arrives. MT5 still
                # independently polls /terminal-decision and must pass all gates.
                updated_council=council_result(client_id,decision)
                # The first terminal poll may record a temporary veto before
                # either local vote is received. Persist the revised council
                # reason after each real vote so the owner never sees only a
                # stale "NOT_CONFIGURED" outcome in the opportunity history.
                council_votes=updated_council.get("votes") or {}
                all_voted=all((council_votes.get(name) or {}).get("status")=="READY"
                              for name in ("Fast AI","Qwen","DeepSeek"))
                if not updated_council.get("approved"):
                    queue_ai_diagnostic_event({
                        "event_id":f"council-post-vote:{client_id}:{bar_time}:{model}",
                        "client_id":client_id,
                        "event_type":"AI_COUNCIL_VETO" if all_voted else "AI_COUNCIL_WAIT",
                        "payload":{"bar_time":bar_time,"outcome":"WAIT",
                                   "central_direction":decision.get("decision","WAIT"),
                                   "model":model,"approved":False,
                                   "confidence":finite_number(decision.get("confidence")),
                                   "reason_ar":updated_council.get("reason_ar",""),
                                   "stage":"AFTER_LOCAL_VOTE"}})
                if updated_council.get("approved"):
                    queue_ai_diagnostic_event({
                        "event_id":f"council-votes-ready:{client_id}:{bar_time}",
                        "client_id":client_id,
                        "event_type":"AI_COUNCIL_VOTES_READY",
                        "payload":{"bar_time":bar_time,"direction":direction,
                                   "central_direction":decision.get("decision","WAIT"),
                                   "reason_ar":updated_council.get("reason_ar",""),
                                   "execution_authorized":False}})
                return self.send_json(200,{"accepted":True,"diagnostic_queued":diagnostic_queued,
                    "unified_v4":unified_route,"accuracy_recorded":accuracy_recorded,
                    "bar_time":bar_time,"direction":direction,"model":model,
                    "council_approved":bool(updated_council.get("approved")),
                    "council_reason_ar":updated_council.get("reason_ar",""),
                    "execution_authorized":False})
            if path.startswith("/v1/terminal-recovery-events/"):
                client_id=path.rsplit("/",1)[-1]
                if not validate_license(client_id,token): return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
                event_type=str(data.get("event_type") or "")
                allowed=("RECOVERY_ARMED","RECOVERY_WAITING","RECOVERY_OPENED","RECOVERY_BLOCKED","RECOVERY_REJECTED","RECOVERY_CLOSED","RECOVERY_CLOSE_RETRY")
                if event_type not in allowed: return self.send_json(400,{"error":"نوع حدث التعافي غير مسموح"})
                event_key=str(data.get("event_id") or "").strip()
                payload=data.get("payload") if isinstance(data.get("payload"),dict) else None
                if not event_key or len(event_key)>160 or payload is None: return self.send_json(400,{"error":"بيانات حدث التعافي غير مكتملة"})
                if len(json.dumps(payload,ensure_ascii=False))>40000: return self.send_json(413,{"error":"تفاصيل حدث التعافي أكبر من الحد المسموح"})
                row={"event_id":("mt5:"+client_id+":"+event_key)[:240],"client_id":client_id,"event_type":event_type,"payload":payload}
                sb("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",row,admin=True,prefer="resolution=ignore-duplicates,return=minimal")
                return self.send_json(200,{"saved":True,"event_type":event_type})
            if path.startswith("/v1/terminal-history/batch/"):
                client_id=path.rsplit("/",1)[-1]
                if not validate_license(client_id,token): return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
                symbol=str(data.get("symbol") or "")[:40]
                timeframe=str(data.get("timeframe") or "")[:20]
                if not btc_market(symbol):
                    return self.send_json(409,{"accepted":False,"error":"رمز الشموع لا يطابق مسار التداول "+ENGINE_MARKET})
                if not symbol or not timeframe: return self.send_json(400,{"error":"رمز السوق أو الفريم غير صالح"})
                metadata=data.get("metadata") if isinstance(data.get("metadata"),dict) else {}
                candles=clean_historical_candles(data.get("candles"),metadata,client_id,symbol,timeframe)
                if not candles: return self.send_json(400,{"error":"لا توجد شموع تاريخية صالحة في الدفعة"})
                saved=0
                for start in range(0,len(candles),500):
                    batch=candles[start:start+500]
                    sb("POST","/rest/v1/alzool_engine_market_candles?on_conflict=client_id,symbol,timeframe,candle_time",
                       batch,admin=True,prefer="resolution=merge-duplicates,return=minimal")
                    saved+=len(batch)
                return self.send_json(200,{"accepted":True,"candles_saved":saved,"symbol":symbol,"timeframe":timeframe})
            if path=="/api/client/basic-settings":
                u=current_user(token)
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    return self.send_json(403,{"error":"إعدادات العميل فقط"})
                if banned(admin_user(u["id"])):
                    return self.send_json(403,{"error":"الحساب موقوف"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"):
                    return self.send_json(403,{"error":"اتصال العميل غير مرخص"})
                try:
                    if datetime.fromisoformat(str(lic["expires_at"]).replace("Z","+00:00"))<=datetime.now(timezone.utc):
                        return self.send_json(403,{"error":"انتهت صلاحية الاتصال"})
                except (KeyError,ValueError,TypeError):
                    return self.send_json(403,{"error":"سجل الترخيص غير صالح"})
                allowed={"single_enabled","single_profit_target","group_enabled",
                         "group_profit_target","stop_loss_enabled","stop_loss_mode","stop_loss_value"}
                if not isinstance(data,dict) or not data or set(data)-allowed:
                    return self.send_json(400,{"error":"لا يسمح بتعديل إعدادات AI أو الاستراتيجيات أو أي إعدادات متقدمة"})
                query="/rest/v1/alzool_btc_trade_settings?client_id=eq."+urllib.parse.quote(lic["client_id"],safe="")
                stored=sb("GET",query+"&select=single_enabled,single_profit_target,group_enabled,group_profit_target,stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management&limit=1",admin=True)
                prior=stored[0] if stored else get_trade_settings(lic["client_id"]) or {}
                fields=dict(basic_settings(prior))
                fields.update(data)
                for key in ("single_enabled","group_enabled","stop_loss_enabled"):
                    if not isinstance(fields.get(key),bool):
                        return self.send_json(400,{"error":"قيمة التفعيل غير صحيحة"})
                for key in ("single_profit_target","group_profit_target","stop_loss_value"):
                    value=fields.get(key)
                    if isinstance(value,bool) or not isinstance(value,(int,float)):
                        return self.send_json(400,{"error":"القيم يجب أن تكون أرقاماً إنجليزية موجبة"})
                    value=float(value)
                    if not math.isfinite(value) or value<=0 or value>100000:
                        return self.send_json(400,{"error":"قيمة الهدف أو وقف الخسارة خارج الحد"})
                    fields[key]=value
                if fields.get("stop_loss_mode") not in ("ATR","POINTS"):
                    return self.send_json(400,{"error":"نوع وقف الخسارة غير صحيح"})
                # Do not copy any client-provided position-management fields.
                # Preserve owner's strategy/AI/risk settings exactly as stored.
                pm=stored[0].get("position_management") if stored else prior.get("position_management")
                row={**fields,"client_id":lic["client_id"],
                     "position_management":pm if isinstance(pm,dict) else {},
                     "updated_at":datetime.now(timezone.utc).isoformat()}
                sb("POST","/rest/v1/alzool_btc_trade_settings?on_conflict=client_id",
                   row,admin=True,prefer="resolution=merge-duplicates,return=representation")
                TRADE_SETTINGS_CACHE.pop(lic["client_id"],None)
                return self.send_json(200,{"saved":True,"settings":basic_settings(row),
                    "advanced_settings_unchanged":True})
            if path=="/api/auto-trading":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                if not lic or lic.get("revoked"): return self.send_json(409,{"error":"لا يوجد ترخيص تداول مرتبط بهذا الحساب"})
                enabled=data.get("enabled")
                if not isinstance(enabled,bool): return self.send_json(400,{"error":"قيمة التفعيل غير صحيحة"})
                if enabled:
                    live=CONNECTIONS.get(lic["client_id"])
                    if not live or (datetime.now(timezone.utc)-live["received_at"]).total_seconds()>30:
                        return self.send_json(409,{"error":"لا يمكن تفعيل التداول قبل اتصال MT5 واستلام بيانات حديثة"})
                changed=sb("PATCH","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(lic["client_id"],safe=""),
                    {"trading_enabled":enabled},admin=True,prefer="return=representation")
                if not changed: return self.send_json(409,{"error":"تعذر حفظ حالة التداول الآلي"})
                cached=TRADE_SETTINGS_CACHE.get(lic["client_id"])
                if cached:
                    updated=dict(cached["settings"]); updated["trading_enabled"]=enabled
                    _store_trade_settings(lic["client_id"],updated)
                return self.send_json(200,{"saved":True,"trading_enabled":enabled,"client_id":lic["client_id"],
                    "message":"تم تفعيل التداول الآلي" if enabled else "تم إيقاف فتح صفقات جديدة"})
            if path=="/api/trade-management/settings":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                client_id,item=connection_for_authenticated_user(u)
                if not client_id: return self.send_json(409,{"error":"لا يوجد اتصال MT5 مرخّص لهذا الحساب"})
                single_enabled=data.get("single_enabled") is True
                group_enabled=data.get("group_enabled") is True
                single_target=finite_number(data.get("single_profit_target"),0)
                group_target=finite_number(data.get("group_profit_target"),0)
                if not math.isfinite(single_target) or not math.isfinite(group_target) or single_target<=0 or group_target<=0:
                    return self.send_json(400,{"error":"أهداف الربح يجب أن تكون أرقامًا موجبة وبعملة الحساب"})
                if single_target>100000000 or group_target>100000000:
                    return self.send_json(400,{"error":"قيمة هدف الربح أكبر من الحد المسموح"})
                existing=sb("GET","/rest/v1/alzool_btc_trade_settings?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management&limit=1",admin=True)
                prior=existing[0] if existing else {"stop_loss_enabled":False,"stop_loss_mode":"ATR","stop_loss_value":1.8}
                prior_pm=prior.get("position_management") if isinstance(prior.get("position_management"),dict) else {}
                stop_loss_user_configured=(data.get("stop_loss_user_configured") is True or
                    prior_pm.get("stop_loss_user_configured") is True)
                stop_loss_enabled=(data.get("stop_loss_enabled",prior.get("stop_loss_enabled")) is True
                    if stop_loss_user_configured else False)
                stop_loss_mode=str(data.get("stop_loss_mode",prior.get("stop_loss_mode","ATR"))).upper()
                stop_loss_value=finite_number(data.get("stop_loss_value",prior.get("stop_loss_value",1.8)),0)
                if stop_loss_mode not in ("POINTS","ATR"):
                    return self.send_json(400,{"error":"نوع وقف الخسارة غير صالح"})
                if not math.isfinite(stop_loss_value) or stop_loss_value<=0 or stop_loss_value>1000000:
                    return self.send_json(400,{"error":"قيمة وقف الخسارة يجب أن تكون أكبر من صفر"})
                pm=data.get("position_management",prior_pm)
                if not isinstance(pm,dict):
                    return self.send_json(400,{"error":"إعدادات التعزيز والتبريد غير صالحة"})
                mode=str(pm.get("mode",prior_pm.get("mode","AI"))).upper()
                if mode not in ("AI","MANUAL"):
                    return self.send_json(400,{"error":"اختر وضع AI أو يدوي"})
                clean_pm={"mode":mode,
                          "entry_council_mode":(prior_pm.get("entry_council_mode","ADVISORY") if prior_pm.get("entry_council_mode") in ("STRICT","ADVISORY","MASTER") else "ADVISORY"),
                          "entry_council_mode_user_selected":prior_pm.get("entry_council_mode_user_selected") is True,
                          "stop_loss_user_configured":stop_loss_user_configured,
                          "optional_limits_user_selected":(
                             pm.get("optional_limits_user_selected") is True or
                             prior_pm.get("optional_limits_user_selected") is True)}
                profit_mode=data.get("profit_close_mode",prior_pm.get("profit_close_mode","FIXED"))
                if profit_mode not in ("FIXED","SMART"):
                    return self.send_json(400,{"error":"اختر جني ربح ثابت أو ذكي"})
                if "profit_close_mode" in data and profit_mode=="SMART":
                    if (not item or item.get("smart_profit_capable") is not True or
                        not 0<=(datetime.now(timezone.utc)-item["received_at"]).total_seconds()<=30):
                        return self.send_json(409,{"error":"جني الربح الذكي يحتاج EA V3.03 متصلاً الآن"})
                    if prior_pm.get("entry_council_mode")!="MASTER":
                        return self.send_json(400,{"error":"اختر MASTER AI أولاً لتفعيل جني الربح الذكي"})
                    if data.get("confirm_smart_profit") is not True:
                        return self.send_json(400,{"error":"أكد اختيار جني الربح الذكي"})
                clean_pm["profit_close_mode"]=profit_mode
                entry_guard_enabled=pm.get("entry_guard_enabled",prior_pm.get("entry_guard_enabled",True)) is True
                entry_max_delay_bars=int(finite_number(pm.get("entry_max_delay_bars",prior_pm.get("entry_max_delay_bars",1)),0))
                entry_max_deviation_atr=finite_number(pm.get("entry_max_deviation_atr",prior_pm.get("entry_max_deviation_atr",0.5)),0)
                if not 1<=entry_max_delay_bars<=10 or not 0.05<=entry_max_deviation_atr<=20:
                    return self.send_json(400,{"error":"تحقق من صلاحية حد تأخير الدخول ومسافة السعر حسب ATR"})
                clean_pm["entry_guard_enabled"]=entry_guard_enabled
                clean_pm["entry_max_delay_bars"]=entry_max_delay_bars
                clean_pm["entry_max_deviation_atr"]=entry_max_deviation_atr
                recovery_enabled=pm.get("recovery_enabled",prior_pm.get("recovery_enabled",False)) is True
                recovery_loss_trigger=finite_number(pm.get("recovery_loss_trigger",prior_pm.get("recovery_loss_trigger",0.0)),0.0)
                if not math.isfinite(recovery_loss_trigger) or recovery_loss_trigger<0 or recovery_loss_trigger>100000000:
                    return self.send_json(400,{"error":"حد تفعيل التعافي يجب أن يكون صفرًا أو مبلغًا موجبًا بعملة الحساب"})
                clean_pm["recovery_enabled"]=recovery_enabled
                clean_pm["recovery_loss_trigger"]=recovery_loss_trigger
                raw_profit_target=pm.get("recovery_profit_target",prior_pm.get("recovery_profit_target",0.0))
                try:
                    recovery_profit_target=float(raw_profit_target)
                except (TypeError,ValueError,OverflowError):
                    return self.send_json(400,{"error":"هدف ربح التعافي يجب أن يكون مبلغًا رقميًا بعملة الحساب"})
                if not math.isfinite(recovery_profit_target) or recovery_profit_target<0:
                    return self.send_json(400,{"error":"هدف ربح التعافي لا يمكن أن يكون مبلغًا سالبًا"})
                clean_pm["recovery_profit_target"]=recovery_profit_target
                clean_pm["recovery_user_configured"]=pm.get("recovery_user_configured",prior_pm.get("recovery_user_configured",False)) is True
                for prefix in ("addon","cooling"):
                    enabled=pm.get(prefix+"_enabled",prior_pm.get(prefix+"_enabled",False)) is True
                    maximum=int(finite_number(pm.get(prefix+"_max",prior_pm.get(prefix+"_max",0)),-1))
                    lot=finite_number(pm.get(prefix+"_lot",prior_pm.get(prefix+"_lot",0.01)),0)
                    distance=finite_number(pm.get(prefix+"_distance_points",prior_pm.get(prefix+"_distance_points",0)),0)
                    if maximum<0 or maximum>100 or lot<=0 or lot>100 or distance<0 or distance>100000000:
                        return self.send_json(400,{"error":"تحقق من الحد الأقصى واللوت والمسافة في إعدادات التعزيز والتبريد"})
                    if mode=="MANUAL" and enabled and distance<=0:
                        return self.send_json(400,{"error":"في الوضع اليدوي يجب تحديد مسافة أكبر من صفر بالنقاط"})
                    multiplier=finite_number(pm.get(prefix+"_multiplier",prior_pm.get(prefix+"_multiplier",1.5 if prefix=="addon" else 1.2)),0)
                    raw_max_lot=(pm[prefix+"_max_lot"] if prefix+"_max_lot" in pm else
                        prior_pm.get(prefix+"_max_lot") if prior_pm.get("optional_limits_user_selected") else None)
                    max_lot=(finite_number(raw_max_lot,-1) if raw_max_lot is not None and
                        str(raw_max_lot).strip()!="" else None)
                    if not 1.0<=multiplier<=3.0 or (max_lot is not None and max_lot<=0):
                        return self.send_json(400,{"error":"قيمة مضاعف أو سقف لوت غير صحيحة"})
                    clean_pm[prefix+"_enabled"]=enabled
                    clean_pm[prefix+"_max"]=maximum
                    clean_pm[prefix+"_lot"]=lot
                    clean_pm[prefix+"_distance_points"]=distance
                    clean_pm[prefix+"_multiplier"]=multiplier
                    if max_lot is not None:
                        clean_pm[prefix+"_max_lot"]=max_lot
                raw_total_lots=(pm.get("max_total_lots") if "max_total_lots" in pm else
                    prior_pm.get("max_total_lots") if prior_pm.get("optional_limits_user_selected") else None)
                if raw_total_lots is not None and str(raw_total_lots).strip()!="":
                    max_total_lots=finite_number(raw_total_lots,-1)
                    if max_total_lots<0:
                        return self.send_json(400,{"error":"سقف إجمالي اللوتات غير صالح"})
                    clean_pm["max_total_lots"]=max_total_lots
                # Preserve security-critical settings across ordinary form saves.
                # Never allow a frontend to silently enable sensitive actions.
                clean_pm["central_actions_enabled"]=prior_pm.get("central_actions_enabled") is True
                # Keep only owner-selected optional limits; do not manufacture caps.
                for key in ("max_drawdown_pct","max_positions","max_positions_per_direction","recovery_max_lot"):
                    raw=(pm[key] if key in pm else prior_pm.get(key)
                         if prior_pm.get("optional_limits_user_selected") else None)
                    if raw is None or str(raw).strip()=="":
                        continue
                    value=finite_number(raw,-1)
                    if value<0:
                        return self.send_json(400,{"error":"قيمة "+key+" غير صالحة"})
                    clean_pm[key]=int(value) if key in ("max_positions","max_positions_per_direction") else value
                row={"client_id":client_id,"single_enabled":single_enabled,
                     "single_profit_target":single_target,"group_enabled":group_enabled,
                     "group_profit_target":group_target,"stop_loss_enabled":stop_loss_enabled,
                     "stop_loss_mode":stop_loss_mode,"stop_loss_value":stop_loss_value,
                     "position_management":clean_pm,
                     "updated_at":datetime.now(timezone.utc).isoformat()}
                saved=sb("POST","/rest/v1/alzool_btc_trade_settings?on_conflict=client_id",row,admin=True,
                         prefer="resolution=merge-duplicates,return=representation")
                current=TRADE_SETTINGS_CACHE.get(client_id)
                cache_row=dict(row)
                cache_row["trading_enabled"]=bool(current and current["settings"].get("trading_enabled",False))
                _store_trade_settings(client_id,cache_row)
                return self.send_json(200,{"saved":True,"settings":saved[0] if isinstance(saved,list) and saved else row})
            if path=="/api/backtest/learning":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                # Never select the latest customer's candles or learning run
                # for an owner. The authenticated identity owns exactly one ID.
                lic=license_for_user(u["id"])
                client_id=lic.get("client_id","") if lic and not lic.get("revoked") else ""
                if not client_id: return self.send_json(409,{"error":"لا يوجد ترخيص مرتبط لحفظ ذاكرة التعلم"})
                run=data.get("run") if isinstance(data.get("run"),dict) else {}
                run_key=str(run.get("run_key") or "")[:80]
                symbol=str(run.get("symbol") or "")[:40]
                timeframe=str(run.get("timeframe") or "")[:20]
                strategies=run.get("strategy_results") if isinstance(run.get("strategy_results"),list) else []
                combinations=run.get("combination_results") if isinstance(run.get("combination_results"),list) else []
                if not btc_market(symbol):
                    return self.send_json(400,{"error":"رمز الاختبار لا يطابق السوق "+ENGINE_MARKET})
                if not run_key or not symbol or not timeframe or len(strategies)!=200 or len(combinations)!=1000:
                    return self.send_json(400,{"error":"بيانات جولة التعلم غير مكتملة؛ يلزم 200 استراتيجية و1000 تركيبة"})
                if len(json.dumps(run,ensure_ascii=False))>1800000:
                    return self.send_json(413,{"error":"حجم سجل التعلم أكبر من الحد المسموح"})
                row={"client_id":client_id,"run_key":run_key,"symbol":symbol,"timeframe":timeframe,
                    "period_days":int(finite_number(run.get("period_days"),0)),
                    "candle_count":int(finite_number(run.get("candle_count"),0)),
                    "test_points":int(finite_number(run.get("test_points"),0)),
                    "validation_points":int(finite_number(run.get("validation_points"),0)),
                    "lot_size":finite_number(run.get("lot_size"),0.01),
                    "commission_per_lot":finite_number(run.get("commission_per_lot"),0),
                    "slippage_points":finite_number(run.get("slippage_points"),0),
                    "account_currency":str(run.get("account_currency") or "")[:12],
                    "summary":run.get("summary") if isinstance(run.get("summary"),dict) else {},
                    "strategy_results":strategies,"combination_results":combinations,
                    "diagnostics":run.get("diagnostics") if isinstance(run.get("diagnostics"),dict) else {}}
                saved=sb("POST","/rest/v1/alzool_engine_learning_runs?on_conflict=client_id,run_key",row,admin=True,prefer="resolution=merge-duplicates,return=representation")
                history_state=btc_learning_snapshot(client_id,symbol,timeframe,sb,refresh=True)
                return self.send_json(200,{"saved":True,"run":saved[0] if isinstance(saved,list) and saved else {"run_key":run_key},
                    "server_learning":{"status":history_state.get("status"),"refreshing":history_state.get("refreshing",False),
                        "samples":history_state.get("samples",0),"method":"BTC_SERVER_REPLAY_V1"}})
            if path=="/api/opportunities/events":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                # Do not fall back to another user's latest connection.
                if not client_id: return self.send_json(409,{"error":"لا يوجد اتصال MT5 مرخّص لهذا الحساب"})
                event_type=str(data.get("event_type") or "")
                if event_type not in ("SIGNAL_CREATED","WAIT"):
                    return self.send_json(400,{"error":"نوع حدث الفرصة غير مسموح"})
                event_key=str(data.get("event_id") or "").strip()
                payload=data.get("payload") if isinstance(data.get("payload"),dict) else None
                if not event_key or len(event_key)>160 or payload is None:
                    return self.send_json(400,{"error":"بيانات الفرصة غير مكتملة"})
                if len(json.dumps(payload,ensure_ascii=False))>40000:
                    return self.send_json(413,{"error":"تفاصيل الفرصة أكبر من الحد المسموح"})
                row={"event_id":("web:"+client_id+":"+event_key)[:240],"client_id":client_id,
                     "event_type":event_type,"payload":payload}
                sb("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",row,admin=True,
                   prefer="resolution=ignore-duplicates,return=minimal")
                return self.send_json(200,{"saved":True,"event_id":row["event_id"]})
            if path=="/api/ai/actions/control":
                u=owner(token)
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"حساب المالك غير مرتبط بالمحرك"})
                enabled=data.get("enabled")
                if not isinstance(enabled,bool):
                    return self.send_json(400,{"error":"قيمة التفعيل غير صالحة"})
                if enabled and data.get("confirmed_risk") is not True:
                    return self.send_json(400,{"error":"يلزم تأكيد المخاطر لتفعيل الإدارة المركزية"})
                cid=urllib.parse.quote(identity["client_id"],safe="")
                rows=sb("GET","/rest/v1/alzool_btc_trade_settings?client_id=eq."+cid+
                    "&select=position_management&limit=1",admin=True)
                pm=dict((rows[0] if rows else {}).get("position_management") or {})
                pm["central_actions_enabled"]=enabled
                if rows:
                    changed=sb("PATCH","/rest/v1/alzool_btc_trade_settings?client_id=eq."+cid,
                        {"position_management":pm,"updated_at":datetime.now(timezone.utc).isoformat()},
                        admin=True,prefer="return=representation")
                else:
                    changed=sb("POST","/rest/v1/alzool_btc_trade_settings",
                        {"client_id":identity["client_id"],"position_management":pm},
                        admin=True,prefer="return=representation")
                if not changed:return self.send_json(502,{"error":"لم يتم حفظ مفتاح الإدارة"})
                TRADE_SETTINGS_CACHE.pop(identity["client_id"],None)
                return self.send_json(200,{"enabled":enabled,
                    "message":"تم حفظ مفتاح الإدارة؛ لا يعمل دون تفعيل منفصل في MT5 وموافقة المجلس"})
            if path=="/api/owner/entry-policy":
                u=owner(token)
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"حساب المالك غير مرتبط بمحرك MT5"})
                requested=str(data.get("mode","")).upper()
                if requested not in ("STRICT","ADVISORY","MASTER"):
                    return self.send_json(400,{"error":"وضع الدخول غير معروف"})
                if requested in ("ADVISORY","MASTER") and data.get("confirmed_risk") is not True:
                    return self.send_json(400,{"error":"يجب تأكيد مخاطر التنفيذ التلقائي أولاً"})
                cid=urllib.parse.quote(identity["client_id"],safe="")
                rows=sb("GET","/rest/v1/alzool_btc_trade_settings?client_id=eq."+cid+
                    "&select=position_management,stop_loss_enabled&limit=1",admin=True)
                prior=rows[0] if rows else {}
                pm=dict(prior.get("position_management") or {})
                pm["entry_council_mode"]=requested
                pm["entry_council_mode_user_selected"]=True
                if rows:
                    changed=sb("PATCH","/rest/v1/alzool_btc_trade_settings?client_id=eq."+cid,
                        {"position_management":pm,"updated_at":datetime.now(timezone.utc).isoformat()},
                        admin=True,prefer="return=representation")
                else:
                    changed=sb("POST","/rest/v1/alzool_btc_trade_settings",
                        {"client_id":identity["client_id"],"position_management":pm},
                        admin=True,prefer="return=representation")
                if not changed:return self.send_json(502,{"error":"تعذر حفظ سياسة الدخول"})
                with CACHE_LOCK:TRADE_SETTINGS_CACHE.pop(identity["client_id"],None)
                queue_ai_diagnostic_event({
                    "event_id":f"entry-policy:{identity['client_id']}:{time.time_ns()}",
                    "client_id":identity["client_id"],
                    "event_type":"ENTRY_POLICY_CHANGED",
                    "payload":{"mode":requested,"actor":"owner","executed_orders":False}})
                warning=(" | تنبيه: وقف الخسارة معطّل في إعداداتك الحالية"
                         if prior.get("stop_loss_enabled") is False else "")
                return self.send_json(200,{"mode":requested,
                    "message":("تم تفعيل Hybrid AI — أصوات Fast AI وQwen وDeepSeek الحديثة تشارك في ترجيح BUY وSELL دون اشتراط الإجماع"
                               if requested=="ADVISORY" else "تم تفعيل MASTER AI — قرار موحد بلا إجماع إجباري" if requested=="MASTER" else "تم تفعيل المجلس الصارم — إجماع 3 نماذج")+warning})
            if path=="/api/ai/versions/test":
                return self.send_json(409,{"error":"اختبار إصدارات الذهب غير متاح في محرك BTC؛ استخدم بيانات BTC وخزنه المستقل"})
                u=owner(token)
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"لا يوجد معرّف تخزين للمالك"})
                client_id=identity["client_id"]
                item=CONNECTIONS.get(client_id) or {}
                symbol=str(item.get("symbol") or "XAUUSD")[:40]
                timeframe=str(item.get("timeframe") or "M1")[:20]
                if not symbol.upper().startswith("XAUUSD"):return self.send_json(409,{"error":"اختبار الإصدارات مخصص للذهب إلى حين فصل مخزن الإصدارات حسب الرمز"})
                q=urllib.parse.quote
                rows=sb("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+q(client_id,safe="")+
                    "&symbol=eq."+q(symbol,safe="")+"&timeframe=eq."+q(timeframe,safe="")+
                    "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume"+
                    "&order=candle_time.desc&limit=190",admin=True)
                if len(rows)<75:return self.send_json(409,{"error":"لا توجد 75 شمعة تاريخية محفوظة على الأقل"})
                memory=memory_snapshot(client_id,sb)
                history=combine_deals(memory["deals"],item.get("closed_deals") or [])
                learned=live_learning_profile(history)
                candidate=sanitize_profile(learned["weights"],learned.get("combination_weights") or {})
                baseline=get_active_strategy_profile(client_id,sb) or {"strategy_weights":{},"combination_weights":{}}
                summary=compare_profiles(list(reversed(rows)),baseline,candidate)
                saved=save_candidate(client_id,candidate,summary,sb)
                return self.send_json(200,{"id":saved["id"],"version_label":saved["version_label"],
                    "status":"CANDIDATE","test_summary":summary,
                    "message":"اختبار تاريخي تشخيصي؛ لا يشغّل التداول ولا يغير الإصدار الحالي"})
            if path=="/api/ai/versions/activate":
                return self.send_json(409,{"error":"تنشيط أوزان إصدارات الذهب محظور في محرك BTC"})
                u=owner(token)
                identity=license_for_user(u["id"])
                if not identity:return self.send_json(409,{"error":"لا يوجد معرّف تخزين للمالك"})
                result=activate_strategy_version(identity["client_id"],data.get("id"),sb)
                event={"event_id":"version:"+identity["client_id"]+":"+str(result["id"])+":"+str(time.time_ns()),
                    "client_id":identity["client_id"],"event_type":"AI_VERSION_ACTIVATED",
                    "payload":{"version_id":result["id"],"previous_id":result.get("previous_id"),
                        "changed_by":"owner","trading_activated":False}}
                queue_ai_diagnostic_event(event)
                return self.send_json(200,result)
            if path=="/api/owner/terminal-pair":
                u=owner(token)
                return self.send_json(200,issue_owner_pairing(u))
            if path=="/api/owner/engine-license":
                return self.send_json(410,{"error":"تم إلغاء تراخيص المالك؛ استخدم اقتران MT5 الخاص بالمالك."})
            if path=="/api/client/onboarding-package":
                u=current_user(token)
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    return self.send_json(403,{"error":"هذه الخدمة مخصصة للعميل فقط"})
                if banned(admin_user(u["id"])):
                    return self.send_json(403,{"error":"حساب العميل موقوف"})
                record=license_for_user(u["id"])
                if not record or record.get("revoked"):
                    return self.send_json(403,{"error":"اتصال العميل غير مرخص أو موقوف"})
                try:
                    expires=datetime.fromisoformat(str(record["expires_at"]).replace("Z","+00:00"))
                    if expires<=datetime.now(timezone.utc):
                        return self.send_json(403,{"error":"انتهى الترخيص، تواصل مع الإدارة"})
                except (KeyError,ValueError,TypeError):
                    return self.send_json(403,{"error":"بيانات الترخيص غير صالحة"})
                # Only regenerate after an explicit click; this invalidates the older token.
                credentials=issue_license(u["id"])
                bundle=build_client_bundle(credentials["client_id"],credentials["client_token"],
                                           email=u.get("email",""))
                return self.send_json(200,{"onboarding":bundle,"token_rotated":True,
                    "warning_ar":"رمز الاتصال السابق انتهى. حمّل الملف الجديد على MT5."})
            if path=="/api/admin/clients":
                owner(token); email=(data.get("email") or "").strip().lower(); name=(data.get("name") or "").strip()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): return self.send_json(400,{"error":"أدخل بريداً إلكترونياً صحيحاً للعميل"})
                if email==OWNER_EMAIL: return self.send_json(400,{"error":"لا يمكن إنشاء العميل ببريد المالك"})
                password=secrets.token_urlsafe(12)+"A7!"
                u=sb("POST","/auth/v1/admin/users",{"email":email,"password":password,"email_confirm":True,"user_metadata":{"name":name,"role":"client"}},admin=True)
                try: credentials=issue_license(u.get("id"))
                except Exception:
                    try: sb("DELETE","/auth/v1/admin/users/"+urllib.parse.quote(u.get("id",""),safe=""),admin=True)
                    except Exception: pass
                    raise
                # Build the private installation package for this owner response only.
                # Password setup invitation is a separate Auth email; no credentials emailed.
                bundle=build_client_bundle(credentials["client_id"],credentials["client_token"],
                                           email=email,password=password)
                welcome=send_client_password_setup(email)
                return self.send_json(201,{"id":u.get("id"),"email":email,"name":name,"password":password,"engine":credentials,
                                          "engine_server_url":CLIENT_ONBOARDING_URL,
                                          "onboarding":bundle,"welcome_email_status":welcome,
                                          "message":"ملف MT5 جاهز. بريد إعداد كلمة المرور يعتمد على إرسال Supabase Auth. التداول غير مفعّل تلقائياً."})
            if path=="/api/access/check":
                u=current_user(token)
                if banned(admin_user(u["id"])): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                return self.send_json(200,{"ok":True})
            parts=path.strip("/").split("/")
            if len(parts)==4 and parts[:3]==["v1","terminal-state","live"]:
                heartbeat_start=time.perf_counter()
                client_id=parts[3]
                if not validate_license(client_id,token): return self.send_json(403,{"error":"مصادقة اتصال MT5 غير صالحة"})
                # Reject gold/other charts before touching in-memory state or learning.
                if not btc_market(data.get("symbol")):
                    return self.send_json(409,{"accepted":False,
                        "error":"رمز الشارت لا يطابق مسار التداول "+ENGINE_MARKET})
                account=data.get("account") or {}
                if not isinstance(account,dict): return self.send_json(400,{"error":"بيانات الطرفية غير صحيحة"})
                safe={"login":str(account.get("login",""))[:40],"server":str(account.get("server",""))[:100],
                      "company":str(account.get("company",""))[:100],"currency":str(account.get("currency",""))[:12],
                      "leverage":int(finite_number(account.get("leverage",0))),
                      "balance":finite_number(account.get("balance")),"equity":finite_number(account.get("equity")),
                      "margin":finite_number(account.get("margin")),"free_margin":finite_number(account.get("free_margin")),
                      "profit":finite_number(account.get("profit")),
                      "trade_mode":account.get("trade_mode") if account.get("trade_mode") in ("DEMO","REAL","CONTEST") else "UNKNOWN"}
                positions=clean_telemetry_rows(data.get("positions"),("ticket","position_id","symbol","type","volume","open_price","price_current","sl","tp","profit","swap","magic","comment","time"))
                orders=clean_telemetry_rows(data.get("orders"),("ticket","symbol","type","state","volume","price","sl","tp","magic","comment","time_setup"))
                # A BTC profit basket cannot coexist safely with other symbols
                # while the legacy MT5 closer uses account-wide bookkeeping.
                active_symbol=str(data.get("symbol") or "")
                if any(str(p.get("symbol") or "")!=active_symbol for p in positions+orders):
                    return self.send_json(409,{"accepted":False,
                        "error":"حساب MT5 يحتوي مراكز أو أوامر لسوق آخر؛ استخدم حساب Deriv BTC تجريبي منفصل"})
                if not bind_market_account(client_id,safe):
                    return self.send_json(409,{"accepted":False,"error":"معرّف السوق مرتبط بحساب MT5 مختلف أو مستخدم في السوق الآخر"})
                previous=CONNECTIONS.get(client_id,{})
                if data.get("closed_deals_unchanged") is True:
                    # Compact heartbeat: the client only resends the historical
                    # archive on a trade event or its periodic refresh.
                    closed_deals=previous.get("closed_deals") or []
                    action_entry_deals=previous.get("action_entry_deals") or []
                else:
                    closed_deals=clean_telemetry_rows(data.get("closed_deals"),("ticket","position_id","symbol","type","deal_type","entry","reason","volume","price","profit","commission","swap","fee","magic","comment","strategy_id","strategy_comment","time"),200)
                    # Only BTC chart deals enter BTC memory, even when MT5
                    # history still contains older gold transactions.
                    closed_deals=[d for d in closed_deals if str(d.get("symbol") or "")==active_symbol]
                    # Historical persistence must never block real-time ticks.
                    action_entry_deals=clean_telemetry_rows(data.get("action_entry_deals"),("ticket","position_id","symbol","type","deal_type","entry","volume","price","profit","commission","swap","fee","comment","time"),200)
                    action_entry_deals=[d for d in action_entry_deals if str(d.get("symbol") or "")==active_symbol and d.get("entry")==0 and d.get("position_id")]
                    queue_closed_deals(client_id,closed_deals+action_entry_deals,sb)
                candles=clean_telemetry_rows(data.get("closed_candles"),("time","open","high","low","close","tick_volume"),120)
                # Two independent ways to learn about *actual* exit fills:
                # the updated EA's persisted close timestamp, and the deal
                # archive sent by older terminals. Preserve monotonicity.
                recent_exit=last_exit_from_deals(closed_deals,int(time.time()))
                recorded_exit=max(0,int(finite_number(data.get("last_closed_deal_time"))),
                                  int(finite_number(previous.get("last_closed_deal_time"))),
                                  recent_exit)
                m1_closed_open=int(finite_number(data.get("last_m1_closed_open")))
                if m1_closed_open<=0 and str(data.get("timeframe","")).upper() in ("M1","PERIOD_M1") and candles:
                    m1_closed_open=int(finite_number(candles[0].get("time")))
                forming=data.get("forming_candle") if isinstance(data.get("forming_candle"),dict) else None
                if forming:
                    forming=clean_telemetry_rows([forming],("time","open","high","low","close","tick_volume"),1)
                    forming=forming[0] if forming else None
                tick=data.get("tick") if isinstance(data.get("tick"),dict) else {}
                safe_tick={"bid":finite_number(tick.get("bid")),"ask":finite_number(tick.get("ask")),
                           "spread":finite_number(tick.get("spread")),
                           "point_size":max(0,finite_number(tick.get("point_size"))),
                           "volume_min":max(0,finite_number(tick.get("volume_min"))),
                           "volume_max":max(0,finite_number(tick.get("volume_max"))),
                           "volume_step":max(0,finite_number(tick.get("volume_step")))}
                now=datetime.now(timezone.utc)
                previous=CONNECTIONS.get(client_id,{})
                strategy_decision=previous.get("strategy_decision")
                strategy_last_bar=previous.get("strategy_last_bar")
                bot_report=previous.get("bot_report")
                bot_history=previous.get("bot_signal_history") or []
                bot_dispatch_count=int(previous.get("bot_dispatch_count",0))
                bot_new=False
                # A single engine ID holds one chart snapshot. Never re-use a
                # prior XAUUSD decision/vote after the terminal switches to BTCUSD.
                if (str(previous.get("symbol") or "").upper()!=str(data.get("symbol") or "").upper()
                        or str(previous.get("timeframe") or "")!=str(data.get("timeframe") or "")):
                    strategy_decision=None
                    strategy_last_bar=None
                    bot_report=None
                    bot_history=[]
                    bot_dispatch_count=0
                # Every new CLOSED candle independently activates all five bot engines,
                # even if the main 200-strategy decision calculation later fails.
                if len(candles)>=65 and supported_market_symbol(data.get("symbol")):
                    current_bar=int(candles[0].get("time") or 0)
                    if current_bar>0 and (
                        not isinstance(bot_report,dict)
                        or int(finite_number(bot_report.get("bar_time",0)))!=current_bar
                    ):
                        try:
                            bot_report=analyze_advisors(list(reversed(candles)),symbol=data.get("symbol"))
                            bot_new=(bot_report.get("status")=="READY" and int(bot_report.get("bar_time",0))==current_bar)
                        except Exception as bot_error:
                            bot_report=None
                            print("BOT_ENGINE_WARNING:",type(bot_error).__name__,flush=True)
                # MT5 sends closed candles newest-first. Evaluate once per newly closed bar.
                if len(candles)>=65 and supported_market_symbol(data.get("symbol")):
                    bar_time=int(candles[0].get("time") or 0)
                    if bar_time>0 and bar_time!=strategy_last_bar:
                        try:
                            # A live version can be deliberately selected by the owner,
                            # but must not freeze all subsequent real-trade feedback.
                            # Never stop analysis while historical memory is loading.
                            memory=memory_snapshot(client_id,sb)
                            live_learning=live_learning_profile(combine_deals(
                                memory.get("deals") or [],closed_deals),symbol=data.get("symbol") or ENGINE_MARKET)
                            raw_mtf=data.get("mtf_bias") if isinstance(data.get("mtf_bias"),dict) else {}
                            mtf_bias={k:str(v).upper() for k,v in raw_mtf.items() if k in ("M5","M15","H1") and str(v).upper() in ("BUY","SELL","WAIT")}
                            try:
                                chosen_profile=(get_active_strategy_profile(client_id,sb)
                                    if market_family(data.get("symbol"))=="XAUUSD" and not DUAL_MARKET_MODE else None)
                                active_profile_error=None
                            except Exception as profile_error:
                                # Learning/version storage being unavailable never
                                # becomes a fresh-trade WAIT gate.
                                chosen_profile=None
                                active_profile_error=type(profile_error).__name__
                                print("ACTIVE_PROFILE_FALLBACK:",active_profile_error,flush=True)
                            rates=list(reversed(candles))
                            regime=analyze_market_context(rates).get("regime","غير محدد")
                            historical=btc_learning_snapshot(client_id,active_symbol,data.get("timeframe"),sb)
                            try:
                                effective=btc_learning_weights(historical,live_learning,regime)
                            except Exception as learning_error:
                                print("BTC_LEARNING_FALLBACK:",type(learning_error).__name__,flush=True)
                                effective=btc_learning_weights({},live_learning,regime)
                            learning_diagnostic={**effective["diagnostics"],
                                "memory_status":memory.get("status","LOADING"),
                                "memory_deals":int(memory.get("count",0)),
                                "active_version_label":ENGINE_MARKET+" ADAPTIVE V1",
                                "active_version_id":(chosen_profile or {}).get("version_id"),
                                "profile_read_error":active_profile_error}
                            strategy_weights=effective["strategy_weights"]
                            combination_weights=effective["combination_weights"]
                            evaluated=evaluate_market(rates,weights=strategy_weights,
                                combination_weights=combination_weights,mtf_bias=mtf_bias,mode="AUTO",
                                bot_signals=bot_report,market_symbol=data.get("symbol"))
                            strategy_decision={
                                "symbol":active_symbol,
                                "decision":evaluated["decision"],
                                "entry_reference_price":finite_number(candles[0].get("close")),
                                "entry_atr":finite_number(evaluated.get("context",{}).get("atr",0.0)),
                                "strategy_id":evaluated["strategy_id"],
                                "strategy_name":evaluated["strategy_name"],
                                "combination_id":evaluated.get("combination_id",0),
                                "reason_ar":evaluated["reason_ar"],
                                "confidence":evaluated["confidence"],
                                "context":evaluated["context"],
                                "mtf_bias":evaluated.get("mtf_bias",{}),
                                "adaptive":evaluated["adaptive"],
                                "council":evaluated["council"],
                                "family_votes":evaluated.get("family_votes",{}),
                                "vote_summary":evaluated["vote_summary"],
                                "combination_summary":evaluated["combination_summary"],
                                "directional_scores":evaluated.get("directional_scores",{}),
                                "directional_candidates":evaluated.get("directional_candidates",{}),
                                "bot_evidence":evaluated.get("bot_evidence",{}),
                                "strategy_count":evaluated["strategy_count"],
                                "combination_count":evaluated["combination_count"],
                                "learning":learning_diagnostic,
                                "bar_time":bar_time,
                                "advisory_bots":(bot_report if isinstance(bot_report,dict)
                                                 and int(finite_number(bot_report.get("bar_time",0)))==bar_time
                                                 else {"status":"WAIT","advisors":[]}),
                                "generated_at":now.isoformat(),
                                "execution_ready":False
                            }
                            strategy_last_bar=bar_time
                            # Persist bounded proof of weight usage in the candle EVALUATOR only.
                            # Neither this event nor its status grants trading permission.
                            learning_audit=decision_audit_event(
                                client_id,active_symbol,data.get("timeframe"),bar_time,
                                learning_diagnostic)
                            if learning_audit:
                                queue_ai_diagnostic_event(learning_audit)
                            if learning_diagnostic.get("status")=="APPLIED":
                                # Persistent, idempotent proof that adjusted live
                                # weights actually entered a closed-candle decision.
                                digest=hashlib.sha256(json.dumps({
                                    "s":learning_diagnostic["changed_strategies"],
                                    "c":learning_diagnostic["changed_combinations"],
                                    "at":learning_diagnostic.get("last_attributed_at"),"historical_last_bar":learning_diagnostic.get("historical_last_bar"),"regime":learning_diagnostic.get("regime")},
                                    ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()[:16]
                                event_id=("live-learning:"+client_id+":"+digest)
                                queue_ai_diagnostic_event({
                                    "event_id":event_id,
                                    "client_id":client_id,
                                    "event_type":"LIVE_LEARNING_APPLIED",
                                    "payload":{"bar_time":bar_time,"symbol":active_symbol,
                                        "last_attributed_at":learning_diagnostic.get("last_attributed_at"),
                                        "processed_positions":learning_diagnostic["processed_positions"],
                                        "applied_strategies":learning_diagnostic["applied_strategies"],
                                        "applied_combinations":learning_diagnostic["applied_combinations"],
                                        "active_version_label":learning_diagnostic["active_version_label"],
                                        "historical_samples":learning_diagnostic.get("historical_samples",0),
                                        "regime":learning_diagnostic.get("regime"),
                                        "reason_ar":"تم تمرير الأوزان المتعلمة إلى محرك قرار الشمعة "+ENGINE_MARKET}}
                                )
                        except Exception as engine_error:
                            print("CENTRAL_ENGINE_WARNING:",str(engine_error)[:250],flush=True)
                if bot_new:
                    # Each bot sends a distinct same-bar signal to the central
                    # bus and the existing async Supabase diagnostics queue.
                    bot_events=build_bot_messages(client_id,str(data.get("symbol","")),
                        str(data.get("timeframe","")),bot_report,
                        ((strategy_decision or {}).get("decision","WAIT")
                         if int(finite_number((strategy_decision or {}).get("bar_time",0)))==
                            int(finite_number(bot_report.get("bar_time",0))) else "WAIT"))
                    bot_history=update_history(bot_history,bot_events)
                    bot_dispatch_count=sum(bool(queue_ai_diagnostic_event(e)) for e in bot_events)
                # V4 research is entirely separate from execution authorization.
                # A small bounded in-memory computation and existing async event
                # queue avoid additional database calls on the heartbeat path.
                try:
                    for event in accuracy_v4.settle(client_id,active_symbol,data.get("timeframe"),candles):
                        queue_ai_diagnostic_event(event)
                    for event in master_v2.settle(client_id,active_symbol,data.get("timeframe"),candles):
                        queue_ai_diagnostic_event(event)
                    if (strategy_decision and candles and
                        int(finite_number(strategy_decision.get("bar_time")))==int(finite_number(candles[0].get("time"))) and
                        strategy_last_bar!=previous.get("strategy_last_bar")):
                        v4=accuracy_v4.shadow_rank(strategy_decision,list(reversed(candles)))
                        event=accuracy_v4.record(client_id,active_symbol,data.get("timeframe"),
                            strategy_decision,safe_tick,"QUANT",v4["direction"],None,now.timestamp())
                        if event:
                            event["payload"]["research_ranking"]=v4
                            queue_ai_diagnostic_event(event)
                except Exception as exc:
                    print("ACCURACY_V4_SHADOW_WARNING:",type(exc).__name__,flush=True)
                if strategy_decision:
                    cached_settings=(TRADE_SETTINGS_CACHE.get(client_id) or {}).get("settings") or {}
                    update_council(client_id,strategy_decision,safe,
                        master_review=(strategy_last_bar!=previous.get("strategy_last_bar") and
                            (cached_settings.get("position_management") or {}).get("entry_council_mode")=="MASTER" and
                            terminal_is_owner(client_id)))
                    try:
                        # Observe the actual returned vote and current quote, not
                        # a retroactive quote from the start of the candle.
                        votes=council_result(client_id,strategy_decision)
                        if votes.get("bar_time")==strategy_decision.get("bar_time"):
                            for kind,model,vote in (
                                ("FAST_AI","Fast AI",(votes.get("votes") or {}).get("Fast AI") or {}),
                                ("CLOUD_AI","GPT-OSS 120B",votes.get("groq_advisory") or {})):
                                if vote.get("status")!="READY":continue
                                event=accuracy_v4.record(client_id,active_symbol,data.get("timeframe"),
                                    strategy_decision,safe_tick,kind,vote.get("direction"),
                                    vote.get("confidence"),now.timestamp(),model=model,analysis_mode="UNIFIED")
                                if event:queue_ai_diagnostic_event(event)
                        feedback=feedback_learning.snapshot(client_id,active_symbol,sb,closed_deals+action_entry_deals)
                        # Shadow observes only an already verified cached owner. Never add an auth DB lookup here.
                        with CACHE_LOCK:
                            shadow_owner=dict(OWNER_TERMINAL_ID_CACHE.get(client_id) or {})
                        if (shadow_owner.get("is_owner") is True and shadow_owner.get("until",0)>time.monotonic()
                                and entry_council_mode(cached_settings,True)=="MASTER"):
                            event=master_v2.observe(client_id,active_symbol,data.get("timeframe"),strategy_decision,votes,feedback,safe_tick,now.timestamp())
                            if event:queue_ai_diagnostic_event(event)
                            master_v3.submit(client_id,active_symbol,data.get("timeframe"),strategy_decision,votes,feedback,safe_tick,cached_settings,now.timestamp(),queue_ai_diagnostic_event,arena=arena_master.evidence(client_id,strategy_decision),positions=positions)
                    except Exception as feedback_error:
                        print("FEEDBACK_LEARNING_WARNING:",type(feedback_error).__name__,flush=True)
                CONNECTIONS[client_id]={"client_id":client_id,"account":safe,"connected":True,
                    "symbol":str(data.get("symbol",""))[:40],"timeframe":str(data.get("timeframe",""))[:20],
                    "terminal_build":int(finite_number(data.get("terminal_build",0))),
                    "mt5_position_management_enabled":(data.get("mt5_position_management_enabled")
                        if isinstance(data.get("mt5_position_management_enabled"),bool) else None),
                    "mt5_central_trading_enabled":(data.get("mt5_central_trading_enabled")
                        if isinstance(data.get("mt5_central_trading_enabled"),bool) else None),
                    "positions":positions,
                    "master_direct_demo_capable":data.get("master_direct_demo_capable") is True,
                    # Server-verified owner identity, never a client-provided flag.
                    "owner_master_compatible":terminal_is_owner(client_id),
                    "smart_profit_capable":data.get("smart_profit_capable") is True,
                    "smart_profit_runner_version":304 if data.get("smart_profit_runner_version")==304 else None,
                    "recovery_bidirectional_capable":data.get("recovery_bidirectional_capable") is True,
                    "recovery_cycle_active":data.get("recovery_cycle_active") is True,
                    "recovery_group_net":(finite_number(data["recovery_group_net"]) if
                        data.get("recovery_group_net") is not None else None),
                    "orders":orders,"closed_deals":closed_deals,"action_entry_deals":action_entry_deals,"closed_candles":candles,
                    "last_closed_deal_time":recorded_exit,
                    "last_m1_closed_open":m1_closed_open,
                    "forming_candle":forming,"tick":safe_tick,"strategy_decision":strategy_decision,
                    "bot_report":bot_report,"bot_signal_history":bot_history,
                    "bot_dispatch_count":bot_dispatch_count,
                    "local_ai_advice":retain_current_local_ai_advice(previous.get("local_ai_advice"),strategy_decision,time.time()),
                    "strategy_last_bar":strategy_last_bar,
                    "received_at":now,"last_seen":now.isoformat()}
                if strategy_decision:
                    btc_learning_snapshot(client_id,active_symbol,data.get("timeframe"),sb)
                return self.send_json(200,{"accepted":True,"telemetry":True,
                    "market_owner_terminal":terminal_is_owner(client_id) if DUAL_MARKET_MODE else False,
                    "positions_received":len(positions),"orders_received":len(orders),
                    "closed_deals_received":len(closed_deals),
                    "candles_received":len(candles),
                    "strategy_engine":"ready" if strategy_decision else "waiting_for_65_closed_supported_market_candles",
                    "server_processing_ms":round((time.perf_counter()-heartbeat_start)*1000,1)})
            return self.send_json(404,{"error":"not_found"})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:250]})
    def do_PATCH(self):
        try:
            owner(token_of(self)); prefix="/api/admin/clients/"
            if not self.path.startswith(prefix): return self.send_json(404,{"error":"not_found"})
            uid=self.path[len(prefix):]
            if not re.fullmatch(r"[0-9a-fA-F-]{20,}",uid): return self.send_json(400,{"error":"معرّف الحساب غير صحيح"})
            active=bool(self.read_json().get("active"))
            sb("PUT","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),{"ban_duration":"none" if active else "876000h"},admin=True)
            rows=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(uid,safe="")+"&select=client_id",admin=True)
            if rows:
                sb("PATCH","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(uid,safe=""),{"revoked":not active},admin=True)
                for row in rows:
                    CONNECTIONS.pop(row["client_id"],None)
                    LICENSE_CACHE.pop(row["client_id"],None)
                    TRADE_SETTINGS_CACHE.pop(row["client_id"],None)
            return self.send_json(200,{"ok":True,"active":active})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:250]})
    def do_DELETE(self):
        try:
            owner(token_of(self)); prefix="/api/admin/clients/"
            if not self.path.startswith(prefix): return self.send_json(404,{"error":"not_found"})
            uid=self.path[len(prefix):]
            if not re.fullmatch(r"[0-9a-fA-F-]{20,}",uid): return self.send_json(400,{"error":"معرّف الحساب غير صحيح"})
            target=admin_user(uid)
            if (target.get("email") or "").lower()==OWNER_EMAIL: return self.send_json(403,{"error":"لا يمكن حذف حساب المالك"})
            rows=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(uid,safe="")+"&select=client_id",admin=True)
            for row in rows:
                CONNECTIONS.pop(row["client_id"],None)
                LICENSE_CACHE.pop(row["client_id"],None)
                TRADE_SETTINGS_CACHE.pop(row["client_id"],None)
            sb("DELETE","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),admin=True)
            return self.send_json(200,{"ok":True,"deleted":True})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:250]})
    def end_headers(self):
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options","SAMEORIGIN"); self.send_header("Content-Security-Policy","frame-ancestors 'self'"); super().end_headers()

def _read_stored_m1_for_smoke():
    """Fetch latest 500 closed BTCUSD M1 candles; never read legacy gold history."""
    base="/rest/v1/alzool_engine_market_candles?"
    latest=sb("GET",base+"symbol=eq.BTCUSD&timeframe=eq.PERIOD_M1"
              "&select=client_id&order=candle_time.desc&limit=1",admin=True)
    if not latest:return []
    cid=urllib.parse.quote(str(latest[0]["client_id"]),safe="")
    result=sb("GET",base+"client_id=eq."+cid+"&symbol=eq.BTCUSD"
              "&timeframe=eq.PERIOD_M1"
              "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume"
              "&order=candle_time.desc&limit=500",admin=True)
    return list(reversed(result or []))


if __name__=="__main__":
    # Validate pure trading policy without connecting to MT5 or sending trades.
    # Fail a deployment rather than activating an untested account recovery change.
    import unittest
    from pathlib import Path
    test_dir=str(Path(__file__).resolve().parents[2]/"tests")
    recovery_suite=unittest.TestSuite()
    for pattern in ("test_recovery_basket_speed.py","test_adaptive_memory.py","test_close_groups.py","test_arena_master.py","test_specialist_market.py","test_master_v3.py","test_master_entry_review.py","test_owner_decisions.py","test_owner_recovery.py",
                    "test_recovery_action_protocol.py","test_hybrid_decision.py",
                    "test_position_sizing.py","test_entry_quality.py",
                    "test_live_learning.py","test_learning_observability.py","test_post_close_m1.py",
                    "test_recovery_candle_guard.py","test_local_ai_relay_cpu.py",
                    "test_position_action_safety.py","test_position_management_status.py",
                    "test_btc_market_gate.py","test_btc_market_symbols.py",
                    "test_btc_market_isolation.py","test_btc_adaptive_learning.py"):
        recovery_suite.addTests(unittest.defaultTestLoader.discover(
            start_dir=test_dir,pattern=pattern))
    recovery_result=unittest.TextTestRunner(verbosity=1).run(recovery_suite)
    if not recovery_result.wasSuccessful():
        raise RuntimeError("ALZOOL_OFFLINE_RECOVERY_CHECKS_FAILED")
    print("ALZOOL_OFFLINE_RECOVERY_CHECKS_PASS",flush=True)
    print("BTC_SUPABASE_PROJECT_READY:",btc_database_ready(),flush=True)
    # Safe read-only startup probe: do not print keys, user rows or errors.
    try:
        rows=sb("GET","/rest/v1/alzool_engine_clients?select=client_id&limit=1",admin=True)
        print("BTC_SUPABASE_SERVICE_ROLE_ACCESS:", "OK" if isinstance(rows,list) else "UNEXPECTED_RESPONSE",flush=True)
    except Exception as exc:
        print("BTC_SUPABASE_SERVICE_ROLE_ACCESS:FAILED",type(exc).__name__,flush=True)
    # Server-side smoke test, read-only, never exposes the API key.
    if os.getenv("GROQ_VERIFY_ON_STARTUP","false").lower() in ("true","1","yes"):
        threading.Thread(target=lambda: print("GROQ_SMOKE_STATUS:",verify_groq_credentials(),flush=True),
                         daemon=True,name="alzool-groq-smoke").start()
    if os.getenv("ALZOOL_HISTORICAL_M1_SMOKE","false").strip().lower() in ("true","1","yes"):
        threading.Thread(target=run_offline_m1_smoke,args=(_read_stored_m1_for_smoke,),
                         daemon=True,name="alzool-offline-m1-diagnostic").start()
    bootstrap_owner()
    port=int(os.getenv("PORT","10000"))
    print("AL-ZOOL connection/licensing service listening on",port,flush=True)
    # This release is the owner-approved unified pilot. Starts with NO entry
    # market selected, even if an older account had trading_enabled=true.
    # Existing exit management uses the same isolated account settings.
    if os.getenv("ALZOOL_ENABLE_DUAL_PILOT","1")=="1":
        os.environ["ALZOOL_ENABLE_DUAL_PILOT"]="1"
        from dual_market_server import run as run_dual_market
        print("AL-ZOOL DUAL MARKET startup: entry selection OFF",flush=True)
        from strategy_research import run as run_strategy_research
        threading.Thread(target=run_strategy_research,args=(sb,OWNER_EMAIL),daemon=True,name="strategy-evidence").start()
        from specialist_market import run as run_specialist
        threading.Thread(target=run_specialist,args=(sb,OWNER_EMAIL),daemon=True,name="specialist-research").start()
        run_dual_market()
    else:
        ThreadingHTTPServer((os.getenv("ALZOOL_BIND_HOST","0.0.0.0"),port),Handler).serve_forever()
