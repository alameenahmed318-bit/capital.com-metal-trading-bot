"""Capital.com Metals AI Bot — METAL IMPERIUM IRON V1.

This wrapper intentionally overrides only the metals strategy runtime.
The FX bot and its Dynamic Momentum Hybrid V7 implementation are untouched.

Entry authority:
  M5 -> EMA9/EMA21 -> RSI14 -> 14-bar breakout -> ATR -> confirmation.
Risk:
  1% per trade, 2% maximum reserved basket risk, one position per epic.
Protection:
  1R break-even, 1.5R trailing, broker SL/TP remain authoritative.
No grid / martingale / averaging.
AI is support/diagnostic only and cannot create a trade direction.
"""
from datetime import datetime, timezone
import json
import os

import pandas as pd

import bot as base

STRATEGY_ID = "METAL_IMPERIUM_IRON_V1"
base.STRATEGY_ID = STRATEGY_ID

# Metals state is isolated from FX state.
base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

# Metals only.
base.EPICS = ["GOLD", "SILVER"]
base.RESOLUTION = "MINUTE_5"
base.CANDLE_COUNT = 300
base.HTF_RESOLUTION = "MINUTE_15"
base.HTF_CANDLE_COUNT = 300

# Unified risk/execution profile.
base.AGGRESSIVE_BASE_RISK = 0.01
base.MAX_BASKET_RISK = 0.02
base.MAX_POSITIONS_PER_EPIC = 1
base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

# Iron strategy parameters.
IRON_EMA_FAST = 9
IRON_EMA_SLOW = 21
IRON_RSI_PERIOD = 14
IRON_RSI_BUY = 52.0
IRON_RSI_SELL = 48.0
IRON_BREAKOUT_LOOKBACK = 14
IRON_BREAKOUT_BUFFER_ATR = 0.20
IRON_MIN_ATR = 0.0  # Symbol-specific ATR is checked as positive; no arbitrary price-unit gate.
IRON_SL_ATR = 1.80
IRON_TP_ATR = 2.20
IRON_BE_R = 1.00
IRON_BE_OFFSET_R = 0.05
IRON_TRAIL_START_R = 1.50
IRON_TRAIL_ATR = 1.00

# Keep the base manager aligned with the iron protection policy.
base.SL_ATR_MULT = IRON_SL_ATR
base.TP_ATR_MULT = IRON_TP_ATR
base.BREAKEVEN_ENABLED = True
base.BREAKEVEN_START_R = IRON_BE_R
base.BREAKEVEN_OFFSET_R = IRON_BE_OFFSET_R
base.TRAILING_ENABLED = True
base.TRAILING_START_R = IRON_TRAIL_START_R
base.TRAILING_DISTANCE_R = IRON_TRAIL_ATR

# AI cannot veto a valid iron signal merely by becoming unavailable.
# It remains diagnostic/support-only on this strategy.
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False

base.reload_runtime_state()


def _rsi_atr_frame(df):
    if df is None or len(df) < max(60, IRON_BREAKOUT_LOOKBACK + 5):
        return None
    d = df.copy()
    try:
        d = base.add_indicators(d)
        return d
    except Exception as exc:
        base.log(f"IRON indicators unavailable | {exc}")
        return None


def iron_signal(df, epic, htf_df=None):
    """Single metals entry authority.

    Uses the latest available M5 observation and compares it with the
    preceding 14 closed M5 candles. M15 is informational only.
    """
    d = _rsi_atr_frame(df)
    if d is None:
        return None

    try:
        cur = d.iloc[-1]
        close = base.safe_float(cur.get("close"))
        atr = base.safe_float(cur.get("atr"))
        rsi = base.safe_float(cur.get("rsi"))

        if close is None or atr is None or rsi is None or atr <= 0:
            return None

        ema9 = float(d["close"].ewm(span=IRON_EMA_FAST, adjust=False).mean().iloc[-1])
        ema21 = float(d["close"].ewm(span=IRON_EMA_SLOW, adjust=False).mean().iloc[-1])

        # Exclude the current observation from the breakout range.
        prior = d.iloc[-(IRON_BREAKOUT_LOOKBACK + 1):-1]
        if len(prior) < IRON_BREAKOUT_LOOKBACK:
            return None

        resistance = float(prior["high"].max())
        support = float(prior["low"].min())
        buy_zone = resistance + IRON_BREAKOUT_BUFFER_ATR * atr
        sell_zone = support - IRON_BREAKOUT_BUFFER_ATR * atr

        buy = close > buy_zone and ema9 > ema21 and rsi >= IRON_RSI_BUY
        sell = close < sell_zone and ema9 < ema21 and rsi <= IRON_RSI_SELL

        if buy and not sell:
            base.log(
                f"{epic}: IRON BUY | M5 | EMA9>EMA21 | RSI={rsi:.1f} | "
                f"breakout=+{(close-resistance)/atr:.2f}ATR | buffer=0.20ATR"
            )
            return "BUY"

        if sell and not buy:
            base.log(
                f"{epic}: IRON SELL | M5 | EMA9<EMA21 | RSI={rsi:.1f} | "
                f"breakout=+{(support-close)/atr:.2f}ATR | buffer=0.20ATR"
            )
            return "SELL"

    except Exception as exc:
        base.log(f"{epic}: IRON signal unavailable | {exc}")

    return None


def m5_entry_direction(micro_frames, epic=None):
    if not isinstance(micro_frames, dict):
        return None
    m5 = micro_frames.get("M5_DF")
    if m5 is None:
        return None
    return iron_signal(m5, epic, None)


def generate_signal(df, epic, htf_df=None):
    return iron_signal(df, epic, htf_df)


def m5_entry_strength(micro_frames, signal):
    if signal not in {"BUY", "SELL"}:
        return 0.0
    m5 = micro_frames.get("M5_DF") if isinstance(micro_frames, dict) else None
    d = _rsi_atr_frame(m5)
    if d is None:
        return 0.0
    try:
        cur = d.iloc[-1]
        rsi = base.safe_float(cur.get("rsi"))
        atr = base.safe_float(cur.get("atr"))
        close = base.safe_float(cur.get("close"))
        if None in (rsi, atr, close) or atr <= 0:
            return 0.0
        prior = d.iloc[-(IRON_BREAKOUT_LOOKBACK + 1):-1]
        resistance = float(prior["high"].max())
        support = float(prior["low"].min())
        breakout_strength = (
            (close - resistance) / atr if signal == "BUY"
            else (support - close) / atr
        )
        ema9 = float(d["close"].ewm(span=9, adjust=False).mean().iloc[-1])
        ema21 = float(d["close"].ewm(span=21, adjust=False).mean().iloc[-1])
        trend_ok = ema9 > ema21 if signal == "BUY" else ema9 < ema21
        rsi_ok = rsi >= IRON_RSI_BUY if signal == "BUY" else rsi <= IRON_RSI_SELL
        score = 0.60
        if trend_ok:
            score += 0.15
        if rsi_ok:
            score += 0.10
        if breakout_strength >= IRON_BREAKOUT_BUFFER_ATR:
            score += 0.10
        return min(score, 0.95)
    except Exception:
        return 0.0


def safety_allows_new_entry(balance, positions, epic=None, account_currency=None):
    """Metals-only 2% daily loss gate using a fixed UTC day-start balance."""
    if not getattr(base, "KILL_SWITCH_ENABLED", True):
        return True

    base.reset_daily_safety(balance)
    equity = base.account_equity(balance, positions)
    start_balance = base.safe_float(base.SAFETY.get("day_start_balance"), balance) or balance

    if start_balance <= 0:
        return False

    daily_floor = start_balance * (1.0 - 0.02)
    if equity <= daily_floor:
        base.log(
            f"{epic}: IRON DAILY LOSS STOP | start={start_balance:.2f} | "
            f"equity={equity:.2f} | limit=2%"
        )
        return False

    if epic is not None and int(base.ERROR_STREAKS.get(epic, 0)) >= base.MAX_CONSECUTIVE_ERRORS:
        base.log(f"{epic}: IRON error isolation stop for this run.")
        return False

    return True


def calculate_trade(df, direction, entry_price=None, strength=1.0, epic=None, ai_decision=None, early_entry=False):
    """Iron risk/target engine: 1.8 ATR SL and 2.2 ATR TP."""
    if df is None or len(df) < 30 or direction not in {"BUY", "SELL"}:
        return None

    current = df.iloc[-1]
    atr = base.safe_float(current.get("atr"))
    price = base.safe_float(entry_price) if entry_price is not None else base.safe_float(current.get("close"))
    if atr is None or atr <= 0 or price is None:
        return None

    stop_distance = IRON_SL_ATR * atr
    target_distance = IRON_TP_ATR * atr

    stop_level = price - stop_distance if direction == "BUY" else price + stop_distance
    profit_level = price + target_distance if direction == "BUY" else price - target_distance

    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": profit_level,
        "risk_distance": stop_distance,
        "atr": atr,
        "signal_strength": strength,
    }


# Ensure the base execution path resolves these metals-only overrides.
base.generate_signal = generate_signal
base.m5_entry_direction = m5_entry_direction
base.m5_entry_strength = m5_entry_strength
base.calculate_trade = calculate_trade
base.safety_allows_new_entry = safety_allows_new_entry


def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | M5 authority | M15 support | "
        f"markets={base.EPICS} | risk=1% | max_basket=2% | "
        f"SL=1.8ATR | TP=2.2ATR | BE=1R | TRAIL=1.5R"
    )
    return base.run_cycle()


if __name__ == "__main__":
    run_cycle()
