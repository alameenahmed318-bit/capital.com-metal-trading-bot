"""Supabase-backed authentication and persistence for the AL-ZOOL central engine."""
from __future__ import annotations
import hashlib, hmac, json, math, secrets, time, urllib.parse, urllib.request, urllib.error, uuid
from datetime import datetime, timezone, timedelta
from .engine import evaluate_market

TTL_SECONDS = 12

class EngineGateway:
    def __init__(self, supabase_url: str, service_key: str):
        self.url = (supabase_url or "").rstrip("/")
        self.key = service_key or ""

    def _request(self, method, path, payload=None, prefer=None):
        if not self.url or not self.key:
            raise RuntimeError("Supabase engine storage is not configured")
        headers={"apikey":self.key,"Authorization":"Bearer "+self.key,"Accept":"application/json"}
        if payload is not None:
            headers["Content-Type"]="application/json"
        if prefer:
            headers["Prefer"]=prefer
        data=json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode() if payload is not None else None
        req=urllib.request.Request(self.url+"/rest/v1/"+path,data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as response:
                raw=response.read()
                return json.loads(raw.decode()) if raw else []
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode("utf-8","replace")
            raise RuntimeError("Supabase engine storage error: "+detail[:250]) from None

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, user_id, days=3650):
        client_id=uuid.uuid4().hex
        token=secrets.token_urlsafe(40)
        expires=datetime.now(timezone.utc)+timedelta(days=days)
        self._request("POST","alzool_engine_clients",{
            "client_id":client_id,"user_id":user_id,"token_hash":self._hash(token),
            "expires_at":expires.isoformat(),"revoked":False
        },"return=representation")
        self._request("POST","alzool_engine_weights",
            [{"client_id":client_id,"strategy_id":i,"weight":1.0} for i in range(1,201)],
            "resolution=ignore-duplicates,return=minimal")
        return {"client_id":client_id,"client_token":token,"expires_at":expires.isoformat()}

    def set_user_revoked(self, user_id, revoked):
        self._request("PATCH","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe=""),
                      {"revoked":bool(revoked)},"return=minimal")

    def _client(self, client_id, token):
        rows=self._request("GET","alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                           "&select=user_id,token_hash,expires_at,revoked")
        if not rows:
            return None
        row=rows[0]
        try:
            expiry=datetime.fromisoformat(row["expires_at"].replace("Z","+00:00")).timestamp()
        except Exception:
            return None
        if row["revoked"] or expiry<=time.time() or not hmac.compare_digest(self._hash(token),row["token_hash"]):
            return None
        return row

    @staticmethod
    def _validate_market(data):
        if not isinstance(data,dict) or data.get("symbol")!="XAUUSD":
            raise ValueError("symbol must be XAUUSD")
        candles=data.get("candles")
        if not isinstance(candles,list) or not 65<=len(candles)<=300:
            raise ValueError("candles must contain 65..300 closed bars")
        normalized=[]; previous=0
        for item in candles:
            if not isinstance(item,dict): raise ValueError("invalid candle")
            try:
                c={"time":int(item["time"])}
                for k in ("open","high","low","close"): c[k]=float(item[k])
                if "tick_volume" in item: c["tick_volume"]=int(item["tick_volume"])
            except (KeyError,TypeError,ValueError,OverflowError): raise ValueError("invalid candle fields") from None
            vals=[c[k] for k in ("open","high","low","close")]
            if c["time"]<=previous or c["time"]<=0 or not all(math.isfinite(v) and 0<v<1e12 for v in vals):
                raise ValueError("invalid candle time or price")
            if c["high"]<max(c["open"],c["close"],c["low"]) or c["low"]>min(c["open"],c["close"],c["high"]):
                raise ValueError("inconsistent OHLC candle")
            if c.get("tick_volume",-1)<0: raise ValueError("invalid tick volume")
            previous=c["time"]; normalized.append(c)
        try: bid,ask=float(data["bid"]),float(data["ask"])
        except (KeyError,TypeError,ValueError,OverflowError): raise ValueError("valid bid and ask are required") from None
        if not math.isfinite(bid) or not math.isfinite(ask) or bid<=0 or ask<bid:
            raise ValueError("valid bid and ask are required")
        return normalized,bid,ask

    def market(self, client_id, token, data):
        client=self._client(client_id,token)
        if not client: raise PermissionError("client authorization failed")
        rates,_,_=self._validate_market(data)
        mtf=data.get("mtf_bias",{})
        if not isinstance(mtf,dict) or any(k not in ("M5","M15","H1") or v not in ("BUY","SELL","WAIT") for k,v in mtf.items()):
            raise ValueError("mtf_bias must contain only M5/M15/H1 BUY/SELL/WAIT values")
        mode=str(data.get("strategy_mode","AUTO")).upper()
        if mode not in ("AUTO","HUNTER","SNIPER"): raise ValueError("invalid strategy_mode")
        rows=self._request("GET","alzool_engine_weights?client_id=eq."+client_id+"&select=strategy_id,weight")
        weights={str(r["strategy_id"]):float(r["weight"]) for r in rows}
        result=evaluate_market(rates,weights=weights or None,mtf_bias=mtf,mode=mode)
        now=time.time(); expires=now+TTL_SECONDS; decision_id=str(uuid.uuid4())
        confidence=float(result.get("confidence",0.0))
        signed=f"{client_id}|{decision_id}|{result['decision']}|{int(result['strategy_id'])}|{confidence:.8f}|{expires:.6f}"
        payload={"decision_id":decision_id,"client_id":client_id,"decision":result["decision"],
                 "strategy_id":result["strategy_id"],"strategy_name":result["strategy_name"],
                 "reason_ar":result["reason_ar"],"confidence":confidence,"context":result["context"],
                 "analysis":result,"created_at":now,"expires_at":expires,"signed_payload":signed,
                 "signature":hmac.new(token.encode(),signed.encode(),hashlib.sha256).hexdigest()}
        self._request("POST","alzool_engine_decisions",{"client_id":client_id,"payload":payload,
                      "created_at":datetime.fromtimestamp(now,timezone.utc).isoformat()},
                      "resolution=merge-duplicates,return=minimal")
        return {k:v for k,v in payload.items() if k!="analysis"}

    def latest(self, client_id, token, kind="decision"):
        if not self._client(client_id,token): raise PermissionError("client authorization failed")
        rows=self._request("GET","alzool_engine_decisions?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&select=payload,created_at")
        if not rows: return {kind:None}
        payload=rows[0]["payload"]
        if time.time()-float(payload.get("created_at",0))>TTL_SECONDS: return {kind:None,"reason":"expired"}
        return {"analysis":payload.get("analysis"),"decision_id":payload.get("decision_id")} if kind=="analysis" else payload

    def outcome(self, client_id, token, data):
        if not self._client(client_id,token): raise PermissionError("client authorization failed")
        deal=data.get("deal_id"); sid=data.get("strategy_id"); pnl=data.get("pnl")
        if not isinstance(deal,str) or not deal.strip() or len(deal)>120 or isinstance(sid,bool) or not isinstance(sid,int) or isinstance(pnl,bool):
            raise ValueError("valid deal_id, integer strategy_id and numeric pnl required")
        try: pnl=float(pnl)
        except (TypeError,ValueError,OverflowError): raise ValueError("pnl must be finite") from None
        if not 1<=sid<=200 or not math.isfinite(pnl) or abs(pnl)>=1e9: raise ValueError("invalid strategy_id or pnl")
        result=self._request("POST","alzool_engine_outcomes",
            {"client_id":client_id,"deal_id":deal.strip(),"strategy_id":sid,"pnl":pnl},
            "resolution=ignore-duplicates,return=representation")
        if result:
            rows=self._request("GET","alzool_engine_weights?client_id=eq."+client_id+"&strategy_id=eq."+str(sid)+"&select=weight")
            old=float(rows[0]["weight"]) if rows else 1.0
            new=max(.25,min(4.0,old*(1.04 if pnl>0 else .96 if pnl<0 else 1.0)))
            self._request("POST","alzool_engine_weights",
                {"client_id":client_id,"strategy_id":sid,"weight":new},
                "resolution=merge-duplicates,return=minimal")
        return {"accepted":True,"duplicate":not bool(result)}
