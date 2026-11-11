"""Server-side position management planner.

The planner is deliberately pure: it converts dashboard settings and the latest
MT5 snapshot into one broker action. It never connects to a broker or executes
orders. MT5 remains the execution adapter.
"""
from __future__ import annotations

import math
import time
import uuid


ALZOOL_MAGIC = "26110801"


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def build_management_command(settings, live_state, *, now=None):
    """Return one prioritized, deterministic management instruction or WAIT.

    Only AL-ZOOL positions on the chart symbol are managed. Monetary targets
    use the account currency as reported by MT5.
    """
    now = time.time() if now is None else float(now)
    payload = (live_state or {}).get("payload") or live_state or {}
    account = payload.get("account") or {}
    tick = payload.get("tick") or {}
    symbol = str(payload.get("symbol") or "")
    positions = payload.get("positions") or []
    if not symbol or not isinstance(positions, list):
        return {"action": "WAIT", "reason": "بيانات الحساب أو الصفقات غير متاحة"}

    owned = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        if str(position.get("magic", "")) != ALZOOL_MAGIC:
            continue
        if str(position.get("symbol", "")) != symbol:
            continue
        ticket = str(position.get("ticket", "")).strip()
        if not ticket:
            continue
        owned.append(position)

    if not owned:
        return {"action": "WAIT", "reason": "لا توجد صفقات AL-ZOOL مفتوحة"}

    # Respect the dashboard master switch: pausing trading does not alter
    # existing positions, their SL/TP, or pending orders.
    if not bool(settings.get("_trading_enabled", False)):
        return {"action": "WAIT", "reason": "التداول متوقف من لوحة التحكم"}

    floating = sum(_number(p.get("profit")) + _number(p.get("swap")) for p in owned)
    count = len(owned)

    def command(action, reason, ticket="", sl=0.0, tp=0.0):
        return {
            "action": action,
            "command_id": uuid.uuid4().hex,
            "ticket": str(ticket),
            "sl": float(sl),
            "tp": float(tp),
            "reason": reason,
            "expires_at": now + 8.0,
        }

    # Highest priority: configured account basket loss protection.
    if bool(settings.get("global_loss_enabled", True)):
        limit = abs(_number(settings.get("global_loss_amount"), 50.0))
        if limit > 0 and floating <= -limit:
            return command("CLOSE_ALL", "تم بلوغ حد الخسارة الإجمالية المحدد في لوحة التحكم")

    # Then configured per-position and basket profit exits.
    if count == 1 and bool(settings.get("per_trade_profit_enabled", True)):
        target = _number(settings.get("per_trade_profit_target"), 2.0)
        if target > 0 and floating >= target:
            return command("CLOSE_ALL", "تحقق هدف ربح الصفقة المحدد في لوحة التحكم")
    if count >= 2 and bool(settings.get("global_profit_enabled", True)):
        target = _number(settings.get("profit_target"), 1.0)
        if target > 0 and floating >= target:
            return command("CLOSE_ALL", "تحقق هدف ربح السلة المحدد في لوحة التحكم")

    # Trailing-stop decisions are made centrally. The EA only applies the SL.
    if bool(settings.get("trailing_enabled", True)):
        bid = _number(tick.get("bid"))
        ask = _number(tick.get("ask"))
        point = _number(payload.get("point"))
        if point <= 0:
            point = _number(payload.get("point_size"))
        # MT5 telemetry sends prices but not point size in older clients.
        # Do not guess digits/point size: WAIT until telemetry provides it.
        if bid > 0 and ask >= bid and point > 0:
            start = _number(settings.get("trail_start"), 3.0)
            distance = _number(settings.get("trail_distance"), 3.0)
            if distance > 0:
                for position in owned:
                    side = int(_number(position.get("type"), -1))
                    opened = _number(position.get("open_price"))
                    old_sl = _number(position.get("sl"))
                    tp = _number(position.get("tp"))
                    ticket = str(position.get("ticket"))
                    if side == 0 and bid - opened >= start:
                        candidate = bid - distance
                        if candidate > old_sl + point:
                            return command("MODIFY_SL", "تحديث وقف الخسارة المتحرك من المحرك المركزي",
                                           ticket=ticket, sl=candidate, tp=tp)
                    elif side == 1 and opened - ask >= start:
                        candidate = ask + distance
                        if old_sl <= 0 or candidate < old_sl - point:
                            return command("MODIFY_SL", "تحديث وقف الخسارة المتحرك من المحرك المركزي",
                                           ticket=ticket, sl=candidate, tp=tp)

    return {"action": "WAIT", "reason": "لا يوجد أمر إدارة جديد"}
