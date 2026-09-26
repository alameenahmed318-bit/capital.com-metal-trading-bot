"""Build the persisted realized-outcome AI dataset and registry after each run."""
import json, sqlite3
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import ai_outcomes

def main():
    report=ai_outcomes.build_training_dataset()
    with sqlite3.connect(ai_outcomes.DB_PATH) as con:
        df=pd.read_sql_query("SELECT * FROM trade_outcomes WHERE status='CLOSED' AND outcome_label IS NOT NULL ORDER BY entry_time",con)
    pnl=pd.to_numeric(df.get("pnl",pd.Series(dtype=float)),errors="coerce").dropna()
    wins=int((pnl>0).sum()); losses=int((pnl<0).sum())
    eq=pnl.cumsum()
    peak=eq.cummax() if len(eq) else pd.Series(dtype=float)
    dd=(eq-peak) if len(eq) else pd.Series(dtype=float)
    metrics={
      "model_version":"realized-outcome-v1",
      "training_period_start":str(df["entry_time"].iloc[0]) if len(df) else None,
      "training_period_end":str(df["entry_time"].iloc[-1]) if len(df) else None,
      "number_of_samples":int(len(df)),
      "buy_sell_wait_distribution":{
        "BUY":int((df.get("direction",pd.Series(dtype=str))=="BUY").sum()),
        "SELL":int((df.get("direction",pd.Series(dtype=str))=="SELL").sum()),
        "WAIT":0
      },
      "realized_win_rate":round(wins/len(pnl),4) if len(pnl) else None,
      "expectancy":round(float(pnl.mean()),6) if len(pnl) else None,
      "max_drawdown":round(float(abs(dd.min())),6) if len(dd) else 0.0,
      "last_training_at":datetime.now(timezone.utc).isoformat(),
      "label_source":"broker-reported-realized-pnl",
      "dataset_path":report.get("path")
    }
    ai_outcomes.update_registry("realized-outcome-v1",metrics)
    print(json.dumps({"dataset":report,"registry":metrics},indent=2,default=str))

if __name__=="__main__":
    main()
