"""One public URL, two isolated market workers. Owner-only staging pilot.

The gateway never issues a broker order. It mediates the existing EA's entry
and action permits, retaining local profit/exit management when switching.
On a restart the entry selector intentionally returns to OFF; an owner must
select a ready market again. Never expose the worker ports publicly.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from multi_market_switch import MarketSwitch, Terminal, MARKETS

ROOT=Path(__file__).resolve().parents[2]
WORKER_PORTS={"BTCUSD":10001,"XAUUSD":10002}
SWITCH=MarketSwitch()
PERMIT_LOCK=threading.RLock()
OWNER_TERMINALS={}
BOOT_TIME=time.monotonic()
MAX_BODY=2100000

def route(path):
    parsed=urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/") or "\\" in parsed.path or "%" in parsed.path or "//" in parsed.path:
        raise ValueError("Invalid path")
    parts=parsed.path.split("/")
    if any(x in (".","..") for x in parts):raise ValueError("Invalid path")
    if parsed.path.startswith("/market/"):
        if len(parts)<4 or parts[2] not in MARKETS:raise ValueError("Invalid market")
        return parts[2],"/"+"/".join(parts[3:])+("?" + parsed.query if parsed.query else "")
    return "BTCUSD",path

def request(market,path,method="GET",body=None,headers=None):
    # No user-supplied host, port or forwarding headers.
    req=urllib.request.Request(
        "http://127.0.0.1:"+str(WORKER_PORTS[market])+path,
        data=body,method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:
            return r.status,r.headers.get("Content-Type","application/octet-stream"),r.read()
    except urllib.error.HTTPError as e:
        return e.code,e.headers.get("Content-Type","application/json"),e.read()

def decode(data):
    try:
        obj=json.loads(data)
        return obj if isinstance(obj,dict) else {}
    except (ValueError,TypeError,UnicodeError):return {}

def json_response(status,data):
    return status,"application/json; charset=utf-8",json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()

def terminal_id(path):
    return urllib.parse.urlsplit(path).path.rsplit("/",1)[-1]

def record_heartbeat(market,path,payload,response):
    if response.get("accepted") is not True or response.get("market_owner_terminal") is not True:return
    sym=str(payload.get("symbol") or "")
    acct=payload.get("account") or {}
    positions=payload.get("positions") or []
    orders=payload.get("orders") or []
    terminal=Terminal(sym,str(acct.get("login") or ""),str(acct.get("server") or ""),
        time.time(),len(positions),payload.get("mt5_central_trading_enabled") is True,
        sum(1 for p in positions if not isinstance(p,dict) or p.get("symbol")!=sym),
        sum(1 for p in orders if not isinstance(p,dict) or p.get("symbol")!=sym))
    if terminal.market!=market:return
    with PERMIT_LOCK:
        OWNER_TERMINALS[market]=terminal_id(path)
        SWITCH.observe(terminal)

def blocked(path):
    return json_response(409,{"execution_ready":False,"execution_authorized":False,
        "signal":"WAIT","trading_enabled":False,
        "error":"السوق غير مختار للتنفيذ أو الاتصال غير جاهز",
        "reason_ar":"السوق غير مختار للتنفيذ أو الاتصال غير جاهز"})

def permit_request(market,path,method,body,headers):
    with PERMIT_LOCK:
        # Only the owner terminal whose heartbeat was verified by its worker.
        if OWNER_TERMINALS.get(market)!=terminal_id(path) or not SWITCH.entry_allowed(market):
            return blocked(path)
        status,kind,raw=request(market,path,method,body,headers)
        data=decode(raw)
        if status==200 and (data.get("execution_ready") is True or data.get("execution_authorized") is True):
            if path.startswith("/v1/terminal-decision/") or path.startswith("/v1/terminal-action-claim/"):
                # Record the expiry BEFORE releasing the shared handover lock.
                until=data.get("authorization_expires_at") if path.startswith("/v1/terminal-decision/") else data.get("expires_at_unix")
                try:
                    if not SWITCH.issue_entry(market,expires_at=float(until)):return blocked(path)
                except (ValueError,TypeError,OverflowError):return blocked(path)
        return status,kind,raw

def owner_states(headers):
    states={}
    for market in MARKETS:
        status,_,raw=request(market,"/api/multi-market/owner-state",headers=headers)
        data=decode(raw)
        if status!=200 or data.get("owner") is not True:return None
        states[market]=data
    return states

def selection_response(headers,wanted=None):
    states=owner_states(headers)
    if states is None:return json_response(403,{"error":"تعذر التحقق من صلاحية المالك"})
    with PERMIT_LOCK:
        report=SWITCH.status()
        if wanted is not None:
            # Refuse a selection until startup blackout and account checks pass.
            if wanted not in MARKETS:return json_response(400,{"error":"سوق غير معتمد"})
            if time.monotonic()-BOOT_TIME<20:
                return json_response(409,{"error":"تهيئة بوابة التداول بعد إعادة التشغيل",**report})
            if states[wanted].get("ready") is not True or not report["markets"][wanted]["ready"]:
                return json_response(409,{"error":"اربط MT5 للسوق المختار على حساب منفصل أولاً",**report})
            report=SWITCH.choose(wanted)
            if not report.get("accepted"):return json_response(409,{"error":"السوق غير جاهز أو الحساب مشترك",**report})
        for market in MARKETS:
            report["markets"][market]["paired"]=states[market].get("paired",False)
            report["markets"][market]["trading_enabled"]=states[market].get("trading_enabled",False)
        return json_response(200,report)

class Gateway(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print("MARKET_GATEWAY:",fmt%args,flush=True)
    def _send(self,res):
        status,kind,raw=res
        # Compress only the final HTTP response, never the permit parser's
        # internal worker response or a native MT5 JSON response.
        from bandwidth_payload import negotiate_payload
        raw,compressed=negotiate_payload(raw,self.path,self.headers.get("Accept-Encoding",""),kind)
        self.send_response(status)
        self.send_header("Content-Type",kind)
        self.send_header("Vary","Accept-Encoding")
        if compressed:self.send_header("Content-Encoding","gzip")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","SAMEORIGIN")
        self.send_header("Content-Security-Policy","frame-ancestors 'self'")
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def _handle(self):
        try:
            market,path=route(self.path)
            bare=urllib.parse.urlsplit(path).path
            length=int(self.headers.get("Content-Length","0"))
            if not 0<=length<=MAX_BODY:return self._send(json_response(413,{"error":"حجم الطلب غير مسموح"}))
            body=self.rfile.read(length) if length else None
            headers={k:self.headers[k] for k in ("Authorization","Content-Type") if self.headers.get(k)}
            if path=="/api/multi-market/status" and self.command=="GET":
                return self._send(selection_response(headers))
            if path=="/api/multi-market/select" and self.command=="POST":
                wanted=decode(body).get("market")
                if wanted not in MARKETS:return self._send(json_response(400,{"error":"سوق غير معتمد"}))
                return self._send(selection_response(headers,wanted))
            if bare=="/api/multi-market/owner-state":
                return self._send(json_response(404,{"error":"not_found"}))
            if bare=="/multi-market-ui.js" and self.command=="GET":
                return self._send((200,"application/javascript; charset=utf-8",(ROOT/"multi-market-ui.js").read_bytes()))
            if bare.startswith(("/v1/terminal-decision/","/v1/terminal-actions/","/v1/terminal-action-claim/")):
                return self._send(permit_request(market,path,self.command,body,headers))
            response=request(market,path,self.command,body,headers)
            status,kind,raw=response
            if bare.startswith("/v1/terminal-state/live/") and self.command=="POST" and status==200:
                record_heartbeat(market,path,decode(body),decode(raw))
            # Browser chrome is injected into every real HTML page before its
            # scripts. Existing form IDs and event handlers remain intact.
            if status==200 and "text/html" in kind and self.command=="GET":
                raw=raw.replace(b"</head>",b'<script src="/multi-market-ui.js"></script></head>',1)
                response=status,kind,raw
            return self._send(response)
        except ValueError:return self._send(json_response(400,{"error":"طلب غير صالح"}))
        except Exception as exc:
            print("MARKET_GATEWAY_ERROR:",type(exc).__name__,flush=True)
            return self._send(json_response(503,{"error":"محرك السوق غير متاح"}))
    do_GET=do_POST=do_PATCH=do_DELETE=_handle

def run():
    # A staging/pilot entrypoint. Deployment must opt in explicitly.
    if os.getenv("ALZOOL_ENABLE_DUAL_PILOT")!="1":
        raise RuntimeError("Dual-market pilot is not enabled")
    children=[]
    try:
        for market,port in WORKER_PORTS.items():
            public=os.getenv("PUBLIC_ENGINE_URL","").rstrip("/")
            env={**os.environ,"ALZOOL_MULTI_MARKET":"1","ALZOOL_ENGINE_MARKET":market,
                "ALZOOL_BIND_HOST":"127.0.0.1","PORT":str(port),
                "PUBLIC_ENGINE_URL":public+("/market/XAUUSD" if market=="XAUUSD" else "")}
            # Import only after setting the worker lane. Normal single-market
            # app startup tests are run by CI before pilot deployment.
            children.append(subprocess.Popen([sys.executable,"-c",
                "from app import Handler;from http.server import ThreadingHTTPServer;import os;ThreadingHTTPServer(('127.0.0.1',int(os.environ['PORT'])),Handler).serve_forever()"],
                cwd=str(Path(__file__).parent),env=env))
        ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),Gateway).serve_forever()
    finally:
        for child in children:child.terminate()
        for child in children:child.wait(timeout=5)

if __name__=="__main__":run()
