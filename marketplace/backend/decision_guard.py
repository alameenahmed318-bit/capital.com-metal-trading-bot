"""Fail-closed entry authorization for the central AL-ZOOL decision path.

This is a deterministic first-stage council/risk gate. It deliberately does not
pretend that remote/local LLM agents are connected; model orchestration is a
separate, hardware-aware stage.
"""
from datetime import datetime, timezone, timedelta
import math


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def evaluate_entry_gate(decision, account, symbol, received_at, now, trading_enabled):
    """Return a structured authorization record; any uncertainty means WAIT."""
    checks = []
    def reject(code, reason):
        checks.append({"check": code, "passed": False, "reason_ar": reason})
        return {
            "approved": False,
            "status": "WAIT",
            "reason_ar": reason,
            "checks": checks,
            "authorization_id": None,
        }
    def passed(code, reason):
        checks.append({"check": code, "passed": True, "reason_ar": reason})

    if not trading_enabled:
        return reject("trading_switch", "التداول الآلي متوقف من لوحة التحكم")
    if not isinstance(decision, dict) or not decision:
        return reject("decision", "لا يوجد قرار مركزي صالح")
    if not isinstance(account, dict) or not account:
        return reject("account_snapshot", "بيانات الحساب غير متاحة؛ الحماية تمنع الدخول")
    if not str(symbol or "").upper().startswith("XAUUSD"):
        return reject("symbol", "رمز التداول ليس XAUUSD المدعوم في محرك الذهب")
    if not isinstance(received_at, datetime):
        return reject("telemetry_time", "وقت استقبال بيانات MT5 غير صالح")
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    age = (now - received_at).total_seconds()
    if age < -2 or age > 30:
        return reject("telemetry_freshness", "بيانات MT5 قديمة أو توقيتها غير صحيح؛ لا دخول")
    passed("telemetry_freshness", "بيانات MT5 حديثة")

    signal = str(decision.get("decision", "WAIT")).upper()
    if signal not in ("BUY", "SELL"):
        return reject("signal", "قرار المحرك WAIT؛ لا يوجد تصريح دخول")
    passed("signal", "اتجاه القرار صالح")
    confidence = _number(decision.get("confidence"))
    if confidence < 0.45 or confidence > 1.0:
        return reject("confidence", "ثقة القرار أقل من الحد المعتمد أو غير صالحة")
    passed("confidence", "الثقة اجتازت الحد الحالي 45%")

    strategy_id = int(_number(decision.get("strategy_id")))
    if not 1 <= strategy_id <= 200:
        return reject("strategy", "معرّف الاستراتيجية غير صالح")
    bar_time = int(_number(decision.get("bar_time")))
    if bar_time <= 0 or bar_time > int(now.timestamp()) + 60:
        return reject("closed_bar", "وقت الشمعة المغلقة غير صالح")
    passed("closed_bar", "قرار مرتبط بشمعة مغلقة")

    context = decision.get("context") if isinstance(decision.get("context"), dict) else {}
    atr = _number(context.get("atr", decision.get("entry_atr")))
    reference = _number(decision.get("entry_reference_price"))
    if atr <= 0 or reference <= 0:
        return reject("quant_data", "بيانات ATR أو سعر مرجع القرار غير متاحة")
    passed("quant_data", "بيانات السعر وATR صالحة")

    family_votes = decision.get("family_votes")
    if not isinstance(family_votes, dict):
        return reject("council_families", "تصويت عائلات الاستراتيجيات غير متاح")
    buy_families = sum(1 for value in family_votes.values() if value == "BUY")
    sell_families = sum(1 for value in family_votes.values() if value == "SELL")
    support = buy_families if signal == "BUY" else sell_families
    opposition = sell_families if signal == "BUY" else buy_families
    if support < 1 or support <= opposition:
        return reject("council_families", "مجلس العائلات لا يؤيد اتجاه الدخول؛ WAIT")
    passed("council_families", f"تأييد {support} عائلة مقابل {opposition} معارضة")

    combo_summary = decision.get("combination_summary")
    if not isinstance(combo_summary, dict):
        return reject("council_combinations", "تصويت التركيبات غير متاح")
    combo_support = int(_number(combo_summary.get(signal)))
    opposite = "SELL" if signal == "BUY" else "BUY"
    combo_opposition = int(_number(combo_summary.get(opposite)))
    if combo_support <= combo_opposition or combo_support < 1:
        return reject("council_combinations", "تصويت التركيبات لا يؤيد الاتجاه؛ WAIT")
    passed("council_combinations", f"تأييد {combo_support} تركيبة مقابل {combo_opposition}")

    balance = _number(account.get("balance"))
    equity = _number(account.get("equity"))
    free_margin = _number(account.get("free_margin"))
    if balance <= 0 or equity <= 0 or free_margin <= 0:
        return reject("risk_guardian_account", "حالة الرصيد أو حقوق الملكية أو الهامش الحر غير آمنة")
    passed("risk_guardian_account", "الرصيد وحقوق الملكية والهامش الحر موجبة")

    return {
        "approved": True,
        "status": signal,
        "reason_ar": "اجتاز القرار مجلس العائلات والتركيبات وحارس سلامة الحساب",
        "checks": checks,
        "authorization_id": f"AZ-{bar_time}-{strategy_id}-{signal}",
        "issued_at": now.astimezone(timezone.utc).isoformat(),
        "expires_at": (now.astimezone(timezone.utc) + timedelta(seconds=15)).isoformat(),
        "expires_at_unix": int(now.timestamp()) + 15,
    }
