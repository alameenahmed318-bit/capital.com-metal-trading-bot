"""AL-ZOOL central multi-model voting engine.

Fast AI is deterministic and immediate. Deep AI uses opt-in Qwen and DeepSeek
connections. No remote model is marked approved when unavailable. The trading
gate must require both matching fresh votes; this module NEVER places orders.
"""
import json
import math
import os
import threading
import time
import urllib.request

_LOCK=threading.RLock()
_VOTES={}
_MAX_ENTRIES=80
_QWEN_URL=os.getenv("QWEN_API_URL","").strip()
_QWEN_MODEL=os.getenv("QWEN_MODEL","qwen2.5:3b").strip()
_DEEPSEEK_KEY=os.getenv("DEEPSEEK_API_KEY","").strip()
_DEEPSEEK_MODEL=os.getenv("DEEPSEEK_MODEL","deepseek-chat").strip()
_DEEPSEEK_URL="https://api.deepseek.com/chat/completions"
_TIMEOUT=4

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
            "stream":False,"format":"json","options":{"num_predict":150,"temperature":0}},
            {},timeout=_TIMEOUT)
        return _parse("Qwen",result.get("response",""))
    except Exception as exc:
        return _vote("Qwen","ERROR",reason="فشل اتصال Qwen: "+type(exc).__name__)

def _deepseek(decision):
    if not _DEEPSEEK_KEY:return _vote("DeepSeek","NOT_CONFIGURED",reason="DEEPSEEK_API_KEY غير مضبوط")
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
    if signal not in ("BUY","SELL") or confidence<.45 or atr<=0 or price<=0 or supports<=oppose:
        return _vote("Fast AI","READY",reason="التحليل الكمي لا يجيز الدخول")
    if _num(account.get("equity"))<=0 or _num(account.get("free_margin"))<=0:
        return _vote("Fast AI","READY",reason="حارس المخاطر: الحساب غير آمن")
    return _vote("Fast AI","READY",signal,confidence,"توافق السعر وATR والعائلات وسلامة الحساب")

def risk_guardian(account,positions=None,limits=None):
    limits=limits or {}
    balance=_num(account.get("balance"));equity=_num(account.get("equity"))
    free=_num(account.get("free_margin"))
    if balance<=0 or equity<=0 or free<=0:
        return {"approved":False,"reason_ar":"الرصيد أو الهامش غير آمن"}
    max_drawdown=max(0,min(100,_num(limits.get("max_drawdown_pct",5),5)))
    drawdown=max(0,(balance-equity)/balance*100)
    if drawdown>max_drawdown:
        return {"approved":False,"reason_ar":"تجاوز حد تراجع حقوق الملكية"}
    max_positions=max(0,int(_num(limits.get("max_positions",10),10)))
    if len(positions or [])>=max_positions:
        return {"approved":False,"reason_ar":"تجاوز عدد الصفقات المسموح"}
    return {"approved":True,"reason_ar":"حارس المخاطر موافق",
            "drawdown_pct":round(drawdown,2)}

def _worker(client_id,bar,decision,account):
    qwen=_qwen(decision)
    deepseek=_deepseek(decision)
    with _LOCK:
        item=_VOTES.get(client_id)
        if item and item["bar_time"]==bar:
            item.update({"qwen":qwen,"deepseek":deepseek,"status":"READY",
                         "updated_at":time.time()})

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
        _VOTES[client_id]={"bar_time":bar,"status":"PENDING",
            "fast":fast_ai(decision,account),"qwen":_vote("Qwen","PENDING"),
            "deepseek":_vote("DeepSeek","PENDING"),"updated_at":time.time()}
    threading.Thread(target=_worker,args=(client_id,bar,dict(decision),dict(account)),
                     daemon=True,name="alzool-council").start()

def council_result(client_id,decision):
    signal=str(decision.get("decision","WAIT")).upper()
    bar=int(_num(decision.get("bar_time")))
    with _LOCK:
        v=dict(_VOTES.get(client_id) or {})
    if not v or v.get("bar_time")!=bar:
        return {"approved":False,"status":"WAIT","reason_ar":"المجلس لم يصوّت على الشمعة الحالية","votes":v}
    votes=[v.get("fast",{}),v.get("qwen",{}),v.get("deepseek",{})]
    approved=(signal in ("BUY","SELL") and all(x.get("status")=="READY"
        and x.get("direction")==signal and _num(x.get("confidence"))>=.5 for x in votes))
    return {"approved":approved,"status":signal if approved else "WAIT",
            "reason_ar":"المجلس متعدد النماذج وافق" if approved else
                        "بانتظار توافق Fast AI وQwen وDeepSeek على نفس الاتجاه",
            "votes":{voter.get("model","unknown"):voter for voter in votes},
            "bar_time":bar}
