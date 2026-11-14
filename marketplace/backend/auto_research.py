"""AL-ZOOL bounded, non-trading automatic research on historical candles.

Once per UTC day per connected engine, evaluates a candidate version against the
current strategy-weight profile. May SAVE a candidate but never activates it.
"""
import threading
import time
import urllib.parse
from datetime import datetime,timezone
from strategy_experiments import sanitize_profile,compare_profiles
from strategy_versions import get_active,save_candidate

_LOCK=threading.RLock()
_RUNNING=set()
_LAST_ATTEMPT={}
_INTERVAL=3600*12

def schedule(client_id,symbol,timeframe,db,learning_builder,deal_snapshot):
    if not str(symbol).upper().startswith("XAUUSD"):return False
    with _LOCK:
        now=time.monotonic()
        if client_id in _RUNNING or now-_LAST_ATTEMPT.get(client_id,-_INTERVAL)<_INTERVAL:
            return False
        _RUNNING.add(client_id)
        _LAST_ATTEMPT[client_id]=now
    thread=threading.Thread(target=_research,
        args=(client_id,symbol,timeframe,db,learning_builder,list(deal_snapshot or [])),
        name="alzool-research",daemon=True)
    thread.start()
    return True

def _research(client_id,symbol,timeframe,db,learning_builder,deals):
    result={"status":"NO_DATA","auto_activated":False}
    try:
        enc=lambda x:urllib.parse.quote(x,safe="")
        rows=db("GET","/rest/v1/alzool_engine_market_candles?client_id=eq."+enc(client_id)+
            "&symbol=eq."+enc(symbol)+"&timeframe=eq."+enc(timeframe)+
            "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume"+
            "&order=candle_time.desc&limit=190",admin=True)
        if len(rows)<75:
            result={"status":"NO_DATA","candles":len(rows),"auto_activated":False}
        else:
            learned=learning_builder(deals)
            candidate=sanitize_profile(learned["weights"],learned.get("combination_weights") or {})
            active=get_active(client_id,db) or {"strategy_weights":{},"combination_weights":{}}
            summary=compare_profiles(list(reversed(rows)),active,candidate)
            current=summary["candidate"]
            stronger=(current["signals"]>=6 and summary["baseline"]["signals"]>=6 and
                     summary["delta_hit_rate"]>=0.05)
            result={"status":"REVIEW_CANDIDATE" if stronger else "NO_EVIDENCE",
                "test":summary,"candles":len(rows),"auto_activated":False}
            if stronger:
                saved=save_candidate(client_id,candidate,{"source":"AUTOMATED_RESEARCH",**summary},db)
                result["version_label"]=saved["version_label"]
    except Exception as exc:
        result={"status":"ERROR","error_type":type(exc).__name__,"auto_activated":False}
    finally:
        try:
            day=datetime.now(timezone.utc).strftime("%Y%m%d")
            event={"event_id":f"research:{client_id}:{day}",
                "client_id":client_id,"event_type":"AI_RESEARCH_RESULT","payload":result}
            db("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
                event,admin=True,prefer="resolution=ignore-duplicates,return=minimal")
        except Exception:pass
        with _LOCK:_RUNNING.discard(client_id)
