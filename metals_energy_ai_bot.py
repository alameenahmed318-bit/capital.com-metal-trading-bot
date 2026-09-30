"""Capital.com Metals AI Bot — METAL IMPERIUM IRON V1 (ULTIMATE FLUID EDITION).

استراتيجية واحدة مرنة:
- M5 completed candle + EMA 9/21 + momentum.
- لا يوجد AI veto.
- لا يوجد حد اصطناعي لعدد الصفقات.
- منع Grid / Martingale / Averaging.
- عكس اتجاه التنفيذ اختياري ومفعّل هنا: SELL من الاستراتيجية -> BUY للتنفيذ،
  وBUY من الاستراتيجية -> SELL للتنفيذ.
- حماية الأرباح ومراقبة المراكز تبقى في bot.py.
"""

import pandas as pd
import bot as base
import fast_market_strategy as fast_strategy

STRATEGY_ID = "METAL_IMPERIUM_IRON_V1"
base.STRATEGY_ID = STRATEGY_ID

# نحافظ على عزل بوت المعادن عن بوت العملات.
base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.LEGACY_POSITION_OWNERSHIP_FILE = "metals_energy_ai_legacy_ownership_DISABLED.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

# قائمة المعادن/الأسواق الحالية تبقى كما هي.
base.EPICS = ["GOLD", "US100", "US500"]
base.STRATEGY_ALLOWED_EPICS = list(base.EPICS)
# Metals/energy bot must never inherit the FX discovery universe.
base.DYNAMIC_FX_UNIVERSE = False

# أكبر عدد ممكن بدون حد اصطناعي لكل سوق.
# حدود الوسيط وإدارة المخاطر تبقى فعالة.
base.MAX_POSITIONS_PER_EPIC = None

# ممنوع Grid / Martingale / Averaging.
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

# لا تجعل الفلاتر غير الضرورية تخنق الدخول.
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False
base.SIDEWAYS_FILTER_ENABLED = False
base.SPREAD_FILTER_ENABLED = False

# عكس اتجاه التنفيذ:
# استراتيجية BUY  -> أمر SELL
# استراتيجية SELL -> أمر BUY
base.REVERSE_ENTRY_DIRECTION = True
# Shared fast strategy: M5 local context + H1 confirmation.
base.generate_signal = fast_strategy.fast_signal
base.calculate_trade = fast_strategy.calculate_trade

# الاستراتيجية تعمل على شموع M5 المكتملة.
base.RESOLUTION = "MINUTE_5"
base.CANDLE_COUNT = 300
base.STRATEGY_CANDLE_RESOLUTION_SECONDS = 300

IRON_EMA_FAST = 9
IRON_EMA_SLOW = 21
IRON_SL_ATR = 1.80
MAX_ENTRY_DRIFT_ATR = 0.25
STRONG_ENTRY_DRIFT_ATR = 0.30


def is_wick_dangerous(last_candle, direction):
    """ارفض فقط الذيل الضخم جداً الذي يعاكس اتجاه الإشارة."""
    total_range = float(last_candle["high"]) - float(last_candle["low"])
    if total_range <= 0:
        return False

    upper_wick = float(last_candle["high"]) - max(
        float(last_candle["open"]), float(last_candle["close"])
    )
    lower_wick = min(
        float(last_candle["open"]), float(last_candle["close"])
    ) - float(last_candle["low"])

    if direction == "BUY" and (upper_wick / total_range) > 0.65:
        return True
    if direction == "SELL" and (lower_wick / total_range) > 0.65:
        return True
    return False


def iron_signal(df, epic, htf_df=None, live_quote=None, open_positions_list=None):
    """المحرك الوحيد للدخول في بوت المعادن."""
    if df is None or df.empty or len(df) < 30:
        return None

    # آخر شمعة M5 مكتملة فقط.
    last_candle = df.iloc[-2]

    # السعر اللحظي من WebSocket إن توفر.
    live_bid = live_quote.get("bid") if isinstance(live_quote, dict) else None
    current_price = (
        base.safe_float(live_bid)
        if live_bid is not None
        else base.safe_float(df.iloc[-1].get("close"))
    )
    if current_price is None:
        return None

    d = base.add_indicators(df.copy())
    if d is None or len(d) < 30:
        return None

    current = d.iloc[-2]
    atr = base.safe_float(current.get("atr"))
    if atr is None or atr <= 0:
        return None

    ema9 = float(
        d["close"].astype(float).ewm(span=IRON_EMA_FAST, adjust=False).mean().iloc[-2]
    )
    ema21 = float(
        d["close"].astype(float).ewm(span=IRON_EMA_SLOW, adjust=False).mean().iloc[-2]
    )

    candle_open = base.safe_float(last_candle.get("open"))
    candle_close = base.safe_float(last_candle.get("close"))
    if candle_open is None or candle_close is None:
        return None

    is_candle_green = candle_close > candle_open
    is_candle_red = candle_close < candle_open

    trend_up = ema9 > ema21 and is_candle_green
    trend_down = ema9 < ema21 and is_candle_red

    # إذا كان الاتجاه واضحاً، نعطيه هامش دخول أكبر.
    strong_trend = abs(ema9 - ema21) >= 0.10 * atr
    allowed_drift = (
        STRONG_ENTRY_DRIFT_ATR * atr
        if strong_trend
        else MAX_ENTRY_DRIFT_ATR * atr
    )

    price_drift = abs(current_price - candle_close)

    # Entry drift is now advisory, not a hard entry veto.
    # The old hard gate rejected valid trend signals whenever live price had
    # moved away from the completed candle. This caused missed opportunities
    # without improving the strategy's core direction test.
    # Keep the measurement for telemetry, but evaluate the signal from the
    # completed candle + EMA trend and execute using the current live quote.
    if price_drift > allowed_drift:
        base.log(
            f"{epic}: IRON DRIFT OVERSHOOT | "
            f"drift={price_drift:.6f} reference={allowed_drift:.6f} | "
            f"advisory_only=True"
        )

    if trend_up:
        base.log(
            f"{epic}: IRON BUY SIGNAL | EMA9={ema9:.6f} | EMA21={ema21:.6f} | "
            f"ATR={atr:.6f} | drift={price_drift:.6f}"
        )
        return "BUY"

    if trend_down:
        base.log(
            f"{epic}: IRON SELL SIGNAL | EMA9={ema9:.6f} | EMA21={ema21:.6f} | "
            f"ATR={atr:.6f} | drift={price_drift:.6f}"
        )
        return "SELL"

    return None


# process_epic() في bot.py يستدعي generate_signal(df, epic, htf_df).
# نربط المحرك الجديد بهذا المسار مع الحفاظ على نفس الواجهة.
def generate_signal(df, epic, htf_df=None):
    return iron_signal(df, epic, htf_df)


base.generate_signal = generate_signal
base.reload_runtime_state()


def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | isolated=true | "
        f"markets={base.EPICS} | reverse_entries={base.REVERSE_ENTRY_DIRECTION} | "
        f"max_positions_per_epic=None | ai_entry_veto=False | "
        f"profit_protection=True"
    )
    return base.run_cycle()


if __name__ == "__main__":
    run_cycle()
