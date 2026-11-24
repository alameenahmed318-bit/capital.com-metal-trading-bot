"""V4 forward-only research: independent local AI and quantitative ranking.

Never changes a live decision, trading settings, authorizations or positions.
A forecast is recorded using the quote observed when it is made, BEFORE the
next candle closes. Its result is a price proxy, not an actual broker fill.
No storage/network work occurs here. The caller queues bounded audit events.
"""
import math
import threading
from market_symbols import market_family

VERSION="AZ_ACCURACY_V4"
PROTOCOL="INDEPENDENT_SHADOW_V4"
_LOCK=threading.RLock()
_PENDING={}
_MAX_PENDING=512

def num(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError,OverflowError):return default

def clamp(x,lo,hi):return max(lo,min(hi,x))

def shadow_rank(decision,rates):
    """Small, bounded candle-context correction; no extra WAIT gate.

    Evaluated only as a shadow candidate until forward evidence exists.
    Market-relative returns make the calculation reusable without sharing
    gold and BTC evidence or parameters.
    """
    scores=decision.get("directional_scores") or {}
    choices=decision.get("directional_candidates") or {}
    context=decision.get("context") or {}
    atr=num(context.get("atr"))
    baseline=str(decision.get("decision","WAIT")).upper()
    eligible=[d for d in ("BUY","SELL") if isinstance(choices.get(d),dict)
              and 1<=int(num(choices[d].get("strategy_id")))<=200]
    if len(rates)<6 or atr<=0 or not eligible:
        return {"direction":baseline,"baseline":baseline,"adjustment":0.0,"version":VERSION}
    closes=[num(x.get("close")) for x in rates[-6:]]
    if min(closes)<=0:
        return {"direction":baseline,"baseline":baseline,"adjustment":0.0,"version":VERSION}
    moves=[b-a for a,b in zip(closes,closes[1:])]
    travel=sum(abs(x) for x in moves)
    efficiency=abs(closes[-1]-closes[0])/travel if travel>0 else 0.0
    momentum=clamp((closes[-1]-closes[-4])/(3*atr),-1,1)
    last=rates[-1]
    hi,lo,op,cl=[num(last.get(k)) for k in ("high","low","open","close")]
    span=hi-lo
    wick_bias=0.0
    if span>0 and lo<=min(op,cl)<=max(op,cl)<=hi:
        wick_bias=((min(op,cl)-lo)-(hi-max(op,cl)))/span
    # Directional context is soft evidence, including reversals in choppy markets.
    shift=clamp(.045*momentum*efficiency+.035*wick_bias,-.08,.08)
    ranking={}
    for d in eligible:
        trap=context.get("buy_trap" if d=="BUY" else "sell_trap") is True
        ranking[d]=num(scores.get(d))+(shift if d=="BUY" else -shift)-(.025 if trap else 0)
    chosen=max(eligible,key=lambda d:ranking[d])
    if len(eligible)==2 and abs(ranking[eligible[0]]-ranking[eligible[1]])<1e-9:
        chosen=baseline if baseline in eligible else "WAIT"
    return {"version":VERSION,"direction":chosen,"baseline":baseline,
            "adjustment":round(shift,6),"efficiency":round(efficiency,6),
            "scores":{k:round(v,6) for k,v in ranking.items()},
            "execution_authorized":False}

def record(client,symbol,timeframe,decision,tick,kind,direction,confidence,now,model=""):
    """Record once per client, symbol, bar, forecast type. First quote wins."""
    if not client or not market_family(symbol) or str(timeframe).upper() not in ("M1","PERIOD_M1"):
        return None
    bar=int(num(decision.get("bar_time")))
    now=num(now)
    # A completed M1 bar begins at bar, closes at bar+60. Forecast horizon
    # ends at bar+120. Never accept a forecast after that close.
    if bar<=0 or not bar+60<=now<bar+120:return None
    bid,ask=num(tick.get("bid")),num(tick.get("ask"))
    if not 0<bid<=ask:return None
    if direction not in ("BUY","SELL","WAIT"):return None
    if kind not in ("QUANT","LOCAL_AI"):return None
    baseline=str(decision.get("decision","WAIT")).upper()
    if baseline not in ("BUY","SELL","WAIT"):return None
    key=(str(client),str(symbol).upper(),bar,kind,str(model)[:80])
    event_id="accuracy-v4:"+":".join(map(str,key))
    confidence=num(confidence,-1)
    forecast={"version":VERSION,"kind":kind,"model":str(model)[:80],
        "symbol":str(symbol),"market":market_family(symbol),"timeframe":"M1",
        "bar_time":bar,"forecast_time_unix":round(now,3),
        "direction":direction,"baseline_direction":baseline,
        "reported_confidence":confidence if 0<=confidence<=1 else None,
        "entry_bid":bid,"entry_ask":ask,"spread_at_forecast":ask-bid,
        "atr":num((decision.get("context") or {}).get("atr")),
        "metric":"NEXT_CLOSE_QUOTE_PROXY_NOT_BROKER_FILL",
        "execution_authorized":False}
    with _LOCK:
        if key in _PENDING:return None
        if len(_PENDING)>=_MAX_PENDING:
            _PENDING.pop(next(iter(_PENDING)),None)
        _PENDING[key]=(event_id,forecast)
    return {"event_id":event_id,"client_id":str(client),
            "event_type":"AI_ACCURACY_V4_FORECAST","payload":dict(forecast)}

def settle(client,symbol,timeframe,candles):
    """Use only the exact next closed bar; no crossing gaps, markets or lanes."""
    if str(timeframe).upper() not in ("M1","PERIOD_M1"):return []
    closed={int(num(c.get("time"))):c for c in candles if isinstance(c,dict)}
    if not closed:return []
    latest=max(closed)
    events=[]
    with _LOCK:
        for key,(eid,f) in list(_PENDING.items()):
            if key[0]!=str(client) or key[1]!=str(symbol).upper():continue
            target=f["bar_time"]+60
            if latest<target:continue
            _PENDING.pop(key,None)
            c=closed.get(target)
            if not c:continue
            close=num(c.get("close"))
            if close<=0:continue
            # MT5 bar close is treated as bid proxy; ask approximated using
            # spread at forecast, not a future known spread. NOT cash P&L.
            def proxy(side):
                if side=="BUY":return close-f["entry_ask"]
                if side=="SELL":return f["entry_bid"]-close-f["spread_at_forecast"]
                return None
            model_net=proxy(f["direction"]);base_net=proxy(f["baseline_direction"])
            atr=f["atr"]
            events.append({"event_id":eid+":outcome","client_id":str(client),
                "event_type":"AI_ACCURACY_V4_OUTCOME","payload":{
                    **f,"next_bar_time":target,"next_close":close,
                    "model_net_price_proxy":model_net,"baseline_net_price_proxy":base_net,
                    "model_net_atr":model_net/atr if model_net is not None and atr>0 else None,
                    "baseline_net_atr":base_net/atr if base_net is not None and atr>0 else None,
                    "model_positive":model_net>0 if model_net is not None else None,
                    "baseline_positive":base_net>0 if base_net is not None else None,
                    "actual_trade_proven":False}})
    return events
