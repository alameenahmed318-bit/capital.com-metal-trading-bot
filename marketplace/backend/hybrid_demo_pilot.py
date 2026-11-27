"""Demo-only streaming entry learner; broker execution remains in existing gates.
Labels are observed quote counterfactuals, not attributed broker-trade profits.
No network, training, disk I/O or waits on the request/decision thread.
"""
import hashlib,json,math,os,queue,threading,time
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
    raw={};monitor={}
    arena=(d.get("master") or {}).get("arena_shadow") or {}
    if fresh(arena):
        for n,v in (arena.get("models") or {}).items():
            if n in NAMES[:4] and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                raw[n]=max(-6.,min(6.,float(v)))
        for n,v in (arena.get("monitor_models") or {}).items():
            if n in NAMES[:4] and isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):monitor[n]=max(-6.,min(6.,float(v)))
    # Caller obtains server-accepted parallel snapshot, never browser votes.
    if fresh(parallel) and parallel.get("client_id")==str(item["client_id"]):
        for n,m in (parallel.get("models") or {}).items():
            if n in PARALLEL and isinstance(m,dict):
                v=m.get("forecast")
                if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
                    target=monitor if m.get("status")=="MONITOR_ONLY" else raw
                    target[n]=max(-6.,min(6.,float(v)))
    ctx=d.get("context") or {};scores=d.get("directional_scores") or {}
    return {"key":(str(item["client_id"]),symbol),"bar":int(bar),"at":now,"bid":bid,"ask":ask,"atr":atr,
        "raw":raw,"monitor":monitor,"context":[num(scores.get("BUY")),num(scores.get("SELL")),num(ctx.get("buy_quality"))/100.,
            num(ctx.get("sell_quality"))/100.,(ask-bid)/atr,age/60.]}
def vector(row,excluded=()):
    x=[]
    for n in NAMES:
        available=n in row["raw"] and n not in excluded
        x.extend([row["raw"][n] if available else 0.,float(available)])
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
            v=r["raw"].get(n,r.get("monitor",{}).get(n,0.))
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
        s=STATE[key]={"rows":load(key),"pending":None,"model":None,"next":0.}
    rows=s["rows"];p=s["pending"]
    if p and at>=p["at"]+60:
        if at<=p["at"]+75 and not any(r["bar"]==p["bar"] for r in rows):
            rows.append({**p,"end":at,"y":[(row["bid"]-p["ask"])/p["atr"],(p["bid"]-row["ask"])/p["atr"]]})
            del rows[:-3000]
            try:save(key,rows)
            except Exception:pass
        s["pending"]=None
    if not s["pending"] and (row["raw"] or row.get("monitor")) and (not rows or row["bar"]>rows[-1]["bar"]):s["pending"]=row
    excluded=retired(rows)
    if len(rows)>=MIN_TRAIN and (at>=s["next"] or (s["model"] and s["model"][1]!=excluded and at>=s.get("exclude_retry",0.))):
        s["next"]=at+RETRAIN;s["exclude_retry"]=at+60
        if not training_resources_ready():
            s["error"]="INSUFFICIENT_MEMORY_HEADROOM"
            s["next"]=at+300
        else:
            try:
                fitted=train(rows,excluded)
                if fitted:
                    s["model"]=(fitted,excluded)
                    s.pop("error",None)
            except Exception as exc:
                s["error"]=type(exc).__name__;s["next"]=at+600
    m=s["model"]
    if not m or not row["raw"] or m[1]!=excluded:
        result={"status":"RESOURCE_LIMIT" if s.get("error")=="INSUFFICIENT_MEMORY_HEADROOM" else "BOOTSTRAP","samples":len(rows),"excluded":list(excluded),"error":s.get("error")}
    else:
        a,b,w=m[0];x=vector(row,excluded)
        if not any(n not in excluded for n in row["raw"]):result={"status":"NO_ADVICE","samples":len(rows),"excluded":list(excluded)}
        else:
            ca=prediction(a,x);xb=prediction(b,x);p=choose_blend(ca,xb,w)
            side=max(range(2),key=lambda i:p[i])
            result={"status":"READY","direction":SIDES[side] if p[side]>0 else "WAIT",
                "predicted_atr":dict(zip(SIDES,p)),"catboost_weight":w,"xgboost_weight":1-w,
                "samples":len(rows),"excluded":list(excluded),"advisers":[n for n in row["raw"] if n not in excluded]}
    with LOCK:
        if len(PUBLISHED)>=32 and key not in PUBLISHED:PUBLISHED.pop(next(iter(PUBLISHED)))
        PUBLISHED[key]={**result,"bar":row["bar"],"at":at,"version":VERSION}
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
    return p if p.get("bar")==row["bar"] and 0<=now-num(p.get("at"),-1)<=10 else {"status":"PENDING","excluded":p.get("excluded",[]),"samples":p.get("samples",0)}
