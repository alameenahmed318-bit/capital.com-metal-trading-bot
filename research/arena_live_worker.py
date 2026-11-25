"""Independent live research worker. No order endpoints or account changes.
Three separately fitted action/side specialists use historical price-path
proxies, not real basket outcomes. Refresh fits hourly outside the server.
"""
import time,math,getpass,os,traceback,threading,json,hashlib
from pathlib import Path
if os.name=='nt':os.environ.setdefault('TORCH_COMPILE_DISABLE','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('MKL_NUM_THREADS','1')
import model_arena_worker as arena
from specialist_market import features,samples,LOOKBACK
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
class ValidatedDirection:
    """Suppress weak forecasts using the model's validation-selected cutoff."""
    def __init__(self,model,threshold,meta=None):
        self.model=model
        self.threshold=threshold
        self.meta=meta or {}
    def predict(self,rows):
        values=self.model.predict(rows)
        return [float(v) if math.isfinite(float(v)) and abs(float(v))>self.threshold else 0.0 for v in values]

def direction_payoffs(rows,forecasts,threshold):
    return [r["buy"] if float(v)>0 else r["sell"] for r,v in zip(rows,forecasts)
            if math.isfinite(float(v)) and abs(float(v))>threshold]

def gold_direction(bars,names=("LightGBM","XGBoost","CatBoost")):
    """Gold-only chronological 60/20/20 calibration; final test is never tuned."""
    data=samples(bars)
    n=len(data)
    if n<600:return {}
    a=int(n*.6);b=int(n*.8)
    train=[v for v in data[:a] if v["end"]<data[a]["time"]]
    validation=[v for v in data[a:b] if v["end"]<data[b]["time"]]
    test=data[b:]
    if len(train)<350 or min(len(validation),len(test))<100:return {}
    fitted={}
    for name in names:
        try:
            if name=="LightGBM":m=estimator()
            elif name=="XGBoost":
                from xgboost import XGBRegressor
                m=XGBRegressor(n_estimators=80,max_depth=3,n_jobs=1,learning_rate=.05,random_state=42)
            else:
                from catboost import CatBoostRegressor
                m=CatBoostRegressor(iterations=80,depth=4,thread_count=1,learning_rate=.05,verbose=False,allow_writing_files=False,random_seed=42)
            m.fit([v["x"] for v in train],[max(-6,min(6,v["y"])) for v in train])
            pv=m.predict([v["x"] for v in validation])
            options=[]
            for threshold in (.05,.10,.15,.20,.30,.40,.50,.75,1.0):
                pay=direction_payoffs(validation,pv,threshold)
                if len(pay)>=30 and sum(pay)>0:
                    options.append((sum(pay),threshold,len(pay)))
            if not options:
                print("XAUUSD",name,"VALIDATION_REJECTED","no positive cutoff with 30 signals",flush=True)
                continue
            val_net,threshold,val_signals=max(options,key=lambda x:x[0])
            pt=m.predict([v["x"] for v in test])
            pay=direction_payoffs(test,pt,threshold)
            approved=len(pay)>=30 and sum(pay)>0
            print("XAUUSD",name,"HOLDOUT","threshold",threshold,
                  "validation_signals",val_signals,"validation_net",round(val_net,5),
                  "test_signals",len(pay),"net_proxy",round(sum(pay),5),
                  "APPROVED" if approved else "REJECTED",flush=True)
            # Keep the exact tested model; a refit would change its calibrated scores.
            if approved:fitted[name]=ValidatedDirection(m,threshold,{"approved_at":time.time(),"last_end":test[-1]["end"],"train_end":train[-1]["end"],"validation_net":round(val_net,5),"validation_signals":val_signals,"test_net":round(sum(pay),5),"test_signals":len(pay)})
        except Exception as exc:
            print("XAUUSD",name,"FIT_ERROR",type(exc).__name__,str(exc),flush=True)
    return fitted


# Persistent CatBoost champion, using native CBM format (no pickle).
CHAMPION_TTL=6*3600
CACHE_DIR=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"ALZOOL"/"direction-models"

def champion_fresh(meta):
    try:return 0<=time.time()-float(meta["approved_at"])<=CHAMPION_TTL
    except (TypeError,ValueError,KeyError):return False

def champion_path(cid):
    return CACHE_DIR/("gold-catboost-"+hashlib.sha256(str(cid).encode()).hexdigest()[:24]+".json")

def save_champion(cid,model):
    try:
        CACHE_DIR.mkdir(parents=True,exist_ok=True)
        p=champion_path(cid);filename=p.stem+"-"+str(time.time_ns())+".cbm"
        model.model.save_model(str(CACHE_DIR/filename))
        tmp=p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"format":1,"market":"XAUUSD","model":"CatBoost","file":filename,"threshold":model.threshold,"meta":model.meta},allow_nan=False),encoding="utf-8")
        oldfile=None
        if p.exists():
            try:oldfile=json.loads(p.read_text(encoding="utf-8")).get("file")
            except Exception:pass
        os.replace(tmp,p)
        if isinstance(oldfile,str) and Path(oldfile).name==oldfile and oldfile.startswith(p.stem+"-") and oldfile.endswith(".cbm") and oldfile!=filename:
            try:(CACHE_DIR/oldfile).unlink(missing_ok=True)
            except OSError:pass
    except Exception as exc:print("XAUUSD CatBoost CACHE_SAVE_ERROR",type(exc).__name__,flush=True)

def load_champion(cid):
    try:
        p=champion_path(cid)
        if not p.exists():return None
        d=json.loads(p.read_text(encoding="utf-8"));meta=d["meta"];f=d["file"]
        if d.get("format")!=1 or d.get("market")!="XAUUSD" or d.get("model")!="CatBoost":return None
        if Path(f).name!=f or not f.startswith(p.stem+"-") or not f.endswith(".cbm"):return None
        threshold=float(d["threshold"])
        if not math.isfinite(threshold) or not .05<=threshold<=1:return None
        if not champion_fresh(meta) or int(meta["test_signals"])<30 or float(meta["test_net"])<=0:return None
        from catboost import CatBoostRegressor
        m=CatBoostRegressor(thread_count=1);m.load_model(str(CACHE_DIR/f))
        return ValidatedDirection(m,threshold,meta)
    except Exception as exc:
        print("XAUUSD CatBoost CACHE_LOAD_ERROR",type(exc).__name__,flush=True)
        return None

def recover_known_gold(bars):
    # Exactly one previously approved snapshot from the 25 Nov log.
    # Never search other historical cutoffs for a favorable result.
    if len(bars)<11116:return None
    old=gold_direction(bars[:11116],names=("CatBoost",)).get("CatBoost")
    if old is None:return None
    m=old.meta
    if not (old.threshold==.15 and m["validation_signals"]==379 and m["test_signals"]==365
            and abs(m["validation_net"]-7.12333)<.0001 and abs(m["test_net"]-22.61177)<.0001):
        print("XAUUSD CatBoost RECOVERY_SNAPSHOT_MISMATCH",flush=True);return None
    m["approved_at"]=datetime.fromisoformat("2026-11-25T11:08:37+00:00").timestamp()
    if not champion_fresh(m):return None
    print("XAUUSD CatBoost RECOVERY_SNAPSHOT_MATCHED",flush=True)
    return old

def recheck_champion(old,data):
    if old is None or not champion_fresh(old.meta) or len(data)<600:return None
    test=data[int(len(data)*.8):]
    if not test or test[0]["time"]<=old.meta["train_end"]:return None
    pay=direction_payoffs(test,old.model.predict([v["x"] for v in test]),old.threshold)
    new=[v for v in data if v["time"]>old.meta["last_end"]][::3]
    forward=direction_payoffs(new,old.model.predict([v["x"] for v in new]),old.threshold) if new else []
    # Negative fresh evidence suspends the champion; sparse evidence cannot
    # renew the original time limit. Compare candidates on the same holdout.
    ok=len(pay)>=30 and sum(pay)>0 and (len(forward)<5 or sum(forward)>0)
    print("XAUUSD CatBoost CHAMPION_RECHECK","test_signals",len(pay),"test_net",round(sum(pay),5),
          "new_signals",len(forward),"new_net",round(sum(forward),5),"PASS" if ok else "SUSPENDED",flush=True)
    return sum(pay) if ok else None

def choose_gold_champion(cid,bars,new,previous):
    old=previous.get("CatBoost") or load_champion(cid)
    if old is None and "CatBoost" not in new:old=recover_known_gold(bars)
    candidate=new.get("CatBoost")
    try:old_net=recheck_champion(old,samples(bars)) if old is not None else None
    except Exception as exc:
        old_net=None
        print("XAUUSD CatBoost RECHECK_ERROR",type(exc).__name__,flush=True)
    if old_net is not None and (candidate is None or old_net>=candidate.meta["test_net"]):
        new["CatBoost"]=old
        save_champion(cid,old)
        print("XAUUSD CatBoost CHAMPION_RETAINED",flush=True)
    elif candidate is not None:
        save_champion(cid,candidate)
        print("XAUUSD CatBoost CHAMPION_PROMOTED",flush=True)
    else:
        print("XAUUSD CatBoost NO_VALID_CHAMPION",flush=True)
    return new

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
            # Prioritize gold's first fit, then retain the normal BTC cycle.
            for market in ("XAUUSD", *[m for m in arena.MARKETS if m!="XAUUSD"]):
                if time.monotonic()<next_fit.get(market,0):continue
                next_fit[market]=time.monotonic()+3600
                try:
                    with lock:auth=state["token"]
                    cid,bars=arena.archive(market,auth)
                    if not cid:continue
                    if market=="XAUUSD":
                        with lock:
                            prior=cache.get(market)
                            previous=prior[1] if prior and prior[0]==cid else {}
                        direction=choose_gold_champion(cid,bars,gold_direction(bars),previous)
                    else:
                        direction=train_direction(bars)
                    # Publish validated gold direction models immediately;
                    # the slower action fit must not delay their availability.
                    if market=="XAUUSD":
                        with lock:
                            old=cache.get(market)
                            prev_actions=old[2] if old and old[0]==cid else {}
                            cache[market]=(cid,direction,prev_actions)
                    try:actions,reports=train_actions(bars)
                    except Exception as exc:
                        actions,reports={},{}
                        print(market,"ACTION_FIT_ERROR",type(exc).__name__,flush=True)
                    with lock:
                        old=cache.get(market)
                        if old and old[0]==cid:
                            direction=direction if market=="XAUUSD" else {**old[1],**direction};actions={**old[2],**actions}
                        cache[market]=(cid,direction,actions)
                    print(market,"FIT",len(bars),"direction",list(direction),"actions",reports,flush=True)
                    arena.api(market,"action-training",auth,{"client":cid,"candles":len(bars),"reports":reports})
                except Exception as exc:
                    next_fit[market]=time.monotonic()+60
                    print(market,"FIT_ERROR",type(exc).__name__,flush=True)
            time.sleep(5)
    threading.Thread(target=train_loop,daemon=True,name="arena-training-lane").start()
    print("ALZOOL LIVE ADVISERS | seven models | GOLD FAST LANE | no order permission",flush=True)
    def poll_market(market,auth):
        ctx=arena.api(market,"live-context",auth)
        if ctx.get("status")!="READY":return
        cid=ctx["client"];bar=ctx["bar_time"]
        if not fresh(bar):return
        with lock:
            old=jobs.get(market)
            if old and old["bar_time"]==bar and old["client"]==cid and seen.get(market)==(cid,bar):
                return
        f=features(ctx["bars"])
        if f is None:return
        x,atr=f
        with lock:
            fitted=cache.get(market)
            old=jobs.get(market)
            if not old or old["bar_time"]!=bar or old["client"]!=cid:
                jobs[market]={"client":cid,"symbol":ctx["symbol"],"bar_time":bar,
                              "values":[(b["close"]-ctx["bars"][-1]["close"])/atr for b in ctx["bars"]]}
        if not fitted or fitted[0]!=cid or seen.get(market)==(cid,bar):return
        _,models,actions=fitted
        scores={};action_scores={}
        for name,m in models.items():
            if market=="XAUUSD" and name=="CatBoost" and not champion_fresh(m.meta):continue
            try:
                value=float(m.predict([x])[0])
                if math.isfinite(value):scores[name]=value
            except Exception as exc:print(market,name,type(exc).__name__,flush=True)
        body={"client":cid,"symbol":ctx["symbol"],"bar_time":bar}
        # Gold's entry evidence is transmitted before position-management
        # inference. The server merges same-bar partial updates by model/action.
        gold=market=="XAUUSD"
        if gold and scores:
            if not fresh(bar):return
            response=arena.api(market,"live-forecast",auth,{**body,"models":scores,"actions":{}})
            print(market,bar,"DIRECTION_FIRST",list(scores),"ACCEPTED" if response.get("accepted") else response.get("reason","REJECTED"),
                  "age_after_close_sec",round(time.time()-bar-60,3),flush=True)
            if not response.get("accepted"):return
        for (action,side),m in actions.items():
            try:
                value=float(m.predict([x])[0])
                if math.isfinite(value):action_scores.setdefault(action,{})[side]=value
            except Exception as exc:print(market,action,side,type(exc).__name__,flush=True)
        if not fresh(bar):return
        if gold and scores:
            if action_scores:
                response=arena.api(market,"live-forecast",auth,{**body,"models":{},"actions":action_scores})
                if not response.get("accepted"):return
            seen[market]=(cid,bar)
            return
        if not scores and not action_scores:return
        response=arena.api(market,"live-forecast",auth,{**body,"models":scores,"actions":action_scores})
        if response.get("accepted"):seen[market]=(cid,bar)
        print(market,bar,"FAST",list(scores),"ACCEPTED" if response.get("accepted") else response.get("reason","REJECTED"),flush=True)
    def gold_loop():
        # A slow BTC request cannot consume the gold candle's entry window.
        while True:
            try:
                with lock:auth=state["token"]
                poll_market("XAUUSD",auth)
            except Exception as exc:
                print("XAUUSD","LIVE_WORKER_ERROR",type(exc).__name__,getattr(exc,"code",""),flush=True)
            time.sleep(2)
    threading.Thread(target=gold_loop,daemon=True,name="arena-gold-fast-lane").start()
    while True:
        if time.monotonic()>renew:
            try:
                token=arena.login(email,password);renew=time.monotonic()+1800
                with lock:state["token"]=token
            except Exception as exc:
                print("AUTH_REFRESH_ERROR",type(exc).__name__,flush=True)
                time.sleep(10);continue
        for market in arena.MARKETS:
            if market=="XAUUSD":continue
            try:
                poll_market(market,token)
            except Exception as exc:
                print(market,"LIVE_WORKER_ERROR",type(exc).__name__,flush=True)
        time.sleep(3)
if __name__=="__main__":
    email=input("Owner email: ").strip()
    live(email,getpass.getpass("Owner password: "))
