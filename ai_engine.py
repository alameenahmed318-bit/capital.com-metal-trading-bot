"""Local ML decision engine used by all Capital bots.

This is a supervised-learning layer, not an LLM wrapper. It trains on rolling
Capital.com candle history and predicts BUY/SELL/WAIT. No external AI service
or AI API key is required. Safety limits remain outside this module.
"""
from __future__ import annotations
import json, os
from typing import Any
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

AI_ENABLED = os.environ.get("AI_TRADING_ENABLED", "true").lower() in {"1","true","yes"}
MIN_TRAIN_SAMPLES = int(os.environ.get("AI_MIN_TRAIN_SAMPLES","120"))
HORIZON = int(os.environ.get("AI_HORIZON","4"))
LABEL_ATR_MULT = float(os.environ.get("AI_LABEL_ATR_MULT","0.08"))
MIN_CONFIDENCE = float(os.environ.get("AI_MIN_CONFIDENCE","0.58"))
MODEL_DIR = os.environ.get("AI_MODEL_DIR","ai_models")
FEATURES = ["ret1","ret3","ret8","rsi","atr_pct","ema9_gap","ema21_gap","ema50_gap","ema200_gap","bb_z","range_pct","body_pct","close_pos","vol_ratio","htf20_gap","htf50_gap","htf200_gap"]

def _features(df, htf):
    x=df.copy(); c=pd.to_numeric(x["close"],errors="coerce"); h=pd.to_numeric(x["high"],errors="coerce"); l=pd.to_numeric(x["low"],errors="coerce"); o=pd.to_numeric(x["open"],errors="coerce")
    e9=c.ewm(span=9,adjust=False).mean(); e21=c.ewm(span=21,adjust=False).mean(); e50=c.ewm(span=50,adjust=False).mean(); e200=c.ewm(span=200,adjust=False).mean()
    d=c.diff(); gain=d.clip(lower=0).rolling(14).mean(); loss=(-d.clip(upper=0)).rolling(14).mean(); rsi=100-100/(1+gain/loss.replace(0,np.nan))
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean()
    mid=c.rolling(20).mean(); sd=c.rolling(20).std(ddof=0); af=atr.rolling(20).mean(); ass=atr.rolling(100).mean()
    if htf is None or htf.empty: htf=pd.DataFrame({"close":c})
    hc=pd.to_numeric(htf["close"],errors="coerce"); he20=hc.ewm(span=20,adjust=False).mean(); he50=hc.ewm(span=50,adjust=False).mean(); he200=hc.ewm(span=200,adjust=False).mean()
    hi=np.linspace(0,len(hc)-1,len(x)).astype(int) if len(hc) else np.zeros(len(x),dtype=int)
    hv20=np.asarray(he20)[hi] if len(hc) else np.zeros(len(x)); hv50=np.asarray(he50)[hi] if len(hc) else np.zeros(len(x)); hv200=np.asarray(he200)[hi] if len(hc) else np.zeros(len(x))
    out=pd.DataFrame(index=x.index)
    out["ret1"]=c.pct_change(1); out["ret3"]=c.pct_change(3); out["ret8"]=c.pct_change(8); out["rsi"]=rsi/100; out["atr_pct"]=atr/c.replace(0,np.nan)
    out["ema9_gap"]=(c-e9)/c.replace(0,np.nan); out["ema21_gap"]=(c-e21)/c.replace(0,np.nan); out["ema50_gap"]=(c-e50)/c.replace(0,np.nan); out["ema200_gap"]=(c-e200)/c.replace(0,np.nan)
    out["bb_z"]=(c-mid)/sd.replace(0,np.nan); out["range_pct"]=(h-l)/c.replace(0,np.nan); out["body_pct"]=(c-o).abs()/(h-l).replace(0,np.nan); out["close_pos"]=(c-l)/(h-l).replace(0,np.nan)
    out["vol_ratio"]=af/ass.replace(0,np.nan)
    last_hc=float(hc.iloc[-1]) if len(hc) else 0.0
    out["htf20_gap"]=(hv20-hv50)/np.maximum(np.abs(hv50),1e-9)
    out["htf50_gap"]=(hv50-hv200)/np.maximum(np.abs(hv200),1e-9)
    out["htf200_gap"]=(hv20-hv200)/np.maximum(np.abs(hv200),1e-9)
    return out.replace([np.inf,-np.inf],np.nan)

def _label(df):
    c=pd.to_numeric(df["close"],errors="coerce"); h=pd.to_numeric(df["high"],errors="coerce"); l=pd.to_numeric(df["low"],errors="coerce")
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean(); future=c.shift(-HORIZON)-c; threshold=atr*LABEL_ATR_MULT
    y=pd.Series(0,index=df.index,dtype=int); y[future>threshold]=1; y[future<-threshold]=-1; return y

def decide(df, htf_df, epic, existing_signal=None) -> dict[str,Any]:
    result={"enabled":AI_ENABLED,"signal":None,"confidence":0.0,"buy_probability":0.0,"sell_probability":0.0,"wait_probability":1.0,"sl_atr":2.0,"tp_atr":3.0,"reason":"AI_UNAVAILABLE"}
    if not AI_ENABLED or df is None or len(df)<260:
        result["reason"]="insufficient_ai_history"; return result
    fx=_features(df,htf_df); y=_label(df); valid=fx[FEATURES].notna().all(axis=1)
    train_end=max(0,len(df)-HORIZON-1); idx=[i for i in fx.index[:train_end] if bool(valid.loc[i])]
    if len(idx)<MIN_TRAIN_SAMPLES or y.loc[idx].nunique()<2:
        result["reason"]=f"insufficient_training_data:{len(idx)}"; return result
    model=HistGradientBoostingClassifier(max_iter=180,learning_rate=0.06,max_leaf_nodes=15,l2_regularization=1.0,random_state=42)
    model.fit(fx.loc[idx,FEATURES].astype(float),y.loc[idx].astype(int))
    latest_idx=fx.index[-2]; latest=fx.loc[[latest_idx],FEATURES].astype(float)
    if latest.isna().any(axis=None):
        result["reason"]="latest_features_invalid"; return result
    probs=model.predict_proba(latest)[0]; classes=list(model.classes_); p={int(k):float(v) for k,v in zip(classes,probs)}
    pb,ps,pw=p.get(1,0.0),p.get(-1,0.0),p.get(0,0.0); best=max(((pb,"BUY"),(ps,"SELL"),(pw,"WAIT")),key=lambda z:z[0])
    signal=best[1] if best[0]>=MIN_CONFIDENCE and best[1]!="WAIT" else None
    vol=float(fx.loc[latest_idx,"vol_ratio"]) if pd.notna(fx.loc[latest_idx,"vol_ratio"]) else 1.0
    sl_atr=float(np.clip(1.5+0.9*max(0.0,min(1.5,vol-0.7)),1.5,2.85)); rr=1.35+1.65*max(0.0,min(1.0,(best[0]-0.50)/0.50)); tp_atr=float(np.clip(sl_atr*rr,2.0,5.5))
    result.update({"signal":signal,"confidence":float(best[0]),"buy_probability":pb,"sell_probability":ps,"wait_probability":pw,"sl_atr":sl_atr,"tp_atr":tp_atr,"reason":f"AI_MODEL classes={classes} train={len(idx)}"})
    try:
        os.makedirs(MODEL_DIR,exist_ok=True)
        with open(os.path.join(MODEL_DIR,f"{epic}_latest.json"),"w",encoding="utf-8") as f: json.dump(result,f,indent=2)
    except Exception: pass
    return result
