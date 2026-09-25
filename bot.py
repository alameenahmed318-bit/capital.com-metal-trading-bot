import json
import math
import os
import re
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from capital_api import CapitalAPI
from portfolio_risk import portfolio_risk_overlay
from execution_costs import evaluate_pretrade_cost

DEMO_ONLY = True
ALLOW_GRID = False
ALLOW_MARTINGALE = False
ALLOW_AVERAGING = False

MAX_POSITIONS_PER_EPIC = 10
GRID_STEP_R = 0.75
MARTINGALE_MULTIPLIER = 1.25
AGGRESSIVE_BASE_RISK = getattr(config, "RISK_PER_TRADE", 0.01)
MAX_BASKET_RISK = 0.04

EPICS = list(dict.fromkeys(getattr(config, "EPICS", ["GOLD", "EURUSD", "SILVER", "OIL_CRUDE", "US100", "US500"])))

RESOLUTION = getattr(config, "RESOLUTION", "MINUTE_15")
CANDLE_COUNT = getattr(config, "CANDLE_COUNT", 300)
EMA_FAST = getattr(config, "EMA_FAST", 9)
EMA_SLOW = getattr(config, "EMA_SLOW", 21)
RSI_PERIOD = getattr(config, "RSI_PERIOD", 14)
ATR_PERIOD = getattr(config, "ATR_PERIOD", 14)
HTF_RESOLUTION = getattr(config, "HTF_RESOLUTION", "HOUR")
HTF_CANDLE_COUNT = getattr(config, "HTF_CANDLE_COUNT", 250)
HTF_EMA_FAST = getattr(config, "HTF_EMA_FAST", 50)
HTF_EMA_SLOW = getattr(config, "HTF_EMA_SLOW", 200)
VOL_REGIME_MIN = getattr(config, "VOL_REGIME_MIN", 1.05)
VOL_REGIME_FAST = getattr(config, "VOL_REGIME_FAST", 20)
VOL_REGIME_SLOW = getattr(config, "VOL_REGIME_SLOW", 200)
MAX_PORTFOLIO_RISK = getattr(config, "MAX_PORTFOLIO_RISK", 0.06)
PORTFOLIO_VOL_TARGET_ANNUAL = getattr(config, "PORTFOLIO_VOL_TARGET_ANNUAL", 0.10)
PORTFOLIO_RISK_MIN_MULTIPLIER = getattr(config, "PORTFOLIO_RISK_MIN_MULTIPLIER", 0.35)
PORTFOLIO_RISK_MAX_MULTIPLIER = getattr(config, "PORTFOLIO_RISK_MAX_MULTIPLIER", 1.00)
PORTFOLIO_COV_LOOKBACK = getattr(config, "PORTFOLIO_COV_LOOKBACK", 192)
PRETRADE_COST_FILTER_ENABLED = getattr(config, "PRETRADE_COST_FILTER_ENABLED", True)
MAX_COST_TO_STOP_RATIO = getattr(config, "MAX_COST_TO_STOP_RATIO", 0.25)
EXTRA_SLIPPAGE_BUFFER_PCT = getattr(config, "EXTRA_SLIPPAGE_BUFFER_PCT", 0.01)
ALPHA_ENSEMBLE_ENABLED = getattr(config, "ALPHA_ENSEMBLE_ENABLED", True)
ALPHA_MIN_AGREEMENT = getattr(config, "ALPHA_MIN_AGREEMENT", 2)
XAU_WORKING_ORDER_ENABLED = False  # Gold uses market orders on BUY and SELL signals
XAU_WORKING_TRIGGER = getattr(config, "XAU_WORKING_TRIGGER", 4400.0)

MARKET_RSI_SETTINGS = getattr(config, "MARKET_RSI_SETTINGS", {})
if not MARKET_RSI_SETTINGS:
    MARKET_RSI_SETTINGS = {epic: (40, 70, 30, 60) for epic in EPICS}
MARKET_BIAS = {epic: "BOTH" for epic in EPICS}

# Give losing trades more breathing room while keeping risk sizing tied to the wider stop.
# The position size is reduced automatically as risk distance increases.
SL_ATR_MULT = 2.0
TP_ATR_MULT = 3.0
TRAILING_ENABLED = True
# Give winning trades more room before the protective stop starts following price.
TRAILING_START_R = 2.00
TRAILING_DISTANCE_R = 1.50

# Profit-lock: activate only after meaningful profit, then allow a wider pullback.
# Values are in the account currency (AED for an AED account).
PROFIT_TRAIL_ENABLED = True
PROFIT_TRAIL_START = 1.0
PROFIT_TRAIL_DISTANCE = 1.0

# Hard per-position loss guard in account currency (AED for an AED account).
# This is a secondary protection; the broker-side ATR stop remains the primary stop.
MAX_LOSS_PER_POSITION = 10.0

# Strategy Selector: automatically classify market regime and choose Trend/Breakout/Range.
STRATEGY_SELECTOR_ENABLED = True
TREND_EMA_GAP_ATR = 0.25
RANGE_EMA_GAP_ATR = 0.10
RANGE_RSI_BUY_MAX = 48
RANGE_RSI_SELL_MIN = 52

# Strategy v2 filters
USE_SUPPORT_RESISTANCE = True
USE_BREAKOUT_CONFIRMATION = True
SR_LOOKBACK = 60
SR_BUFFER_ATR = 0.25
BREAKOUT_LOOKBACK = 20
# When enabled, a profitable existing basket can add legs immediately
# (without waiting for the normal grid distance) until the per-epic cap.
ADD_TO_PROFITABLE_BASKET = True
# Smaller incremental risk for additional legs while the existing basket is profitable.
PROFITABLE_ADD_RISK = 0.002

# Free, local risk/execution protections (no external paid service).
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_PCT = 0.08
EXECUTION_QUALITY_ENABLED = True
EXECUTION_QUALITY_FILE = "execution_quality.json"
MAX_ACCEPTABLE_SLIPPAGE_PCT = 0.03

# Entry-quality upgrades: allow a little more room for normal execution lag,
# but block entries that are materially stretched or over-correlated with
# existing exposure. All rejections are persisted with an exact reason.
LATE_ENTRY_MAX_ATR = 0.50
LATE_ENTRY_STRONG_MAX_ATR = 0.75
CORRELATION_FILTER_ENABLED = True
CORRELATION_LOOKBACK = 96
CORRELATION_THRESHOLD = 0.80
CORRELATION_CACHE_SECONDS = 60
ENTRY_REJECTION_FILE = "entry_rejections.json"
ENTRY_REJECTION_MAX_ROWS = 1000

DAILY_LOSS_LIMIT_AED = 300.0  # Daily entry-stop threshold for AED demo accounts
DAILY_LOSS_LIMIT_PCT = 0.03  # Fallback for non-AED accounts
EQUITY_DRAWDOWN_LIMIT_PCT = 0.05
LOSS_COOLDOWN_MINUTES = 20
SIDEWAYS_FILTER_ENABLED = True
SIDEWAYS_ATR_RATIO_MAX = 0.90
BREAKEVEN_ENABLED = True
# Do not move to break-even too early; allow normal market pullbacks first.
BREAKEVEN_START_R = 1.25
BREAKEVEN_OFFSET_R = 0.10
KILL_SWITCH_ENABLED = False
MAX_CONSECUTIVE_ERRORS = 3
SAFETY_STATE_FILE = "bot_safety_state.json"

# Conservative strategy-quality upgrades.
SESSION_FILTER_ENABLED = getattr(config, "SESSION_FILTER_ENABLED", True)
SESSION_START_UTC = getattr(config, "SESSION_START_UTC", 7)
SESSION_END_UTC = getattr(config, "SESSION_END_UTC", 20)
WEEKEND_FILTER_ENABLED = getattr(config, "WEEKEND_FILTER_ENABLED", True)
ADAPTIVE_RISK_ENABLED = getattr(config, "ADAPTIVE_RISK_ENABLED", True)
ADAPTIVE_RISK_HIGH_VOL_1 = getattr(config, "ADAPTIVE_RISK_HIGH_VOL_1", 1.25)
ADAPTIVE_RISK_HIGH_VOL_2 = getattr(config, "ADAPTIVE_RISK_HIGH_VOL_2", 1.50)
ADAPTIVE_RISK_LOW_VOL = getattr(config, "ADAPTIVE_RISK_LOW_VOL", 0.75)
BREAKOUT_CONFIRM_ATR = getattr(config, "BREAKOUT_CONFIRM_ATR", 0.05)
MIN_ENTRY_SCORE = getattr(config, "MIN_ENTRY_SCORE", 65.0)

MIN_TRADE_SIZE = getattr(config, "MIN_TRADE_SIZE", {"GOLD": 0.01, "EURUSD": 0.01, "SILVER": 1.0, "OIL_CRUDE": 0.01, "US100": 0.01, "US500": 0.01})
STATE_FILE = "trades_state.json"
OPEN_POSITIONS_FILE = "open_positions.json"

def log(message):
    print(f"[BOT] {message}")

def record_entry_rejection(epic, reason, details=""):
    """Persist every blocked new-entry decision with a machine-readable reason."""
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "epic": epic,
        "reason": str(reason),
        "details": str(details),
    }
    try:
        if os.path.exists(ENTRY_REJECTION_FILE):
            with open(ENTRY_REJECTION_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, list):
                data = []
        else:
            data = []
        data = data[-(ENTRY_REJECTION_MAX_ROWS - 1):] + [row]
        temp_file = f"{ENTRY_REJECTION_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temp_file, ENTRY_REJECTION_FILE)
    except Exception as exc:
        log(f"{epic}: rejection log write failed: {exc}")
    log(f"{epic}: ENTRY REJECTED | reason={reason}" + (f" | {details}" if details else ""))

def load_safety_state():
    if not os.path.exists(SAFETY_STATE_FILE):
        return {"day": "", "day_start_balance": None, "peak_equity": None, "consecutive_errors": 0, "cooldown_until": {}}
    try:
        with open(SAFETY_STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        state.setdefault("day", "")
        state.setdefault("day_start_balance", None)
        state.setdefault("peak_equity", None)
        state.setdefault("consecutive_errors", 0)
        state.setdefault("cooldown_until", {})
        return state
    except Exception as exc:
        log(f"Could not load safety state: {exc}")
        return {"day": "", "day_start_balance": None, "peak_equity": None, "consecutive_errors": 0, "cooldown_until": {}}

def save_safety_state(state):
    temp_file = f"{SAFETY_STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
    os.replace(temp_file, SAFETY_STATE_FILE)

SAFETY = load_safety_state()

def utc_day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def reset_daily_safety(balance):
    today = utc_day()
    if SAFETY.get("day") != today:
        SAFETY["day"] = today
        SAFETY["day_start_balance"] = float(balance)
        SAFETY["peak_equity"] = float(balance)
        SAFETY["consecutive_errors"] = 0
        SAFETY["cooldown_until"] = {}
        save_safety_state(SAFETY)

def account_equity(balance, positions):
    return float(balance) + sum(position_unrealized_pnl(p) for p in positions)

def safety_allows_new_entry(balance, positions):
    if not KILL_SWITCH_ENABLED:
        return True
    reset_daily_safety(balance)
    equity = account_equity(balance, positions)
    peak = safe_float(SAFETY.get("peak_equity"), equity) or equity
    if equity > peak:
        SAFETY["peak_equity"] = equity
        peak = equity
        save_safety_state(SAFETY)
    start_balance = safe_float(SAFETY.get("day_start_balance"), balance) or balance
    daily_loss_limit = DAILY_LOSS_LIMIT_AED if str(getattr(config, "ACCOUNT_CURRENCY", "AED")).upper() == "AED" else start_balance * DAILY_LOSS_LIMIT_PCT
    daily_floor = start_balance - daily_loss_limit
    drawdown_floor = peak * (1.0 - EQUITY_DRAWDOWN_LIMIT_PCT)
    if balance <= daily_floor or equity <= drawdown_floor:
        log(f"SAFETY STOP: new entries disabled | balance={balance:.2f} equity={equity:.2f} day_floor={daily_floor:.2f} drawdown_floor={drawdown_floor:.2f}")
        return False
    if int(SAFETY.get("consecutive_errors", 0)) >= MAX_CONSECUTIVE_ERRORS:
        log(f"KILL SWITCH: {SAFETY['consecutive_errors']} consecutive errors; new entries disabled.")
        return False
    return True

def cooldown_active(epic):
    until = safe_float(SAFETY.get("cooldown_until", {}).get(epic))
    if until and datetime.now(timezone.utc).timestamp() < until:
        log(f"{epic}: cooldown active after recent loss; no new entry.")
        return True
    return False

def set_loss_cooldown(epic):
    SAFETY.setdefault("cooldown_until", {})[epic] = datetime.now(timezone.utc).timestamp() + LOSS_COOLDOWN_MINUTES * 60
    save_safety_state(SAFETY)

def market_spread_pct(market):
    # Capital.com calls the sell-side quote "offer" (not "ask") in its
    # REST market snapshot. Support both nested and top-level responses.
    snapshot = market.get("snapshot", {}) or {}
    bid = safe_float(snapshot.get("bid") if snapshot.get("bid") is not None else market.get("bid"))
    offer = safe_float(
        snapshot.get("offer")
        if snapshot.get("offer") is not None
        else snapshot.get("ask")
        if snapshot.get("ask") is not None
        else market.get("offer")
        if market.get("offer") is not None
        else market.get("ask")
    )
    if bid is None or offer is None or bid <= 0 or offer <= 0 or offer < bid:
        return None
    return ((offer - bid) / ((offer + bid) / 2.0)) * 100.0

def spread_allows_entry(api, epic):
    if not SPREAD_FILTER_ENABLED:
        return True
    spread = market_spread_pct(api.get_market(epic))
    if spread is None:
        log(f"{epic}: spread unavailable; entry blocked for safety.")
        return False
    if spread > MAX_SPREAD_PCT:
        log(f"{epic}: spread too wide ({spread:.4f}% > {MAX_SPREAD_PCT:.4f}%); entry blocked.")
        return False
    return True

def _load_execution_quality():
    if not os.path.exists(EXECUTION_QUALITY_FILE):
        return {"updated_at": None, "orders": []}
    try:
        with open(EXECUTION_QUALITY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {"updated_at": None, "orders": []}
        data.setdefault("orders", [])
        return data
    except Exception as exc:
        log(f"Could not load execution-quality log: {exc}")
        return {"updated_at": None, "orders": []}

def _save_execution_quality(data):
    temp_file = f"{EXECUTION_QUALITY_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    os.replace(temp_file, EXECUTION_QUALITY_FILE)

def _confirmed_entry_level(confirmation):
    if not isinstance(confirmation, dict):
        return None
    nested = confirmation.get("deal") if isinstance(confirmation.get("deal"), dict) else {}
    for value in (confirmation.get("level"), confirmation.get("openLevel"), confirmation.get("executionPrice"), confirmation.get("executedPrice"), nested.get("level"), nested.get("openLevel"), nested.get("executionPrice"), nested.get("executedPrice")):
        parsed = safe_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None

def record_execution_quality(epic, direction, requested_price, actual_price, spread_pct, deal_reference=None, deal_id=None, deal_status="ACCEPTED", size=None):
    if not EXECUTION_QUALITY_ENABLED:
        return
    requested_price = safe_float(requested_price)
    actual_price = safe_float(actual_price)
    spread_pct = safe_float(spread_pct)
    slippage_price = None
    slippage_pct = None
    if requested_price is not None and actual_price is not None and requested_price > 0:
        slippage_price = (actual_price - requested_price) if direction == "BUY" else (requested_price - actual_price)
        slippage_pct = max(0.0, slippage_price / requested_price * 100.0)
    if str(deal_status).upper() in {"REJECTED", "FAILED"}:
        grade = "REJECTED"
    elif slippage_pct is None:
        grade = "UNMEASURED"
    elif slippage_pct <= 0.005:
        grade = "A"
    elif slippage_pct <= 0.015:
        grade = "B"
    elif slippage_pct <= 0.03:
        grade = "C"
    elif slippage_pct <= 0.06:
        grade = "D"
    else:
        grade = "E"
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "epic": epic, "direction": direction, "requested_executable_price": requested_price, "actual_fill_price": actual_price, "spread_pct_at_order": spread_pct, "slippage_price": round(slippage_price, 8) if slippage_price is not None else None, "slippage_pct": round(slippage_pct, 6) if slippage_pct is not None else None, "deal_reference": deal_reference, "deal_id": deal_id, "deal_status": deal_status, "quality_grade": grade, "size": safe_float(size)}
    data = _load_execution_quality()
    data["orders"] = (data.get("orders") or [])[-499:] + [row]
    data["updated_at"] = row["timestamp"]
    _save_execution_quality(data)
    log(f"{epic}: EXECUTION QUALITY | spread={spread_pct:.4f}% | requested={requested_price} | fill={actual_price} | adverse slippage={slippage_pct:.4f}% | grade={grade}" if spread_pct is not None and slippage_pct is not None else f"{epic}: EXECUTION QUALITY | spread={spread_pct} | requested={requested_price} | fill={actual_price} | slippage={slippage_pct} | grade={grade}")

def breakeven_stops(api, positions, epic, current_price):
    if not BREAKEVEN_ENABLED:
        return
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        direction = position_direction(position)
        entry = position_open_level(position)
        current_sl = position_stop_level(position)
        if not deal_id or entry is None or not direction:
            continue
        risk = original_risk_distance(position)
        if risk is None or risk <= 0:
            continue
        favorable = current_price - entry if direction == "BUY" else entry - current_price
        if favorable < BREAKEVEN_START_R * risk:
            continue
        new_stop = entry + BREAKEVEN_OFFSET_R * risk if direction == "BUY" else entry - BREAKEVEN_OFFSET_R * risk
        if direction == "BUY" and current_sl is not None and new_stop <= current_sl:
            continue
        if direction == "SELL" and current_sl is not None and new_stop >= current_sl:
            continue
        try:
            api.modify_position(deal_id=deal_id, stop_level=new_stop)
            log(f"{epic}: BREAK-EVEN STOP UPDATED | {direction} | new SL={new_stop}")
        except Exception as exc:
            log(f"{epic}: break-even update failed: {exc}")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"risk_distance": {}, "profit_trail": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            return {"risk_distance": {}, "profit_trail": {}}
        state.setdefault("risk_distance", {})
        state.setdefault("profit_trail", {})
        return state
    except Exception as exc:
        log(f"Could not load state file: {exc}")
        return {"risk_distance": {}, "profit_trail": {}}

def save_state(state):
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
    os.replace(temp_file, STATE_FILE)

STATE = load_state()

def safe_float(value, default=None):
    """Safely parse numeric values, including Capital.com strings with currency text."""
    try:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            number = float(value)
            return number if math.isfinite(number) else default
        text_value = str(value).strip().replace(",", "")
        try:
            number = float(text_value)
            return number if math.isfinite(number) else default
        except ValueError:
            match = re.search(r"[-+]?\\d+(?:\\.\\d+)?", text_value)
            if not match:
                return default
            number = float(match.group(0))
            return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default

def normalize_direction(value):
    if value is None:
        return None
    value = str(value).upper()
    if value in ("BUY", "LONG"):
        return "BUY"
    if value in ("SELL", "SHORT"):
        return "SELL"
    return value

def _position_nested(position):
    nested = position.get("position")
    return nested if isinstance(nested, dict) else {}

def _first_value(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None

def position_epic(position):
    nested = _position_nested(position)
    instrument = position.get("instrument") if isinstance(position.get("instrument"), dict) else {}
    nested_instrument = nested.get("instrument") if isinstance(nested.get("instrument"), dict) else {}
    return _first_value(
        position.get("epic"),
        position.get("instrumentName"),
        instrument.get("epic"),
        nested.get("epic"),
        nested.get("instrumentName"),
        nested_instrument.get("epic"),
        position.get("market", {}).get("epic") if isinstance(position.get("market"), dict) else None,
        nested.get("market", {}).get("epic") if isinstance(nested.get("market"), dict) else None,
    )

def position_deal_id(position):
    nested = _position_nested(position)
    return _first_value(position.get("dealId"), position.get("dealReference"), nested.get("dealId"), nested.get("dealReference"))

def position_direction(position):
    nested = _position_nested(position)
    return normalize_direction(_first_value(position.get("direction"), nested.get("direction")))

def position_open_level(position):
    nested = _position_nested(position)
    return safe_float(_first_value(
        position.get("openLevel"),
        position.get("openPrice"),
        position.get("level"),
        nested.get("openLevel"),
        nested.get("openPrice"),
        nested.get("level"),
    ))

def position_current_level(position):
    nested = _position_nested(position)
    return safe_float(_first_value(
        position.get("level"),
        position.get("currentLevel"),
        position.get("currentPrice"),
        nested.get("level"),
        nested.get("currentLevel"),
        nested.get("currentPrice"),
    ))

def position_stop_level(position):
    nested = _position_nested(position)
    return safe_float(_first_value(position.get("stopLevel"), nested.get("stopLevel")))

def position_profit_level(position):
    nested = _position_nested(position)
    return safe_float(_first_value(position.get("profitLevel"), nested.get("profitLevel")))

def position_unrealized_pnl(position):
    nested = _position_nested(position)
    return safe_float(_first_value(
        position.get("profitLoss"),
        position.get("unrealizedProfitLoss"),
        position.get("unrealizedPnl"),
        position.get("profit"),
        nested.get("profitLoss"),
        nested.get("unrealizedProfitLoss"),
        nested.get("unrealizedPnl"),
        nested.get("profit"),
        position.get("upl"),
        nested.get("upl"),
    ), 0.0) or 0.0

def position_summary(position):
    return {
        "dealId": position_deal_id(position),
        "epic": position_epic(position),
        "direction": position_direction(position),
        "size": position_size_value(position),
        "entry": position_open_level(position),
        "current": position_current_level(position),
        "stopLoss": position_stop_level(position),
        "takeProfit": position_profit_level(position),
        "profitLoss": position_unrealized_pnl(position),
    }

def log_and_save_open_positions(positions):
    summaries = [position_summary(p) for p in positions]
    if not summaries:
        log("OPEN POSITIONS: none")
    for item in summaries:
        pnl = item["profitLoss"]
        status = "PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
        log(
            f"OPEN POSITION | {item['epic']} | {item['direction']} | "
            f"entry={item['entry']} | current={item['current']} | "
            f"P/L={pnl} | {status} | SL={item['stopLoss']} | TP={item['takeProfit']} | "
            f"size={item['size']} | dealId={item['dealId']}"
        )
    try:
        with open(OPEN_POSITIONS_FILE, "w", encoding="utf-8") as file:
            json.dump({"updated_at": datetime.now(timezone.utc).isoformat(), "positions": summaries}, file, indent=2)
    except Exception as exc:
        log(f"Could not save open positions snapshot: {exc}")


def enforce_max_position_loss(api, positions, epic, account_currency):
    """Close any position whose current P/L reaches the hard account-currency loss cap."""
    if MAX_LOSS_PER_POSITION is None or MAX_LOSS_PER_POSITION <= 0:
        return
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id or pnl > -MAX_LOSS_PER_POSITION:
            continue
        try:
            response = api.close_position(deal_id)
            log(
                f"{epic}: HARD LOSS LIMIT CLOSE | deal={deal_id} | "
                f"P/L={pnl:.2f} {account_currency} | limit=-{MAX_LOSS_PER_POSITION:.2f} {account_currency}"
            )
            log(f"{epic}: HARD LOSS CLOSE RESPONSE = {response}")
        except Exception as exc:
            log(f"{epic}: hard loss close failed | deal={deal_id} | {exc}")


def original_risk_distance(position):
    """Recover the original SL distance even after break-even/trailing moved the SL."""
    deal_id = position_deal_id(position)
    if deal_id:
        stored = safe_float(STATE.get("risk_distance", {}).get(str(deal_id)))
        if stored is not None and stored > 0:
            return stored

    entry = position_open_level(position)
    take_profit = position_profit_level(position)
    rr_ratio = TP_ATR_MULT / SL_ATR_MULT if SL_ATR_MULT else 0.0
    if entry is not None and take_profit is not None and rr_ratio > 0:
        inferred = abs(take_profit - entry) / rr_ratio
        if inferred > 0:
            return inferred

    stop = position_stop_level(position)
    if entry is not None and stop is not None:
        inferred = abs(entry - stop)
        return inferred if inferred > 0 else None
    return None

def quote_to_account_rate(market, account_currency):
    """Convert P/L quoted in the instrument currency into account currency.

    The enabled portfolio instruments are normally USD-quoted. We only use a
    fixed USD/AED conversion for the AED account case; unknown currency pairs
    return None instead of silently using an incorrect conversion.
    """
    instrument = market.get("instrument", {})
    quote_currency = (
        instrument.get("currency")
        or instrument.get("currencyCode")
        or instrument.get("quoteCurrency")
        or market.get("currency")
        or "USD"
    )
    if isinstance(quote_currency, dict):
        quote_currency = quote_currency.get("code") or quote_currency.get("currencyCode")
    quote_currency = str(quote_currency).upper()
    account_currency = str(account_currency).upper()
    if quote_currency == account_currency:
        return 1.0
    if quote_currency == "USD" and account_currency == "AED":
        return 3.6725
    if quote_currency == "AED" and account_currency == "USD":
        return 1.0 / 3.6725
    return None


def get_position_size(api, epic, risk_amount_account, risk_distance, account_currency):
    risk_amount_account = safe_float(risk_amount_account)
    risk_distance = safe_float(risk_distance)
    if risk_amount_account is None or risk_amount_account <= 0 or risk_distance is None or risk_distance <= 0:
        return None
    market = api.get_market(epic)
    instrument = market.get("instrument", {})
    dealing = market.get("dealingRules", {})
    lot_size = safe_float(instrument.get("lotSize"), 1.0) or 1.0
    min_size = safe_float(dealing.get("minDealSize", {}).get("value"), MIN_TRADE_SIZE.get(epic, 0.01))
    step = safe_float(dealing.get("minSizeIncrement", {}).get("value"), min_size)
    if min_size <= 0 or step <= 0 or lot_size <= 0:
        return None
    account_to_quote = quote_to_account_rate(market, account_currency)
    if account_to_quote is None or account_to_quote <= 0:
        log(f"{epic}: unsupported currency conversion for account {account_currency}; trade skipped.")
        return None
    raw_size = (risk_amount_account / account_to_quote) / (risk_distance * lot_size)
    if raw_size < min_size:
        return None
    steps = math.floor((raw_size - min_size) / step + 1e-12)
    size = min_size + max(0, steps) * step
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    size = round(size, decimals)
    estimated_risk_account = size * risk_distance * lot_size * account_to_quote
    if estimated_risk_account > risk_amount_account * 1.000001:
        size = round(max(0, size - step), decimals)
    return size if size >= min_size else None

def candles_to_dataframe(raw):
    prices = raw.get("prices", [])
    if not prices:
        return pd.DataFrame()
    rows = []
    for candle in prices:
        def mid(price):
            bid = safe_float(price.get("bid"))
            ask = safe_float(price.get("ask"))
            return (bid + ask) / 2 if bid is not None and ask is not None else (bid if bid is not None else ask)
        rows.append({
            "time": candle.get("snapshotTime"),
            "open": mid(candle.get("openPrice", {})),
            "high": mid(candle.get("highPrice", {})),
            "low": mid(candle.get("lowPrice", {})),
            "close": mid(candle.get("closePrice", {})),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

def add_indicators(df):
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    previous_close = df["close"].shift(1)
    true_range = pd.concat([df["high"] - df["low"], (df["high"] - previous_close).abs(), (df["low"] - previous_close).abs()], axis=1).max(axis=1)
    df["atr"] = true_range.ewm(alpha=1 / ATR_PERIOD, min_periods=ATR_PERIOD, adjust=False).mean()
    return df

def get_rsi_settings(epic):
    return MARKET_RSI_SETTINGS.get(epic, (42, 68, 32, 58))

def session_allows_entry():
    if not SESSION_FILTER_ENABLED:
        return True
    now = datetime.now(timezone.utc)
    if WEEKEND_FILTER_ENABLED and now.weekday() >= 5:
        return False
    hour = now.hour + now.minute / 60.0
    return SESSION_START_UTC <= hour < SESSION_END_UTC


def adaptive_risk_multiplier(df):
    if not ADAPTIVE_RISK_ENABLED or len(df) < VOL_REGIME_SLOW + 5:
        return 1.0
    fast = safe_float(df["atr"].rolling(VOL_REGIME_FAST).mean().iloc[-2])
    slow = safe_float(df["atr"].rolling(VOL_REGIME_SLOW).mean().iloc[-2])
    if not fast or not slow or slow <= 0:
        return 1.0
    ratio = fast / slow
    if ratio >= ADAPTIVE_RISK_HIGH_VOL_2:
        return 0.50
    if ratio >= ADAPTIVE_RISK_HIGH_VOL_1 or ratio <= ADAPTIVE_RISK_LOW_VOL:
        return 0.75
    return 1.0


def breakout_confirmation(df, direction, atr):
    if not USE_BREAKOUT_CONFIRMATION:
        return True
    if len(df) < BREAKOUT_LOOKBACK + 4 or atr is None or atr <= 0:
        return False
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    recent = df.iloc[-(BREAKOUT_LOOKBACK + 1):-1]
    high = safe_float(recent["high"].max())
    low = safe_float(recent["low"].min())
    close = safe_float(cur["close"])
    open_price = safe_float(cur["open"])
    prev_close = safe_float(prev["close"])
    if None in (high, low, close, open_price, prev_close):
        return False
    if direction == "BUY":
        return close > high + BREAKOUT_CONFIRM_ATR * atr and close > open_price and close > prev_close
    if direction == "SELL":
        return close < low - BREAKOUT_CONFIRM_ATR * atr and close < open_price and close < prev_close
    return False


def market_regime(df, htf_df):
    """Classify the closed-candle market into TREND, BREAKOUT, or RANGE."""
    if len(df) < max(VOL_REGIME_SLOW + 5, SR_LOOKBACK + 5) or len(htf_df) < HTF_EMA_SLOW + 5:
        return "RANGE"

    current = df.iloc[-2]
    atr = safe_float(current["atr"])
    if atr is None or atr <= 0:
        return "RANGE"

    htf_fast = htf_df["close"].ewm(span=HTF_EMA_FAST, adjust=False).mean().iloc[-2]
    htf_slow = htf_df["close"].ewm(span=HTF_EMA_SLOW, adjust=False).mean().iloc[-2]
    ema_gap = abs(float(current["ema_fast"] - current["ema_slow"]))
    recent = df.iloc[-(BREAKOUT_LOOKBACK + 1):-1]
    price = float(current["close"])
    breakout_high = float(recent["high"].max())
    breakout_low = float(recent["low"].min())

    if price > breakout_high or price < breakout_low:
        return "BREAKOUT"

    htf_aligned = (htf_fast > htf_slow and current["ema_fast"] > current["ema_slow"]) or (
        htf_fast < htf_slow and current["ema_fast"] < current["ema_slow"]
    )
    if htf_aligned and ema_gap >= TREND_EMA_GAP_ATR * atr:
        return "TREND"

    return "RANGE"


def alpha_ensemble_confirmation(df, direction):
    """Independent confirmation from trend, momentum, slope and structure."""
    if len(df) < 80:
        return False, {"reason": "insufficient_history"}
    cur = df.iloc[-2]
    close = safe_float(cur.get("close"))
    if close is None or close <= 0:
        return False, {"reason": "invalid_price"}
    closes = df["close"].astype(float)
    ema9 = closes.ewm(span=9, adjust=False).mean().iloc[-2]
    ema21 = closes.ewm(span=21, adjust=False).mean().iloc[-2]
    ema50 = closes.ewm(span=50, adjust=False).mean().iloc[-2]
    ema200 = closes.ewm(span=200, adjust=False).mean().iloc[-2]
    roc5 = close / float(closes.iloc[-7]) - 1.0
    roc20 = close / float(closes.iloc[-22]) - 1.0
    x20 = np.arange(20, dtype=float)
    x60 = np.arange(60, dtype=float)
    slope20 = float(np.polyfit(x20, closes.iloc[-21:-1].to_numpy(dtype=float), 1)[0])
    slope60 = float(np.polyfit(x60, closes.iloc[-61:-1].to_numpy(dtype=float), 1)[0])
    recent20 = df.iloc[-21:-1]
    high20 = float(recent20["high"].max())
    low20 = float(recent20["low"].min())
    want = 1 if direction == "BUY" else -1
    votes = {
        "trend": 1 if (ema9 > ema21 and ema50 > ema200) else -1 if (ema9 < ema21 and ema50 < ema200) else 0,
        "momentum": 1 if (roc5 > 0 and roc20 > 0) else -1 if (roc5 < 0 and roc20 < 0) else 0,
        "slope": 1 if (slope20 > 0 and slope60 > 0) else -1 if (slope20 < 0 and slope60 < 0) else 0,
        "structure": 1 if close > high20 else -1 if close < low20 else 0,
    }
    agreement = sum(1 for v in votes.values() if v == want)
    opposed = sum(1 for v in votes.values() if v == -want)
    return agreement >= ALPHA_MIN_AGREEMENT and agreement > opposed, {"votes": votes, "agreement": agreement, "opposed": opposed}

def quant_signal_score(df, epic, htf_df):
    """Institutional-style multi-factor score inspired by trend, momentum,
    breakout, volatility and disciplined risk frameworks.
    This is our own implementation, not a copy of any firm's proprietary model.
    """
    if len(df) < 205 or len(htf_df) < HTF_EMA_SLOW + 5:
        return None

    cur = df.iloc[-2]
    prev = df.iloc[-3]
    close = safe_float(cur["close"])
    atr = safe_float(cur["atr"])
    rsi = safe_float(cur["rsi"])
    if close is None or atr is None or atr <= 0 or rsi is None:
        return None

    closes = df["close"]
    ema9 = closes.ewm(span=9, adjust=False).mean().iloc[-2]
    ema21 = closes.ewm(span=21, adjust=False).mean().iloc[-2]
    ema50 = closes.ewm(span=50, adjust=False).mean().iloc[-2]
    ema100 = closes.ewm(span=100, adjust=False).mean().iloc[-2]
    ema200 = closes.ewm(span=200, adjust=False).mean().iloc[-2]

    htf_close = htf_df["close"]
    htf50 = htf_close.ewm(span=50, adjust=False).mean().iloc[-2]
    htf200 = htf_close.ewm(span=200, adjust=False).mean().iloc[-2]

    roc5 = (close / safe_float(df["close"].iloc[-7], close) - 1.0) if safe_float(df["close"].iloc[-7]) else 0.0
    roc20 = (close / safe_float(df["close"].iloc[-22], close) - 1.0) if safe_float(df["close"].iloc[-22]) else 0.0

    recent20 = df.iloc[-21:-1]
    recent60 = df.iloc[-61:-1]
    breakout_high = float(recent20["high"].max())
    breakout_low = float(recent20["low"].min())
    support = float(recent60["low"].min())
    resistance = float(recent60["high"].max())

    atr20 = float(df["atr"].rolling(20).mean().iloc[-2]) if "atr" in df else atr
    atr100 = float(df["atr"].rolling(100).mean().iloc[-2]) if "atr" in df else atr
    vol_ratio = atr20 / atr100 if atr100 > 0 else 1.0

    scores = {"BUY": 0.0, "SELL": 0.0}

    # Man-style multi-speed trend: fast, medium, slow/HTF.
    if ema9 > ema21: scores["BUY"] += 15
    elif ema9 < ema21: scores["SELL"] += 15

    if ema21 > ema50: scores["BUY"] += 15
    elif ema21 < ema50: scores["SELL"] += 15

    if ema50 > ema100 > ema200: scores["BUY"] += 10
    elif ema50 < ema100 < ema200: scores["SELL"] += 10

    if htf50 > htf200: scores["BUY"] += 20
    elif htf50 < htf200: scores["SELL"] += 20

    # AQR-style separation of absolute trend from momentum.
    if roc5 > 0: scores["BUY"] += 5
    elif roc5 < 0: scores["SELL"] += 5
    if roc20 > 0: scores["BUY"] += 10
    elif roc20 < 0: scores["SELL"] += 10

    # Breakout gets full weight only after a completed-candle confirmation.
    buy_breakout = close > breakout_high and breakout_confirmation(df, "BUY", atr)
    sell_breakout = close < breakout_low and breakout_confirmation(df, "SELL", atr)
    if buy_breakout: scores["BUY"] += 15
    elif sell_breakout: scores["SELL"] += 15

    # Volatility regime: usable expansion, but reject extreme/noisy conditions.
    if 0.85 <= vol_ratio <= 1.80:
        if roc20 > 0: scores["BUY"] += 5
        elif roc20 < 0: scores["SELL"] += 5

    # RSI is a confirmation, not the primary signal.
    long_min, long_max, short_min, short_max = get_rsi_settings(epic)
    if long_min <= rsi <= long_max and roc5 >= 0:
        scores["BUY"] += 5
    if short_min <= rsi <= short_max and roc5 <= 0:
        scores["SELL"] += 5

    # Avoid chasing a trend directly into resistance/support unless a true breakout occurred.
    if close >= resistance - 0.25 * atr and close <= resistance and close <= breakout_high:
        scores["BUY"] -= 10
    if close <= support + 0.25 * atr and close >= support and close >= breakout_low:
        scores["SELL"] -= 10

    buy_score = max(0.0, min(100.0, scores["BUY"]))
    sell_score = max(0.0, min(100.0, scores["SELL"]))

    speed_votes_buy = sum([ema9 > ema21, ema21 > ema50, ema50 > ema200, htf50 > htf200])
    speed_votes_sell = sum([ema9 < ema21, ema21 < ema50, ema50 < ema200, htf50 < htf200])
    if speed_votes_buy < 3: buy_score = min(buy_score, 69.0)
    if speed_votes_sell < 3: sell_score = min(sell_score, 69.0)
    log(f"{epic}: SPEED ENSEMBLE | BUY={speed_votes_buy}/4 SELL={speed_votes_sell}/4")

    regime = market_regime(df, htf_df)

    # Regime-aware weighting without adding new indicators.
    if regime == "TREND":
        if htf50 > htf200 and ema9 > ema21 and roc20 > 0:
            buy_score += 4
        if htf50 < htf200 and ema9 < ema21 and roc20 < 0:
            sell_score += 4
    elif regime == "BREAKOUT":
        if buy_breakout and roc5 > 0:
            buy_score += 5
        if sell_breakout and roc5 < 0:
            sell_score += 5
    else:
        if not buy_breakout:
            buy_score = min(buy_score, 78.0)
        if not sell_breakout:
            sell_score = min(sell_score, 78.0)
        if close <= support + SR_BUFFER_ATR * atr and rsi <= RANGE_RSI_BUY_MAX:
            buy_score = max(buy_score, 72.0)
        if close >= resistance - SR_BUFFER_ATR * atr and rsi >= RANGE_RSI_SELL_MIN:
            sell_score = max(sell_score, 72.0)

    adaptive_mult = adaptive_risk_multiplier(df)
    log(f"{epic}: QUANT SCORE | BUY={buy_score:.1f} SELL={sell_score:.1f} REGIME={regime} VOL_RATIO={vol_ratio:.2f} | adaptive_risk={adaptive_mult:.2f}")

    if buy_score >= MIN_ENTRY_SCORE and buy_score > sell_score + 8:
        if ALPHA_ENSEMBLE_ENABLED:
            ok, details = alpha_ensemble_confirmation(df, "BUY")
            log(f"{epic}: ALPHA ENSEMBLE BUY | {details}")
            if not ok:
                return None
        return "BUY"
    if sell_score >= MIN_ENTRY_SCORE and sell_score > buy_score + 8:
        if ALPHA_ENSEMBLE_ENABLED:
            ok, details = alpha_ensemble_confirmation(df, "SELL")
            log(f"{epic}: ALPHA ENSEMBLE SELL | {details}")
            if not ok:
                return None
        return "SELL"
    return None


def generate_signal(df, epic, htf_df=None):
    if not STRATEGY_SELECTOR_ENABLED:
        return quant_signal_score(df, epic, htf_df)
    return quant_signal_score(df, epic, htf_df)


def market_entry_strength(df, htf_df, direction):
    """Independent 0..1 confidence proxy using completed 15m/1h candles.
    This is not a predicted probability of profit.
    """
    if len(df) < 55 or htf_df is None or len(htf_df) < 205:
        return 0.0
    close = df["close"]
    hclose = htf_df["close"]
    last = -2
    atr = safe_float(df["atr"].iloc[last])
    price = safe_float(close.iloc[last])
    if not atr or not price:
        return 0.0
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[last]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[last]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[last]
    h50 = hclose.ewm(span=50, adjust=False).mean().iloc[last]
    h200 = hclose.ewm(span=200, adjust=False).mean().iloc[last]
    roc5 = price - close.iloc[-7]
    sign = 1 if direction == "BUY" else -1
    votes = sum([
        sign * (ema9 - ema21) > 0,
        sign * (ema21 - ema50) > 0,
        sign * (h50 - h200) > 0,
        sign * roc5 > 0,
    ])
    return votes / 4.0


# Cache 15m closes for correlation checks so the 10-second monitor does not
# repeatedly download the same history during one short monitoring window.
CORRELATION_CACHE = {}

def correlation_allows_entry(api, epic, df, positions, signal):
    """Block only when the new trade materially duplicates existing directional risk."""
    if not CORRELATION_FILTER_ENABLED:
        return True

    candidate = df[["close"]].copy()
    if candidate.empty:
        return True
    candidate["ret"] = candidate["close"].astype(float).pct_change()
    candidate_ret = candidate["ret"].dropna().tail(CORRELATION_LOOKBACK)
    if len(candidate_ret) < max(30, CORRELATION_LOOKBACK // 2):
        return True

    open_epics = []
    for position in positions:
        other_epic = position_epic(position)
        other_direction = position_direction(position)
        if other_epic and other_epic != epic and other_direction in ("BUY", "SELL"):
            open_epics.append((other_epic, other_direction))

    checked = set()
    for other_epic, other_direction in open_epics:
        if other_epic in checked:
            continue
        checked.add(other_epic)
        now = time.monotonic()
        cached = CORRELATION_CACHE.get(other_epic)
        if cached and now - cached["time"] < CORRELATION_CACHE_SECONDS:
            other_df = cached["df"]
        else:
            try:
                raw_other = api.get_candles(
                    epic=other_epic,
                    resolution=RESOLUTION,
                    max_candles=max(CANDLE_COUNT, CORRELATION_LOOKBACK + 20),
                )
                other_df = candles_to_dataframe(raw_other)
                CORRELATION_CACHE[other_epic] = {"time": now, "df": other_df}
            except Exception as exc:
                log(f"{epic}: correlation check skipped for {other_epic}; data unavailable: {exc}")
                continue

        if other_df is None or other_df.empty or "close" not in other_df.columns:
            continue
        other_ret = other_df["close"].astype(float).pct_change().dropna().tail(CORRELATION_LOOKBACK)
        joined = pd.concat([candidate_ret.rename("candidate"), other_ret.rename("other")], axis=1).dropna()
        if len(joined) < max(30, CORRELATION_LOOKBACK // 2):
            continue
        corr = safe_float(joined["candidate"].corr(joined["other"]))
        if corr is None:
            continue

        # Positive correlation is relevant when both trades point the same way:
        # BUY+BUY or SELL+SELL concentrates directional exposure. Opposite-side
        # trades are not blocked by this filter merely because markets correlate.
        same_direction = signal == other_direction
        if same_direction and corr >= CORRELATION_THRESHOLD:
            record_entry_rejection(
                epic,
                "HIGH_CORRELATION_EXPOSURE",
                f"candidate={signal} vs {other_epic}={other_direction}; correlation={corr:.3f}; threshold={CORRELATION_THRESHOLD:.2f}",
            )
            return False

        log(
            f"{epic}: CORRELATION CHECK | vs={other_epic} | corr={corr:.3f} | "
            f"candidate={signal} existing={other_direction} | blocked={same_direction and corr >= CORRELATION_THRESHOLD}"
        )
    return True


def calculate_trade(df, direction, entry_price=None, strength=0.75, epic=None):
    # Use only the latest completed 15m candle for volatility.
    current = df.iloc[-2]
    atr = safe_float(current["atr"])
    price = safe_float(entry_price) if entry_price is not None else safe_float(current["close"])
    reference = safe_float(current["close"])
    if price is None or atr is None or atr <= 0 or reference is None:
        return None

    # Avoid chasing a stretched live quote. Recheck on the next scan.
    max_chase_atr = LATE_ENTRY_STRONG_MAX_ATR if strength >= 1.0 else LATE_ENTRY_MAX_ATR
    if direction == "BUY" and price > reference + max_chase_atr * atr:
        if epic:
            record_entry_rejection(
                epic,
                "LATE_ENTRY",
                f"BUY quote={price:.6f}; completed_close={reference:.6f}; distance={(price-reference)/atr:.2f} ATR; limit={max_chase_atr:.2f} ATR",
            )
        return None
    if direction == "SELL" and price < reference - max_chase_atr * atr:
        if epic:
            record_entry_rejection(
                epic,
                "LATE_ENTRY",
                f"SELL quote={price:.6f}; completed_close={reference:.6f}; distance={(reference-price)/atr:.2f} ATR; limit={max_chase_atr:.2f} ATR",
            )
        return None

    # Strong aligned signals receive more volatility room; position sizing
    # automatically shrinks as stop distance widens.
    sl_mult = 1.5 + 0.5 * min(1.0, max(0.0, strength))
    sl_distance = atr * sl_mult
    # No fixed take-profit: existing live profit trail controls the exit.
    profit_level = None
    if direction == "BUY":
        stop_level = price - sl_distance
    elif direction == "SELL":
        stop_level = price + sl_distance
    else:
        return None

    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": profit_level,
        "risk_distance": sl_distance,
        "atr": atr,
        "signal_strength": strength,
    }

def get_positions_for_epic(positions, epic):
    return [p for p in positions if position_epic(p) == epic]

def position_size_value(position):
    return safe_float(position.get("size") or position.get("position", {}).get("size"), 0.0) or 0.0

def estimated_position_risk_account(position, api, account_currency):
    entry, stop, size = position_open_level(position), position_stop_level(position), position_size_value(position)
    if entry is None or stop is None or size <= 0:
        return 0.0
    try:
        market = api.get_market(position_epic(position))
        lot_size = safe_float(market.get("instrument", {}).get("lotSize"), 1.0) or 1.0
        quote_to_account = quote_to_account_rate(market, account_currency)
        if quote_to_account is None:
            return 0.0
        return abs(entry - stop) * size * lot_size * quote_to_account
    except Exception:
        return 0.0

def basket_reserved_risk(api, positions, epic, account_currency):
    return sum(estimated_position_risk_account(p, api, account_currency) for p in get_positions_for_epic(positions, epic))


def portfolio_reserved_risk(api, positions, account_currency):
    return sum(estimated_position_risk_account(p, api, account_currency) for p in positions)

def manage_profit_trailing(api, positions, epic, account_currency):
    """Lock profit after +20 account-currency units; allow an 8-unit pullback."""
    if not PROFIT_TRAIL_ENABLED:
        return

    active_deals = set()
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id:
            continue

        deal_key = str(deal_id)
        active_deals.add(deal_key)
        state = STATE.setdefault("profit_trail", {}).get(deal_key)

        if state is None:
            if pnl >= PROFIT_TRAIL_START:
                STATE["profit_trail"][deal_key] = {
                    "peak_profit": round(float(pnl), 2),
                    "activated": True,
                }
                log(
                    f"{epic}: PROFIT TRAIL ACTIVATED | deal={deal_id} | "
                    f"profit={pnl:.2f} account-currency | floor={pnl - PROFIT_TRAIL_DISTANCE:.2f} account-currency"
                )
            continue

        peak = safe_float(state.get("peak_profit"), pnl) or pnl
        if pnl > peak:
            peak = float(pnl)
            state["peak_profit"] = round(peak, 2)
            log(
                f"{epic}: PROFIT TRAIL MOVED | deal={deal_id} | "
                f"peak={peak:.2f} account-currency | floor={peak - PROFIT_TRAIL_DISTANCE:.2f} account-currency"
            )

        floor = peak - PROFIT_TRAIL_DISTANCE
        if pnl <= floor:
            try:
                response = api.close_position(deal_id)
                log(
                    f"{epic}: PROFIT TRAIL CLOSE | deal={deal_id} | "
                    f"peak={peak:.2f} {account_currency} | current={pnl:.2f} {account_currency} | "
                    f"drop={peak - pnl:.2f} {account_currency} | close_floor={floor:.2f} {account_currency}"
                )
                log(f"{epic}: CLOSE RESPONSE = {response}")
                del STATE["profit_trail"][deal_key]
            except Exception as exc:
                log(f"{epic}: profit-trail close failed | deal={deal_id} | {exc}")

    for deal_key in list(STATE.setdefault("profit_trail", {}).keys()):
        if deal_key not in active_deals:
            del STATE["profit_trail"][deal_key]

    save_state(STATE)

def manage_trailing_stops(api, positions, epic, current_price, df=None):
    """Tighten broker-side SL using completed-candle ATR and market structure.
    Never widen an existing stop; never remove the original broker stop.
    """
    if not TRAILING_ENABLED or df is None or len(df) < 25:
        return
    atr = safe_float(df["atr"].iloc[-2])
    if atr is None or atr <= 0:
        return
    recent = df.iloc[-12:-1]  # completed candles only
    swing_low = safe_float(recent["low"].min())
    swing_high = safe_float(recent["high"].max())
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        direction = position_direction(position)
        entry = position_open_level(position)
        current_sl = position_stop_level(position)
        if not deal_id or direction not in ("BUY", "SELL") or entry is None:
            continue
        stored_risk = original_risk_distance(position)
        if stored_risk is None or stored_risk <= 0:
            continue
        STATE["risk_distance"][str(deal_id)] = stored_risk
        favorable = current_price - entry if direction == "BUY" else entry - current_price
        # Wait for price to move at least 1R before tightening.
        if favorable < stored_risk:
            continue
        volatility_gap = 1.4 * atr
        if direction == "BUY":
            structure_stop = swing_low - 0.15 * atr if swing_low is not None else current_price - volatility_gap
            candidate = max(current_price - volatility_gap, structure_stop)
            # Preserve room for noise and avoid a stop above the market.
            candidate = min(candidate, current_price - 0.75 * atr)
            if current_sl is not None and candidate <= current_sl + 0.05 * atr:
                continue
        else:
            structure_stop = swing_high + 0.15 * atr if swing_high is not None else current_price + volatility_gap
            candidate = min(current_price + volatility_gap, structure_stop)
            candidate = max(candidate, current_price + 0.75 * atr)
            if current_sl is not None and candidate >= current_sl - 0.05 * atr:
                continue
        try:
            api.modify_position(deal_id=deal_id, stop_level=candidate)
            log(f"{epic}: ADAPTIVE SL | {direction} | old={current_sl} | new={candidate} | ATR={atr:.6f}")
        except Exception as exc:
            log(f"{epic}: adaptive SL update rejected; previous broker SL retained | {exc}")
    save_state(STATE)

def cleanup_state(positions):
    active_deals = {str(position_deal_id(p)) for p in positions if position_deal_id(p)}
    changed = False
    for deal_id in list(STATE["risk_distance"].keys()):
        if deal_id not in active_deals:
            del STATE["risk_distance"][deal_id]
            changed = True
    if changed:
        save_state(STATE)

OPEN_POSITION_MONITOR_SECONDS = 10
OPEN_POSITION_MONITOR_WINDOW_SECONDS = 14 * 60
# Prevent the 10-second signal loop from stacking the same profitable-basket
# leg repeatedly; signals are still evaluated every 10 seconds.
PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS = 60
LAST_ENTRY_AT = {}

def monitor_open_positions(api, account_currency, duration_seconds=OPEN_POSITION_MONITOR_WINDOW_SECONDS):
    """Check entry signals and manage open positions every 10 seconds until the next scheduled cycle."""
    started = time.monotonic()
    scan_number = 0
    log(
        f"ENTRY+POSITION MONITOR | interval={OPEN_POSITION_MONITOR_SECONDS}s | "
        f"window={duration_seconds}s | markets={len(EPICS)}"
    )
    while time.monotonic() - started < duration_seconds:
        scan_started = time.monotonic()
        scan_number += 1
        log(f"10s ENTRY SCAN #{scan_number} | checking {len(EPICS)} markets")
        try:
            for epic in EPICS:
                try:
                    positions = api.get_open_positions()
                    balance = api.get_balance()
                    process_epic(
                        api=api,
                        epic=epic,
                        positions=positions,
                        balance=balance,
                        account_currency=account_currency,
                        allow_entry_without_signal=False,
                    )
                except Exception as exc:
                    log(f"{epic}: 10s entry scan error: {exc}")
        except Exception as exc:
            log(f"10s ENTRY SCAN ERROR: {exc}")

        elapsed = time.monotonic() - scan_started
        remaining = duration_seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        sleep_for = min(OPEN_POSITION_MONITOR_SECONDS, remaining)
        # If API processing takes longer than 10 seconds, start the next scan
        # immediately instead of overlapping workflow executions.
        if elapsed >= OPEN_POSITION_MONITOR_SECONDS:
            sleep_for = 0
        if sleep_for > 0:
            time.sleep(sleep_for)
    log("ENTRY+POSITION MONITOR | 15-minute scan window completed.")

def process_epic(api, epic, positions, balance, account_currency, allow_entry_without_signal=True):
    log("")
    if not session_allows_entry() and not get_positions_for_epic(positions, epic):
        log(f"{epic}: liquidity session filter active; no new entry now.")
        return None
    log("=" * 60)
    log(f"PROCESSING {epic}")
    log("=" * 60)
    try:
        raw = api.get_candles(epic=epic, resolution=RESOLUTION, max_candles=CANDLE_COUNT)
        df = candles_to_dataframe(raw)
        if df.empty:
            log(f"{epic}: no candle data.")
            return None
        df = add_indicators(df)
        if len(df) < 3:
            log(f"{epic}: insufficient candles.")
            return None
        # Use the live market snapshot for position management instead of
        # treating the last candle close as the current executable price.
        market = api.get_market(epic)
        snapshot = market.get("snapshot", {}) or {}
        live_bid = safe_float(snapshot.get("bid") if snapshot.get("bid") is not None else market.get("bid"))
        live_offer = safe_float(
            snapshot.get("offer")
            if snapshot.get("offer") is not None
            else snapshot.get("ask")
            if snapshot.get("ask") is not None
            else market.get("offer")
            if market.get("offer") is not None
            else market.get("ask")
        )
        if live_bid is None or live_offer is None or live_bid <= 0 or live_offer <= 0 or live_offer < live_bid:
            log(f"{epic}: invalid live market quote.")
            return None
        current_price = (live_bid + live_offer) / 2.0
        # Position management must continue even when new entries are blocked
        # by daily loss, cooldown, spread, or kill-switch protections.
        # Profit lock is checked before normal entry logic on every bot pass.
        # Hard loss guard runs before all other management so a position cannot
        # remain beyond the configured account-currency loss ceiling.
        enforce_max_position_loss(api, positions, epic, account_currency)
        # Refresh after hard-loss closures so trailing/break-even never tries to
        # modify a position that was already closed in this same cycle.
        positions = api.get_open_positions()
        manage_profit_trailing(api, positions, epic, account_currency)
        breakeven_stops(api, positions, epic, current_price)
        manage_trailing_stops(api, positions, epic, current_price, df=df)

        if not safety_allows_new_entry(balance, positions):
            record_entry_rejection(epic, "SAFETY_STOP")
            return None
        if cooldown_active(epic):
            record_entry_rejection(epic, "LOSS_COOLDOWN")
            return None
        if not spread_allows_entry(api, epic):
            record_entry_rejection(epic, "SPREAD_FILTER")
            return None
        htf_df = candles_to_dataframe(api.get_candles(epic=epic, resolution=HTF_RESOLUTION, max_candles=HTF_CANDLE_COUNT))
        # The selector chooses Trend/Breakout/Range from current market structure.
        signal = generate_signal(df, epic, htf_df)
        epic_positions = get_positions_for_epic(positions, epic)
        if signal is None and not epic_positions:
            log(f"No signal this cycle for {epic}.")
            return None
        if epic_positions:
            directions = {position_direction(p) for p in epic_positions if position_direction(p)}
            if len(directions) != 1:
                log(f"{epic}: mixed-direction basket detected; no new leg.")
                return None
            basket_direction = next(iter(directions))
            if signal is None:
                if not allow_entry_without_signal:
                    log(f"{epic}: no fresh entry signal; managing existing position only.")
                    return None
                signal = basket_direction
            elif signal != basket_direction:
                log(f"{epic}: signal {signal} conflicts with existing basket {basket_direction}; no new leg.")
                return None
        log(f"{epic}: SIGNAL = {signal}")
        if not correlation_allows_entry(api, epic, df, positions, signal):
            return None
        # Execute at the current executable side of the spread:
        # BUY enters at offer/ask, SELL enters at bid.
        execution_price = live_offer if signal == "BUY" else live_bid
        order_spread_pct = market_spread_pct(api.get_market(epic))
        log(f"{epic}: EXECUTABLE QUOTE | {signal}={execution_price} | spread={order_spread_pct:.4f}%" if order_spread_pct is not None else f"{epic}: EXECUTABLE QUOTE | {signal}={execution_price} | spread=N/A")
        strength = market_entry_strength(df, htf_df, signal)
        if strength < 0.75:
            record_entry_rejection(
                epic,
                "WEAK_ALIGNMENT",
                f"strength={strength:.2f}; required=0.75",
            )
            return None
        trade = calculate_trade(df, signal, entry_price=execution_price, strength=strength, epic=epic)
        if trade is None:
            log(f"{epic}: executable price is stretched versus completed candle; wait for next scan.")
            return None
        log(f"{epic}: DYNAMIC PRICE | strength={strength:.2f} | entry={trade['entry']} | SL={trade['stop_level']} | ATR={trade['atr']:.6f}")
        if PRETRADE_COST_FILTER_ENABLED:
            cost_ok, cost_diag = evaluate_pretrade_cost(
                epic=epic,
                market=market,
                entry_price=execution_price,
                risk_distance=trade["risk_distance"],
                execution_quality_file=EXECUTION_QUALITY_FILE,
                max_cost_to_risk=MAX_COST_TO_STOP_RATIO,
                extra_slippage_buffer_pct=EXTRA_SLIPPAGE_BUFFER_PCT,
            )
            log(f"{epic}: PRE-TRADE COST | {cost_diag}")
            if not cost_ok:
                record_entry_rejection(epic, "PRETRADE_COST", cost_diag)
                return None
        sizing_balance = min(float(balance), float(getattr(config, "BALANCE_CAP", balance)))
        existing_count = len(epic_positions)
        if epic_positions:
            last_entry = LAST_ENTRY_AT.get(epic)
            if (
                last_entry is not None
                and time.monotonic() - last_entry < PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS
            ):
                remaining = PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS - (time.monotonic() - last_entry)
                record_entry_rejection(epic, "PROFITABLE_ADD_COOLDOWN", f"remaining={remaining:.0f}s")
                return None
        if existing_count >= MAX_POSITIONS_PER_EPIC:
            record_entry_rejection(epic, "MAX_POSITIONS_PER_EPIC", f"limit={MAX_POSITIONS_PER_EPIC}")
            return None
        reserved_risk = basket_reserved_risk(api, positions, epic, account_currency)
        portfolio_reserved = portfolio_reserved_risk(api, positions, account_currency)
        max_basket_amount = sizing_balance * MAX_BASKET_RISK
        max_portfolio_amount = sizing_balance * MAX_PORTFOLIO_RISK
        remaining_basket_risk = max_basket_amount - reserved_risk
        remaining_portfolio_risk = max_portfolio_amount - portfolio_reserved
        if remaining_portfolio_risk <= 0:
            record_entry_rejection(epic, "PORTFOLIO_RISK_CAP", f"limit={MAX_PORTFOLIO_RISK * 100:.1f}%")
            return None
        leg_multiplier = MARTINGALE_MULTIPLIER ** existing_count if ALLOW_MARTINGALE else 1.0
        if epic_positions:
            requested_risk = sizing_balance * PROFITABLE_ADD_RISK
        else:
            requested_risk = sizing_balance * AGGRESSIVE_BASE_RISK

        risk_multiplier = adaptive_risk_multiplier(df)
        requested_risk *= risk_multiplier
        log(f"{epic}: ADAPTIVE RISK | multiplier={risk_multiplier:.2f} | requested={requested_risk:.2f}")
        # Hard cap: every new position may risk at most 3 AED.
        # This caps position sizing as well as the secondary loss guard below.
        base_risk_amount = min(
            requested_risk,
            max(0.0, remaining_basket_risk),
            max(0.0, remaining_portfolio_risk),
            MAX_LOSS_PER_POSITION,
        )
        if base_risk_amount <= 0:
            risk_amount = 0.0
        else:
            risk_positions = []
            for p in positions:
                rp = dict(p)
                rp["risk_amount_account"] = estimated_position_risk_account(p, api, account_currency)
                risk_positions.append(rp)
            portfolio_mult, portfolio_diag = portfolio_risk_overlay(
                api=api,
                candidate_epic=epic,
                existing_positions=risk_positions,
                candidate_risk_amount=base_risk_amount,
                target_vol=PORTFOLIO_VOL_TARGET_ANNUAL,
                lookback=PORTFOLIO_COV_LOOKBACK,
                min_multiplier=PORTFOLIO_RISK_MIN_MULTIPLIER,
                max_multiplier=PORTFOLIO_RISK_MAX_MULTIPLIER,
            )
            log(f"{epic}: PORTFOLIO RISK OVERLAY | multiplier={portfolio_mult:.3f} | {portfolio_diag}")
            risk_amount = base_risk_amount * portfolio_mult
        if risk_amount <= 0:
            record_entry_rejection(epic, "BASKET_RISK_CAP", f"limit={MAX_BASKET_RISK * 100:.1f}%")
            return None
        if epic_positions:
            profitable_position = False
            for position in epic_positions:
                entry = position_open_level(position)
                direction = position_direction(position)
                if entry is None or direction != signal:
                    continue
                if (signal == "BUY" and current_price > entry) or (signal == "SELL" and current_price < entry):
                    profitable_position = True
                    break

            if ADD_TO_PROFITABLE_BASKET and profitable_position:
                log(f"{epic}: profitable basket detected; adding next leg up to max {MAX_POSITIONS_PER_EPIC}.")
            else:
                # Never add to a losing/flat basket. Grid, averaging, and
                # martingale are disabled; extra legs are allowed only when
                # an existing position in the same direction is profitable.
                record_entry_rejection(epic, "BASKET_NOT_PROFITABLE")
                return None
        size = get_position_size(api, epic, risk_amount, trade["risk_distance"], account_currency)
        if size is None:
            record_entry_rejection(epic, "MIN_TRADE_SIZE_EXCEEDS_RISK")
            return None
        log(f"{epic}: risk budget={risk_amount:.2f}; entry={trade['entry']}; SL={trade['stop_level']}; TP={trade['profit_level']}; size={size}")
        if DEMO_ONLY and str(getattr(config, "IS_DEMO", "true")).lower() not in ("true", "1", "yes"):
            raise RuntimeError("DEMO_ONLY=True but IS_DEMO is not enabled.")
        response = api.place_order(direction=signal, size=size, stop_level=trade["stop_level"], profit_level=trade["profit_level"], epic=epic)
        log(f"{epic}: ORDER SENT")
        log(f"{epic}: {response}")

        # Capital.com documents that a successful POST /positions response is
        # not by itself proof that the position was opened. Confirm the deal.
        deal_reference = response.get("dealReference") if isinstance(response, dict) else None
        actual_fill_price = None
        deal_id = None
        deal_status = "UNCONFIRMED"
        if deal_reference:
            confirmation = api.get_confirmation(deal_reference)
            log(f"{epic}: DEAL CONFIRMATION = {confirmation}")
            deal_status = str(confirmation.get("dealStatus") or confirmation.get("status") or "").upper()
            if deal_status in {"REJECTED", "FAILED"}:
                record_execution_quality(epic, signal, execution_price, None, order_spread_pct, deal_reference=deal_reference, deal_status=deal_status, size=size)
                raise RuntimeError(f"Capital.com rejected deal {deal_reference}: {confirmation}")
            deal_id = confirmation.get("dealId") or confirmation.get("dealReference")
            actual_fill_price = _confirmed_entry_level(confirmation)
            if actual_fill_price is None and deal_id:
                try:
                    confirmed_positions = api.get_open_positions()
                    for opened in confirmed_positions:
                        if str(position_deal_id(opened)) == str(deal_id):
                            actual_fill_price = position_open_level(opened)
                            break
                except Exception as fill_exc:
                    log(f"{epic}: fill-price lookup failed: {fill_exc}")
            record_execution_quality(epic, signal, execution_price, actual_fill_price, order_spread_pct, deal_reference=deal_reference, deal_id=deal_id, deal_status=deal_status or "ACCEPTED", size=size)
        else:
            log(f"{epic}: WARNING - no dealReference returned; order confirmation unavailable.")
            record_execution_quality(epic, signal, execution_price, None, order_spread_pct, deal_status="UNCONFIRMED", size=size)

        SAFETY["consecutive_errors"] = 0
        LAST_ENTRY_AT[epic] = time.monotonic()
        save_safety_state(SAFETY)
        return response
    except Exception as exc:
        SAFETY["consecutive_errors"] = int(SAFETY.get("consecutive_errors", 0)) + 1
        save_safety_state(SAFETY)
        log(f"{epic}: ERROR ({SAFETY['consecutive_errors']}/{MAX_CONSECUTIVE_ERRORS}): {exc}")
        traceback.print_exc()
        return None

def update_loss_cooldowns_from_history(api):
    """Use Capital.com history to pause an epic after a recently closed loss.
    This uses Capital.com's own API only; no paid external service is required.
    """
    try:
        now = datetime.now(timezone.utc)
        # Only inspect the cooldown window itself. A fixed one-hour lookback
        # would repeatedly reset the same loss and extend the cooldown forever.
        start = now.timestamp() - (LOSS_COOLDOWN_MINUTES * 60)
        from_date = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        to_date = now.strftime("%Y-%m-%dT%H:%M:%S")
        transactions = api.get_transactions(from_date, to_date)
        for tx in transactions:
            note = str(tx.get("note") or tx.get("description") or tx.get("transactionType") or "").lower()
            if "close" not in note and "closed" not in note:
                continue
            epic = tx.get("instrumentName") or tx.get("epic")
            pnl = direct_transaction_pnl(tx)
            if epic and pnl is not None and pnl < 0 and epic in EPICS:
                set_loss_cooldown(epic)
                log(f"{epic}: recent closed loss detected ({pnl}); cooldown applied for {LOSS_COOLDOWN_MINUTES}m.")
    except Exception as exc:
        log(f"Loss-history check unavailable; continuing safely: {exc}")

def direct_transaction_pnl(tx):
    """Read P/L only when Capital.com returns it explicitly on the transaction.

    No nested-field guessing and no reconstruction from price/size is used.
    """
    return safe_float(_first_value(
        tx.get("profitAndLoss"),
        tx.get("profitLoss"),
        tx.get("realizedProfitLoss"),
        tx.get("realisedProfitLoss"),
        tx.get("realizedPnl"),
        tx.get("realisedPnl"),
    ))


def get_broker_account_profit_loss(api):
    """Return the broker-reported account P/L from GET /accounts."""
    accounts = api.get_accounts()
    account = next((a for a in accounts if a.get("accountId") == api.account_id), None)
    if account is None:
        raise RuntimeError(f"Selected Capital.com account {api.account_id} was not found.")
    balance = account.get("balance") or {}
    pnl = safe_float(balance.get("profitLoss"))
    currency = str(balance.get("currency") or account.get("currency") or "").upper()
    return pnl, currency



def get_closed_trade_report(api, from_date, to_date):
    """Build a report without inventing realized P/L."""
    transactions = api.get_transactions(from_date, to_date)
    wins = losses = flat = unknown_pnl = closed = 0
    total_pnl = total_wins = total_losses = 0.0

    for tx in transactions:
        transaction_type = str(tx.get("transactionType") or "").upper()
        note = str(tx.get("note") or tx.get("description") or transaction_type or "").lower()
        if not ("CLOSE" in transaction_type or "CLOSED" in transaction_type or "close" in note or "closed" in note):
            continue
        closed += 1
        pnl = direct_transaction_pnl(tx)
        if pnl is None:
            unknown_pnl += 1
            continue
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            total_wins += pnl
        elif pnl < 0:
            losses += 1
            total_losses += pnl
        else:
            flat += 1

    broker_pnl, broker_currency = get_broker_account_profit_loss(api)
    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "unknown_pnl": unknown_pnl,
        "total_pnl": round(total_pnl, 2),
        "total_wins": round(total_wins, 2),
        "total_losses": round(total_losses, 2),
        "broker_account_pnl": broker_pnl,
        "broker_currency": broker_currency,
    }


def save_live_stats(api, account_currency):
    """Persist broker-direct statistics; never infer trade P/L."""
    try:
        today = utc_day()
        now = datetime.now(timezone.utc)
        transactions = api.get_transactions(f"{today}T00:00:00", now.strftime("%Y-%m-%dT%H:%M:%S"))
        closed_rows = []
        unknown_closed = 0
        for tx in transactions:
            transaction_type = str(tx.get("transactionType") or "").upper()
            note = str(tx.get("note") or tx.get("description") or transaction_type or "").lower()
            if not ("CLOSE" in transaction_type or "CLOSED" in transaction_type or "close" in note or "closed" in note):
                continue
            pnl = direct_transaction_pnl(tx)
            if pnl is None:
                unknown_closed += 1
                continue
            epic = tx.get("epic") or tx.get("instrumentName") or "UNKNOWN"
            closed_rows.append((str(epic), float(pnl)))

        open_positions = api.get_open_positions()
        broker_pnl, broker_currency = get_broker_account_profit_loss(api)
        winners = [p for _, p in closed_rows if p > 0]
        losers = [p for _, p in closed_rows if p < 0]
        by_epic = {}
        for epic in EPICS:
            vals = [p for e, p in closed_rows if e == epic]
            by_epic[epic] = {
                "trades_with_explicit_pnl": len(vals),
                "winners": sum(1 for p in vals if p > 0),
                "losers": sum(1 for p in vals if p < 0),
                "win_rate": round(sum(1 for p in vals if p > 0) / len(vals) * 100, 1) if vals else 0,
                "pnl": round(sum(vals), 2),
            }

        closed_pnls = [p for _, p in closed_rows]
        stats = {
            "last_updated": now.isoformat(),
            "currency": broker_currency or account_currency,
            "source": "Capital.com API /accounts + /history/transactions",
            "pnl_policy": "Broker-reported only; no inferred or reconstructed trade P/L",
            "broker_account_profit_loss": broker_pnl,
            "overall": {
                "open_trades": len(open_positions),
                "closed_trades_seen": len(closed_pnls) + unknown_closed,
                "closed_trades_with_explicit_pnl": len(closed_pnls),
                "closed_trades_without_explicit_pnl": unknown_closed,
                "winners": len(winners),
                "losers": len(losers),
                "win_rate": round(len(winners) / len(closed_pnls) * 100, 1) if closed_pnls else 0,
                "explicit_closed_trade_pnl": round(sum(closed_pnls), 2),
                "broker_account_profit_loss": broker_pnl,
                "best_trade": round(max(closed_pnls), 2) if closed_pnls else None,
                "worst_trade": round(min(closed_pnls), 2) if closed_pnls else None,
                "average_win": round(sum(winners) / len(winners), 2) if winners else None,
                "average_loss": round(sum(losers) / len(losers), 2) if losers else None,
            },
            "by_epic": by_epic,
        }
        with open("stats.json", "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2)
        log(f"STATS SAVED | broker account P/L={broker_pnl:.2f} {broker_currency or account_currency} | closed seen={len(closed_pnls)+unknown_closed} | explicit trade P/L={len(closed_pnls)} | unknown={unknown_closed}")
    except Exception as exc:
        log(f"Live stats save failed; continuing safely: {exc}")


def log_trade_report(api, account_currency):
    try:
        today = utc_day()
        now = datetime.now(timezone.utc)
        report = get_closed_trade_report(api, f"{today}T00:00:00", now.strftime("%Y-%m-%dT%H:%M:%S"))
        broker_currency = report["broker_currency"] or account_currency
        broker_pnl = report["broker_account_pnl"]
        broker_pnl_text = f"{broker_pnl:.2f} {broker_currency}" if broker_pnl is not None else "UNAVAILABLE"
        log(
            f"TRADE REPORT | today={today} | closed seen={report['closed']} | "
            f"closed with explicit P/L={report['closed'] - report['unknown_pnl']} | "
            f"unknown P/L={report['unknown_pnl']} | broker account P/L={broker_pnl_text}"
        )
    except Exception as exc:
        log(f"Trade report unavailable; continuing safely: {exc}")

def run_cycle():
    log("Starting trading cycle...")
    log("DEMO MODE / LIVE TRADING DISABLED")
    log(f"Safety: daily loss={DAILY_LOSS_LIMIT_PCT*100:.1f}%, equity drawdown={EQUITY_DRAWDOWN_LIMIT_PCT*100:.1f}%, spread filter={SPREAD_FILTER_ENABLED}, breakeven={BREAKEVEN_ENABLED}, cooldown={LOSS_COOLDOWN_MINUTES}m, kill switch={KILL_SWITCH_ENABLED}.")
    log(f"Strategy Selector: enabled={STRATEGY_SELECTOR_ENABLED} | regimes=TREND/BREAKOUT/RANGE | Trend gap={TREND_EMA_GAP_ATR}ATR | Range gap<{RANGE_EMA_GAP_ATR}ATR.")
    log(f"Controlled aggressive mode: Grid={ALLOW_GRID}, Averaging={ALLOW_AVERAGING}, Martingale={ALLOW_MARTINGALE}; profitable-basket add={ADD_TO_PROFITABLE_BASKET}, max positions/epic={MAX_POSITIONS_PER_EPIC}, grid step={GRID_STEP_R}R, martingale x{MARTINGALE_MULTIPLIER}, max basket risk={MAX_BASKET_RISK * 100:.1f}%.")
    api = CapitalAPI()
    log("Logging in to Capital.com...")
    api.login()
    balance = api.get_balance()
    account_currency = api.get_account_currency()
    log(f"Account balance: {balance} {account_currency}")

    # A successful login/account read proves the API is healthy. Do not let
    # stale errors from an earlier bot run permanently block this run.
    if int(SAFETY.get("consecutive_errors", 0)) > 0:
        SAFETY["consecutive_errors"] = 0
        save_safety_state(SAFETY)

    reset_daily_safety(balance)
    update_loss_cooldowns_from_history(api)
    log_trade_report(api, account_currency)
    for cycle_epic in EPICS:
        # Refresh account state before EVERY epic so newly opened/closed positions
        # are immediately reflected in subsequent decisions within this run.
        positions = api.get_open_positions()
        log(f"Open positions before {cycle_epic}: {len(positions)}")
        log_and_save_open_positions(positions)
        cleanup_state(positions)
        # Refresh balance before EVERY epic so risk sizing reflects any
        # positions opened/closed earlier in this same cycle.
        balance = api.get_balance()
        log(f"Refreshed balance before {cycle_epic}: {balance} {account_currency}")
        process_epic(
            api=api,
            epic=cycle_epic,
            positions=positions,
            balance=balance,
            account_currency=account_currency,
        )
        time.sleep(1)
    # Refresh the persisted report after all markets have been processed so
    # closures that happened during this cycle are included.
    # Keep managing any open positions every 10 seconds until the next
    # scheduled 15-minute entry scan. New entries are NOT rescanned here.
    monitor_open_positions(api, account_currency)
    save_live_stats(api, account_currency)
    log("Trading cycle completed.")

if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        log(f"MAIN ERROR: {exc}")
        traceback.print_exc()