"""One opt-in low-priority local monitoring worker, no execution endpoint.
Fits only in its own lane; existing live worker remains unchanged.
New models are research-only, never submitted to live-forecast.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
import argparse,getpass,time,sys,math,json
from pathlib import Path
from collections import deque
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
def fit(b,epochs):
    from sklearn.ensemble import IsolationForest
    xs=[];ys=[]
    for i in range(47,len(b)-3,2):
        if b[i+3,0]-b[i-47,0]!=50*60:continue
        x,atr=feature(b[i-47:i+1]);xs.append(x);ys.append(np.clip((b[i+3,4]-b[i+1,1])/atr,-6,6))
    if len(xs)<300:raise ValueError("insufficient contiguous archive")
    cut=int(.8*len(xs));x=torch.tensor(np.asarray(xs[:cut]));y=torch.tensor(np.asarray(ys[:cut]),dtype=torch.float32)
    models={}
    for name,maker in (("TCN",TCN),("PatchTST",OfficialPatch),("LSTM Multi",lambda:Recurrent(4)),("Patch Transformer",Patch),("LSTM",lambda:Recurrent(1))):
        try:
            torch.manual_seed(42);m=maker();opt=torch.optim.AdamW(m.parameters(),lr=.001)
            for _ in range(epochs):
                for ix in torch.randperm(len(x)).split(128):
                    q=x[ix];q=q[:,:,:1] if name=="LSTM" else q
                    loss=nn.functional.smooth_l1_loss(m(q),y[ix]);opt.zero_grad();loss.backward();opt.step()
            models[name]=m.eval()
            print(name,"FIT_SHADOW",len(x),flush=True)
        except Exception as e:print(name,"UNAVAILABLE",type(e).__name__,flush=True)
    forest=IsolationForest(n_estimators=64,contamination=.03,n_jobs=1,random_state=42).fit(x.numpy().reshape(len(x),-1))
    return models,forest
def predict(fitted,b):
    x,atr=feature(b);q=torch.tensor(x[None]);models,forest=fitted;out={}
    with torch.no_grad():
        for name,m in models.items():
            v=float(m(q[:,:,:1] if name=="LSTM" else q)[0])
            if math.isfinite(v):out[name]=max(-6.,min(6.,v))
    out["Isolation Forest"]=float(np.clip(-forest.decision_function(x.reshape(1,-1))[0],-1,1))
    return out,atr
def main():
    p=argparse.ArgumentParser();p.add_argument("--epochs",type=int,default=8);p.add_argument("--market",choices=["XAUUSD","BTCUSD","BOTH"],default="BOTH")
    a=p.parse_args()
    if not 1<=a.epochs<=40:raise ValueError("epochs 1..40")
    email=input("Owner email: ").strip();password=getpass.getpass("Owner password: ")
    token=arena.login(email,password);renew=time.monotonic()+1500
    markets=arena.MARKETS if a.market=="BOTH" else (a.market,)
    fits={};last={};pending={m:deque(maxlen=120) for m in markets};outcomes={m:{} for m in markets};nextfit={}
    while True:
        if time.monotonic()>=renew:
            try:token=arena.login(email,password);renew=time.monotonic()+1500
            except Exception as e:print("AUTH",type(e).__name__,flush=True);time.sleep(30);continue
        for market in markets:
            try:
                # Training is in this independent worker only. Previous models remain usable on failure.
                if time.monotonic()>=nextfit.get(market,0):
                    nextfit[market]=time.monotonic()+21600
                    cid,rows=arena.archive(market,token)
                    if cid:
                        try:fits[market]=(cid,fit(array(rows[-6000:]),a.epochs))
                        except Exception as e:print(market,"FIT_ERROR",type(e).__name__,flush=True)
                ctx=arena.api(market,"live-context",token)
                if ctx.get("status")!="READY" or market not in fits or ctx["client"]!=fits[market][0]:continue
                bar=int(ctx["bar_time"])
                if last.get(market)==(ctx["client"],bar) or not 60<=time.time()-bar<105:continue
                b=array(ctx["bars"])
                if len(b)<48 or b[-1,0]-b[-48,0]!=47*60:continue
                scope=(ctx["client"],ctx["symbol"])
                # Evaluate only matured, matching symbol/client forecasts at exact t+3.
                lookup={int(row[0]):row for row in b}
                remain=deque(maxlen=120)
                for key,t,pred,scale in pending[market]:
                    if key!=scope:continue
                    if bar<t+180:remain.append((key,t,pred,scale));continue
                    if t+60 not in lookup or t+180 not in lookup:continue
                    op=lookup[t+60];end=lookup[t+180]
                    move=(end[4]-op[1])/scale
                    cost=.5*(op[6]*op[7]+end[6]*end[7])/scale+.24
                    for name,v in pred.items():
                        if name=="Isolation Forest" or abs(v)<=.05:continue
                        q=outcomes[market].setdefault((scope,name),deque(maxlen=300))
                        q.append((float(np.sign(v)*move>0),float(np.sign(v)*move-cost)))
                pending[market]=remain
                pred,scale=predict(fits[market][1],b)
                if not 60<=time.time()-bar<120:continue
                payload={}
                for name,v in pred.items():
                    q=outcomes[market].get((scope,name),[])
                    payload[name]={"forecast":v,"samples":len(q),"accuracy":sum(t[0] for t in q)/len(q) if q else None,"net_atr":sum(t[1] for t in q) if q else None}
                reply=arena.api(market,"parallel-monitor",token,{"client":ctx["client"],"symbol":ctx["symbol"],"bar_time":bar,"models":payload})
                if reply.get("accepted"):
                    last[market]=(ctx["client"],bar);pending[market].append((scope,bar,pred,scale))
                print(market,bar,"SHADOW",list(payload),reply.get("accepted"),flush=True)
            except Exception as e:print(market,"SHADOW_ERROR",type(e).__name__,flush=True)
        time.sleep(10)
if __name__=="__main__":main()
