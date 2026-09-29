"""Capital.com FX AI Bot.

Trades currency instruments only. Metals, oil and indices are intentionally excluded.
Shared execution/risk protections remain in bot.py.
"""
import bot as base

STRATEGY_ID = "CAPITAL_FX_AI"
base.STRATEGY_ID = STRATEGY_ID
base.STRATEGY_ALLOWED_EPICS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURCHF"]
base.POSITION_OWNERSHIP_FILE = "fx_ai_strategy_positions.json"
base.STATE_FILE = "fx_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "fx_ai_open_positions.json"
base.SAFETY_STATE_FILE = "fx_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "fx_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "fx_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "fx_ai_entry_candle_state.json"

# Explicit allowlist: no other currency pairs can be traded.
base.EPICS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURCHF"]
assert not set(base.EPICS) & set(base.config.DISABLED_FX_EPICS), "Excluded FX pair in trading allowlist"

base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.reload_runtime_state()

def run_cycle():
    base.log(f"STARTING {STRATEGY_ID} | FX ONLY | markets={base.EPICS}")
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
