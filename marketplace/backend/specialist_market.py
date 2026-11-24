"""Market-specialized supervised gradient-boosted regression, research only.
Fit historical closed M1 data, tune on validation, test untouched later bars.
No dependency on LLMs, no order authority, no live risk/settings writes.
"""
import bisect,math,json,time,os,tempfile,urllib.parse,hashlib
from datetime import datetime,timezone
from pathlib import Path
from market_symbols import market_family
from strategy_research import _candle

VERSION="SPECIALIST_GBDT_V1"
MARKETS=("XAUUSD","BTCUSD")
REPORT=Path(tempfile.gettempdir())/"alzool-specialist-v1.json"
LOOKBACK=60
HORIZON=3
LIMIT=12000
FEATURES=("return_1","return_5","return_15","return_60","range_atr","body_atr","close_location",
          "volatility_ratio","volume_ratio","spread_atr","hour_sin","hour_cos")

def clamp(x,a=-6,b=6):return max(a,min(b,x))

def features(window):
    if len(window)!=LOOKBACK+1:return None
    if any(b["time"]-a["time"]!=60 for a,b in zip(window,window[1:])):return None
    last=window[-1];p=last["close"]
    ranges=[max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])) for a,b in zip(window,window[1:])]
    atr=sum(ranges[-14:])/14
    if atr<=0 or p<=0:return None
    span=last["high"]-last["low"];vol=sum(x["tick_volume"] for x in window[-20:])/20
    spread=last["spread_points"]*last["point_size"]
    if spread<0 or last["point_size"]<=0:return None
    angle=2*math.pi*((last["time"]%86400)/86400)
    x=[(p-window[-1-n]["close"])/atr for n in (1,5,15,60)]
    x.extend([span/atr,(p-last["open"])/atr,(p-last["low"])/span if span>0 else .5,
      sum(ranges[-5:])/5/atr,last["tick_volume"]/max(vol,1),spread/atr,math.sin(angle),math.cos(angle)])
    return [clamp(v) for v in x],atr

def samples(bars):
    bars=sorted({b["time"]:b for b in bars}.values(),key=lambda b:b["time"])
    out=[]
    for i in range(LOOKBACK,len(bars)-HORIZON):
        if bars[i+HORIZON]["time"]-bars[i]["time"]!=60*HORIZON:continue
        f=features(bars[i-LOOKBACK:i+1])
        if f is None:continue
        x,atr=f;entry=bars[i+1];exit=bars[i+HORIZON]
        entry_cost=entry["spread_points"]*entry["point_size"]
        exit_cost=exit["spread_points"]*exit["point_size"]
        if min(entry["point_size"],exit["point_size"])<=0 or min(entry_cost,exit_cost)<0:continue
        move=(exit["close"]-entry["open"])/atr
        out.append({"time":bars[i]["time"],"end":exit["time"],"x":x,"y":move,
             "buy":move-entry_cost/atr,"sell":-move-exit_cost/atr})
    return out

def predict(model,x):
    return model["bias"]+sum(t["left"] if x[t["feature"]]<=t["cut"] else t["right"] for t in model["trees"])

def fit(train,rounds=24):
    """Gradient boosting of regression stumps; squared loss on clipped ATR return."""
    ys=[clamp(s["y"]) for s in train];n=len(ys)
    bias=sum(ys)/n;pred=[bias]*n;trees=[]
    cuts=[];bins=[]
    for j in range(len(FEATURES)):
        values=sorted(s["x"][j] for s in train)
        c=sorted(set(values[int((n-1)*q/10)] for q in range(1,10)))
        cuts.append(c);bins.append([bisect.bisect_left(c,s["x"][j]) for s in train])
    for _ in range(rounds):
        residual=[y-p for y,p in zip(ys,pred)];total=sum(residual);best=None
        for j,c in enumerate(cuts):
            sums=[0.0]*(len(c)+1);counts=[0]*(len(c)+1)
            for k,r in zip(bins[j],residual):sums[k]+=r;counts[k]+=1
            a=0.0;cnt=0
            for k,cut in enumerate(c):
                a+=sums[k];cnt+=counts[k]
                if min(cnt,n-cnt)<max(30,n//30):continue
                b=total-a;gain=a*a/cnt+b*b/(n-cnt)
                if best is None or gain>best[0]:
                    best=(gain,j,cut,.12*a/cnt,.12*b/(n-cnt))
        if best is None:break
        _,j,cut,left,right=best
        trees.append({"feature":j,"cut":cut,"left":left,"right":right})
        pred=[p+(left if s["x"][j]<=cut else right) for s,p in zip(train,pred)]
        time.sleep(.004)
    return {"bias":bias,"trees":trees}

def choose(value,threshold):
    return "BUY" if value>threshold else "SELL" if value< -threshold else "WAIT"

def score(data,forecasts,threshold):
    n=len(data);net=0;signals=positive=buy=sell=0
    for s,v in zip(data,forecasts):
        side=choose(v,threshold)
        if side=="WAIT":continue
        payoff=s[side.lower()];net+=payoff;signals+=1;positive+=int(payoff>0)
        buy+=int(side=="BUY");sell+=int(side=="SELL")
    return {"windows":n,"signals":signals,"positive":positive,"buy":buy,"sell":sell,"wait":n-signals,
        "positive_rate":round(positive/signals,5) if signals else None,"net_atr":round(net,5),
        "net_per_window":round(net/max(n,1),6)}

def train(bars):
    data=samples(bars);n=len(data)
    base={"version":VERSION,"candles":len(bars),"windows":n,"frame":"M1","horizon_minutes":HORIZON,
       "first_candle":min((b["time"] for b in bars),default=None),"last_candle":max((b["time"] for b in bars),default=None),
       "execution_authorized":False,"metric":"HISTORICAL_NEXT_OPEN_TO_3M_CLOSE_SPREAD_PROXY_NOT_TRADES"}
    if n<600:return {**base,"status":"INSUFFICIENT_HISTORY"},None
    a=int(n*.6);b=int(n*.8)
    # Purge label overlap at both split boundaries. No random shuffle.
    tr=[s for s in data[:a] if s["end"]<data[a]["time"]]
    val=[s for s in data[a:b] if s["end"]<data[b]["time"]];test=data[b:]
    model=fit(tr)
    pv=[predict(model,s["x"]) for s in val]
    pt=[predict(model,s["x"]) for s in test]
    # Threshold selected only on validation; WAIT is a valid alternative.
    options=(.0,.05,.10,.20,.35,.50,1e9)
    threshold=max(options,key=lambda t:score(val,pv,t)["net_atr"])
    # Baseline momentum receives the same independent validation selection.
    bv=[s["x"][1] for s in val];bt=[s["x"][1] for s in test]
    baseline_threshold=max(options,key=lambda t:score(val,bv,t)["net_atr"])
    v=score(val,pv,threshold);t=score(test,pt,threshold);bl=score(test,bt,baseline_threshold)
    report={**base,"status":"TRAINED_RESEARCH_ONLY","model":"GRADIENT_BOOSTED_REGRESSION_STUMPS",
        "features":list(FEATURES),"trees":len(model["trees"]),"threshold":threshold,
        "train_windows":len(tr),"validation_windows":len(val),"test_windows":len(test),
        "train_end":tr[-1]["end"],"validation_start":val[0]["time"],"test_start":test[0]["time"],
        "validation":v,"test":t,"baseline_test":bl,"baseline":"5_MIN_MOMENTUM_TUNED_ON_VALIDATION",
        "verdict":"REVIEW_CANDIDATE" if t["signals"]>=50 and t["net_atr"]>0 and t["net_atr"]>bl["net_atr"] and v["net_atr"]>0 else "NOT_VALIDATED"}
    return report,{**model,"threshold":threshold}

def load(sb,uid,market):
    ids=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(uid,safe="")+
        "&market=eq."+market+"&revoked=eq.false&select=client_id&limit=30",admin=True)
    chosen=None
    for row in ids or []:
        cid=str(row["client_id"]);q="/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(cid,safe="")+"&timeframe=eq.PERIOD_M1&symbol=ilike."+market+"*"
        last=sb("GET",q+"&select=symbol,candle_time&order=candle_time.desc&limit=1",admin=True)
        if last and market_family(last[0]["symbol"])==market and (chosen is None or last[0]["candle_time"]>chosen[0]):
            chosen=(last[0]["candle_time"],cid,last[0]["symbol"],q)
    if not chosen:return None,None,[]
    cutoff,cid,symbol,q=chosen
    q+="&symbol=eq."+urllib.parse.quote(symbol,safe="")+"&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size&order=candle_time.desc&limit=1000"
    bars=[]
    for _ in range(LIMIT//1000):
        batch=sb("GET",q+"&candle_time=lt."+urllib.parse.quote(cutoff,safe=""),admin=True)
        if not isinstance(batch,list):raise ValueError("history response")
        for row in batch:
            try:
                if row.get("spread_points") is None or row.get("point_size") is None:continue
                bars.append(_candle(row))
            except (ValueError,TypeError,KeyError):continue
        if len(batch)<1000:break
        cutoff=batch[-1]["candle_time"]
        time.sleep(.15)
    return cid,symbol,bars

def publish(reports):
    fd,path=tempfile.mkstemp(prefix="alzool-specialist-",dir=REPORT.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(reports,f,allow_nan=False)
        os.replace(path,REPORT)
    finally:
        if os.path.exists(path):os.unlink(path)

def report(market):
    try:
        return json.loads(REPORT.read_text(encoding="utf-8")).get(market,{"status":"LOADING"})
    except (OSError,ValueError):return {"status":"LOADING"}

def run(sb,owner_email):
    """Parent process only, once per hour, independent of terminal connection."""
    reports={};previous={}
    while True:
        try:
            users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
            uid=next(u["id"] for u in users if str(u.get("email") or "").lower()==owner_email.lower())
            for market in MARKETS:
                try:
                    cid,symbol,bars=load(sb,uid,market)
                    fingerprint=hashlib.sha256(json.dumps(sorted(bars,key=lambda b:b["time"]),sort_keys=True).encode()).hexdigest()
                    if market in previous and previous[market][0]==fingerprint:
                        result,model=previous[market][1:]
                        result={**result,"reused_same_archive":True}
                    else:
                        result,model=train(bars)
                        previous[market]=(fingerprint,result,model)
                    stamp=datetime.now(timezone.utc).isoformat()
                    result={**result,"symbol":symbol or market,"market":market,"checked_at":stamp,
                            "archive_age_sec":round(time.time()-result["last_candle"]) if result.get("last_candle") else None}
                    reports[market]={"report":result,"model":model,"client_id":cid}
                    publish(reports)
                    if cid:
                        eid="specialist:"+hashlib.sha256((cid+market+VERSION+fingerprint).encode()).hexdigest()
                        sb("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",
                           {"event_id":eid,"client_id":cid,"event_type":"SPECIALIST_TRAINING","payload":result},
                           admin=True,prefer="resolution=merge-duplicates,return=minimal")
                    print("SPECIALIST_TRAINING",market,result["status"],result.get("verdict"),flush=True)
                except Exception as exc:
                    reports[market]={"report":{"status":"UNAVAILABLE","error":type(exc).__name__}}
                    publish(reports)
                    print("SPECIALIST_WARNING",market,type(exc).__name__,flush=True)
        except Exception as exc:print("SPECIALIST_OWNER_WARNING",type(exc).__name__,flush=True)
        time.sleep(3600)
