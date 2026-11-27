"""Demo-only streaming entry learner; broker execution remains in existing gates.
Labels are observed quote counterfactuals, not attributed broker-trade profits.
No network, training, disk I/O or waits on the request/decision thread.
"""
import hashlib,json,math,os,queue,threading,time,urllib.parse
from pathlib import Path
from collections import deque
NAMES=("Extra Trees","TiRex-2","CatBoost","Chronos-Bolt Tiny","TCN","GRU","Patch Transformer")
PARALLEL=NAMES[4:]
VERSION="HYBRID_META_DEMO_V1"
SIDES=("BUY","SELL")
MIN_TRAIN=150
RETRAIN=1800
JOBS=queue.Queue(maxsize=64)
LOCK=threading.Lock()
PUBLISHED={}
STARTED=False
STATE={}
RECENT={}
MEMORY_DB=None
MEMORY_JOBS=queue.Queue(maxsize=128)
MEMORY_STARTED=False
MEMORY_HEALTH={}
MEMORY_EVENT="HYBRID_META_SCALP_OUTCOME_V1"
def configure_memory(db):
    global MEMORY_DB
    MEMORY_DB=db
def memory_id(key,bar):
    return "meta-scalp:"+hashlib.sha256(json.dumps([*key,int(bar)],separators=(",",":")).encode()).hexdigest()
def durable_load(key):
    if MEMORY_DB is None:return []
    cid,symbol=key
    query="/rest/v1/alzool_engine_signal_events?"+urllib.parse.urlencode({"client_id":"eq."+cid,"event_type":"eq."+MEMORY_EVENT,"payload->>symbol":"eq."+symbol,"select":"payload","order":"created_at.desc","limit":600})
    data=MEMORY_DB("GET",query,admin=True)
    rows=[]
    for item in data or []:
        p=item.get("payload") or {}
        row=p.get("sample")
        if isinstance(row,dict) and p.get("symbol")==symbol and tuple(row.get("key") or ())==key and "y" in row and "raw" in row and "context" in row:rows.append(row)
    return rows
def durable_save(key,row):
    if MEMORY_DB is None:return
    event={"event_id":memory_id(key,row["bar"]),"client_id":key[0],"event_type":MEMORY_EVENT,"payload":{"symbol":key[1],"sample":row}}
    MEMORY_DB("POST","/rest/v1/alzool_engine_signal_events?on_conflict=event_id",event,admin=True,prefer="resolution=ignore-duplicates,return=minimal")
def memory_loop():
    while True:
        key,row=MEMORY_JOBS.get()
        try:
            for attempt in range(3):
                try:
                    durable_save(key,row)
                    MEMORY_HEALTH[key]="DURABLE"
                    break
                except Exception as exc:
                    MEMORY_HEALTH[key]="RETRY_LOCAL_FALLBACK"
                    if attempt==2:
                        print("META_MEMORY_WRITE_WARNING",type(exc).__name__,flush=True)
                    else:time.sleep(2**attempt)
        finally:MEMORY_JOBS.task_done()
def queue_memory(key,row):
    global MEMORY_STARTED
    if MEMORY_DB is None:return
    if not MEMORY_STARTED:
        threading.Thread(target=memory_loop,daemon=True,name="meta-durable-memory").start();MEMORY_STARTED=True
    try:MEMORY_JOBS.put_nowait((key,row))
    except queue.Full:MEMORY_HEALTH[key]="QUEUE_FULL_LOCAL_FALLBACK"

ROOT=Path(os.getenv("ALZOOL_META_STATE_DIR",str(Path.home()/".alzool-meta-demo")))
def num(v,default=0.):
    try:
        if isinstance(v,bool):return default
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError,ValueError,OverflowError):return default
def allowed(item):
    return (os.getenv("ALZOOL_HYBRID_DEMO")=="1" and item.get("master_direct_demo_capable") is True
            and (item.get("account") or {}).get("trade_mode")=="DEMO"
            and str(item.get("timeframe","")).upper().replace("PERIOD_","")=="M1"
            and bool(item.get("client_id")))
def collect(d,item,parallel,now):
    if not allowed(item):return None
    symbol=d.get("symbol");bar=num(d.get("bar_time"));age=now-bar-60
    if not symbol or symbol!=item.get("symbol") or not 0<=age<60:return None
    tick=item.get("tick") or {};bid=num(tick.get("bid"));ask=num(tick.get("ask"))
    atr=num(d.get("entry_atr") or (d.get("context") or {}).get("atr"))
    if not 0<bid<=ask or atr<=0:return None
    def fresh(e):
        return (isinstance(e,dict) and e.get("status")=="SHADOW_READY"
          and e.get("symbol")==symbol and num(e.get("bar_time"))==bar
          and 0<=now-num(e.get("received_at"),-1)<=60)
    raw={};monitor={};horizons={}
    arena=(d.get("master") or {}).get("arena_shadow") or {}
    if fresh(arena):
        for n,v in (arena.get("models") or {}).items():
            if n in NAMES[:4] and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                raw[n]=max(-6.,min(6.,float(v)))
        for n,v in (arena.get("monitor_models") or {}).items():
            if n in NAMES[:4] and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):monitor[n]=max(-6.,min(6.,float(v)))
    if fresh(arena):
        horizons.update({n:int((arena.get("horizons") or {}).get(n,3)) for n in set(raw)|set(monitor)})
    # Caller obtains server-accepted parallel snapshot, never browser votes.
    if fresh(parallel) and parallel.get("client_id")==str(item["client_id"]):
        for n,m in (parallel.get("models") or {}).items():
            if n in PARALLEL and isinstance(m,dict):
                v=m.get("forecast")
                if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                    target=monitor if m.get("status")=="MONITOR_ONLY" else raw
                    target[n]=max(-6.,min(6.,float(v)))
                    horizons[n]=int(m.get("horizon",3))
    ctx=d.get("context") or {};scores=d.get("directional_scores") or {}
    return {"key":(str(item["client_id"]),symbol),"bar":int(bar),"at":now,"bid":bid,"ask":ask,"atr":atr,
        "raw":raw,"monitor":monitor,"horizons":horizons,"context":[num(scores.get("BUY")),num(scores.get("SELL")),num(ctx.get("buy_quality"))/100.,
            num(ctx.get("sell_quality"))/100.,(ask-bid)/atr,age/60.]}
def all_forecasts(row):
    """Worker validation labels are provenance, never a master participation veto."""
    return {n:v for n,v in {**(row.get("monitor") or {}),**(row.get("raw") or {})}.items()
            if n in NAMES and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)}

def calibrated_strength(signed,mass,profile=None):
    # Shrink weak evidence smoothly. Saturation requires a genuinely large
    # aggregate margin; this is a directional score, never a probability.
    s=num(signed);m=max(abs(s),num(mass))
    return max(-.95,min(.95,s/(.15+.65*m)))

def performance_profile(rows):
    """Master-only bounded weights from matured same-market quote outcomes.
    No local approval gate and no permanent zero. Missing predictions are not
    reallocated to the remaining models. These are proxy scores, not deal P&L.
    """
    factors={};statuses={};counts={}
    for n in NAMES:
        outcomes=[]
        for r in rows[-240:]:
            v=all_forecasts(r).get(n)
            if v is not None and abs(v)>.05:
                outcomes.append(r["y"][0 if v>0 else 1]-.3)
        q=outcomes[-120:];counts[n]=len(q)
        factor=.75;status="COLLECTING"
        if len(q)>=30:
            # Shrink toward neutral with small samples; repeated losses
            # reduce influence but do not remove future learning evidence.
            mean=sum(q)/len(q)
            factor=max(.15,min(1.5,.75+mean*len(q)/(len(q)+60)))
            status="REDUCED" if factor<.60 else "STRONGER" if factor>.90 else "ADAPTIVE"
        factors[n]=factor;statuses[n]=status
    total=sum(factors.values())
    return {"weights":{n:factors[n]/total for n in NAMES},"statuses":statuses,"counts":counts}

def vector(row,excluded=()):
    x=[];forecasts=all_forecasts(row)
    weights=row.get("model_weights") or {}
    for n in NAMES:
        available=n in forecasts and n not in excluded
        scale=len(NAMES)*num(weights.get(n),1./len(NAMES))
        x.extend([forecasts[n]*scale if available else 0.,float(available)])
    return x+row["context"]
def choose_blend(a,b,w):
    return [w*a[i]+(1-w)*b[i] for i in range(2)]
def prediction(m,x):
    return [float(v) for v in m.predict([x+[0.],x+[1.]])]
def score(p,y,stress=.3):
    side=max(range(2),key=lambda i:p[i])
    return y[side]-stress if p[side]>0 else 0.
def path_for(key):
    return ROOT/(hashlib.sha256(repr(key).encode()).hexdigest()+".json")
def load(key):
    try:
        x=json.loads(path_for(key).read_text(encoding="utf-8"))
        if x.get("version")!=VERSION:return []
        rows=x["rows"]
        return [r for r in rows[-3000:] if isinstance(r,dict) and "y" in r and "raw" in r and "context" in r]
    except Exception:return []
def save(key,rows):
    ROOT.mkdir(parents=True,exist_ok=True)
    p=path_for(key);tmp=p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version":VERSION,"rows":rows[-3000:]},allow_nan=False),encoding="utf-8")
    os.replace(tmp,p)
def retired(rows):
    # Retire only sustained negative observed quote evidence, not one loss.
    out=[]
    for n in NAMES:
        samples=[]
        for r in rows:
            v=all_forecasts(r).get(n,0.)
            if abs(v)>.05:samples.append(r["y"][0 if v>0 else 1]-.3)
        if len(samples)>=60 and sum(samples[-60:-30])<0 and sum(samples[-30:])<0:out.append(n)
    return tuple(out)
def memory_headroom():
    # In a constrained container avoid loading native ML libraries if the
    # cgroup cannot accommodate their peak footprint. Unknown limits are
    # treated conservatively when the resource guard is requested.
    try:
        limit=Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if limit!="max":
            return int(limit)-int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
    except (OSError,ValueError):
        pass
    try:
        return int(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text())-int(Path("/sys/fs/cgroup/memory/memory.usage_in_bytes").read_text())
    except (OSError,ValueError):
        return None

def training_resources_ready():
    if os.getenv("ALZOOL_META_RESOURCE_GUARD")!="1":return True
    available=memory_headroom()
    return available is not None and available>=400*1024*1024

def train(rows,excluded):
    from catboost import CatBoostRegressor
    import xgboost as xgb
    import numpy as np
    # Purge the chronological split by the actual outcome time.
    cut=int(len(rows)*.8);v=rows[cut:];t=[r for r in rows[:cut] if r["end"]<v[0]["at"]]
    if len(t)<100 or len(v)<30:return None
    x=[];y=[]
    for r in t:
        vec=vector(r,excluded)
        for i in range(2):x.append(vec+[float(i)]);y.append(max(-6.,min(6.,r["y"][i])))
    a=CatBoostRegressor(iterations=100,depth=4,learning_rate=.05,thread_count=1,verbose=False,allow_writing_files=False,random_seed=42)
    a.fit(x,y)
    booster=xgb.train({"max_depth":3,"eta":.05,"nthread":1,"seed":42,"objective":"reg:squarederror","tree_method":"hist"},xgb.DMatrix(np.asarray(x,dtype=np.float32),label=np.asarray(y,dtype=np.float32),nthread=1),num_boost_round=100)
    class NativePredictor:
        def predict(self,values):
            return booster.inplace_predict(np.asarray(values,dtype=np.float32))
    b=NativePredictor()
    pa=[prediction(a,vector(r,excluded)) for r in v];pb=[prediction(b,vector(r,excluded)) for r in v]
    # Both participate; weights are selected on prior validation only.
    w=max((.25,.5,.75),key=lambda w:sum(score(choose_blend(u,z,w),r["y"]) for u,z,r in zip(pa,pb,v)))
    return (a,b,w)
def work(row):
    key=row["key"];at=row["at"]
    s=STATE.get(key)
    if s is None:
        if len(STATE)>=16:STATE.pop(next(iter(STATE)))
        local=load(key)
        try:
            remote=durable_load(key)
            rows=sorted({int(r["bar"]):r for r in local+remote}.values(),key=lambda r:r["bar"])[-3000:]
            MEMORY_HEALTH[key]="RESTORED" if MEMORY_DB is not None else "LOCAL_ONLY"
        except Exception as exc:
            rows=local;MEMORY_HEALTH[key]="READ_FAILED_LOCAL_FALLBACK"
            print("META_MEMORY_READ_WARNING",type(exc).__name__,flush=True)
        s=STATE[key]={"rows":rows,"pending":None,"model":None,"next":0.,"memory_retry":at+60}
        # Backfill any surviving local samples after restart without blocking entry.
        for r in local[-60:]:queue_memory(key,r)
    rows=s["rows"];p=s["pending"]
    if MEMORY_DB is not None and MEMORY_HEALTH.get(key)=="READ_FAILED_LOCAL_FALLBACK" and at>=s["memory_retry"]:
        s["memory_retry"]=at+120
        try:
            remote=durable_load(key)
            rows[:]=sorted({int(r["bar"]):r for r in rows+remote}.values(),key=lambda r:r["bar"])[-3000:]
            MEMORY_HEALTH[key]="RESTORED"
        except Exception:pass
    if p and at>=p["at"]+60:
        if at<=p["at"]+75 and not any(r["bar"]==p["bar"] for r in rows):
            rows.append({**p,"end":at,"y":[(row["bid"]-p["ask"])/p["atr"],(p["bid"]-row["ask"])/p["atr"]]})
            del rows[:-3000]
            queue_memory(key,rows[-1])
            try:save(key,rows)
            except Exception:pass
        s["pending"]=None
    # A one-minute adviser and an old three-minute adviser are not the same
    # training distribution. Retain old samples; train and weight the new
    # configuration only on matching horizons. Unknown historical tags = 3.
    signature={n:int(row.get("horizons",{}).get(n,3)) for n in all_forecasts(row)}
    def compatible(r):
        h=r.get("horizons") or {}
        return all(int(h.get(n,3))==signature.get(n,int(h.get(n,3))) for n in all_forecasts(r))
    eligible_rows=[r for r in rows if compatible(r)]
    profile=performance_profile(eligible_rows)
    row={**row,"model_weights":profile["weights"]}
    if not s["pending"] and all_forecasts(row) and (not rows or row["bar"]>rows[-1]["bar"]):s["pending"]=row
    # The master manages relative influence; no automatic hard retirement.
    excluded=()
    if len(eligible_rows)>=MIN_TRAIN and (at>=s["next"] or (s["model"] and s["model"][1]!=excluded and at>=s.get("exclude_retry",0.))):
        s["next"]=at+RETRAIN;s["exclude_retry"]=at+60
        if not training_resources_ready():
            s["error"]="INSUFFICIENT_MEMORY_HEADROOM"
            s["next"]=at+300
        else:
            try:
                fitted=train(eligible_rows,excluded)
                if fitted:
                    s["model"]=(fitted,excluded)
                    s["model_horizons"]=dict(signature)
                    s.pop("error",None)
            except Exception as exc:
                s["error"]=type(exc).__name__;s["next"]=at+600
    m=s["model"]
    if m and any(s.get("model_horizons",{}).get(n,3)!=h for n,h in signature.items()):
        s["model"]=m=None;s["next"]=0.
    if not m or not all_forecasts(row) or m[1]!=excluded:
        result={"status":"RESOURCE_LIMIT" if s.get("error")=="INSUFFICIENT_MEMORY_HEADROOM" else "BOOTSTRAP","samples":len(eligible_rows),"excluded":list(excluded),"error":s.get("error")}
    else:
        a,b,w=m[0];x=vector(row,excluded)
        if not any(n not in excluded for n in all_forecasts(row)):result={"status":"NO_ADVICE","samples":len(rows),"excluded":list(excluded)}
        else:
            ca=prediction(a,x);xb=prediction(b,x);p=choose_blend(ca,xb,w)
            side=max(range(2),key=lambda i:p[i])
            result={"status":"READY","direction":SIDES[side] if p[side]>0 else "WAIT",
                "predicted_atr":dict(zip(SIDES,p)),"catboost_weight":w,"xgboost_weight":1-w,
                "samples":len(rows),"excluded":list(excluded),"advisers":[n for n in all_forecasts(row) if n not in excluded]}
    with LOCK:
        if len(PUBLISHED)>=32 and key not in PUBLISHED:PUBLISHED.pop(next(iter(PUBLISHED)))
        PUBLISHED[key]={**result,"memory_status":MEMORY_HEALTH.get(key,"LOCAL_ONLY"),"stored_samples":len(rows),"model_profile":profile,"forecast_snapshot":all_forecasts(row),"bar":row["bar"],"at":at,"version":VERSION}
def loop():
    while True:
        row=JOBS.get()
        try:work(row)
        except Exception as exc:print("HYBRID_META_DEMO_WARNING",type(exc).__name__,flush=True)
        finally:JOBS.task_done()
def submit(row):
    global STARTED
    if row is None:return {}
    key=row["key"];now=row["at"]
    with LOCK:
        if not STARTED:
            threading.Thread(target=loop,daemon=True,name="meta-demo-worker").start();STARTED=True
        last=RECENT.get(key,0.)
        if now-last>=2:
            try:
                JOBS.put_nowait(row);RECENT[key]=now
                if len(RECENT)>64:RECENT.pop(next(iter(RECENT)))
            except queue.Full:pass
        p=dict(PUBLISHED.get(key) or {})
    return p if p.get("bar")==row["bar"] and 0<=now-num(p.get("at"),-1)<=10 and p.get("forecast_snapshot")==all_forecasts(row) else {"status":"PENDING","excluded":[],"samples":p.get("samples",0),"model_profile":p.get("model_profile") or performance_profile([]),"memory_status":p.get("memory_status","LOADING")}
