"""AL-ZOOL web gateway: static portal + Supabase Auth customer administration."""
import json, os, re, secrets, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from server_engine.engine import FAMILIES, STRATEGIES, COMBINATIONS
from server_engine.supabase_gateway import EngineGateway
SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_ANON_KEY=os.getenv("SUPABASE_ANON_KEY","")
SUPABASE_SERVICE_ROLE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
OWNER_EMAIL=os.getenv("OWNER_EMAIL","Meenosvn1@gmail.com").strip().lower()
OWNER_PASSWORD=os.getenv("OWNER_PASSWORD","")
PUBLIC_ENGINE_URL=os.getenv("PUBLIC_ENGINE_URL","https://al-zool-gold-sniper.onrender.com").rstrip("/")
ENGINE=EngineGateway(SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY)

def sb(method,path,payload=None,bearer=None,admin=False):
    if not SUPABASE_URL: raise RuntimeError("SUPABASE_URL is not configured")
    key=SUPABASE_SERVICE_ROLE_KEY if admin else SUPABASE_ANON_KEY
    if not key: raise RuntimeError("Supabase key is not configured")
    headers={"apikey":key,"Accept":"application/json","Authorization":"Bearer "+(SUPABASE_SERVICE_ROLE_KEY if admin else (bearer or key))}
    data=None
    if payload is not None:
        headers["Content-Type"]="application/json"; data=json.dumps(payload).encode()
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
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and OWNER_PASSWORD): return
    try:
        users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
        if any((u.get("email") or "").lower()==OWNER_EMAIL for u in users): return
        sb("POST","/auth/v1/admin/users",{"email":OWNER_EMAIL,"password":OWNER_PASSWORD,"email_confirm":True,"user_metadata":{"name":"AL-ZOOL OWNER","role":"owner"}},admin=True)
        print("Owner account created.")
    except Exception as e: print("OWNER_BOOTSTRAP_WARNING:",str(e))

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

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,fmt,*args): print("WEB:",fmt%args)
    def send_json(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): print("CLIENT_DISCONNECTED_BEFORE_RESPONSE: "+str(self.path))
    def read_json(self):
        n=int(self.headers.get("Content-Length","0"))
        if n>1000000: raise ValueError("حجم الطلب كبير")
        return json.loads(self.rfile.read(n) or b"{}")
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Allow","GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods","GET, POST, PATCH, DELETE, OPTIONS"); self.end_headers()
    def do_GET(self):
        if self.path=="/api/health": return self.send_json(200,{"ok":True,"service":"AL-ZOOL Web"})
        if self.path=="/health":
            try: return self.send_json(200,ENGINE.health())
            except Exception as e: return self.send_json(503,{"service":"AL-ZOOL central engine","status":"setup_required","error":str(e)[:180]})
        parts=urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if len(parts)==3 and parts[0]=="v1" and parts[1]=="control":
            try: return self.send_json(200,ENGINE.trading_control(parts[2],token_of(self)))
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(503,{"error":str(e)[:200]})
        if len(parts)==3 and parts[0]=="v1" and parts[1] in ("decision","analysis"):
            try:
                result=ENGINE.latest(parts[2],token_of(self),"analysis" if parts[1]=="analysis" else "decision")
                return self.send_json(200,result)
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(500,{"error":str(e)[:250]})
        if self.path=="/api/engine/workspace":
            try:
                u=current_user(token_of(self))
                try: fresh=admin_user(u["id"])
                except Exception: fresh=u
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                return self.send_json(200,ENGINE.workspace_for_user(u["id"],limit=100))
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:200]})
        if self.path=="/api/engine/status":
            try:
                u=current_user(token_of(self))
                try: fresh=admin_user(u["id"])
                except Exception: fresh=u
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                return self.send_json(200,ENGINE.status_for_user(u["id"],stale_after=30))
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)[:200]})
        if self.path=="/api/config":
            return self.send_json(200,{"supabase_url":SUPABASE_URL,"supabase_anon_key":SUPABASE_ANON_KEY,"owner_email":OWNER_EMAIL,"ready":bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY)})
        if self.path=="/api/engine/catalog":
            try:
                u=current_user(token_of(self))
                try: fresh=admin_user(u["id"])
                except Exception: fresh=u
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                return self.send_json(200,{
                    "families":[{"name":name,"kind":kind} for name,kind in FAMILIES],
                    "strategies":[{"id":s["id"],"name":s["name"],"family":s["family"],"fast":s["fast"],"slow":s["slow"],"period":s["period"]} for s in STRATEGIES],
                    "combinations":[{"id":x["id"],"name":x["name"],"members":list(x["members"])} for x in COMBINATIONS]
                })
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(500,{"error":str(e)[:250]})
        if self.path=="/api/admin/clients":
            try:
                owner(token_of(self))
                users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
                rows=[]
                for u in users:
                    email=(u.get("email") or "").lower()
                    if not email or email==OWNER_EMAIL: continue
                    rows.append({"id":u.get("id"),"email":email,"name":(u.get("user_metadata") or {}).get("name",""),"created_at":u.get("created_at"),"active":not banned(u)})
                return self.send_json(200,{"clients":rows})
            except PermissionError as e: return self.send_json(403,{"error":str(e)})
            except Exception as e: return self.send_json(502,{"error":str(e)})
        return super().do_GET()
    def do_POST(self):
        try:
            data=self.read_json(); token=token_of(self)
            if len(urllib.parse.urlparse(self.path).path.strip("/").split("/"))==3 and urllib.parse.urlparse(self.path).path.strip("/").split("/")[0:2]==["v1","market"]:
                client_id=urllib.parse.urlparse(self.path).path.strip("/").split("/")[2]
                result=ENGINE.market(client_id,token_of(self),data)
                return self.send_json(200,result)
            if len(urllib.parse.urlparse(self.path).path.strip("/").split("/"))==3 and urllib.parse.urlparse(self.path).path.strip("/").split("/")[0:2]==["v1","terminal-state"]:
                client_id=urllib.parse.urlparse(self.path).path.strip("/").split("/")[2]
                return self.send_json(200,ENGINE.terminal_state(client_id,token_of(self),data))
            if len(urllib.parse.urlparse(self.path).path.strip("/").split("/"))==3 and urllib.parse.urlparse(self.path).path.strip("/").split("/")[0:2]==["v1","outcome"]:
                client_id=urllib.parse.urlparse(self.path).path.strip("/").split("/")[2]
                return self.send_json(200,ENGINE.outcome(client_id,token_of(self),data))
            if self.path=="/api/engine/control":
                u=current_user(token)
                try: fresh=admin_user(u["id"])
                except Exception: fresh=u
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                enabled=data.get("enabled")
                if not isinstance(enabled,bool): return self.send_json(400,{"error":"enabled must be true or false"})
                ENGINE.set_user_trading_enabled(u["id"],enabled)
                return self.send_json(200,{"ok":True,"trading_enabled":enabled})
            if self.path=="/api/owner/engine-credentials":
                u=owner(token)
                credentials=ENGINE.issue(u.get("id"))
                return self.send_json(201,{"engine":credentials,"engine_server_url":PUBLIC_ENGINE_URL,
                    "message":"تم إصدار بيانات ربط محرك المالك. احتفظ بها بسرية."})
            if self.path=="/api/access/check":
                u=current_user(token)
                try: fresh=admin_user(u["id"])
                except Exception: fresh=u
                if banned(fresh): return self.send_json(403,{"error":"تم إيقاف هذا الحساب من الإدارة"})
                email=(u.get("email") or "").lower()
                return self.send_json(200,{"ok":True,"role":"owner" if email==OWNER_EMAIL else "client","email":email})
            if self.path=="/api/admin/clients":
                owner(token); email=(data.get("email") or "").strip().lower(); name=(data.get("name") or "").strip()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): return self.send_json(400,{"error":"أدخل بريداً إلكترونياً صحيحاً للعميل"})
                if email==OWNER_EMAIL: return self.send_json(400,{"error":"لا يمكن إنشاء العميل ببريد المالك"})
                password=secrets.token_urlsafe(12)+"A7!"
                u=sb("POST","/auth/v1/admin/users",{"email":email,"password":password,"email_confirm":True,"user_metadata":{"name":name,"role":"client"}},admin=True)
                try:
                    engine_credentials=ENGINE.issue(u.get("id"))
                except Exception:
                    try: sb("DELETE","/auth/v1/admin/users/"+urllib.parse.quote(u.get("id",""),safe=""),admin=True)
                    except Exception: pass
                    raise
                return self.send_json(201,{"id":u.get("id"),"email":email,"name":name,"password":password,"engine":engine_credentials,"engine_server_url":PUBLIC_ENGINE_URL,"message":"تم إنشاء الحساب وصلاحية محرك التداول. احفظ بيانات الدخول والربط الآن."})
            return self.send_json(404,{"error":"not_found"})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:300]})
    def do_PATCH(self):
        try:
            owner(token_of(self)); prefix="/api/admin/clients/"
            if not self.path.startswith(prefix): return self.send_json(404,{"error":"not_found"})
            uid=self.path[len(prefix):]
            if not re.fullmatch(r"[0-9a-fA-F-]{20,}",uid): return self.send_json(400,{"error":"معرّف الحساب غير صحيح"})
            active=bool(self.read_json().get("active"))
            sb("PUT","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),{"ban_duration":"none" if active else "876000h"},admin=True)
            ENGINE.set_user_revoked(uid,not active)
            return self.send_json(200,{"ok":True,"active":active})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:300]})
    def do_DELETE(self):
        try:
            owner(token_of(self)); prefix="/api/admin/clients/"
            if not self.path.startswith(prefix): return self.send_json(404,{"error":"not_found"})
            uid=self.path[len(prefix):]
            if not re.fullmatch(r"[0-9a-fA-F-]{20,}",uid): return self.send_json(400,{"error":"معرّف الحساب غير صحيح"})
            target=admin_user(uid)
            if (target.get("email") or "").lower()==OWNER_EMAIL: return self.send_json(403,{"error":"لا يمكن حذف حساب المالك"})
            ENGINE.set_user_revoked(uid,True)
            sb("DELETE","/auth/v1/admin/users/"+urllib.parse.quote(uid,safe=""),admin=True)
            return self.send_json(200,{"ok":True,"deleted":True})
        except PermissionError as e: return self.send_json(403,{"error":str(e)})
        except Exception as e: return self.send_json(400,{"error":str(e)[:300]})
    def end_headers(self):
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options","DENY"); super().end_headers()

if __name__=="__main__":
    bootstrap_owner(); port=int(os.getenv("PORT","10000"))
    print("AL-ZOOL web listening on",port)
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
