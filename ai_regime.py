"""Market-regime classifier for the AI layer.
Advisory only: it never submits or modifies orders.
Regimes: TREND_UP, TREND_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY, TRANSITION.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

REGIMES=("TREND_UP","TREND_DOWN","RANGE","HIGH_VOLATILITY","LOW_VOLATILITY","TRANSITION")

def detect_regime(df, lookback=100):
    if df is None or len(df)<max(60,lookback):
        return {"regime":"TRANSITION","confidence":0.0,"reason":"insufficient_history","atr_pct":None,"atr":None,"trend_strength":0.0}
    x=df.iloc[:-1].copy() if len(df)>1 else df.copy()
    c=pd.to_numeric(x["close"],errors="coerce")
    h=pd.to_numeric(x["high"],errors="coerce")
    l=pd.to_numeric(x["low"],errors="coerce")
    e20=c.ewm(span=20,adjust=False).mean()
    e50=c.ewm(span=50,adjust=False).mean()
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean()
    atr_pct=(atr/c).replace([np.inf,-np.inf],np.nan)
    vol_fast=atr.rolling(20).mean()
    vol_slow=atr.rolling(80).mean()
    i=-1
    vals=[c.iloc[i],e20.iloc[i],e50.iloc[i],atr.iloc[i],atr_pct.iloc[i],vol_fast.iloc[i],vol_slow.iloc[i]]
    if any(pd.isna(v) for v in vals):
        return {"regime":"TRANSITION","confidence":0.0,"reason":"invalid_indicators","atr_pct":None,"trend_strength":0.0}
    trend_strength=abs(float(e20.iloc[i]-e50.iloc[i]))/max(float(atr.iloc[i]),1e-9)
    slope=(float(c.iloc[i])-float(c.iloc[max(0,len(c)-9)]))/max(float(atr.iloc[i])*8,1e-9)
    vol_ratio=float(vol_fast.iloc[i])/max(float(vol_slow.iloc[i]),1e-9)
    if vol_ratio>=1.8:
        regime="HIGH_VOLATILITY"
        conf=min(1.0,0.55+0.25*min(vol_ratio-1.8,1.8))
    elif vol_ratio<=0.65:
        regime="LOW_VOLATILITY"
        conf=min(1.0,0.55+0.25*min(0.65-vol_ratio,0.5))
    elif trend_strength>=0.65 and slope>0.15:
        regime="TREND_UP"; conf=min(1.0,0.55+0.18*min(trend_strength,2.0)+0.12*min(slope,1.5))
    elif trend_strength>=0.65 and slope<-0.15:
        regime="TREND_DOWN"; conf=min(1.0,0.55+0.18*min(trend_strength,2.0)+0.12*min(abs(slope),1.5))
    elif trend_strength<0.35:
        regime="RANGE"; conf=min(1.0,0.55+0.35*(1.0-trend_strength/0.35))
    else:
        regime="TRANSITION"; conf=0.5
    return {"regime":regime,"confidence":float(conf),"atr_pct":float(atr_pct.iloc[i]),"atr":float(atr.iloc[i]),"trend_strength":float(trend_strength),"vol_ratio":float(vol_ratio),"slope":float(slope)}

def direction_allowed(regime, direction):
    if direction not in ("BUY","SELL"): return False
    if regime=="TREND_UP": return direction=="BUY"
    if regime=="TREND_DOWN": return direction=="SELL"
    if regime=="HIGH_VOLATILITY": return True
    if regime in ("RANGE","LOW_VOLATILITY","TRANSITION"): return True
    return False
