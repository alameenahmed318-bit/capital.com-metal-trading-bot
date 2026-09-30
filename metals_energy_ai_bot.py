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
base.EPICS = ["GOLD", "SILVER", "OIL_CRUDE", "US100", "US500"]
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


# بوت المعادن يستخدم نفس الاستراتيجية المشتركة السريعة مثل بوت العملات.
# الفرق الوحيد: المعادن/الطاقة تعمل على M5 محلياً مع تأكيد H1.
base.RESOLUTION = "MINUTE_5"
base.CANDLE_COUNT = 300
base.STRATEGY_CANDLE_RESOLUTION_SECONDS = 300

base.generate_signal = fast_strategy.fast_signal
base.calculate_trade = fast_strategy.calculate_trade

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
