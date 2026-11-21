"""Read-only per-bot signal envelopes delivered to the AL-ZOOL central bus.

This module never sends a broker command. Event IDs are stable for a client,
bot and closed candle, so retries and restarts do not duplicate journal rows.
"""
from __future__ import annotations
from market_symbols import supported_market_symbol
import math

BOT_IDS = ("adaptive", "selective", "scalping", "breakout", "ensemble")


def build_bot_messages(client_id, symbol, timeframe, report, central_direction="WAIT"):
    """Produce five independent, serializable reports, one per bot."""
    if not isinstance(report, dict) or report.get("status") != "READY":
        return []
    client_id = str(client_id or "")[:80]
    symbol = str(symbol or "")[:40]
    timeframe = str(timeframe or "")[:20]
    if not client_id or not supported_market_symbol(symbol):
        return []
    try:
        bar = int(report["bar_time"])
    except (TypeError, ValueError, KeyError, OverflowError):
        return []
    if bar <= 0:
        return []
    bots = report.get("advisors")
    if not isinstance(bots, list) or len(bots) != 5:
        return []
    keys = [str(item.get("id", "")) if isinstance(item, dict) else "" for item in bots]
    if len(set(keys)) != 5 or set(keys) != set(BOT_IDS):
        return []
    messages = []
    for bot in bots:
        direction = str(bot.get("signal", "WAIT"))
        if direction not in ("BUY", "SELL", "WAIT"):
            return []
        try:
            confidence = float(bot.get("confidence", 0))
        except (TypeError, ValueError, OverflowError):
            return []
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            return []
        evidence = bot.get("evidence") if isinstance(bot.get("evidence"), dict) else {}
        safe_evidence = {}
        for key, value in list(evidence.items())[:18]:
            try:
                val = float(value)
                if math.isfinite(val):
                    safe_evidence[str(key)[:40]] = round(val, 6)
            except (TypeError, ValueError, OverflowError):
                continue
        messages.append({
            "event_id": f"bot:{client_id}:{symbol.upper()}:{bar}:{bot['id']}",
            "client_id": client_id,
            "event_type": "BOT_ANALYSIS_SIGNAL",
            "payload": {
                "source": "MT5_CLOSED_CANDLE",
                "bot_id": bot["id"],
                "bot_name": str(bot.get("name", ""))[:100],
                "bar_time": bar,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "confidence": confidence,
                "reason_ar": str(bot.get("reason_ar", ""))[:450],
                "evidence": safe_evidence,
                "central_direction": central_direction if central_direction in ("BUY", "SELL", "WAIT") else "WAIT",
                "can_execute": False,
            },
        })
    return messages



def market_bar_fresh(bar_time, timeframe, now_unix):
    """Whether a closed MT5 bar can truthfully be labelled a *recent* analysis.

    A live terminal heartbeat alone is insufficient (e.g. weekend/closed market).
    Unknown broker timeframes deliberately never assert LIVE.
    This affects UI labels only and is not an order authorization.
    """
    import re
    try:
        bar=int(bar_time)
        now=int(now_unix)
        tf=str(timeframe or "").upper()
        m=re.fullmatch(r"(?:PERIOD_)?(M|H)([0-9]{1,3})",tf)
        if not m: return False
        interval=int(m.group(2))*(60 if m.group(1)=="M" else 3600)
        if interval<=0 or interval>86400: return False
        age=now-bar
        return -120<=age<=max(180,interval*2+60)
    except (TypeError,ValueError,OverflowError):
        return False

def update_history(previous, messages, max_items=50):
    """In-memory bounded per-account signal journal; no other client is included."""
    history = list(previous) if isinstance(previous, list) else []
    ids = {event.get("event_id") for event in history if isinstance(event, dict)}
    for event in messages:
        if isinstance(event, dict) and event.get("event_id") not in ids:
            history.append(event)
            ids.add(event.get("event_id"))
    return history[-max(5, min(int(max_items), 100)):]
