"""AL-ZOOL: licensed MT5 telemetry, central strategy analysis and account controls."""
import hashlib, hmac, json, math, os, re, secrets, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from strategy_engine import evaluate_market, STRATEGIES, COMBINATIONS

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

def live_learning_profile(deals):
    """Build transparent, outcome-based live weights from real closed XAUUSD positions."""
    grouped={}
    today=datetime.now(timezone(timedelta(hours=4))).date()
    today_total=set()
    for deal in deals if isinstance(deals,list) else []:
        if not isinstance(deal,dict): continue
        symbol=str(deal.get("symbol") or "").upper()
        if not symbol.startswith("XAUUSD"): continue
        entry=int(finite_number(deal.get("entry"),1))
        if entry not in (1,2,3): continue
        stamp=int(finite_number(deal.get("time")))
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
            ckey=str(cid); citem=combo_stats.setdefault(ckey,{"combination_id":cid,"trades":0,"wins":0,"losses":0,"flat":0,"net":0.0})
            citem["trades"]+=1; citem["net"]+=row["net"]
            if row["net"]>0: citem["wins"]+=1; combo_weights[ckey]=min(1.15,combo_weights[ckey]*1.01)
            elif row["net"]<0: citem["losses"]+=1; combo_weights[ckey]=max(0.85,combo_weights[ckey]*0.99)
            else: citem["flat"]+=1
        if not 1<=sid<=200: continue
        key=str(sid); item=stats.setdefault(key,{"strategy_id":sid,"trades":0,"wins":0,"losses":0,"flat":0,"net":0.0})
        item["trades"]+=1; item["net"]+=row["net"]
        if row["net"]>0:
            item["wins"]+=1; weights[key]=min(1.5,weights[key]*1.02)
        elif row["net"]<0:
            item["losses"]+=1; weights[key]=max(0.5,weights[key]*0.98)
        else: item["flat"]+=1
        if row["position_id"] in today_total: matched_today.add(row["position_id"])
    matched=sum(x["trades"] for x in stats.values())
    wins=sum(x["wins"] for x in stats.values()); losses=sum(x["losses"] for x in stats.values())
    coverage=(matched/len(rows)*100.0) if rows else 0.0
    daily_coverage=(len(matched_today)/len(today_total)*100.0) if today_total else 0.0
    for key,item in stats.items(): item["weight"]=round(weights[key],4)
    return {"processed_positions":matched,"closed_positions":len(rows),"unmatched_positions":max(0,len(rows)-matched),
        "wins":wins,"losses":losses,"flat":sum(x["flat"] for x in stats.values()),
        "net":sum(x["net"] for x in rows if x["strategy_id"]),
        "coverage_percent":round(coverage,1),"daily_closed_positions":len(today_total),
        "daily_processed_positions":len(matched_today),"daily_coverage_percent":round(daily_coverage,1),
        "updated_strategies":sum(1 for x in stats.values() if x["wins"]+x["losses"]>0),
        "weights":weights,"strategies":sorted(stats.values(),key=lambda x:(x["net"],x["trades"]),reverse=True),
        "combination_weights":combo_weights,"processed_combinations":sum(x["trades"] for x in combo_stats.values()),
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
        if path.startswith("/v1/terminal-control/settings/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
            rows=sb("GET","/rest/v1/alzool_engine_trade_settings?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=single_enabled,single_profit_target,group_enabled,group_profit_target,stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management&limit=1",admin=True)
            clients=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=trading_enabled&limit=1",admin=True)
            settings=rows[0] if rows else {"single_enabled":False,"single_profit_target":1,
                                           "group_enabled":False,"group_profit_target":5,
                                           "stop_loss_enabled":True,"stop_loss_mode":"ATR","stop_loss_value":1.8,"position_management":{"mode":"AI","addon_enabled":False,"addon_max":0,"addon_lot":0.01,"addon_distance_points":0,"addon_multiplier":1.5,"addon_max_lot":0.05,"cooling_enabled":False,"cooling_max":0,"cooling_lot":0.01,"cooling_distance_points":0,"cooling_multiplier":1.2,"cooling_max_lot":0.05,"max_total_lots":0.10}}
            pm=settings.get("position_management") if isinstance(settings.get("position_management"),dict) else {}
            settings["ai_exit"]=pm.get("ai_exit",{"enabled":True,"min_confidence":0.75,"confirm_bars":2})
            if client_id in CONNECTIONS:
                CONNECTIONS[client_id]["trade_settings_cache"]=settings
            settings["trading_enabled"]=bool(clients and clients[0].get("trading_enabled",False))
            return self.send_json(200,settings)
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
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                    client_id=(item or {}).get("client_id","")
                else:
                    lic=license_for_user(u["id"]); client_id=(lic or {}).get("client_id","")
                if not client_id: return self.send_json(409,{"error":"لا يوجد اتصال MT5 مرخّص لهذا الحساب"})
                rows=sb("GET","/rest/v1/alzool_engine_trade_settings?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                        "&select=single_enabled,single_profit_target,group_enabled,group_profit_target,stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management,updated_at&limit=1",admin=True)
                settings=rows[0] if rows else {"single_enabled":False,"single_profit_target":1,
                    "group_enabled":False,"group_profit_target":5,"stop_loss_enabled":True,
                    "stop_loss_mode":"ATR","stop_loss_value":1.8,"position_management":{"mode":"AI","addon_enabled":False,"addon_max":0,"addon_lot":0.01,"addon_distance_points":0,"addon_multiplier":1.5,"addon_max_lot":0.05,"cooling_enabled":False,"cooling_max":0,"cooling_lot":0.01,"cooling_distance_points":0,"cooling_multiplier":1.2,"cooling_max_lot":0.05,"max_total_lots":0.10},"updated_at":None}
                pm=settings.get("position_management") if isinstance(settings.get("position_management"),dict) else {}
                settings["ai_exit"]=pm.get("ai_exit",{"enabled":True,"min_confidence":0.75,"confirm_bars":2})
                return self.send_json(200,{"client_id":client_id,"settings":settings})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path in ("/health","/api/health"):
            return self.send_json(200,{"ok":True,"service":"AL-ZOOL MT5 Connection & Central Strategy Execution","mt5_connection_api":True,"trading_engine":True,"execution_location":"licensed MT5 terminal"})
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
        if path=="/api/backtest/learning":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    latest=sb("GET","/rest/v1/alzool_engine_market_candles?select=client_id&order=uploaded_at.desc&limit=1",admin=True)
                    client_id=latest[0].get("client_id","") if latest else ""
                    if not client_id:
                        latest_run=sb("GET","/rest/v1/alzool_engine_learning_runs?select=client_id&order=created_at.desc&limit=1",admin=True)
                        client_id=latest_run[0].get("client_id","") if latest_run else ""
                else:
                    lic=license_for_user(u["id"]); client_id=lic.get("client_id") if lic else ""
                if not client_id: return self.send_json(200,{"runs":[],"count":0})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit=max(1,min(100,int(finite_number((params.get("limit") or ["30"])[0],30))))
                rows=sb("GET","/rest/v1/alzool_engine_learning_runs?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=id,run_key,symbol,timeframe,period_days,candle_count,test_points,validation_points,lot_size,commission_per_lot,slippage_points,account_currency,summary,strategy_results,combination_results,diagnostics,created_at"+
                    "&order=created_at.desc&limit="+str(limit),admin=True)
                cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
                history=sb("GET","/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&deal_time=gte."+urllib.parse.quote(cutoff,safe="")+"&select=payload&order=deal_time.asc&limit=5000",admin=True)
                deals=[x.get("payload",{}) for x in history if isinstance(x,dict) and isinstance(x.get("payload"),dict)]
                live=live_learning_profile(deals)
                return self.send_json(200,{"runs":rows if isinstance(rows,list) else [],"count":len(rows) if isinstance(rows,list) else 0,"live":live})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:220]})
        if path.startswith("/v1/terminal-decision/"):
            client_id=path.rsplit("/",1)[-1]
            if not validate_license(client_id,token_of(self)):
                return self.send_json(403,{"error":"ترخيص الاتصال غير صالح أو منتهي"})
            item=CONNECTIONS.get(client_id)
            if not item or (datetime.now(timezone.utc)-item["received_at"]).total_seconds()>30:
                return self.send_json(409,{"error":"لا توجد بيانات MT5 حديثة"})
            decision=item.get("strategy_decision") or {}
            confidence=finite_number(decision.get("confidence",0.0))
            signal=str(decision.get("decision","WAIT"))
            strategy_id=int(finite_number(decision.get("strategy_id",0)))
            clients=sb("GET","/rest/v1/alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=trading_enabled&limit=1",admin=True)
            trading_enabled=bool(clients and clients[0].get("trading_enabled",False))
            ready=(trading_enabled and signal in ("BUY","SELL") and confidence>=0.45 and
                   1<=strategy_id<=200 and int(finite_number(decision.get("bar_time",0)))>0)
            bar_time=int(finite_number(decision.get("bar_time",0)))
            cached_settings=item.get("trade_settings_cache") or {}
            ai_exit=cached_settings.get("ai_exit") if isinstance(cached_settings.get("ai_exit"),dict) else {"enabled":True,"min_confidence":0.75,"confirm_bars":2}
            ai_exit_enabled=ai_exit.get("enabled") is True
            ai_exit_confidence=max(0.0,min(1.0,finite_number(ai_exit.get("min_confidence",0.75),0.75)))
            ai_exit_bars=max(1,min(10,int(finite_number(ai_exit.get("confirm_bars",2),2))))
            exit_watch=item.get("ai_exit_watch") or {"direction":"","count":0,"bar_time":0}
            if not ai_exit_enabled:
                exit_watch={"direction":"","count":0,"bar_time":bar_time}
                item["ai_exit_watch"]=exit_watch
            elif bar_time>int(exit_watch.get("bar_time",0)):
                if signal in ("BUY","SELL") and confidence>=ai_exit_confidence:
                    if signal==exit_watch.get("direction"):
                        exit_watch["count"]=int(exit_watch.get("count",0))+1
                    else:
                        exit_watch={"direction":signal,"count":1,"bar_time":bar_time}
                    exit_watch["bar_time"]=bar_time
                else:
                    exit_watch={"direction":"","count":0,"bar_time":bar_time}
                item["ai_exit_watch"]=exit_watch
            exit_signal=exit_watch.get("direction") if ai_exit_enabled and int(exit_watch.get("count",0))>=ai_exit_bars else ""
            return self.send_json(200,{"signal":signal if ready else ("WAIT" if signal in ("BUY","SELL") else signal),
                "exit_signal":exit_signal,"exit_bar_time":int(exit_watch.get("bar_time",0)),
                "exit_confidence":confidence if exit_signal else 0.0,
                "ai_exit_enabled":ai_exit_enabled,"ai_exit_min_confidence":ai_exit_confidence,
                "ai_exit_confirm_bars":ai_exit_bars,
                "confidence":confidence,"reason_ar":decision.get("reason_ar","بانتظار اكتمال بيانات التحليل"),
                "strategy_id":strategy_id,"strategy_name":decision.get("strategy_name",""),
                "combination_id":int(finite_number(decision.get("combination_id",0))),
                "bar_time":decision.get("bar_time",0),"execution_ready":ready,"trading_enabled":trading_enabled,
                "minimum_confidence":0.45,
                "message":"قرار جاهز للتنفيذ على MT5 بعد إغلاق شمعة التأكيد" if ready else ("التداول الآلي متوقف من لوحة التحكم" if not trading_enabled else "لا توجد إشارة مستوفية لشروط الدخول")})
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
        if path=="/api/opportunities/events":
            try:
                u=current_user(token_of(self))
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id and (u.get("email") or "").lower()==OWNER_EMAIL:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                    client_id=(item or {}).get("client_id","")
                if not client_id: return self.send_json(200,{"events":[],"count":0})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit=max(1,min(500,int(finite_number((params.get("limit") or ["200"])[0],200))))
                days=int(finite_number((params.get("days") or ["30"])[0],30))
                days=days if days in (0,7,30,90,365) else 30
                kind=str((params.get("kind") or ["opportunities"])[0])
                event_types={
                    "opportunities":["SIGNAL_CREATED","WAIT","BLOCKED"],
                    "trades":["TRADE_OPENED","TRADE_CLOSED","ORDER_SENT","ADDON_OPENED","COOLING_OPENED","PROTECTION","BLOCKED"]
                }.get(kind)
                query="/rest/v1/alzool_engine_signal_events?client_id=eq."+urllib.parse.quote(client_id,safe="")
                if event_types:
                    query+="&event_type=in."+urllib.parse.quote("("+",".join(event_types)+")",safe=",()")
                if days:
                    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
                    query+="&created_at=gte."+urllib.parse.quote(cutoff,safe="")
                query+="&select=event_id,event_type,payload,created_at&order=created_at.desc&limit="+str(limit)
                rows=sb("GET",query,admin=True)
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
                if not client_id and (u.get("email") or "").lower()==OWNER_EMAIL:
                    latest=sb("GET","/rest/v1/alzool_engine_closed_deals?select=client_id&order=deal_time.desc&limit=1",admin=True)
                    client_id=latest[0].get("client_id","") if latest else ""
                if not client_id: return self.send_json(200,{"trades":[],"count":0,"currency":""})
                params=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                days=int(finite_number((params.get("days") or ["30"])[0],30))
                days=days if days in (0,7,30,90,365) else 30
                query="/rest/v1/alzool_engine_closed_deals?client_id=eq."+urllib.parse.quote(client_id,safe="")
                if days:
                    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
                    query+="&deal_time=gte."+urllib.parse.quote(cutoff,safe="")
                query+="&select=deal_time,payload&order=deal_time.desc"
                records=[]; offset=0
                while len(records)<5000:
                    page=sb("GET",query+"&limit=1000&offset="+str(offset),admin=True)
                    if not isinstance(page,list) or not page: break
                    records.extend(page); offset+=len(page)
                    if len(page)<1000: break
                grouped={}
                for record in records[:5000]:
                    deal=record.get("payload") if isinstance(record.get("payload"),dict) else {}
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
                        row["side"]="شراء" if deal_type==0 else "بيع" if deal_type==1 else row["side"]
                        row["side_priority"]=2
                        row["open_volume"]+=volume
                    elif entry in (1,3):
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
                wins=sum(1 for x in trades if x["net"]>0)
                return self.send_json(200,{"trades":trades,"count":len(trades),"deal_rows":len(records),
                    "win_rate":(wins/len(trades)*100) if trades else None,
                    "net":sum(x["net"] for x in trades),"currency":currency,"days":days})
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
                return self.send_json(200,{"saved":True,"trading_enabled":enabled,"client_id":lic["client_id"],
                    "message":"تم تفعيل التداول الآلي" if enabled else "تم إيقاف فتح صفقات جديدة"})
            if path=="/api/trade-management/settings":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                    client_id=(item or {}).get("client_id","")
                else:
                    lic=license_for_user(u["id"]); client_id=(lic or {}).get("client_id","")
                if not client_id: return self.send_json(409,{"error":"لا يوجد اتصال MT5 مرخّص لهذا الحساب"})
                single_enabled=data.get("single_enabled") is True
                group_enabled=data.get("group_enabled") is True
                single_target=finite_number(data.get("single_profit_target"),0)
                group_target=finite_number(data.get("group_profit_target"),0)
                if not math.isfinite(single_target) or not math.isfinite(group_target) or single_target<=0 or group_target<=0:
                    return self.send_json(400,{"error":"أهداف الربح يجب أن تكون أرقامًا موجبة وبعملة الحساب"})
                if single_target>100000000 or group_target>100000000:
                    return self.send_json(400,{"error":"قيمة هدف الربح أكبر من الحد المسموح"})
                existing=sb("GET","/rest/v1/alzool_engine_trade_settings?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                    "&select=stop_loss_enabled,stop_loss_mode,stop_loss_value,position_management&limit=1",admin=True)
                prior=existing[0] if existing else {"stop_loss_enabled":True,"stop_loss_mode":"ATR","stop_loss_value":1.8}
                stop_loss_enabled=data.get("stop_loss_enabled",prior.get("stop_loss_enabled")) is True
                stop_loss_mode=str(data.get("stop_loss_mode",prior.get("stop_loss_mode","ATR"))).upper()
                stop_loss_value=finite_number(data.get("stop_loss_value",prior.get("stop_loss_value",1.8)),0)
                if stop_loss_mode not in ("POINTS","ATR"):
                    return self.send_json(400,{"error":"نوع وقف الخسارة غير صالح"})
                if not math.isfinite(stop_loss_value) or stop_loss_value<=0 or stop_loss_value>1000000:
                    return self.send_json(400,{"error":"قيمة وقف الخسارة يجب أن تكون أكبر من صفر"})
                prior_pm=prior.get("position_management") if isinstance(prior.get("position_management"),dict) else {}
                pm=data.get("position_management",prior_pm)
                if not isinstance(pm,dict):
                    return self.send_json(400,{"error":"إعدادات التعزيز والتبريد غير صالحة"})
                mode=str(pm.get("mode",prior_pm.get("mode","AI"))).upper()
                if mode not in ("AI","MANUAL"):
                    return self.send_json(400,{"error":"اختر وضع AI أو يدوي"})
                clean_pm={"mode":mode}
                for prefix in ("addon","cooling"):
                    enabled=pm.get(prefix+"_enabled",prior_pm.get(prefix+"_enabled",False)) is True
                    maximum=int(finite_number(pm.get(prefix+"_max",prior_pm.get(prefix+"_max",0)),-1))
                    lot=finite_number(pm.get(prefix+"_lot",prior_pm.get(prefix+"_lot",0.01)),0)
                    distance=finite_number(pm.get(prefix+"_distance_points",prior_pm.get(prefix+"_distance_points",0)),0)
                    if maximum<0 or maximum>100 or lot<=0 or lot>100 or distance<0 or distance>100000000:
                        return self.send_json(400,{"error":"تحقق من الحد الأقصى واللوت والمسافة في إعدادات التعزيز والتبريد"})
                    if enabled and maximum<1:
                        return self.send_json(400,{"error":"حدد الحد الأقصى لعدد الإضافات قبل تفعيل التعزيز أو التبريد"})
                    if mode=="MANUAL" and enabled and distance<=0:
                        return self.send_json(400,{"error":"في الوضع اليدوي يجب تحديد مسافة أكبر من صفر بالنقاط"})
                    multiplier=finite_number(pm.get(prefix+"_multiplier",prior_pm.get(prefix+"_multiplier",1.5 if prefix=="addon" else 1.2)),0)
                    max_lot=finite_number(pm.get(prefix+"_max_lot",prior_pm.get(prefix+"_max_lot",0.05)),0)
                    if not 1.0<=multiplier<=3.0 or not 0.01<=max_lot<=100:
                        return self.send_json(400,{"error":"المضاعف يجب أن يكون بين 1 و3، والحد الأقصى للوت بين 0.01 و100"})
                    clean_pm[prefix+"_enabled"]=enabled
                    clean_pm[prefix+"_max"]=maximum
                    clean_pm[prefix+"_lot"]=lot
                    clean_pm[prefix+"_distance_points"]=distance
                    clean_pm[prefix+"_multiplier"]=multiplier
                    clean_pm[prefix+"_max_lot"]=max_lot
                max_total_lots=finite_number(pm.get("max_total_lots",prior_pm.get("max_total_lots",0.10)),0)
                if not 0.01<=max_total_lots<=100:
                    return self.send_json(400,{"error":"سقف إجمالي اللوتات يجب أن يكون بين 0.01 و100"})
                clean_pm["max_total_lots"]=max_total_lots
                ai_exit=data.get("ai_exit",prior_pm.get("ai_exit",{"enabled":True,"min_confidence":0.75,"confirm_bars":2}))
                if not isinstance(ai_exit,dict):
                    return self.send_json(400,{"error":"إعدادات القفلة الذكية غير صالحة"})
                ai_exit_enabled=ai_exit.get("enabled",True) is True
                ai_exit_confidence=finite_number(ai_exit.get("min_confidence",0.75),0)
                ai_exit_bars=int(finite_number(ai_exit.get("confirm_bars",2),0))
                if not 0.50<=ai_exit_confidence<=0.99 or not 1<=ai_exit_bars<=10:
                    return self.send_json(400,{"error":"تحقق من نسبة الثقة وعدد شموع التأكيد للقفلة الذكية"})
                clean_pm["ai_exit"]={"enabled":ai_exit_enabled,"min_confidence":ai_exit_confidence,"confirm_bars":ai_exit_bars}
                row={"client_id":client_id,"single_enabled":single_enabled,
                     "single_profit_target":single_target,"group_enabled":group_enabled,
                     "group_profit_target":group_target,"stop_loss_enabled":stop_loss_enabled,
                     "stop_loss_mode":stop_loss_mode,"stop_loss_value":stop_loss_value,
                     "position_management":clean_pm,
                     "updated_at":datetime.now(timezone.utc).isoformat()}
                saved=sb("POST","/rest/v1/alzool_engine_trade_settings?on_conflict=client_id",row,admin=True,
                         prefer="resolution=merge-duplicates,return=representation")
                return self.send_json(200,{"saved":True,"settings":saved[0] if isinstance(saved,list) and saved else row})
            if path=="/api/backtest/learning":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                if (u.get("email") or "").lower()==OWNER_EMAIL:
                    latest=sb("GET","/rest/v1/alzool_engine_market_candles?select=client_id&order=uploaded_at.desc&limit=1",admin=True)
                    client_id=latest[0].get("client_id","") if latest else ""
                    if not client_id:
                        latest_run=sb("GET","/rest/v1/alzool_engine_learning_runs?select=client_id&order=created_at.desc&limit=1",admin=True)
                        client_id=latest_run[0].get("client_id","") if latest_run else ""
                else:
                    lic=license_for_user(u["id"]); client_id=lic.get("client_id") if lic else ""
                if not client_id: return self.send_json(409,{"error":"لا يوجد ترخيص مرتبط لحفظ ذاكرة التعلم"})
                run=data.get("run") if isinstance(data.get("run"),dict) else {}
                run_key=str(run.get("run_key") or "")[:80]
                symbol=str(run.get("symbol") or "")[:40]
                timeframe=str(run.get("timeframe") or "")[:20]
                strategies=run.get("strategy_results") if isinstance(run.get("strategy_results"),list) else []
                combinations=run.get("combination_results") if isinstance(run.get("combination_results"),list) else []
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
                return self.send_json(200,{"saved":True,"run":saved[0] if isinstance(saved,list) and saved else {"run_key":run_key}})
            if path=="/api/opportunities/events":
                u=current_user(token)
                if banned(u): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                lic=license_for_user(u["id"])
                client_id=(lic or {}).get("client_id","")
                if not client_id and (u.get("email") or "").lower()==OWNER_EMAIL:
                    item=max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)
                    client_id=(item or {}).get("client_id","")
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
                closed_deals=clean_telemetry_rows(data.get("closed_deals"),("ticket","position_id","symbol","type","deal_type","entry","reason","volume","price","profit","commission","swap","fee","magic","comment","strategy_id","strategy_comment","time"),200)
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
                previous=CONNECTIONS.get(client_id,{})
                strategy_decision=previous.get("strategy_decision")
                strategy_last_bar=previous.get("strategy_last_bar")
                # MT5 sends closed candles newest-first. Evaluate once per newly closed bar.
                if len(candles)>=65 and str(data.get("symbol","")).upper().startswith("XAUUSD"):
                    bar_time=int(candles[0].get("time") or 0)
                    if bar_time>0 and bar_time!=strategy_last_bar:
                        try:
                            live_learning=live_learning_profile(closed_deals)
                            raw_mtf=data.get("mtf_bias") if isinstance(data.get("mtf_bias"),dict) else {}
                            mtf_bias={k:str(v).upper() for k,v in raw_mtf.items() if k in ("M5","M15","H1") and str(v).upper() in ("BUY","SELL","WAIT")}
                            evaluated=evaluate_market(list(reversed(candles)),weights=live_learning["weights"],combination_weights=live_learning.get("combination_weights"),mtf_bias=mtf_bias,mode="AUTO")
                            strategy_decision={
                                "decision":evaluated["decision"],
                                "strategy_id":evaluated["strategy_id"],
                                "strategy_name":evaluated["strategy_name"],
                                "combination_id":evaluated.get("combination_id",0),
                                "reason_ar":evaluated["reason_ar"],
                                "confidence":evaluated["confidence"],
                                "context":evaluated["context"],
                                "adaptive":evaluated["adaptive"],
                                "council":evaluated["council"],
                                "vote_summary":evaluated["vote_summary"],
                                "combination_summary":evaluated["combination_summary"],
                                "strategy_count":evaluated["strategy_count"],
                                "combination_count":evaluated["combination_count"],
                                "bar_time":bar_time,
                                "generated_at":now.isoformat(),
                                "execution_ready":False
                            }
                            strategy_last_bar=bar_time
                        except Exception as engine_error:
                            print("CENTRAL_ENGINE_WARNING:",str(engine_error)[:250],flush=True)
                CONNECTIONS[client_id]={"client_id":client_id,"account":safe,"connected":True,
                    "symbol":str(data.get("symbol",""))[:40],"timeframe":str(data.get("timeframe",""))[:20],
                    "terminal_build":int(finite_number(data.get("terminal_build",0))),
                    "positions":positions,"orders":orders,"closed_deals":closed_deals,"closed_candles":candles,
                    "forming_candle":forming,"tick":safe_tick,"strategy_decision":strategy_decision,
                    "strategy_last_bar":strategy_last_bar,"ai_exit_watch":previous.get("ai_exit_watch"),
                    "received_at":now,"last_seen":now.isoformat()}
                return self.send_json(200,{"accepted":True,"telemetry":True,
                    "positions_received":len(positions),"orders_received":len(orders),
                    "closed_deals_received":len(closed_deals),
                    "candles_received":len(candles),
                    "strategy_engine":"ready" if strategy_decision else "waiting_for_65_closed_XAUUSD_candles"})
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
