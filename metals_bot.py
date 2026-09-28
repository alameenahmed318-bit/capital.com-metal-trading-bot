"""Capital.com Metals Bot.

Trades metals only: GOLD and SILVER.
AI remains advisory/support-only; shared execution and risk management stay in bot.py.
"""
import bot as base

STRATEGY_ID = "CAPITAL_METALS"
base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "metals_strategy_positions.json"
base.STATE_FILE = "metals_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_open_positions.json"
base.SAFETY_STATE_FILE = "metals_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_entry_candle_state.json"

# Metals only. Keep FX, crypto, energy and indices out of this bot.
base.EPICS = ["GOLD", "SILVER"]

base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

# Keep the metals bot on the baseline strategy unless explicitly changed
# in this dedicated workflow.
base.config.STRATEGY = "baseline"
base.reload_runtime_state()

def run_cycle():
    base.log(f"STARTING {STRATEGY_ID} | METALS ONLY | markets={base.EPICS}")
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
