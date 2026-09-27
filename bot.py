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
import ai_engine
import ai_pipeline
import ai_outcomes
import capital_news
import professional_ai_monitor
from capital_websocket import CapitalLivePriceStream

DEMO_ONLY = True
STRATEGY_ID = "CAPITAL_FX_AI"
POSITION_OWNERSHIP_FILE = "fx_ai_strategy_positions.json"
LEGACY_POSITION_OWNERSHIP_FILE = "strategy_positions.json"
ALLOW_GRID = False
ALLOW_MARTINGALE = False
ALLOW_AVERAGING = False

STRONG_SIGNAL_MIN_CONFIDENCE = 0.80
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
MAX_PORTFOLIO_RISK = getattr(config, "MAX_PORTFOLIO_RISK", 0.09)
PORTFOLIO_VOL_TARGET_ANNUAL = getattr(config, "PORTFOLIO_VOL_TARGET_ANNUAL", 0.10)
PORTFOLIO_RISK_MIN_MULTIPLIER = getattr(config, "PORTFOLIO_RISK_MIN_MULTIPLIER", 0.35)
PORTFOLIO_RISK_MAX_MULTIPLIER = getattr(config, "PORTFOLIO_RISK_MAX_MULTIPLIER", 1.00)
PORTFOLIO_COV_LOOKBACK = getattr(config, "PORTFOLIO_COV_LOOKBACK", 192)
MAX_COST_TO_STOP_RATIO = getattr(config, "MAX_COST_TO_STOP_RATIO", 0.40)
EXTRA_SLIPPAGE_BUFFER_PCT = getattr(config, "EXTRA_SLIPPAGE_BUFFER_PCT", 0.01)
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

# Dynamic profit protection. It tracks every profitable position from the
# first positive broker-reported P/L and computes the protected floor from the
# live peak. No fixed +profit activation/close amount is used.
PROFIT_TRAIL_ENABLED = True

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
ADD_TO_PROFITABLE_BASKET = False
# Smaller incremental risk for additional legs while the existing basket is profitable.
PROFITABLE_ADD_RISK = 0.002

# Free, local risk/execution protections (no external paid service).
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_PCT = 0.15
EXECUTION_QUALITY_ENABLED = True
EXECUTION_QUALITY_FILE = "fx_ai_execution_quality.json"
MAX_ACCEPTABLE_SLIPPAGE_PCT = 0.03

# Entry-quality upgrades: allow a little more room for normal execution lag,
# but block entries that are materially stretched or over-correlated with
# existing exposure. All rejections are persisted with an exact reason.
LATE_ENTRY_MAX_ATR = 0.50
LATE_ENTRY_STRONG_MAX_ATR = 0.75
ENTRY_REJECTION_FILE = "fx_ai_entry_rejections.json"
ENTRY_REJECTION_MAX_ROWS = 1000

DAILY_LOSS_LIMIT_AED = 300.0  # Daily entry-stop threshold for AED demo accounts
DAILY_LOSS_LIMIT_PCT = 0.03  # Fallback for non-AED accounts
EQUITY_DRAWDOWN_LIMIT_PCT = 0.05
LOSS_COOLDOWN_MINUTES = 3
SIDEWAYS_ATR_RATIO_MAX = 0.90
BREAKEVEN_ENABLED = True
# Do not move to break-even too early; allow normal market pullbacks first.
BREAKEVEN_START_R = 1.25
BREAKEVEN_OFFSET_R = 0.10
KILL_SWITCH_ENABLED = True
MAX_CONSECUTIVE_ERRORS = 3
# Error isolation: one broken/unavailable epic must never disable entries on
# unrelated markets. Critical failures are tracked per epic for this run.
UNAVAILABLE_EPICS = set()
ERROR_STREAKS = {}
SAFETY_STATE_FILE = "fx_ai_bot_safety_state.json"

# Conservative strategy-quality upgrades.
WEEKEND_FILTER_ENABLED = getattr(config, "WEEKEND_FILTER_ENABLED", True)
ADAPTIVE_RISK_ENABLED = getattr(config, "ADAPTIVE_RISK_ENABLED", True)
ADAPTIVE_RISK_HIGH_VOL_1 = getattr(config, "ADAPTIVE_RISK_HIGH_VOL_1", 1.25)
ADAPTIVE_RISK_HIGH_VOL_2 = getattr(config, "ADAPTIVE_RISK_HIGH_VOL_2", 1.50)
ADAPTIVE_RISK_LOW_VOL = getattr(config, "ADAPTIVE_RISK_LOW_VOL", 0.75)
BREAKOUT_CONFIRM_ATR = getattr(config, "BREAKOUT_CONFIRM_ATR", 0.05)
MIN_ENTRY_SCORE = None  # Legacy compatibility only; active gate is dynamic_entry_score_floor().
# Active AI bots use their profile-specific confidence floor.
MIN_ENTRY_STRENGTH = None  # Legacy compatibility only; active gate is dynamic_entry_policy().

MIN_TRADE_SIZE = getattr(config, "MIN_TRADE_SIZE", {"GOLD": 0.01, "EURUSD": 0.01, "SILVER": 1.0, "OIL_CRUDE": 0.01, "US100": 0.01, "US500": 0.01})
STATE_FILE = "fx_ai_trades_state.json"
OPEN_POSITIONS_FILE = "fx_ai_open_positions.json"

def log(message):
    print(f"[BOT] {message}")

def _load_owned_deals():
    if not os.path.exists(POSITION_OWNERSHIP_FILE):
        return set()
    try:
        with open(POSITION_OWNERSHIP_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return {str(x) for x in data.get(STRATEGY_ID, [])} if isinstance(data, dict) else set()
    except Exception as exc:
        log(f"Could not load position ownership: {exc}")
        return set()

def _ownership_initialized():
    if not os.path.exists(POSITION_OWNERSHIP_FILE):
        return False
    try:
        with open(POSITION_OWNERSHIP_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return isinstance(data, dict) and STRATEGY_ID in data
    except Exception:
        return False

def _save_owned_deals(deals):
    try:
        data = {}
        if os.path.exists(POSITION_OWNERSHIP_FILE):
            with open(POSITION_OWNERSHIP_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
        if not isinstance(data, dict): data = {}
        data[STRATEGY_ID] = sorted({str(x) for x in deals})
        temp_file = f"{POSITION_OWNERSHIP_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temp_file, POSITION_OWNERSHIP_FILE)
    except Exception as exc:
        log(f"Could not save position ownership: {exc}")

def _load_legacy_owned_deals():
    if not os.path.exists(LEGACY_POSITION_OWNERSHIP_FILE):
        return set()
    try:
        with open(LEGACY_POSITION_OWNERSHIP_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return set()
        return {str(x) for x in data.get(STRATEGY_ID, [])}
    except Exception as exc:
        log(f"Could not load legacy position ownership: {exc}")
        return set()

def filter_owned_positions(positions):
    # Each strategy has its own ownership registry. On first use, migrate only
    # that strategy's entries from the old shared registry; never adopt every
    # open account position.
    if not os.path.exists(POSITION_OWNERSHIP_FILE):
        legacy_owned = _load_legacy_owned_deals()
        _save_owned_deals(legacy_owned)
    owned = _load_owned_deals()
    return [p for p in positions if position_deal_id(p) and str(position_deal_id(p)) in owned]

def _confirmed_position_direction(confirmation, confirmed_positions=None, deal_id=None):
    """Return the broker-confirmed direction for an opened deal."""
    if isinstance(confirmation, dict):
        direct = normalize_direction(_first_value(
            confirmation.get("direction"),
            confirmation.get("dealDirection"),
            confirmation.get("positionDirection"),
        ))
        if direct in {"BUY", "SELL"}:
            return direct
        affected = confirmation.get("affectedDeals")
        if isinstance(affected, list):
            for item in affected:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("dealId")
                if deal_id is not None and item_id is not None and str(item_id) != str(deal_id):
                    continue
                direct = normalize_direction(_first_value(
                    item.get("direction"), item.get("dealDirection"), item.get("positionDirection")
                ))
                if direct in {"BUY", "SELL"}:
                    return direct
    for position in confirmed_positions or []:
        if deal_id is not None and str(position_deal_id(position)) != str(deal_id):
            continue
        direct = position_direction(position)
        if direct in {"BUY", "SELL"}:
            return direct
    return None


def register_owned_position(deal_id):
    if deal_id:
        owned = _load_owned_deals()
        owned.add(str(deal_id))
        _save_owned_deals(owned)


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

def safety_allows_new_entry(balance, positions, epic=None, account_currency=None):
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
    # Use the live broker account currency, not a static/missing config value.
    currency = str(account_currency or getattr(config, "ACCOUNT_CURRENCY", "AED")).upper()
    daily_loss_limit = DAILY_LOSS_LIMIT_AED if currency == "AED" else start_balance * DAILY_LOSS_LIMIT_PCT
    daily_floor = start_balance - daily_loss_limit
    drawdown_floor = peak * (1.0 - EQUITY_DRAWDOWN_LIMIT_PCT)
    # All Capital bots use one hard daily loss limit for new-entry safety.
    # For AED accounts this is a fixed 300 AED per UTC day. Do not trigger
    # SAFETY_STOP from the separate percentage peak-equity drawdown gate.
    # Use equity for the daily-loss stop so floating losses cannot bypass
    # the entry kill-switch merely because the broker balance excludes
    # unrealized P/L. Balance is still logged for audit/debugging.
    if equity <= daily_floor:
        log(
            f"SAFETY STOP: new entries disabled | balance={balance:.2f} "
            f"equity={equity:.2f} day_floor={daily_floor:.2f} "
            f"daily_loss_limit={daily_loss_limit:.2f}"
        )
        return False
    # Error isolation: the old global counter allowed one bad market/API
    # response to shut down every other market. Gate only the affected epic.
    if epic is not None and int(ERROR_STREAKS.get(epic, 0)) >= MAX_CONSECUTIVE_ERRORS:
        log(f"KILL SWITCH: {epic} has {ERROR_STREAKS[epic]} consecutive critical errors; this epic is disabled for this run.")
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

def spread_allows_entry(api, epic, market=None, ai_decision=None):
    """AI-owned spread decision.
    
    The ML decision engine controls whether the current spread is acceptable.
    Broker quote validity and broker TRADEABLE status remain mandatory fail-closed
    conditions. The legacy fixed MAX_SPREAD_PCT is retained only as a diagnostic
    reference, not as an independent entry veto.
    """
    if not SPREAD_FILTER_ENABLED:
        return True
    market = market if market is not None else api.get_market(epic)
    spread = market_spread_pct(market)
    if spread is None:
        log(f"{epic}: spread unavailable; AI cannot evaluate execution cost; entry blocked.")
        return False
    # AI is optional. When AI trading is disabled, use the broker-safe
    # legacy spread cap so the base strategy can continue to evaluate entries.
    if ai_decision is None or not ai_decision.get("enabled"):
        accepted = spread <= MAX_SPREAD_PCT
        log(
            f"{epic}: STRATEGY SPREAD DECISION | spread={spread:.4f}% | "
            f"cap={MAX_SPREAD_PCT:.4f}% | accepted={accepted}"
        )
        return accepted

    confidence = float(ai_decision.get("confidence", 0.0) or 0.0)
    signal = ai_decision.get("signal")
    advanced = ai_decision.get("advanced_ai") if isinstance(ai_decision.get("advanced_ai"), dict) else {}
    uncertainty = float(advanced.get("enhanced_uncertainty", 0.0) or 0.0)
    regime = str((advanced.get("regime") or {}).get("regime") or "UNKNOWN").upper()
    cost = advanced.get("execution_cost") if isinstance(advanced.get("execution_cost"), dict) else {}
    total_price_cost = safe_float(cost.get("total_price_cost"))
    
    # AI dynamically sets the spread tolerance from its confidence, uncertainty,
    # regime and estimated execution cost. It may accept a wider-than-normal
    # spread when the model sees sufficient edge, or reject a normally-small
    # spread when confidence/conditions are poor.
    dynamic_cap = 0.08 + 0.42 * max(0.0, min(1.0, confidence))
    dynamic_cap *= max(0.35, 1.0 - 0.60 * max(0.0, min(1.0, uncertainty)))
    if regime in {"TREND", "BREAKOUT"}:
        dynamic_cap *= 1.10
    elif regime == "RANGE":
        dynamic_cap *= 0.90
    dynamic_cap = max(0.03, min(0.50, dynamic_cap))

    # If the AI execution-cost model has a usable estimate, require the spread
    # to remain below the AI-derived tolerance. Otherwise use the model's
    # confidence/uncertainty decision alone.
    # Normalize AI cost from price units into percentage units before comparing
    # it with spread, which is already a percentage. The old comparison could
    # reject valid BTC/FX entries by dividing price units by percentage points.
    cost_ratio = None
    cost_pct = None
    if total_price_cost is not None and total_price_cost >= 0:
        snapshot = market.get("snapshot", {}) or {}
        reference_price = safe_float(
            snapshot.get("offer")
            or snapshot.get("ask")
            or market.get("offer")
            or market.get("ask")
        )
        if reference_price is not None and reference_price > 0:
            cost_pct = (total_price_cost / reference_price) * 100.0
            if spread > 0:
                cost_ratio = cost_pct / spread

    accepted = (
        signal in {"BUY", "SELL"}
        and confidence >= float(ai_decision.get("required_confidence", 0.0) or 0.0)
        and uncertainty <= 0.85
        and spread <= dynamic_cap
    )
    # Preserve the 2.5x sanity check, but now both values use percentage units.
    if cost_ratio is not None and cost_ratio > 2.5:
        accepted = False

    log(
        f"{epic}: AI SPREAD DECISION | signal={signal} | confidence={confidence:.3f} | "
        f"uncertainty={uncertainty:.3f} | regime={regime} | spread={spread:.4f}% | "
        f"AI_CAP={dynamic_cap:.4f}% | cost={total_price_cost} | cost_pct={cost_pct} | cost/spread={cost_ratio} | "
        f"legacy_cap={MAX_SPREAD_PCT:.4f}% | accepted={accepted}"
    )
    return accepted

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


def reload_runtime_state():
    """Reload strategy-specific state after a wrapper selects its state files."""
    global STATE, SAFETY
    STATE = load_state()
    SAFETY = load_safety_state()

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
    raw = _first_value(
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
    )
    # Missing broker P/L is unknown, not zero. Preserve the last valid reading
    # in position telemetry so profit protection never treats missing data as
    # a fresh flat/profit reset.
    if raw is None or safe_float(raw, None) is None:
        return None
    return safe_float(raw, None)

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
    """Close any position whose current P/L reaches the hard account-currency loss cap.
    Returns True only when a close was actually sent, so callers can refresh
    broker positions only when the safety guard changed account state.
    """
    closed = False
    if MAX_LOSS_PER_POSITION is None or MAX_LOSS_PER_POSITION <= 0:
        return closed
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id or pnl > -MAX_LOSS_PER_POSITION:
            continue
        try:
            response = api.close_position(deal_id)
            closed = True
            log(
                f"{epic}: HARD LOSS LIMIT CLOSE | deal={deal_id} | "
                f"P/L={pnl:.2f} {account_currency} | limit=-{MAX_LOSS_PER_POSITION:.2f} {account_currency}"
            )
            log(f"{epic}: HARD LOSS CLOSE RESPONSE = {response}")
        except Exception as exc:
            log(f"{epic}: hard loss close failed | deal={deal_id} | {exc}")
    return closed


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


def get_position_size(api, epic, risk_amount_account, risk_distance, account_currency, market=None):
    risk_amount_account = safe_float(risk_amount_account)
    risk_distance = safe_float(risk_distance)
    if risk_amount_account is None or risk_amount_account <= 0 or risk_distance is None or risk_distance <= 0:
        return None
    # Reuse the live market snapshot already fetched by process_epic.
    # This removes a duplicate market API request on every candidate entry.
    market = market if market is not None else api.get_market(epic)
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

CANDLE_CACHE_DEFAULT_TTL_SECONDS = 2.0
# Entry decisions must always use a fresh broker candle response.
ENTRY_CANDLE_CACHE_TTL_SECONDS = 0.0
STRATEGY_CANDLE_RESOLUTION_SECONDS = 900

def get_cached_candles(api, epic, resolution, max_candles, cache=None, ttl_seconds=CANDLE_CACHE_DEFAULT_TTL_SECONDS):
    """Short-lived historical-candle cache. Live quotes remain uncached."""
    if cache is None:
        return candles_to_dataframe(api.get_candles(epic=epic, resolution=resolution, max_candles=max_candles))
    now = time.monotonic()
    key = (epic, resolution, int(max_candles))
    item = cache.get(key)
    if item is not None and now - item[0] < max(0.0, float(ttl_seconds)):
        return item[1].copy()
    df = candles_to_dataframe(api.get_candles(epic=epic, resolution=resolution, max_candles=max_candles))
    cache[key] = (now, df.copy())
    return df

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
    # Never trust API response order for candle recency.
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = (
        df.dropna(subset=["time", "open", "high", "low", "close"])
          .drop_duplicates(subset=["time"], keep="last")
          .sort_values("time")
          .reset_index(drop=True)
    )
    return df

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


def pullback_confirmation_score(df, direction, atr):
    """Score a pullback/reclaim setup without blocking an otherwise valid signal.

    Returns 0..5. This is a quality bonus only: it never rejects a trade.
    """
    if len(df) < 30 or atr is None or atr <= 0:
        return 0.0
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    close = safe_float(cur["close"])
    ema21 = float(df["close"].ewm(span=21, adjust=False).mean().iloc[-2])
    ema50 = float(df["close"].ewm(span=50, adjust=False).mean().iloc[-2])
    if close is None:
        return 0.0
    if direction == "BUY":
        touched = float(cur["low"]) <= ema21 + 0.20 * atr or float(prev["low"]) <= ema21 + 0.20 * atr
        reclaimed = close > ema21 and close > float(prev["close"])
        aligned = ema21 > ema50
    else:
        touched = float(cur["high"]) >= ema21 - 0.20 * atr or float(prev["high"]) >= ema21 - 0.20 * atr
        reclaimed = close < ema21 and close < float(prev["close"])
        aligned = ema21 < ema50
    return 5.0 if touched and reclaimed and aligned else 0.0


def breakout_quality_score(df, direction, atr):
    """Score breakout strength without blocking an otherwise valid signal.

    Returns 0..5 based on candle body, close location and follow-through.
    """
    if len(df) < BREAKOUT_LOOKBACK + 4 or atr is None or atr <= 0:
        return 0.0
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    body = abs(float(cur["close"]) - float(cur["open"]))
    candle_range = max(float(cur["high"]) - float(cur["low"]), 1e-12)
    body_ratio = body / candle_range
    if direction == "BUY":
        close_location = (float(cur["close"]) - float(cur["low"])) / candle_range
        follow = float(cur["close"]) > float(prev["close"])
    else:
        close_location = (float(cur["high"]) - float(cur["close"])) / candle_range
        follow = float(cur["close"]) < float(prev["close"])
    quality = 0.0
    if body_ratio >= 0.55:
        quality += 2.0
    if close_location >= 0.70:
        quality += 2.0
    if follow:
        quality += 1.0
    return min(5.0, quality)


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



def dynamic_entry_score_floor(regime, vol_ratio, news_buy=0.0, news_sell=0.0):
    """Adaptive score floor from market regime, volatility and cached news stress."""
    regime = str(regime or "RANGE").upper()
    floor = {"TREND": 45.0, "BREAKOUT": 52.0, "RANGE": 62.0}.get(regime, 58.0)
    vol = safe_float(vol_ratio)
    if vol is not None:
        if vol < 0.85:
            floor += 5.0
        elif vol > 1.50:
            floor += 8.0
        elif vol > 1.25:
            floor += 3.0
    if max(abs(float(news_buy or 0.0)), abs(float(news_sell or 0.0))) >= 1.0:
        floor += 8.0
    return float(np.clip(floor, 45.0, 80.0))


def dynamic_entry_policy(df, htf_df, epic, direction, strength):
    """Return adaptive strength floor and position capacity for this market state."""
    regime = market_regime(df, htf_df)
    atr20 = float(df["atr"].rolling(20).mean().iloc[-2]) if "atr" in df else 0.0
    atr100 = float(df["atr"].rolling(100).mean().iloc[-2]) if "atr" in df else 0.0
    vol_ratio = atr20 / atr100 if atr100 > 0 else 1.0
    news_buy, _ = capital_news.score(epic, "BUY")
    news_sell, _ = capital_news.score(epic, "SELL")
    score_floor = dynamic_entry_score_floor(regime, vol_ratio, news_buy, news_sell)
    strength_floor = {"TREND": 0.50, "BREAKOUT": 0.55, "RANGE": 0.65}.get(regime, 0.60)
    if vol_ratio < 0.85:
        strength_floor += 0.05
    elif vol_ratio > 1.50:
        strength_floor += 0.08
    if max(abs(float(news_buy or 0.0)), abs(float(news_sell or 0.0))) >= 1.0:
        strength_floor += 0.05
    strength_floor = float(np.clip(strength_floor, 0.50, 0.90))
    # Position count is NOT fixed here. The strategy decides whether another
    # leg is justified from the live basket state and its risk budget.
    # No arbitrary numeric trade-count ladder is imposed by this policy.
    strong = float(strength) >= strength_floor
    max_positions = None
    return regime, vol_ratio, score_floor, strength_floor, max_positions, strong

def asset_specific_strategy_scores(df, epic, direction, atr, rsi, ema9, ema21, ema50, htf50, htf200):
    """Local, non-blocking strategy layer tailored to the asset class.
    Uses only already-fetched candles; never makes an extra API request.
    Returns a bounded score bonus for the requested direction.
    """
    if direction not in {"BUY", "SELL"} or len(df) < 30:
        return 0.0, []
    sign = 1 if direction == "BUY" else -1
    close = df["close"]
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    price = safe_float(cur["close"], 0.0) or 0.0
    prev_close = safe_float(prev["close"], price) or price
    atr_val = safe_float(atr, 0.0) or 0.0
    if price <= 0 or atr_val <= 0:
        return 0.0, []
    bonus = 0.0
    reasons = []
    epic_u = str(epic).upper()
    if epic_u in set(getattr(config, "FX_EPICS", [])):
        if sign * (ema9 - ema21) > 0 and sign * (ema21 - ema50) > 0 and sign * (htf50 - htf200) > 0:
            bonus += 2.5; reasons.append("FX_TREND_ALIGNMENT")
        pullback = (price - prev_close) / atr_val
        if sign * pullback > 0 and abs(pullback) <= 0.60:
            bonus += 1.5; reasons.append("FX_PULLBACK_RECLAIM")
        roc10 = price / (safe_float(close.iloc[-12], price) or price) - 1.0
        if sign * roc10 > 0 and ((direction == "BUY" and 45 <= rsi <= 68) or (direction == "SELL" and 32 <= rsi <= 55)):
            bonus += 2.0; reasons.append("FX_MOMENTUM_RSI")
        if abs((price - prev_close) / atr_val) > 1.25:
            bonus -= 1.0; reasons.append("FX_STRETCH_PENALTY")
    if epic_u in {"GOLD", "SILVER"}:
        atr20 = safe_float(df["atr"].rolling(20).mean().iloc[-2], atr_val) or atr_val
        atr50 = safe_float(df["atr"].rolling(50).mean().iloc[-2], atr_val) or atr_val
        vol_ratio = atr20 / atr50 if atr50 > 0 else 1.0
        recent10 = df.iloc[-11:-1]
        prior10 = df.iloc[-21:-11]
        hh = float(recent10["high"].max()) if not recent10.empty else price
        ll = float(recent10["low"].min()) if not recent10.empty else price
        prior_hh = float(prior10["high"].max()) if not prior10.empty else hh
        prior_ll = float(prior10["low"].min()) if not prior10.empty else ll
        if sign * (ema9 - ema21) > 0 and sign * (ema21 - ema50) > 0:
            bonus += 2.0; reasons.append("METAL_TREND")
        if vol_ratio >= 1.10 and sign * (price - prev_close) > 0:
            bonus += 2.0; reasons.append("METAL_VOL_EXPANSION")
        if direction == "BUY" and price > prior_hh:
            bonus += 2.0; reasons.append("METAL_BREAKOUT")
        elif direction == "SELL" and price < prior_ll:
            bonus += 2.0; reasons.append("METAL_BREAKDOWN")
        if direction == "BUY" and price > prev_close and rsi < 72:
            bonus += 1.0; reasons.append("METAL_PULLBACK_CONTINUATION")
        elif direction == "SELL" and price < prev_close and rsi > 28:
            bonus += 1.0; reasons.append("METAL_PULLBACK_CONTINUATION")
    return max(-1.0, min(7.0, bonus)), reasons


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

    # Asset-specific strategies are local score bonuses only. The common
    # strategy remains the sole entry authority; no extra network/API call is made.
    asset_bonus_buy, buy_reasons = asset_specific_strategy_scores(df, epic, "BUY", atr, rsi, ema9, ema21, ema50, htf50, htf200)
    asset_bonus_sell, sell_reasons = asset_specific_strategy_scores(df, epic, "SELL", atr, rsi, ema9, ema21, ema50, htf50, htf200)
    buy_score = max(0.0, min(100.0, buy_score + asset_bonus_buy))
    sell_score = max(0.0, min(100.0, sell_score + asset_bonus_sell))
    if buy_reasons or sell_reasons:
        log(f"{epic}: ASSET STRATEGY | BUY+{asset_bonus_buy:.1f} {buy_reasons} | SELL+{asset_bonus_sell:.1f} {sell_reasons}")

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

    # Cached Capital.com news support. Reads local cache only: no network I/O
    # occurs in the order-decision path, so news cannot delay order submission.
    news_buy, news_buy_reasons = capital_news.score(epic, "BUY")
    news_sell, news_sell_reasons = capital_news.score(epic, "SELL")
    buy_score = max(0.0, min(100.0, buy_score + news_buy))
    sell_score = max(0.0, min(100.0, sell_score + news_sell))
    if news_buy_reasons or news_sell_reasons:
        log(
            f"{epic}: CAPITAL NEWS | BUY{news_buy:+.1f} {news_buy_reasons} | "
            f"SELL{news_sell:+.1f} {news_sell_reasons}"
        )

    # Entry-quality bonuses are non-blocking: they only improve the score.
    # Entry-quality bonuses. These are deliberately non-blocking: they improve
    # scoring when a pullback/reclaim or high-quality breakout is present, but
    # they never reject a signal by themselves.
    pullback_buy = pullback_confirmation_score(df, "BUY", atr)
    pullback_sell = pullback_confirmation_score(df, "SELL", atr)
    breakout_quality_buy = breakout_quality_score(df, "BUY", atr) if buy_breakout else 0.0
    breakout_quality_sell = breakout_quality_score(df, "SELL", atr) if sell_breakout else 0.0
    # Entry-quality bonuses are applied exactly once and remain non-blocking.
    buy_score = max(0.0, min(100.0, buy_score + pullback_buy + breakout_quality_buy))
    sell_score = max(0.0, min(100.0, sell_score + pullback_sell + breakout_quality_sell))
    log(
        f"{epic}: ENTRY QUALITY | pullback BUY={pullback_buy:.1f} SELL={pullback_sell:.1f} | "
        f"breakout BUY={breakout_quality_buy:.1f} SELL={breakout_quality_sell:.1f}"
    )

    adaptive_mult = adaptive_risk_multiplier(df)
    log(f"{epic}: QUANT SCORE | BUY={buy_score:.1f} SELL={sell_score:.1f} REGIME={regime} VOL_RATIO={vol_ratio:.2f} | adaptive_risk={adaptive_mult:.2f}")

    dynamic_floor = dynamic_entry_score_floor(regime, vol_ratio, news_buy, news_sell)
    log(f"{epic}: ADAPTIVE ENTRY FLOOR | regime={regime} | floor={dynamic_floor:.1f} | vol_ratio={vol_ratio:.2f} | news_stress={max(abs(news_buy), abs(news_sell)):.2f}")
    if buy_score >= dynamic_floor and buy_score > sell_score + 8:
        return "BUY"
    if sell_score >= dynamic_floor and sell_score > buy_score + 8:
        return "SELL"
    return None



def classic_25sep_signal(df, epic, htf_df):
    """Simplified 25/9 strategy: candle structure + M15 trend + H1 confirmation.
    This is the sole entry authority. AI remains advisory only.
    """
    if df is None or htf_df is None or len(df) < 205 or len(htf_df) < 205:
        return None
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    close = safe_float(cur["close"])
    prev_close = safe_float(prev["close"])
    atr = safe_float(cur["atr"])
    rsi = safe_float(cur["rsi"])
    if None in (close, prev_close, atr, rsi) or atr <= 0:
        return None

    m15 = df["close"].astype(float)
    h1 = htf_df["close"].astype(float)
    ema9 = m15.ewm(span=9, adjust=False).mean().iloc[-2]
    ema21 = m15.ewm(span=21, adjust=False).mean().iloc[-2]
    h50 = h1.ewm(span=50, adjust=False).mean().iloc[-2]
    h200 = h1.ewm(span=200, adjust=False).mean().iloc[-2]

    # Read the completed candle, not the still-forming candle.
    body = abs(float(cur["close"]) - float(cur["open"]))
    candle_range = max(float(cur["high"]) - float(cur["low"]), 1e-12)
    body_ratio = body / candle_range
    close_pos_buy = (float(cur["close"]) - float(cur["low"])) / candle_range
    close_pos_sell = (float(cur["high"]) - float(cur["close"])) / candle_range

    recent20 = df.iloc[-21:-1]
    prior_high = float(recent20["high"].max())
    prior_low = float(recent20["low"].min())

    buy_trend = ema9 > ema21 and h50 > h200
    sell_trend = ema9 < ema21 and h50 < h200
    buy_momentum = close > prev_close and 45 <= rsi <= 70
    sell_momentum = close < prev_close and 30 <= rsi <= 55
    buy_breakout = close > prior_high
    sell_breakout = close < prior_low

    buy_candle = float(cur["close"]) > float(cur["open"]) and close_pos_buy >= 0.55
    sell_candle = float(cur["close"]) < float(cur["open"]) and close_pos_sell >= 0.55
    buy = (buy_trend and buy_momentum and buy_candle) or (buy_breakout and buy_candle and rsi < 75)
    sell = (sell_trend and sell_momentum and sell_candle) or (sell_breakout and sell_candle and rsi > 25)

    if buy and not sell:
        log(f"{epic}: 25SEP CLASSIC BUY | candle={completed_candle_key(df)} | RSI={rsi:.1f} | body={body_ratio:.2f}")
        return "BUY"
    if sell and not buy:
        log(f"{epic}: 25SEP CLASSIC SELL | candle={completed_candle_key(df)} | RSI={rsi:.1f} | body={body_ratio:.2f}")
        return "SELL"
    return None


def generate_signal(df, epic, htf_df=None):
    return classic_25sep_signal(df, epic, htf_df)


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


def calculate_trade(df, direction, entry_price=None, strength=1.0, epic=None, ai_decision=None):
    # Use only the latest completed 15m candle for volatility.
    current = df.iloc[-2]
    atr = safe_float(current["atr"])
    price = safe_float(entry_price) if entry_price is not None else safe_float(current["close"])
    reference = safe_float(current["close"])
    if price is None or atr is None or atr <= 0 or reference is None:
        return None

    # Avoid chasing a stretched live quote. Recheck on the next scan.
    # Both active bots use the same hard late-entry protection.
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

    # 25/9 protection: fixed 2 ATR SL and 3 ATR TP. AI does not alter execution.
    sl_mult = 2.0
    tp_mult = 3.0
    sl_distance = atr * sl_mult
    profit_level = None
    if tp_mult > 0:
        profit_level = price + atr * tp_mult if direction == "BUY" else price - atr * tp_mult
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

def estimated_position_risk_account(position, api, account_currency, market_cache=None):
    entry, stop, size = position_open_level(position), position_stop_level(position), position_size_value(position)
    # Unknown risk must never be interpreted as zero risk.
    if entry is None or stop is None or size <= 0:
        return None
    try:
        epic = position_epic(position)
        market = None
        if market_cache is not None and epic in market_cache:
            market = market_cache[epic]
        if market is None:
            market = api.get_market(epic)
            if market_cache is not None:
                market_cache[epic] = market
        lot_size = safe_float(market.get("instrument", {}).get("lotSize"), 1.0) or 1.0
        quote_to_account = quote_to_account_rate(market, account_currency)
        if quote_to_account is None:
            return None
        return abs(entry - stop) * size * lot_size * quote_to_account
    except Exception:
        return None

def basket_reserved_risk(api, positions, epic, account_currency, market_cache=None):
    risks = [
        estimated_position_risk_account(p, api, account_currency, market_cache=market_cache)
        for p in get_positions_for_epic(positions, epic)
    ]
    return None if any(r is None for r in risks) else sum(risks)


def portfolio_reserved_risk(api, positions, account_currency, market_cache=None):
    risks = [
        estimated_position_risk_account(p, api, account_currency, market_cache=market_cache)
        for p in positions
    ]
    return None if any(r is None for r in risks) else sum(risks)

def confirm_position_closed(api, deal_id, attempts=1):
    """Confirm broker close with minimal extra REST traffic."""
    for _ in range(max(1, int(attempts))):
        try:
            open_ids = {str(position_deal_id(p)) for p in api.get_open_positions() if position_deal_id(p)}
            if str(deal_id) not in open_ids:
                return True
        except Exception as exc:
            log(f"POSITION CLOSE CONFIRMATION FAILED | deal={deal_id} | {exc}")
            return False
    return False


def ai_manage_positions(api, positions, epic, ai_decision):
    """AI position manager: HOLD / PROTECT / EXIT using the final AI market state.

    Broker SL/TP and trailing remain safety nets. The AI does not close simply
    because a fixed profit amount was reached; it closes when the market thesis
    materially changes or the validated expected edge turns negative.
    """
    if not ai_decision or not ai_decision.get("enabled"):
        return

    advanced = ai_decision.get("advanced_ai") if isinstance(ai_decision.get("advanced_ai"), dict) else {}
    signal = advanced.get("signal_after") or ai_decision.get("signal") or ai_decision.get("raw_signal")
    confidence = float(ai_decision.get("confidence", 0.0) or 0.0)
    pm = advanced.get("position_management") if isinstance(advanced.get("position_management"), dict) else {}
    action_bias = str(pm.get("action_bias") or "WAIT").upper()
    profit_actions = (advanced.get("profit_management") or {}).get("actions", []) if isinstance(advanced.get("profit_management"), dict) else []
    profit_action_by_deal = {
        str(item.get("deal_id")): str(item.get("action") or "WAIT").upper()
        for item in profit_actions
        if item.get("deal_id")
    }
    explicit_exit = bool(
        ai_decision.get("exit")
        or ai_decision.get("should_exit")
        or advanced.get("exit")
        or advanced.get("should_exit")
    )
    exit_confidence = max(0.65, float(os.environ.get("AI_EXIT_CONFIDENCE", "0.65")))

    for position in get_positions_for_epic(positions, epic):
        direction = position_direction(position)
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id:
            continue
        if pnl is None:
            log(f"{epic}: AI position management skipped | deal={deal_id} | broker P/L unavailable; preserving last protection state.")
            continue

        protection = STATE.setdefault("position_telemetry", {}).get(str(deal_id), {})
        peak_profit = safe_float(protection.get("peak_profit"), pnl)
        giveback = safe_float(protection.get("giveback"), 0.0) or 0.0
        protected_floor = safe_float(protection.get("protected_floor"))
        log(
            f"{epic}: POSITION STATE | deal={deal_id} | pnl={pnl:.2f} | "
            f"peak={peak_profit:.2f} | giveback={giveback:.2f} | "
            f"floor={protected_floor if protected_floor is not None else 'n/a'} | "
            f"AI={signal} | confidence={confidence:.3f} | action={action_bias}"
        )

        opposite_signal = (
            signal in ("BUY", "SELL")
            and direction in ("BUY", "SELL")
            and signal != direction
        )
        reversal_exit = opposite_signal and confidence >= exit_confidence
        negative_edge_exit = (
            action_bias == "EXIT_NEGATIVE_EDGE"
            and signal == direction
            and confidence >= exit_confidence
        )
        ai_profit_action = profit_action_by_deal.get(str(deal_id), "WAIT")
        STATE.setdefault("profit_trail", {}).setdefault(str(deal_id), {})["ai_profit_action"] = ai_profit_action
        profit_giveback_exit = (
            ai_profit_action == "EXIT"
            or action_bias == "EXIT_PROFIT_GIVEBACK"
        )
        # LOSS-PRESERVATION RULE: AI may not directly close a losing position.
        # A negative-P/L trade must remain under the broker SL / hard-loss guard;
        # this prevents AI thesis changes or profit-management signals from
        # repeatedly cutting losers while profitable trades are allowed to run.
        # Winning/flat positions can still be closed by the AI exit logic below.
        ai_authorized_exit = (
            (explicit_exit or reversal_exit or negative_edge_exit or profit_giveback_exit)
            and pnl >= 0.0
        )
        if (explicit_exit or reversal_exit or negative_edge_exit or profit_giveback_exit) and pnl < 0.0:
            log(
                f"{epic}: AI LOSS EXIT BLOCKED | deal={deal_id} | pnl={pnl:.2f} | "
                f"AI={signal} | action={action_bias} | broker_SL_remains_active=True"
            )

        if ai_authorized_exit:
            try:
                response = api.close_position(deal_id)
                confirmed = confirm_position_closed(api, deal_id)
                if explicit_exit:
                    reason = "EXPLICIT_AI_EXIT"
                elif reversal_exit:
                    reason = "AI_REVERSAL"
                elif profit_giveback_exit:
                    reason = "AI_DYNAMIC_PROFIT_LOCK"
                else:
                    reason = "AI_NEGATIVE_EXPECTED_EDGE"
                log(
                    f"{epic}: AI EXIT | reason={reason} | existing={direction} | "
                    f"AI={signal} | action={action_bias} | confidence={confidence:.3f} | "
                    f"pnl={pnl:.2f} | deal={deal_id} | response={response} | confirmed_closed={confirmed}"
                )
            except Exception as exc:
                log(f"{epic}: AI EXIT failed | deal={deal_id} | {exc}")
            continue

        if action_bias == "PROTECT":
            log(
                f"{epic}: AI PROTECT | existing={direction} | AI={signal} | "
                f"confidence={confidence:.3f} | pnl={pnl:.2f} | "
                f"uncertainty={advanced.get('enhanced_uncertainty')} | "
                f"regime={(advanced.get('regime') or {}).get('regime', 'UNKNOWN')}"
            )
        elif action_bias == "HOLD" and direction == signal:
            log(
                f"{epic}: AI HOLD | existing={direction} | confidence={confidence:.3f} | "
                f"pnl={pnl:.2f} | regime={(advanced.get('regime') or {}).get('regime', 'UNKNOWN')}"
            )

def update_profit_telemetry(positions, epic):
    """Refresh live profit telemetry before AI decides how to manage the trade."""
    telemetry = STATE.setdefault("position_telemetry", {})
    trails = STATE.setdefault("profit_trail", {})
    now = time.time()
    result = []
    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        if not deal_id:
            continue
        pnl = position_unrealized_pnl(position)
        if pnl is None:
            log(f"{epic}: PROFIT TELEMETRY HOLD | deal={deal_id} | broker P/L unavailable; last valid peak preserved.")
            existing = trails.get(str(deal_id))
            if existing:
                result.append(dict(telemetry.get(str(deal_id), {})))
            continue
        pnl = float(pnl)
        key = str(deal_id)
        trail = trails.get(key) or {
            "peak_profit": pnl,
            "peak_time": datetime.now(timezone.utc).isoformat(),
            "activated": False,
            "last_pnl": pnl,
            "last_update_ts": now,
        }
        old_peak = float(safe_float(trail.get("peak_profit"), pnl) or pnl)
        peak = max(old_peak, pnl)
        last_pnl = float(safe_float(trail.get("last_pnl"), pnl) or pnl)
        last_ts = float(safe_float(trail.get("last_update_ts"), now) or now)
        dt = max(0.5, now - last_ts)
        velocity = (pnl - last_pnl) / dt
        previous_velocity = float(safe_float(trail.get("profit_velocity"), 0.0) or 0.0)
        acceleration = (velocity - previous_velocity) / dt
        if peak > old_peak:
            trail["peak_time"] = datetime.now(timezone.utc).isoformat()
        giveback = max(0.0, peak - pnl)
        giveback_ratio = giveback / peak if peak > 0 else 0.0
        trail.update({
            "peak_profit": round(peak, 2),
            "giveback": round(giveback, 2),
            "giveback_ratio": round(giveback_ratio, 4),
            "profit_velocity": round(velocity, 6),
            "profit_acceleration": round(acceleration, 6),
            "current_profit": round(pnl, 2),
            "last_pnl": round(pnl, 2),
            "last_update_ts": now,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "activated": bool(trail.get("activated") or pnl > 0),
        })
        trails[key] = trail
        telemetry[key] = {
            "epic": epic, "deal_id": deal_id, "direction": position_direction(position),
            "current_profit": round(pnl, 2), "peak_profit": round(peak, 2),
            "giveback": round(giveback, 2), "giveback_ratio": round(giveback_ratio, 4),
            "profit_velocity": round(velocity, 6), "profit_acceleration": round(acceleration, 6),
            "peak_time": trail.get("peak_time"), "last_update": trail.get("last_update"),
        }
        result.append(dict(telemetry[key]))
    return result

def manage_profit_trailing(api, positions, epic, account_currency):
    """Dynamic profit protection driven by the live peak, not a fixed profit target.

    The manager starts tracking as soon as a position becomes profitable. The
    protected floor rises smoothly with the peak profit, so even a small gain
    can be protected without forcing an immediate close. AI remains responsible
    for HOLD/PROTECT/EXIT decisions; this is the broker-side safety net.
    """
    if not PROFIT_TRAIL_ENABLED:
        return

    active_deals = set()
    telemetry = STATE.setdefault("position_telemetry", {})
    trails = STATE.setdefault("profit_trail", {})

    for position in get_positions_for_epic(positions, epic):
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id:
            continue
        if pnl is None:
            log(f"{epic}: PROFIT TRAIL HOLD | deal={deal_id} | broker P/L unavailable; preserving peak/floor.")
            continue

        deal_key = str(deal_id)
        active_deals.add(deal_key)
        now_iso = datetime.now(timezone.utc).isoformat()
        trail = trails.get(deal_key) or {
            "peak_profit": float(pnl),
            "peak_time": now_iso,
            "activated": False,
        }
        peak = safe_float(trail.get("peak_profit"), pnl)
        if peak is None:
            peak = float(pnl)

        if pnl > peak:
            peak = float(pnl)
            trail["peak_profit"] = round(peak, 2)
            trail["peak_time"] = now_iso

        # Smoothly increase the percentage of profit protected as the trade
        # develops. There is no fixed +1.20/+2.00 activation threshold.
        # At small profits the lock is intentionally loose; as the peak grows,
        # more of it is retained.
        protected_fraction = 0.30 + 0.50 * (1.0 - math.exp(-max(0.0, peak) / 5.0))
        ai_profit_action = str(trail.get("ai_profit_action") or "RUNNER").upper()
        if ai_profit_action == "PROTECT":
            protected_fraction += 0.08
        elif ai_profit_action == "HARVEST":
            protected_fraction += 0.16
        protected_fraction = max(0.30, min(0.88, protected_fraction))
        floor = peak * protected_fraction if peak > 0 else None
        giveback = max(0.0, peak - pnl)

        trail["peak_profit"] = round(peak, 2)
        trail["protected_fraction"] = round(protected_fraction, 4)
        trail["protected_floor"] = round(floor, 2) if floor is not None else None
        trail["giveback"] = round(giveback, 2)
        trail["last_update"] = now_iso

        # Activate as soon as the position has any positive broker-reported P/L.
        # A tiny positive peak is tracked, but a close is only possible after a
        # real giveback to the dynamic floor.
        if pnl > 0:
            if not trail.get("activated"):
                trail["activated"] = True
                log(
                    f"{epic}: PROFIT PROTECTION ACTIVATED | deal={deal_id} | "
                    f"peak={peak:.2f} {account_currency} | "
                    f"protected={protected_fraction:.0%} | floor={floor:.2f} {account_currency}"
                )
            elif pnl > safe_float(trails.get(deal_key, {}).get("peak_profit"), -float("inf")):
                log(f"{epic}: PROFIT PEAK UPDATED | deal={deal_id} | peak={peak:.2f} {account_currency}")

        trails[deal_key] = trail
        telemetry[deal_key] = {
            "epic": epic,
            "direction": position_direction(position),
            "current_profit": round(float(pnl), 2),
            "peak_profit": round(float(peak), 2),
            "giveback": round(float(giveback), 2),
            "protected_fraction": round(float(protected_fraction), 4),
            "protected_floor": round(float(floor), 2) if floor is not None else None,
            "peak_time": trail.get("peak_time"),
            "last_update": now_iso,
        }

        # LOSS-PRESERVATION RULE: this discretionary profit manager may only
        # close while the broker still reports a non-negative P/L. Once P/L is
        # negative, only the hard loss guard or the broker SL may close it.
        if trail.get("activated") and floor is not None and pnl >= 0.0 and pnl <= floor:
            try:
                log(
                    f"{epic}: PROFIT EXIT INTENT | reason=DYNAMIC_PROFIT_PROTECTION | "
                    f"deal={deal_id} | peak={peak:.2f} {account_currency} | "
                    f"current={pnl:.2f} {account_currency} | floor={floor:.2f} {account_currency}"
                )
                response = api.close_position(deal_id)
                confirmed = confirm_position_closed(api, deal_id)
                log(
                    f"{epic}: DYNAMIC PROFIT PROTECTION CLOSE | deal={deal_id} | "
                    f"peak={peak:.2f} {account_currency} | current={pnl:.2f} {account_currency} | "
                    f"giveback={giveback:.2f} | protected={protected_fraction:.0%} | "
                    f"floor={floor:.2f} {account_currency} | response={response} | confirmed_closed={confirmed}"
                )
                trails.pop(deal_key, None)
                telemetry.pop(deal_key, None)
            except Exception as exc:
                log(f"{epic}: dynamic profit-protection close failed | deal={deal_id} | {exc}")
        elif trail.get("activated") and floor is not None and pnl < 0.0:
            log(
                f"{epic}: PROFIT EXIT BLOCKED | deal={deal_id} | current={pnl:.2f} {account_currency} | "
                f"floor={floor:.2f} {account_currency} | broker_SL_or_hard_loss_guard_only=True"
            )

    # Only prune deals belonging to this market. Other markets may be
    # monitored later in the same pass; deleting their peaks here would
    # reset profit protection before their next quote arrives.
    for deal_key in list(trails.keys()):
        if deal_key not in active_deals and (telemetry.get(deal_key) or {}).get("epic") == epic:
            trails.pop(deal_key, None)
            telemetry.pop(deal_key, None)

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

# Live quotes are the primary executable-price source. Historical candles
# remain cached so fast scans do not create a REST/API storm.
LIVE_PRICE_MAX_AGE_SECONDS = 3.0
LIVE_PRICE_STREAM = None
OPEN_POSITION_MONITOR_SECONDS = 2
OPEN_POSITION_MONITOR_WINDOW_SECONDS = 14 * 60
# Fast entry scanner: rotate a small number of markets every few seconds.
# This reduces worst-case entry wait without hammering the broker API.
FAST_ENTRY_SCAN_SECONDS = 5
FAST_ENTRY_MARKETS_PER_SCAN = 4
# Keep candle data close to the scanner cadence so a new completed
# candle is recognized quickly; live executable quotes remain uncached.
FAST_ENTRY_CANDLE_CACHE_TTL_SECONDS = 5.0
FAST_ENTRY_MARKET_CACHE_TTL_SECONDS = 20.0
# Prevent the fast management loop from stacking the same profitable-basket
# leg repeatedly; signals are still evaluated through the normal entry path.
PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS = 60
LAST_ENTRY_AT = {}

# Entry freshness: the strategy is candle-driven. A completed 15m candle may
# authorize an entry only once per epic/direction. This is NOT a trade-count
# cap; it prevents repeated orders from reusing the same unchanged candle signal
# during the fast scanner and across overlapping scheduler runs.
ENTRY_CANDLE_STATE_FILE = "fx_ai_entry_candle_state.json"
ENTRY_CANDLE_RESOLUTION = RESOLUTION

def _load_entry_candle_state():
    try:
        if not os.path.exists(ENTRY_CANDLE_STATE_FILE):
            return {}
        with open(ENTRY_CANDLE_STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"Entry candle state load failed; using empty state: {exc}")
        return {}


def _save_entry_candle_state(state):
    try:
        temp_file = f"{ENTRY_CANDLE_STATE_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
        os.replace(temp_file, ENTRY_CANDLE_STATE_FILE)
    except Exception as exc:
        log(f"Entry candle state save failed: {exc}")


def strategy_candles_are_fresh(df, now=None):
    """Fail closed when the latest completed 15m candle is missing/stale."""
    if df is None or len(df) < 3:
        return False, "INSUFFICIENT_CANDLES"
    times = pd.to_datetime(df["time"], utc=True, errors="coerce")
    if times.isna().any():
        return False, "INVALID_CANDLE_TIME"
    completed = times.iloc[-2]
    previous = times.iloc[-3]
    spacing = (completed - previous).total_seconds()
    if spacing <= 0 or abs(spacing - STRATEGY_CANDLE_RESOLUTION_SECONDS) > 2:
        return False, f"CANDLE_SPACING_INVALID:{spacing:.0f}s"
    current_time = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now, tz="UTC")
    age = (current_time - completed).total_seconds()
    # Allows normal start-time timestamping of a completed 15m bar,
    # but rejects genuinely stale broker data.
    if age < -120:
        return False, f"CANDLE_TIME_IN_FUTURE:{age:.0f}s"
    if age > 2400:
        return False, f"STALE_COMPLETED_CANDLE:{age:.0f}s"
    return True, None

def completed_candle_key(df):
    """Return the timestamp of the last fully completed strategy candle."""
    if df is None or len(df) < 3:
        return None
    value = df.iloc[-2].get("time")
    if value is None:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def candle_entry_is_fresh(epic, direction, df):
    """Allow a strategy entry only when the completed candle is new."""
    key = completed_candle_key(df)
    if key is None:
        return False, None, "COMPLETED_CANDLE_UNAVAILABLE"
    state = _load_entry_candle_state()
    row = state.get(str(epic), {})
    if row.get("candle") == key:
        return False, key, "SAME_COMPLETED_CANDLE"
    return True, key, None


def mark_candle_entry(epic, direction, candle_key):
    if not candle_key:
        return
    state = _load_entry_candle_state()
    state[str(epic)] = {
        "candle": candle_key,
        "direction": direction,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_entry_candle_state(state)


def monitor_open_positions(api, account_currency, duration_seconds=OPEN_POSITION_MONITOR_WINDOW_SECONDS):
    """Continuously protect positions and scan for new entries from live quotes.

    Open positions are checked every ~2s. New-entry candidates are rotated in
    small batches every ~5s, using cached candles/market metadata and the live
    WebSocket quote. The final order path still performs a fresh broker
    TRADEABLE check before submitting, so speed does not weaken the safety gate.
    """
    management_interval = max(1.0, float(OPEN_POSITION_MONITOR_SECONDS))
    entry_interval = max(2.0, float(FAST_ENTRY_SCAN_SECONDS))
    window = max(management_interval, float(duration_seconds))
    deadline = time.monotonic() + window
    candle_cache = {}
    market_cache = {}
    market_cache_ts = {}
    iteration = 0
    entry_cursor = 0
    next_entry_scan = time.monotonic()
    log(
        f"FAST MONITOR | manage={management_interval:.1f}s | "
        f"entry_scan={entry_interval:.1f}s/{FAST_ENTRY_MARKETS_PER_SCAN} markets | "
        f"window={window:.0f}s | WS={'ON' if LIVE_PRICE_STREAM is not None else 'OFF'}"
    )

    while time.monotonic() < deadline:
        iteration += 1
        started = time.monotonic()
        try:
            positions = api.get_open_positions()
            owned = filter_owned_positions(positions)
            open_epics = [epic for epic in EPICS if get_positions_for_epic(owned, epic)]

            balance = api.get_balance() if open_epics else None

            # Priority 1: protect every open position on the fast 2-second loop.
            for epic in open_epics:
                if time.monotonic() >= deadline:
                    break
                now = time.monotonic()
                cached_market = market_cache.get(epic)
                if cached_market is None or now - market_cache_ts.get(epic, 0.0) >= FAST_ENTRY_MARKET_CACHE_TTL_SECONDS:
                    try:
                        cached_market = api.get_market(epic)
                        market_cache[epic] = cached_market
                        market_cache_ts[epic] = now
                    except Exception as market_exc:
                        log(f"{epic}: fast monitor market refresh failed: {market_exc}")
                        cached_market = None
                process_epic(
                    api=api, epic=epic, positions=positions, balance=balance,
                    account_currency=account_currency, allow_entry_without_signal=False,
                    market=cached_market, candle_cache=candle_cache,
                    candle_cache_ttl=max(FAST_ENTRY_CANDLE_CACHE_TTL_SECONDS, management_interval + 2.0),
                    position_management_only=True,
                )

            # Priority 2: continuously look for fresh entries. Rotate only a few
            # markets per pass so every market is revisited quickly while staying
            # comfortably below the broker's account-wide request ceiling.
            now = time.monotonic()
            if now >= next_entry_scan and EPICS:
                batch_size = min(int(FAST_ENTRY_MARKETS_PER_SCAN), len(EPICS))
                candidates = [
                    EPICS[(entry_cursor + offset) % len(EPICS)]
                    for offset in range(batch_size)
                ]
                entry_cursor = (entry_cursor + batch_size) % len(EPICS)
                for epic in candidates:
                    if time.monotonic() >= deadline:
                        break
                    refresh_now = time.monotonic()
                    cached_market = market_cache.get(epic)
                    if cached_market is None or refresh_now - market_cache_ts.get(epic, 0.0) >= FAST_ENTRY_MARKET_CACHE_TTL_SECONDS:
                        try:
                            cached_market = api.get_market(epic)
                            market_cache[epic] = cached_market
                            market_cache_ts[epic] = refresh_now
                        except Exception as market_exc:
                            log(f"{epic}: fast entry market refresh failed: {market_exc}")
                            continue
                    try:
                        process_epic(
                            api=api, epic=epic, positions=positions,
                            balance=balance if balance is not None else api.get_balance(),
                            account_currency=account_currency,
                            allow_entry_without_signal=True,
                            market=cached_market,
                            candle_cache=candle_cache,
                            candle_cache_ttl=FAST_ENTRY_CANDLE_CACHE_TTL_SECONDS,
                            position_management_only=False,
                        )
                    except Exception as entry_exc:
                        log(f"{epic}: FAST ENTRY SCAN error: {entry_exc}")
                next_entry_scan = now + entry_interval

            elapsed = time.monotonic() - started
            remaining = deadline - time.monotonic()
            # Management cadence is the hard protection cadence; entry scans
            # happen when their own 5-second timer is due.
            sleep_for = max(0.0, min(management_interval, remaining))
            log(
                f"FAST MONITOR | pass={iteration} | elapsed={elapsed:.2f}s | "
                f"open={len(open_epics)} | next_in={sleep_for:.2f}s"
            )
            if sleep_for > 0:
                time.sleep(sleep_for)
        except Exception as exc:
            log(f"FAST MONITOR | pass={iteration} error: {exc}")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(management_interval, remaining))

    log(f"FAST MONITOR | completed | passes={iteration}")

def process_epic(api, epic, positions, balance, account_currency, allow_entry_without_signal=True, market=None, candle_cache=None, candle_cache_ttl=CANDLE_CACHE_DEFAULT_TTL_SECONDS, position_management_only=False):
    log("")
    owned_positions = filter_owned_positions(positions)
    log("=" * 60)
    log(f"PROCESSING {epic}")
    log("=" * 60)
    try:
        if epic in UNAVAILABLE_EPICS:
            record_entry_rejection(epic, "MARKET_UNAVAILABLE", "temporarily unavailable during this run")
            return None
        # Keep a fresh executable quote and existing-position protection ahead of
        # entry gates. New-entry-only epics can skip expensive candle work when
        # safety/cooldown/spread blocks the entry.
        if market is None:
            market = api.get_market(epic)

        # Prefer the freshest Capital.com WebSocket quote for executable pricing.
        # REST remains the source of market status/instrument metadata.
        live_quote = None
        if LIVE_PRICE_STREAM is not None:
            try:
                live_quote = LIVE_PRICE_STREAM.get_quote(epic)
                live_age = LIVE_PRICE_STREAM.age_seconds(epic)
                if live_quote and live_age is not None and live_age <= LIVE_PRICE_MAX_AGE_SECONDS:
                    snapshot = dict(market.get("snapshot", {}) or {})
                    snapshot["bid"] = live_quote["bid"]
                    snapshot["offer"] = live_quote["offer"]
                    snapshot["livePriceTimestamp"] = live_quote.get("timestamp")
                    snapshot["livePriceReceivedAt"] = live_quote.get("received_at_iso")
                    market = dict(market)
                    market["snapshot"] = snapshot
                    log(
                        f"{epic}: LIVE WS QUOTE | bid={live_quote['bid']} | "
                        f"offer={live_quote['offer']} | age={live_age:.2f}s"
                    )
                else:
                    snapshot = market.get("snapshot", {}) or {}
                    if LIVE_PRICE_STREAM is not None and live_age is not None:
                        log(f"{epic}: LIVE WS quote stale ({live_age:.2f}s); using REST snapshot.")
            except Exception as live_exc:
                snapshot = market.get("snapshot", {}) or {}
                log(f"{epic}: LIVE WS quote unavailable; using REST snapshot: {live_exc}")
        else:
            snapshot = market.get("snapshot", {}) or {}
        market_status = str(snapshot.get("marketStatus") or market.get("marketStatus") or "").upper()
        if market_status != "TRADEABLE" and not get_positions_for_epic(owned_positions, epic):
            log(f"{epic}: broker market status={market_status or 'UNKNOWN'}; fail closed, no new entry.")
            return None
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

        epic_positions = get_positions_for_epic(owned_positions, epic)
        df = None

        # Existing positions must continue to be managed even when entry gates
        # block new entries, so load the 15m data only for those positions.
        if epic_positions:
            df = get_cached_candles(
                api, epic, RESOLUTION, CANDLE_COUNT,
                cache=candle_cache, ttl_seconds=candle_cache_ttl
            )
            if df.empty:
                log(f"{epic}: no candle data; running price-independent loss and profit guards.")
                enforce_max_position_loss(api, owned_positions, epic, account_currency)
                fresh_positions = filter_owned_positions(api.get_open_positions())
                update_profit_telemetry(fresh_positions, epic)
                manage_profit_trailing(api, fresh_positions, epic, account_currency)
                return None
            df = add_indicators(df)
            if len(df) < 3:
                log(f"{epic}: insufficient candles; running price-independent loss and profit guards.")
                enforce_max_position_loss(api, owned_positions, epic, account_currency)
                fresh_positions = filter_owned_positions(api.get_open_positions())
                update_profit_telemetry(fresh_positions, epic)
                manage_profit_trailing(api, fresh_positions, epic, account_currency)
                return None

        hard_loss_closed = enforce_max_position_loss(api, owned_positions, epic, account_currency)
        if hard_loss_closed:
            positions = api.get_open_positions()
            owned_positions = filter_owned_positions(positions)
            epic_positions = get_positions_for_epic(owned_positions, epic)

        # Refresh profit telemetry before AI; actual protection runs after AI.
        if epic_positions:
            update_profit_telemetry(owned_positions, epic)
        breakeven_stops(api, owned_positions, epic, current_price)
        manage_trailing_stops(api, owned_positions, epic, current_price, df=df)

        # Entry safety gates must not stop management of an already-open trade.
        # They only suppress NEW entries.
        if not epic_positions and not safety_allows_new_entry(balance, positions, epic=epic, account_currency=account_currency):
            record_entry_rejection(epic, "SAFETY_STOP")
            return None
        if not epic_positions and cooldown_active(epic):
            record_entry_rejection(epic, "LOSS_COOLDOWN")
            return None
        # AI must evaluate the live spread after seeing the market/candle context.
        # The old fixed spread filter is no longer an independent veto.
        
        # No existing position: only now spend the candle API/indicator work
        # needed to search for a fresh entry.
        if df is None:
            # ENTRY CANDLES: bypass cache; read the broker's newest candle set now.
            df = get_cached_candles(
                api, epic, RESOLUTION, CANDLE_COUNT,
                cache=candle_cache, ttl_seconds=ENTRY_CANDLE_CACHE_TTL_SECONDS
            )
            if df.empty:
                log(f"{epic}: no candle data.")
                return None
            df = add_indicators(df)
            if len(df) < 3:
                log(f"{epic}: insufficient candles.")
                return None

        fresh_ok, fresh_reason = strategy_candles_are_fresh(df)
        if not fresh_ok:
            record_entry_rejection(epic, "STALE_OR_INVALID_STRATEGY_CANDLE", fresh_reason)
            log(f"{epic}: ENTRY BLOCKED | candle data not fresh: {fresh_reason}")
            return None

        htf_df = get_cached_candles(
            api, epic, HTF_RESOLUTION, HTF_CANDLE_COUNT,
            cache=candle_cache, ttl_seconds=ENTRY_CANDLE_CACHE_TTL_SECONDS
        )
        # Capital.com can return only a handful of HOUR candles for some
        # instruments/session windows. The integrated AI bots need a usable HTF history.
        # If the native HOUR response is short, rebuild 1H candles from the
        # already-fetched 15m history instead of skipping the signal engine.
        native_htf_count = len(htf_df)
        if STRATEGY_ID in {"CAPITAL_FX_AI", "CAPITAL_METALS_ENERGY_AI"} and native_htf_count < 205:
            try:
                # Capital.com may return only a few native HOUR candles. Rebuild
                # from a larger 15m window, but count only complete 4-candle hours.
                fallback_df = get_cached_candles(
                    api,
                    epic,
                    RESOLUTION,
                    max(1000, CANDLE_COUNT),
                    cache=candle_cache,
                    ttl_seconds=candle_cache_ttl,
                ) if len(df) < 600 else df
                tmp = fallback_df[["time", "open", "high", "low", "close"]].copy()
                tmp["time"] = pd.to_datetime(tmp["time"], utc=True, errors="coerce")
                tmp = tmp.dropna(subset=["time"]).sort_values("time").drop_duplicates("time")
                # Build H1 from explicit consecutive 15m bars rather than
                # resample/floor semantics. Capital candle timestamps can be
                # interpreted differently by endpoint/resolution; four
                # consecutive 15m observations are unambiguous and avoid
                # silently dropping or mixing an hour.
                rows = []
                times = tmp["time"].tolist()
                for start in range(0, max(0, len(tmp) - 3), 4):
                    chunk = tmp.iloc[start:start + 4]
                    if len(chunk) != 4:
                        continue
                    deltas = chunk["time"].diff().dropna().dt.total_seconds()
                    if len(deltas) != 3 or not np.allclose(deltas.to_numpy(dtype=float), 900.0, atol=2.0):
                        continue
                    rows.append({
                        "time": chunk["time"].iloc[-1],
                        "open": float(chunk["open"].iloc[0]),
                        "high": float(chunk["high"].max()),
                        "low": float(chunk["low"].min()),
                        "close": float(chunk["close"].iloc[-1]),
                    })
                rebuilt = pd.DataFrame(rows).dropna(subset=["time", "open", "high", "low", "close"])
                rebuilt = rebuilt.drop_duplicates("time").sort_values("time").reset_index(drop=True)
                rebuilt["time"] = rebuilt["time"].dt.strftime("%Y-%m-%dT%H:%M:%S")
                if len(rebuilt) >= 205:
                    htf_df = rebuilt
                    log(
                        f"{epic}: {STRATEGY_ID} HTF FALLBACK | native 1h={native_htf_count} | "
                        f"rebuilt 1h={len(htf_df)} from 15m={len(fallback_df)} | required_1h=205 | complete_hours_only=True"
                    )
                else:
                    log(
                        f"{epic}: {STRATEGY_ID} HTF FALLBACK insufficient | native 1h={native_htf_count} | "
                        f"rebuilt 1h={len(rebuilt)} from 15m={len(fallback_df)} | required_1h=205 | complete_hours_only=True"
                    )
            except Exception as exc:
                log(f"{epic}: {STRATEGY_ID} HTF FALLBACK failed: {exc}")
        # AI is SUPPORT-ONLY. The legacy strategy remains the sole entry authority.
        # AI output is retained for advisory analysis, logging and learning only.
        legacy_signal = generate_signal(df, epic, htf_df)
        ai_decision = ai_engine.decide(df, htf_df, epic, existing_signal=legacy_signal, strategy_id=STRATEGY_ID)
        # Advanced AI safety stack is shadow-only by default. It can add diagnostics
        # without changing the active AI execution path unless explicitly switched to enforce mode.
        try:
            position_context = update_profit_telemetry(owned_positions, epic) if epic_positions else []
            ai_decision["advanced_ai"] = ai_pipeline.evaluate(
                df,
                ai_decision,
                strategy_signal=legacy_signal,
                bid=live_bid,
                ask=live_offer,
                htf_df=htf_df,
                position_context=position_context,
            )
            adv = ai_decision["advanced_ai"]
            log(
                f"{epic}: ADVANCED AI | mode={adv.get('mode')} | "
                f"regime={adv.get('regime',{}).get('regime')} | "
                f"uncertainty={float(adv.get('uncertainty',{}).get('uncertainty',1.0)):.3f} | "
                f"drift={float(adv.get('drift_score',0.0)):.3f} | "
                f"mh={adv.get('multihorizon',{}).get('signal')} "
                f"mh_agree={float(adv.get('multihorizon',{}).get('agreement',0.0)):.2f} | "
                f"conformal_q={float(adv.get('conformal',{}).get('quality',0.0)):.2f} "
                f"conformal_wide={adv.get('conformal',{}).get('wide')} | "
                f"enh_unc={float(adv.get('enhanced_uncertainty',1.0)):.3f} | "
                f"edge={adv.get('expected_edge',{}).get('expected_gross_pnl')} "
                f"edge_avail={adv.get('expected_edge',{}).get('available')} | "
                f"cost={adv.get('execution_cost',{}).get('total_price_cost')} | "
                f"meta={adv.get('meta_label',{}).get('accepted')} | "
                f"risk_mult={float(adv.get('risk_multiplier',0.0)):.3f}"
            )
        except Exception as _advanced_ai_exc:
            # Advanced diagnostics must never break the active AI decision path.
            log(f"{epic}: ADVANCED AI diagnostics unavailable: {_advanced_ai_exc}")
        # Snapshot the exact features used by the AI at decision time.
        # These values are stored with the eventual broker-reported outcome;
        # they are never rebuilt from future candles.
        ai_feature_snapshot = {}
        try:
            _ai_fx = ai_engine._features(df, htf_df)
            _ai_idx = _ai_fx.index[-2]
            ai_feature_snapshot = {
                k: (float(_ai_fx.loc[_ai_idx, k]) if pd.notna(_ai_fx.loc[_ai_idx, k]) else None)
                for k in ai_engine.FEATURES
            }
        except Exception as _ai_feature_exc:
            log(f"{epic}: AI outcome feature snapshot unavailable: {_ai_feature_exc}")
        log(
            f"{epic}: AI DECISION | signal={ai_decision.get('signal')} | "
            f"confidence={float(ai_decision.get('confidence', 0.0)):.3f} | "
            f"BUY={float(ai_decision.get('buy_probability', 0.0)):.3f} | "
            f"SELL={float(ai_decision.get('sell_probability', 0.0)):.3f} | "
            f"WAIT={float(ai_decision.get('wait_probability', 0.0)):.3f} | "
            f"RAW={ai_decision.get('raw_signal')} | STRATEGY={legacy_signal} | "
            f"AGREE={ai_decision.get('strategy_agreement')} | "
            f"SL_ATR={ai_decision.get('sl_atr')} | TP_ATR={ai_decision.get('tp_atr')} | "
            f"{ai_decision.get('reason')}"
        )
        # Spread is an ENTRY-quality decision only. It must never prevent
        # management/protection/exit of an already-open position.
        if not epic_positions and not spread_allows_entry(
            api, epic, market=market, ai_decision=None
        ):
            record_entry_rejection(
                epic,
                "AI_SPREAD_DECISION",
                f"AI rejected current spread; legacy_cap={MAX_SPREAD_PCT:.4f}%",
            )
            return None
        # AI SUPPORT-ONLY: advisory analysis never manages or closes positions.
        # Broker-side SL/TP, trailing and profit protection remain authoritative.
        # Do not call AI position-management actions on the entry path.
        refreshed_positions = api.get_open_positions()
        refreshed_owned = filter_owned_positions(refreshed_positions)
        manage_profit_trailing(api, refreshed_owned, epic, account_currency)
        breakeven_stops(api, refreshed_owned, epic, current_price)
        manage_trailing_stops(api, refreshed_owned, epic, current_price, df=df)
        if position_management_only:
            log(f"{epic}: POSITION MANAGEMENT ONLY | entry scan skipped after AI management.")
            return None
        # STRATEGY IS THE SOLE ENTRY AUTHORITY. AI is advisory only.
        signal = legacy_signal
        strategy_strength = market_entry_strength(df, htf_df, signal) if signal in {"BUY", "SELL"} else 0.0
        ai_support_signal = ai_decision.get("signal") or ai_decision.get("raw_signal")
        ai_support_confidence = float(ai_decision.get("confidence", 0.0) or 0.0)
        log(
            f"{epic}: ENTRY MODE | authority=STRATEGY | strategy={signal} | "
            f"AI_SUPPORT={ai_support_signal or 'NONE'} | AI_confidence={ai_support_confidence:.3f} | "
            f"AI_agrees={ai_support_signal == signal if signal in {'BUY','SELL'} else False} | "
            f"no_ai_entry_gate=True"
        )
        epic_positions = get_positions_for_epic(owned_positions, epic)
        # Manual broker positions do not belong to this bot and must never
        # block a new bot entry or consume the bot's per-epic capacity.
        # Only positions registered in POSITION_OWNERSHIP_FILE are managed
        # and counted by this strategy.
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
                    log(f"{epic}: STRATEGY WAIT; managing existing position only.")
                    return None
                signal = basket_direction
            elif signal != basket_direction:
                log(f"{epic}: signal {signal} conflicts with existing basket {basket_direction}; no new leg.")
                return None
        log(f"{epic}: SIGNAL = {signal}")
        # Candle strategy freshness gate: never re-enter from the same completed
        # candle after a close. This is deliberately based on candle identity,
        # not an arbitrary number of trades or a timer.
        candle_fresh, entry_candle_key, candle_rejection = candle_entry_is_fresh(epic, signal, df)
        if not candle_fresh:
            record_entry_rejection(
                epic,
                candle_rejection,
                f"direction={signal}; candle={entry_candle_key or 'UNKNOWN'}"
            )
            log(
                f"{epic}: CANDLE ENTRY BLOCK | reason={candle_rejection} | "
                f"direction={signal} | candle={entry_candle_key or 'UNKNOWN'}"
            )
            return None
        log(f"{epic}: CANDLE ENTRY CONFIRMED | completed_candle={entry_candle_key} | direction={signal}")
        # Correlation is advisory in flexible-AI mode. AI owns direction; correlation is logged
        # for exposure awareness but must not silently starve valid entries.
        # Execute at the current executable side of the spread:
        # BUY enters at offer/ask, SELL enters at bid.
        execution_price = live_offer if signal == "BUY" else live_bid
        order_spread_pct = market_spread_pct(market)
        log(f"{epic}: EXECUTABLE QUOTE | {signal}={execution_price} | spread={order_spread_pct:.4f}%" if order_spread_pct is not None else f"{epic}: EXECUTABLE QUOTE | {signal}={execution_price} | spread=N/A")
        # Strategy-only entry gate. AI confidence is informational and cannot delay,
        # block, reverse or modify the strategy entry.
        strength = strategy_strength
        regime_now = "25SEP_CLASSIC"
        vol_ratio_now = 1.0
        strong_signal = True
        log(f"{epic}: CLASSIC ENTRY | strategy=25SEP | strength={strength:.2f} | filters=MINIMAL")
        trade = calculate_trade(df, signal, entry_price=execution_price, strength=strength, epic=epic, ai_decision=None)
        if trade is None:
            log(f"{epic}: executable price is stretched versus completed candle; wait for next scan.")
            return None
        log(f"{epic}: DYNAMIC PRICE | strength={strength:.2f} | entry={trade['entry']} | SL={trade['stop_level']} | ATR={trade['atr']:.6f}")
        sizing_balance = min(float(balance), float(getattr(config, "BALANCE_CAP", balance)))
        existing_count = len(epic_positions)
        # No arbitrary position-count cap. Additional exposure is governed only
        # by the strategy signal plus the live basket/portfolio risk budget.
        log(
            f"{epic}: POSITION CAP | fixed_count_limit=DISABLED | "
            f"existing={existing_count} | risk_budget_controls_entry=True"
        )
        if epic_positions:
            last_entry = LAST_ENTRY_AT.get(epic)
            if (
                last_entry is not None
                and not strong_signal
                and time.monotonic() - last_entry < PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS
            ):
                remaining = PROFITABLE_ADD_ENTRY_COOLDOWN_SECONDS - (time.monotonic() - last_entry)
                record_entry_rejection(epic, "PROFITABLE_ADD_COOLDOWN", f"remaining={remaining:.0f}s")
                return None
        # Reuse the current market snapshots already fetched for this scan.
        # Risk calculations used to request the same market repeatedly.
        risk_market_cache = {epic: market}
        # Risk budgets are strategy-owned: manual positions are not counted
        # against this bot's basket/portfolio allocation. Account-level safety
        # (daily loss/equity protection) still sees the full broker account.
        reserved_risk = basket_reserved_risk(api, owned_positions, epic, account_currency, market_cache=risk_market_cache)
        portfolio_reserved = portfolio_reserved_risk(api, owned_positions, account_currency, market_cache=risk_market_cache)
        max_basket_amount = sizing_balance * MAX_BASKET_RISK
        max_portfolio_amount = sizing_balance * MAX_PORTFOLIO_RISK
        # Unknown reserved risk is fail-closed for NEW entries only. Existing
        # positions continue to receive profit/loss protection.
        if reserved_risk is None or portfolio_reserved is None:
            record_entry_rejection(epic, "RISK_DATA_UNAVAILABLE", "reserved risk could not be verified")
            log(f"{epic}: risk data unavailable; new entry skipped, existing positions remain managed.")
            return None
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

        legacy_risk_multiplier = adaptive_risk_multiplier(df)
        # AI is advisory only: it cannot change entry direction or risk sizing.
        risk_multiplier = float(np.clip(legacy_risk_multiplier, PORTFOLIO_RISK_MIN_MULTIPLIER, PORTFOLIO_RISK_MAX_MULTIPLIER))
        requested_risk *= risk_multiplier
        log(f"{epic}: AI ASSISTANT | risk remains strategy-owned | multiplier={risk_multiplier:.3f} | requested={requested_risk:.2f} | legacy_vol_mult={legacy_risk_multiplier:.2f}")
        # Hard cap: every new position may risk at most MAX_LOSS_PER_POSITION
        # in account currency. This also caps the secondary loss guard below.
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
            for p in owned_positions:
                rp = dict(p)
                rp["risk_amount_account"] = estimated_position_risk_account(
                    p, api, account_currency, market_cache=risk_market_cache
                )
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
                candle_cache=candle_cache,
                candle_cache_ttl=candle_cache_ttl,
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

            if strong_signal and profitable_position:
                log(f"{epic}: profitable basket + strong AI signal; add-on evaluated within remaining risk budget.")
            else:
                # Never add to a losing/flat basket. Grid, averaging, and
                # martingale are disabled; extra legs are allowed only when
                # an existing position in the same direction is profitable.
                record_entry_rejection(epic, "BASKET_NOT_PROFITABLE")
                return None
        size = get_position_size(api, epic, risk_amount, trade["risk_distance"], account_currency, market=market)
        if size is None:
            record_entry_rejection(epic, "MIN_TRADE_SIZE_EXCEEDS_RISK")
            return None
        log(f"{epic}: risk budget={risk_amount:.2f}; entry={trade['entry']}; SL={trade['stop_level']}; TP={trade['profit_level']}; size={size}")
        if DEMO_ONLY and str(getattr(config, "IS_DEMO", "true")).lower() not in ("true", "1", "yes"):
            raise RuntimeError("DEMO_ONLY=True but IS_DEMO is not enabled.")
        # Recheck broker status immediately before submitting, including add-on legs.
        fresh_market = api.get_market(epic)
        fresh_snapshot = fresh_market.get("snapshot", {}) or {}
        fresh_status = str(fresh_snapshot.get("marketStatus") or fresh_market.get("marketStatus") or "").upper()
        if fresh_status != "TRADEABLE":
            record_entry_rejection(epic, "MARKET_NOT_TRADEABLE", f"broker_status={fresh_status or 'UNKNOWN'}")
            return None
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
            # Capital.com may return the actual opened position IDs inside
            # affectedDeals rather than as the top-level dealId. Those are the
            # permanent IDs required by /positions/{dealId}.
            affected_deals = confirmation.get("affectedDeals") if isinstance(confirmation.get("affectedDeals"), list) else []
            opened_deal_ids = [
                str(item.get("dealId"))
                for item in affected_deals
                if isinstance(item, dict)
                and item.get("dealId")
                and str(item.get("status", "")).upper() in {"OPENED", "OPEN"}
            ]
            top_level_deal_id = confirmation.get("dealId")
            if top_level_deal_id:
                opened_deal_ids.insert(0, str(top_level_deal_id))
            opened_deal_ids = list(dict.fromkeys(opened_deal_ids))
            # Never use dealReference as a substitute for the permanent opened
            # position/deal ID. Without a real opened deal ID we cannot safely
            # verify direction, price, SL/TP, ownership, or later closure.
            if not opened_deal_ids:
                raise RuntimeError(
                    f"Capital.com confirmation has no opened dealId for {deal_reference}; execution state unconfirmed."
                )
            deal_id = opened_deal_ids[0]
            for opened_deal_id in opened_deal_ids:
                register_owned_position(opened_deal_id)
            # HARD POST-FILL DIRECTION CHECK: the requested direction is not
            # trusted until the broker confirms the opened position itself.
            confirmed_positions = []
            if deal_id:
                try:
                    confirmed_positions = api.get_open_positions()
                except Exception as position_exc:
                    raise RuntimeError(f"Post-fill position verification failed: {position_exc}")
            confirmed_direction = _confirmed_position_direction(
                confirmation, confirmed_positions=confirmed_positions, deal_id=deal_id
            )
            log(
                f"{epic}: POST-FILL DIRECTION CHECK | requested={signal} | "
                f"confirmed={confirmed_direction or 'UNKNOWN'} | deal={deal_id}"
            )
            if confirmed_direction not in {"BUY", "SELL"}:
                if deal_id:
                    try:
                        close_response = api.close_position(deal_id)
                        close_confirmed = confirm_position_closed(api, deal_id)
                        log(
                            f"{epic}: POST-FILL DIRECTION UNKNOWN | emergency_close={close_response} | "
                            f"confirmed_closed={close_confirmed}"
                        )
                    except Exception as close_exc:
                        log(f"{epic}: POST-FILL DIRECTION UNKNOWN | emergency close failed: {close_exc}")
                raise RuntimeError("Broker opened deal but its direction could not be verified; trade rejected fail-closed.")
            if confirmed_direction != signal:
                try:
                    close_response = api.close_position(deal_id)
                    close_confirmed = confirm_position_closed(api, deal_id)
                    log(
                        f"{epic}: POST-FILL DIRECTION MISMATCH | requested={signal} | "
                        f"confirmed={confirmed_direction} | deal={deal_id} | "
                        f"emergency_close={close_response} | confirmed_closed={close_confirmed}"
                    )
                except Exception as close_exc:
                    log(f"{epic}: POST-FILL DIRECTION MISMATCH | emergency close failed: {close_exc}")
                raise RuntimeError(
                    f"Broker direction mismatch: requested={signal}, confirmed={confirmed_direction}"
                )

            actual_fill_price = _confirmed_entry_level(confirmation)
            if actual_fill_price is None and deal_id:
                for opened in confirmed_positions:
                    if str(position_deal_id(opened)) == str(deal_id):
                        actual_fill_price = position_open_level(opened)
                        break
            record_execution_quality(epic, signal, execution_price, actual_fill_price, order_spread_pct, deal_reference=deal_reference, deal_id=deal_id, deal_status=deal_status or "ACCEPTED", size=size)
            # Persist an outcome-training row only after the broker confirms
            # the position. P/L is filled later from broker transaction history.
            try:
                adverse_slip = None
                if actual_fill_price is not None and execution_price:
                    adverse = (actual_fill_price - execution_price) if signal == "BUY" else (execution_price - actual_fill_price)
                    adverse_slip = max(0.0, adverse / execution_price * 100.0)
                ai_outcomes.record_entry(
                    deal_id=deal_id,
                    deal_reference=deal_reference,
                    entry_time=datetime.now(timezone.utc).isoformat(),
                    epic=epic,
                    direction=signal,
                    entry_price=actual_fill_price if actual_fill_price is not None else execution_price,
                    stop_loss=trade.get("stop_level"),
                    take_profit=trade.get("profit_level"),
                    size=size,
                    spread_pct=order_spread_pct,
                    slippage_pct=adverse_slip,
                    ai=ai_decision,
                    strategy_id=STRATEGY_ID,
                    regime=ai_decision.get("regime"),
                    volatility=ai_decision.get("volatility") or ai_decision.get("vol_ratio"),
                    features=ai_feature_snapshot,
                    reason=ai_decision.get("reason"),
                )
                log(f"{epic}: AI OUTCOME LEDGER | deal={deal_id} | pending broker P/L reconciliation")
            except Exception as _outcome_exc:
                log(f"{epic}: AI outcome ledger write failed: {_outcome_exc}")
        else:
            # Never treat an order as successfully managed without a broker
            # deal reference. The POST response is not sufficient proof of an
            # opened/managed position, so fail closed instead of starting a
            # cooldown on an unconfirmed trade.
            log(f"{epic}: ERROR - no dealReference returned; order confirmation unavailable.")
            record_execution_quality(epic, signal, execution_price, None, order_spread_pct, deal_status="UNCONFIRMED", size=size)
            raise RuntimeError("Capital.com order returned no dealReference; execution state is unconfirmed.")

        ERROR_STREAKS.pop(epic, None)
        SAFETY["consecutive_errors"] = 0
        LAST_ENTRY_AT[epic] = time.monotonic()
        # Persist the completed candle that actually authorized the confirmed
        # broker fill. If the order was not confirmed, this marker is never saved.
        mark_candle_entry(epic, signal, entry_candle_key)
        save_safety_state(SAFETY)

        # Further entries are evaluated on the next scheduled scan, not through
        # recursive API calls. Portfolio and basket risk remain hard safeguards.
        return response
    except Exception as exc:
        error_text = str(exc)
        # Capital.com returns 404 for an epic that is not available in the
        # selected account/market universe. This is not a system failure and
        # must never trip the global kill switch.
        if "404" in error_text or "market not found" in error_text.lower() or "epic not found" in error_text.lower():
            UNAVAILABLE_EPICS.add(epic)
            record_entry_rejection(epic, "MARKET_UNAVAILABLE", error_text)
            log(f"{epic}: market unavailable; isolated from safety error counter: {exc}")
            return None
        ERROR_STREAKS[epic] = int(ERROR_STREAKS.get(epic, 0)) + 1
        # Keep legacy state for reporting, but it no longer controls all markets.
        SAFETY["consecutive_errors"] = ERROR_STREAKS[epic]
        save_safety_state(SAFETY)
        log(f"{epic}: ERROR ({ERROR_STREAKS[epic]}/{MAX_CONSECUTIVE_ERRORS}): {exc}")
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

        open_positions = filter_owned_positions(api.get_open_positions())
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
    log(f"Risk-budgeted entries: Grid={ALLOW_GRID}, Averaging={ALLOW_AVERAGING}, Martingale={ALLOW_MARTINGALE}; no fixed position-count cap; max basket risk={MAX_BASKET_RISK * 100:.1f}%.")
    api = CapitalAPI()
    log("Logging in to Capital.com...")
    api.login()

    # Start the authenticated Capital.com WebSocket once per bot run.
    # It streams live bid/offer prices while the normal AI/candle engine runs.
    global LIVE_PRICE_STREAM
    try:
        LIVE_PRICE_STREAM = CapitalLivePriceStream(
            cst=api.cst,
            security_token=api.security_token,
            epics=EPICS,
            log_fn=log,
        )
        LIVE_PRICE_STREAM.start()
        log(
            f"LIVE PRICE FEED | WebSocket active | markets={len(EPICS)} | "
            f"max_quote_age={LIVE_PRICE_MAX_AGE_SECONDS:.1f}s"
        )
    except Exception as live_start_exc:
        LIVE_PRICE_STREAM = None
        log(f"LIVE PRICE FEED | unavailable; REST fallback active: {live_start_exc}")

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
    try:
        reconciliation = ai_outcomes.reconcile(api)
        dataset_report = ai_outcomes.build_training_dataset()
        log(f"AI OUTCOME RECONCILIATION | {reconciliation} | DATASET={dataset_report}")
    except Exception as outcome_exc:
        log(f"AI outcome reconciliation unavailable; trading continues safely: {outcome_exc}")
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
    # Keep the live WebSocket/fast scanner active for the remainder of the cycle.
    # This allows new entries during the cycle instead of waiting for the next
    # 15-minute GitHub Actions invocation.
    monitor_open_positions(api, account_currency)
    save_live_stats(api, account_currency)
    try:
        ai_health = professional_ai_monitor.run()
        log(f"AI HEALTH | status={ai_health.get('status', 'UNKNOWN')} | samples={ai_health.get('samples', 0)} | anomalies={ai_health.get('anomalies', [])}")
    except Exception as ai_health_exc:
        log(f"AI HEALTH | monitor warning: {ai_health_exc}")
    if LIVE_PRICE_STREAM is not None:
        try:
            LIVE_PRICE_STREAM.stop()
        except Exception as live_stop_exc:
            log(f"LIVE PRICE FEED | shutdown warning: {live_stop_exc}")
        LIVE_PRICE_STREAM = None
    log("Trading cycle completed.")

if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        log(f"MAIN ERROR: {exc}")
        traceback.print_exc()