"""Realized-outcome expected-edge diagnostics.

Uses broker-reported closed-trade P/L only. It never fabricates monetary
execution costs or converts spread percentages into account-currency P/L.
This module is advisory until enough realized data exists.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

def estimate(ai_decision: dict, realized_frame=None, direction=None) -> dict:
    direction = str(direction or ai_decision.get("signal") or "").upper()
    if direction not in {"BUY", "SELL"}:
        return {"available": False, "reason": "no_direction"}

    if realized_frame is None or len(realized_frame) < 20:
        return {"available": False, "reason": "insufficient_realized_samples", "samples": 0}

    frame = realized_frame.copy()
    if "direction" not in frame.columns or "pnl" not in frame.columns:
        return {"available": False, "reason": "missing_realized_columns", "samples": 0}
    frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce")
    frame = frame.dropna(subset=["pnl"])
    frame = frame[frame["direction"].astype(str).str.upper() == direction]
    if len(frame) < 10:
        return {"available": False, "reason": "insufficient_directional_samples", "samples": int(len(frame))}

    wins = frame.loc[frame["pnl"] > 0, "pnl"]
    losses = frame.loc[frame["pnl"] < 0, "pnl"].abs()
    if len(wins) < 3 or len(losses) < 3:
        return {"available": False, "reason": "insufficient_win_loss_samples", "samples": int(len(frame))}

    p = float(np.clip(ai_decision.get("confidence", 0.0), 0.0, 1.0))
    avg_win = float(wins.mean())
    avg_loss = float(losses.mean())
    breakeven = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else None
    expected_gross_pnl = p * avg_win - (1.0 - p) * avg_loss
    historical_expectancy = float(frame["pnl"].mean())

    return {
        "available": True,
        "direction": direction,
        "samples": int(len(frame)),
        "win_rate": float((frame["pnl"] > 0).mean()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "breakeven_probability": breakeven,
        "model_probability": p,
        "probability_edge": float(p - breakeven) if breakeven is not None else None,
        "expected_gross_pnl": expected_gross_pnl,
        "historical_expectancy": historical_expectancy,
        "cost_adjusted": False,
        "note": "Gross realized-P/L edge only; execution cost is not converted to money without broker contract-value data."
    }
