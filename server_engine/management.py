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
        if action in ("OPEN_MARKET", "PLACE_PENDING"):
            if comment.startswith("AZS_CANDLE"):
                stop_distance = (_number(settings.get("candle_chase_sl_distance"), 3.0)
                                 if _truth(settings, "candle_chase_sl_enabled") else 0.0)
            else:
                stop_distance = (_number(settings.get("fixed_sl_distance"), 3.0)
                                 if _truth(settings, "fixed_sl_enabled", True) else 0.0)
            if stop_distance > 0:
                if _truth(settings, "require_broker_stops", True):
                    broker_minimum = _number(payload.get("stops_level")) * _number(payload.get("point"))
                    stop_distance = max(stop_distance, broker_minimum)
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

    # Daily realized + floating loss is evaluated centrally using broker-day timestamps.
    day_start = _number(payload.get("day_start"))
    realized_today = 0.0
    for deal in payload.get("history") or []:
        if not isinstance(deal, dict) or _number(deal.get("time")) < day_start:
            continue
        realized_today += _number(deal.get("net_profit"),
                                  _number(deal.get("profit")) + _number(deal.get("commission")) +
                                  _number(deal.get("swap")) + _number(deal.get("fee")))
    daily_limit = abs(_number(settings.get("loss_limit"), 50.0))
    if daily_limit > 0 and realized_today + floating <= -daily_limit:
        if positions:
            return command("CLOSE_ALL", "تم بلوغ حد الخسارة اليومي من إعدادات لوحة التحكم")
        if orders:
            return command("CANCEL_PENDING", "إلغاء الأوامر المعلقة بعد بلوغ حد الخسارة اليومي",
                           ticket=orders[0].get("ticket"), key="daily_loss_cancel_" + str(orders[0].get("ticket")))
        return {"action": "WAIT", "reason": "تم بلوغ حد الخسارة اليومي؛ الدخول متوقف حتى يوم التداول التالي"}

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

    # Smart-profit protection exits a profitable basket when the central signal
    # confirms that the market direction has turned against the basket.
    if positions and _truth(settings, "smart_profit_enabled"):
        decision = market_decision or {}
        signal = str(decision.get("decision") or "").upper()
        confidence = _number(decision.get("confidence"))
        if confidence >= 0.65 and floating > 0:
            basket_buy = any(int(_number(p.get("type"), -1)) == 0 for p in positions)
            basket_sell = any(int(_number(p.get("type"), -1)) == 1 for p in positions)
            if (basket_buy and not basket_sell and signal == "SELL") or (basket_sell and not basket_buy and signal == "BUY"):
                return command("CLOSE_ALL", "حماية الربح الذكية: إشارة معاكسة مؤكدة والسلة رابحة")

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

    # AI loss guard and confirmed basket reversal are server-side decisions.
    decision = market_decision or {}
    direction = str(decision.get("decision") or "").upper()
    confidence = _number(decision.get("confidence"))
    if _truth(settings, "ai_loss_guard_enabled") and direction in ("BUY", "SELL") and confidence >= 0.72:
        for p in positions:
            pnl = _number(p.get("profit")) + _number(p.get("swap"))
            side = int(_number(p.get("type"), -1))
            opposite = (side == 0 and direction == "SELL") or (side == 1 and direction == "BUY")
            if pnl < 0 and opposite:
                return command("CLOSE_POSITION", "حارس الخسارة الذكي: تأكد الاتجاه المعاكس مركزياً",
                               ticket=p.get("ticket"))

    if _truth(settings, "reverse_protection_enabled") and positions and direction in ("BUY", "SELL") and confidence >= 0.65:
        if not (buys and sells):
            basket_direction = "BUY" if buys else "SELL"
            if direction != basket_direction:
                volume = sum(_number(p.get("volume")) for p in positions)
                weighted = sum(_number(p.get("open_price")) * _number(p.get("volume")) for p in positions)
                average = weighted / volume if volume > 0 else 0.0
                quote = bid if basket_direction == "BUY" else ask
                adverse = (average - quote) if basket_direction == "BUY" else (quote - average)
                required = _number(settings.get("reverse_trigger"), 5.0) + _number(settings.get("reverse_continue"), 2.0)
                if average > 0 and adverse >= required:
                    return command("CLOSE_ALL", "تأكيد الانعكاس: تجاوزت الحركة المعاكسة مسافة التفعيل والاستمرار")

    # Recovery basket target is independent of whether a new recovery entry is due.
    if _truth(settings, "recovery_enabled") and _number(settings.get("recovery_target_profit")) > 0:
        if floating >= _number(settings.get("recovery_target_profit")):
            return command("CLOSE_ALL", "تحقق هدف ربح سلة التعافي")

    # Recovery entry: server chooses direction and lot from the complete open basket.
    if _truth(settings, "recovery_enabled"):
        recovery_positions = [p for p in positions if "AZR_RECOVERY" in str(p.get("comment", ""))]
        minimum = int(_number(settings.get("recovery_min_positions"), 3))
        trigger = abs(_number(settings.get("recovery_trigger_loss"), 10))
        basket_start = min((_number(p.get("time")) for p in positions), default=0.0)
        recovery_limit = int(_number(settings.get("recovery_max_entries"), 0))
        recovery_history = [d for d in (payload.get("history") or [])
                            if isinstance(d, dict) and "AZR_RECOVERY" in str(d.get("comment", ""))
                            and int(_number(d.get("entry"), -1)) in (0, 2)
                            and _number(d.get("time")) >= basket_start]
        if (count >= minimum and floating <= -trigger and not recovery_positions and
                (recovery_limit <= 0 or len(recovery_history) < recovery_limit) and direction in ("BUY", "SELL")):
            min_conf = _number(settings.get("recovery_min_confidence"), .65)
            body = _number(forming.get("close")) - _number(forming.get("open"))
            candle_agrees = body > 0 if direction == "BUY" else body < 0
            atr_value = _atr(candles)
            recent = candles[-14:] if isinstance(candles, list) else []
            if direction == "BUY":
                adverse_extreme = min((_number(x.get("low")) for x in recent), default=0.0)
                rebound = bid - adverse_extreme
            else:
                adverse_extreme = max((_number(x.get("high")) for x in recent), default=0.0)
                rebound = adverse_extreme - ask
            rebound_required = atr_value * _number(settings.get("recovery_atr_distance"), 1.0)
            if confidence >= min_conf and candle_agrees and atr_value > 0 and adverse_extreme > 0 and rebound >= rebound_required:
                total_lots = sum(_number(p.get("volume")) for p in positions)
                mode = str(settings.get("recovery_mode", "AUTO")).upper()
                requested = (_number(settings.get("recovery_manual_lot"), .01) if mode == "MANUAL"
                             else total_lots + _number(payload.get("volume_step"), .01))
                cap = _number(settings.get("recovery_max_lot")) if mode == "MANUAL" else 0.0
                lots = _volume(requested, payload, cap)
                if lots > 0:
                    return command("OPEN_MARKET", "تعافٍ مركزي: اللوت محسوب من مجموع لوتات السلة",
                                   direction=direction, lots=lots, comment="AZR_RECOVERY",
                                    key="recovery_" + str((candles[-1] or {}).get("time", payload.get("sequence", "0"))) + "_" + "_".join(sorted(str(p.get("ticket")) for p in positions)))

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
                           direction=basket_direction, lots=lots, comment=tag,
                           key=kind + "_" + str(anchor_position.get("ticket")))

    # Cancel stale candle-follow orders or orders contradicted by the latest server signal.
    forming_time = _number(forming.get("time"))
    for order in orders:
        order_type = int(_number(order.get("type"), -1))
        order_comment = str(order.get("comment", ""))
        setup_time = _number(order.get("time_setup"))
        stale_candle = "AZS_CANDLE" in order_comment and forming_time > 0 and setup_time > 0 and setup_time < forming_time
        pending_direction = "BUY" if order_type in (2, 4, 6) else "SELL" if order_type in (3, 5, 7) else ""
        contradicted = direction in ("BUY", "SELL") and pending_direction and pending_direction != direction
        if stale_candle or contradicted:
            return command("CANCEL_PENDING", "إلغاء أمر معلق قديم أو مخالف لقرار السيرفر",
                           ticket=order.get("ticket"), key="cancel_" + str(order.get("ticket")))

    # Candle-follow breakout orders are generated centrally, one command at a time.
    if _truth(settings, "candle_chase_enabled"):
        candle = forming
        candle_open = _number(candle.get("open"))
        candle_high = _number(candle.get("high"))
        candle_low = _number(candle.get("low"))
        candle_close = _number(candle.get("close"))
        move = abs(candle_close - candle_open)
        if candle_open > 0 and candle_high >= candle_low > 0 and move >= _number(settings.get("candle_chase_min_move"), 0.1):
            candle_orders = [o for o in orders if "AZS_CANDLE" in str(o.get("comment", ""))]
            candle_orders += [p for p in positions if "AZS_CANDLE" in str(p.get("comment", ""))]
            chase_max = int(_number(settings.get("candle_chase_max_orders"), 1))
            if len(candle_orders) < chase_max:
                cooldown = _number(settings.get("candle_chase_cooldown"), 0.0)
                last_chase = max([_number(o.get("time_setup") or o.get("time")) for o in candle_orders] or [0.0])
                if cooldown > 0 and last_chase > 0 and now - last_chase < cooldown:
                    return {"action": "WAIT", "reason": "فترة تهدئة متابعة الشمعة لم تنته بعد"}
                candle_time = str(candle.get("time") or payload.get("sequence", "0"))
                buy_exists = any(int(_number(o.get("type"), -1)) in (0, 2, 4, 6) for o in candle_orders)
                sell_exists = any(int(_number(o.get("type"), -1)) in (1, 3, 5, 7) for o in candle_orders)
                distance = max(_number(settings.get("pending_distance"), 2.0),
                               _number(payload.get("stops_level")) * _number(payload.get("point")))
                lots = _volume(_number(settings.get("lot"), 0.01), payload)
                if lots > 0 and not buy_exists and len(candle_orders) < chase_max:
                    price = candle_high + distance
                    tp_distance = (_number(settings.get("candle_chase_single_target"), 0.5)
                                   if chase_max == 1 else _number(settings.get("candle_chase_buy_target"), 1.0))
                    tp = price + tp_distance if tp_distance > 0 else 0.0
                    return command("PLACE_PENDING", "أمر متابعة شمعة شراء محسوب مركزياً",
                                   direction="BUY", lots=lots, price=price, tp=tp,
                                   comment="AZS_CANDLE_BUY", key="candle_buy_" + candle_time)
                if lots > 0 and not sell_exists and len(candle_orders) < chase_max:
                    price = candle_low - distance
                    tp_distance = (_number(settings.get("candle_chase_single_target"), 0.5)
                                   if chase_max == 1 else _number(settings.get("candle_chase_sell_target"), 1.0))
                    tp = price - tp_distance if tp_distance > 0 else 0.0
                    return command("PLACE_PENDING", "أمر متابعة شمعة بيع محسوب مركزياً",
                                   direction="SELL", lots=lots, price=price, tp=tp,
                                   comment="AZS_CANDLE_SELL", key="candle_sell_" + candle_time)

    # Main BUY/SELL entries are decided and sized centrally.
    if direction in ("BUY", "SELL"):
        side_allowed = _truth(settings, "allow_buy", True) if direction == "BUY" else _truth(settings, "allow_sell", True)
        spread_points = _number(tick.get("spread_points"))
        spread_limit = _number(settings.get("max_spread_points"))
        max_positions = int(_number(settings.get("max_positions"), 0))
        current_total = count + len(orders)
        opposite_position = bool(sells if direction == "BUY" else buys)
        opposite_pending = any(
            int(_number(o.get("type"), -1)) in ((3, 5, 7) if direction == "BUY" else (2, 4, 6))
            for o in orders)
        opposite_exists = opposite_position or opposite_pending
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
                distance = max(_number(settings.get("pending_distance"), 2.0),
                                _number(payload.get("stops_level")) * _number(payload.get("point")))
                entry_price = (ask + distance) if direction == "BUY" else (bid - distance)
                action = "PLACE_PENDING" if pending else "OPEN_MARKET"
                return command(action, str(decision.get("reason_ar") or "إشارة مركزية من محرك الاستراتيجيات"),
                               direction=direction, lots=lots, price=entry_price if pending else 0.0,
                               comment="AZS" + str(decision.get("strategy_id") or 0),
                               key=str(direction) + "_" + str((candles[-1] or {}).get("time", payload.get("sequence", "0"))))

    return {"action": "WAIT", "reason": "لا يوجد أمر إدارة جديد من لوحة التحكم"}
