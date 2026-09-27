"""Capital.com Metals + Energy AI Bot.

Trades precious/industrial metals and crude oil only. FX and indices are intentionally excluded.
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

# Metals + oil only. No FX and no indices.
base.EPICS = [epic for epic in list(dict.fromkeys(getattr(base.config, "EPICS", [])))
              if epic in {"GOLD", "SILVER", "OIL_CRUDE"}]

base.MIN_ENTRY_SCORE = 50.0
base.MIN_ENTRY_STRENGTH = 0.55
base.MAX_POSITIONS_PER_EPIC = 3
base.MAX_BASKET_RISK = min(float(getattr(base, "MAX_BASKET_RISK", 0.04)), 0.04)
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False

def run_cycle():
    base.log(f"STARTING {STRATEGY_ID} | METALS+OIL ONLY | markets={base.EPICS}")
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
