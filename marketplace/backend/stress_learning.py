"""Walk-forward stress learning. Read-only research, never an execution permit.

All market candles are actual stored MT5 bars. Extra costs, delay and adverse
gap are counterfactual stress assumptions, not invented historical trades.
One sampled origin counts once regardless of the number of stress scenarios.
"""
import hashlib,json,math,time,urllib.parse
from datetime import datetime,timezone
from strategy_engine import evaluate_market
from btc_adaptive_learning import REGIMES,effective_weights,_add,number,_candle
from market_symbols import market_family

VERSION="STRESS_WALK_FORWARD_V1"
SCENARIOS={"observed":"Actual recorded spread",
           "wide":"2x spread + 0.10 ATR slippage per side",
           "extreme":"5x spread + 0.30 ATR slippage per side",
           "delayed":"Enter one minute late, exit third close, 2x spread + 0.10 ATR per side",
           "gap":"5x spread + 0.30 ATR per side + 0.75 ATR adverse gap"}
MAX_BARS=10000
TRAIN_LIMIT=240
TEST_LIMIT=120

def pick(items,maximum):
    if len(items)<=maximum:return items
    return [items[round(j*(len(items)-1)/(maximum-1))] for j in range(maximum)]

def _spread(bar):
    return number(bar.get("spread_points"))*number(bar.get("point_size"))

def eligible_indices(bars):
    """No overnight/weekend gaps or missing spreads in test window."""
    out=[]
    for i in range(119,len(bars)-3):
        window=bars[i-119:i+4]
        if window[-1]["time"]-window[0]["time"]!=122*60:continue
        if any(b["time"]-a["time"]!=60 for a,b in zip(window,window[1:])):continue
        if any(_spread(b)<=0 for b in bars[i+1:i+4]):continue
        out.append(i)
    return out

def outcomes(bars,i,side,atr):
    if side not in ("BUY","SELL"):return {k:0.0 for k in SCENARIOS}
    a,b,c=bars[i+1:i+4]
    sign=1 if side=="BUY" else -1
    raw=sign*(a["close"]-a["open"])/atr
    cost=_spread(a)/atr
    delayed=sign*(c["close"]-b["open"])/atr
    return {"observed":raw-cost,"wide":raw-2*cost-.20,
            "extreme":raw-5*cost-.60,
            "delayed":delayed-2*_spread(b)/atr-.20,
            "gap":raw-5*cost-.60-.75}

def _profile():
    return {"status":"READY","samples":0,"global":{"s":{},"c":{}},
            "regimes":{r:{"s":{},"c":{},"samples":0} for r in REGIMES}}

def _learn(profile,result,nets,regime):
    profile["samples"]+=1
    profile["regimes"][regime]["samples"]+=1
    for kind,field,upper in (("s","votes",200),("c","combination_votes",1000)):
        for vote in result.get(field,[]):
            ident=str(vote.get("id",""))
            side=vote.get("signal")
            if not ident.isdigit() or not 1<=int(ident)<=upper or side not in ("BUY","SELL"):continue
            net=max(-3.0,min(3.0,nets[side]))
            _add(profile["global"][kind],ident,net)
            _add(profile["regimes"][regime][kind],ident,net)

def fit_and_test(rows,market,evaluator=evaluate_market,pause=.018):
    bars=sorted({int(b["time"]):b for b in rows}.values(),key=lambda b:b["time"])
    # A fixed chronological split, with all three training outcome bars
    # strictly before the holdout. Freeze weights before that holdout.
    split=int(len(bars)*.70)
    eligible=eligible_indices(bars)
    train=pick([i for i in eligible if i+3<split],TRAIN_LIMIT)
    test=pick([i for i in eligible if i>=split],TEST_LIMIT)
    summary={"version":VERSION,"market":market,"candles":len(bars),
             "eligible_windows":len(eligible),"training_windows":0,
             "validation_windows":0,"scenario_count":len(SCENARIOS),
             "scenarios":SCENARIOS,"policy":"RESEARCH_ONLY",
             "actual_trading_proof":False,
             "risk_settings_changed":False}
    if len(train)<24 or len(test)<16:
        return {**summary,"status":"INSUFFICIENT_HISTORY"},{}
    ordinary,robust=_profile(),_profile()
    for i in train:
        result=evaluator(bars[i-119:i+1],market_symbol=market)
        atr=number(result.get("entry_atr"))
        regime=(result.get("context") or {}).get("regime")
        if atr<=0 or regime not in REGIMES:continue
        sides={d:outcomes(bars,i,d,atr) for d in ("BUY","SELL")}
        _learn(ordinary,result,{d:v["observed"] for d,v in sides.items()},regime)
        # Each origin contributes ONE aggregate stress-adjusted observation.
        # These counterfactual costs do not inflate the training sample count.
        _learn(robust,result,{d:sum(v.values())/len(v) for d,v in sides.items()},regime)
        if pause:time.sleep(pause)
    if ordinary["samples"]<24:
        return {**summary,"status":"INSUFFICIENT_HISTORY","training_windows":ordinary["samples"]},{}
    profiles={}
    for name,profile in (("ordinary",ordinary),("stress",robust)):
        profiles[name]={r:effective_weights(profile,{},r) for r in (*REGIMES,"غير محدد")}
    summary["training_windows"]=ordinary["samples"]
    summary["training_last_outcome"]=bars[train[-1]+3]["time"]
    summary["validation_first_origin"]=bars[test[0]]["time"]
    summary["trained_strategies"]=len(robust["global"]["s"])
    summary["trained_combinations"]=len(robust["global"]["c"])
    neutral=profiles["stress"]["غير محدد"]
    summary["changed_strategies"]=len(neutral["strategy_weights"])
    summary["changed_combinations"]=len(neutral["combination_weights"])
    def bucket():
        return {scenario:{"signals":0,"positive":0,"net":0.0,"worst":0.0} for scenario in SCENARIOS}
    totals={name:bucket() for name in ("unweighted","ordinary","stress")}
    regimes={r:{"windows":0,"observed_net":{"unweighted":0.0,"ordinary":0.0,"stress":0.0}} for r in REGIMES}
    changed=0;extreme_windows=0
    for i in test:
        past=bars[i-119:i+1]
        base=evaluator(past,market_symbol=market)
        atr=number(base.get("entry_atr"))
        regime=(base.get("context") or {}).get("regime")
        if atr<=0 or regime not in REGIMES:continue
        decisions={"unweighted":base}
        for name in ("ordinary","stress"):
            w=profiles[name][regime]
            decisions[name]=evaluator(past,market_symbol=market,
                                      weights=w["strategy_weights"],combination_weights=w["combination_weights"])
        changed+=decisions["stress"]["decision"]!=decisions["ordinary"]["decision"]
        summary["validation_windows"]+=1
        regimes[regime]["windows"]+=1
        # Real extreme movement, not a synthetic price series.
        extreme_windows+=max(b["high"]-b["low"] for b in bars[i+1:i+4])>=2*atr
        for name,d in decisions.items():
            side=d["decision"]
            if side not in ("BUY","SELL"):continue
            vals=outcomes(bars,i,side,atr)
            regimes[regime]["observed_net"][name]+=vals["observed"]
            for scenario,net in vals.items():
                result=totals[name][scenario]
                result["signals"]+=1;result["positive"]+=net>0
                result["net"]+=net;result["worst"]=min(result["worst"],net)
        if pause:time.sleep(pause)
    n=summary["validation_windows"]
    for model in totals.values():
        for d in model.values():
            d["hit_rate"]=round(100*d["positive"]/d["signals"],2) if d["signals"] else None
            d["net_per_window"]=round(d["net"]/max(1,n),6)
            d["net"]=round(d["net"],5);d["worst"]=round(d["worst"],5)
    # Research verdict, not an authorization to promote or a profit guarantee.
    ordinary_net=totals["ordinary"]["observed"]["net_per_window"]
    stress_net=totals["stress"]["observed"]["net_per_window"]
    stress_mean=sum(d["net_per_window"] for d in totals["stress"].values())/len(SCENARIOS)
    ordinary_mean=sum(d["net_per_window"] for d in totals["ordinary"].values())/len(SCENARIOS)
    verdict="CANDIDATE_FOR_MORE_TESTS" if n>=50 and stress_net>ordinary_net and stress_mean>ordinary_mean and stress_net>0 else "NOT_VALIDATED"
    summary.update(status="RESEARCH_COMPLETE" if n else "INSUFFICIENT_HISTORY",
                   verdict=verdict,validation=totals,regimes=regimes,
                   changed_decisions=changed,extreme_observed_windows=extreme_windows,
                   stress_observations=n*len(SCENARIOS),
                   active_weights_changed=False)
    # Save fully reproducible learned evidence, not only a scoreboard.
    return summary,{"ordinary":ordinary,"stress":robust,
        "weights":{name:{r:{"strategies":w["strategy_weights"],"combinations":w["combination_weights"]} for r,w in by_regime.items()} for name,by_regime in profiles.items()}}

def _encode(s):return urllib.parse.quote(str(s),safe="")

def load_archive(db,uid,market):
    clients=db("GET","/rest/v1/alzool_engine_clients?user_id=eq."+_encode(uid)+"&market=eq."+market+
               "&revoked=eq.false&select=client_id&limit=1",admin=True)
    if not isinstance(clients,list) or not clients:return None,None,[]
    cid=str(clients[0]["client_id"])
    path="/rest/v1/alzool_engine_market_candles?client_id=eq."+_encode(cid)+"&timeframe=eq.PERIOD_M1&symbol=ilike."+market+"*"
    latest=db("GET",path+"&select=symbol&order=candle_time.desc&limit=1",admin=True)
    if not latest:return cid,None,[]
    symbol=latest[0]["symbol"]
    if market_family(symbol)!=market:raise ValueError("Invalid archive symbol")
    path+="&symbol=eq."+_encode(symbol)+"&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size&order=candle_time.desc"
    rows=[]
    for offset in range(0,MAX_BARS,1000):
        page=db("GET",path+"&limit=1000&offset="+str(offset),admin=True)
        if not isinstance(page,list):raise ValueError("Invalid archive page")
        rows.extend(c for row in page if (c:=_candle(row)) is not None)
        if len(page)<1000:break
    return cid,symbol,sorted({r["time"]:r for r in rows}.values(),key=lambda b:b["time"])

def run_market(db,uid,market):
    cid,symbol,rows=load_archive(db,uid,market)
    if not cid or not symbol:
        return {"status":"INSUFFICIENT_HISTORY","market":market,"policy":"RESEARCH_ONLY"}
    digest=hashlib.sha256(json.dumps([VERSION,market,symbol,rows],sort_keys=True,separators=(",",":")).encode()).hexdigest()[:32]
    event_id="stress-v1:"+hashlib.sha256((cid+"|"+symbol+"|"+digest).encode()).hexdigest()[:40]
    event_path="/rest/v1/alzool_engine_signal_events?client_id=eq."+_encode(cid)+"&event_id=eq."+_encode(event_id)+"&select=payload&limit=1"
    exists=db("GET",event_path,admin=True)
    if exists and isinstance(exists[0].get("payload"),dict):
        saved=exists[0]["payload"]
        if saved.get("version")==VERSION and saved.get("summary",{}).get("status")=="RESEARCH_COMPLETE":
            return {**saved["summary"],"persisted":True,"reused_same_archive":True}
    print("STRESS_LEARNING_BEGIN",market,len(rows),flush=True)
    summary,learned=fit_and_test(rows,market)
    summary.update(symbol=symbol,evaluated_at=datetime.now(timezone.utc).isoformat(),archive_digest=digest)
    if summary["status"]!="RESEARCH_COMPLETE":return summary
    payload={"version":VERSION,"summary":summary,"profiles":learned}
    db("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
       {"event_id":event_id,"client_id":cid,"event_type":"STRESS_LEARNING_PROFILE","payload":payload},
       admin=True,prefer="resolution=ignore-duplicates,return=minimal")
    saved=db("GET",event_path,admin=True)
    if not saved or saved[0].get("payload",{}).get("summary",{}).get("archive_digest")!=digest:
        raise ValueError("Stress profile persistence not confirmed")
    return {**saved[0]["payload"]["summary"],"persisted":True}

def latest_report(db,cid):
    rows=db("GET","/rest/v1/alzool_engine_signal_events?client_id=eq."+_encode(cid)+
        "&event_type=eq.STRESS_LEARNING_PROFILE&select=payload->summary&order=created_at.desc&limit=1",admin=True)
    if rows and isinstance(rows[0].get("summary"),dict):
        return {**rows[0]["summary"],"persisted":True}
    return {"status":"NO_SAVED_REPORT","policy":"RESEARCH_ONLY"}
