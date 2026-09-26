"""Time-safe conformal-style uncertainty diagnostics for trading candles.

This module never uses future candles for the current decision. It builds
rolling empirical return intervals from closed historical bars and exposes
interval width / coverage diagnostics. It is deliberately advisory until
validated on realized broker outcomes.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _closed_close(df):
    if df is None or len(df) < 40 or "close" not in df.columns:
        return pd.Series(dtype=float)
    c = pd.to_numeric(df["close"], errors="coerce")
    # The last Capital candle may still be forming. Exclude it.
    if len(c) >= 2:
        c = c.iloc[:-1]
    return c.dropna()


def _quantile_abs_return(returns: pd.Series, q: float) -> float | None:
    x = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(x) < 30:
        return None
    return float(np.quantile(np.abs(x.to_numpy(dtype=float)), q))


def prediction_interval(df, horizon: int = 4, coverage: float = 0.80, lookback: int = 160) -> dict:
    c = _closed_close(df)
    if len(c) < horizon + 35:
        return {"available": False, "reason": "insufficient_history"}
    # Historical horizon returns ending strictly before the latest closed bar.
    r = c.pct_change(horizon).dropna()
    if lookback:
        r = r.iloc[-lookback:]
    if len(r) < 30:
        return {"available": False, "reason": "insufficient_calibration"}
    alpha = float(np.clip(1.0 - coverage, 0.01, 0.50))
    lo = float(np.quantile(r.to_numpy(dtype=float), alpha / 2.0))
    hi = float(np.quantile(r.to_numpy(dtype=float), 1.0 - alpha / 2.0))
    median = float(np.median(r.to_numpy(dtype=float)))
    return {
        "available": True,
        "coverage_target": float(coverage),
        "horizon_bars": int(horizon),
        "lower_return": lo,
        "median_return": median,
        "upper_return": hi,
        "width": max(0.0, hi - lo),
        "samples": int(len(r)),
    }


def evaluate(df, atr_pct=None, horizon: int = 4, coverage: float = 0.80) -> dict:
    interval = prediction_interval(df, horizon=horizon, coverage=coverage)
    if not interval.get("available"):
        return {"interval": interval, "quality": 0.0, "wide": True, "direction": "WAIT"}
    width = float(interval["width"])
    atr = float(atr_pct) if atr_pct is not None and np.isfinite(atr_pct) else None
    # Convert ATR percentage to the same return units. A very wide interval
    # relative to ATR means uncertainty is high.
    width_to_atr = width / max(atr, 1e-9) if atr is not None and atr > 0 else None
    quality = 1.0
    if width_to_atr is not None:
        quality = float(np.clip(1.0 - max(0.0, width_to_atr - 1.5) / 3.0, 0.0, 1.0))
    direction = "BUY" if interval["median_return"] > 0 else "SELL" if interval["median_return"] < 0 else "WAIT"
    return {
        "interval": interval,
        "width_to_atr": width_to_atr,
        "quality": quality,
        "wide": bool(width_to_atr is not None and width_to_atr > 4.5),
        "direction": direction,
    }
