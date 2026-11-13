"""Independent AL-ZOOL bot profiles and signal evaluation.

This module deliberately produces analysis signals only. It does not place orders.
Execution must be wired through the authenticated MT5 client in a later, tested step.
"""
from __future__ import annotations

from copy import deepcopy
from strategy_engine import STRATEGIES, calculate_signal, analyze_market_context

BOT_PROFILES = [
    {"id":"trend","name":"بوت الاتجاه","style":"TREND","families":["EMA Trend","EMA Pullback","MACD Momentum","Adaptive Swing Trend"],"min_votes":2,"min_confidence":0.55},
    {"id":"breakout","name":"بوت الاختراق","style":"BREAKOUT","families":["Donchian Breakout","Pivot Breakout","Inside Bar Breakout","ATR Expansion"],"min_votes":2,"min_confidence":0.55},
    {"id":"scalping","name":"بوت السكالبينج","style":"SCALPING","families":["RSI Momentum","MACD Momentum","Candle Momentum","Volume Pressure"],"min_votes":2,"min_confidence":0.60},
    {"id":"candles","name":"بوت الشموع","style":"CANDLE","families":["Candle Momentum","Inside Bar Breakout","Engulfing Reversal","EMA Pullback"],"min_votes":2,"min_confidence":0.55},
    {"id":"mean_reversion","name":"بوت الارتداد","style":"MEAN_REVERSION","families":["Bollinger Reversion","Range Reversion","Stochastic Reversal","Williams %R Reversal"],"min_votes":2,"min_confidence":0.60},
]

DEFAULT_SETTINGS = {
    "enabled": False, "mode": "MANUAL", "symbol": "XAUUSD",
    "timeframe": "M5", "lot": 0.01, "max_positions": 1,
    "stop_loss_enabled": False, "stop_loss_points": 0,
    "take_profit_enabled": False, "take_profit_points": 0,
    "addon_enabled": False, "addon_mode": "MANUAL", "addon_max": 0,
    "addon_lot": 0.01, "addon_distance_points": 0,
    "cooling_enabled": False, "cooling_mode": "MANUAL", "cooling_max": 0,
    "cooling_lot": 0.01, "cooling_distance_points": 0,
}

def bot_catalog():
    return [{"id": p["id"], "name": p["name"], "style": p["style"],
             "families": list(p["families"]), "execution_status": "NOT_CONNECTED",
             "settings": deepcopy(DEFAULT_SETTINGS)} for p in BOT_PROFILES]

def evaluate_bot(bot_id: str, rates: list[dict], weights: dict | None = None) -> dict:
    """Evaluate one bot independently using only its assigned strategy families."""
    profile = next((p for p in BOT_PROFILES if p["id"] == bot_id), None)
    if profile is None:
        raise ValueError("Unknown bot id")
    if not isinstance(rates, list) or len(rates) < 65:
        return {"bot_id": bot_id, "signal": "WAIT", "confidence": 0.0,
                "votes": {"BUY": 0, "SELL": 0, "WAIT": 0},
                "reason": "بيانات 65 شمعة مغلقة على الأقل مطلوبة",
                "execution_status": "NOT_CONNECTED"}
    weights = weights or {}
    votes = {"BUY": 0, "SELL": 0, "WAIT": 0}
    details = []
    for strategy in STRATEGIES:
        if strategy["family"] not in profile["families"]:
            continue
        signal, reason = calculate_signal(rates, strategy)
        weight = max(0.0, min(5.0, float(weights.get(str(strategy["id"]), 1.0))))
        votes[signal] += weight
        details.append({"strategy_id": strategy["id"], "family": strategy["family"],
                        "signal": signal, "weight": weight, "reason": reason})
    active = votes["BUY"] + votes["SELL"]
    winner = "BUY" if votes["BUY"] > votes["SELL"] else "SELL" if votes["SELL"] > votes["BUY"] else "WAIT"
    confidence = max(votes["BUY"], votes["SELL"]) / active if active else 0.0
    required = profile["min_votes"]
    winning_count = sum(1 for d in details if d["signal"] == winner) if winner != "WAIT" else 0
    context = analyze_market_context(rates)
    if winner == "WAIT" or winning_count < required or confidence < profile["min_confidence"]:
        winner = "WAIT"
        reason = "التصويت المستقل غير كافٍ؛ لا توجد إشارة دخول"
    elif context.get("buy_trap") and winner == "BUY" or context.get("sell_trap") and winner == "SELL":
        winner = "WAIT"
        reason = "احتمال فخ سعري؛ تم تعليق الإشارة"
    else:
        reason = f"إشارة {profile['name']} بعد تصويت {winning_count} استراتيجيات"
    return {"bot_id": bot_id, "signal": winner, "confidence": round(confidence, 4),
            "votes": {k: round(v, 2) for k, v in votes.items()},
            "reason": reason, "market_regime": context["regime"],
            "details": details, "execution_status": "NOT_CONNECTED"}
