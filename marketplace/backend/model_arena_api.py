"""Owner-only arena persistence. Research reports never authorize trading."""
import math, re, urllib.parse, hashlib, json
from datetime import datetime,timezone
from report_cache import archive_read, cutoff
MODELS=("LightGBM","XGBoost","CatBoost","TimesFM-2.5","Chronos-2")
CHALLENGERS=("TiRex-2","Chronos-Bolt Tiny")
CLOUD_CHALLENGERS=("Qwen 3.8 27B","GPT-OSS 120B (Arena)")
LIVE_MODELS=MODELS+CHALLENGERS
REPORT_MODELS=LIVE_MODELS+CLOUD_CHALLENGERS
VERSION="ARENA_V1"
STATUSES=("RUNNING","TESTED","UNAVAILABLE","INSUFFICIENT_HISTORY","LICENSE_REVIEW","ERROR")
def enc(x):return urllib.parse.quote(str(x),safe="")
def ids(sb,uid,market):
    rows=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+enc(uid)+"&market=eq."+market+"&revoked=eq.false&select=client_id&limit=30",admin=True)
    return [str(r["client_id"]) for r in rows or [] if r.get("client_id")]
def query(sb,uid,market,params):
    owned=ids(sb,uid,market)
    if not owned:return {"rows":[],"next_cursor":None}
    cid=(params.get("client") or [""])[0]
    sym=(params.get("symbol") or [""])[0]
    cursor=(params.get("cursor") or [""])[0]
    if not cid:
        best=None
        for c in owned:
            rows=archive_read(sb,"/rest/v1/alzool_engine_market_candles?client_id=eq."+enc(c)+"&timeframe=eq.PERIOD_M1&symbol=ilike."+market+"*&select=symbol,candle_time&order=candle_time.desc&limit=1",60)
            if rows and (best is None or rows[0]["candle_time"]>best[0]):best=(rows[0]["candle_time"],c,rows[0]["symbol"])
        if best is None:return {"rows":[],"next_cursor":None}
        _,cid,sym=best
    if cid not in owned or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}",sym) or not sym.upper().startswith(market):raise PermissionError("invalid owner market")
    q="/rest/v1/alzool_engine_market_candles?client_id=eq."+enc(cid)+"&symbol=eq."+enc(sym)+"&timeframe=eq.PERIOD_M1&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size&order=candle_time.desc&limit=1000"
    if cursor:
        datetime.fromisoformat(cursor.replace("Z","+00:00"))
        q+="&candle_time=lt."+enc(cursor)
    else:q+="&candle_time=lt."+enc(cutoff(seconds=60))
    rows=archive_read(sb,q,300 if cursor else 60)
    return {"client":cid,"symbol":sym,"rows":rows,"next_cursor":rows[-1]["candle_time"] if len(rows)==1000 else None}
def report(sb,uid,market):
    owned=ids(sb,uid,market)
    out={}
    if owned:
        rows=archive_read(sb,"/rest/v1/alzool_engine_signal_events?client_id=in.("+",".join(enc(x) for x in owned)+")&event_type=eq.ARENA_MODEL_REPORT&payload->>market=eq."+market+"&select=payload,created_at&order=created_at.desc&limit=200")
        for row in rows or []:
            p=row.get("payload") or {};name=p.get("model")
            if name in REPORT_MODELS and name not in out:out[name]={**p,"recorded_at":row["created_at"]}
    return {"version":VERSION,"market":market,"models":[out.get(n,{"model":n,"status":"WORKER_NOT_CONNECTED","market":market}) for n in REPORT_MODELS],"execution_authorized":False}
def save(sb,uid,market,data):
    if not isinstance(data,dict) or len(json.dumps(data))>20000:raise ValueError("invalid report")
    name=data.get("model");status=data.get("status")
    cid=data.get("client")
    if name not in REPORT_MODELS or status not in STATUSES or cid not in ids(sb,uid,market):raise ValueError("invalid model or client")
    def score(x):
        if not isinstance(x,dict):return None
        out={}
        for k in ("windows","signals","positive","buy","sell","wait","net_atr","positive_rate","net_per_window","max_drawdown_atr"):
            v=x.get(k)
            if v is None:continue
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError("invalid metric")
            out[k]=v
        return out
    p={"version":VERSION,"model":name,"status":status,"market":market,"source":"OWNER_LOCAL_RESEARCH_WORKER","execution_authorized":False,
       "checked_at":datetime.now(timezone.utc).isoformat(),"metric":"HISTORICAL_3M_SPREAD_PROXY_NOT_BROKER_TRADES"}
    for k in ("candles","train_windows","validation_windows","test_windows","sample_seconds","threshold"):
        v=data.get(k)
        if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):p[k]=v
    for k in ("detail","dataset_id","first_candle","last_candle","test_id","model_version","method"):
        if isinstance(data.get(k),str):p[k]=data[k][:240]
    for k in ("test","baseline_test","validation","stress_test","stress_baseline"):
        s=score(data.get(k))
        if s is not None:p[k]=s
    eid="arena:"+hashlib.sha256((cid+name+p["checked_at"]).encode()).hexdigest()
    sb("POST","/rest/v1/alzool_engine_signal_events",{"event_id":eid,"client_id":cid,"event_type":"ARENA_MODEL_REPORT","payload":p},admin=True,prefer="return=minimal")
    return {"accepted":True,"execution_authorized":False}
