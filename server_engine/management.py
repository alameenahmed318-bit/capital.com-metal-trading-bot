"""Central AL-ZOOL trade-management planner.

All trading decisions are made here from dashboard settings and MT5 telemetry.
The MT5 expert advisor is an execution/telemetry adapter only.
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


def _truth(settings, key, default=False):
    value = settings.get(key, default)
    return value is True or value == 1 or value == "true"


def _owned(payload):
    symbol = str(payload.get("symbol") or "")
    result = []
    for item in payload.get("positions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("magic", "")) != ALZOOL_MAGIC:
            continue
        if str(item.get("symbol", "")) != symbol or not item.get("ticket"):
            continue
        result.append(item)
    return result


def _volume(lots, payload, cap=0.0):
    """Normalize to broker volume step; broker min/max are telemetry, not hidden limits."""
    step = _number(payload.get("volume_step"), 0.01)
    minimum = _number(payload.get("volume_min"), step)
    maximum = _number(payload.get("volume_max"), 100.0)
    if step <= 0 or minimum <= 0 or maximum <= 0:
        return 0.0
    requested = max(0.0, _number(lots))
    if cap > 0:
        requested = min(requested, cap)
    normalized = math.ceil((requested - 1e-10) / step) * step
    if normalized < minimum - 1e-9 or normalized > maximum + 1e-9:
        return 0.0
    precision = max(0, min(8, len(str(step).split(".")[-1].rstrip("0"))))
    return round(normalized, precision)


def _atr(candles):
    if not isinstance(candles, list) or len(candles) < 15:
        return 0.0
    ranges = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        hi, lo, pc = _number(c.get("high")), _number(c.get("low")), _number(prev.get("close"))
        if hi <= 0 or lo <= 0:
            return 0.0
        ranges.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(ranges[-14:]) / min(14, len(ranges))


def build_management_command(settings, live_state, *, now=None, market_decision=None):
    """Return one prioritized signed-command candidate; no broker API is called."""
    now = time.time() if now is None else float(now)
    payload = (live_state or {}).get("payload") or live_state or {}
    symbol = str(payload.get("symbol") or "")
    positions = _owned(payload)
    orders = [o for o in (payload.get("orders") or [])
              if isinstance(o, dict) and str(o.get("symbol", "")) == symbol
              and str(o.get("magic", "")) == ALZOOL_MAGIC]
    tick = payload.get("tick") or {}
    bid, ask = _number(tick.get("bid")), _number(tick.get("ask"))
    if not symbol or bid <= 0 or ask < bid:
        return {"action": "WAIT", "reason": "بيانات السعر أو الرمز غير متاحة"}

    def command(action, reason, *, ticket="", sl=0.0, tp=0.0,
                direction="", lots=0.0, price=0.0, comment="", key=""):
        seq = str(payload.get("sequence", "0"))
        # Stable per live snapshot: repeated polls cannot issue duplicate orders.
        identity = "|".join((key or seq, action, str(ticket), direction, str(comment)))
        command_id = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        if action in ("OPEN_MARKET", "PLACE_PENDING") and _truth(settings, "fixed_sl_enabled", True):
            stop_distance = _number(settings.get("fixed_sl_distance"), 3.0)
            if stop_distance > 0:
                entry_price = price if action == "PLACE_PENDING" else (ask if direction == "BUY" else bid)
                sl = (entry_price - stop_distance) if direction == "BUY" else (entry_price + stop_distance)
        return {"action": action, "command_id": command_id, "ticket": str(ticket),
                "sl": float(sl), "tp": float(tp), "direction": direction,
                "lots": float(lots), "price": float(price), "comment": comment,
                "reason": reason, "expires_at": now + 8.0}

    if not _truth(settings, "_trading_enabled"):
        return {"action": "WAIT", "reason": "التداول متوقف من لوحة التحكم"}

    floating = sum(_number(p.get("profit")) + _number(p.get("swap")) for p in positions)
    count = len(positions)

    if positions and _truth(settings, "global_loss_enabled", True):
        limit = abs(_number(settings.get("global_loss_amount"), 50.0))
        if limit > 0 and floating <= -limit:
            return command("CLOSE_ALL", "تم بلوغ حد الخسارة الإجمالية من لوحة التحكم")
    if count == 1 and _truth(settings, "per_trade_profit_enabled", True):
        target = _number(settings.get("per_trade_profit_target"), 2.0)
        if target > 0 and floating >= target:
            return command("CLOSE_ALL", "تحقق هدف ربح الصفقة")
    if count >= 2 and _truth(settings, "global_profit_enabled", True):
        target = _number(settings.get("profit_target"), 1.0)
        if target > 0 and floating >= target:
            return command("CLOSE_ALL", "تحقق هدف ربح السلة")

    # Central trailing-stop calculation; MT5 only applies the returned price.
    point = _number(payload.get("point"))
    if positions and _truth(settings, "trailing_enabled", True) and point > 0:
        start, distance = _number(settings.get("trail_start"), 3), _number(settings.get("trail_distance"), 3)
        if distance > 0:
            for p in positions:
                side = int(_number(p.get("type"), -1))
                opened, old_sl, tp = _number(p.get("open_price")), _number(p.get("sl")), _number(p.get("tp"))
                if side == 0 and bid - opened >= start:
                    candidate = bid - distance
                    if candidate > old_sl + point:
                        return command("MODIFY_SL", "وقف متحرك محسوب مركزياً", ticket=p["ticket"], sl=candidate, tp=tp)
                elif side == 1 and opened - ask >= start:
                    candidate = ask + distance
                    if old_sl <= 0 or candidate < old_sl - point:
                        return command("MODIFY_SL", "وقف متحرك محسوب مركزياً", ticket=p["ticket"], sl=candidate, tp=tp)

    buys = [p for p in positions if int(_number(p.get("type"), -1)) == 0]
    sells = [p for p in positions if int(_number(p.get("type"), -1)) == 1]
    decision = market_decision or {}
    direction = str(decision.get("decision") or "").upper()
    confidence = _number(decision.get("confidence"))
    candles = decision.get("candles") or []
    forming = payload.get("forming_candle") or {}
    atr = _atr(candles)

    # Main BUY/SELL entries are decided and sized centrally.
    if direction in ("BUY", "SELL"):
        side_allowed = _truth(settings, "allow_buy", True) if direction == "BUY" else _truth(settings, "allow_sell", True)
        spread_points = _number(tick.get("spread_points"))
        spread_limit = _number(settings.get("max_spread_points"))
        max_positions = int(_number(settings.get("max_positions"), 0))
        current_total = count + len(orders)
        opposite_exists = bool(sells if direction == "BUY" else buys)
        permitted = side_allowed and (spread_limit <= 0 or spread_points <= spread_limit)
        permitted = permitted and (not _truth(settings, "one_direction_only", True) or not opposite_exists)
        permitted = permitted and (max_positions <= 0 or current_total < max_positions)
        if permitted:
            lot = _number(settings.get("lot"), 0.01)
            if _truth(settings, "ai_risk_enabled"):
                account = payload.get("account") or {}
                risk_cash = _number(account.get("balance")) * _number(settings.get("ai_risk_percent"), 0.5) / 100.0
                stop_distance = _number(settings.get("fixed_sl_distance"), 3.0)
                tick_value = _number(payload.get("tick_value"))
                tick_size = _number(payload.get("tick_size"))
                if risk_cash > 0 and stop_distance > 0 and tick_value > 0 and tick_size > 0:
                    lot = risk_cash / ((stop_distance / tick_size) * tick_value)
                else:
                    lot = 0.0
                lot = min(lot, _number(settings.get("ai_max_lot"), 0.05))
            lots = _volume(lot, payload)
            if lots > 0:
                pending = str(settings.get("order_mode", "Market Order")) == "Pending Order"
                distance = _number(settings.get("pending_distance"), 2.0)
                entry_price = (ask + distance) if direction == "BUY" else (bid - distance)
                action = "PLACE_PENDING" if pending else "OPEN_MARKET"
                return command(action, str(decision.get("reason_ar") or "إشارة مركزية من محرك الاستراتيجيات"),
                               direction=direction, lots=lots, price=entry_price if pending else 0.0,
                               comment="AZS" + str(decision.get("strategy_id") or 0),
                               key=str(direction) + "_" + str((candles[-1] or {}).get("time", payload.get("sequence", "0"))))

    # Recovery basket target is independent of whether a new recovery entry is due.
    if _truth(settings, "recovery_enabled") and _number(settings.get("recovery_target_profit")) > 0:
        if floating >= _number(settings.get("recovery_target_profit")):
            return command("CLOSE_ALL", "تحقق هدف ربح سلة التعافي")

    # Recovery entry: server chooses direction and lot from the complete open basket.
    if _truth(settings, "recovery_enabled"):
        recovery_positions = [p for p in positions if "AZR_RECOVERY" in str(p.get("comment", ""))]
        minimum = int(_number(settings.get("recovery_min_positions"), 3))
        trigger = abs(_number(settings.get("recovery_trigger_loss"), 10))
        if count >= minimum and floating <= -trigger and not recovery_positions and direction in ("BUY", "SELL"):
            min_conf = _number(settings.get("recovery_min_confidence"), .65)
            body = _number(forming.get("close")) - _number(forming.get("open"))
            candle_agrees = body > 0 if direction == "BUY" else body < 0
            if confidence >= min_conf and candle_agrees:
                total_lots = sum(_number(p.get("volume")) for p in positions)
                mode = str(settings.get("recovery_mode", "AUTO")).upper()
                requested = (_number(settings.get("recovery_manual_lot"), .01) if mode == "MANUAL"
                             else total_lots + _number(payload.get("volume_step"), .01))
                cap = _number(settings.get("recovery_max_lot")) if mode == "MANUAL" else 0.0
                lots = _volume(requested, payload, cap)
                if lots > 0:
                    return command("OPEN_MARKET", "تعافٍ مركزي: اللوت محسوب من مجموع لوتات السلة",
                                   direction=direction, lots=lots, comment="AZR_RECOVERY",\n                                   key="recovery_" + "_".join(sorted(str(p.get("ticket")) for p in positions)))

    # Add-on and cooling use separate quotas and separate most-recent anchors.
    for kind in ("addon", "cooling"):
        if buys and sells:
            continue
        if not _truth(settings, kind + "_enabled"):
            continue
        tag = "AZS_ADDON" if kind == "addon" else "AZS_COOLING"
        tagged = [p for p in positions if tag in str(p.get("comment", ""))]
        maximum = int(_number(settings.get(kind + "_max"), 0))
        if maximum > 0 and len(tagged) >= maximum:
            continue
        side_positions = buys if buys else sells
        anchor_position = max(tagged or side_positions, key=lambda p: _number(p.get("time")))
        anchor = _number(anchor_position.get("open_price"))
        quote = bid if direction == "BUY" or buys else ask
        basket_direction = "BUY" if buys else "SELL"
        if kind == "addon":
            distance = _number(settings.get("addon_step"), 2.0)
            movement = quote - anchor if basket_direction == "BUY" else anchor - quote
            smart_ok = (not _truth(settings, "addon_smart_enabled") or
                        (confidence >= .60 and ((direction == basket_direction) and
                         ((_number(forming.get("close")) > _number(forming.get("open"))) if basket_direction == "BUY"
                          else (_number(forming.get("close")) < _number(forming.get("open")))))))
        else:
            distance = _number(settings.get("cooling_step"), 3.0)
            movement = anchor - quote if basket_direction == "BUY" else quote - anchor
            smart_ok = (not _truth(settings, "cooling_smart_enabled") or
                        (confidence >= .68 and direction != basket_direction and
                         ((_number(forming.get("close")) > _number(forming.get("open"))) if basket_direction == "BUY"
                          else (_number(forming.get("close")) < _number(forming.get("open"))))))
        if movement < distance or not smart_ok:
            continue
        multiplier_enabled = _truth(settings, kind + "_multiplier_enabled", True)
        multiplier = _number(settings.get(kind + "_multiplier"), 1.2) if multiplier_enabled else 1.0
        lot = _number(settings.get("lot"), .01) * (multiplier ** (len(tagged) + 1))
        if _truth(settings, kind + "_smart_enabled"):
            lot *= .85 + .30 * min(1.0, max(0.0, confidence))
        lots = _volume(lot, payload, _number(settings.get(kind + "_max_lot")))
        if lots > 0:
            return command("OPEN_MARKET", ("تعزيز مركزي" if kind == "addon" else "تبريد مركزي"),
                           direction=basket_direction, lots=lots, comment=tag,\n                           key=kind + "_" + str(anchor_position.get("ticket")))

    return {"action": "WAIT", "reason": "لا يوجد أمر إدارة جديد من لوحة التحكم"}
