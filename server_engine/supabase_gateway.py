"""Supabase-backed authentication and persistence for the AL-ZOOL central engine."""
from __future__ import annotations
import hashlib, hmac, json, math, secrets, time, urllib.parse, urllib.request, urllib.error, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from .engine import evaluate_market, build_synthetic_indicator

TTL_SECONDS = 12

class EngineGateway:
    def __init__(self, supabase_url: str, service_key: str):
        self.url = (supabase_url or "").rstrip("/")
        self.key = service_key or ""
        # Per-process hot snapshots avoid re-reading the same latest tick and
        # decision from PostgREST on every 500 ms dashboard refresh.
        # Supabase remains the durable fallback after restarts or on other instances.
        self._live_cache = {}
        self._decision_cache = {}
        # Keep a small bounded pool for independent durable writes.
        self._persist_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alzool-persist")

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

    def health(self):
        self._request("GET","alzool_engine_clients?select=client_id&limit=1")
        return {"service":"AL-ZOOL central engine","status":"online","storage":"supabase","trading_execution":"local_mt5_client"}

    def issue(self, user_id, days=3650):
        if not user_id:
            raise ValueError("A valid Supabase user is required")
        token=secrets.token_urlsafe(40)
        expires=datetime.now(timezone.utc)+timedelta(days=days)
        existing=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+"&select=client_id")
        if existing:
            client_id=existing[0]["client_id"]
            self._request("PATCH","alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe=""),{
                "token_hash":self._hash(token),"expires_at":expires.isoformat(),"revoked":False,"trading_enabled":False
            },"return=minimal")
        else:
            client_id=uuid.uuid4().hex
            self._request("POST","alzool_engine_clients",{
                "client_id":client_id,"user_id":user_id,"token_hash":self._hash(token),
                "expires_at":expires.isoformat(),"revoked":False,"trading_enabled":False
            },"return=representation")
            self._request("POST","alzool_engine_weights",
                [{"client_id":client_id,"strategy_id":i,"weight":1.0} for i in range(1,201)],
                "resolution=ignore-duplicates,return=minimal")
        return {"client_id":client_id,"client_token":token,"expires_at":expires.isoformat()}

    def set_user_revoked(self, user_id, revoked):
        patch={"revoked":bool(revoked)}
        if revoked: patch["trading_enabled"]=False
        self._request("PATCH","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe=""),
                      patch,"return=minimal")

    def set_user_trading_enabled(self, user_id, enabled):
        self._request("PATCH","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe=""),
                      {"trading_enabled":bool(enabled)},"return=minimal")

    def trading_control(self, client_id, token):
        client=self._client(client_id,token)
        if not client: raise PermissionError("client authorization failed")
        # _client already loaded the authoritative client row; reuse it instead
        # of making a second sequential Supabase round trip on every control poll.
        return {"trading_enabled":bool(client.get("trading_enabled",False))}

    def _client(self, client_id, token):
        rows=self._request("GET","alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+
                           "&select=user_id,token_hash,expires_at,revoked,trading_enabled")
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
            if "tick_volume" in c and c["tick_volume"]<0: raise ValueError("invalid tick volume")
            previous=c["time"]; normalized.append(c)
        try: bid,ask=float(data["bid"]),float(data["ask"])
        except (KeyError,TypeError,ValueError,OverflowError): raise ValueError("valid bid and ask are required") from None
        if not math.isfinite(bid) or not math.isfinite(ask) or bid<=0 or ask<bid:
            raise ValueError("valid bid and ask are required")
        return normalized,bid,ask

    def market(self, client_id, token, data):
        request_started=time.perf_counter()
        auth_started=time.perf_counter()
        client=self._client(client_id,token)
        auth_ms=(time.perf_counter()-auth_started)*1000
        if not client: raise PermissionError("client authorization failed")
        rates,_,_=self._validate_market(data)
        mtf=data.get("mtf_bias",{})
        if not isinstance(mtf,dict) or any(k not in ("M5","M15","H1") or v not in ("BUY","SELL","WAIT") for k,v in mtf.items()):
            raise ValueError("mtf_bias must contain only M5/M15/H1 BUY/SELL/WAIT values")
        dashboard_settings=self.settings_for_client(client_id,token)
        configured_mode=str(dashboard_settings.get("strategy_mode","AUTO")).upper()
        if configured_mode.startswith("HUNTER"): mode="HUNTER"
        elif configured_mode.startswith("SNIPER"): mode="SNIPER"
        else: mode="AUTO"
        selected_raw=(configured_mode if configured_mode.isdigit()
                      else dashboard_settings.get("selected_strategy_id",data.get("selected_strategy_id",0)))
        if isinstance(selected_raw,bool): raise ValueError("selected_strategy_id must be 0..200")
        try: selected_id=int(selected_raw or 0)
        except (TypeError,ValueError,OverflowError): raise ValueError("selected_strategy_id must be 0..200") from None
        if not 0<=selected_id<=200: raise ValueError("selected_strategy_id must be 0..200")
        weights_started=time.perf_counter()
        rows=self._request("GET","alzool_engine_weights?client_id=eq."+client_id+"&select=strategy_id,weight")
        weights_ms=(time.perf_counter()-weights_started)*1000
        weights={str(r["strategy_id"]):float(r["weight"]) for r in rows}
        strategy_started=time.perf_counter()
        result=evaluate_market(rates,weights=weights or None,mtf_bias=mtf,mode=mode,selected_strategy_id=selected_id,
                               forming_candle=data.get("forming_candle"),tick=data.get("tick"))
        strategy_ms=(time.perf_counter()-strategy_started)*1000
        # Keep a short, client-isolated market-regime history. It is advisory only
        # and is stored inside the decision payload; it never changes the signal.
        previous_payload=self._decision_cache.get(client_id)
        if previous_payload is None:
            previous_rows=self._request("GET","alzool_engine_decisions?client_id=eq."+client_id+
                                        "&select=payload&order=created_at.desc&limit=1")
            previous_payload=(previous_rows[0].get("payload") or {}) if previous_rows else {}
        previous_analysis=(previous_payload or {}).get("analysis") or {}
        previous_context=previous_analysis.get("context") or {}
        previous_memory=previous_analysis.get("market_memory") or {}
        history=list(previous_memory.get("history") or [])
        current_regime=(result.get("context") or {}).get("regime","غير محدد")
        previous_regime=previous_context.get("regime")
        state_changed=bool(previous_regime and previous_regime!=current_regime)
        if not history and previous_regime:
            history.append({"regime":previous_regime,"at":(previous_payload or {}).get("created_at")})
        history.append({"regime":current_regime,"at":time.time()})
        history=history[-12:]
        consecutive=0
        for item in reversed(history):
            if item.get("regime")!=current_regime: break
            consecutive+=1
        transitions=[]
        for idx,item in enumerate(history):
            if idx and item.get("regime")!=history[idx-1].get("regime"):
                transitions.append({"from":history[idx-1].get("regime"),"to":item.get("regime"),"at":item.get("at")})
        result["market_memory"]={"current_regime":current_regime,"previous_regime":previous_regime,
                                 "state_changed":state_changed,"consecutive_decisions":consecutive,
                                 "history":history,"transitions":transitions[-6:],
                                 "note_ar":"ذاكرة حالة السوق مستقلة لكل عميل؛ لا تغيّر شروط الدخول أو الصفقات المفتوحة"}
        now=time.time(); expires=now+TTL_SECONDS; decision_id=str(uuid.uuid4())
        confidence=float(result.get("confidence",0.0))
        signed=f"{client_id}|{decision_id}|{result['decision']}|{int(result['strategy_id'])}|{confidence:.8f}|{expires:.6f}"
        payload={"decision_id":decision_id,"client_id":client_id,"decision":result["decision"],
                 "strategy_id":result["strategy_id"],"strategy_name":result["strategy_name"],
                 "reason_ar":result["reason_ar"],"confidence":confidence,"context":result["context"],
                 "analysis":result,"candles":rates,"symbol":data.get("symbol"),"bid":float(data.get("bid")),"ask":float(data.get("ask")),"timeframe":data.get("timeframe","CURRENT"),"created_at":now,"expires_at":expires,"signed_payload":signed,
                 "signature":hmac.new(token.encode(),signed.encode(),hashlib.sha256).hexdigest()}
        persist_started=time.perf_counter()
        decision_row={"client_id":client_id,"payload":payload,
                      "created_at":datetime.fromtimestamp(now,timezone.utc).isoformat()}
        signal_type = "WAIT" if result["decision"] == "WAIT" else ("BLOCKED" if not bool(client.get("trading_enabled", False)) else "SIGNAL_CREATED")
        signal_reason = result.get("reason_ar", "")
        if signal_type == "BLOCKED":
            signal_reason = "إشارة " + result["decision"] + " متاحة، لكن التداول الآلي متوقف من لوحة التحكم؛ لم يُرسل أمر."
        event_payload = {k: payload.get(k) for k in ("decision_id","decision","strategy_id","strategy_name","reason_ar","confidence","symbol","timeframe","bid","ask","expires_at")}
        event_payload["reason_ar"] = signal_reason
        # The decision and its matching signal-log event are independent durable
        # writes. Run them concurrently, but wait for BOTH before returning a
        # signed decision to MT5 or publishing the hot cache.
        decision_future=self._persist_executor.submit(
            self._request,"POST","alzool_engine_decisions",decision_row,
            "resolution=merge-duplicates,return=minimal")
        event_future=self._persist_executor.submit(
            self._store_signal_event,client_id,decision_id + ":decision",
            signal_type,event_payload,now)
        decision_future.result()
        event_future.result()
        persist_ms=(time.perf_counter()-persist_started)*1000
        # Publish the same evaluated snapshot to the local hot path only after
        # durable persistence succeeds; never expose an uncommitted decision.
        self._decision_cache[client_id] = payload
        total_ms=(time.perf_counter()-request_started)*1000
        print("AL-ZOOL LATENCY market_total_ms=%.1f auth_ms=%.1f weights_ms=%.1f strategy_ms=%.1f persist_ms=%.1f" %
              (total_ms,auth_ms,weights_ms,strategy_ms,persist_ms),flush=True)
        return {k:v for k,v in payload.items() if k!="analysis"}

    def _store_signal_event(self, client_id, event_id, event_type, payload, created_at=None):
        stamp = datetime.fromtimestamp(created_at, timezone.utc).isoformat() if created_at else datetime.now(timezone.utc).isoformat()
        row = {"event_id": str(event_id)[:180], "client_id": client_id, "event_type": str(event_type)[:40],
               "payload": payload if isinstance(payload, dict) else {}, "created_at": stamp}
        self._request("POST", "alzool_engine_signal_events?on_conflict=event_id", row, "resolution=ignore-duplicates,return=minimal")

    def ingest_signal_events(self, client_id, token, events):
        if not self._client(client_id, token):
            raise PermissionError("client authorization failed")
        if not isinstance(events, list) or len(events) > 50:
            raise ValueError("events must be an array with at most 50 records")
        allowed = {"ORDER_SENT","TRADE_OPENED","TRADE_CLOSED","ORDER_REJECTED","ADDON_OPENED","COOLING_OPENED","PROTECTION","BLOCKED"}
        accepted = 0
        for item in events:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "").strip()
            event_type = str(item.get("event_type") or "").strip().upper()
            payload = item.get("payload") or {}
            if not event_id or len(event_id) > 180 or event_type not in allowed or not isinstance(payload, dict):
                continue
            self._store_signal_event(client_id, event_id, event_type, payload)
            accepted += 1
        return {"accepted": accepted}

    def signal_events_for_user(self, user_id, limit=100):
        clients = self._request("GET", "alzool_engine_clients?user_id=eq." + urllib.parse.quote(user_id, safe="") +
                                "&select=client_id,revoked&limit=1")
        if not clients or clients[0].get("revoked"):
            return {"events": []}
        cid = urllib.parse.quote(clients[0]["client_id"], safe="")
        safe_limit = max(1, min(int(limit), 200))
        rows = self._request("GET", "alzool_engine_signal_events?client_id=eq." + cid +
                             "&select=event_id,event_type,payload,created_at&order=created_at.desc&limit=" + str(safe_limit))
        return {"events": rows}

    def latest(self, client_id, token, kind="decision"):
        if not self._client(client_id,token): raise PermissionError("client authorization failed")
        rows=self._request("GET","alzool_engine_decisions?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&select=payload,created_at")
        if not rows: return {kind:None}
        payload=rows[0]["payload"]
        if time.time()-float(payload.get("created_at",0))>TTL_SECONDS: return {kind:None,"reason":"expired"}
        return {"analysis":payload.get("analysis"),"decision_id":payload.get("decision_id")} if kind=="analysis" else payload

    def management_command(self, client_id, token):
        """Create a short-lived signed broker instruction from central settings."""
        client=self._client(client_id,token)
        if not client:
            raise PermissionError("client authorization failed")
        settings=self.settings_for_client(client_id,token)
        settings["_trading_enabled"]=bool(client.get("trading_enabled",False))
        live=self._live_cache.get(client_id)
        if live is None:
            cid=urllib.parse.quote(client_id,safe="")
            rows=self._request("GET","alzool_engine_live_state?client_id=eq."+cid+
                               "&select=payload,sequence,source_time,received_at&order=received_at.desc&limit=1")
            live=rows[0] if rows else None
        if not live:
            plan={"action":"WAIT","reason":"لا توجد بيانات MT5 حديثة"}
        else:
            received=live.get("received_at")
            try:
                age=time.time()-datetime.fromisoformat(received.replace("Z","+00:00")).timestamp()
            except Exception:
                age=999999
            if age>5:
                plan={"action":"WAIT","reason":"بيانات MT5 قديمة؛ لا يصدر أمر إدارة"}
            else:
                decision_payload=self._decision_cache.get(client_id)
                if decision_payload is None:
                    rows=self._request("GET","alzool_engine_decisions?client_id=eq."+
                        urllib.parse.quote(client_id,safe="")+
                        "&select=payload&order=created_at.desc&limit=1")
                    decision_payload=(rows[0].get("payload") or {}) if rows else {}
                plan=build_management_command(settings,live,market_decision=decision_payload)
        action=str(plan.get("action","WAIT"))
        command_id=str(plan.get("command_id") or uuid.uuid4().hex)
        ticket=str(plan.get("ticket",""))
        sl=float(plan.get("sl",0.0) or 0.0)
        tp=float(plan.get("tp",0.0) or 0.0)
        direction=str(plan.get("direction",""))
        lots=float(plan.get("lots",0.0) or 0.0)
        price=float(plan.get("price",0.0) or 0.0)
        comment=str(plan.get("comment","")).replace("|","")[:48]
        expires=float(plan.get("expires_at",time.time()+5.0))
        signed=(f"{client_id}|{command_id}|{action}|{ticket}|{sl:.8f}|{tp:.8f}|"
                f"{direction}|{lots:.8f}|{price:.8f}|{comment}|{expires:.6f}")
        signature=hmac.new(token.encode("utf-8"),signed.encode("utf-8"),hashlib.sha256).hexdigest()
        return {**plan,"client_id":client_id,"command_id":command_id,
                "ticket":ticket,"sl":sl,"tp":tp,"direction":direction,"lots":lots,
                "price":price,"comment":comment,"expires_at":expires,
                "signed_payload":signed,"signature":signature}

    def status_for_user(self, user_id, stale_after=5):
        """Report MT5 connected from one-second terminal telemetry, not candle decisions."""
        rows=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+"&select=client_id,revoked,expires_at,trading_enabled,trading_settings&limit=1")
        if not rows:
            return {"mt5_connected":False,"trading_enabled":False,"reason":"credentials_not_issued","last_seen_at":None,"seconds_since_last_data":None}
        client=rows[0]
        if client.get("revoked"):
            return {"mt5_connected":False,"trading_enabled":False,"reason":"client_revoked","last_seen_at":None,"seconds_since_last_data":None}
        cid=urllib.parse.quote(client["client_id"],safe="")
        live=self._request("GET","alzool_engine_live_state?client_id=eq."+cid+"&select=sequence,received_at&order=received_at.desc&limit=1")
        row=live[0] if live else None
        stamp=(row or {}).get("received_at")
        try:
            seen=datetime.fromisoformat(stamp.replace("Z","+00:00")).timestamp() if stamp else 0
        except Exception:
            seen=0
        age=max(0,time.time()-seen) if seen else None
        threshold=min(max(float(stale_after),1.0),5.0)
        online=age is not None and age<=threshold
        return {"mt5_connected":online,"trading_enabled":bool(client.get("trading_enabled",False)),
                "reason":"live_data_recent" if online else ("no_live_data" if not row else "live_data_stale"),
                "last_seen_at":stamp,"seconds_since_last_data":round(age,2) if age is not None else None,
                "live_age_ms":int(age*1000) if age is not None else None,"sequence":(row or {}).get("sequence"),
                "client_id":client["client_id"]}

    def outcome(self, client_id, token, data):
        if not self._client(client_id,token): raise PermissionError("client authorization failed")
        if not isinstance(data,dict): raise ValueError("outcome must be an object")
        deal=data.get("deal_id"); sid=data.get("strategy_id"); pnl=data.get("pnl")
        if not isinstance(deal,str) or not deal.strip() or len(deal)>120 or isinstance(sid,bool) or not isinstance(sid,int) or isinstance(pnl,bool):
            raise ValueError("valid deal_id, integer strategy_id and numeric pnl required")
        try: pnl=float(pnl)
        except (TypeError,ValueError,OverflowError): raise ValueError("pnl must be finite") from None
        if not 1<=sid<=200 or not math.isfinite(pnl) or abs(pnl)>=1e9: raise ValueError("invalid strategy_id or pnl")

        # Resolve controls before the database transaction. The RPC below inserts
        # the outcome and changes its strategy weight atomically, so a retry can
        # never leave a saved-but-untrained result or train the same deal twice.
        settings=self.settings_for_client(client_id,token)
        learning_enabled=bool(settings.get("learning_enabled",True))
        learning_rate=max(0.001,min(0.20,float(settings.get("learning_rate_percent",4.0))/100.0))
        result=self._request("POST","rpc/alzool_record_learning_outcome",{
            "p_client_id":client_id,
            "p_deal_id":deal.strip(),
            "p_strategy_id":sid,
            "p_pnl":pnl,
            "p_learning_enabled":learning_enabled,
            "p_learning_rate":learning_rate,
        })
        if isinstance(result,list) and result:
            result=result[0]
        if not isinstance(result,dict):
            raise RuntimeError("Supabase learning transaction returned an invalid response")
        return result



    BASIC_SETTINGS_DEFAULTS = {"learning_enabled":True,"learning_rate_percent":4.0,"reverse_protection_enabled":False,"ai_risk_enabled":False,"allow_buy":True,"allow_sell":True,"max_spread_points":0,"require_broker_stops":True,"lot":0.01,"ai_risk_percent":0.5,"ai_max_lot":0.05,"profit_target":1,"loss_limit":50,"global_profit_enabled":True,"per_trade_profit_enabled":True,"per_trade_profit_target":2,"smart_profit_enabled":False,"one_direction_only":True,"global_loss_enabled":True,"global_loss_amount":50,"ai_loss_guard_enabled":False,"max_positions":0,"fixed_sl_enabled":True,"fixed_sl_distance":3,"trailing_enabled":True,"trail_start":3,"trail_distance":3,"pending_distance":2,"order_mode":"Market Order","reverse_trigger":5,"reverse_continue":2,"candle_chase_enabled":False,"candle_chase_max_orders":1,"candle_chase_min_move":0.1,"candle_chase_cooldown":2,"candle_chase_buy_target":1,"candle_chase_sell_target":1,"candle_chase_single_target":0.5,"candle_chase_sl_enabled":False,"candle_chase_sl_distance":3,"addon_enabled":False,"addon_smart_enabled":False,"addon_step":2,"addon_multiplier_enabled":True,"addon_multiplier":1.2,"addon_max":3,"addon_max_lot":0.05,"cooling_enabled":False,"cooling_smart_enabled":False,"cooling_step":3,"cooling_multiplier_enabled":True,"cooling_multiplier":1.2,"cooling_max":2,"cooling_max_lot":0.05,"recovery_enabled":False,"recovery_mode":"AUTO","recovery_manual_lot":0.01,"recovery_max_lot":0.05,"recovery_trigger_loss":10.0,"recovery_target_profit":0.5,"recovery_min_positions":3,"recovery_atr_distance":1.0,"recovery_min_confidence":0.65,"recovery_max_entries":0,"adaptive_strategy_switch":False,"strategy_mode":"AUTO — Best current fit"}

    def settings_for_client(self, client_id, token):
        client=self._client(client_id,token)
        if not client: raise PermissionError("client authorization failed")
        rows=self._request("GET","alzool_engine_clients?client_id=eq."+urllib.parse.quote(client_id,safe="")+"&select=trading_settings&limit=1")
        if not rows: raise PermissionError("client settings unavailable")
        saved=rows[0].get("trading_settings") or {}
        return {**self.BASIC_SETTINGS_DEFAULTS,**saved}

    def settings_for_user(self, user_id):
        rows=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+"&select=trading_settings&limit=1")
        if not rows: return dict(self.BASIC_SETTINGS_DEFAULTS)
        saved=rows[0].get("trading_settings") or {}
        return {**self.BASIC_SETTINGS_DEFAULTS,**saved}

    def save_settings_for_user(self, user_id, values):
        if not isinstance(values,dict): raise ValueError("settings must be an object")
        allowed=set(self.BASIC_SETTINGS_DEFAULTS)
        if not values or any(k not in allowed for k in values): raise ValueError("unknown or empty setting")
        current=self.settings_for_user(user_id)
        merged={**current,**values}
        # Normalize Arabic-Indic/Persian digits and Arabic decimal separators.
        for key,default in self.BASIC_SETTINGS_DEFAULTS.items():
            if isinstance(default,(int,float)) and not isinstance(default,bool) and isinstance(merged.get(key),str):
                raw=merged[key].strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹","01234567890123456789"))
                raw=raw.replace("٫",".").replace("٬","").replace("،","").replace(" ","")
                try: merged[key]=float(raw)
                except (TypeError,ValueError): raise ValueError(key+" must be a valid number") from None
        for key,default in self.BASIC_SETTINGS_DEFAULTS.items():
            value=merged.get(key)
            if isinstance(default,bool):
                if not isinstance(value,bool): raise ValueError(key+" must be boolean")
            elif isinstance(default,(int,float)):
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)):
                    raise ValueError(key+" must be a finite number")
            elif not isinstance(value,str):
                raise ValueError(key+" must be text")
        if not 0.1<=merged["learning_rate_percent"]<=20.0: raise ValueError("learning_rate_percent must be 0.1..20")
        if merged["order_mode"] not in ("Market Order","Pending Order"): raise ValueError("invalid order_mode")
        if merged["lot"]<=0 or merged["profit_target"]<=0 or merged["per_trade_profit_target"]<=0 or merged["loss_limit"]<=0 or merged["global_loss_amount"]<=0: raise ValueError("risk, lot and profit/loss targets must be greater than zero")
        if not 0.05<=merged["ai_risk_percent"]<=2.0 or merged["ai_max_lot"]<=0: raise ValueError("AI risk must be 0.05..2% and AI max lot must be positive")
        if merged["max_spread_points"]<0: raise ValueError("max_spread_points cannot be negative (0 means no spread limit)")
        if merged["max_positions"]<0 or merged["addon_max"]<0 or merged["cooling_max"]<0: raise ValueError("maximum counts cannot be negative")
        if merged["fixed_sl_distance"]<=0 or merged["trail_start"]<0 or merged["trail_distance"]<=0 or merged["pending_distance"]<=0 or merged["reverse_trigger"]<=0 or merged["reverse_continue"]<=0: raise ValueError("stop, trailing, pending and reversal distances are invalid")
        if merged["addon_step"]<=0 or merged["cooling_step"]<=0 or merged["addon_multiplier"]<1 or merged["cooling_multiplier"]<1 or merged["addon_max_lot"]<=0 or merged["cooling_max_lot"]<=0: raise ValueError("add-on/cooling distances, multipliers and lot caps are invalid")
        if merged["recovery_mode"] not in ("AUTO","MANUAL"): raise ValueError("invalid recovery_mode")
        if merged["recovery_manual_lot"]<=0 or merged["recovery_max_lot"]<=0 or merged["recovery_trigger_loss"]<=0 or merged["recovery_target_profit"]<0 or merged["recovery_min_positions"]<2 or merged["recovery_atr_distance"]<=0 or not 0.50<=merged["recovery_min_confidence"]<=0.95 or merged["recovery_max_entries"]<0: raise ValueError("smart recovery settings are invalid; recovery_max_entries 0 means unlimited")
        if merged["candle_chase_max_orders"]<1 or merged["candle_chase_max_orders"]>10 or merged["candle_chase_min_move"]<=0 or merged["candle_chase_cooldown"]<0 or merged["candle_chase_buy_target"]<0 or merged["candle_chase_sell_target"]<0 or merged["candle_chase_single_target"]<0 or merged["candle_chase_sl_distance"]<=0: raise ValueError("candle-follow settings are invalid")
        if merged["strategy_mode"] not in ("AUTO — Best current fit","HUNTER — Frequent opportunities","SNIPER — Selective entries") and not merged["strategy_mode"].isdigit():
            raise ValueError("invalid strategy_mode")
        # A dashboard user may save settings before issuing MT5 credentials.
        # Create the per-user engine row first so the save is durable instead of
        # silently PATCHing zero rows and reporting success.
        client_rows=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+
                                  "&select=client_id&limit=1")
        if not client_rows:
            self.issue(user_id)
        self._request("PATCH","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe=""),
                      {"trading_settings":merged},"return=minimal")
        return merged

    def terminal_state(self, client_id, token, data):
        if not self._client(client_id,token): raise PermissionError("client authorization failed")
        if not isinstance(data,dict): raise ValueError("terminal state must be an object")
        keys=("account","positions","orders","history","settings","events")
        payload={k:data.get(k) for k in keys}
        if not isinstance(payload.get("positions"),list) or not isinstance(payload.get("orders"),list) or not isinstance(payload.get("history"),list): raise ValueError("positions, orders and history arrays are required")
        if len(payload["positions"])>200 or len(payload["orders"])>200 or len(payload["history"])>200: raise ValueError("too many terminal records")
        row={"client_id":client_id,"payload":payload,"updated_at":datetime.now(timezone.utc).isoformat()}
        self._request("POST","alzool_engine_terminal_state",row,"resolution=merge-duplicates,return=minimal")
        return {"accepted":True,"positions":len(payload["positions"]),"orders":len(payload["orders"])}

    def terminal_state_live(self, client_id, token, data):
        client=self._client(client_id,token)
        if not client: raise PermissionError("client authorization failed")
        if not isinstance(data,dict): raise ValueError("live state must be an object")
        account=data.get("account") or {}
        positions=data.get("positions",[])
        orders=data.get("orders",[])
        if not isinstance(account,dict) or not isinstance(positions,list) or not isinstance(orders,list):
            raise ValueError("invalid live account or trade arrays")
        if len(positions)>200 or len(orders)>200: raise ValueError("too many live trade records")
        try: sequence=int(data.get("sequence",0))
        except (TypeError,ValueError,OverflowError): raise ValueError("invalid sequence") from None
        if sequence<0: raise ValueError("invalid sequence")
        try:
            epoch=float(data.get("source_time",0) or 0)
            source_time=datetime.fromtimestamp(epoch,timezone.utc).isoformat() if epoch>0 else None
        except (TypeError,ValueError,OverflowError,OSError): source_time=None
        payload={k:data.get(k) for k in ("account","positions","orders","tick","forming_candle","symbol","timeframe","sequence","point","volume_step","volume_min","volume_max")}
        received=datetime.now(timezone.utc).isoformat()
        row={"client_id":client_id,"payload":payload,"sequence":sequence,"source_time":source_time,
             "received_at":received,"updated_at":received}
        self._request("POST","alzool_engine_live_state",row,"resolution=merge-duplicates,return=minimal")
        self._live_cache[client_id] = {"payload":payload,"sequence":sequence,
                                       "source_time":source_time,"received_at":received}
        return {"accepted":True,"sequence":sequence,"received_at":received}

    def live_state_for_user(self, user_id):
        clients=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+
                              "&select=client_id,revoked,trading_enabled&limit=1")
        if not clients: return {"connected":False,"reason":"credentials_not_issued","live_state":None}
        client=clients[0]
        client_id=client["client_id"]
        cid=urllib.parse.quote(client_id,safe="")
        row=self._live_cache.get(client_id)
        if row is None:
            rows=self._request("GET","alzool_engine_live_state?client_id=eq."+cid+
                               "&select=payload,sequence,source_time,received_at&order=received_at.desc&limit=1")
            row=rows[0] if rows else None
        live_payload=(row or {}).get("payload") or {}
        decision_payload=self._decision_cache.get(client_id)
        if decision_payload is None:
            decisions=self._request("GET","alzool_engine_decisions?client_id=eq."+cid+
                                    "&select=payload&order=created_at.desc&limit=1")
            decision_payload=(decisions[0].get("payload") or {}) if decisions else {}
        candles=decision_payload.get("candles") or []
        indicator=build_synthetic_indicator(candles,live_payload.get("forming_candle"),live_payload.get("tick"))
        stamp=(row or {}).get("received_at")
        try: received=datetime.fromisoformat(stamp.replace("Z","+00:00")).timestamp() if stamp else 0
        except Exception: received=0
        age_ms=max(0,int((time.time()-received)*1000)) if received else None
        revoked=bool(client.get("revoked"))
        fresh=age_ms is not None and age_ms<=5000 and not revoked
        return {"connected":fresh,"reason":"live_data_recent" if fresh else ("no_live_data" if not row else "live_data_stale"),
                "trading_enabled":bool(client.get("trading_enabled",False)) and not revoked,
                "sequence":(row or {}).get("sequence"),"source_time":(row or {}).get("source_time"),
                "received_at":stamp,"age_ms":age_ms,"live_state":live_payload,"indicator":indicator}

    def workspace_for_user(self, user_id, limit=100):
        """Return live MT5 state and persisted intelligence for the authenticated dashboard user."""
        clients=self._request("GET","alzool_engine_clients?user_id=eq."+urllib.parse.quote(user_id,safe="")+"&select=client_id,revoked,expires_at,trading_enabled,trading_settings&limit=1")
        if not clients:
            return {"connected":False,"reason":"credentials_not_issued","analysis":None,"decisions":[],"outcomes":[],"weights":[],"terminal_state":None,"live_state":None}
        client=clients[0]
        cid=urllib.parse.quote(client["client_id"],safe="")
        decisions=self._request("GET","alzool_engine_decisions?client_id=eq."+cid+"&select=payload,created_at&order=created_at.desc&limit=1")
        outcomes=self._request("GET","alzool_engine_outcomes?client_id=eq."+cid+"&select=deal_id,strategy_id,pnl,created_at&order=created_at.desc&limit="+str(max(1,min(int(limit),200))))
        opened_events=self._request("GET","alzool_engine_signal_events?client_id=eq."+cid+
                                    "&event_type=eq.TRADE_OPENED&select=payload&order=created_at.desc&limit=500")
        closed_events=self._request("GET","alzool_engine_signal_events?client_id=eq."+cid+
                                    "&event_type=eq.TRADE_CLOSED&select=payload&order=created_at.desc&limit=500")
        regime_by_position={}
        position_by_close_deal={}
        for event in opened_events:
            event_payload=event.get("payload") or {}
            position_key=str(event_payload.get("position_id") or "").strip()
            regime=str(event_payload.get("market_regime") or "").strip()
            if position_key and regime and position_key not in regime_by_position:
                regime_by_position[position_key]=regime
        for event in closed_events:
            event_payload=event.get("payload") or {}
            deal_key=str(event_payload.get("learning_id") or event_payload.get("deal_id") or "").strip()
            position_key=str(event_payload.get("position_id") or "").strip()
            if deal_key and position_key:
                position_by_close_deal[deal_key]=position_key
        regime_stats={}
        strategy_regime_stats={}
        for outcome in outcomes:
            deal_key=str(outcome.get("deal_id") or "").strip()
            regime=regime_by_position.get(position_by_close_deal.get(deal_key,""))
            if not regime: continue
            try: pnl=float(outcome.get("pnl") or 0.0)
            except (TypeError,ValueError,OverflowError): continue
            sid=int(outcome.get("strategy_id") or 0)
            bucket=regime_stats.setdefault(regime,{"regime":regime,"trades":0,"wins":0,"losses":0,"net_pnl":0.0})
            bucket["trades"]+=1
            bucket["wins"]+=1 if pnl>0 else 0
            bucket["losses"]+=1 if pnl<0 else 0
            bucket["net_pnl"]+=pnl
            sbucket=strategy_regime_stats.setdefault((regime,sid),{"regime":regime,"strategy_id":sid,"trades":0,"wins":0,"net_pnl":0.0})
            sbucket["trades"]+=1
            sbucket["wins"]+=1 if pnl>0 else 0
            sbucket["net_pnl"]+=pnl
        regime_performance=[]
        for bucket in regime_stats.values():
            bucket["win_rate_pct"]=round(100*bucket["wins"]/max(1,bucket["trades"]),1)
            bucket["net_pnl"]=round(bucket["net_pnl"],2)
            regime_performance.append(bucket)
        strategy_regime_performance=[]
        for bucket in strategy_regime_stats.values():
            bucket["win_rate_pct"]=round(100*bucket["wins"]/max(1,bucket["trades"]),1)
            bucket["net_pnl"]=round(bucket["net_pnl"],2)
            strategy_regime_performance.append(bucket)
        regime_performance.sort(key=lambda x:x["trades"],reverse=True)
        strategy_regime_performance.sort(key=lambda x:(x["trades"],x["net_pnl"]),reverse=True)

        weights=self._request("GET","alzool_engine_weights?client_id=eq."+cid+"&select=strategy_id,weight&order=strategy_id.asc")
        terminal_rows=self._request("GET","alzool_engine_terminal_state?client_id=eq."+cid+"&select=payload,updated_at&limit=1")
        live_rows=self._request("GET","alzool_engine_live_state?client_id=eq."+cid+"&select=payload,sequence,source_time,received_at&order=received_at.desc&limit=1")
        payload=(decisions[0].get("payload") or {}) if decisions else None
        live_row=live_rows[0] if live_rows else None
        live_stamp=(live_row or {}).get("received_at")
        try:
            live_seen=datetime.fromisoformat(live_stamp.replace("Z","+00:00")).timestamp() if live_stamp else 0
        except Exception:
            live_seen=0
        live_age=max(0,time.time()-live_seen) if live_seen else None
        revoked=bool(client.get("revoked"))
        terminal_row=terminal_rows[0] if terminal_rows else None
        terminal_payload=dict((terminal_row or {}).get("payload") or {})
        live_payload=(live_row or {}).get("payload") or {}
        for key in ("account","positions","orders","tick","forming_candle","symbol","timeframe","sequence"):
            if key in live_payload:
                terminal_payload[key]=live_payload[key]
        terminal_state={"payload":terminal_payload,"updated_at":(terminal_row or {}).get("updated_at"),
                        "live_updated_at":live_stamp,"live_sequence":(live_row or {}).get("sequence")}
        fresh=live_age is not None and live_age<=5 and not revoked
        return {
            "settings":{**self.BASIC_SETTINGS_DEFAULTS,**(client.get("trading_settings") or {})},
            "connected":fresh,
            "reason":"live_data_recent" if fresh else ("client_revoked" if revoked else ("no_live_data" if not live_row else "live_data_stale")),
            "trading_enabled":bool(client.get("trading_enabled",False)) and not revoked,
            "last_seen_at":live_stamp,"seconds_since_last_data":round(live_age,2) if live_age is not None else None,
            "live_age_ms":int(live_age*1000) if live_age is not None else None,
            "live_sequence":(live_row or {}).get("sequence"),"live_state":live_payload,
            "analysis":(payload or {}).get("analysis"),"candles":(payload or {}).get("candles",[]),
            "symbol":(payload or {}).get("symbol"),"timeframe":(payload or {}).get("timeframe"),
            "decision":{"decision_id":(payload or {}).get("decision_id"),"decision":(payload or {}).get("decision"),
                        "strategy_id":(payload or {}).get("strategy_id"),"strategy_name":(payload or {}).get("strategy_name"),
                        "reason_ar":(payload or {}).get("reason_ar"),"confidence":(payload or {}).get("confidence"),
                        "created_at":(payload or {}).get("created_at"),"expires_at":(payload or {}).get("expires_at")} if payload else None,
            "outcomes":outcomes,"weights":weights,"outcome_count":len(outcomes),
            "regime_performance":regime_performance,"strategy_regime_performance":strategy_regime_performance,
            "terminal_state":terminal_state,"client_revoked":revoked
        }

