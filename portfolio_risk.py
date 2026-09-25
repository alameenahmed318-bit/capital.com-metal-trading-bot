"""Portfolio-level covariance/risk-budget overlay.

This module does not predict direction. It estimates how a candidate trade changes
the portfolio's realized volatility and correlation concentration, then scales the
candidate risk budget down when the portfolio is already crowded.
"""
import math
import numpy as np
import pandas as pd

DEFAULT_LOOKBACK = 192
DEFAULT_TARGET_VOL = 0.10
DEFAULT_MIN_MULTIPLIER = 0.35
DEFAULT_MAX_MULTIPLIER = 1.00
PERIODS_PER_YEAR = 96 * 252  # 15-minute bars, 24h clock


def _mid_series(raw):
    rows = []
    for c in raw.get("prices", []):
        p = c.get("closePrice", {}) or {}
        bid, ask = p.get("bid"), p.get("ask")
        try:
            if bid is not None and ask is not None:
                rows.append((float(bid) + float(ask)) / 2.0)
        except (TypeError, ValueError):
            continue
    return pd.Series(rows, dtype=float)


def portfolio_risk_overlay(api, candidate_epic, existing_positions, candidate_risk_amount,
                           target_vol=DEFAULT_TARGET_VOL, lookback=DEFAULT_LOOKBACK,
                           min_multiplier=DEFAULT_MIN_MULTIPLIER,
                           max_multiplier=DEFAULT_MAX_MULTIPLIER,
                           candle_cache=None, candle_cache_ttl=8.0):
    """Return (multiplier, diagnostics).

    Risk-budget weights are based on stop-defined account risk, then combined with
    the realized covariance of 15m returns. This is intentionally one-sided:
    the overlay can reduce a trade's risk but never lever it above the requested
    budget. Missing/short histories fail open to 1.0 because the existing spread,
    stop, basket and portfolio caps remain active.
    """
    candidate_risk_amount = float(candidate_risk_amount or 0.0)
    if candidate_risk_amount <= 0:
        return 1.0, {"reason": "no_candidate_risk"}

    epics = []
    risk_by_epic = {}
    for p in existing_positions or []:
        epic = p.get("epic") or (p.get("instrument") or {}).get("epic")
        if not epic:
            continue
        epics.append(epic)
        risk = p.get("risk_amount_account")
        try:
            risk = float(risk) if risk is not None else 0.0
        except (TypeError, ValueError):
            risk = 0.0
        risk_by_epic[epic] = risk_by_epic.get(epic, 0.0) + max(0.0, risk)

    risk_by_epic[candidate_epic] = risk_by_epic.get(candidate_epic, 0.0) + candidate_risk_amount
    epics = list(dict.fromkeys(epics + [candidate_epic]))
    if len(epics) == 1:
        return 1.0, {"reason": "single_asset"}

    returns = {}
    annual_vol = {}
    for epic in epics:
        try:
            cached_df = None
            if candle_cache is not None:
                now = __import__("time").monotonic()
                key = (epic, "MINUTE_15", int(lookback + 1))
                item = candle_cache.get(key)
                if item is not None and now - item[0] < max(0.0, float(candle_cache_ttl)):
                    cached_df = item[1]
            if cached_df is not None and not cached_df.empty and "close" in cached_df.columns:
                s = cached_df["close"].astype(float)
            else:
                raw = api.get_candles(epic=epic, resolution="MINUTE_15", max_candles=lookback + 1)
                s = _mid_series(raw)
            if len(s) < max(60, lookback // 2):
                continue
            r = np.log(s).diff().dropna()
            if len(r) < 50:
                continue
            returns[epic] = r.reset_index(drop=True)
            annual_vol[epic] = float(r.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR))
        except Exception:
            continue

    if len(returns) < 2:
        return 1.0, {"reason": "insufficient_history", "assets": list(returns)}

    frame = pd.concat(returns, axis=1, keys=list(returns)).dropna()
    if len(frame) < 40:
        return 1.0, {"reason": "insufficient_overlap", "bars": len(frame)}

    cov = frame.cov(ddof=1).to_numpy(dtype=float) * PERIODS_PER_YEAR
    cols = list(frame.columns)

    weights_raw = np.array([max(0.0, risk_by_epic.get(e, 0.0)) for e in cols], dtype=float)
    if weights_raw.sum() <= 0:
        return 1.0, {"reason": "no_risk_weights"}
    weights = weights_raw / weights_raw.sum()

    cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
    cov = (cov + cov.T) / 2.0
    port_var = float(weights @ cov @ weights)
    realized_port_vol = math.sqrt(max(port_var, 0.0))

    # Marginal contribution of the candidate asset to the covariance portfolio.
    candidate_idx = cols.index(candidate_epic)
    marginal = float((cov @ weights)[candidate_idx])
    candidate_rc = float(weights[candidate_idx] * marginal / max(realized_port_vol, 1e-12))

    vol_mult = min(1.0, target_vol / max(realized_port_vol, target_vol))
    # Additional concentration penalty when the candidate is responsible for a
    # large share of portfolio risk.
    rc_share = abs(candidate_rc) / max(realized_port_vol, 1e-12)
    concentration_mult = 1.0 if rc_share <= 0.35 else max(0.50, 0.35 / rc_share)

    multiplier = max(min_multiplier, min(max_multiplier, vol_mult * concentration_mult))
    corr_with_candidate = {}
    corr = frame.corr()
    for e in cols:
        if e != candidate_epic:
            try:
                corr_with_candidate[e] = round(float(corr.loc[candidate_epic, e]), 3)
            except Exception:
                pass

    return multiplier, {
        "realized_portfolio_vol": round(realized_port_vol, 4),
        "target_vol": target_vol,
        "candidate_risk_contribution_share": round(rc_share, 4),
        "vol_multiplier": round(vol_mult, 4),
        "concentration_multiplier": round(concentration_mult, 4),
        "multiplier": round(multiplier, 4),
        "annualized_asset_vol": {k: round(v, 4) for k, v in annual_vol.items()},
        "candidate_correlations": corr_with_candidate,
        "assets": cols,
        "bars": len(frame),
    }
