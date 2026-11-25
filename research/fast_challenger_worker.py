"""CPU challenger benchmark. Read-only history API; no forecast/order submissions.
Run separate from live worker. Local report only. All four share chronological
splits, costs and identical sampled evaluation origins.
"""
import argparse, gc, getpass, json, math, os, statistics, time
from pathlib import Path
os.environ["OMP_NUM_THREADS"]="1"
os.environ["MKL_NUM_THREADS"]="1"
os.environ["TOKENIZERS_PARALLELISM"]="false"
import model_arena_worker as arena
from specialist_market import samples, features

NAMES=("TCN","GRU","Granite TTM R2","Moirai 2.0 Small")
CONTEXT=512
EXTRA_COST=.10

def dataset(bars):
    import numpy as np
    bars=sorted({x["time"]:x for x in bars}.values(),key=lambda x:x["time"])
    by={x["time"]:i for i,x in enumerate(bars)}
    out=[]
    for row in samples(bars):
        i=by[row["time"]]
        if i<CONTEXT-1:continue
        w=bars[i-CONTEXT+1:i+1]
        if w[-1]["time"]-w[0]["time"]!=(CONTEXT-1)*60:continue
        f=features(w[-61:])
        if f is None:continue
        _,atr=f
        x=np.array([(b["close"]-w[-1]["close"])/atr for b in w],dtype=np.float32)
        if np.isfinite(x).all():out.append({**row,"series":x})
    n=len(out)
    if n<600:return None
    a=int(n*.6);b=int(n*.8)
    tr=[r for r in out[:a] if r["end"]<out[a]["time"]]
    va=[r for r in out[a:b] if r["end"]<out[b]["time"]][::4]
    te=out[b:][::4]
    def bounded(rows):
        if len(rows)<=192:return rows
        return [rows[i*(len(rows)-1)//191] for i in range(192)]
    va,te=bounded(va),bounded(te)
    if len(tr)<300 or min(len(va),len(te))<40:return None
    return tr,va,te

def neural(name,tr):
    import torch
    from torch import nn
    import numpy as np
    torch.manual_seed(42)
    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            if name=="GRU":
                self.gru=nn.GRU(1,16,batch_first=True)
            else:
                layers=[]
                for j,d in enumerate((1,2,4,8)):
                    layers.extend([nn.ConstantPad1d((2*d,0),0),nn.Conv1d(1 if j==0 else 16,16,3,dilation=d),nn.ReLU()])
                self.conv=nn.Sequential(*layers)
            self.head=nn.Linear(16,1)
        def forward(self,x):
            if name=="GRU":h=self.gru(x)[0][:,-1,:]
            else:h=self.conv(x.transpose(1,2))[:,:,-1]
            return self.head(h).squeeze(-1)
    model=Net()
    opt=torch.optim.AdamW(model.parameters(),lr=.001)
    x=torch.tensor(np.stack([np.clip(r["series"][-64:],-20,20) for r in tr]))[:,:,None]
    y=torch.tensor([max(-6,min(6,r["y"])) for r in tr],dtype=torch.float32)
    for epoch in range(8):
        for idx in torch.randperm(len(x)).split(128):
            opt.zero_grad()
            loss=nn.functional.smooth_l1_loss(model(x[idx]),y[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            opt.step()
    model.eval()
    def predict(series):
        with torch.inference_mode():
            return model(torch.tensor(np.clip(np.stack(series)[:,-64:],-20,20))[:,:,None]).numpy()
    return predict

def pretrained(name):
    import numpy as np
    import torch
    if name=="Granite TTM R2":
        from tsfm_public.toolkit.get_model import get_model
        model=get_model("ibm-granite/granite-timeseries-ttm-r2",context_length=512,prediction_length=3).to("cpu").eval()
        def predict(series):
            x=torch.tensor(np.stack(series),dtype=torch.float32)[:,:,None]
            with torch.inference_mode():
                out=model(past_values=x).prediction_outputs
            if out.ndim!=3 or out.shape[1]<3:raise ValueError("Unexpected Granite output shape")
            return out[:,2,0].cpu().numpy()
    else:
        from uni2ts.model.moirai2 import Moirai2Forecast,Moirai2Module
        model=Moirai2Forecast(module=Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small"),
            prediction_length=3,context_length=512,target_dim=1,
            feat_dynamic_real_dim=0,past_feat_dynamic_real_dim=0).to("cpu").eval()
        def predict(series):
            with torch.inference_mode():
                out=np.asarray(model.predict([np.asarray(x) for x in series]))
            if out.ndim!=3 or out.shape[1]<5 or out.shape[2]<3:raise ValueError("Unexpected Moirai output shape")
            return out[:,4,2]
    return predict

def metrics(rows,pred,cutoff):
    net=0.;n=pos=0;peak=dd=0.
    for r,p in zip(rows,pred):
        if abs(p)<=cutoff:continue
        payoff=r["buy" if p>0 else "sell"]-EXTRA_COST
        n+=1;pos+=payoff>0;net+=payoff
        peak=max(peak,net);dd=max(dd,peak-net)
    return {"signals":n,"positive":pos,"net_atr":round(net,5),"drawdown_atr":round(dd,5)}

def evaluate(name,parts):
    import numpy as np
    tr,va,te=parts
    t=time.monotonic()
    predict=neural(name,tr) if name in ("TCN","GRU") else pretrained(name)
    setup=round(time.monotonic()-t,2)
    predict([va[0]["series"]])
    times=[];pred=[]
    for row in va+te:
        t=time.perf_counter()
        p=np.asarray(predict([row["series"]])).reshape(-1)
        times.append((time.perf_counter()-t)*1000)
        if len(p)!=1 or not math.isfinite(float(p[0])):raise ValueError("Invalid forecast")
        pred.append(float(p[0]))
    pv,pt=pred[:len(va)],pred[len(va):]
    choices=[]
    for cut in (.05,.10,.15,.20,.30,.40,.50,.75,1.0):
        v=metrics(va,pv,cut)
        if v["signals"]>=20 and v["net_atr"]>0:choices.append((v["net_atr"],cut,v))
    speed={"median_ms":round(statistics.median(times),2),"p95_ms":round(sorted(times)[math.ceil(.95*len(times))-1],2)}
    common={"setup_sec":setup,**speed,"validation_windows":len(va),"test_windows":len(te),"extra_cost_atr":EXTRA_COST}
    if not choices:return {**common,"status":"VALIDATION_REJECTED"}
    _,cut,v=max(choices,key=lambda z:z[0])
    te_result=metrics(te,pt,cut)
    passed=te_result["signals"]>=20 and te_result["net_atr"]>0 and speed["p95_ms"]<=500
    return {**common,"status":"REVIEW_CANDIDATE" if passed else "TEST_REJECTED","threshold":cut,"validation":v,"test":te_result}

def main():
    import torch
    torch.set_num_threads(1)
    parser=argparse.ArgumentParser()
    parser.add_argument("--models",nargs="+",choices=NAMES,default=list(NAMES))
    parser.add_argument("--market",choices=("XAUUSD","BTCUSD","both"),default="both")
    args=parser.parse_args()
    email=input("Owner email: ").strip()
    password=getpass.getpass("Owner password: ")
    token=arena.login(email,password)
    report={"execution_authorized":False,"markets":{}}
    for market in ("XAUUSD","BTCUSD"):
        if args.market not in (market,"both"):continue
        try:
            cid,bars=arena.archive(market,token)
            parts=dataset(bars)
            results={}
            report["markets"][market]={"candles":len(bars),"models":results}
            for name in args.models:
                try:
                    print(market,name,"TESTING",flush=True)
                    result=evaluate(name,parts) if parts else {"status":"INSUFFICIENT_HISTORY"}
                except Exception as exc:
                    result={"status":"ERROR","detail":type(exc).__name__+": "+str(exc)[:300]}
                results[name]=result
                print(market,name,json.dumps(result,allow_nan=False),flush=True)
                gc.collect()
                time.sleep(1)
        except Exception as exc:report["markets"][market]={"status":"ERROR","detail":type(exc).__name__}
    path=Path(__file__).resolve().parent/("fast_challengers_"+time.strftime("%Y%m%d_%H%M%S")+".json")
    path.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print("REPORT",path,flush=True)

if __name__=="__main__":main()
