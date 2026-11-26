"""Research-only multivariate LSTM, temporal convolution, patch Transformer.
Train on chronological past, calibrate thresholds on validation, report later data.
Patch network is a compact PatchTST-inspired challenger, not the official checkpoint.
"""
import argparse,json,math,time
from pathlib import Path
import numpy as np
import torch
from torch import nn
from core import load_bars,features
from advisers import anomaly_features

class LSTM(nn.Module):
    def __init__(self,d):
        super().__init__();self.rnn=nn.LSTM(d,32,2,batch_first=True,dropout=.1);self.head=nn.Linear(32,1)
    def forward(self,x):return self.head(self.rnn(x)[0][:,-1]).squeeze(-1)

class TCN(nn.Module):
    def __init__(self,d):
        super().__init__();self.first=nn.Conv1d(d,32,1)
        self.layers=nn.ModuleList([nn.Conv1d(32,32,3,dilation=i) for i in (1,2,4,8)])
        self.head=nn.Linear(32,1)
    def forward(self,x):
        z=self.first(x.transpose(1,2))
        for layer,d in zip(self.layers,(1,2,4,8)):
            z=z+torch.relu(layer(nn.functional.pad(z,(2*d,0))))
        return self.head(z[:,:,-1]).squeeze(-1)

class Patch(nn.Module):
    def __init__(self,d):
        super().__init__();self.embed=nn.Linear(8*d,32);self.pos=nn.Parameter(torch.zeros(1,8,32))
        self.enc=nn.TransformerEncoder(nn.TransformerEncoderLayer(32,4,64,.1,batch_first=True),2)
        self.head=nn.Linear(32,1)
    def forward(self,x):
        z=self.embed(x.reshape(len(x),8,-1))+self.pos
        return self.head(self.enc(z)[:,-1]).squeeze(-1)

def main():
    p=argparse.ArgumentParser();p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--development",action="store_true");p.add_argument("--epochs",type=int,default=40)
    a=p.parse_args();torch.set_num_threads(1)
    if a.epochs<10:raise ValueError("At least 10 epochs")
    symbol,b=load_bars(a.bars);x,atr,valid=features(b);n=len(b)
    cutoff=1795210980 if symbol=="XAUUSD" else 1795422420 if symbol in ("BTCUSD","BTCUSDm") else None
    if cutoff is None:raise ValueError("Unsupported market")
    boundary=int(.8*n) if a.development else int(np.searchsorted(b[:,0],cutoff,side="right"))
    fit=int(.65*boundary)
    if fit<400 or n-boundary<300:raise ValueError("Not enough separated data")
    # 64 closed bars of causal OHLC-derived features and tick volume.
    def ids(lo,hi,step):
        return np.array([i for i in range(max(63,lo),hi-3,step)
                         if valid[i] and b[i+3,0]-b[i-63,0]==66*60],dtype=int)
    tr=ids(0,fit,1);va=ids(fit+5,boundary,3);te=ids(boundary+5,n,3)
    if min(len(tr),len(va),len(te))<30:raise ValueError("Insufficient samples")
    def contexts(i):return torch.from_numpy(np.stack([x[j-63:j+1] for j in i]))
    def target(i):return (b[i+3,4]-b[i+1,1])/atr[i]
    tx=contexts(tr);ty=torch.from_numpy(np.clip(target(tr),-6,6).astype(np.float32))
    vx=contexts(va);ex=contexts(te)
    anomaly,_=anomaly_features(x,valid,fit,42)
    def stats(i,pred,t,gate,stress):
        s=np.where(abs(pred)>t,np.sign(pred),0.)
        if gate:s=np.where(anomaly[i]>0,0.,s)
        cost=.5*(b[i+1,6]*b[i+1,7]+b[i+3,6]*b[i+3,7])/atr[i]+(.68 if stress else .24)
        raw=target(i);pnl=s*raw-(s!=0)*cost;eq=np.r_[0.,np.cumsum(pnl)]
        k=s!=0;num=int(k.sum())
        return {"signals":num,"buy":int((s>0).sum()),"sell":int((s<0).sum()),"net_atr":float(pnl.sum()),
          "drawdown_atr":float(np.max(np.maximum.accumulate(eq)-eq)),
          "direction_accuracy":float((s[k]*raw[k]>0).mean()) if num else None,
          "positive_rate":float((pnl[k]>0).mean()) if num else None}
    report={"symbol":symbol,"development_only":a.development,"execution_authorized":False,
        "train_windows":len(tr),"validation_windows":len(va),"test_windows":len(te),
        "epochs_per_seed":a.epochs,"models":{},"limitations":["Previously inspected fixture in development mode",
        "Compact patch Transformer inspired by PatchTST, not official model",
        "M1 next-open three-bar proxy, not exact MT5 positions or live profits"]}
    predictions={}
    for name,cls in (("LSTM_MULTIVARIATE",LSTM),("TCN",TCN),("PATCH_TRANSFORMER",Patch)):
        vals=[];tests=[];lat=[]
        for seed in (17,42,83):
            torch.manual_seed(seed);m=cls(x.shape[1]);opt=torch.optim.AdamW(m.parameters(),lr=.001,weight_decay=.001)
            for epoch in range(a.epochs):
                m.train()
                for ix in torch.randperm(len(tx)).split(128):
                    loss=nn.functional.smooth_l1_loss(m(tx[ix]),ty[ix]);opt.zero_grad();loss.backward()
                    nn.utils.clip_grad_norm_(m.parameters(),1.);opt.step()
            m.eval()
            with torch.no_grad():
                vals.append(m(vx).numpy());tests.append(m(ex).numpy())
                start=time.perf_counter()
                for _ in range(20):m(ex[:1])
                lat.append((time.perf_counter()-start)*50)
        predictions[name]=(np.mean(vals,axis=0),np.mean(tests,axis=0),float(np.median(lat)))
    predictions["ENSEMBLE"]=(np.mean([v[0] for v in predictions.values()],axis=0),np.mean([v[1] for v in predictions.values()],axis=0),sum(v[2] for v in predictions.values()))
    predictions["MOMENTUM"]=(x[va,1],x[te,1],0.)
    for name,(v,e,ms) in predictions.items():
        variants={}
        for gate in (False,True):
            options=[]
            # Quantiles derived only from validation; require both directions and enough signals.
            for threshold in sorted(set([0.]+[float(np.quantile(abs(v),q)) for q in (.2,.4,.6,.75,.85,.9)])):
                r=stats(va,v,threshold,gate,True)
                if r["signals"]>=20 and min(r["buy"],r["sell"])>=3:
                    options.append((r["net_atr"]-.5*r["drawdown_atr"],threshold,r))
            key="anomaly_filter" if gate else "raw"
            if not options:variants[key]={"status":"INSUFFICIENT_SIGNALS"};continue
            rank,t,r=max(options,key=lambda z:z[0])
            base=stats(te,e,t,gate,False);stress=stats(te,e,t,gate,True)
            variants[key]={"threshold":t,"validation_stress":r,"test_base":base,"test_stress":stress,
                "validation_positive":rank>0,
                "research_pass":bool(not a.development and rank>0 and base["net_atr"]>0 and stress["net_atr"]>0 and stress["signals"]>=20 and min(stress["buy"],stress["sell"])>=3)}
        report["models"][name]={"single_inference_ms_cpu":ms,"variants":variants}
    Path(a.out).write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
