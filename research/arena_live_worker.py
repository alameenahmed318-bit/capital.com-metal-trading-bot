"""Independent live research worker. No order endpoints or account changes.
Three separately fitted action/side specialists use historical price-path
proxies, not real basket outcomes. Refresh fits hourly outside the server.
"""
import time,math,getpass,os,traceback,threading
if os.name=='nt':os.environ.setdefault('TORCH_COMPILE_DISABLE','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('MKL_NUM_THREADS','1')
import model_arena_worker as arena
from specialist_market import features,samples,LOOKBACK
from model_arena_api import LIVE_MODELS
from datetime import datetime,timezone
ACTIONS=("ADDON","COOLING","RECOVERY")
def estimator():
    from lightgbm import LGBMRegressor
    return LGBMRegressor(n_estimators=80,max_depth=3,num_leaves=7,learning_rate=.05,n_jobs=1,verbosity=-1,random_state=42)
def train_actions(bars):
    rows=[]
    for i in range(LOOKBACK,len(bars)-5):
        past=bars[i-LOOKBACK:i+1];f=features(past)
        if f is None or bars[i+5]["time"]-bars[i]["time"]!=300:continue
        x,atr=f;future=bars[i+1:i+6];entry=future[0]["open"]
        cost=future[0]["spread_points"]*future[0]["point_size"]/atr
        for side,sign in (("BUY",1),("SELL",-1)):
            move5=sign*x[1];move15=sign*x[2]
            close=[sign*(b["close"]-entry)/atr for b in future]
            adverse=max(0,max((entry-b["low"])/atr if sign==1 else (b["high"]-entry)/atr for b in future))
            # Different historical contexts, horizons and path penalties.
            for action,eligible,target in (
                ("ADDON",move5>.35,close[2]-cost-.3*adverse),
                ("COOLING",move5<-.5,close[4]-cost-.6*adverse),
                ("RECOVERY",move15<-1,close[4]-cost-adverse)):
                if eligible:rows.append((action,side,bars[i]["time"],x,max(-6,min(6,target))))
    fitted={};reports={}
    for action in ACTIONS:
        for side in ("BUY","SELL"):
            r=[x for x in rows if x[0]==action and x[1]==side]
            n=len(r);split=int(n*.8)
            if n<150:continue
            test=r[split:];train=[v for v in r[:split] if v[2]+300<test[0][2]]
            if len(train)<100:continue
            model=estimator();model.fit([v[3] for v in train],[v[4] for v in train])
            predictions=model.predict([v[3] for v in test])
            take=[v[4] for p,v in zip(predictions,test) if p>0]
            reports[action+":"+side]={"train":len(train),"test":len(test),"signals":len(take),"positive":sum(v>0 for v in take),"net_proxy":round(sum(take),4)}
            fitted[(action,side)]=model
    return fitted,reports
def train_direction(bars):
    d=samples(bars)
    if len(d)<600:return {}
    # Purge future-label overlap at the chronological split, evaluate using
    # recorded side-specific costs, and only refit a validated tree.
    cut=int(len(d)*.8)
    holdout=d[cut:]
    train=[v for v in d[:cut] if v["end"]<holdout[0]["time"]]
    if len(train)<400 or len(holdout)<100:return {}
    models={}
    for name in ("LightGBM","XGBoost","CatBoost"):
        try:
            if name=="LightGBM":m=estimator()
            elif name=="XGBoost":
                from xgboost import XGBRegressor
                m=XGBRegressor(n_estimators=80,max_depth=3,n_jobs=1,learning_rate=.05,random_state=42)
            else:
                from catboost import CatBoostRegressor
                m=CatBoostRegressor(iterations=80,depth=4,thread_count=1,learning_rate=.05,verbose=False,allow_writing_files=False,random_seed=42)
            m.fit([v["x"] for v in train],[max(-6,min(6,v["y"])) for v in train])
            pred=m.predict([v["x"] for v in holdout])
            selected=[v["buy"] if p>0 else v["sell"] for p,v in zip(pred,holdout)
                      if math.isfinite(float(p)) and abs(p)>.05]
            print(name,"HOLDOUT",len(train),len(holdout),"signals",len(selected),
                  "net_proxy",round(sum(selected),5),flush=True)
            if len(selected)<30 or sum(selected)<=0:continue
            m.fit([v["x"] for v in d],[max(-6,min(6,v["y"])) for v in d])
            models[name]=m
        except Exception as e:print(name,"FIT_ERROR",type(e).__name__,flush=True)
    return models
PRETRAINED=("TiRex-2","Chronos-Bolt Tiny","Chronos-2","TimesFM-2.5")

def forecast(name,values,models):
    import numpy as np,torch
    torch.set_num_threads(1)
    x=torch.as_tensor(values,dtype=torch.float32)
    if name not in models:
        if name=="TiRex-2":
            from tirex2 import load_model
            models[name]=load_model("NX-AI/TiRex-2",device="cpu")
        elif name=="Chronos-Bolt Tiny":
            from chronos import BaseChronosPipeline
            models[name]=BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-tiny",device_map="cpu",torch_dtype=torch.float32)
        elif name=="Chronos-2":
            from chronos import Chronos2Pipeline
            models[name]=Chronos2Pipeline.from_pretrained("amazon/chronos-2",device_map="cpu")
        elif name=="TimesFM-2.5":
            import timesfm
            model=timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch",torch_compile=False)
            model.compile(timesfm.ForecastConfig(max_context=128,max_horizon=128,normalize_inputs=True,per_core_batch_size=1,infer_is_positive=False))
            models[name]=model
        else:raise ValueError("unsupported model")
    m=models[name]
    if name=="TiRex-2":
        from tirex2 import TimeseriesType
        ts=TimeseriesType(target=x.unsqueeze(0),past_covariates=None,future_covariates=None)
        out=m.forecast([ts],prediction_length=3,output_type="numpy",pad_context=False,batch_size=1)
        value=float(next(iter(out))[0,4,2])
    elif name=="Chronos-Bolt Tiny":
        q,_=m.predict_quantiles([x],prediction_length=3,quantile_levels=[.5])
        value=float(q[0][2,0])
    elif name=="Chronos-2":
        q,_=m.predict_quantiles([x],prediction_length=3,quantile_levels=[.5],batch_size=1)
        a=q[0];value=float(a[0,2,0]) if a.ndim==3 else float(a[2,0])
    else:
        p,_=m.forecast(horizon=3,inputs=[np.asarray(values,dtype=np.float32)])
        value=float(p[0][2])
    if not math.isfinite(value):raise ValueError("nonfinite forecast")
    return max(-6.,min(6.,value))

def fresh(bar):
    return 60<=time.time()-bar<120

def live(email,password):
    token=arena.login(email,password);renew=time.monotonic()+1800
    state={"token":token};lock=threading.Lock()
    jobs={};cache={};seen={};next_fit={}
    # One bounded slow lane with latest-only slots (one per market). Never
    # waits on the MT5/server path, and cannot delay the fast prediction lane.
    def heavy():
        models={};done={};retry={};turn=0
        while True:
            with lock:pending=list(jobs.items());auth=state["token"]
            pairs=[(market,name,job) for market,job in pending for name in PRETRAINED
                   if done.get((market,name))!=job["bar_time"] and fresh(job["bar_time"]) and time.monotonic()>=retry.get(name,0)]
            if not pairs:time.sleep(1);continue
            market,name,job=pairs[turn%len(pairs)];turn+=1
            bar=job["bar_time"];done[(market,name)]=bar
            try:
                v=forecast(name,job["values"],models)
                if not fresh(bar):print(market,name,"LATE_SKIPPED",flush=True);continue
                body={k:job[k] for k in ("client","symbol","bar_time")}
                reply=arena.api(market,"live-forecast",auth,{**body,"models":{name:v},"actions":{}})
                print(market,bar,name,"ACCEPTED" if reply.get("accepted") else reply.get("reason","REJECTED"),flush=True)
            except Exception as exc:
                retry[name]=time.monotonic()+60
                print(market,name,"ERROR",type(exc).__name__,flush=True)
    threading.Thread(target=heavy,daemon=True,name="arena-forecast-lane").start()
    # Training runs separately; keep previous fitted models until a complete
    # new fit is available. Never replace a good fit with an empty result.
    def train_loop():
        while True:
            for market in arena.MARKETS:
                if time.monotonic()<next_fit.get(market,0):continue
                next_fit[market]=time.monotonic()+3600
                try:
                    with lock:auth=state["token"]
                    cid,bars=arena.archive(market,auth)
                    if not cid:continue
                    direction=train_direction(bars)
                    try:actions,reports=train_actions(bars)
                    except Exception as exc:
                        actions,reports={},{}
                        print(market,"ACTION_FIT_ERROR",type(exc).__name__,flush=True)
                    with lock:
                        old=cache.get(market)
                        if old and old[0]==cid:
                            direction={**old[1],**direction};actions={**old[2],**actions}
                        cache[market]=(cid,direction,actions)
                    print(market,"FIT",len(bars),"direction",list(direction),"actions",reports,flush=True)
                    arena.api(market,"action-training",auth,{"client":cid,"candles":len(bars),"reports":reports})
                except Exception as exc:
                    next_fit[market]=time.monotonic()+60
                    print(market,"FIT_ERROR",type(exc).__name__,flush=True)
            time.sleep(5)
    threading.Thread(target=train_loop,daemon=True,name="arena-training-lane").start()
    print("ALZOOL LIVE ADVISERS | seven models | cloud Arena OFF | no order permission",flush=True)
    while True:
        if time.monotonic()>renew:
            try:
                token=arena.login(email,password);renew=time.monotonic()+1800
                with lock:state["token"]=token
            except Exception as exc:
                print("AUTH_REFRESH_ERROR",type(exc).__name__,flush=True)
                time.sleep(10);continue
        for market in arena.MARKETS:
            try:
                ctx=arena.api(market,"live-context",token)
                if ctx.get("status")!="READY":continue
                cid=ctx["client"];bar=ctx["bar_time"]
                if not fresh(bar):continue
                f=features(ctx["bars"])
                if f is None:continue
                x,atr=f
                with lock:
                    fitted=cache.get(market)
                    old=jobs.get(market)
                    if not old or old["bar_time"]!=bar or old["client"]!=cid:
                        jobs[market]={"client":cid,"symbol":ctx["symbol"],"bar_time":bar,
                                      "values":[(b["close"]-ctx["bars"][-1]["close"])/atr for b in ctx["bars"]]}
                if not fitted or fitted[0]!=cid or seen.get(market)==(cid,bar):continue
                _,models,actions=fitted
                scores={};action_scores={}
                for name,m in models.items():
                    try:scores[name]=float(m.predict([x])[0])
                    except Exception as exc:print(market,name,type(exc).__name__,flush=True)
                for (action,side),m in actions.items():
                    try:action_scores.setdefault(action,{})[side]=float(m.predict([x])[0])
                    except Exception as exc:print(market,action,side,type(exc).__name__,flush=True)
                if not scores and not action_scores:continue
                if not fresh(bar):continue
                body={"client":cid,"symbol":ctx["symbol"],"bar_time":bar,"models":scores,"actions":action_scores}
                response=arena.api(market,"live-forecast",token,body)
                seen[market]=(cid,bar)
                print(market,bar,"FAST",list(scores),"ACCEPTED" if response.get("accepted") else response.get("reason","REJECTED"),flush=True)
            except Exception as exc:
                print(market,"LIVE_WORKER_ERROR",type(exc).__name__,flush=True)
        time.sleep(3)
if __name__=="__main__":
    email=input("Owner email: ").strip()
    live(email,getpass.getpass("Owner password: "))
