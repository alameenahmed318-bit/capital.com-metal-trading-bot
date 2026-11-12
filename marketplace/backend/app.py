"""AL-ZOOL: MT5 connection monitor and customer/licence administration only."""
import hashlib, hmac, json, math, os, re, secrets, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_ANON_KEY=os.getenv("SUPABASE_ANON_KEY","")
SUPABASE_SERVICE_ROLE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
OWNER_EMAIL=os.getenv("OWNER_EMAIL","Meenosvn1@gmail.com").strip().lower()
PUBLIC_ENGINE_URL=os.getenv("PUBLIC_ENGINE_URL","https://al-zool-gold-sniper.onrender.com").rstrip("/")
CONNECTIONS={}

def sb(method,path,payload=None,bearer=None,admin=False,prefer=None):
    if not SUPABASE_URL: raise RuntimeError("SUPABASE_URL is not configured")
    key=SUPABASE_SERVICE_ROLE_KEY if admin else SUPABASE_ANON_KEY
    if not key: raise RuntimeError("Supabase key is not configured")
    headers={"apikey":key,"Accept":"application/json","Authorization":"Bearer "+(SUPABASE_SERVICE_ROLE_KEY if admin else (bearer or key))}
    data=None
    if payload is not None:
        headers["Content-Type"]="application/json"; data=json.dumps(payload).encode()
    if prefer: headers["Prefer"]=prefer
    req=urllib.request.Request(SUPABASE_URL+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            raw=r.read(); return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")
        try:
            obj=json.loads(detail); detail=obj.get("msg") or obj.get("message") or detail
        except Exception: pass
        raise ValueError(str(detail)[:300])

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
    return {"client_id":client_id,"client_token":token,"expires_at":expires}

def validate_license(client_id,token):
    if not client_id or not token: return False
    rows=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&select=token_hash,expires_at,revoked&limit=1",admin=True)
    if not rows: return False
    row=rows[0]
    try: expired=datetime.fromisoformat(row["expires_at"].replace("Z","+00:00"))<=datetime.now(timezone.utc)
    except Exception: return False
    return not row.get("revoked") and not expired and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),row["token_hash"])

def finite_number(value, default=0.0):
    try:
        number=float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default

def clean_telemetry_rows(rows, allowed, limit=200):
    if not isinstance(rows,list): return []
    cleaned=[]
    for item in rows[:limit]:
        if not isinstance(item,dict): continue
        row={}
        for key in allowed:
            if key not in item: continue
            value=item[key]
            if key in ("symbol","comment","ticket","magic","position_id"):
                row[key]=str(value)[:120]
            elif key in ("type","state","entry","reason","deal_type"):
                try: row[key]=int(value)
                except (TypeError,ValueError,OverflowError): row[key]=0
            elif key=="time":
                try: row[key]=int(value)
                except (TypeError,ValueError,OverflowError): row[key]=0
            else: row[key]=finite_number(value)
        cleaned.append(row)
    return cleaned

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
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
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
    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path in ("/health","/api/health"):
            return self.send_json(200,{"ok":True,"service":"AL-ZOOL MT5 Connection & Licensing","mt5_connection_api":True,"trading_engine":False})
        if path=="/api/config":
            return self.send_json(200,{"supabase_url":SUPABASE_URL,"supabase_anon_key":SUPABASE_ANON_KEY,"owner_email":OWNER_EMAIL,"ready":bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY)})
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
                is_owner=(u.get("email") or "").lower()==OWNER_EMAIL
                if is_owner:
                    latest=sb("GET","/rest/v1/alzool_engine_market_candles?select=client_id&order=uploaded_at.desc&limit=1",admin=True)
                    client_id=latest[0].get("client_id","") if latest else ""
                else:
                    lic=license_for_user(u["id"]); client_id=lic.get("client_id") if lic else ""
                if not client_id:
                    return self.send_json(409,{"error":"لا يوجد سجل تاريخي محفوظ لهذا الحساب بعد. حدّث عميل MT5 لإرسال التاريخ."})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                requested_days=int(finite_number((params.get("days") or ["90"])[0],90))
                days=requested_days if requested_days in (0,7,30,90,180,365,730) else 90
                requested_limit=int(finite_number((params.get("limit") or ["5000"])[0],5000))
                row_limit=max(100,min(10000,requested_limit))
                meta_query=("/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=symbol,timeframe&order=uploaded_at.desc")
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
                    if tf and tf not in available_timeframes: available_timeframes.append(tf)
                    if sym and sym not in available_symbols: available_symbols.append(sym)
                if not available_timeframes:
                    return self.send_json(409,{"error":"لم تصل شموع تاريخية إلى قاعدة البيانات بعد. شغّل عميل MT5 المحدّث."})
                timeframe=str((params.get("timeframe") or [available_timeframes[0]])[0])[:20]
                symbol=str((params.get("symbol") or [available_symbols[0] if available_symbols else "XAUUSD"])[0])[:40]
                if timeframe not in available_timeframes: return self.send_json(400,{"error":"الفريم المطلوب غير موجود ضمن البيانات المحفوظة","available_timeframes":available_timeframes})
                if symbol not in available_symbols: return self.send_json(400,{"error":"الرمز المطلوب غير موجود ضمن البيانات المحفوظة","available_symbols":available_symbols})
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
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                else:
                    lic=license_for_user(u["id"]); client_id=lic.get("client_id") if lic else ""
                    item=CONNECTIONS.get(client_id) if client_id else None
                t_lookup=time.perf_counter()
                connected=bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30)
                if not connected:
                    return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة. تحقق من اتصال الطرفية.",
                        "diagnostics":{"auth_ms":round((t_user-t0)*1000),"ban_check_ms":round((t_admin-t_user)*1000),"ban_check_source":"fresh_auth_user_record",
                        "connection_lookup_ms":round((t_lookup-t_admin)*1000),"server_total_ms":round((t_lookup-t0)*1000)}})
                candles=(item or {}).get("closed_candles",[])
                # MT5 sends series data newest-first; return oldest-first for analysis.
                result={"symbol":item.get("symbol"),"timeframe":item.get("timeframe"),
                    "tick":item.get("tick",{}),"forming_candle":item.get("forming_candle"),
                    "candles":list(reversed(candles)),"received_at":item["last_seen"],
                    "diagnostics":{"auth_ms":round((t_user-t0)*1000),"ban_check_ms":round((t_admin-t_user)*1000),"ban_check_source":"fresh_auth_user_record",
                    "connection_lookup_ms":round((t_lookup-t_admin)*1000),"server_total_ms":round((t_lookup-t0)*1000)}}
                return self.send_json(200,result)
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path=="/api/dashboard/summary":
            try:
                u=current_user(token_of(self)); fresh=admin_user(u["id"])
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    # Resolve the owner's stable licensed client_id from Supabase first.
                    # CONNECTIONS is intentionally ephemeral and may be empty after a Render restart.
                    lic=license_for_user(u["id"])
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                    client_id=(lic or {}).get("client_id") or (item or {}).get("client_id","")
                    if client_id:
                        item=CONNECTIONS.get(client_id) or item
                else:
                    lic=license_for_user(u["id"]); client_id=lic.get("client_id") if lic else ""
                    item=CONNECTIONS.get(client_id) if client_id else None
                connected=bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30)
                account=(item or {}).get("account",{}) if connected else {}
                positions=(item or {}).get("positions",[]) if connected else []
                deals=(item or {}).get("closed_deals",[]) if connected else []
                # Durable 30-day history from Supabase. Resolve the latest stored client
                # for the owner as a fallback if the account licence lookup is unavailable.
                history_records=0
                history_error=""
                try:
                    if not client_id and (u.get("email") or "").lower()==OWNER_EMAIL:
                        latest=sb("GET","/rest/v1/alzool_engine_closed_deals?select=client_id&order=deal_time.desc&limit=1",admin=True)
                        if latest: client_id=latest[0].get("client_id","")
                    if client_id:
                        cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
                        stored=sb("GET","/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                            "&deal_time=gte."+urllib.parse.quote(cutoff,safe="")+
                            "&select=deal_time,payload&order=deal_time.desc&limit=2000",admin=True)
                        if isinstance(stored,list):
                            history_records=len(stored)
                            deals=[]
                            for record in stored:
                                if not isinstance(record,dict) or not isinstance(record.get("payload"),dict): continue
                                deal=dict(record["payload"])
                                if not int(deal.get("time") or 0) and record.get("deal_time"):
                                    deal["time"]=int(datetime.fromisoformat(record["deal_time"].replace("Z","+00:00")).timestamp())
                                deals.append(deal)
                except Exception as history_error_value:
                    history_error=str(history_error_value)[:180]
                    print("CLOSED_HISTORY_READ_WARNING:",history_error,flush=True)
                uae=timezone(timedelta(hours=4))
                today=datetime.now(uae).date()
                by_position={}
                daily_by_position={}
                daily_profit=daily_loss=0.0
                for deal in deals:
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
        if path=="/api/connection/status":
            try:
                u=current_user(token_of(self))
                if (u.get("email") or "").lower()!=OWNER_EMAIL:
                    lic=license_for_user(u["id"])
                    client_id=lic.get("client_id") if lic else ""
                    item=CONNECTIONS.get(client_id) if client_id else None
                else:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                
                return self.send_json(200,{"connected":bool(item and (datetime.now(timezone.utc)-item["received_at"]).total_seconds()<30),
                                           "connection":({k:v for k,v in item.items() if k!="received_at"} if item else None)})
            except Exception as e: return self.send_json(403,{"error":str(e)[:180]})
        return super().do_GET()
    def do_POST(self):
        try:
            data=self.read_json(); token=token_of(self)
            path=urllib.parse.urlparse(self.path).path
            if path.startswith("/v1/terminal-history/batch/"):
                client_id=path.rsplit("/",1)[-1]
                if not validate_license(client_id,token): return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
                symbol=str(data.get("symbol") or "")[:40]
                timeframe=str(data.get("timeframe") or "")[:20]
                if not symbol or not timeframe: return self.send_json(400,{"error":"رمز السوق أو الفريم غير صالح"})
                metadata=data.get("metadata") if isinstance(data.get("metadata"),dict) else {}
                candles=clean_historical_candles(data.get("candles"),metadata,client_id,symbol,timeframe)
                if not candles: return self.send_json(400,{"error":"لا توجد شموع تاريخية صالحة في الدفعة"})
                saved=0
                for start in range(0,len(candles),500):
                    batch=candles[start:start+500]
                    sb("POST","/rest/v1/alzool_engine_market_candles?on_conflict=client_id,symbol,timeframe,candle_time",
                       batch,admin=True,prefer="resolution=merge-duplicates")
                    saved+=len(batch)
                return self.send_json(200,{"accepted":True,"candles_saved":saved,"symbol":symbol,"timeframe":timeframe})
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
                return self.send_json(201,{"id":u.get("id"),"email":email,"name":name,"password":password,"engine":credentials,
                                          "engine_server_url":PUBLIC_ENGINE_URL,"message":"بيانات الدخول والترخيص والاتصال فقط. لا توجد صلاحيات تداول."})
            if path=="/api/access/check":
                u=current_user(token)
                if banned(admin_user(u["id"])): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                return self.send_json(200,{"ok":True})
            parts=path.strip("/").split("/")
            if len(parts)==4 and parts[:3]==["v1","terminal-state","live"]:
                client_id=parts[3]
                if not validate_license(client_id,token): return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
                account=data.get("account") or {}
                if not isinstance(account,dict): return self.send_json(400,{"error":"بيانات الطرفية غير صحيحة"})
                safe={"login":str(account.get("login",""))[:40],"server":str(account.get("server",""))[:100],
                      "company":str(account.get("company",""))[:100],"currency":str(account.get("currency",""))[:12],
                      "leverage":int(finite_number(account.get("leverage",0))),
                      "balance":finite_number(account.get("balance")),"equity":finite_number(account.get("equity")),
                      "margin":finite_number(account.get("margin")),"free_margin":finite_number(account.get("free_margin")),
                      "profit":finite_number(account.get("profit"))}
                positions=clean_telemetry_rows(data.get("positions"),("ticket","symbol","type","volume","open_price","price_current","sl","tp","profit","swap","magic","comment","time"))
                orders=clean_telemetry_rows(data.get("orders"),("ticket","symbol","type","state","volume","price","sl","tp","magic","comment","time_setup"))
                closed_deals=clean_telemetry_rows(data.get("closed_deals"),("ticket","position_id","symbol","type","deal_type","entry","reason","volume","price","profit","commission","swap","fee","magic","comment","time"),200)
                # Persist MT5 history so closed trades survive Render restarts/deploys.
                if closed_deals:
                    persisted=[]
                    for deal in closed_deals:
                        deal_id=str(deal.get("ticket") or "")
                        stamp=int(deal.get("time") or 0)
                        if not deal_id or stamp<=0: continue
                        persisted.append({"client_id":client_id,"deal_id":deal_id,
                            "deal_time":datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                            "payload":deal})
                    if persisted:
                        sb("POST","/rest/v1/alzool_engine_closed_deals?on_conflict=client_id,deal_id",
                           persisted,admin=True,prefer="resolution=merge-duplicates")
                candles=clean_telemetry_rows(data.get("closed_candles"),("time","open","high","low","close","tick_volume"),120)
                forming=data.get("forming_candle") if isinstance(data.get("forming_candle"),dict) else None
                if forming:
                    forming=clean_telemetry_rows([forming],("time","open","high","low","close","tick_volume"),1)
                    forming=forming[0] if forming else None
                tick=data.get("tick") if isinstance(data.get("tick"),dict) else {}
                safe_tick={"bid":finite_number(tick.get("bid")),"ask":finite_number(tick.get("ask")),
                           "spread":finite_number(tick.get("spread"))}
                now=datetime.now(timezone.utc)
                CONNECTIONS[client_id]={"client_id":client_id,"account":safe,"connected":True,
                    "symbol":str(data.get("symbol",""))[:40],"timeframe":str(data.get("timeframe",""))[:20],
                    "terminal_build":int(finite_number(data.get("terminal_build",0))),
                    "positions":positions,"orders":orders,"closed_deals":closed_deals,"closed_candles":candles,
                    "forming_candle":forming,"tick":safe_tick,
                    "received_at":now,"last_seen":now.isoformat()}
                return self.send_json(200,{"accepted":True,"telemetry":True,
                    "positions_received":len(positions),"orders_received":len(orders),
                    "closed_deals_received":len(closed_deals),
                    "candles_received":len(candles)})
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
                for row in rows: CONNECTIONS.pop(row["client_id"],None)
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
            for row in rows: CONNECTIONS.pop(row["client_id"],None)
            sb("DELETE","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),admin=True)
            return self.send_json(200,{"ok":True,"deleted":True})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:250]})
    def end_headers(self):
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options","DENY"); super().end_headers()

if __name__=="__main__":
    bootstrap_owner()
    port=int(os.getenv("PORT","10000"))
    print("AL-ZOOL connection/licensing service listening on",port,flush=True)
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
