"""Isolated owner research worker. Never requests trading permissions.
Run on a separate computer/process; only the owner's candle history and
research scores are transferred. Password is prompted, never persisted.
"""
import argparse, getpass, hashlib, json, math, os, time, urllib.parse, urllib.request
from datetime import datetime,timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
# Keep heavy libraries out of the production server.
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
from specialist_market import samples,features,score,LOOKBACK
from strategy_research import _candle
from model_arena_api import MODELS,REPORT_MODELS
from challenger_forecasts import LOCAL,CLOUD,forecast_local,cloud_forecast
SERVER="https://al-zool-meeno-engine.onrender.com"
MARKETS=("BTCUSD","XAUUSD")
def request(url,body=None,headers=None,timeout=45):
    h={"Accept":"application/json",**(headers or {})}
    if body is not None:h["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=json.dumps(body,allow_nan=False).encode() if body is not None else None,headers=h)
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
def login(email,password):
    cfg=request(SERVER+"/api/config")
    return request(cfg["supabase_url"]+"/auth/v1/token?grant_type=password",{"email":email,"password":password},{"apikey":cfg["supabase_anon_key"]})["access_token"]
def api(market,path,token,body=None):
    prefix="/market/XAUUSD" if market=="XAUUSD" else ""
    return request(SERVER+prefix+"/api/ai/arena/"+path,body,{"Authorization":"Bearer "+token})
def archive(market,token):
    params={};bars=[];cid=None;symbol=None
    for _ in range(12):
        p=api(market,"history?"+urllib.parse.urlencode(params),token)
        cid=p.get("client");symbol=p.get("symbol")
        for r in p.get("rows") or []:
            try:
                if r.get("spread_points") is not None and r.get("point_size") is not None:bars.append(_candle(r))
            except (TypeError,ValueError,KeyError):continue
        if not p.get("next_cursor"):break
        params={"client":cid,"symbol":symbol,"cursor":p["next_cursor"]}
    return cid,sorted({b["time"]:b for b in bars}.values(),key=lambda b:b["time"])
def dataset(bars):
    d=samples(bars);n=len(d)
    if n<600:return None
    a=int(n*.6);b=int(n*.8)
    tr=[s for s in d[:a] if s["end"]<d[a]["time"]]
    va=[s for s in d[a:b] if s["end"]<d[b]["time"]]
    te=d[b:]
    # All five models use identical deterministic, nonoverlapping sampled
    # validation and test windows. Full tree training remains chronological.
    def subset(rows,n=24):
        pool=rows[::3]
        if len(pool)<=n:return pool
        return [pool[i*(len(pool)-1)//(n-1)] for i in range(n)]
    return tr,subset(va),subset(te)
def tree(name,tr,va,te):
    if name=="LightGBM":
        import lightgbm as lib
        m=lib.LGBMRegressor(n_estimators=120,max_depth=4,num_leaves=15,learning_rate=.03,n_jobs=1,verbosity=-1,random_state=42)
    elif name=="XGBoost":
        import xgboost as lib
        m=lib.XGBRegressor(n_estimators=120,max_depth=4,learning_rate=.03,n_jobs=1,random_state=42,tree_method="hist")
    else:
        import catboost as lib
        m=lib.CatBoostRegressor(iterations=120,depth=4,learning_rate=.03,thread_count=1,verbose=False,allow_writing_files=False,random_seed=42)
    m.fit([s["x"] for s in tr],[max(-6,min(6,s["y"])) for s in tr])
    return list(map(float,m.predict([s["x"] for s in va+te]))),str(lib.__version__)
def series_context(bars,rows):
    by={b["time"]:i for i,b in enumerate(bars)}
    out=[]
    for row in rows:
        i=by[row["time"]];w=bars[i-LOOKBACK:i+1]
        f,atr=features(w)
        # Normalize by known ATR, using only the context available at origin.
        p=w[-1]["close"]
        out.append([(b["close"]-p)/atr for b in w])
    return out
def pretrained(name,bars,rows,allow_timesfm):
    contexts=series_context(bars,rows)
    if name in LOCAL:return forecast_local(name,contexts)
    import numpy as np
    import torch
    torch.set_num_threads(1)
    if name=="Chronos-2":
        from chronos import Chronos2Pipeline
        m=Chronos2Pipeline.from_pretrained("amazon/chronos-2",device_map="cpu")
        q,_=m.predict_quantiles([torch.tensor(x,dtype=torch.float32) for x in contexts],prediction_length=3,quantile_levels=[0.5],batch_size=4)
        return [float(a[0,2,0]) if a.ndim==3 else float(a[2,0]) for a in q],"amazon/chronos-2"
    if name!="TimesFM-2.5":raise ValueError("unknown pretrained model")
    import timesfm
    m=timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch",torch_compile=False)
    m.compile(timesfm.ForecastConfig(max_context=128,max_horizon=128,normalize_inputs=True,per_core_batch_size=2,infer_is_positive=False))
    point,_=m.forecast(horizon=3,inputs=[np.asarray(x,dtype=np.float32) for x in contexts])
    return [float(x[2]) for x in point],"google/timesfm-2.5-200m-pytorch"

def stress(data,forecasts,threshold,extra=.10):
    adjusted=[{**s,"buy":s["buy"]-extra,"sell":s["sell"]-extra} for s in data]
    out=score(adjusted,forecasts,threshold)
    equity=peak=drawdown=0.
    for s,v in zip(adjusted,forecasts):
        side="buy" if v>threshold else "sell" if v< -threshold else None
        if side:equity+=s[side]
        peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    return {**out,"max_drawdown_atr":round(drawdown,5)}

def evaluate(name,bars,parts,allow_timesfm,cloud_key=None):
    tr,va,te=parts
    supervised=name in ("LightGBM","XGBoost","CatBoost")
    if name in CLOUD:p,version=cloud_forecast(name,series_context(bars,va+te),cloud_key)
    else:p,version=tree(name,tr,va,te) if supervised else pretrained(name,bars,va+te,allow_timesfm)
    if len(p)!=len(va)+len(te) or not all(math.isfinite(v) for v in p):raise ValueError("invalid model predictions")
    opts=(0,.05,.10,.20,.35,.50,1e9)
    pv,pt=p[:len(va)],p[len(va):]
    threshold=max(opts,key=lambda t:score(va,pv,t)["net_atr"])
    bv=[s["x"][1] for s in va];bt=[s["x"][1] for s in te]
    baseline=max(opts,key=lambda t:score(va,bv,t)["net_atr"])
    return {"status":"TESTED","method":"SUPERVISED" if supervised else "CLOUD_RESEARCH" if name in CLOUD else "PRETRAINED_ZERO_SHOT",
       "model_version":version,"train_windows":len(tr) if supervised else 0,
       "validation_windows":len(va),"test_windows":len(te),"threshold":threshold,
       "validation":score(va,pv,threshold),"test":score(te,pt,threshold),"baseline_test":score(te,bt,baseline),
       "stress_test":stress(te,pt,threshold),"stress_baseline":stress(te,bt,baseline),
       "test_id":hashlib.sha256(json.dumps([s["time"] for s in te]).encode()).hexdigest()}
def run(email,password,names,allow_timesfm,cloud_key=None):
    for market in MARKETS:
        try:
            token=login(email,password);cid,bars=archive(market,token)
            if not cid:print(market,"NO_ARCHIVE",flush=True);continue
            parts=dataset(bars)
            common={"client":cid,"candles":len(bars),"dataset_id":hashlib.sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest(),
               "first_candle":datetime.fromtimestamp(bars[0]["time"],timezone.utc).isoformat() if bars else "",
               "last_candle":datetime.fromtimestamp(bars[-1]["time"],timezone.utc).isoformat() if bars else ""}
            for name in names:
                t=time.monotonic()
                def publish(result):
                    # Fresh token before writing even if inference took minutes.
                    api(market,"report",login(email,password),{**common,"model":name,**result})
                try:
                    if parts is None:result={"status":"INSUFFICIENT_HISTORY"}
                    else:
                        publish({"status":"RUNNING"})
                        result=evaluate(name,bars,parts,allow_timesfm,cloud_key)
                except (ImportError,ModuleNotFoundError) as exc:result={"status":"UNAVAILABLE","detail":"Missing package: "+str(exc.name or type(exc).__name__)}
                except Exception as exc:result={"status":"ERROR","detail":type(exc).__name__}
                publish({**result,"sample_seconds":round(time.monotonic()-t,2)})
                print(market,name,result["status"],flush=True)
                import gc
                gc.collect()
        except Exception as exc:print(market,"WORKER_ERROR",type(exc).__name__,flush=True)
def main():
    p=argparse.ArgumentParser();p.add_argument("--once",action="store_true");p.add_argument("--models",nargs="+",choices=REPORT_MODELS,default=list(MODELS));p.add_argument("--cloud",action="store_true",help="Explicit bounded paid API research; requires --once");args=p.parse_args()
    cloud_selected=any(n in CLOUD for n in args.models)
    if cloud_selected and (not args.cloud or not args.once):p.error("Cloud model tests require --cloud --once; API charges may apply.")
    cloud_key=getpass.getpass("Groq API key (local only): ") if cloud_selected else None
    email=input("Owner email: ").strip();password=getpass.getpass("Owner password: ")
    while True:
        run(email,password,args.models,False,cloud_key)
        if args.once:return
        time.sleep(3600)
if __name__=="__main__":main()
