"""Closed-bar multi-horizon directional ensemble.

This is intentionally deterministic and time-safe: it uses only completed
bars and does not train or infer from future observations. It complements,
rather than replaces, the supervised AI engine.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


DEFAULT_HORIZONS = (2, 4, 8, 16)


def evaluate(df, horizons=DEFAULT_HORIZONS) -> dict:
    if df is None or len(df) < max(horizons) + 30 or "close" not in df.columns:
        return {"available": False, "reason": "insufficient_history"}
    c = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(c) < max(horizons) + 30:
        return {"available": False, "reason": "insufficient_history"}
    # Exclude the current/forming candle.
    c = c.iloc[:-1] if len(c) >= 2 else c
    latest = float(c.iloc[-1])
    if latest <= 0:
        return {"available": False, "reason": "invalid_price"}

    rows = []
    votes = {"BUY": 0, "SELL": 0, "WAIT": 0}
    weighted_buy = 0.0
    weighted_sell = 0.0
    total_weight = 0.0
    for h in horizons:
        if len(c) <= h:
            continue
        ret = latest / max(float(c.iloc[-1 - h]), 1e-12) - 1.0
        # Scale by recent volatility so a tiny raw move does not become a
        # falsely strong directional vote.
        vol_window = c.pct_change().iloc[-max(20, h * 4):].dropna()
        vol = float(vol_window.std(ddof=0)) if len(vol_window) >= 10 else 0.0
        z = ret / max(vol * np.sqrt(h), 1e-8)
        side = "BUY" if z > 0.20 else "SELL" if z < -0.20 else "WAIT"
        weight = float(np.sqrt(h))
        if side == "BUY":
            votes["BUY"] += 1
            weighted_buy += weight * min(1.0, abs(z))
        elif side == "SELL":
            votes["SELL"] += 1
            weighted_sell += weight * min(1.0, abs(z))
        else:
            votes["WAIT"] += 1
        total_weight += weight
        rows.append({"horizon_bars": int(h), "return": float(ret), "z": float(z), "signal": side})

    if not rows or total_weight <= 0:
        return {"available": False, "reason": "no_horizon_votes"}
    buy_score = weighted_buy / total_weight
    sell_score = weighted_sell / total_weight
    best = "BUY" if buy_score > sell_score else "SELL" if sell_score > buy_score else "WAIT"
    directional_votes = votes["BUY"] + votes["SELL"]
    agreement = max(votes["BUY"], votes["SELL"]) / max(directional_votes, 1)
    return {
        "available": True,
        "horizons": rows,
        "votes": votes,
        "buy_score": float(buy_score),
        "sell_score": float(sell_score),
        "signal": best,
        "agreement": float(agreement if directional_votes else 0.0),
        "directional_coverage": float(directional_votes / len(rows)),
    }
