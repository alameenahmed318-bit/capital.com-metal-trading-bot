"""Execution-cost model: spread + slippage + adverse volatility.
Advisory only. Monetary edge is intentionally not fabricated without contract data.
"""
from __future__ import annotations
import numpy as np

def estimate_cost(entry_price, bid=None, ask=None, atr=None, slippage_atr_frac=0.05):
    try:
        p=float(entry_price)
        spread=(float(ask)-float(bid)) if bid is not None and ask is not None else None
        spread_pct=(spread/p*100.0) if spread is not None and p>0 else None
        slip=float(atr)*slippage_atr_frac if atr is not None and float(atr)>0 else 0.0
        total=max(0.0,spread or 0.0)+max(0.0,slip)
        return {"spread":spread,"spread_pct":spread_pct,"slippage_estimate":slip,"total_price_cost":total}
    except Exception as exc:
        return {"spread":None,"spread_pct":None,"slippage_estimate":None,"total_price_cost":None,"error":str(exc)}

def edge_after_cost(expected_move, cost):
    if expected_move is None or cost is None or cost.get("total_price_cost") is None: return None
    return float(expected_move)-float(cost["total_price_cost"])

def pass_cost_gate(expected_move, cost, min_edge_ratio=1.25):
    edge=edge_after_cost(expected_move,cost)
    if edge is None: return False
    return bool(edge>0 and float(expected_move)>=float(cost["total_price_cost"])*min_edge_ratio)
