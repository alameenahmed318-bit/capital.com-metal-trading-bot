"""Capital.com Metals & Energy AI Bot.

Uses the shared fast market strategy on completed M5 candles with H1
confirmation. AI remains advisory only; execution direction is normal.
The bot is isolated from the FX bot and trades only its configured markets.
"""

import bot as base
import fast_market_strategy as fast_strategy

STRATEGY_ID = "METAL_IMPERIUM_IRON_V1"
base.STRATEGY_ID = STRATEGY_ID

# Strict ownership/state isolation from the FX bot.
base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.LEGACY_POSITION_OWNERSHIP_FILE = "metals_energy_ai_legacy_ownership_DISABLED.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

# Metals / energy / indices universe only.
base.EPICS = ["GOLD", "SILVER", "OIL_CRUDE", "US100", "US500"]
base.STRATEGY_ALLOWED_EPICS = list(base.EPICS)
base.DYNAMIC_FX_UNIVERSE = False

# No artificial per-market cap; broker/risk controls remain active.
base.MAX_POSITIONS_PER_EPIC = None

# No Grid / Martingale / Averaging.
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

# Keep non-essential entry filters from blocking valid opportunities.
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False
base.SIDEWAYS_FILTER_ENABLED = False
base.SPREAD_FILTER_ENABLED = False

# Normal execution direction:
# strategy BUY -> broker BUY
# strategy SELL -> broker SELL
base.REVERSE_ENTRY_DIRECTION = False

# One shared strategy engine; no legacy iron_signal wrapper.
base.generate_signal = fast_strategy.fast_signal
base.calculate_trade = fast_strategy.calculate_trade

# M5 completed-candle strategy with H1 confirmation.
base.RESOLUTION = "MINUTE_5"
base.CANDLE_COUNT = 300
base.STRATEGY_CANDLE_RESOLUTION_SECONDS = 300

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
