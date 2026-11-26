"""Four forecast candidates. Research on public development fixtures only.
Kronos/Moirai frozen base + locally learned Ridge calibration; Chronos LoRA;
official Hugging Face PatchTST architecture trained from scratch.
"""
import argparse,json,time,sys
from pathlib import Path
import numpy as np
import torch
from core import load_bars,features
from sklearn.linear_model import Ridge

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--model",required=True,choices=["kronos","chronos","patchtst","moirai"])
    p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--kronos-path");p.add_argument("--development",action="store_true",required=True)
    a=p.parse_args();torch.set_num_threads(1);torch.manual_seed(42);np.random.seed(42)
    symbol,b=load_bars(a.bars);x,atr,valid=features(b);n=len(b);fit=int(.52*n);boundary=int(.8*n)
    def origins(lo,hi,step):
        return np.array([i for i in range(max(127,lo),hi-3,step) if valid[i] and b[i+3,0]-b[i-127,0]==130*60],dtype=int)
    tr=origins(0,fit,3);va=origins(fit+5,boundary,3);te=origins(boundary+5,n,3)
    if min(len(tr),len(va),len(te))<30:raise ValueError("Insufficient separated windows")
    def ctx(ids):return np.asarray([(b[i-127:i+1,4]-b[i,4])/atr[i] for i in ids],dtype=np.float32)
    def future(ids):return np.asarray([(b[i+1:i+4,4]-b[i,4])/atr[i] for i in ids],dtype=np.float32)
    def y(ids):return (b[ids+3,4]-b[ids+1,1])/atr[ids]
    def chunks(ids,size=16):
        for k in range(0,len(ids),size):yield ids[k:k+size]
    model=a.model
    if model=="kronos":
        import pandas as pd
        if not a.kronos_path:raise ValueError("Pinned Kronos source directory required")
        sys.path.insert(0,a.kronos_path)
        from model import Kronos,KronosTokenizer,KronosPredictor
        predictor=KronosPredictor(Kronos.from_pretrained("NeoQuasar/Kronos-small"),KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base"),device="cpu",max_context=128)
        def pred(ids):
            result=[]
            for ix in chunks(ids,8):
                frames=[pd.DataFrame(b[i-127:i+1,1:5],columns=["open","high","low","close"]) for i in ix]
                ts=[pd.Series(pd.to_datetime(b[i-127:i+1,0],unit="s")) for i in ix]
                ft=[pd.Series(pd.to_datetime(b[i,0]+np.arange(1,4)*60,unit="s")) for i in ix]
                f=predictor.predict_batch(frames,ts,ft,pred_len=3,T=.7,top_p=.9,sample_count=3,verbose=False)
                result.extend((float(z["close"].iloc[-1])-b[i,4])/atr[i] for i,z in zip(ix,f))
            return np.asarray(result)
        method="FROZEN_KRONOS_SMALL_PLUS_RIDGE_CALIBRATION"
    elif model=="moirai":
        from uni2ts.model.moirai2 import Moirai2Forecast,Moirai2Module
        m=Moirai2Forecast(module=Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small"),
            prediction_length=3,context_length=128,target_dim=1,feat_dynamic_real_dim=0,past_feat_dynamic_real_dim=0).to("cpu").eval()
        def pred(ids):
            result=[]
            with torch.no_grad():
                for ix in chunks(ids):
                    f=m.predict(list(ctx(ix)))
                    result.extend(np.asarray(f)[:,4,2].tolist())
            return np.asarray(result)
        method="FROZEN_MOIRAI2_PLUS_RIDGE_CALIBRATION_NONCOMMERCIAL_RESEARCH"
    elif model=="chronos":
        from chronos import Chronos2Pipeline
        import peft
        m=Chronos2Pipeline.from_pretrained("amazon/chronos-2",device_map="cpu",torch_dtype=torch.float32)
        # Only complete contexts + targets wholly before the fit cutoff.
        series=[torch.from_numpy(np.r_[c,f].astype(np.float32)) for c,f in zip(ctx(tr),future(tr))]
        m=m.fit(series,prediction_length=3,finetune_mode="lora",context_length=128,
            min_past=64,num_steps=32,batch_size=8,learning_rate=1e-5,
            output_dir=Path(a.out).with_suffix(".chronos"),report_to=[],disable_tqdm=True)
        def pred(ids):
            out=[]
            for ix in chunks(ids,8):
                q,_=m.predict_quantiles([torch.tensor(c) for c in ctx(ix)],prediction_length=3,quantile_levels=[.5],batch_size=8)
                out.extend(float(z.reshape(-1,3,1)[0,2,0]) for z in q)
            return np.asarray(out)
        method="CHRONOS2_LORA_32_STEPS"
    else:
        from transformers import PatchTSTConfig,PatchTSTForPrediction
        m=PatchTSTForPrediction(PatchTSTConfig(num_input_channels=1,context_length=128,prediction_length=3,
            patch_length=8,patch_stride=8,d_model=32,num_attention_heads=4,num_hidden_layers=2,ffn_dim=64,loss="mse",scaling=None))
        train=torch.from_numpy(ctx(tr)[:,:,None]);tgt=torch.from_numpy(future(tr)[:,:,None])
        opt=torch.optim.AdamW(m.parameters(),lr=.001)
        m.train()
        for epoch in range(20):
            for ix in torch.randperm(len(train)).split(64):
                loss=m(past_values=train[ix],future_values=tgt[ix]).loss
                opt.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),1.);opt.step()
        m.eval()
        def pred(ids):
            result=[]
            with torch.no_grad():
                for ix in chunks(ids,32):
                    z=m(past_values=torch.from_numpy(ctx(ix)[:,:,None])).prediction_outputs
                    result.extend(z[:,2,0].tolist())
            return np.asarray(result)
        method="HF_PATCHTST_TRAINED_20_EPOCHS"
    # Calibration on training only, threshold on validation only. All models receive same split.
    start=time.perf_counter();pv=pred(va);elapsed=time.perf_counter()-start
    pt=pred(te)
    if model in ("kronos","moirai"):
        pr=pred(tr)
        if not np.isfinite(pr).all():raise ValueError("Nonfinite training predictions")
        cal=Ridge(alpha=10.).fit(np.column_stack([pr,x[tr,1],x[tr,5]]),y(tr))
        pv=cal.predict(np.column_stack([pv,x[va,1],x[va,5]]))
        pt=cal.predict(np.column_stack([pt,x[te,1],x[te,5]]))
    if len(pv)!=len(va) or len(pt)!=len(te) or not np.isfinite(np.r_[pv,pt]).all():raise ValueError("Bad forecasts")
    def stats(ids,vals,threshold,stress):
        side=np.where(abs(vals)>threshold,np.sign(vals),0.);raw=y(ids)
        cost=.5*(b[ids+1,6]*b[ids+1,7]+b[ids+3,6]*b[ids+3,7])/atr[ids]+(.68 if stress else .24)
        pnl=side*raw-(side!=0)*cost;eq=np.r_[0.,np.cumsum(pnl)];s=side!=0;n=int(s.sum())
        return {"signals":n,"buy":int((side>0).sum()),"sell":int((side<0).sum()),"net_atr":float(pnl.sum()),
            "drawdown_atr":float(np.max(np.maximum.accumulate(eq)-eq)),
            "direction_accuracy":float(np.mean(side[s]*raw[s]>0)) if n else None,
            "positive_rate":float(np.mean(pnl[s]>0)) if n else None}
    opts=[]
    for t in sorted(set([0.]+[float(np.quantile(abs(pv),q)) for q in (.2,.4,.6,.75,.85,.9)])):
        r=stats(va,pv,t,True)
        if r["signals"]>=20 and min(r["buy"],r["sell"])>=3:opts.append((r["net_atr"]-.5*r["drawdown_atr"],t,r))
    report={"symbol":symbol,"model":model,"method":method,"development_only":True,"execution_authorized":False,
        "train_windows":len(tr),"validation_windows":len(va),"test_windows":len(te),
        "batch_inference_ms_per_window":elapsed*1000/len(va),"research_pass":False}
    if opts:
        score,t,r=max(opts,key=lambda z:z[0])
        report.update(threshold=t,validation_stress=r,test_base=stats(te,pt,t,False),test_stress=stats(te,pt,t,True))
    else:report["status"]="INSUFFICIENT_VALIDATION_SIGNALS"
    Path(a.out).write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
