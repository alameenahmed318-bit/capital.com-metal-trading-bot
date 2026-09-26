"""Capital.com V4 - Smart Opportunity Engine (DEMO ONLY).

V4 is fully isolated from V1/V2/V3:
- Own strategy ID and state/ownership files.
- Does not modify shared strategy files.
- Adaptive market-regime entry filter on top of the V2 quantitative signal.
- Fast 2-second scan, but stricter opportunity confirmation than V3.
- Grid, martingale and averaging disabled.
"""

import time
import bot as base
import v2_bot as v2

V4_STRATEGY_ID = "CAPITAL_V4_SMART_OPPORTUNITY"
V4_MIN_SCORE = 55.0
V4_MIN_ENTRY_STRENGTH = 0.82
V4_PROFIT_TARGET = 0.25

# Local aliases; V4 changes only this Python process, never the V1/V2/V3 files.
v2.V2_MIN_SCORE = V4_MIN_SCORE
quant_signal_score_v2 = v2.quant_signal_score_v2
market_entry_strength_v2 = v2.market_entry_strength_v2


def _regime_ok(df, direction):
    """Require directional structure and avoid flat/choppy entries."""
    try:
        close = df["close"].astype(float)
        ema_fast = close.ewm(span=9, adjust=False).mean()
        ema_slow = close.ewm(span=21, adjust=False).mean()
        atr = (df["high"].astype(float) - df["low"].astype(float)).rolling(14).mean()

        if len(close) < 30 or atr.iloc[-2] <= 0:
            return False, "insufficient-regime-data"

        gap = abs(float(ema_fast.iloc[-2] - ema_slow.iloc[-2]))
        atr_value = float(atr.iloc[-2])
        gap_ratio = gap / atr_value

        recent = close.iloc[-2]
        prior = close.iloc[-7]
        momentum = (float(recent) - float(prior)) / atr_value

        if gap_ratio < 0.12:
            return False, f"flat-gap={gap_ratio:.2f}"

        if direction == "BUY" and momentum < 0.05:
            return False, f"weak-up-momentum={momentum:.2f}"
        if direction == "SELL" and momentum > -0.05:
            return False, f"weak-down-momentum={momentum:.2f}"

        return True, f"gap={gap_ratio:.2f},momentum={momentum:.2f}"
    except Exception as exc:
        return False, f"regime-error={exc}"


def v4_signal(df, htf_df, epic):
    result = quant_signal_score_v2(df, htf_df, epic)
    if isinstance(result, tuple):
        signal = result[0] if len(result) > 0 else None
        score = result[1] if len(result) > 1 else None
    else:
        signal, score = result, None

    if signal not in ("BUY", "SELL") or score is None or score < V4_MIN_SCORE:
        return None

    ok, reason = _regime_ok(df, signal)
    if not ok:
        base.log(f"{epic}: V4 OPPORTUNITY REJECTED | {reason}")
        return None

    base.log(f"{epic}: V4 OPPORTUNITY CONFIRMED | {signal} | score={score:.2f} | {reason}")
    return signal


def v4_entry_strength(df, htf_df, direction):
    raw = market_entry_strength_v2(df, htf_df, direction)
    return min(1.0, raw + 0.20)


def v4_profit_manager(api, positions, epic, account_currency):
    """Fast profit capture, while allowing stronger moves to continue via trailing."""
    for position in base.get_positions_for_epic(positions, epic):
        deal_id = base.position_deal_id(position)
        if not deal_id:
            continue
        pnl = base.position_unrealized_pnl(position)
        if pnl is None:
            continue
        if pnl >= V4_PROFIT_TARGET:
            try:
                response = api.close_position(deal_id)
                base.log(
                    f"{epic}: V4 PROFIT TARGET HIT | deal={deal_id} | "
                    f"pnl={pnl:.2f} {account_currency} | target={V4_PROFIT_TARGET:.2f} | "
                    f"CLOSE RESPONSE={response}"
                )
            except Exception as exc:
                base.log(f"{epic}: V4 profit close failed | deal={deal_id} | {exc}")


base.quant_signal_score = v4_signal
base.market_entry_strength = v4_entry_strength
base.manage_profit_trailing = v4_profit_manager
base.STRATEGY_ID = V4_STRATEGY_ID

base.POSITION_OWNERSHIP_FILE = "v4_strategy_positions.json"
base.STATE_FILE = "v4_trades_state.json"
base.OPEN_POSITIONS_FILE = "v4_open_positions.json"
base.SAFETY_STATE_FILE = "v4_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "v4_execution_quality.json"
base.ENTRY_REJECTION_FILE = "v4_entry_rejections.json"

base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = True
base.PROFIT_TRAIL_ENABLED = False
base.PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS = 3
base.MAX_POSITIONS_PER_EPIC = 8
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False
base.MIN_ENTRY_SCORE = V4_MIN_SCORE
base.MIN_ENTRY_STRENGTH = V4_MIN_ENTRY_STRENGTH


def run_cycle():
    base.log(
        f"STARTING {V4_STRATEGY_ID} | DEMO ONLY | "
        f"SMART OPPORTUNITY | score>={V4_MIN_SCORE} | "
        f"entry-strength>={V4_MIN_ENTRY_STRENGTH}"
    )
    api = base.CapitalAPI()
    api.login()
    balance = api.get_balance()
    account_currency = api.get_account_currency()
    base.log(f"Account balance: {balance} {account_currency}")
    base.reset_daily_safety(balance)
    base.update_loss_cooldowns_from_history(api)
    base.log_trade_report(api, account_currency)

    scan_seconds = 2
    window_seconds = 14 * 60
    deadline = time.monotonic() + window_seconds

    # Fetch account state once per rapid scan pass, not once per epic.
    # This keeps the 2-second opportunity loop fast without removing the
    # safety refresh performed inside process_epic before a new entry.
    while time.monotonic() < deadline:
        cycle_positions = api.get_open_positions()
        cycle_balance = api.get_balance()
        for epic in base.EPICS:
            try:
                base.process_epic(
                    api=api,
                    epic=epic,
                    positions=cycle_positions,
                    balance=cycle_balance,
                    account_currency=account_currency,
                )
            except Exception as exc:
                base.log(f"{epic}: V4 scan error: {exc}")
        time.sleep(scan_seconds)

    base.save_live_stats(api, account_currency)
    base.log("V4 smart-opportunity 2-second scan window completed.")


if __name__ == "__main__":
    run_cycle()
