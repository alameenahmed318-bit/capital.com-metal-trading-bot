"""Pre-trade transaction-cost model for Capital.com CFDs.

Capital.com states that spread is a primary trading cost and that overnight funding
and currency conversion can also affect P/L. The bot cannot know future funding
exactly from the order ticket alone, so this module models observable execution
costs (spread + measured slippage) and uses a conservative configurable budget.
"""
import json
import os
import statistics


def _load_slippage(file_path, epic):
    if not file_path or not os.path.exists(file_path):
        return 0.0
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = [x for x in (data.get("orders") or [])
                if x.get("epic") == epic and x.get("slippage_pct") is not None]
        if not rows:
            return None
        values = [max(0.0, float(x["slippage_pct"])) for x in rows[-100:]]
        return float(statistics.median(values))
    except Exception:
        return None


def evaluate_pretrade_cost(epic, market, entry_price, risk_distance,
                           execution_quality_file="execution_quality.json",
                           max_cost_to_risk=0.25,
                           extra_slippage_buffer_pct=0.01):
    """Return (allowed, diagnostics).

    Round-trip spread is approximated by the current bid/offer difference.
    Observed adverse slippage is added twice (entry + exit) plus a small buffer.
    The cost must remain below a fraction of the stop distance.
    """
    snapshot = market.get("snapshot", {}) or {}
    bid = snapshot.get("bid", market.get("bid"))
    ask = snapshot.get("offer", snapshot.get("ask", market.get("offer", market.get("ask"))))
    try:
        bid, ask, entry_price, risk_distance = map(float, (bid, ask, entry_price, risk_distance))
    except (TypeError, ValueError):
        return False, {"reason": "invalid_quote_or_risk_distance"}

    if bid <= 0 or ask <= 0 or ask < bid or risk_distance <= 0:
        return False, {"reason": "invalid_quote_or_risk_distance"}

    spread = ask - bid
    median_slippage_pct = _load_slippage(execution_quality_file, epic)
    # Missing execution history must not silently become zero slippage.
    # Use the configured buffer as the minimum observed adverse slippage
    # estimate, keeping the filter conservative until enough real fills exist.
    if median_slippage_pct is None:
        median_slippage_pct = max(0.0, float(extra_slippage_buffer_pct))
        slippage_source = "fallback_buffer"
    else:
        slippage_source = "observed_median"
    slippage_price = entry_price * ((2.0 * median_slippage_pct + extra_slippage_buffer_pct) / 100.0)
    round_trip_cost = spread + slippage_price
    ratio = round_trip_cost / risk_distance

    allowed = ratio <= max_cost_to_risk
    return allowed, {
        "spread_price": round(spread, 8),
        "median_slippage_pct": round(median_slippage_pct, 6),
        "slippage_source": slippage_source,
        "estimated_round_trip_cost": round(round_trip_cost, 8),
        "cost_to_stop_ratio": round(ratio, 4),
        "max_cost_to_risk": max_cost_to_risk,
        "allowed": allowed,
    }
