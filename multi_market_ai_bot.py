"""Capital.com Multi-Market AI Bot.

Single integrated AI bot for every enabled market. Shared execution/risk
protections live in bot.py; this module only selects the multi-market
strategy identity and owns the legacy positions during the migration.
"""
import bot as base

STRATEGY_ID = "CAPITAL_MULTI_MARKET_AI"

base.STRATEGY_ID = STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "multi_market_strategy_positions.json"
base.STATE_FILE = "multi_market_trades_state.json"
base.OPEN_POSITIONS_FILE = "multi_market_open_positions.json"
base.SAFETY_STATE_FILE = "multi_market_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "multi_market_execution_quality.json"
base.ENTRY_REJECTION_FILE = "multi_market_entry_rejections.json"

# All configured markets. No V2/V3 consensus layer and no cross-bot approval.
base.EPICS = list(dict.fromkeys(getattr(base.config, "EPICS", [
    "GOLD", "EURUSD", "SILVER", "OIL_CRUDE", "US100", "US500", "EURUSD_W"
])))

# One integrated AI decision per market. Hard broker/risk/execution controls
# in bot.py remain mandatory; strategy votes are not chained together.
base._save_owned_deals(set([
    "00000000-6603-f203-0493-972c0004511e","00000000-6603-f206-0493-972c0004511e",
    "00000000-6604-35e9-0493-972c0004511e","00000000-6604-35ec-0493-972c0004511e",
    "00000000-6604-a6b5-0493-972c0004511e","00000000-6604-a6b8-0493-972c0004511e",
    "00000000-6605-cbdd-0493-972c0004511e","00000000-6605-cbe0-0493-972c0004511e",
    "00000000-6604-fc0e-0493-972c0004511e","00000000-6604-fc11-0493-972c0004511e",
    "00000000-6605-34db-0493-972c0004511e","00000000-6605-34de-0493-972c0004511e",
    "00000000-6605-4405-0493-972c0004511e","00000000-6605-4408-0493-972c0004511e",
    "00000000-6605-817d-0493-972c0004511e","00000000-6605-8180-0493-972c0004511e",
    "00000000-6605-a3f4-0493-972c0004511e","00000000-6605-a3f7-0493-972c0004511e",
    "00000000-6605-dc76-0493-972c0004511e","00000000-6605-dc79-0493-972c0004511e"
]))

base.MIN_ENTRY_SCORE = 50.0
base.MIN_ENTRY_STRENGTH = 0.55
base.MAX_POSITIONS_PER_EPIC = 5
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False

def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | DEMO ONLY | ALL MARKETS | "
        f"AI integrated | markets={len(base.EPICS)}"
    )
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
