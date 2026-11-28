"""One opt-in low-priority local monitoring worker, no execution endpoint.
Fits only in its own lane; existing live worker remains unchanged.
New models are research-only, never submitted to live-forecast.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
import argparse,getpass,time,sys,math,json,urllib.parse
from pathlib import Path
from collections import deque
from parallel_memory import state_path,load,save,pause_model,trade_calibrator
if os.name=="nt":
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
else:
    try:os.nice(10)
    except OSError:pass
import numpy as np
import torch
from torch import nn
import model_arena_worker as arena
torch.set_num_threads(1)
NAMES=("TCN","PatchTST","LSTM Multi","Patch Transformer","LSTM")
USE_ANOMALY=True
ENTRY_HORIZON=3 # Unified launcher selects one-minute entry training only.
FIELDS=("time","open","high","low","close","tick_volume","spread_points","point_size")
def array(rows):
    return np.asarray([[float(r.get(k,0)) for k in FIELDS] for r in rows],dtype=np.float64)
def feature(b):
    # 48 closes and OHLC range/body/volume, normalized by causal 14-bar ATR.
    c=b[:,4];prev=np.r_[c[0],c[:-1]]
    tr=np.maximum(b[:,2]-b[:,3],np.maximum(abs(b[:,2]-prev),abs(b[:,3]-prev)))
    atr=max(float(tr[-14:].mean()),1e-9)
    x=np.column_stack([(c-c[-1])/atr,(c-b[:,1])/atr,(b[:,2]-b[:,3])/atr,np.log1p(np.maximum(b[:,5],0))/10])
    return np.clip(x[-48:],-12,12).astype(np.float32),atr
class Recurrent(nn.Module):
    def __init__(self,d):
        super().__init__();self.r=nn.LSTM(d,16,batch_first=True);self.h=nn.Linear(16,1)
    def forward(self,x):return self.h(self.r(x)[0][:,-1]).squeeze(-1)
class GRU(nn.Module):
    def __init__(self):
        super().__init__();self.r=nn.GRU(4,16,batch_first=True);self.h=nn.Linear(16,1)
    def forward(self,x):return self.h(self.r(x)[0][:,-1]).squeeze(-1)
class TCN(nn.Module):
    def __init__(self):
        super().__init__();self.a=nn.Conv1d(4,24,3,padding=1);self.b=nn.Conv1d(24,24,3,padding=1);self.h=nn.Linear(24,1)
    def forward(self,x):return self.h(torch.relu(self.b(torch.relu(self.a(x.transpose(1,2)))))[:,:,-1]).squeeze(-1)
class Patch(nn.Module):
    def __init__(self):
        super().__init__();self.emb=nn.Linear(24,24);self.pos=nn.Parameter(torch.zeros(1,8,24))
        self.enc=nn.TransformerEncoder(nn.TransformerEncoderLayer(24,4,48,.1,batch_first=True),1);self.h=nn.Linear(24,1)
    def forward(self,x):return self.h(self.enc(self.emb(x.reshape(len(x),8,24))+self.pos)[:,-1]).squeeze(-1)
class OfficialPatch(nn.Module):
    def __init__(self):
        super().__init__()
        from transformers import PatchTSTConfig,PatchTSTForPrediction
        self.m=PatchTSTForPrediction(PatchTSTConfig(num_input_channels=1,context_length=48,prediction_length=1,patch_length=6,patch_stride=6,d_model=24,num_attention_heads=4,num_hidden_layers=1,ffn_dim=48,scaling=None))
    def forward(self,x):return self.m(past_values=x[:,:,:1]).prediction_outputs[:,0,0]
def fit(b,epochs,paused=(),slippage_points=None):
    from sklearn.ensemble import IsolationForest
    xs=[];ys=[];costs=[]
    for i in range(47,len(b)-ENTRY_HORIZON,2):
        if b[i+ENTRY_HORIZON,0]-b[i-47,0]!=(47+ENTRY_HORIZON)*60:continue
        x,atr=feature(b[i-47:i+1]);xs.append(x);ys.append(np.clip((b[i+ENTRY_HORIZON,4]-b[i+1,1])/atr,-6,6))
        costs.append(.5*(b[i+1,6]*b[i+1,7]+b[i+ENTRY_HORIZON,6]*b[i+ENTRY_HORIZON,7])/atr+.24+max(0.,float(slippage_points or 0))*b[i+1,7]/atr)
    if len(xs)<300:raise ValueError("insufficient contiguous archive")
    cut=int(.8*len(xs));x=torch.tensor(np.asarray(xs[:cut]));y=torch.tensor(np.asarray(ys[:cut]),dtype=torch.float32)
    models={};thresholds={}
    for name,maker in (("TCN",TCN),("PatchTST",OfficialPatch),("LSTM Multi",lambda:Recurrent(4)),("GRU",GRU),("Patch Transformer",Patch),("LSTM",lambda:Recurrent(1))):
        if name not in NAMES:continue
        try:
            torch.manual_seed(42);m=maker();opt=torch.optim.AdamW(m.parameters(),lr=.001)
            for _ in range(0 if name=="GRU" else epochs):
                for ix in torch.randperm(len(x)).split(128):
                    q=x[ix];q=q[:,:,:1] if name=="LSTM" else q
                    loss=nn.functional.smooth_l1_loss(m(q),y[ix]);opt.zero_grad();loss.backward();opt.step()
            m.eval()
            # Calibrate on chronological held-out windows, with saved backtest slippage.
            with torch.no_grad():
                vx=torch.tensor(np.asarray(xs[cut+3:]));pv=m(vx[:,:,:1] if name=="LSTM" else vx).numpy()
            truth=np.asarray(ys[cut+3:]);cost=np.asarray(costs[cut+3:]);options=[]
            for t in (.05,.1,.2,.35,.5,.75,1.):
                s=np.where(abs(pv)>t,np.sign(pv),0.);n=int(np.count_nonzero(s))
                if n>=20:options.append((float((s*truth-(s!=0)*cost).sum()),t))
            thresholds[name]=max(options)[1] if options else .05
            if name in ("LSTM Multi","GRU"):
                # Chronological validation and later untouched holdout, with
                # non-overlapping 3-minute outcomes. Refit from scratch below.
                from adviser_validation import select_threshold,metrics
                a=int(.6*len(xs));b=int(.8*len(xs))
                tx=torch.tensor(np.asarray(xs[:a-3]));ty=torch.tensor(np.asarray(ys[:a-3]),dtype=torch.float32)
                torch.manual_seed(42);m=maker();opt=torch.optim.AdamW(m.parameters(),lr=.0007,weight_decay=.01)
                for _ in range(epochs):
                    for ix in torch.randperm(len(tx)).split(128):
                        loss=nn.functional.smooth_l1_loss(m(tx[ix]),ty[ix]);opt.zero_grad();loss.backward();opt.step()
                m.eval()
                vi=list(range(a,b-3,2));ti=list(range(b,len(xs),2))
                with torch.no_grad():
                    pv=m(torch.tensor(np.asarray([xs[i] for i in vi]))).numpy()
                    pt=m(torch.tensor(np.asarray([xs[i] for i in ti]))).numpy()
                # Choose absolute threshold on validation only; score actual side net.
                vp=[abs(float(p)) for p in pv]
                vy=[float(np.sign(p)*ys[i]-costs[i]) for p,i in zip(pv,vi)]
                t=select_threshold(vp,vy,20)
                report=metrics([abs(float(p)) for p in pt],[float(np.sign(p)*ys[i]-costs[i]) for p,i in zip(pt,ti)],t,20) if t is not None else {"eligible":False}
                print(name,"RETEST",report,"threshold",t,flush=True)
                thresholds[name]=t if report["eligible"] else float("inf")
            models[name]=m
            print(name,"FIT_SHADOW",len(x),"threshold",thresholds[name],flush=True)
        except Exception as e:print(name,"UNAVAILABLE",type(e).__name__,flush=True)
    forest=IsolationForest(n_estimators=64,contamination=.03,n_jobs=1,random_state=42).fit(x.numpy().reshape(len(x),-1)) if USE_ANOMALY else None
    return models,forest,thresholds
def predict(fitted,b,paused=(),monitor=None):
    x,atr=feature(b);q=torch.tensor(x[None]);models,forest,thresholds=fitted;out={}
    with torch.no_grad():
        for name,m in models.items():
            v=float(m(q[:,:,:1] if name=="LSTM" else q)[0])
            if math.isfinite(v):
                if name in paused or math.isinf(thresholds.get(name,0.)):
                    if monitor is not None:monitor[name]=max(-6.,min(6.,v))
                else:out[name]=max(-6.,min(6.,v)) if abs(v)>thresholds.get(name,.05) else 0.
    if forest is not None:out["Isolation Forest"]=float(np.clip(-forest.decision_function(x.reshape(1,-1))[0],-1,1))
    return out,atr
def exact_archive(market,token,cid,symbol):
    params={"client":cid,"symbol":symbol};bars={}
    for _ in range(12):
        d=arena.api(market,"history?"+urllib.parse.urlencode(params),token)
        if d.get("client")!=cid or d.get("symbol")!=symbol:raise ValueError("archive scope mismatch")
        for r in d.get("rows") or []:
            try:
                if r.get("spread_points") is None or r.get("point_size") is None:continue
                v=arena._candle(r);bars[v["time"]]=v
            except (TypeError,ValueError,KeyError):continue
        if not d.get("next_cursor"):break
        params["cursor"]=d["next_cursor"]
    return [bars[t] for t in sorted(bars)]
def main(email=None,password=None,argv=None):
    p=argparse.ArgumentParser();p.add_argument("--epochs",type=int,default=8);p.add_argument("--market",choices=["XAUUSD","BTCUSD","BOTH"],default="BOTH")
    a=p.parse_args(argv)
    if not 1<=a.epochs<=40:raise ValueError("epochs 1..40")
    if email is None:email=input("Owner email: ").strip()
    if password is None:password=getpass.getpass("Owner password: ")
    token=arena.login(email,password);renew=time.monotonic()+1500
    markets=arena.MARKETS if a.market=="BOTH" else (a.market,)
    from concurrent.futures import ThreadPoolExecutor
    from urllib.error import HTTPError
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="parallel-train")
    fits={};last={};nextfit={};state={};reports={};calibrators={};paths={};scopes={}
    jobs={};retry={};status_log={}
    markets=tuple(sorted(markets,key=lambda m:m!="XAUUSD"))
    def note(market,key,*parts):
        now=time.monotonic()
        if now-status_log.get((market,key),-1000)>=30:
            status_log[(market,key)]=now
            print(market,*parts,flush=True)
    def bind(market,cid,symbol):
        key=(cid,symbol)
        if scopes.get(market)!=key:
            scopes[market]=key
            p=state_path(email,cid,symbol)
            paths[market]=p.with_name(p.stem+"-m1"+p.suffix) if ENTRY_HORIZON==1 else p
            state[market]=load(paths[market]);last.pop(market,None);nextfit.pop(market,None)
        d=state[market];d.setdefault("outcomes",{});d.setdefault("pending",[]);d.setdefault("pause_until",{})
        return d
    def train_job(market,auth,cid,symbol):
        print(market,"TRAIN_BACKGROUND_START","HORIZON",ENTRY_HORIZON,flush=True)
        rows=exact_archive(market,auth,cid,symbol)
        history=array(rows[-6000:]);evidence={}
        try:
            e=arena.api(market,"parallel-learning?"+urllib.parse.urlencode({"client":cid,"symbol":symbol}),auth)
            if e.get("symbol")==symbol:evidence=e
        except Exception as exc:print(market,"LEARNING_EVIDENCE_UNAVAILABLE",type(exc).__name__,flush=True)
        fitted=fit(history,a.epochs,(),evidence.get("slippage_points"))
        cal=None;report={"trade_status":"UNAVAILABLE","trade_samples":0}
        try:cal,report=trade_calibrator(history,evidence.get("positions",[]),feature)
        except Exception as exc:print(market,"TRADE_CALIBRATION_ERROR",type(exc).__name__,flush=True)
        return fitted,cal,{**report,"backtests":evidence.get("backtests",0)}
    try:
      while True:
        if time.monotonic()>=renew:
            try:token=arena.login(email,password);renew=time.monotonic()+1500
            except Exception as e:print("AUTH",type(e).__name__,flush=True);time.sleep(10);continue
        for market in markets:
            pending=jobs.get(market)
            if pending and pending[1].done():
                scope,job=jobs.pop(market)
                try:
                    fitted,cal,report=job.result()
                    if scope==scopes.get(market) and fitted[0]:
                        fits[market]=(scope,fitted);calibrators[market]=cal;reports[market]=report
                        print(market,"TRAIN_BACKGROUND_READY",list(fitted[0]),report,flush=True)
                    elif scope==scopes.get(market):nextfit[market]=time.monotonic()+120
                except Exception as exc:
                    nextfit[market]=time.monotonic()+120
                    print(market,"TRAIN_BACKGROUND_ERROR",type(exc).__name__,getattr(exc,"code",""),str(exc)[:160],flush=True)
            if time.monotonic()<retry.get(market,0):continue
            try:
                ctx=arena.live_context(market,token,last.get(market),scopes.get(market,("",None))[1],timeout=6)
                if ctx.get("status")=="UNCHANGED":continue
                if ctx.get("status")!="READY":
                    note(market,"context","CONTEXT",ctx.get("status","UNKNOWN"));continue
                cid=ctx["client"];symbol=ctx["symbol"];d=bind(market,cid,symbol)
                if market not in jobs and time.monotonic()>=nextfit.get(market,0):
                    nextfit[market]=time.monotonic()+21600
                    jobs[market]=(scopes[market],executor.submit(train_job,market,token,cid,symbol))
                if market not in fits or fits[market][0]!=scopes[market]:
                    note(market,"training","TRAINING_OR_WAITING",list(NAMES));continue
                bar=int(ctx["bar_time"])
                if last.get(market)==(cid,bar):continue
                if not 60<=time.time()-bar<120:continue
                b=array(ctx["bars"])
                if len(b)<48 or b[-1,0]-b[-48,0]!=47*60:continue
                lookup={int(row[0]):row for row in b};remain=[]
                for t,pred,scale in d["pending"]:
                    if bar<t+60*ENTRY_HORIZON:remain.append([t,pred,scale]);continue
                    if t+60 not in lookup or t+60*ENTRY_HORIZON not in lookup:continue
                    op=lookup[t+60];end=lookup[t+60*ENTRY_HORIZON];move=(end[4]-op[1])/scale
                    cost=.5*(op[6]*op[7]+end[6]*end[7])/scale+.24
                    for name,v in pred.items():
                        if name=="Isolation Forest" or abs(v)<=.05:continue
                        q=d["outcomes"].setdefault(name,[])
                        if not q or q[-1][0]<t:q.append([t,float(np.sign(v)*move>0),float(np.sign(v)*move-cost)])
                        d["outcomes"][name]=q[-600:]
                d["pending"]=remain
                # Pause sustained poor performers in monitoring only. Re-assess only new post-pause samples.
                now=time.time()
                for name in NAMES:
                    until=float(d["pause_until"].get(name,0))
                    if until>now:continue
                    fresh=[r for r in d["outcomes"].get(name,[]) if r[0]>=until]
                    if pause_model(fresh,now):d["pause_until"][name]=now+21600
                paused=[n for n,v in d["pause_until"].items() if float(v)>now]
                monitor={}
                pred,scale=predict(fits[market][1],b,paused,monitor)
                payload={}
                for name,v in {**monitor,**pred}.items():
                    q=d["outcomes"].get(name,[])
                    payload[name]={"forecast":v,"monitor_only":name in monitor,"samples":len(q),"accuracy":sum(t[1] for t in q)/len(q) if q else None,"net_atr":sum(t[2] for t in q) if q else None}
                report={**reports.get(market,{}),"persisted_samples":sum(len(q) for q in d["outcomes"].values()),
                    "paused":[n for n,v in d["pause_until"].items() if float(v)>now]}
                cal=calibrators.get(market)
                if cal is not None:
                    x,_=feature(b);row=np.r_[x[-12:].mean(0),x[-1]]
                    for side,tag in ((0,"buy"),(1,"sell")):report["trade_probability_"+tag]=float(cal.predict_proba([np.r_[row,side]])[0,1])
                if not 60<=time.time()-bar<120:save(paths[market],d);continue
                reply=arena.api(market,"parallel-monitor",token,{"client":cid,"symbol":symbol,"bar_time":bar,"horizon":ENTRY_HORIZON,"models":payload,"learning":report},timeout=6)
                if reply.get("accepted"):
                    last[market]=(cid,bar)
                    if not any(t[0]==bar for t in d["pending"]):d["pending"].append([bar,{**monitor,**pred},scale])
                    d["pending"]=d["pending"][-120:]
                save(paths[market],d)
                print(market,bar,"SHADOW",list(pred),"MONITOR",list(monitor),reply.get("accepted"),flush=True)
            except HTTPError as e:
                retry[market]=time.monotonic()+(30 if e.code==403 else 3)
                note(market,"http","SHADOW_HTTP_ERROR",e.code)
            except Exception as e:
                retry[market]=time.monotonic()+3
                note(market,"error","SHADOW_ERROR",type(e).__name__,str(e)[:160])
        time.sleep(1)
    finally:
        executor.shutdown(wait=False,cancel_futures=True)
if __name__=="__main__":main()
