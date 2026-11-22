"""BTC-only bounded adaptive learning using the SAME server strategy library.

Historical evidence is a next-bar directional test, NOT portfolio profit or a
copy of the incompatible browser library. Rebuild from recent closed candles;
never add overlapping replays as new independent observations. Live outcomes
are shrunk toward neutral from the first attributed close. No execution permits.
"""
import math
import os
import threading
import time
import hashlib
import urllib.parse
from datetime import datetime, timezone
from market_symbols import market_family, same_market_symbol
from strategy_engine import evaluate_market, analyze_market_context

VERSION="BTC_SERVER_REPLAY_V1"
REGIMES=("اتجاه صاعد","اتجاه هابط","تذبذب جانبي","تقلب مرتفع")
_LOCK=threading.RLock()
_CACHE={}
_BUSY=set()
_ATTEMPT={}
_REFRESH=1800
_MAX_CANDLES=600
_MAX_SAMPLES=80

def number(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError,OverflowError):
        return default

def timeframe_key(tf):
    tf=str(tf or "").upper()
    return tf if tf.startswith("PERIOD_") else "PERIOD_"+tf

def key_for(client,symbol,tf):
    if market_family(symbol) not in (("BTCUSD","XAUUSD") if os.getenv("ALZOOL_MULTI_MARKET","0")=="1" else ("BTCUSD",)) or timeframe_key(tf)!="PERIOD_M1":
        return None
    return str(client),str(symbol),timeframe_key(tf)

def evidence_weight(n,net,absolute,span):
    # Small samples influence softly; no arbitrary minimum for live feedback.
    n=max(0,number(n))
    absolute=max(0,number(absolute))
    if not n or absolute<=1e-12:return 1.0
    return round(1+span*max(-1,min(1,number(net)/absolute))*n/(n+24),6)

def _add(bucket,ident,net):
    item=bucket.setdefault(str(ident),[0,0.0,0.0])
    item[0]+=1;item[1]+=net;item[2]+=abs(net)

def historical_profile(candles,evaluator=evaluate_market,pause=0,market="BTCUSD"):
    """Use past closed windows only and score the following bar net of spread.
    Each sampled next bar occurs once; all 1200 models use exact server IDs.
    """
    if len(candles)<100:return {"status":"INSUFFICIENT_HISTORY","samples":0,"global":{},"regimes":{}}
    candles=candles[-_MAX_CANDLES:]
    indices=list(range(64,len(candles)-1))
    if len(indices)>_MAX_SAMPLES:
        step=(len(indices)-1)/(_MAX_SAMPLES-1)
        indices=sorted({indices[round(i*step)] for i in range(_MAX_SAMPLES)})
    aggregate={"s":{},"c":{}}
    regimes={r:{"s":{},"c":{},"samples":0} for r in REGIMES}
    samples=0
    for i in indices:
        nxt=candles[i+1]
        current=candles[i]
        # A missing M1 interval isn't a valid next-minute observation.
        if int(number(nxt.get("time")))-int(number(current.get("time")))!=60:continue
        spread=number(nxt.get("spread_points"))*number(nxt.get("point_size"))
        if spread<=0:continue
        result=evaluator(candles[max(0,i-119):i+1],market_symbol=market)
        context=result.get("context") or {}
        atr=number(context.get("atr"))
        regime=context.get("regime")
        if atr<=0 or regime not in regimes:continue
        movement=number(nxt.get("close"))-number(nxt.get("open"))
        # Normalize by known ATR before the outcome; cap extreme outliers.
        for name,field in (("s","votes"),("c","combination_votes")):
            for vote in result.get(field,[]):
                ident=str(vote.get("id",""))
                if not ident.isdigit() or not 1<=int(ident)<=(200 if name=="s" else 1000):continue
                signal=vote.get("signal")
                if signal not in ("BUY","SELL"):continue
                net=max(-3,min(3,((movement if signal=="BUY" else -movement)-spread)/atr))
                _add(aggregate[name],ident,net)
                _add(regimes[regime][name],ident,net)
        regimes[regime]["samples"]+=1
        samples+=1
        if pause:time.sleep(pause)
    return {"status":"READY" if samples else "INSUFFICIENT_HISTORY",
            "samples":samples,"global":aggregate,"regimes":regimes,
            "first_bar":candles[0]["time"],"last_bar":candles[-1]["time"]}

def _historical_weights(profile,regime,kind):
    general=(profile.get("global") or {}).get(kind,{})
    specific=((profile.get("regimes") or {}).get(regime) or {}).get(kind,{})
    result={}
    span=.24 if kind=="s" else .085
    for ident in general.keys()|specific.keys():
        a=general.get(ident,[0,0,0]);b=specific.get(ident,[0,0,0])
        # Regime evidence is a shrinkage overlay, not a second independent sample.
        gw=evidence_weight(*a,span)
        rw=evidence_weight(*b,span)
        share=min(.70,number(b[0])/(number(b[0])+16))
        weight=round(gw*(1-share)+rw*share,6)
        if abs(weight-1)>1e-6:result[ident]=weight
    return result

def effective_weights(history,live,regime):
    history=history or {};live=live or {}
    diagnostics={"method":"BTC_SERVER_REGIME_REPLAY_AND_LIVE_OUTCOMES",
        "status":"INSUFFICIENT_EVIDENCE","history_status":history.get("status","LOADING"),
        "historical_samples":int(history.get("samples",0)),
        "regime":regime,
        "regime_samples":int(((history.get("regimes") or {}).get(regime) or {}).get("samples",0)),
        "historical_last_bar":history.get("last_bar"),
        "processed_positions":int(live.get("processed_positions",0)),
        "closed_positions":int(live.get("closed_positions",0)),
        "unmatched_positions":int(live.get("unmatched_positions",0)),
        "coverage_percent":number(live.get("coverage_percent")),
        "last_attributed_at":live.get("last_attributed_at"),"last_closed_at":live.get("last_closed_at"),
        "risk_settings_changed":False,"active_version_preserved":True,
        "historical_model":"same server engine; next-bar movement minus observed spread; no fill/commission simulation",
        "live_regime_scope":"GLOBAL_ONLY_NO_ENTRY_REGIME_IN_LEGACY_DEALS"}
    out={}
    for kind,rows,idfield,maxid,low,high,span,label in (
        ("s","strategies","strategy_id",200,.5,1.5,.30,"strategies"),
        ("c","combinations","combination_id",1000,.85,1.15,.09,"combinations")):
        base=_historical_weights(history,regime,kind) if history.get("status") in ("READY","STALE") else {}
        stats={str(x[idfield]):x for x in live.get(rows,[]) if isinstance(x,dict) and str(x.get(idfield,"")).isdigit() and 1<=int(x[idfield])<=maxid}
        changed=[];weights={};live_count=0
        for ident in base.keys()|stats.keys():
            if not ident.isdigit() or not 1<=int(ident)<=maxid:continue
            item=stats.get(ident,{})
            n=number(item.get("wins"))+number(item.get("losses"))
            observed=evidence_weight(n,item.get("net",0),number(item.get("gross_win"))+number(item.get("gross_loss")),span)
            baseline=base.get(ident,1.0)
            final=round(max(low,min(high,baseline*observed)),6)
            if observed!=1:live_count+=1
            if abs(final-1)>1e-6:
                weights[ident]=final
                changed.append({"id":int(ident),"base":baseline,"learned":observed,"effective":final,"closed_trades":int(n)})
        changed.sort(key=lambda x:abs(x["effective"]-1),reverse=True)
        out["strategy_weights" if kind=="s" else "combination_weights"]=weights
        diagnostics["applied_"+label]=len(weights)
        diagnostics["historical_"+label]=len(base)
        diagnostics["live_"+label]=live_count
        diagnostics["eligible_"+label]=sum(1 for x in stats.values() if number(x.get("wins"))+number(x.get("losses"))>0)
        diagnostics["changed_"+label]=changed[:12]
    if out["strategy_weights"] or out["combination_weights"]:diagnostics["status"]="APPLIED"
    elif diagnostics["historical_samples"] or diagnostics["processed_positions"]:diagnostics["status"]="NEUTRAL_NO_CHANGE"
    diagnostics["reason_ar"]="تعلم تاريخي متوافق مع محرك البيتكوين وتعزيز تدريجي من نتائج صفقات MT5 المنسوبة؛ لا يغيّر الحماية أو صلاحية الدخول."
    out["diagnostics"]=diagnostics
    return out

def _candle(row):
    try:
        stamp=int(datetime.fromisoformat(str(row["candle_time"]).replace("Z","+00:00")).timestamp())
        result={"time":stamp,"open":float(row["candle_open"]),"high":float(row["candle_high"]),
            "low":float(row["candle_low"]),"close":float(row["candle_close"]),
            "tick_volume":number(row.get("tick_volume")),"spread_points":number(row.get("spread_points")),
            "point_size":number(row.get("point_size"))}
        if not all(math.isfinite(v) for v in result.values()):return None
        if result["high"]<max(result["open"],result["close"]) or result["low"]>min(result["open"],result["close"]):return None
        return result
    except (KeyError,ValueError,TypeError,OverflowError):return None

def _event_id(key):
    return "btc-learn-v1:"+hashlib.sha256("|".join(key).encode()).hexdigest()[:40]

def _refresh(key,db):
    client,symbol,tf=key
    enc=lambda x:urllib.parse.quote(x,safe="")
    phase="restore"
    try:
        # Restore a persisted profile only for this exact account/symbol/timeframe.
        with _LOCK: existing=_CACHE.get(key)
        if not existing:
            events=db("GET","/rest/v1/alzool_engine_signal_events?client_id=eq."+enc(client)+
                "&event_id=eq."+enc(_event_id(key))+"&select=payload&limit=1",admin=True)
            if events and isinstance(events[0].get("payload"),dict):
                saved=events[0]["payload"]
                if saved.get("version")==VERSION and saved.get("symbol")==symbol and saved.get("timeframe")==tf:
                    with _LOCK:_CACHE[key]=saved
        phase="history_read"
        rows=db("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+enc(client)+
            "&symbol=eq."+enc(symbol)+"&timeframe=eq."+enc(tf)+
            "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size"+
            "&order=candle_time.desc&limit="+str(_MAX_CANDLES),admin=True)
        by_time={x["time"]:x for row in rows if (x:=_candle(row)) is not None}
        candles=[by_time[t] for t in sorted(by_time)]
        phase="replay"
        profile=historical_profile(candles,pause=.015,market=market_family(symbol))
        profile.update({"version":VERSION,"symbol":symbol,"timeframe":tf,"updated_at":datetime.now(timezone.utc).isoformat()})
        with _LOCK:_CACHE[key]=profile
        phase="persist"
        db("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
            {"event_id":_event_id(key),"client_id":client,"event_type":"BTC_LEARNING_PROFILE","payload":profile},
            admin=True,prefer="resolution=merge-duplicates,return=minimal")
        print("BTC_LEARNING_READY:",symbol,profile["samples"],flush=True)
    except Exception as exc:
        with _LOCK:
            previous=_CACHE.get(key)
            if previous and previous.get("status") in ("READY","STALE"):
                _CACHE[key]={**previous,"status":"STALE","error":type(exc).__name__}
            else:_CACHE[key]={"status":"UNAVAILABLE","error":type(exc).__name__}
        # Identify the failing stage without exposing credentials or account data.
        print("BTC_LEARNING_WARNING:",phase,type(exc).__name__,str(exc)[:160] if isinstance(exc,ValueError) else "",flush=True)
    finally:
        with _LOCK:_BUSY.discard(key)

def snapshot(client,symbol,tf,db,refresh=False):
    key=key_for(client,symbol,tf)
    if key is None:return {"status":"UNSUPPORTED_MARKET_OR_TIMEFRAME"}
    with _LOCK:
        now=time.monotonic()
        state=(_CACHE.get(key) or {}).get("status")
        interval=60 if state in ("UNAVAILABLE","STALE") else _REFRESH
        due=now-_ATTEMPT.get(key,-_REFRESH)>=interval
        if (due or refresh) and key not in _BUSY:
            # Global bound avoids a burst of 80 evaluations per client.
            if len(_BUSY)<1:
                _BUSY.add(key);_ATTEMPT[key]=now
                threading.Thread(target=_refresh,args=(key,db),daemon=True,name="btc-adaptive-learning").start()
        result=dict(_CACHE.get(key) or {"status":"LOADING"})
        result["refreshing"]=key in _BUSY
        return result
