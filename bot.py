import json
import math
import os
import time
import traceback

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

MIN_TRADE_SIZE = {"GOLD": 0.01, "EURUSD": 0.01, "SILVER": 1.0, "OIL_CRUDE": 0.01, "US100": 0.01, "US500": 0.01}
STATE_FILE = "trades_state.json"

def log(message):
    print(f"[BOT] {message}")

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

def get_position_size(api, epic, risk_amount_account, risk_distance):
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
    account_to_quote = 3.6725
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

def generate_signal(df, epic, htf_df=None):
    if len(df) < max(EMA_SLOW + 5, RSI_PERIOD + 5, ATR_PERIOD + 5) or htf_df is None or len(htf_df) < HTF_EMA_SLOW + 5:
        return None
    htf_fast = htf_df["close"].ewm(span=HTF_EMA_FAST, adjust=False).mean().iloc[-2]
    htf_slow = htf_df["close"].ewm(span=HTF_EMA_SLOW, adjust=False).mean().iloc[-2]
    atr_fast = df["atr"].rolling(VOL_REGIME_FAST).mean().iloc[-2]
    atr_slow = df["atr"].rolling(VOL_REGIME_SLOW).mean().iloc[-2]
    if pd.isna(atr_fast) or pd.isna(atr_slow) or atr_slow <= 0 or atr_fast / atr_slow < VOL_REGIME_MIN:
        return None
    previous, current = df.iloc[-3], df.iloc[-2]
    if any(pd.isna(current[key]) or pd.isna(previous[key]) for key in ["ema_fast", "ema_slow"]) or pd.isna(current["rsi"]) or pd.isna(current["atr"]):
        return None
    long_min, long_max, short_min, short_max = get_rsi_settings(epic)
    bullish_cross = previous["ema_fast"] <= previous["ema_slow"] and current["ema_fast"] > current["ema_slow"]
    bearish_cross = previous["ema_fast"] >= previous["ema_slow"] and current["ema_fast"] < current["ema_slow"]
    if bullish_cross and htf_fast > htf_slow and long_min <= current["rsi"] <= long_max:
        return "BUY"
    if bearish_cross and htf_fast < htf_slow and short_min <= current["rsi"] <= short_max:
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

def estimated_position_risk_account(position, api):
    entry, stop, size = position_open_level(position), position_stop_level(position), position_size_value(position)
    if entry is None or stop is None or size <= 0:
        return 0.0
    try:
        market = api.get_market(position_epic(position))
        lot_size = safe_float(market.get("instrument", {}).get("lotSize"), 1.0) or 1.0
        return abs(entry - stop) * size * lot_size * 3.6725
    except Exception:
        return 0.0

def basket_reserved_risk(api, positions, epic):
    return sum(estimated_position_risk_account(p, api) for p in get_positions_for_epic(positions, epic))

def manage_trailing_stops(api, positions, epic, current_price):
    if not TRAILING_ENABLED:
        return
    for position in get_positions_for_epic(positions, epic):
        deal_id, direction, entry, current_sl = position_deal_id(position), position_direction(position), position_open_level(position), position_stop_level(position)
        if not deal_id or not direction or entry is None:
            continue
        deal_key = str(deal_id)
        stored_risk = safe_float(STATE["risk_distance"].get(deal_key))
        if stored_risk is None:
            if current_sl is None:
                continue
            stored_risk = abs(entry - current_sl)
            if stored_risk <= 0:
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

def process_epic(api, epic, positions, balance):
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
        manage_trailing_stops(api, positions, epic, current_price)
        htf_df = candles_to_dataframe(api.get_candles(epic=epic, resolution=HTF_RESOLUTION, max_candles=HTF_CANDLE_COUNT))
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
        reserved_risk = basket_reserved_risk(api, positions, epic)
        max_basket_amount = sizing_balance * MAX_BASKET_RISK
        remaining_basket_risk = max_basket_amount - reserved_risk
        leg_multiplier = MARTINGALE_MULTIPLIER ** existing_count if ALLOW_MARTINGALE else 1.0
        requested_risk = sizing_balance * AGGRESSIVE_BASE_RISK * leg_multiplier
        risk_amount = min(requested_risk, max(0.0, remaining_basket_risk))
        if risk_amount <= 0:
            log(f"{epic}: basket risk cap reached ({MAX_BASKET_RISK * 100:.1f}%).")
            return None
        if epic_positions:
            latest = epic_positions[-1]
            latest_entry, latest_stop = position_open_level(latest), position_stop_level(latest)
            latest_r = abs(latest_entry - latest_stop) if latest_entry is not None and latest_stop is not None else trade["risk_distance"]
            if latest_entry is None or latest_r <= 0:
                return None
            adverse_move = latest_entry - trade["entry"] if signal == "BUY" else trade["entry"] - latest_entry
            if adverse_move < latest_r * GRID_STEP_R:
                log(f"{epic}: basket exists but grid distance not reached; no averaging leg.")
                return None
        size = get_position_size(api, epic, risk_amount, trade["risk_distance"])
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
            return response
        response = api.place_order(direction=signal, size=size, stop_level=trade["stop_level"], profit_level=trade["profit_level"], epic=epic)
        log(f"{epic}: ORDER SENT")
        log(f"{epic}: {response}")
        return response
    except Exception as exc:
        log(f"{epic}: ERROR: {exc}")
        traceback.print_exc()
        return None

def run_cycle():
    log("Starting trading cycle...")
    log("DEMO MODE / LIVE TRADING DISABLED")
    log(f"Controlled aggressive mode: Grid={ALLOW_GRID}, Averaging={ALLOW_AVERAGING}, Martingale={ALLOW_MARTINGALE}; max positions/epic={MAX_POSITIONS_PER_EPIC}, grid step={GRID_STEP_R}R, martingale x{MARTINGALE_MULTIPLIER}, max basket risk={MAX_BASKET_RISK * 100:.1f}%.")
    api = CapitalAPI()
    log("Logging in to Capital.com...")
    api.login()
    balance = api.get_balance()
    log(f"Account balance: {balance}")
    for cycle_epic in EPICS:
        # Refresh account state before EVERY epic so newly opened/closed positions
        # are immediately reflected in subsequent decisions within this run.
        positions = api.get_open_positions()
        log(f"Open positions before {cycle_epic}: {len(positions)}")
        cleanup_state(positions)
        process_epic(api=api, epic=cycle_epic, positions=positions, balance=balance)
        time.sleep(1)
    log("Trading cycle completed.")

if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        log(f"MAIN ERROR: {exc}")
        traceback.print_exc()
