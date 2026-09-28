"""Capital.com Metals + Energy AI Bot.

Trades precious/industrial metals, crude oil, and indices. FX is intentionally excluded.
Shared execution/risk protections remain in bot.py.
"""
import bot as base

STRATEGY_ID = "CAPITAL_METALS_ENERGY_AI"
base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

# Metals + oil + indices only. No FX.
base.EPICS = [epic for epic in list(dict.fromkeys(getattr(base.config, "EPICS", [])))
              if epic in {"GOLD", "SILVER", "US100", "US500"}]

base.MAX_POSITIONS_PER_EPIC = 3
base.MAX_BASKET_RISK = min(float(getattr(base, "MAX_BASKET_RISK", 0.02)), 0.02)
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False
base.reload_runtime_state()

def run_cycle():
    base.log(f"STARTING {STRATEGY_ID} | METALS+OIL+INDICES | markets={base.EPICS}")
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
