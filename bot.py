import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import requests

import config
import logger
import stats
import strategy
import risk
from capital_api import CapitalAPI

DIRECTION_TO_API = {"LONG": "BUY", "SHORT": "SELL"}
CLOSE_TOLERANCE = 0.5


def notify_discord(message):
    if not config.DISCORD_WEBHOOK_URL:
        return
    try:
        resp = requests.post(config.DISCORD_WEBHOOK_URL, json={"content": message})
        if resp.status_code >= 300:
            print(f"notify_discord error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"notify_discord error: {e}")


def format_trade_message(epic, direction, size, entry, sl, tp, risk_amount):
    return (
        f"🟢 **OPENED {direction} — {epic}**\n"
        f"Size: {size}\n"
        f"Entry: ${entry:.2f}\n"
        f"Stop Loss: ${sl:.2f}\n"
        f"Take Profit: ${tp:.2f}\n"
        f"Risk: ${risk_amount:.2f}"
    )


def format_daily_summary(data):
    now = datetime.now(timezone.utc)
    overall = data["overall"]

    def signed(v):
        return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"

    lines = [
        f"Daily Stats Summary - {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Overall:",
        f"Trades: {overall['total_trades']} ({overall['closed_trades']} closed, {overall['open_trades']} open)",
        f"Win rate: {overall['win_rate']}% ({overall['winners']}W / {overall['losers']}L)",
        f"Total PnL: {signed(overall['total_pnl'])}",
        f"Best trade: {signed(overall['best_trade'])} | Worst trade: {signed(overall['worst_trade'])}",
        f"Expectancy: {signed(overall['expectancy'])} | Max drawdown: {signed(overall['max_drawdown'])}",
        f"Current streak: {overall['current_streak']}",
        "",
        "By Epic:",
    ]
    for epic, epic_stats in data["by_epic"].items():
        lines.append(
            f"{epic}: {epic_stats['trades']} trades, {epic_stats['win_rate']}% win rate, PnL {signed(epic_stats['pnl'])}"
        )

    return "\n".join(lines)


def send_daily_summary_if_due(stats_data):
    now = datetime.now(timezone.utc)
    # ponytail: gated on "first cron slot of the hour" instead of a last-sent-date
    # file, since the schedule always lands on the same 4 minutes each hour - add
    # a state file if the schedule ever runs off that grid.
    if now.hour == config.DAILY_SUMMARY_HOUR_UTC and now.minute < 15:
        notify_discord(format_daily_summary(stats_data))


def is_paused():
    username = os.environ.get("GITHUB_USERNAME")
    repo = os.environ.get("GITHUB_REPO")
    if not username or not repo:
        return False
    try:
        resp = requests.get(f"https://api.github.com/repos/{username}/{repo}/contents/PAUSED", timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def get_total_pnl():
    with sqlite3.connect(config.DB_PATH) as conn:
        total = conn.execute("SELECT SUM(pnl) FROM trades WHERE close_price IS NOT NULL").fetchone()[0]
    return total or 0


def determine_close_reason(close_price, entry_price, direction, stop_loss, take_profit):
    """Classify by proximity to the actual SL/TP levels, not by which side of entry the
    close landed on. Side-of-entry logic labelled every close as SL or TP even when the
    position was closed manually or by the broker mid-range, so the SL/TP split in
    trades.db was an assumption rather than a measurement. A close that matches neither
    level within CLOSE_TOLERANCE is genuinely MANUAL."""
    near_tp = take_profit is not None and abs(close_price - take_profit) <= CLOSE_TOLERANCE
    near_sl = stop_loss is not None and abs(close_price - stop_loss) <= CLOSE_TOLERANCE

    if near_tp and not near_sl:
        return "TP"
    if near_sl and not near_tp:
        return "SL"
    if near_sl and near_tp:
        # Degenerate case: SL and TP within tolerance of each other. Fall back to which
        # side of entry the close landed on, which is unambiguous for a real fill.
        if direction == "LONG":
            return "TP" if close_price > entry_price else "SL"
        return "TP" if close_price < entry_price else "SL"

    return "MANUAL"


CLOSE_PRICE_MAX_DEVIATION = 0.10
RECONCILIATION_TOLERANCE = 0.05


def fetch_close_details(api, deal_id, direction, size, entry_price, fallback_price):
    close_price = None
    try:
        for activity in api.get_deal_activity(deal_id):
            level = activity.get("details", {}).get("level")
            if level is not None and level > 0:
                close_price = level
                break
    except Exception:
        pass

    if not close_price:
        close_price = fallback_price

    if not close_price:
        return None, None

    if direction == "LONG":
        pnl = (close_price - entry_price) * size
    else:
        pnl = (entry_price - close_price) * size

    return round(close_price, 2), round(pnl, 2)


def is_valid_price(candidate, reference):
    if not candidate or candidate <= 0:
        return False
    return abs(candidate - reference) / reference <= CLOSE_PRICE_MAX_DEVIATION


def fetch_reported_pnl(api, deal_id):
    """Cross-check our computed pnl against Capital.com's own realized-pnl figure for
    the same deal, sourced from /history/transactions."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        txs = api.get_transactions(f"{today}T00:00:00", f"{today}T23:59:59", tx_type="TRADE")
    except Exception:
        return None
    for tx in txs:
        if tx.get("dealId") == deal_id and tx.get("note") == "Trade closed":
            try:
                return float(tx["size"])
            except (KeyError, ValueError, TypeError):
                return None
    return None


def resolve_opened_position(api, epic, estimated_entry_price):
    """Look the freshly opened position up directly from /positions rather than
    trusting the confirms endpoint's dealId, so deal_id and entry_price both reflect
    the real fill (and so a transient confirmation-read glitch on an order that
    actually filled can still be reconciled instead of logged as an error)."""
    for p in api.get_open_positions():
        if p["market"]["epic"] == epic:
            level = p["position"]["level"]
            if is_valid_price(level, estimated_entry_price):
                return p["position"]["dealId"], level
            print(f"WARNING: live fill level {level} for {epic} deviates >10% from estimate {estimated_entry_price}, discarding.")
            break
    return None, None


def effective_balance(api):
    """Caps the balance the risk layer sees, so demo-account sizing (funded far above
    any balance worth sizing against) resembles what a real account would take."""
    return min(api.get_balance(), config.BALANCE_CAP)


def check_closed_trades(api, epic, positions, current_close_price):
    live_deal_ids = {p["position"]["dealId"] for p in positions if p["market"]["epic"] == epic}

    with sqlite3.connect(config.DB_PATH) as conn:
        open_trades = conn.execute(
            "SELECT deal_id, direction, size, entry_price, stop_loss, take_profit FROM trades "
            "WHERE epic = ? AND status = 'OPENED' AND close_price IS NULL AND deal_id IS NOT NULL",
            (epic,),
        ).fetchall()

    for deal_id, direction, size, entry_price, stop_loss, take_profit in open_trades:
        if deal_id in live_deal_ids:
            continue

        close_price, pnl = fetch_close_details(api, deal_id, direction, size, entry_price, current_close_price)

        if not is_valid_price(close_price, entry_price):
            print(f"WARNING: skipping close for {epic} deal {deal_id}, invalid close_price={close_price} (entry={entry_price})")
            continue

        close_reason = determine_close_reason(close_price, entry_price, direction, stop_loss, take_profit)

        logger.update_trade_status(deal_id, "CLOSED", close_price=close_price, pnl=pnl, close_reason=close_reason)

        reported_pnl = fetch_reported_pnl(api, deal_id)
        if reported_pnl is not None and abs(pnl - reported_pnl) > RECONCILIATION_TOLERANCE:
            print(f"WARNING: PnL reconciliation mismatch for {epic} deal {deal_id}: "
                  f"bot={pnl} capital.com={reported_pnl} diff={round(pnl - reported_pnl, 2)}")

        running_total = get_total_pnl()
        balance = api.get_balance()
        result_emoji = "🟢" if pnl >= 0 else "🔴"
        notify_discord(
            f"{result_emoji} **CLOSED {direction} — {epic} [{close_reason}]**\n"
            f"PnL: {'+' if pnl >= 0 else ''}${pnl:.2f}\n"
            f"Running total: {'+' if running_total >= 0 else ''}${running_total:.2f}\n"
            f"Account balance: ${balance:.2f}"
        )


def run_epic_cycle(api, epic):
    try:
        positions = api.get_open_positions()

        candles = api.get_candles(epic)
        df = strategy.candles_to_dataframe(candles)
        df = strategy.add_indicators(df)
        if config.STRATEGY == "vol_regime":
            df = strategy.add_volatility_regime(df)
        last = df.iloc[-1]

        check_closed_trades(api, epic, positions, last["close"])

        if any(p["market"]["epic"] == epic for p in positions):
            print(f"Position already open on {epic}, skipping cycle.")
            return

        # The vol_regime rule returns a context describing every gate it applied, so a
        # SKIP is recorded with its reason rather than vanishing - verifying that live
        # decisions match backtested ones is the point of the forward test.
        if config.STRATEGY == "vol_regime":
            signal, ctx = strategy.generate_vol_regime_signal(df)
        else:
            signal, ctx = strategy.generate_signal(df), None

        baseline_atr = None
        if config.STRATEGY == "vol_regime" and config.VOL_MANAGED_SIZING:
            b = last.get("baseline_atr")
            baseline_atr = None if b is None or pd.isna(b) else float(b)
        size_mult = risk.volatility_scalar(last["atr"], baseline_atr) if baseline_atr else None

        logger.log_signal(epic, last["ema_fast"], last["ema_slow"], last["rsi"], last["atr"],
                          signal or "NONE", context=ctx, size_multiplier=size_mult)

        if signal is None:
            reason = f" ({ctx['gate']})" if ctx else ""
            print(f"No signal this cycle for {epic}{reason}.")
            return

        direction = "LONG" if signal == "BUY" else "SHORT"
        balance = effective_balance(api)
        entry_price = last["close"]
        atr = last["atr"]

        try:
            trade = risk.calculate_trade(balance, entry_price, atr, direction, epic=epic,
                                         baseline_atr=baseline_atr)
        except ValueError as e:
            print(f"Skipping {epic}: {e}")
            return

        result = api.place_order(
            direction=DIRECTION_TO_API[direction],
            size=trade["size"],
            stop_level=trade["stop_loss"],
            profit_level=trade["take_profit"],
            epic=epic,
        )
        confirmation = api.get_confirmation(result["dealReference"])

        # Always check /positions directly rather than trusting the confirms endpoint
        # alone - it disambiguates the real fill price/deal_id even on ACCEPTED, and on
        # a non-ACCEPTED status it catches a confirms-read glitch on an order that
        # actually filled, instead of raising immediately and leaving a filled,
        # stop-protected position untracked.
        resolved_deal_id, resolved_entry_price = resolve_opened_position(api, epic, entry_price)
        if resolved_deal_id:
            deal_id, entry_price = resolved_deal_id, resolved_entry_price
        elif confirmation.get("dealStatus") != "ACCEPTED":
            raise RuntimeError(f"Order not accepted for {epic}: {confirmation}")
        else:
            deal_id = confirmation.get("dealId", result.get("dealReference"))
            print(f"WARNING: could not confirm live position for {epic} after order placement; "
                  f"logging estimated entry_price and order-level deal_id as a fallback.")

        logger.log_trade(
            epic, direction, trade["size"], entry_price,
            trade["stop_loss"], trade["take_profit"], "OPENED", deal_id=deal_id,
        )
        notify_discord(format_trade_message(
            epic, direction, trade["size"], entry_price, trade["stop_loss"], trade["take_profit"], trade["risk_amount"],
        ))

    except Exception as e:
        logger.log_trade(epic, "NONE", None, None, None, None, "ERROR", error=str(e))
        notify_discord(f"ERROR: {epic} - {e}\nCycle skipped.")

def run_cycle():
    if is_paused():
        print("Bot is paused. Skipping cycle.")
        return

    api = CapitalAPI()

    try:
        api.login()
    except Exception as e:
        notify_discord(f"ERROR: login failed - {e}\nCycle skipped.")
        return

    for epic in config.EPICS:
        run_epic_cycle(api, epic)

    stats_data = stats.update_stats()
    send_daily_summary_if_due(stats_data)


if __name__ == "__main__":
    logger.init_db()

    try:
        run_cycle()
    except Exception as e:
        print(f"MAIN LOOP ERROR: {e}")
        notify_discord(f"ERROR: Main loop - {e}")


