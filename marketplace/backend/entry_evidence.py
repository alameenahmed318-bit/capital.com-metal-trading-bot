"""Nonblocking, same-closed-candle quality evidence for AL-ZOOL XAUUSD.

These scores *rank* already strategy-backed trade directions. They never
authorize a trade by themselves and never add new mandatory entry gates.
"""
import math


def _num(value, default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError,OverflowError):
        return default


def clamp(value, minimum=-1.0, maximum=1.0):
    return max(minimum,min(maximum,value))


def closed_candle_pressure(rates):
    """Return -1..1; negative = selling pressure, positive = buying pressure.

    Blend confirmed candle's directional body with closing location. A doji,
    flat candle, or invalid bar adds zero directional pressure; no WAIT gate.
    """
    if not rates:return 0.0
    last=rates[-1] or {}
    high=_num(last.get("high"))
    low=_num(last.get("low"))
    opening=_num(last.get("open"))
    close=_num(last.get("close"))
    span=high-low
    if span<=0 or not low<=min(opening,close)<=max(opening,close)<=high:
        return 0.0
    body=clamp((close-opening)/span)
    close_location=clamp(2.0*(close-low)/span-1.0)
    return round(clamp(0.65*body+0.35*close_location),6)


def model_market_fit(context, side):
    """Soft model-vote reliability using observable candle-side evidence.

    The vote remains nonzero even for a side with weak quality or a false
    breakout flag, so these factors cannot act as mandatory model vetoes.
    """
    ctx=context if isinstance(context,dict) else {}
    key="buy_quality" if side=="BUY" else "sell_quality"
    trap_key="buy_trap" if side=="BUY" else "sell_trap"
    q=clamp(_num(ctx.get(key),50.0)/100.0,0.0,1.0)
    factor=0.55+0.45*q
    if ctx.get(trap_key) is True:
        factor*=0.65
    return round(clamp(factor,0.25,1.0),6)
