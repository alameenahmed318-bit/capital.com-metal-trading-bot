"""AL-ZOOL central multi-model voting engine.

Fast AI is deterministic and immediate. Deep AI uses opt-in Qwen and DeepSeek
connections. No remote model is marked approved when unavailable. The trading
gate must require both matching fresh votes; this module NEVER places orders.
"""
import json
import concurrent.futures
import math
import os
import threading
import time
import urllib.request
import groq_advisor

_LOCK=threading.RLock()
_VOTES={}
_MAX_ENTRIES=80
_QWEN_URL=os.getenv("QWEN_API_URL","").strip()
_QWEN_MODEL=os.getenv("QWEN_MODEL","qwen3.5:4b").strip()
_DEEPSEEK_KEY=os.getenv("DEEPSEEK_API_KEY","").strip()
_DEEPSEEK_MODEL=os.getenv("DEEPSEEK_MODEL","deepseek-chat").strip()
_DEEPSEEK_URL="https://api.deepseek.com/chat/completions"
_TIMEOUT=max(4,min(30,int(os.getenv("AI_MODEL_TIMEOUT_SECONDS","12"))))
_DEEPSEEK_LOCAL_URL=os.getenv("DEEPSEEK_LOCAL_URL","").strip()
_DEEPSEEK_LOCAL_MODEL=os.getenv("DEEPSEEK_LOCAL_MODEL","deepseek-r1:1.5b").strip()

def _num(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError,OverflowError):return default

def _vote(model,status="UNAVAILABLE",direction="WAIT",confidence=0.0,reason=""):
    if direction not in ("BUY","SELL","WAIT"):direction="WAIT"
    return {"model":model,"status":status,"direction":direction,
            "confidence":round(max(0,min(1,_num(confidence))),3),
            "reason_ar":str(reason)[:300]}

def _json_post(url,payload,headers,timeout=_TIMEOUT):
    request=urllib.request.Request(url,data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type":"application/json",**headers},method="POST")
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.loads(response.read(32000).decode("utf-8"))

def _parse(model,message):
    raw=str(message).strip()
    try:
        start=raw.find("{");end=raw.rfind("}")
        result=json.loads(raw[start:end+1]) if start>=0 and end>start else {}
        direction=str(result.get("direction","WAIT")).upper()
        confidence=_num(result.get("confidence",0))
        if direction not in ("BUY","SELL","WAIT") or not 0<=confidence<=1:
            raise ValueError("invalid model vote")
        return _vote(model,"READY",direction,confidence,result.get("reason_ar",""))
    except (ValueError,TypeError):
        return _vote(model,"INVALID",reason="رد النموذج ليس تصويت JSON صالحاً")

def _prompt(decision):
    summary={"direction":str(decision.get("decision","WAIT")),
             "confidence":_num(decision.get("confidence")),
             "strategy_id":int(_num(decision.get("strategy_id"))),
             "bar_time":int(_num(decision.get("bar_time"))),
             "atr":_num((decision.get("context") or {}).get("atr")),
             "families":decision.get("family_votes") or {},
             "combinations":decision.get("combination_summary") or {}}
    # Five original quantitative bot messages are real same-bar inputs;
    # these are NOT votes from the listed commercial MQL5 products.
    bot_feed=decision.get("advisory_bots") if isinstance(decision.get("advisory_bots"),dict) else {}
    if (bot_feed.get("status")=="READY" and
        int(_num(bot_feed.get("bar_time",0)))==int(_num(decision.get("bar_time",0)))):
        summary["trading_bot_signals"]=[
            {"bot_id":str(bot.get("id",""))[:32],
             "signal":str(bot.get("signal","WAIT")),
             "confidence":_num(bot.get("confidence"))}
            for bot in bot_feed.get("advisors",[])[:5] if isinstance(bot,dict)
        ]
        summary["trading_bot_consensus"]=bot_feed.get("consensus",{})

    return ("You are a cautious XAUUSD trade veto agent, not an order executor. "
            "Return ONLY JSON with keys direction (BUY, SELL or WAIT), confidence (0..1), "
            "reason_ar (short Arabic). Disagree or WAIT when data are insufficient. "
            "Never invent market data. Snapshot: "+json.dumps(summary,ensure_ascii=False))

def _qwen(decision):
    if not _QWEN_URL:return _vote("Qwen","NOT_CONFIGURED",reason="QWEN_API_URL غير مضبوط")
    try:
        # A secure, operator-controlled Ollama relay URL is required on hosted Render.
        url=_QWEN_URL.rstrip("/")+"/api/generate"
        result=_json_post(url,{"model":_QWEN_MODEL,"prompt":_prompt(decision),
            "stream":False,"format":"json","think":False,"keep_alive":"30m","options":{"num_predict":150,"temperature":0}},
            {},timeout=_TIMEOUT)
        return _parse("Qwen",result.get("response",""))
    except Exception as exc:
        return _vote("Qwen","ERROR",reason="فشل اتصال Qwen: "+type(exc).__name__)

def _deepseek(decision):
    if not _DEEPSEEK_KEY and not _DEEPSEEK_LOCAL_URL:
        return _vote("DeepSeek","NOT_CONFIGURED",reason="DeepSeek API أو الاتصال المحلي غير مضبوط")
    if not _DEEPSEEK_KEY and _DEEPSEEK_LOCAL_URL:
        try:
            result=_json_post(_DEEPSEEK_LOCAL_URL.rstrip("/")+"/api/generate",
                {"model":_DEEPSEEK_LOCAL_MODEL,"prompt":_prompt(decision),
                 "stream":False,"format":"json","options":{"temperature":0,"num_predict":180}},
                {},timeout=_TIMEOUT)
            return _parse("DeepSeek",result.get("response",""))
        except Exception as exc:
            return _vote("DeepSeek","ERROR",reason="فشل DeepSeek المحلي: "+type(exc).__name__)
    try:
        result=_json_post(_DEEPSEEK_URL,{"model":_DEEPSEEK_MODEL,"temperature":0,
            "response_format":{"type":"json_object"},
            "max_tokens":180,"messages":[{"role":"user","content":_prompt(decision)}]},
            {"Authorization":"Bearer "+_DEEPSEEK_KEY},timeout=_TIMEOUT)
        answer=result["choices"][0]["message"]["content"]
        return _parse("DeepSeek",answer)
    except Exception as exc:
        return _vote("DeepSeek","ERROR",reason="فشل اتصال DeepSeek: "+type(exc).__name__)

def fast_ai(decision,account):
    signal=str(decision.get("decision","WAIT")).upper()
    confidence=_num(decision.get("confidence"))
    atr=_num((decision.get("context") or {}).get("atr"))
    price=_num(decision.get("entry_reference_price"))
    families=decision.get("family_votes") or {}
    supports=sum(1 for v in families.values() if v==signal)
    oppose=sum(1 for v in families.values() if v in ("BUY","SELL") and v!=signal)
    if signal not in ("BUY","SELL") or confidence<.55 or atr<=0 or price<=0 or supports<=oppose:
        return _vote("Fast AI","READY",reason="التحليل الكمي لا يجيز الدخول")
    if _num(account.get("equity"))<=0 or _num(account.get("free_margin"))<=0:
        return _vote("Fast AI","READY",reason="حارس المخاطر: الحساب غير آمن")
    return _vote("Fast AI","READY",signal,confidence,"توافق السعر وATR والعائلات وسلامة الحساب")

def risk_limits_from_settings(settings):
    """Read owner-configured caps; never infer permissions from AI votes."""
    pm=(settings or {}).get("position_management") or {}
    return {
        "max_drawdown_pct":_num(pm.get("max_drawdown_pct",5),5),
        "max_positions":_num(pm.get("max_positions",10),10),
        "max_total_lots":_num(pm.get("max_total_lots",0.10),.10),
    }

def risk_guardian(account,positions=None,limits=None):
    limits=limits or {}
    balance=_num(account.get("balance"));equity=_num(account.get("equity"))
    free=_num(account.get("free_margin"))
    if balance<=0 or equity<=0 or free<=0:
        return {"approved":False,"reason_ar":"الرصيد أو الهامش غير آمن"}
    max_drawdown=max(0,min(100,_num(limits.get("max_drawdown_pct",5),5)))
    drawdown=max(0,(balance-equity)/balance*100)
    if drawdown>max_drawdown:
        return {"approved":False,"reason_ar":"تجاوز حد تراجع حقوق الملكية",
                "drawdown_pct":round(drawdown,2)}
    max_positions=max(0,int(_num(limits.get("max_positions",10),10)))
    if len(positions or [])>=max_positions:
        return {"approved":False,"reason_ar":"تجاوز عدد الصفقات المسموح",
                "open_positions":len(positions or []),"max_positions":max_positions}
    total_lots=sum(max(0,_num(p.get("volume"))) for p in positions or [] if isinstance(p,dict))
    max_total_lots=_num(limits.get("max_total_lots",.10),.10)
    if max_total_lots<=0 or total_lots+1e-8>=max_total_lots:
        return {"approved":False,"reason_ar":"سقف اللوت الإجمالي يمنع فتح صفقات جديدة",
                "total_lots":round(total_lots,4),"max_total_lots":max_total_lots}
    return {"approved":True,"reason_ar":"حارس المخاطر موافق",
            "drawdown_pct":round(drawdown,2),
            "total_lots":round(total_lots,4),"max_total_lots":max_total_lots}

def _worker(client_id,bar,decision,account):
    # Run both remote/local model calls concurrently; never block MT5 telemetry.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        qwen_future=pool.submit(_qwen,decision)
        deepseek_future=pool.submit(_deepseek,decision)
        qwen=qwen_future.result()
        deepseek=deepseek_future.result()
    with _LOCK:
        item=_VOTES.get(client_id)
        if item and item["bar_time"]==bar:
            item.update({"qwen":qwen,"deepseek":deepseek,"status":"READY",
                         "updated_at":time.time()})

def _groq_worker(client_id,bar,decision):
    # Optional reviewer: no trading permissions and no effect on three-vote approval.
    result=groq_advisor.review(decision)
    with _LOCK:
        current=_VOTES.get(client_id)
        if current and current.get("bar_time")==bar:
            current["groq_advisory"]=result
            current["updated_at"]=time.time()

def update_council(client_id,decision,account):
    """Schedule at most once per closed bar, without blocking MT5 telemetry."""
    bar=int(_num(decision.get("bar_time")))
    if bar<=0:return
    with _LOCK:
        previous=_VOTES.get(client_id)
        if previous and previous["bar_time"]==bar:return
        if len(_VOTES)>=_MAX_ENTRIES:
            oldest=min(_VOTES,key=lambda k:_VOTES[k].get("updated_at",0))
            _VOTES.pop(oldest,None)
        remote_qwen=bool(_QWEN_URL)
        remote_deepseek=bool(_DEEPSEEK_KEY or _DEEPSEEK_LOCAL_URL)
        groq_ready=groq_advisor.configured()
        groq_candidate=groq_advisor.is_candidate(decision)
        _VOTES[client_id]={"bar_time":bar,
            "status":"PENDING" if remote_qwen or remote_deepseek else "AWAITING_LOCAL_VOTES",
            "fast":fast_ai(decision,account),
            "bots":(decision.get("advisory_bots") or {}),
            "qwen":_vote("Qwen","PENDING" if remote_qwen else "NOT_CONFIGURED"),
            "deepseek":_vote("DeepSeek","PENDING" if remote_deepseek else "NOT_CONFIGURED"),
            "groq_advisory":{"model":"GPT-OSS 120B",
                             "status":"PENDING" if groq_ready and groq_candidate else
                                      ("SKIPPED" if groq_ready else "NOT_CONFIGURED"),
                             "direction":"WAIT","confidence":0,"can_execute":False,
                             "role":"advisory_only"},
            "updated_at":time.time()}
    # On owner laptops the local relay supplies both model votes. Never
    # allocate a useless cloud worker when neither remote model is configured.
    if remote_qwen or remote_deepseek:
        threading.Thread(target=_worker,args=(client_id,bar,dict(decision),dict(account)),
                         daemon=True,name="alzool-council").start()
    if groq_ready and groq_candidate:
        threading.Thread(target=_groq_worker,args=(client_id,bar,dict(decision)),
                         daemon=True,name="alzool-groq-advisor").start()

def accept_local_model_vote(client_id,advice,decision):
    """Accept owner-terminal authenticated local Qwen or DeepSeek vote."""
    bar=int(_num(decision.get("bar_time")))
    if bar<=0 or bar!=int(_num(advice.get("bar_time"))):return False
    direction=str(advice.get("direction","WAIT")).upper()
    confidence=_num(advice.get("confidence"),-1)
    model_id=str(advice.get("model",""))
    identity={"qwen3.5:4b":("Qwen","local_qwen"),
              "deepseek-r1:1.5b":("DeepSeek","local_deepseek")}.get(model_id)
    if not identity or direction not in ("BUY","SELL","WAIT") or not 0<=confidence<=1:return False
    name,key=identity
    with _LOCK:
        state=_VOTES.get(client_id)
        if not state or state.get("bar_time")!=bar:return False
        vote=_vote(name,"READY",direction,confidence,advice.get("reason_ar",""))
        vote["model_id"]=model_id
        state[key]=vote
        state[key+"_time"]=time.time()
    return True

def accept_local_qwen_vote(client_id,advice,decision):
    """Backward compatible Qwen vote handler."""
    return accept_local_model_vote(client_id,advice,decision)

def council_result(client_id,decision):
    signal=str(decision.get("decision","WAIT")).upper()
    bar=int(_num(decision.get("bar_time")))
    with _LOCK:
        v=dict(_VOTES.get(client_id) or {})
    if not v or v.get("bar_time")!=bar:
        return {"approved":False,"status":"WAIT","reason_ar":"المجلس لم يصوّت على الشمعة الحالية","votes":v}
    qwen_local=v.get("local_qwen") if time.time()-v.get("local_qwen_time",0)<=30 else None
    deepseek_local=v.get("local_deepseek") if time.time()-v.get("local_deepseek_time",0)<=30 else None
    votes=[v.get("fast",{}),qwen_local or v.get("qwen",{}),deepseek_local or v.get("deepseek",{})]
    approved=(signal in ("BUY","SELL") and all(x.get("status")=="READY"
        and x.get("direction")==signal and _num(x.get("confidence"))>=.5 for x in votes))
    # A precise WAIT reason avoids confusing disconnected/missing votes with
    # actual disagreement. Fail closed; this changes diagnostics, not authority.
    if approved:
        reason="المجلس متعدد النماذج وافق"
    elif signal not in ("BUY","SELL"):
        reason="المحرك الكمي اختار WAIT"
    else:
        problem=next(((name,v) for name,v in zip(("Fast AI","Qwen","DeepSeek"),votes)
            if v.get("status")!="READY" or v.get("direction")!=signal
            or _num(v.get("confidence"))<.5),None)
        if problem is None:reason="المجلس ينتظر موافقة نهائية"
        else:
            name,v=problem
            status=v.get("status","MISSING")
            if status!="READY":
                reason=f"{name}: التصويت غير جاهز ({status})"
            elif v.get("direction")!=signal:
                reason=f"{name}: اتجاه التصويت {v.get('direction','WAIT')} يخالف {signal}"
            else:
                reason=f"{name}: ثقة التصويت أقل من 50%"
    return {"approved":approved,"status":signal if approved else "WAIT",
            "reason_ar":reason,
            "votes":{voter.get("model","unknown"):voter for voter in votes},
            "groq_advisory":v.get("groq_advisory",{"status":"NOT_CONFIGURED",
                 "direction":"WAIT","role":"advisory_only","can_execute":False}),
            "bot_inputs":v.get("bots") if isinstance(v.get("bots"),dict) else {},
            "bot_inputs_role":"read_only_evidence_to_model_council",
            "bar_time":bar}
