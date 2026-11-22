"""Read-only, bounded evidence that adaptive weights reached a candle analysis.

A computed backtest, a closed-trade attribution, an analyzed candle and an
authorized MT5 order are different events. Never present one as another.
This module cannot authorize orders or modify risk/trade settings.
"""
import math
from datetime import datetime, timezone
from market_symbols import market_family, same_market_symbol

AUDIT_PERIOD_SECONDS = 900


def _nonnegative_int(value):
    try:
        number = float(value)
        return max(0, int(number)) if math.isfinite(number) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _utc_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def decision_audit_event(client_id, symbol, timeframe, bar_time, diagnostics):
    """Return an idempotent 15-minute audit of a completed candle analysis.

    Proof is only that adjusted weights were passed to the strategy evaluator,
    NOT that a trade was permitted, filled, profitable or validated.
    """
    market = market_family(symbol)
    bar = _nonnegative_int(bar_time)
    if not client_id or market is None or bar == 0 or not isinstance(diagnostics, dict):
        return None
    status = str(diagnostics.get("status") or "INSUFFICIENT_EVIDENCE")
    if status not in ("APPLIED", "NEUTRAL_NO_CHANGE", "INSUFFICIENT_EVIDENCE"):
        status = "UNKNOWN"
    payload = {
        "market": market,
        "symbol": str(symbol),
        "timeframe": str(timeframe or ""),
        "bar_time": bar,
        "status": status,
        "applied_to_candle_analysis": status == "APPLIED",
        "trade_execution_proven": False,
        "validation_proven": False,
        "processed_positions": _nonnegative_int(diagnostics.get("processed_positions")),
        "closed_positions": _nonnegative_int(diagnostics.get("closed_positions")),
        "unmatched_positions": _nonnegative_int(diagnostics.get("unmatched_positions")),
        "effective_non_neutral_strategies": _nonnegative_int(diagnostics.get("applied_strategies")),
        "effective_non_neutral_combinations": _nonnegative_int(diagnostics.get("applied_combinations")),
        "historical_samples": _nonnegative_int(diagnostics.get("historical_samples")),
        "last_attributed_at": diagnostics.get("last_attributed_at"),
        "regime": str(diagnostics.get("regime") or "")[:80],
        "source": "CLOSED_CANDLE_STRATEGY_EVALUATION",
    }
    return {
        "event_id": "learning-candle:" + str(client_id) + ":" + market + ":" +
                    str(timeframe or "") + ":" + str(bar // AUDIT_PERIOD_SECONDS),
        "client_id": str(client_id),
        "event_type": "LEARNING_DECISION_AUDIT",
        "payload": payload,
    }


def live_learning_health(market_symbol, current, last_decision, profile, now=None):
    """Describe the most recent analysis; no trade authorization inference."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    current = current if isinstance(current, dict) else {}
    decision = last_decision if isinstance(last_decision, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    received_at = _utc_datetime(current.get("received_at"))
    age = max(0.0, (now - received_at).total_seconds()) if received_at else None
    terminal_fresh = bool(age is not None and age < 30 and
                          same_market_symbol(current.get("symbol"), market_symbol))
    candles = current.get("closed_candles") or []
    last_bar = _nonnegative_int(candles[0].get("time")) if candles and isinstance(candles[0], dict) else 0
    candle_age = max(0, int(now.timestamp()) - last_bar) if last_bar else None
    # A 30-second terminal heartbeat is not proof that its M1 market candles are fresh.
    # MT5 can keep sending Friday's bars when gold is closed or a feed is stalled.
    candle_fresh = bool(candle_age is not None and candle_age <= 180)
    evaluated_bar = _nonnegative_int(decision.get("bar_time"))
    decision_current = bool(terminal_fresh and candle_fresh and evaluated_bar > 0 and evaluated_bar == last_bar)
    diagnostics = decision.get("learning") if decision_current else {}
    status = str((diagnostics or {}).get("status") or "NO_CURRENT_CANDLE_ANALYSIS")
    return {
        "terminal_fresh": terminal_fresh,
        "terminal_age_seconds": round(age, 1) if age is not None else None,
        "last_closed_bar": last_bar or None,
        "last_closed_bar_age_seconds": candle_age,
        "closed_candle_fresh": candle_fresh,
        "last_evaluated_bar": evaluated_bar or None,
        "decision_current": decision_current,
        "candle_learning_status": status,
        "learned_weights_used_in_analysis": bool(decision_current and status == "APPLIED"),
        "trade_execution_proven": False,
        "closed_positions": _nonnegative_int(profile.get("closed_positions")),
        "attributed_positions": _nonnegative_int(profile.get("processed_positions")),
        "unattributed_positions": _nonnegative_int(profile.get("unmatched_positions")),
        "attribution_coverage_percent": profile.get("coverage_percent", 0),
        "last_attributed_at": profile.get("last_attributed_at"),
        "warning": ("بيانات MT5 أو شمعة القرار غير حديثة؛ لا يمكن إثبات تطبيق الأوزان الآن"
                    if not decision_current else
                    "تطبيق الأوزان على تحليل الشمعة ليس إثباتاً لتنفيذ صفقة"),
    }
