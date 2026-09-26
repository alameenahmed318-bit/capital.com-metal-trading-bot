import time
"""Capital.com V3 - rapid profit-take demo strategy.

V3 is isolated from V1/V2. It reuses the V2 quantitative signal engine,
but closes an owned position when broker-reported unrealized P/L reaches
the configured profit target. Grid, averaging and martingale remain disabled.
"""

import bot as base
import v2_bot as v2

V3_STRATEGY_ID = "CAPITAL_V3_RAPID_PROFIT"
V3_PROFIT_TARGET_AED = 0.20
V3_MIN_SCORE = 42.0
V3_MIN_ENTRY_STRENGTH = 0.55
V3_LATE_ENTRY_MAX_ATR = 0.75

v2.V2_MIN_SCORE = V3_MIN_SCORE
quant_signal_score_v2 = v2.quant_signal_score_v2
market_entry_strength_v2 = v2.market_entry_strength_v2

def v3_signal(df, htf_df, epic):
    result = quant_signal_score_v2(df, htf_df, epic)
    if isinstance(result, tuple):
        signal = result[0] if len(result) > 0 else None
        score = result[1] if len(result) > 1 else None
        diag = result[2] if len(result) > 2 else None
    else:
        signal, score, diag = result, None, None
    if score is not None and score >= V3_MIN_SCORE and signal in ("BUY", "SELL"):
        base.log(f"{epic}: V3 SIGNAL SCORE | score={score:.2f}")
        return signal
    if isinstance(diag, dict):
        base.log(f"{epic}: V3 SIGNAL DIAG | {diag}")
    return None

def v3_entry_strength(df, htf_df, direction):
    raw = market_entry_strength_v2(df, htf_df, direction)
    return min(1.0, raw + 0.35)

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

base.quant_signal_score = v3_signal
base.market_entry_strength = v3_entry_strength
base.manage_profit_trailing = v3_profit_manager
base.STRATEGY_ID = V3_STRATEGY_ID
base.POSITION_OWNERSHIP_FILE = "v3_strategy_positions.json"
base.STATE_FILE = "v3_trades_state.json"
base.OPEN_POSITIONS_FILE = "v3_open_positions.json"
base.SAFETY_STATE_FILE = "v3_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "v3_execution_quality.json"
base.ENTRY_REJECTION_FILE = "v3_entry_rejections.json"

base.SESSION_FILTER_ENABLED = False
base.PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS = 2
base.MAX_POSITIONS_PER_EPIC = 1
base.CORRELATION_FILTER_ENABLED = True
base.PROFIT_TRAIL_ENABLED = False
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

def run_cycle():
    base.log(
        f"STARTING {V3_STRATEGY_ID} | DEMO ONLY | QUANTITY-FOCUSED | "
        f"rapid profit target=+{V3_PROFIT_TARGET_AED:.2f} "
        f"{getattr(base.config, 'ACCOUNT_CURRENCY', 'AED')} | score>={V3_MIN_SCORE}"
    )
    api = base.CapitalAPI()
    base.log("Logging in to Capital.com...")
    api.login()
    balance = api.get_balance()
    account_currency = api.get_account_currency()
    base.log(f"Account balance: {balance} {account_currency}")
    base.reset_daily_safety(balance)
    base.update_loss_cooldowns_from_history(api)
    base.log_trade_report(api, account_currency)

    # One bounded scan per scheduled GitHub Actions invocation.
    # Never keep the account-wide concurrency lock for a long polling window.
    candle_cache = {}
    candle_cache_ttl = 8.0

    for epic in base.EPICS:
        try:
            epic_positions = api.get_open_positions()
            epic_balance = api.get_balance()
            base.process_epic(
                api=api,
                epic=epic,
                positions=epic_positions,
                balance=epic_balance,
                account_currency=account_currency,
                candle_cache=candle_cache,
                candle_cache_ttl=candle_cache_ttl,
            )
        except Exception as exc:
            base.log(f"{epic}: V3 scan error: {exc}")

    base.save_live_stats(api, account_currency)
    base.log("V3 bounded scan completed; releasing account concurrency lock.")

if __name__ == "__main__":
    run_cycle()
