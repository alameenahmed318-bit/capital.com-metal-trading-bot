"""Capital.com FX AI Bot.

Trades currency instruments only. Metals, oil and indices are intentionally excluded.
Shared execution/risk protections remain in bot.py.
"""
import bot as base

STRATEGY_ID = "CAPITAL_FX_AI"
base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "fx_ai_strategy_positions.json"
base.STATE_FILE = "fx_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "fx_ai_open_positions.json"
base.SAFETY_STATE_FILE = "fx_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "fx_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "fx_ai_entry_rejections.json"

# Currency instruments only. Do not silently include metals, energy or indices.
base.EPICS = [epic for epic in list(dict.fromkeys(getattr(base.config, "EPICS", [])))
              if epic not in {"GOLD", "SILVER", "OIL_CRUDE", "US100", "US500"}]

base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False
base.reload_runtime_state()

def run_cycle():
    base.log(f"STARTING {STRATEGY_ID} | FX ONLY | markets={base.EPICS}")
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
