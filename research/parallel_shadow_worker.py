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
    for i in range(47,len(b)-3,2):
        if b[i+3,0]-b[i-47,0]!=50*60:continue
        x,atr=feature(b[i-47:i+1]);xs.append(x);ys.append(np.clip((b[i+3,4]-b[i+1,1])/atr,-6,6))
        costs.append(.5*(b[i+1,6]*b[i+1,7]+b[i+3,6]*b[i+3,7])/atr+.24+max(0.,float(slippage_points or 0))*b[i+1,7]/atr)
    if len(xs)<300:raise ValueError("insufficient contiguous archive")
    cut=int(.8*len(xs));x=torch.tensor(np.asarray(xs[:cut]));y=torch.tensor(np.asarray(ys[:cut]),dtype=torch.float32)
    models={};thresholds={}
    for name,maker in (("TCN",TCN),("PatchTST",OfficialPatch),("LSTM Multi",lambda:Recurrent(4)),("Patch Transformer",Patch),("LSTM",lambda:Recurrent(1))):
        if name not in NAMES or name in paused:continue
        try:
            torch.manual_seed(42);m=maker();opt=torch.optim.AdamW(m.parameters(),lr=.001)
            for _ in range(epochs):
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
            models[name]=m
            print(name,"FIT_SHADOW",len(x),"threshold",thresholds[name],flush=True)
        except Exception as e:print(name,"UNAVAILABLE",type(e).__name__,flush=True)
    forest=IsolationForest(n_estimators=64,contamination=.03,n_jobs=1,random_state=42).fit(x.numpy().reshape(len(x),-1)) if USE_ANOMALY else None
    return models,forest,thresholds
def predict(fitted,b,paused=()):
    x,atr=feature(b);q=torch.tensor(x[None]);models,forest,thresholds=fitted;out={}
    with torch.no_grad():
        for name,m in models.items():
            if name in paused:continue
            v=float(m(q[:,:,:1] if name=="LSTM" else q)[0])
            if math.isfinite(v):out[name]=max(-6.,min(6.,v)) if abs(v)>thresholds.get(name,.05) else 0.
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
    fits={};last={};nextfit={};state={};reports={};calibrators={};paths={};scopes={};pause_until={}
    def bind(market,cid,symbol):
        key=(cid,symbol)
        if scopes.get(market)!=key:
            scopes[market]=key;paths[market]=state_path(email,cid,symbol);state[market]=load(paths[market]);last.pop(market,None);nextfit.pop(market,None)
        d=state[market];d.setdefault("outcomes",{});d.setdefault("pending",[]);d.setdefault("pause_until",{})
        return d
    while True:
        if time.monotonic()>=renew:
            try:token=arena.login(email,password);renew=time.monotonic()+1500
            except Exception as e:print("AUTH",type(e).__name__,flush=True);time.sleep(30);continue
        for market in markets:
            try:
                ctx=arena.api(market,"live-context",token)
                if ctx.get("status")!="READY":continue
                cid=ctx["client"];symbol=ctx["symbol"];d=bind(market,cid,symbol)
                # Only active, connected markets consume training CPU. One attempt per six hours.
                if time.monotonic()>=nextfit.get(market,0):
                    nextfit[market]=time.monotonic()+21600
                    try:
                        rows=exact_archive(market,token,cid,symbol)
                        history=array(rows[-6000:]);evidence=arena.api(market,"parallel-learning?"+urllib.parse.urlencode({"client":cid,"symbol":symbol}),token)
                        if evidence.get("symbol")!=symbol:raise ValueError("wrong evidence symbol")
                        # Retest a suspended model after six hours, not permanently delete it.
                        now=time.time()
                        paused=[n for n,v in d["pause_until"].items() if float(v)>now]
                        fitted=fit(history,a.epochs,paused,evidence.get("slippage_points"))
                        fits[market]=(scopes[market],fitted)
                        cal,report=trade_calibrator(history,evidence.get("positions",[]),feature)
                        calibrators[market]=cal;reports[market]={**report,"backtests":evidence.get("backtests",0),"paused":paused}
                        print(market,"LEARNING",reports[market],flush=True)
                    except Exception as e:
                        print(market,"FIT_ERROR",type(e).__name__,flush=True)
                        if market not in fits or fits[market][0]!=scopes[market]:
                            nextfit[market]=time.monotonic()+600;continue
                    # The fit may take more than one bar. Refresh context.
                    ctx=arena.api(market,"live-context",token)
                    if ctx.get("status")!="READY" or (ctx["client"],ctx["symbol"])!=scopes[market]:continue
                if market not in fits or fits[market][0]!=scopes[market]:continue
                bar=int(ctx["bar_time"])
                if last.get(market)==(cid,bar) or not 60<=time.time()-bar<105:continue
                b=array(ctx["bars"])
                if len(b)<48 or b[-1,0]-b[-48,0]!=47*60:continue
                lookup={int(row[0]):row for row in b};remain=[]
                for t,pred,scale in d["pending"]:
                    if bar<t+180:remain.append([t,pred,scale]);continue
                    if t+60 not in lookup or t+180 not in lookup:continue
                    op=lookup[t+60];end=lookup[t+180];move=(end[4]-op[1])/scale
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
                pred,scale=predict(fits[market][1],b,paused)
                payload={}
                for name,v in pred.items():
                    q=d["outcomes"].get(name,[])
                    payload[name]={"forecast":v,"samples":len(q),"accuracy":sum(t[1] for t in q)/len(q) if q else None,"net_atr":sum(t[2] for t in q) if q else None}
                report={**reports.get(market,{}),"persisted_samples":sum(len(q) for q in d["outcomes"].values()),
                    "paused":[n for n,v in d["pause_until"].items() if float(v)>now]}
                cal=calibrators.get(market)
                if cal is not None:
                    x,_=feature(b);row=np.r_[x[-12:].mean(0),x[-1]]
                    for side,tag in ((0,"buy"),(1,"sell")):report["trade_probability_"+tag]=float(cal.predict_proba([np.r_[row,side]])[0,1])
                if not 60<=time.time()-bar<120:save(paths[market],d);continue
                reply=arena.api(market,"parallel-monitor",token,{"client":cid,"symbol":symbol,"bar_time":bar,"models":payload,"learning":report})
                if reply.get("accepted"):
                    last[market]=(cid,bar)
                    if not any(t[0]==bar for t in d["pending"]):d["pending"].append([bar,pred,scale])
                    d["pending"]=d["pending"][-120:]
                save(paths[market],d)
                print(market,bar,"SHADOW",list(payload),reply.get("accepted"),flush=True)
            except Exception as e:print(market,"SHADOW_ERROR",type(e).__name__,flush=True)
        time.sleep(10)
if __name__=="__main__":main()
