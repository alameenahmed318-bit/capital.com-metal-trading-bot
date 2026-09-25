import pandas as pd
import numpy as np
CORRELATION_LOOKBACK=120
CORRELATION_LIMIT=0.80
MAX_CORRELATED_RISK_MULTIPLIER=1.50

def portfolio_correlation_multiplier(api,candidate_epic,existing_epics):
    universe=list(dict.fromkeys([candidate_epic]+list(existing_epics)))
    if len(universe)<2:return 1.0,{}
    returns={}
    for epic in universe:
        try:
            raw=api.get_candles(epic=epic,resolution='MINUTE_15',max_candles=CORRELATION_LOOKBACK+1)
            rows=[]
            for c in raw.get('prices',[]):
                p=c.get('closePrice',{}); b,a=p.get('bid'),p.get('ask')
                if b is not None and a is not None: rows.append((float(b)+float(a))/2)
            if len(rows)>=30: returns[epic]=pd.Series(rows).pct_change().dropna().reset_index(drop=True)
        except Exception: pass
    if len(returns)<2:return 1.0,{}
    frame=pd.concat(returns,axis=1).dropna()
    if len(frame)<25:return 1.0,{}
    corr=frame.corr(); pairs={}; crowded=0
    for other in corr.columns:
        if other==candidate_epic:continue
        v=float(corr.loc[candidate_epic,other]); pairs[f'{candidate_epic}:{other}']=round(v,3)
        if abs(v)>=CORRELATION_LIMIT: crowded+=1
    return 1.0/min(MAX_CORRELATED_RISK_MULTIPLIER,1+0.5*crowded),pairs
