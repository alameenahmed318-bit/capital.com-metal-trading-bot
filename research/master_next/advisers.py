"""Causal feature adapters. Optional checkpoints only on explicit request."""
import csv
import numpy as np

def lstm_features(b,atr,valid,fit_end,seed):
    import torch
    from torch import nn
    torch.manual_seed(seed);torch.set_num_threads(1)
    c=b[:,4];length=32
    rows=np.arange(length-1,len(b))
    contexts=np.asarray([(c[i-length+1:i+1]-c[i])/atr[i] for i in rows],dtype=np.float32)
    contexts=np.clip(contexts,-12,12)[:,:,None]
    train=(rows+3<fit_end)&valid[rows]&valid[np.minimum(rows+3,len(b)-1)]&(b[np.minimum(rows+3,len(b)-1),0]-b[rows,0]==180)
    if train.sum()<100:raise ValueError("Insufficient contiguous LSTM training windows")
    xi=torch.from_numpy(contexts[train]);orig=rows[train]
    y=torch.from_numpy(np.clip((c[orig+3]-c[orig])/atr[orig],-6,6).astype(np.float32))
    class Forecast(nn.Module):
        def __init__(self):
            super().__init__();self.rnn=nn.LSTM(1,16,batch_first=True);self.head=nn.Linear(16,1)
        def forward(self,x):
            h,_=self.rnn(x);return self.head(h[:,-1]).squeeze(-1)
    m=Forecast();optim=torch.optim.Adam(m.parameters(),lr=.002)
    for _ in range(8):
        for ix in torch.randperm(len(xi)).split(128):
            loss=nn.functional.smooth_l1_loss(m(xi[ix]),y[ix])
            optim.zero_grad();loss.backward();optim.step()
    result=np.zeros(len(b),np.float32);m.eval()
    mask=(rows>=fit_end)&valid[rows]
    with torch.no_grad():
        result[rows[mask]]=np.clip(m(torch.from_numpy(contexts[mask])).numpy(),-6,6)
    return result,mask.sum().item(),m

def anomaly_features(x,valid,fit_end,seed):
    from sklearn.ensemble import IsolationForest
    m=IsolationForest(n_estimators=100,contamination=.03,random_state=seed,n_jobs=1)
    m.fit(x[:fit_end][valid[:fit_end]])
    return np.clip(-m.decision_function(x),-1,1).astype(np.float32),m

def timesfm_features(b,atr,valid,fit_end,origins=None):
    # Explicit opt-in; same checkpoint as the existing project, no duplicate live worker.
    import torch
    torch.set_num_threads(1)
    import timesfm
    model=timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch",torch_compile=False)
    model.compile(timesfm.ForecastConfig(max_context=128,max_horizon=128,normalize_inputs=True,per_core_batch_size=2,infer_is_positive=False))
    vals=np.zeros(len(b),np.float32);mask=np.zeros(len(b),np.float32)
    ids=[int(i) for i in (range(max(127,fit_end),len(b)) if origins is None else origins) if i>=max(127,fit_end) and valid[i] and b[i,0]-b[i-127,0]==127*60]
    for start in range(0,len(ids),16):
        batch=ids[start:start+16]
        contexts=[np.asarray((b[i-127:i+1,4]-b[i,4])/atr[i],dtype=np.float32) for i in batch]
        point,_=model.forecast(horizon=3,inputs=contexts)
        for i,p in zip(batch,point):
            if np.isfinite(p[2]):vals[i]=np.clip(p[2],-6,6);mask[i]=1
    return vals,mask

def news_features(b,path,symbol):
    # User supplies historical point-in-time English headlines with actual ingestion timestamps.
    # No live news scraping, and no assumption that "positive" means gold BUY.
    from transformers import pipeline
    rows=[]
    with open(path,encoding="utf-8-sig",newline="") as fh:
        for row in csv.DictReader(fh):
            if row["symbol"]!=symbol:continue
            published=float(row["published_unix"]);available=float(row["available_unix"])
            if available<published or not np.isfinite([published,available]).all():raise ValueError("Invalid news availability")
            rows.append((available,row["headline"][:1500]))
    if not rows:raise ValueError("No matching point-in-time news")
    pipe=pipeline("text-classification",model="ProsusAI/finbert",device=-1,top_k=None)
    records=[]
    for available,headline in sorted(rows):
        pred=pipe(headline,truncation=True)[0]
        scores={p["label"].lower():p["score"] for p in pred}
        records.append((available,scores.get("positive",0)-scores.get("negative",0)))
    vals=np.zeros(len(b),np.float32);mask=np.zeros(len(b),np.float32)
    for i,t in enumerate(b[:,0]+60):
        selected=[s for at,s in records if t-900<at<=t]
        if selected:vals[i]=np.mean(selected);mask[i]=1
    return vals,mask
