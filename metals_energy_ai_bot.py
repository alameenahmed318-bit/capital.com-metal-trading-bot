"""Capital.com Metals + Energy + Digital Asset Bot using the shared 25/9 strategy.

Trades non-FX instruments from the configured universe. FX remains isolated
in fx_ai_bot.py. Shared execution, reversed entry direction, SL/TP and
progressive profit protection remain in bot.py.
"""
import bot as base

STRATEGY_ID = "CAPITAL_METALS_ENERGY_AI"
base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.LEGACY_POSITION_OWNERSHIP_FILE = "strategy_positions.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

base.EPICS = [
    "GOLD", "SILVER", "PLATINUM", "PALLADIUM",
    "US100", "US500", "BTCUSD", "OIL_CRUDE",
]
base.STRATEGY_ALLOWED_EPICS = list(base.EPICS)

# No arbitrary trade-count cap. Risk budgets and broker protections remain.
base.MAX_POSITIONS_PER_EPIC = None
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False
base.reload_runtime_state()

def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | 25SEP CLASSIC | "
        f"markets={base.EPICS} | reversed_entries={base.REVERSE_ENTRY_DIRECTION} | "
        f"progressive_profit_protection=True"
    )
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
