"""Independent adviser ablation; no order permissions, no production imports."""
import argparse,json
from pathlib import Path
import numpy as np
from core import load_bars,features
from advisers import lstm_features,anomaly_features,timesfm_features

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--development",action="store_true")
    p.add_argument("--timesfm",action="store_true")
    a=p.parse_args()
    symbol,b=load_bars(a.bars);n=len(b);x,atr,valid=features(b)
    cutoff=1795210980 if symbol=="XAUUSD" else 1795422420 if symbol in ("BTCUSD","BTCUSDm") else None
    if cutoff is None:raise ValueError("Unsupported symbol")
    boundary=int(.8*n) if a.development else int(np.searchsorted(b[:,0],cutoff,side="right"))
    fit=int(.6*boundary)
    if fit<400 or n-boundary<300:raise ValueError("Not enough separated archive data")
    l,_,_=lstm_features(b,atr,valid,fit,42)
    anomaly,_=anomaly_features(x,valid,fit,42)
    models={"LSTM":l,"MOMENTUM":x[:,1]}
    masks={k:valid.copy() for k in models}
    # Nonoverlapping three-bar holding windows. At i close, buy/sell next open;
    # exit at i+3 close. All quotes and scale are consistent across advisers.
    def origins(lo,hi):
        return np.array([i for i in range(lo,hi-3,3) if valid[i] and b[i+3,0]-b[i,0]==180],dtype=int)
    va=origins(fit+5,boundary);te=origins(boundary+5,n)
    if min(len(va),len(te))<30:raise ValueError("Insufficient contiguous test origins")
    if a.timesfm:
        f,m=timesfm_features(b,atr,valid,fit,origins=np.r_[va,te])
        models["TimesFM"]=f;masks["TimesFM"]=valid & (m>0)
    def metrics(ids,pred,mask,threshold,anomaly_filter,stress):
        pred=pred[ids];eligible=mask[ids]
        side=np.where(eligible & (abs(pred)>threshold),np.sign(pred),0.)
        if anomaly_filter:side=np.where(anomaly[ids]>0,0.,side)
        raw=(b[ids+3,4]-b[ids+1,1])/atr[ids]
        cost=(.5*(b[ids+1,6]*b[ids+1,7]+b[ids+3,6]*b[ids+3,7]))/atr[ids]+(.68 if stress else .24)
        pnl=side*raw-(side!=0)*cost
        selected=side!=0;num=int(selected.sum())
        eq=np.r_[0.,np.cumsum(pnl)]
        return {"windows":len(ids),"available":int(eligible.sum()),"signals":num,
            "net_atr":float(pnl.sum()),"drawdown_atr":float(np.max(np.maximum.accumulate(eq)-eq)),
            "positive_rate":float(np.mean(pnl[selected]>0)) if num else None,
            "direction_accuracy":float(np.mean(side[selected]*raw[selected]>0)) if num else None,
            "anomaly_windows":int(np.sum(anomaly[ids]>0))}
    def pair(ids,pred,mask,t,gate):
        return {k:metrics(ids,pred,mask,t,gate,stress) for k,stress in (("base",False),("stress",True))}
    report={"symbol":symbol,"development_only":a.development,"execution_authorized":False,
            "train_end":int(b[fit-1,0]),"test_start":int(b[boundary+5,0]),"models":{},
            "cost_model":"entry+exit half-spreads, round-trip ATR costs .24/.68; not account profit",
            "limitations":["Not full production-policy replay","Pretrained model pretraining overlap unknown","No news or cloud API"]}
    for name,pred in models.items():
        mask=masks[name]
        # Independently select threshold on validation for each fixed variant. No test tuning.
        variants={}
        for gate in (False,True):
            options=[]
            for t in (0.,.1,.2,.35,.5,.75,1.,1.5,2.):
                val=pair(va,pred,mask,t,gate)
                stress=val["stress"]
                if stress["signals"]>=20:
                    options.append((stress["net_atr"]-.5*stress["drawdown_atr"],t,val))
            if not options:
                variants["anomaly_filter" if gate else "raw"]={"status":"INSUFFICIENT_SIGNALS"}
                continue
            score,t,val=max(options,key=lambda x:x[0])
            test=pair(te,pred,mask,t,gate)
            passed=not a.development and score>0 and test["stress"]["signals"]>=20 and test["stress"]["net_atr"]>0
            variants["anomaly_filter" if gate else "raw"]={"threshold":t,"validation":val,"test":test,"research_pass":passed}
        report["models"][name]=variants
    Path(a.out).write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
