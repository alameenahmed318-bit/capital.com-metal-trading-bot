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
from adviser_validation import split_rows,nonoverlap,metrics,select_threshold,SideModel,ActionModel

def fit_regressor(name,depth):
    if name=="XGBoost":
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=100,max_depth=depth,n_jobs=1,learning_rate=.035,random_state=42,tree_method="hist",min_child_weight=12,reg_lambda=5)
    if name=="CatBoost":
        from catboost import CatBoostRegressor
        return CatBoostRegressor(iterations=100,depth=depth,thread_count=1,learning_rate=.035,verbose=False,allow_writing_files=False,random_seed=42,l2_leaf_reg=8)
    return estimator()

def validated_fit(name,rows,origin,end,feature,target,minimum=600,min_signals=30):
    parts=split_rows(rows,origin,end,minimum)
    if parts is None:return None,{"status":"INSUFFICIENT"}
    tr,va,te=parts
    va=nonoverlap(va,origin,end);te=nonoverlap(te,origin,end)
    if len(tr)<100 or min(len(va),len(te))<min_signals:return None,{"status":"INSUFFICIENT"}
    model=fit_regressor(name,3)
    model.fit([feature(r) for r in tr],[max(-6,min(6,target(r))) for r in tr])
    pv=model.predict([feature(r) for r in va])
    threshold=select_threshold(pv,[target(r) for r in va],min_signals)
    base={"train":len(tr),"validation":len(va),"test":len(te),"signals":0,"positive":0,"net_proxy":0.}
    if threshold is None:return None,{**base,"status":"VALIDATION_REJECTED"}
    pt=model.predict([feature(r) for r in te])
    result=metrics(pt,[target(r) for r in te],threshold,min_signals)
    report={**base,**result,"threshold":threshold,"status":"PASSED" if result["eligible"] else "HOLDOUT_REJECTED"}
    return ((model,threshold) if result["eligible"] else None),report

def train_actions(bars):
    rows=[]
    for i in range(LOOKBACK,len(bars)-5):
        past=bars[i-LOOKBACK:i+1];f=features(past)
        if f is None or bars[i+5]["time"]-bars[i]["time"]!=300:continue
        x,atr=f;future=bars[i+1:i+6];entry=future[0]["open"]
        for side,sign in (("BUY",1),("SELL",-1)):
            move5=sign*x[1];move15=sign*x[2]
            close=[sign*(b["close"]-entry)/atr for b in future]
            adverse=max(0,max((entry-b["low"])/atr if sign==1 else (b["high"]-entry)/atr for b in future))
            for action,eligible,j,penalty in (
                ("ADDON",move5>.35,2,.3),("COOLING",move5<-.5,4,.6),("RECOVERY",move15<-1,4,1.)):
                cost_bar=future[0] if side=="BUY" else future[j]
                cost=cost_bar["spread_points"]*cost_bar["point_size"]/atr
                if eligible and cost>=0:rows.append((action,side,bars[i]["time"],x,close[j]-cost-penalty*adverse))
    fitted={};reports={}
    for action in ACTIONS:
        for side in ("BUY","SELL"):
            r=[x for x in rows if x[0]==action and x[1]==side]
            try:
                chosen,report=validated_fit("LightGBM",r,lambda x:x[2],lambda x:x[2]+300,lambda x:x[3],lambda x:x[4],minimum=300,min_signals=15)
                reports[action+":"+side]=report
                if chosen:fitted[(action,side)]=ActionModel(*chosen)
            except Exception as exc:
                reports[action+":"+side]={"status":"ERROR","signals":0,"positive":0,"net_proxy":0.}
                print(action,side,"FIT_ERROR",type(exc).__name__,flush=True)
    return fitted,reports
TREE_RETRY={}
DIRECTION_TREES=("LightGBM","XGBoost","CatBoost")
def train_direction(bars,market="UNKNOWN"):
    d=samples(bars)
    if len(d)<600:return {}
    models={}
    for name in DIRECTION_TREES:
        key=(market,name)
        if time.monotonic()<TREE_RETRY.get(key,0):
            print(market,name,"FIT_COOLDOWN",flush=True);continue
        chosen_sides={}
        for side in ("BUY","SELL"):
            try:
                chosen,report=validated_fit(name,d,lambda r:r["time"],lambda r:r["end"],lambda r:r["x"],lambda r:r[side.lower()])
                print(market,name,side,"COST_AWARE",report,flush=True)
                if chosen:chosen_sides[side]=chosen
            except Exception as exc:print(market,name,side,"FIT_ERROR",type(exc).__name__,flush=True)
        if chosen_sides:
            models[name]=SideModel(chosen_sides);TREE_RETRY.pop(key,None)
        else:TREE_RETRY[key]=time.monotonic()+3600
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
                retry[name]=time.monotonic()+600
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
                    direction=train_direction(bars,market)
                    try:actions,reports=train_actions(bars)
                    except Exception as exc:
                        actions,reports={},{}
                        print(market,"ACTION_FIT_ERROR",type(exc).__name__,flush=True)
                    with lock:
                        # Replace only on completed fresh validation. Rejected
                        # models must not remain eligible through stale cache.
                        cache[market]=(cid,direction,actions)
                    print(market,"FIT",len(bars),"direction",list(direction),"actions",reports,flush=True)
                    arena.api(market,"action-training",auth,{"client":cid,"candles":len(bars),"reports":reports})
                except Exception as exc:
                    next_fit[market]=time.monotonic()+60
                    print(market,"FIT_ERROR",type(exc).__name__,flush=True)
            time.sleep(5)
    threading.Thread(target=train_loop,daemon=True,name="arena-training-lane").start()
    print("ALZOOL LIVE ADVISERS | "+", ".join(DIRECTION_TREES+PRETRAINED)+" | no order permission",flush=True)
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
