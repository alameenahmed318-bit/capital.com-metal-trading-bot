import json
import math
import os
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from capital_api import CapitalAPI

DEMO_ONLY = True
ALLOW_GRID = True
ALLOW_MARTINGALE = True
ALLOW_AVERAGING = True

MAX_POSITIONS_PER_EPIC = 3
GRID_STEP_R = 0.75
MARTINGALE_MULTIPLIER = 1.25
AGGRESSIVE_BASE_RISK = 0.015
MAX_BASKET_RISK = 0.04

EPICS = ["GOLD", "EURUSD", "SILVER", "OIL_CRUDE", "US100", "US500"]

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
XAU_WORKING_ORDER_ENABLED = getattr(config, "XAU_WORKING_ORDER_ENABLED", True)
XAU_WORKING_TRIGGER = getattr(config, "XAU_WORKING_TRIGGER", 4400.0)

MARKET_RSI_SETTINGS = {
    "GOLD": (42, 68, 32, 58),
    "EURUSD": (38, 62, 28, 55),
    "SILVER": (45, 72, 30, 58),
    "OIL_CRUDE": (40, 62, 28, 55),
    "US100": (42, 65, 28, 55),
    "US500": (42, 65, 28, 55),
}
MARKET_BIAS = {epic: "BOTH" for epic in EPICS}

SL_ATR_MULT = getattr(config, "SL_ATR_MULT", 1.5)
TP_ATR_MULT = getattr(config, "TP_ATR_MULT", 3.0)
TRAILING_ENABLED = True
TRAILING_START_R = 1.0
TRAILING_DISTANCE_R = 1.0

# Strategy Selector: automatically classify market regime and choose Trend/Breakout/Range.
STRATEGY_SELECTOR_ENABLED = True
TREND_EMA_GAP_ATR = 0.35
RANGE_EMA_GAP_ATR = 0.15
RANGE_RSI_BUY_MAX = 42
RANGE_RSI_SELL_MIN = 58

# Strategy v2 filters
USE_SUPPORT_RESISTANCE = True
USE_BREAKOUT_CONFIRMATION = True
SR_LOOKBACK = 60
SR_BUFFER_ATR = 0.35
BREAKOUT_LOOKBACK = 20
# When enabled, a profitable existing basket can add legs immediately
# (without waiting for the normal grid distance) until the per-epic cap.
ADD_TO_PROFITABLE_BASKET = True

# Free, local risk/execution protections (no external paid service).
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_PCT = 0.08
DAILY_LOSS_LIMIT_PCT = 0.03
EQUITY_DRAWDOWN_LIMIT_PCT = 0.05
LOSS_COOLDOWN_MINUTES = 20
SIDEWAYS_FILTER_ENABLED = True
SIDEWAYS_ATR_RATIO_MAX = 0.90
BREAKEVEN_ENABLED = True
BREAKEVEN_START_R = 0.75
BREAKEVEN_OFFSET_R = 0.05
KILL_SWITCH_ENABLED = True
MAX_CONSECUTIVE_ERRORS = 3
SAFETY_STATE_FILE = "bot_safety_state.json"

MIN_TRADE_SIZE = {"GOLD": 0.01, "EURUSD": 0.01, "SILVER": 1.0, "OIL_CRUDE": 0.01, "US100": 0.01, "US500": 0.01}
STATE_FILE = "trades_state.json"

def log(message):
    print(f"[BOT] {message}")

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

def position_unrealized_pnl(position):
    value = position.get("profitLoss")
    if value is None:
        value = position.get("position", {}).get("profitLoss")
    return safe_float(value, 0.0) or 0.0

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
    daily_floor = start_balance * (1.0 - DAILY_LOSS_LIMIT_PCT)
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
        return {"risk_distance": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            return {"risk_distance": {}}
        state.setdefault("risk_distance", {})
        return state
    except Exception as exc:
        log(f"Could not load state file: {exc}")
        return {"risk_distance": {}}

def save_state(state):
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
    os.replace(temp_file, STATE_FILE)

STATE = load_state()

def safe_float(value, default=None):
    try:
        number = float(value)
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

def position_epic(position):
    return position.get("epic") or position.get("position", {}).get("epic")

def position_deal_id(position):
    return position.get("dealId") or position.get("position", {}).get("dealId") or position.get("dealReference")

def position_direction(position):
    return normalize_direction(position.get("direction") or position.get("position", {}).get("direction"))

def position_open_level(position):
    return safe_float(position.get("level") or position.get("openLevel") or position.get("position", {}).get("level") or position.get("position", {}).get("openLevel"))

def position_stop_level(position):
    return safe_float(position.get("stopLevel") or position.get("position", {}).get("stopLevel"))

def position_profit_level(position):
    return safe_float(position.get("profitLevel") or position.get("position", {}).get("profitLevel"))

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


def generate_signal(df, epic, htf_df=None):
    if len(df) < max(EMA_SLOW + 5, RSI_PERIOD + 5, ATR_PERIOD + 5, SR_LOOKBACK + 5, BREAKOUT_LOOKBACK + 5) or htf_df is None or len(htf_df) < HTF_EMA_SLOW + 5:
        return None

    current = df.iloc[-2]
    previous = df.iloc[-3]
    atr = safe_float(current["atr"])
    if atr is None or atr <= 0:
        return None

    htf_fast = htf_df["close"].ewm(span=HTF_EMA_FAST, adjust=False).mean().iloc[-2]
    htf_slow = htf_df["close"].ewm(span=HTF_EMA_SLOW, adjust=False).mean().iloc[-2]

    long_min, long_max, short_min, short_max = get_rsi_settings(epic)
    rsi = safe_float(current["rsi"])
    if rsi is None:
        return None

    recent = df.iloc[-(SR_LOOKBACK + 1):-1]
    support = float(recent["low"].min())
    resistance = float(recent["high"].max())
    price = float(current["close"])
    near_support = price <= support + SR_BUFFER_ATR * atr
    near_resistance = price >= resistance - SR_BUFFER_ATR * atr

    breakout_high = float(df.iloc[-(BREAKOUT_LOOKBACK + 1):-1]["high"].max())
    breakout_low = float(df.iloc[-(BREAKOUT_LOOKBACK + 1):-1]["low"].min())
    bullish_breakout = price > breakout_high
    bearish_breakout = price < breakout_low

    regime = market_regime(df, htf_df)
    if not STRATEGY_SELECTOR_ENABLED:
        regime = "TREND"

    log(f"{epic}: STRATEGY SELECTOR = {regime}")

    if regime == "BREAKOUT":
        # Breakout entries require confirmation from the closed candle, HTF direction,
        # and RSI; this avoids treating every range touch as a breakout.
        if bullish_breakout and htf_fast > htf_slow and long_min <= rsi <= long_max:
            return "BUY"
        if bearish_breakout and htf_fast < htf_slow and short_min <= rsi <= short_max:
            return "SELL"
        return None

    if regime == "TREND":
        bullish_cross = previous["ema_fast"] <= previous["ema_slow"] and current["ema_fast"] > current["ema_slow"]
        bearish_cross = previous["ema_fast"] >= previous["ema_slow"] and current["ema_fast"] < current["ema_slow"]
        buy_setup = bullish_cross and htf_fast > htf_slow and long_min <= rsi <= long_max
        sell_setup = bearish_cross and htf_fast < htf_slow and short_min <= rsi <= short_max

        # If the trend is already established, allow a clean pullback continuation
        # rather than requiring a brand-new EMA cross on every opportunity.
        ema_gap = abs(float(current["ema_fast"] - current["ema_slow"]))
        if ema_gap >= TREND_EMA_GAP_ATR * atr:
            buy_setup = buy_setup or (
                current["ema_fast"] > current["ema_slow"]
                and htf_fast > htf_slow
                and long_min <= rsi <= long_max
                and not near_resistance
            )
            sell_setup = sell_setup or (
                current["ema_fast"] < current["ema_slow"]
                and htf_fast < htf_slow
                and short_min <= rsi <= short_max
                and not near_support
            )

        if buy_setup:
            return "BUY"
        if sell_setup:
            return "SELL"
        return None

    # RANGE: mean-reversion only near dynamic support/resistance.
    # Do not combine this with the high-volatility gate used by trend/breakout.
    if near_support and rsi <= RANGE_RSI_BUY_MAX:
        return "BUY"
    if near_resistance and rsi >= RANGE_RSI_SELL_MIN:
        return "SELL"
    return None

def calculate_trade(df, direction):
    current = df.iloc[-1]
    price, atr = safe_float(current["close"]), safe_float(current["atr"])
    if price is None or atr is None or atr <= 0:
        return None
    sl_distance, tp_distance = atr * SL_ATR_MULT, atr * TP_ATR_MULT
    if direction == "BUY":
        stop_level, profit_level = price - sl_distance, price + tp_distance
    elif direction == "SELL":
        stop_level, profit_level = price + sl_distance, price - tp_distance
    else:
        return None
    return {"entry": price, "stop_level": stop_level, "profit_level": profit_level, "risk_distance": sl_distance, "atr": atr}

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

def manage_trailing_stops(api, positions, epic, current_price):
    if not TRAILING_ENABLED:
        return
    for position in get_positions_for_epic(positions, epic):
        deal_id, direction, entry, current_sl = position_deal_id(position), position_direction(position), position_open_level(position), position_stop_level(position)
        if not deal_id or not direction or entry is None:
            continue
        deal_key = str(deal_id)
        stored_risk = original_risk_distance(position)
        if stored_risk is None or stored_risk <= 0:
            continue
        STATE["risk_distance"][deal_key] = stored_risk
        r = stored_risk
        if direction == "BUY":
            if current_price - entry < TRAILING_START_R * r:
                continue
            new_stop = current_price - TRAILING_DISTANCE_R * r
            if current_sl is not None and new_stop <= current_sl:
                continue
        elif direction == "SELL":
            if entry - current_price < TRAILING_START_R * r:
                continue
            new_stop = current_price + TRAILING_DISTANCE_R * r
            if current_sl is not None and new_stop >= current_sl:
                continue
        else:
            continue
        try:
            api.modify_position(deal_id=deal_id, stop_level=new_stop)
            log(f"{epic}: TRAILING STOP UPDATED | {direction} | old SL={current_sl} | new SL={new_stop}")
        except Exception as exc:
            log(f"{epic}: trailing update failed: {exc}")
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

def process_epic(api, epic, positions, balance, account_currency):
    log("")
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
        current_price = safe_float(df.iloc[-1]["close"])
        if current_price is None:
            log(f"{epic}: invalid current price.")
            return None
        # Position management must continue even when new entries are blocked
        # by daily loss, cooldown, spread, or kill-switch protections.
        breakeven_stops(api, positions, epic, current_price)
        manage_trailing_stops(api, positions, epic, current_price)

        if not safety_allows_new_entry(balance, positions):
            return None
        if cooldown_active(epic):
            return None
        if not spread_allows_entry(api, epic):
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
                signal = basket_direction
            elif signal != basket_direction:
                log(f"{epic}: signal {signal} conflicts with existing basket {basket_direction}; no new leg.")
                return None
        log(f"{epic}: SIGNAL = {signal}")
        trade = calculate_trade(df, signal)
        if trade is None:
            return None
        sizing_balance = min(float(balance), float(getattr(config, "BALANCE_CAP", balance)))
        existing_count = len(epic_positions)
        if existing_count >= MAX_POSITIONS_PER_EPIC:
            log(f"{epic}: max {MAX_POSITIONS_PER_EPIC} basket positions reached.")
            return None
        reserved_risk = basket_reserved_risk(api, positions, epic, account_currency)
        portfolio_reserved = portfolio_reserved_risk(api, positions, account_currency)
        max_basket_amount = sizing_balance * MAX_BASKET_RISK
        max_portfolio_amount = sizing_balance * MAX_PORTFOLIO_RISK
        remaining_basket_risk = max_basket_amount - reserved_risk
        remaining_portfolio_risk = max_portfolio_amount - portfolio_reserved
        if remaining_portfolio_risk <= 0:
            log(f"{epic}: portfolio risk cap reached ({MAX_PORTFOLIO_RISK * 100:.1f}%).")
            return None
        leg_multiplier = MARTINGALE_MULTIPLIER ** existing_count if ALLOW_MARTINGALE else 1.0
        requested_risk = sizing_balance * AGGRESSIVE_BASE_RISK * leg_multiplier
        risk_amount = min(
            requested_risk,
            max(0.0, remaining_basket_risk),
            max(0.0, remaining_portfolio_risk),
        )
        if risk_amount <= 0:
            log(f"{epic}: basket risk cap reached ({MAX_BASKET_RISK * 100:.1f}%).")
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
                latest = epic_positions[-1]
                latest_entry, latest_stop = position_open_level(latest), position_stop_level(latest)
                latest_r = abs(latest_entry - latest_stop) if latest_entry is not None and latest_stop is not None else trade["risk_distance"]
                if latest_entry is None or latest_r <= 0:
                    return None
                adverse_move = latest_entry - trade["entry"] if signal == "BUY" else trade["entry"] - latest_entry
                if adverse_move < latest_r * GRID_STEP_R:
                    log(f"{epic}: basket exists but grid distance not reached; no averaging leg.")
                    return None
        size = get_position_size(api, epic, risk_amount, trade["risk_distance"], account_currency)
        if size is None:
            log(f"{epic}: minimum trade size would exceed risk budget. Trade skipped.")
            return None
        log(f"{epic}: risk budget={risk_amount:.2f}; entry={trade['entry']}; SL={trade['stop_level']}; TP={trade['profit_level']}; size={size}")
        if DEMO_ONLY and str(getattr(config, "IS_DEMO", "true")).lower() not in ("true", "1", "yes"):
            raise RuntimeError("DEMO_ONLY=True but IS_DEMO is not enabled.")
        if epic == "GOLD" and XAU_WORKING_ORDER_ENABLED:
            if signal != "BUY":
                log(f"{epic}: working-order rule requires BUY; no order placed.")
                return None
            trigger = float(XAU_WORKING_TRIGGER)
            if trigger <= trade["entry"]:
                log(f"{epic}: price is already at/above the working trigger {trigger}; no new working order placed.")
                return None
            stop_level = trigger - trade["risk_distance"]
            profit_level = trigger + trade["risk_distance"] * (TP_ATR_MULT / SL_ATR_MULT)
            response = api.place_working_order(epic=epic, direction="BUY", size=size, level=trigger, stop_level=stop_level, profit_level=profit_level)
            log(f"{epic}: WORKING BUY ORDER SENT | trigger={trigger}")
            log(f"{epic}: SL={stop_level} | TP={profit_level}")
            log(f"{epic}: {response}")
            SAFETY["consecutive_errors"] = 0
            save_safety_state(SAFETY)
            return response
        response = api.place_order(direction=signal, size=size, stop_level=trade["stop_level"], profit_level=trade["profit_level"], epic=epic)
        log(f"{epic}: ORDER SENT")
        log(f"{epic}: {response}")

        # Capital.com documents that a successful POST /positions response is
        # not by itself proof that the position was opened. Confirm the deal.
        deal_reference = response.get("dealReference") if isinstance(response, dict) else None
        if deal_reference:
            confirmation = api.get_confirmation(deal_reference)
            log(f"{epic}: DEAL CONFIRMATION = {confirmation}")
            deal_status = str(confirmation.get("dealStatus") or confirmation.get("status") or "").upper()
            if deal_status in {"REJECTED", "FAILED"}:
                raise RuntimeError(f"Capital.com rejected deal {deal_reference}: {confirmation}")
        else:
            log(f"{epic}: WARNING - no dealReference returned; order confirmation unavailable.")

        SAFETY["consecutive_errors"] = 0
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
        start = (now.timestamp() - 3600)
        from_date = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        to_date = now.strftime("%Y-%m-%dT%H:%M:%S")
        transactions = api.get_transactions(from_date, to_date)
        for tx in transactions:
            note = str(tx.get("note") or tx.get("description") or tx.get("transactionType") or "").lower()
            if "close" not in note and "closed" not in note:
                continue
            epic = tx.get("instrumentName") or tx.get("epic")
            pnl = safe_float(tx.get("profitAndLoss"))
            if pnl is None:
                pnl = safe_float(tx.get("profitLoss"))
            if pnl is None:
                pnl = safe_float(tx.get("profit"))
            if epic and pnl is not None and pnl < 0 and epic in EPICS:
                set_loss_cooldown(epic)
                log(f"{epic}: recent closed loss detected ({pnl}); cooldown applied for {LOSS_COOLDOWN_MINUTES}m.")
    except Exception as exc:
        log(f"Loss-history check unavailable; continuing safely: {exc}")

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
    reset_daily_safety(balance)
    update_loss_cooldowns_from_history(api)
    for cycle_epic in EPICS:
        # Refresh account state before EVERY epic so newly opened/closed positions
        # are immediately reflected in subsequent decisions within this run.
        positions = api.get_open_positions()
        log(f"Open positions before {cycle_epic}: {len(positions)}")
        cleanup_state(positions)
        process_epic(
            api=api,
            epic=cycle_epic,
            positions=positions,
            balance=balance,
            account_currency=account_currency,
        )
        time.sleep(1)
    log("Trading cycle completed.")

if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        log(f"MAIN ERROR: {exc}")
        traceback.print_exc()
