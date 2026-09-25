"""Capital.com V3 - rapid profit-take demo strategy.

V3 is isolated from V1/V2. It reuses the V2 quantitative signal engine,
but closes an owned position when broker-reported unrealized P/L reaches
the configured profit target. Grid, averaging and martingale remain disabled.
"""

import bot as base
from v2_bot import quant_signal_score_v2, market_entry_strength_v2

V3_STRATEGY_ID = "CAPITAL_V3_RAPID_PROFIT"
V3_PROFIT_TARGET_AED = 0.20
V3_MIN_SCORE = 45.0

def v3_signal(df, htf_df, epic):
    signal, score, diag = quant_signal_score_v2(df, htf_df, epic)
    if score is not None and score >= V3_MIN_SCORE:
        if isinstance(diag, dict):
            diag = dict(diag)
            diag["v3_min_score"] = V3_MIN_SCORE
        return signal, score, diag
    return None, score, diag

def v3_entry_strength(df, htf_df, direction):
    raw = market_entry_strength_v2(df, htf_df, direction)
    return min(1.0, raw + 0.25)

def v3_profit_manager(api, positions, epic, account_currency):
    """Close V3 positions as soon as broker-reported P/L reaches +0.20."""
    for position in base.get_positions_for_epic(positions, epic):
        deal_id = base.position_deal_id(position)
        if not deal_id:
            continue
        pnl = base.position_unrealized_pnl(position)
        if pnl is None:
            continue
        if pnl >= V3_PROFIT_TARGET_AED:
            try:
                response = api.close_position(deal_id)
                base.log(
                    f"{epic}: V3 PROFIT TARGET HIT | deal={deal_id} | "
                    f"pnl={pnl:.2f} {account_currency} | target={V3_PROFIT_TARGET_AED:.2f} | "
                    f"CLOSE RESPONSE={response}"
                )
            except Exception as exc:
                base.log(f"{epic}: V3 profit-target close failed | deal={deal_id} | {exc}")

# Scope all V3 behavior to this process only.
base.quant_signal_score = v3_signal
base.market_entry_strength = v3_entry_strength
base.manage_profit_trailing = v3_profit_manager
base.STRATEGY_ID = V3_STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "strategy_positions.json"
base.STATE_FILE = "v3_trades_state.json"
base.OPEN_POSITIONS_FILE = "v3_open_positions.json"
base.SAFETY_STATE_FILE = "v3_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "v3_execution_quality.json"
base.ENTRY_REJECTION_FILE = "v3_entry_rejections.json"

# Rapid-entry mode, while retaining broker SL, spread, cost, portfolio and
# basket-risk controls from bot.py.
base.SESSION_FILTER_ENABLED = False
base.PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS = 20
base.PROFIT_TRAIL_ENABLED = False
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

def run_cycle():
    base.log(
        f"STARTING {V3_STRATEGY_ID} | DEMO ONLY | "
        f"rapid profit target=+{V3_PROFIT_TARGET_AED:.2f} {getattr(base.config, 'ACCOUNT_CURRENCY', 'AED')}"
    )
    return base.run_cycle()

if __name__ == "__main__":
    run_cycle()
