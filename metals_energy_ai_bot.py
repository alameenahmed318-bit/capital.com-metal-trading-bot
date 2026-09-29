"""Capital.com Metals AI Bot — METAL IMPERIUM IRON V1 (FLUID).

Entry is deliberately simple:
completed M5 candle + EMA9/EMA21 direction + candle momentum.
RSI, breakout, M15 and AI are advisory only and never hard entry vetoes.

Protection remains in the shared engine:
ATR stop, break-even, trailing/profit protection, spread/execution checks,
ownership isolation and portfolio/basket risk budgets.

No grid / martingale / averaging.
"""
from datetime import datetime, timezone

import pandas as pd

import bot as base

STRATEGY_ID = "METAL_IMPERIUM_IRON_V1"
base.STRATEGY_ID = STRATEGY_ID

# ============================================================
# STRATEGY ISOLATION
# ============================================================
base.STRATEGY_ALLOWED_EPICS = ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "US100", "US500"]
base.EPICS = ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "US100", "US500"]

base.POSITION_OWNERSHIP_FILE = "metals_energy_ai_strategy_positions.json"
base.LEGACY_POSITION_OWNERSHIP_FILE = "strategy_positions.json"
base.STATE_FILE = "metals_energy_ai_trades_state.json"
base.OPEN_POSITIONS_FILE = "metals_energy_ai_open_positions.json"
base.SAFETY_STATE_FILE = "metals_energy_ai_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "metals_energy_ai_execution_quality.json"
base.ENTRY_REJECTION_FILE = "metals_energy_ai_entry_rejections.json"
base.ENTRY_CANDLE_STATE_FILE = "metals_energy_ai_entry_candle_state.json"

# ============================================================
# FLUID M5 PROFILE
# ============================================================
base.RESOLUTION = "MINUTE_5"
base.CANDLE_COUNT = 300
base.HTF_RESOLUTION = "MINUTE_15"
base.HTF_CANDLE_COUNT = 300
base.ENTRY_CANDLE_RESOLUTION = base.RESOLUTION
base.STRATEGY_CANDLE_RESOLUTION_SECONDS = 300

base.AGGRESSIVE_BASE_RISK = 0.01
base.MAX_BASKET_RISK = 0.02

# There is deliberately no fixed trade-count gate here.
# Extra legs remain constrained by profitable same-direction exposure,
# basket/portfolio risk and broker execution checks.
base.MAX_POSITIONS_PER_EPIC = None

base.ALLOW_GRID = False
base.ALLOW_MARTINGALE = False
base.ALLOW_AVERAGING = False

# Dynamic protection.
base.SL_ATR_MULT = 1.80
base.TP_ATR_MULT = 0.0
base.BREAKEVEN_ENABLED = True
base.BREAKEVEN_START_R = 1.00
base.BREAKEVEN_OFFSET_R = 0.05
base.TRAILING_ENABLED = True
base.TRAILING_START_R = 1.00
base.TRAILING_DISTANCE_R = 1.00
base.PROFIT_TRAIL_ENABLED = True

# Entry timing: normal <=0.25 ATR from the completed M5 close;
# strong market-derived conditions may use <=0.30 ATR.
MAX_ENTRY_DRIFT_ATR = 0.25
STRONG_ENTRY_DRIFT_ATR = 0.30
base.LATE_ENTRY_MAX_ATR = MAX_ENTRY_DRIFT_ATR
base.LATE_ENTRY_STRONG_MAX_ATR = STRONG_ENTRY_DRIFT_ATR
base.LATE_ENTRY_DYNAMIC_ENABLED = True

# These older strategy gates are not part of the Iron entry authority.
base.USE_SUPPORT_RESISTANCE = False
base.USE_BREAKOUT_CONFIRMATION = False
base.SESSION_FILTER_ENABLED = False
base.CORRELATION_FILTER_ENABLED = False

base.reload_runtime_state()


def _frame(df):
    """Prepare indicator data without using the forming candle for direction."""
    if df is None or len(df) < 30:
        return None
    try:
        return base.add_indicators(df.copy())
    except Exception as exc:
        base.log(f"IRON indicators unavailable | {exc}")
        return None


def _completed_m5_direction(d, epic):
    """Simple Fluid entry authority: completed M5 + EMA9/EMA21 + candle body."""
    if d is None or len(d) < 30:
        return None

    cur = d.iloc[-2]
    prev = d.iloc[-3]

    c = base.safe_float(cur.get("close"))
    o = base.safe_float(cur.get("open"))
    pc = base.safe_float(prev.get("close"))
    e9 = base.safe_float(
        d["close"].ewm(span=9, adjust=False).mean().iloc[-2]
    )
    e21 = base.safe_float(
        d["close"].ewm(span=21, adjust=False).mean().iloc[-2]
    )
    atr = base.safe_float(cur.get("atr"))

    if None in (c, o, pc, e9, e21, atr) or atr <= 0:
        return None

    if c > o and c > pc and e9 > e21:
        return "BUY"

    if c < o and c < pc and e9 < e21:
        return "SELL"

    return None


def is_wick_dangerous(candle, direction):
    """Reject only an exceptionally large opposing wick."""
    try:
        high = base.safe_float(candle.get("high"))
        low = base.safe_float(candle.get("low"))
        open_ = base.safe_float(candle.get("open"))
        close = base.safe_float(candle.get("close"))
        if None in (high, low, open_, close) or high <= low:
            return False

        rng = high - low
        upper = high - max(open_, close)
        lower = min(open_, close) - low

        if direction == "BUY":
            return (upper / rng) > 0.65
        if direction == "SELL":
            return (lower / rng) > 0.65
    except Exception:
        return False

    return False


def iron_signal(df, epic, htf_df=None):
    """
    Fluid entry:
      1) completed M5 candle
      2) EMA9/EMA21 direction
      3) candle momentum
      4) only reject a very large opposing wick

    M15/HTF, RSI, breakout and AI do not veto the technical signal.
    Live price is handled by the shared execution/timing path.
    """
    d = _frame(df)
    if d is None:
        return None

    try:
        direction = _completed_m5_direction(d, epic)
        if direction is None:
            return None

        candle = d.iloc[-2]
        if is_wick_dangerous(candle, direction):
            base.log(f"{epic}: IRON FLUID HOLD | opposing wick >65%")
            return None

        atr = base.safe_float(candle.get("atr"))
        close = base.safe_float(candle.get("close"))
        e9 = base.safe_float(
            d["close"].ewm(span=9, adjust=False).mean().iloc[-2]
        )
        e21 = base.safe_float(
            d["close"].ewm(span=21, adjust=False).mean().iloc[-2]
        )

        base.log(
            f"{epic}: IRON FLUID {direction} | M5 completed | "
            f"EMA9={e9:.5f} EMA21={e21:.5f} | ATR={atr:.6f} | close={close:.5f}"
        )
        return direction

    except Exception as exc:
        base.log(f"{epic}: IRON FLUID signal unavailable | {exc}")
        return None


def m5_entry_direction(micro_frames, epic=None):
    if not isinstance(micro_frames, dict):
        return None
    m5 = micro_frames.get("M5_DF")
    return iron_signal(m5, epic, None)


def generate_signal(df, epic, htf_df=None):
    return iron_signal(df, epic, htf_df)


def m5_entry_strength(micro_frames, signal):
    """Non-blocking strength estimate; never acts as an entry gate."""
    if signal not in {"BUY", "SELL"} or not isinstance(micro_frames, dict):
        return 0.0

    d = _frame(micro_frames.get("M5_DF"))
    if d is None:
        return 0.0

    try:
        cur = d.iloc[-2]
        prev = d.iloc[-3]
        c = base.safe_float(cur.get("close"))
        o = base.safe_float(cur.get("open"))
        pc = base.safe_float(prev.get("close"))
        atr = base.safe_float(cur.get("atr"))
        e9 = base.safe_float(
            d["close"].ewm(span=9, adjust=False).mean().iloc[-2]
        )
        e21 = base.safe_float(
            d["close"].ewm(span=21, adjust=False).mean().iloc[-2]
        )

        if None in (c, o, pc, atr, e9, e21) or atr <= 0:
            return 0.0

        aligned = (
            signal == "BUY"
            and c > o and c > pc and e9 > e21
        ) or (
            signal == "SELL"
            and c < o and c < pc and e9 < e21
        )

        # This is only diagnostic. It does not block an otherwise valid signal.
        strength = 0.75 if aligned else 0.60
        if is_wick_dangerous(cur, signal):
            strength = 0.55

        return strength

    except Exception:
        return 0.0


def calculate_trade(
    df,
    direction,
    entry_price=None,
    strength=1.0,
    epic=None,
    ai_decision=None,
    early_entry=False,
):
    """
    Shared risk model with Fluid timing.
    Uses completed M5 ATR for the stop and live executable price for entry.
    """
    if df is None or len(df) < 30 or direction not in {"BUY", "SELL"}:
        return None

    d = _frame(df)
    if d is None:
        return None

    current = d.iloc[-2]
    atr = base.safe_float(current.get("atr"))
    reference = base.safe_float(current.get("close"))
    price = (
        base.safe_float(entry_price)
        if entry_price is not None
        else reference
    )

    if None in (atr, reference, price) or atr <= 0:
        return None

    # Do not chase a completed M5 move. Strongness is market-derived only.
    max_chase_atr = MAX_ENTRY_DRIFT_ATR
    if float(strength or 0.0) >= 0.75:
        max_chase_atr = STRONG_ENTRY_DRIFT_ATR

    drift = abs(price - reference) / atr
    if not early_entry and drift > max_chase_atr:
        if epic:
            base.record_entry_rejection(
                epic,
                "LATE_ENTRY",
                f"{direction} distance={drift:.2f} ATR; limit={max_chase_atr:.2f} ATR",
            )
        return None

    stop_distance = atr * 1.80
    stop_level = (
        price - stop_distance
        if direction == "BUY"
        else price + stop_distance
    )

    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": None,
        "risk_distance": stop_distance,
        "atr": atr,
        "signal_strength": strength,
    }


# ============================================================
# CONNECT THE FLUID STRATEGY TO THE EXISTING EXECUTION ENGINE
# ============================================================
base.generate_signal = generate_signal
base.m5_entry_direction = m5_entry_direction
base.m5_entry_strength = m5_entry_strength
base.calculate_trade = calculate_trade

# Keep the shared safety implementation. KILL_SWITCH_ENABLED=False in the
# current base means the old daily entry kill-switch does not block new trades.
base.safety_allows_new_entry = base.safety_allows_new_entry


def run_cycle():
    base.log(
        f"STARTING {STRATEGY_ID} | "
        f"M5 authority | M15 support-only | markets={base.EPICS} | "
        f"entry=FLUID | drift=0.25/0.30ATR | "
        f"SL=1.8ATR | TP=dynamic | BE=1R | trailing=1R | "
        f"grid=False | martingale=False | averaging=False"
    )
    return base.run_cycle()


if __name__ == "__main__":
    run_cycle()
