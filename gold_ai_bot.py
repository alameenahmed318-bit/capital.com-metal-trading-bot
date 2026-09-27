"""Capital.com Gold AI Bot.

Dedicated AI bot for GOLD only. It uses the same integrated supervised AI
engine and hard execution/risk layer as the multi-market bot, but isolates
gold-specific state and scans only the GOLD epic.
"""
import bot as base

STRATEGY_ID = "CAPITAL_GOLD_AI"

base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "gold_ai_strategy_positions.json"
base.STATE_FILE = "gold_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "gold_ai_open_positions.json"
base.SAFETY_STATE_FILE = "gold_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "gold_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "gold_ai_entry_rejections.json"

# GOLD only. No V2/V3 dependency and no second bot's approval is required.
base.EPICS = ["GOLD"]

# Gold-specific execution profile. The AI decides direction/timing; these are
# only strategy/execution settings, while hard cost/risk/broker checks stay on.
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
    base.log(
        f"STARTING {STRATEGY_ID} | DEMO ONLY | GOLD ONLY | "
        "dedicated AI execution profile"
    )
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
