"""Realized-trade outcome ledger and AI training dataset builder.

Records the exact feature snapshot used at entry, then reconciles closed trades
against Capital.com's broker-reported transaction P/L. This module never
reconstructs P/L when the broker does not report it explicitly.
"""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.environ.get("AI_OUTCOMES_DB", "ai_outcomes.db")
DATASET_PATH = os.environ.get("AI_DATASET_PATH", "ai_training_dataset.parquet")
REGISTRY_PATH = os.environ.get("AI_MODEL_REGISTRY", "ai_model_registry.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 deal_id TEXT UNIQUE,
 deal_reference TEXT,
 entry_time TEXT NOT NULL,
 exit_time TEXT,
 epic TEXT NOT NULL,
 direction TEXT NOT NULL,
 entry_price REAL,
 exit_price REAL,
 stop_loss REAL,
 take_profit REAL,
 size REAL,
 pnl REAL,
 pnl_r REAL,
 spread_pct REAL,
 slippage_pct REAL,
 reason TEXT,
 duration_seconds REAL,
 ai_confidence REAL,
 ai_buy_probability REAL,
 ai_sell_probability REAL,
 ai_wait_probability REAL,
 ai_signal TEXT,
 raw_signal TEXT,
 strategy_signal TEXT,
 strategy_id TEXT,
 regime TEXT,
 volatility REAL,
 outcome_label INTEGER,
 status TEXT NOT NULL DEFAULT 'OPEN',
 features_json TEXT NOT NULL,
 metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_status ON trade_outcomes(status);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_entry_time ON trade_outcomes(entry_time);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_epic ON trade_outcomes(epic);
"""

def _connect():
    con=sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con

def _now():
    return datetime.now(timezone.utc).isoformat()

def record_entry(*, deal_id, deal_reference, entry_time, epic, direction,
                 entry_price, stop_loss, take_profit, size, spread_pct,
                 slippage_pct, ai, strategy_id, regime=None, volatility=None,
                 features=None, reason=None):
    if not deal_id:
        return False
    ai=ai or {}
    row=(str(deal_id), str(deal_reference) if deal_reference else None,
         str(entry_time or _now()), str(epic), str(direction),
         _num(entry_price), _num(None), _num(stop_loss), _num(take_profit),
         _num(size), None, None, _num(spread_pct), _num(slippage_pct),
         reason, None, _num(ai.get("confidence")), _num(ai.get("buy_probability")),
         _num(ai.get("sell_probability")), _num(ai.get("wait_probability")),
         ai.get("signal"), ai.get("raw_signal"), ai.get("strategy_signal"),
         strategy_id, regime, _num(volatility), None, "OPEN",
         json.dumps(features or {}, separators=(",",":"), default=str), None)
    with _connect() as con:
        con.execute("""INSERT OR IGNORE INTO trade_outcomes
        (deal_id,deal_reference,entry_time,exit_time,epic,direction,entry_price,
         exit_price,stop_loss,take_profit,size,pnl,pnl_r,spread_pct,slippage_pct,
         reason,duration_seconds,ai_confidence,ai_buy_probability,ai_sell_probability,
         ai_wait_probability,ai_signal,raw_signal,strategy_signal,strategy_id,regime,
         volatility,outcome_label,status,features_json,metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",row)
    return True

def _num(v):
    try:
        if v is None: return None
        return float(v)
    except Exception:
        return None

def _tx_deal_id(tx):
    for k in ("dealId","dealID","positionId","dealReference"):
        v=tx.get(k)
        if v: return str(v)
    nested=tx.get("deal") if isinstance(tx.get("deal"),dict) else {}
    for k in ("dealId","dealID","positionId","dealReference"):
        v=nested.get(k)
        if v: return str(v)
    return None

def _tx_pnl(tx):
    for k in ("profitAndLoss","profitLoss","realizedProfitLoss","realisedProfitLoss","realizedPnl","realisedPnl"):
        v=_num(tx.get(k))
        if v is not None: return v
    return None

def _tx_exit_price(tx):
    for k in ("level","closeLevel","closingLevel","price","closePrice","executionPrice"):
        v=_num(tx.get(k))
        if v is not None: return v
    return None

def reconcile(api, lookback_days=7):
    """Close OPEN rows only when broker history contains explicit realized P/L."""
    try:
        from datetime import timedelta
        now=datetime.now(timezone.utc)
        start=now-timedelta(days=lookback_days)
        txs=api.get_transactions(start.strftime("%Y-%m-%dT%H:%M:%S"),
                                 now.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return {"updated":0,"unknown":0,"error":"history_unavailable"}
    updated=0
    with _connect() as con:
        rows=con.execute("SELECT deal_id,entry_time,entry_price,stop_loss,deal_reference FROM trade_outcomes WHERE status='OPEN'").fetchall()
        for deal_id,entry_time,entry_price,sl,deal_ref in rows:
            match=None
            for tx in txs:
                tid=_tx_deal_id(tx)
                note=str(tx.get("note") or tx.get("description") or tx.get("transactionType") or "").lower()
                if tid and tid==str(deal_id) and ("close" in note or "closed" in note or "close" in str(tx.get("transactionType","")).lower()):
                    match=tx; break
            if match is None:
                for tx in txs:
                    tid=_tx_deal_id(tx)
                    note=str(tx.get("note") or tx.get("description") or tx.get("transactionType") or "").lower()
                    if deal_ref and tid==str(deal_ref) and ("close" in note or "closed" in note):
                        match=tx; break
            if match is None: continue
            pnl=_tx_pnl(match)
            if pnl is None: continue
            exit_price=_tx_exit_price(match)
            entry=_num(entry_price); stop=_num(sl)
            risk=abs(entry-stop) if entry is not None and stop is not None else None
            pnl_r=(pnl / max(risk,1e-9)) if risk else None
            exit_time=match.get("date") or match.get("timestamp") or match.get("time") or _now()
            try:
                dur=(datetime.fromisoformat(str(exit_time).replace("Z","+00:00"))-
                     datetime.fromisoformat(str(entry_time).replace("Z","+00:00"))).total_seconds()
            except Exception: dur=None
            label=1 if pnl>0 else (-1 if pnl<0 else 0)
            con.execute("""UPDATE trade_outcomes SET exit_time=?,exit_price=?,pnl=?,pnl_r=?,
              outcome_label=?,status='CLOSED',duration_seconds=?,reason=?,metadata_json=?
              WHERE deal_id=? AND status='OPEN'""",
              (str(exit_time),exit_price,pnl,pnl_r,label,dur,
               str(match.get("transactionType") or match.get("note") or "BROKER_CLOSE"),
               json.dumps(match,default=str,separators=(",",":")),str(deal_id)))
            if con.total_changes: updated+=1
    return {"updated":updated,"unknown":0}

def build_training_dataset():
    """Export only CLOSED, explicitly realized outcomes; no future leakage."""
    import pandas as pd
    with _connect() as con:
        df=pd.read_sql_query("""SELECT * FROM trade_outcomes
          WHERE status='CLOSED' AND outcome_label IS NOT NULL
          ORDER BY entry_time ASC""",con)
    if df.empty: return {"rows":0,"path":DATASET_PATH}
    feature_rows=[]
    for _,r in df.iterrows():
        try: f=json.loads(r["features_json"] or "{}")
        except Exception: f={}
        f.update({
          "deal_id":r["deal_id"],"entry_time":r["entry_time"],"exit_time":r["exit_time"],
          "epic":r["epic"],"direction":r["direction"],"pnl":r["pnl"],"pnl_r":r["pnl_r"],
          "outcome_label":r["outcome_label"],"spread_pct":r["spread_pct"],
          "slippage_pct":r["slippage_pct"],"duration_seconds":r["duration_seconds"],
          "ai_confidence":r["ai_confidence"],"ai_buy_probability":r["ai_buy_probability"],
          "ai_sell_probability":r["ai_sell_probability"],"ai_wait_probability":r["ai_wait_probability"],
          "strategy_id":r["strategy_id"],"regime":r["regime"],"volatility":r["volatility"]
        })
        feature_rows.append(f)
    out=pd.DataFrame(feature_rows)
    try: out.to_parquet(DATASET_PATH,index=False)
    except Exception:
        # Keep the canonical parquet path when parquet support exists; otherwise
        # emit a JSON fallback without silently pretending it is parquet.
        fallback=DATASET_PATH+".json"
        out.to_json(fallback,orient="records",date_format="iso")
        return {"rows":len(out),"path":fallback,"parquet_error":True}
    return {"rows":len(out),"path":DATASET_PATH}

def realized_training_frame():
    import pandas as pd
    with _connect() as con:
        rows=con.execute("""SELECT features_json,outcome_label,entry_time,epic,direction
          FROM trade_outcomes WHERE status='CLOSED' AND outcome_label IN (-1,1)
          ORDER BY entry_time ASC""").fetchall()
    if not rows: return pd.DataFrame()
    records=[]
    for f,label,t,epic,direction in rows:
        try: x=json.loads(f or "{}")
        except Exception: continue
        x["outcome_label"]=int(label); x["entry_time"]=t; x["epic"]=epic; x["direction"]=direction
        records.append(x)
    return pd.DataFrame(records)

def update_registry(model_version, metrics):
    payload={"updated_at":_now(),"active_model":model_version,"models":{}}
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH,encoding="utf-8") as f: payload=json.load(f)
        except Exception: pass
    payload.setdefault("models",{})[model_version]=metrics
    with open(REGISTRY_PATH,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,default=str)
    return payload
