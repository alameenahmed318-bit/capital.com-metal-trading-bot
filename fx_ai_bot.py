"""Capital.com FX AI Bot.

Trades currency instruments only. Metals, oil and indices are intentionally excluded.
Shared execution/risk protections remain in bot.py.
"""
import bot as base
import fast_market_strategy as fast_strategy

STRATEGY_ID = "CAPITAL_FX_AI"
base.STRATEGY_ID = STRATEGY_ID
base.STRATEGY_ALLOWED_EPICS = []
base.POSITION_OWNERSHIP_FILE = "fx_ai_strategy_positions.json"
base.STATE_FILE = "fx_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "fx_ai_open_positions.json"
base.SAFETY_STATE_FILE = "fx_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "fx_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "fx_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "fx_ai_entry_candle_state.json"

# FX universe is discovered automatically from Capital.com after authentication.
base.EPICS = []  # Populated dynamically; JPY pairs are excluded in bot.py
base.STRATEGY_ALLOWED_EPICS = []

base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.REVERSE_ENTRY_DIRECTION = False
base.DYNAMIC_FX_UNIVERSE = True
# Shared fast strategy: fast candle/momentum/ATR + light HTF confirmation.
base.generate_signal = fast_strategy.fast_signal
base.calculate_trade = fast_strategy.calculate_trade

base.reload_runtime_state()

def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | FX ONLY | markets={base.EPICS} | "
        f"reverse_entries={base.REVERSE_ENTRY_DIRECTION}"
    )
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
