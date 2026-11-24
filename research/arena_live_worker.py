"""Independent live research worker. No order endpoints or account changes.
Three separately fitted action/side specialists use historical price-path
proxies, not real basket outcomes. Refresh fits hourly outside the server.
"""
import time,math,getpass,os,traceback
import model_arena_worker as arena
from specialist_market import features,samples,LOOKBACK
from model_arena_api import MODELS
from datetime import datetime,timezone
from lightgbm import LGBMRegressor
ACTIONS=("ADDON","COOLING","RECOVERY")
def estimator():
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
    # These are live prediction fits; Arena holdout reports remain separate.
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
            m.fit([v["x"] for v in d],[max(-6,min(6,v["y"])) for v in d]);models[name]=m
        except Exception as e:print(name,"FIT_ERROR",type(e).__name__,flush=True)
    return models
def live(email,password):
    token=arena.login(email,password);renew=time.monotonic()+1800
    cache={};seen={};next_fit={};pretrained={}
    while True:
        if time.monotonic()>renew:
            token=arena.login(email,password);renew=time.monotonic()+1800
        for market in arena.MARKETS:
            try:
                if time.monotonic()>=next_fit.get(market,0):
                    cid,bars=arena.archive(market,token)
                    if cid:
                        direction=train_direction(bars);actions,reports=train_actions(bars)
                        cache[market]=(cid,direction,actions)
                        print(market,"FIT",len(bars),"direction",list(direction),"actions",reports,flush=True)
                        arena.api(market,"action-training",token,{"client":cid,"candles":len(bars),"reports":reports})
                    next_fit[market]=time.monotonic()+3600
                ctx=arena.api(market,"live-context",token)
                if ctx.get("status")!="READY" or market not in cache:continue
                cid,models,actions=cache[market];bar=ctx["bar_time"]
                if ctx["client"]!=cid or seen.get(market)==bar:continue
                f=features(ctx["bars"])
                if f is None:continue
                x,atr=f
                scores={};action_scores={}
                for name,m in models.items():
                    scores[name]=float(m.predict([x])[0])
                for (action,side),m in actions.items():
                    action_scores.setdefault(action,{})[side]=float(m.predict([x])[0])
                body={"client":cid,"symbol":ctx["symbol"],"bar_time":bar,"models":scores,"actions":action_scores}
                response=arena.api(market,"live-forecast",token,body)
                seen[market]=bar
                print(market,bar,"FAST",response.get("accepted"),flush=True)
                # Heavy forecasts run after the fast publish. Late forecasts
                # are rejected by the server, never delay the execution lane.
                import numpy as np,torch
                torch.set_num_threads(1)
                values=np.asarray([(b["close"]-ctx["bars"][-1]["close"])/atr for b in ctx["bars"]],dtype=np.float32)
                for name in ("Chronos-2","TimesFM-2.5"):
                    try:
                        if name not in pretrained:
                            if name=="Chronos-2":
                                from chronos import Chronos2Pipeline
                                pretrained[name]=Chronos2Pipeline.from_pretrained("amazon/chronos-2",device_map="cpu")
                            else:
                                import timesfm
                                m=timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch",torch_compile=False)
                                m.compile(timesfm.ForecastConfig(max_context=128,max_horizon=128,normalize_inputs=True,per_core_batch_size=1,infer_is_positive=False));pretrained[name]=m
                        if name=="Chronos-2":
                            q,_=pretrained[name].predict_quantiles([torch.tensor(values)],prediction_length=3,quantile_levels=[.5],batch_size=1)
                            a=q[0];v=float(a[0,2,0]) if a.ndim==3 else float(a[2,0])
                        else:
                            p,_=pretrained[name].forecast(horizon=3,inputs=[values]);v=float(p[0][2])
                        reply=arena.api(market,"live-forecast",token,{**body,"models":{name:v},"actions":{}})
                        print(market,bar,name,reply.get("accepted"),flush=True)
                    except Exception as e:print(market,name,type(e).__name__,flush=True)
            except Exception as e:
                print(market,"LIVE_WORKER_ERROR",type(e).__name__,flush=True)
                time.sleep(3)
        time.sleep(5)
if __name__=="__main__":
    email=input("Owner email: ").strip()
    live(email,getpass.getpass("Owner password: "))
