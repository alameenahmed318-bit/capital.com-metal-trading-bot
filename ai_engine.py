"""Local ML decision engine used by all Capital bots.

This is a supervised-learning layer, not an LLM wrapper. It trains on rolling
Capital.com candle history and predicts BUY/SELL/WAIT. No external AI service
or AI API key is required. Safety limits remain outside this module.
"""
from __future__ import annotations
import json, os
from typing import Any

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

AI_ENABLED = os.environ.get("AI_TRADING_ENABLED", "true").lower() in {"1","true","yes"}
AI_REQUIRE_STRATEGY_AGREEMENT = os.environ.get("AI_REQUIRE_STRATEGY_AGREEMENT", "true").lower() in {"1","true","yes"}
BASE_MODEL_DIR = os.environ.get("AI_MODEL_DIR","ai_models")
DECISION_CACHE = {}

PROFILES = {
    "CAPITAL_V1": {"min_train":120,"horizon":4,"label_atr":0.08,"min_conf":0.58,"wf_min_train":90,"wf_acc":0.40,"max_iter":180,"lr":0.06,"leaf":15,"seed":101},
    "CAPITAL_V2_QUANT_HYBRID": {"min_train":130,"horizon":4,"label_atr":0.08,"min_conf":0.59,"wf_min_train":95,"wf_acc":0.41,"max_iter":200,"lr":0.055,"leaf":17,"seed":202},
    "CAPITAL_V3_RAPID_PROFIT": {"min_train":120,"horizon":3,"label_atr":0.07,"min_conf":0.58,"wf_min_train":90,"wf_acc":0.40,"max_iter":170,"lr":0.065,"leaf":13,"seed":303},
    "CAPITAL_V4_SMART_OPPORTUNITY": {"min_train":140,"horizon":5,"label_atr":0.10,"min_conf":0.62,"wf_min_train":100,"wf_acc":0.42,"max_iter":220,"lr":0.05,"leaf":19,"seed":404},
}
def _profile(strategy_id):
    return PROFILES.get(strategy_id, PROFILES["CAPITAL_V1"])

FEATURES = ["ret1","ret3","ret8","rsi","atr_pct","ema9_gap","ema21_gap","ema50_gap","ema200_gap","bb_z","range_pct","body_pct","close_pos","vol_ratio","htf20_gap","htf50_gap","htf200_gap"]

def _features(df, htf):
    x=df.copy(); c=pd.to_numeric(x["close"],errors="coerce"); h=pd.to_numeric(x["high"],errors="coerce"); l=pd.to_numeric(x["low"],errors="coerce"); o=pd.to_numeric(x["open"],errors="coerce")
    e9=c.ewm(span=9,adjust=False).mean(); e21=c.ewm(span=21,adjust=False).mean(); e50=c.ewm(span=50,adjust=False).mean(); e200=c.ewm(span=200,adjust=False).mean()
    d=c.diff(); gain=d.clip(lower=0).rolling(14).mean(); loss=(-d.clip(upper=0)).rolling(14).mean(); rsi=100-100/(1+gain/loss.replace(0,np.nan))
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean()
    mid=c.rolling(20).mean(); sd=c.rolling(20).std(ddof=0); af=atr.rolling(20).mean(); ass=atr.rolling(100).mean()
    if htf is None or htf.empty: htf=pd.DataFrame({"close":c})
    hc=pd.to_numeric(htf["close"],errors="coerce"); he20=hc.ewm(span=20,adjust=False).mean(); he50=hc.ewm(span=50,adjust=False).mean(); he200=hc.ewm(span=200,adjust=False).mean()
    # Align higher-timeframe features by candle time, not by row position.
    def _time_col(frame):
        for name in ("snapshotTimeUTC", "snapshotTime", "timestamp", "time", "datetime", "date"):
            if name in frame.columns:
                return name
        return None

    time_x, time_h = _time_col(x), _time_col(htf)
    if time_x and time_h:
        base = pd.DataFrame({"_t": pd.to_datetime(x[time_x], utc=True, errors="coerce")}, index=x.index)
        higher = pd.DataFrame({
            "_t": pd.to_datetime(htf[time_h], utc=True, errors="coerce"),
            "_hv20": np.asarray(he20, dtype=float),
            "_hv50": np.asarray(he50, dtype=float),
            "_hv200": np.asarray(he200, dtype=float),
        }).dropna(subset=["_t"]).sort_values("_t")
        base_sorted = base.sort_values("_t")
        aligned = pd.merge_asof(base_sorted, higher, on="_t", direction="backward")
        aligned = aligned.set_index(base_sorted.index).reindex(x.index)
        hv20 = aligned["_hv20"].to_numpy()
        hv50 = aligned["_hv50"].to_numpy()
        hv200 = aligned["_hv200"].to_numpy()
    else:
        # Compatibility fallback for legacy candle frames without timestamps.
        hi=np.linspace(0,len(hc)-1,len(x)).astype(int) if len(hc) else np.zeros(len(x),dtype=int)
        hv20=np.asarray(he20)[hi] if len(hc) else np.zeros(len(x))
        hv50=np.asarray(he50)[hi] if len(hc) else np.zeros(len(x))
        hv200=np.asarray(he200)[hi] if len(hc) else np.zeros(len(x))

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

def _make_model(profile):
    return HistGradientBoostingClassifier(
        max_iter=profile["max_iter"],
        learning_rate=profile["lr"],
        max_leaf_nodes=profile["leaf"],
        l2_regularization=1.0,
        random_state=profile["seed"],
    )


def _walk_forward_validate(fx, y, train_end, profile):
    """Purged rolling out-of-sample validation using only information available before each validation window."""
    clean = fx[FEATURES].notna().all(axis=1)
    eligible = [i for i in fx.index[:train_end] if bool(clean.loc[i]) and pd.notna(y.loc[i])]
    if len(eligible) < max(profile["wf_min_train"] + profile["horizon"] + 20, profile["min_train"]):
        return {"ok": False, "accuracy": 0.0, "folds": 0, "samples": 0}

    n = len(eligible)
    fold_size = max(10, n // (WF_FOLDS + 1))
    scores = []
    total = 0
    for fold in range(WF_FOLDS):
        val_start_pos = profile["wf_min_train"] + fold * fold_size
        val_end_pos = min(n, val_start_pos + fold_size)
        if val_end_pos <= val_start_pos:
            continue

        # Purge the last HORIZON training labels so their future target window
        # cannot overlap the validation period.
        train_end_pos = val_start_pos - profile["horizon"]
        if train_end_pos < profile["wf_min_train"]:
            continue
        train_idx = eligible[:train_end_pos]
        val_idx = eligible[val_start_pos:val_end_pos]
        if len(train_idx) < profile["wf_min_train"]:
            continue

        model = _make_model(profile)
        model.fit(fx.loc[train_idx, FEATURES].astype(float), y.loc[train_idx].astype(int))
        pred = model.predict(fx.loc[val_idx, FEATURES].astype(float))
        actual = y.loc[val_idx].astype(int).to_numpy()
        scores.append(float((pred == actual).mean()))
        total += len(actual)

    if not scores:
        return {"ok": False, "accuracy": 0.0, "folds": 0, "samples": 0}
    accuracy = float(np.mean(scores))
    return {"ok": accuracy >= profile["wf_acc"], "accuracy": accuracy, "folds": len(scores), "samples": total}


def _label_profile(df, profile):
    c=pd.to_numeric(df["close"],errors="coerce")
    h=pd.to_numeric(df["high"],errors="coerce"); l=pd.to_numeric(df["low"],errors="coerce")
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean()
    future=c.shift(-profile["horizon"])-c
    threshold=atr*profile["label_atr"]
    y=pd.Series(0,index=df.index,dtype=int)
    y[future>threshold]=1; y[future<-threshold]=-1
    return y

def decide(df, htf_df, epic, existing_signal=None, strategy_id="CAPITAL_V1") -> dict[str,Any]:
    profile=_profile(strategy_id)
    result={"enabled":AI_ENABLED,"strategy_id":strategy_id,"signal":None,"raw_signal":None,
            "confidence":0.0,"buy_probability":0.0,"sell_probability":0.0,"wait_probability":1.0,
            "sl_atr":2.0,"tp_atr":3.0,"reason":"AI_UNAVAILABLE","strategy_signal":existing_signal,
            "strategy_agreement":None,"walk_forward":{"ok":False,"accuracy":0.0,"folds":0,"samples":0}}
    if not AI_ENABLED or df is None or len(df)<260:
        result["reason"]="insufficient_ai_history"; return result
    def _last_time(frame):
        if frame is None or frame.empty: return None
        for name in ("time","snapshotTimeUTC","snapshotTime","timestamp","datetime","date"):
            if name in frame.columns:
                return str(frame[name].iloc[-2 if len(frame)>=2 else -1])
        return str(len(frame))
    cache_key=(strategy_id,epic,_last_time(df),_last_time(htf_df),existing_signal)
    cached=DECISION_CACHE.get(cache_key)
    if cached is not None: return dict(cached)
    fx=_features(df,htf_df); y=_label_profile(df,profile)
    valid=fx[FEATURES].notna().all(axis=1)
    train_end=max(0,len(df)-profile["horizon"]-1)
    idx=[i for i in fx.index[:train_end] if bool(valid.loc[i])]
    if len(idx)<profile["min_train"] or y.loc[idx].nunique()<2:
        result["reason"]=f"insufficient_training_data:{len(idx)}"; return result
    wf=_walk_forward_validate(fx,y,train_end,profile)
    result["walk_forward"]=wf
    if not wf["ok"]:
        result["reason"]=f"walk_forward_rejected:accuracy={wf['accuracy']:.3f}"; return result
    model=_make_model(profile)
    model.fit(fx.loc[idx,FEATURES].astype(float),y.loc[idx].astype(int))
    latest_idx=fx.index[-2]
    latest=fx.loc[[latest_idx],FEATURES].astype(float)
    if latest.isna().any(axis=None):
        result["reason"]="latest_features_invalid"; return result
    probs=model.predict_proba(latest)[0]
    classes=list(model.classes_)
    p={int(k):float(v) for k,v in zip(classes,probs)}
    pb,ps,pw=p.get(1,0.0),p.get(-1,0.0),p.get(0,0.0)
    best=max(((pb,"BUY"),(ps,"SELL"),(pw,"WAIT")),key=lambda z:z[0])
    raw_signal=best[1] if best[0]>=profile["min_conf"] and best[1]!="WAIT" else None
    agreement=(raw_signal is not None and existing_signal in ("BUY","SELL") and raw_signal==existing_signal)
    signal=raw_signal if (not AI_REQUIRE_STRATEGY_AGREEMENT or agreement) else None
    vol=float(fx.loc[latest_idx,"vol_ratio"]) if pd.notna(fx.loc[latest_idx,"vol_ratio"]) else 1.0
    sl_atr=float(np.clip(1.5+0.9*max(0.0,min(1.5,vol-0.7)),1.5,2.85))
    rr=1.35+1.65*max(0.0,min(1.0,(best[0]-0.50)/0.50))
    tp_atr=float(np.clip(sl_atr*rr,2.0,5.5))
    result.update({"signal":signal,"raw_signal":raw_signal,"confidence":float(best[0]),
                   "buy_probability":pb,"sell_probability":ps,"wait_probability":pw,
                   "sl_atr":sl_atr,"tp_atr":tp_atr,"strategy_agreement":agreement,
                   "reason":f"AI_{strategy_id} classes={classes} train={len(idx)} wf={wf['accuracy']:.3f}"})
    DECISION_CACHE[cache_key]=dict(result)
    if len(DECISION_CACHE)>512: DECISION_CACHE.pop(next(iter(DECISION_CACHE)))
    try:
        model_dir=os.path.join(BASE_MODEL_DIR,strategy_id)
        os.makedirs(model_dir,exist_ok=True)
        with open(os.path.join(model_dir,f"{epic}_latest.json"),"w",encoding="utf-8") as f:
            json.dump(result,f,indent=2)
    except Exception:
        pass
    return result
