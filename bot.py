import math
import time
import traceback

import numpy as np
import pandas as pd

import config
from capital_api import CapitalAPI


# ============================================================
# SAFETY
# ============================================================

# Keep this bot on Demo while testing.
DEMO_ONLY = True

# No Grid / Martingale / Averaging.
ALLOW_GRID = False
ALLOW_MARTINGALE = False
ALLOW_AVERAGING = False


# ============================================================
# MARKETS
# ============================================================

# Capital.com epic names.
# GOLD is already confirmed in your account.
# EURUSD should be verified by the Capital.com instrument search
# if your account uses a different epic name.
EPICS = [
    "GOLD",
    "EURUSD",
]


# ============================================================
# STRATEGY SETTINGS
# ============================================================

RESOLUTION = getattr(config, "RESOLUTION", "MINUTE_15")
CANDLE_COUNT = getattr(config, "CANDLE_COUNT", 300)

EMA_FAST = getattr(config, "EMA_FAST", 9)
EMA_SLOW = getattr(config, "EMA_SLOW", 21)

RSI_PERIOD = getattr(config, "RSI_PERIOD", 14)

ATR_PERIOD = getattr(config, "ATR_PERIOD", 14)

# GOLD settings
GOLD_RSI_LONG_MIN = 40
GOLD_RSI_LONG_MAX = 70

GOLD_RSI_SHORT_MIN = 30
GOLD_RSI_SHORT_MAX = 60

# EUR/USD settings
# Kept separate so EUR/USD can be tuned independently later.
EURUSD_RSI_LONG_MIN = 40
EURUSD_RSI_LONG_MAX = 70

EURUSD_RSI_SHORT_MIN = 30
EURUSD_RSI_SHORT_MAX = 60


# ============================================================
# SL / TP
# ============================================================

# Initial stop = 1.5 ATR
SL_ATR_MULT = getattr(config, "SL_ATR_MULT", 1.5)

# Initial target = 3 ATR
# This gives approximately 1:2 risk/reward.
TP_ATR_MULT = getattr(config, "TP_ATR_MULT", 3.0)


# ============================================================
# TRAILING STOP
# ============================================================

TRAILING_ENABLED = True

# Start trailing after price has moved 1R in our favour.
TRAILING_START_R = 1.0

# Keep SL 1R away from current price.
TRAILING_DISTANCE_R = 1.0


# ============================================================
# POSITION SIZE
# ============================================================

# We deliberately use fixed minimum sizes here rather than
# pretending we can calculate exact monetary risk without
# Capital.com's instrument contract metadata.
#
# These are small Demo sizes.
MIN_TRADE_SIZE = {
    "GOLD": 0.01,
    "EURUSD": 0.01,
}


# ============================================================
# HELPERS
# ============================================================

def log(message):
    print(f"[BOT] {message}")


def safe_float(value, default=None):
    try:
        return float(value)
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
    """
    Capital.com normally returns the instrument as position.epic.
    """
    return position.get("epic") or position.get("position", {}).get("epic")


def position_deal_id(position):
    """
    Supports the common Capital.com response structures.
    """
    return (
        position.get("dealId")
        or position.get("position", {}).get("dealId")
        or position.get("dealReference")
    )


def position_direction(position):
    """
    Supports both direct and nested position responses.
    """
    direction = (
        position.get("direction")
        or position.get("position", {}).get("direction")
    )

    return normalize_direction(direction)


def position_open_level(position):
    """
    Entry/open price.
    """
    return safe_float(
        position.get("level")
        or position.get("openLevel")
        or position.get("position", {}).get("level")
        or position.get("position", {}).get("openLevel")
    )


def position_stop_level(position):
    return safe_float(
        position.get("stopLevel")
        or position.get("position", {}).get("stopLevel")
    )


def position_profit_level(position):
    return safe_float(
        position.get("profitLevel")
        or position.get("position", {}).get("profitLevel")
    )


def get_position_size(epic):
    return float(MIN_TRADE_SIZE.get(epic, 0.01))


# ============================================================
# CANDLE DATA
# ============================================================

def candles_to_dataframe(raw):
    """
    Convert Capital.com candle response into a DataFrame.

    Capital.com normally returns:
        {
            "prices": [
                {
                    "snapshotTime": "...",
                    "openPrice": {"bid": ..., "ask": ...},
                    "closePrice": {"bid": ..., "ask": ...},
                    "highPrice": {"bid": ..., "ask": ...},
                    "lowPrice": {"bid": ..., "ask": ...},
                }
            ]
        }
    """

    prices = raw.get("prices", [])

    if not prices:
        return pd.DataFrame()

    rows = []

    for candle in prices:
        open_price = candle.get("openPrice", {})
        close_price = candle.get("closePrice", {})
        high_price = candle.get("highPrice", {})
        low_price = candle.get("lowPrice", {})

        def mid(price):
            bid = safe_float(price.get("bid"))
            ask = safe_float(price.get("ask"))

            if bid is not None and ask is not None:
                return (bid + ask) / 2

            return bid if bid is not None else ask

        rows.append(
            {
                "time": candle.get("snapshotTime"),
                "open": mid(open_price),
                "high": mid(high_price),
                "low": mid(low_price),
                "close": mid(close_price),
            }
        )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    return df


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):
    df = df.copy()

    df["ema_fast"] = (
        df["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
        )
        .mean()
    )

    df["ema_slow"] = (
        df["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
        )
        .mean()
    )

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        true_range
        .ewm(
            alpha=1 / ATR_PERIOD,
            min_periods=ATR_PERIOD,
            adjust=False,
        )
        .mean()
    )

    return df


# ============================================================
# SIGNAL
# ============================================================

def get_rsi_settings(epic):
    if epic == "EURUSD":
        return (
            EURUSD_RSI_LONG_MIN,
            EURUSD_RSI_LONG_MAX,
            EURUSD_RSI_SHORT_MIN,
            EURUSD_RSI_SHORT_MAX,
        )

    return (
        GOLD_RSI_LONG_MIN,
        GOLD_RSI_LONG_MAX,
        GOLD_RSI_SHORT_MIN,
        GOLD_RSI_SHORT_MAX,
    )


def generate_signal(df, epic):
    """
    EMA 9/21 cross + RSI filter.

    Only a NEW crossover produces a signal.
    """

    if len(df) < max(
        EMA_SLOW + 5,
        RSI_PERIOD + 5,
        ATR_PERIOD + 5,
    ):
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    if (
        pd.isna(previous["ema_fast"])
        or pd.isna(previous["ema_slow"])
        or pd.isna(current["ema_fast"])
        or pd.isna(current["ema_slow"])
        or pd.isna(current["rsi"])
        or pd.isna(current["atr"])
    ):
        return None

    (
        long_min,
        long_max,
        short_min,
        short_max,
    ) = get_rsi_settings(epic)

    bullish_cross = (
        previous["ema_fast"] <= previous["ema_slow"]
        and current["ema_fast"] > current["ema_slow"]
    )

    bearish_cross = (
        previous["ema_fast"] >= previous["ema_slow"]
        and current["ema_fast"] < current["ema_slow"]
    )

    if bullish_cross:
        if long_min <= current["rsi"] <= long_max:
            return "BUY"

    if bearish_cross:
        if short_min <= current["rsi"] <= short_max:
            return "SELL"

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade(df, direction):
    current = df.iloc[-1]

    price = float(current["close"])
    atr = float(current["atr"])

    if not math.isfinite(price) or not math.isfinite(atr):
        return None

    if atr <= 0:
        return None

    sl_distance = atr * SL_ATR_MULT
    tp_distance = atr * TP_ATR_MULT

    if direction == "BUY":
        stop_level = price - sl_distance
        profit_level = price + tp_distance

    else:
        stop_level = price + sl_distance
        profit_level = price - tp_distance

    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": profit_level,
        "risk_distance": sl_distance,
        "atr": atr,
    }


# ============================================================
# OPEN POSITIONS
# ============================================================

def get_positions_for_epic(positions, epic):
    return [
        position
        for position in positions
        if position_epic(position) == epic
    ]


def has_position_for_epic(positions, epic):
    return len(
        get_positions_for_epic(
            positions,
            epic,
        )
    ) > 0


# ============================================================
# TRAILING STOP
# ============================================================

def manage_trailing_stops(
    api,
    positions,
    epic,
    current_price,
):
    if not TRAILING_ENABLED:
        return

    epic_positions = get_positions_for_epic(
        positions,
        epic,
    )

    if not epic_positions:
        return

    for position in epic_positions:

        deal_id = position_deal_id(position)
        direction = position_direction(position)
        entry = position_open_level(position)
        current_sl = position_stop_level(position)

        if not deal_id:
            log(
                f"{epic}: Cannot trail position - "
                f"missing deal ID."
            )
            continue

        if not direction:
            continue

        if entry is None:
            continue

        current_price = safe_float(current_price)

        if current_price is None:
            continue

        # ----------------------------------------------------
        # We need the ORIGINAL risk distance.
        #
        # If a current stop exists, we can estimate the original
        # risk distance from entry -> stop.
        #
        # Once trailing has moved the stop, that value changes,
        # so we intentionally don't use a moved stop as the
        # trailing distance.
        #
        # Therefore the bot stores the original R in memory
        # during this process.
        # ----------------------------------------------------

        risk_distance = getattr(
            manage_trailing_stops,
            "_risk_distance",
            {},
        )

        if deal_id not in risk_distance:
            if current_sl is None:
                continue

            initial_distance = abs(
                entry - current_sl
            )

            if initial_distance <= 0:
                continue

            risk_distance[deal_id] = initial_distance

        r = risk_distance[deal_id]

        if r <= 0:
            continue

        if direction == "BUY":

            profit_distance = (
                current_price - entry
            )

            if profit_distance < (
                TRAILING_START_R * r
            ):
                continue

            new_stop = (
                current_price
                - TRAILING_DISTANCE_R * r
            )

            # Never move SL backwards.
            if current_sl is not None:
                if new_stop <= current_sl:
                    continue

        elif direction == "SELL":

            profit_distance = (
                entry - current_price
            )

            if profit_distance < (
                TRAILING_START_R * r
            ):
                continue

            new_stop = (
                current_price
                + TRAILING_DISTANCE_R * r
            )

            # Never move SL backwards.
            if current_sl is not None:
                if new_stop >= current_sl:
                    continue

        else:
            continue

        try:
            api.modify_position(
                deal_id=deal_id,
                stop_level=new_stop,
            )

            log(
                f"{epic}: TRAILING STOP UPDATED | "
                f"{direction} | "
                f"old SL={current_sl} | "
                f"new SL={new_stop}"
            )

        except Exception as exc:
            log(
                f"{epic}: trailing stop update failed: "
                f"{exc}"
            )


# ============================================================
# ONE MARKET CYCLE
# ============================================================

def process_epic(
    api,
    epic,
    positions,
):
    log("")
    log("=" * 60)
    log(f"PROCESSING {epic}")
    log("=" * 60)

    try:
        raw = api.get_candles(
            epic=epic,
            resolution=RESOLUTION,
            max_candles=CANDLE_COUNT,
        )

        df = candles_to_dataframe(raw)

        if df.empty:
            log(
                f"{epic}: no candle data."
            )
            return

        df = add_indicators(df)

        current_price = float(
            df.iloc[-1]["close"]
        )

        # ----------------------------------------------------
        # TRAILING FIRST
        # ----------------------------------------------------

        manage_trailing_stops(
            api=api,
            positions=positions,
            epic=epic,
            current_price=current_price,
        )

        # ----------------------------------------------------
        # EXISTING POSITION CHECK
        #
        # IMPORTANT:
        # This check is PER EPIC.
        #
        # Therefore:
        #
        # GOLD position existing
        # does NOT block EURUSD.
        #
        # EURUSD position existing
        # does NOT block GOLD.
        # ----------------------------------------------------

        if has_position_for_epic(
            positions,
            epic,
        ):
            log(
                f"{epic}: existing position found. "
                f"No new position for this epic."
            )
            return

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        signal = generate_signal(
            df,
            epic,
        )

        if signal is None:
            log(
                f"No signal this cycle for {epic}."
            )
            return

        log(
            f"{epic}: SIGNAL = {signal}"
        )

        trade = calculate_trade(
            df,
            signal,
        )

        if trade is None:
            log(
                f"{epic}: could not calculate "
                f"trade levels."
            )
            return

        size = get_position_size(epic)

        log(
            f"{epic}: entry={trade['entry']}"
        )

        log(
            f"{epic}: SL={trade['stop_level']}"
        )

        log(
            f"{epic}: TP={trade['profit_level']}"
        )

        log(
            f"{epic}: size={size}"
        )

        # ----------------------------------------------------
        # DEMO SAFETY
        # ----------------------------------------------------

        if DEMO_ONLY:
            is_demo = str(
                getattr(
                    config,
                    "IS_DEMO",
                    "true",
                )
            ).lower()

            if is_demo not in (
                "true",
                "1",
                "yes",
            ):
                raise RuntimeError(
                    "DEMO_ONLY=True but IS_DEMO "
                    "is not enabled."
                )

        # ----------------------------------------------------
        # OPEN POSITION
        # ----------------------------------------------------

        response = api.place_order(
            direction=signal,
            size=size,
            stop_level=trade["stop_level"],
            profit_level=trade["profit_level"],
            epic=epic,
        )

        log(
            f"{epic}: ORDER SENT"
        )

        log(
            f"{epic}: {response}"
        )

        return response

    except Exception as exc:
        log(
            f"{epic}: ERROR: {exc}"
        )
        traceback.print_exc()

        return None


# ============================================================
# MAIN CYCLE
# ============================================================

def run_cycle():
    log("Starting trading cycle...")

    if DEMO_ONLY:
        log("DEMO MODE / LIVE TRADING DISABLED")

    api = CapitalAPI()

    log("Logging in to Capital.com...")

    api.login()

    balance = api.get_balance()

    log(
        f"Account balance: {balance}"
    )

    positions = api.get_open_positions()

    log(
        f"Open positions: {len(positions)}"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # GOLD and EURUSD are processed independently.
    #
    # We DO NOT stop after the first trade.
    #
    # Example:
    #
    # GOLD -> BUY signal
    # EURUSD -> BUY signal
    #
    # Both can be opened during the same cycle.
    # --------------------------------------------------------

    for epic in EPICS:

        process_epic(
            api=api,
            epic=epic,
            positions=positions,
        )

        # Small delay between API operations.
        time.sleep(1)

    log("")
    log("Trading cycle completed.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        run_cycle()

    except Exception as exc:

        log(
            f"MAIN ERROR: {exc}"
        )

        traceback.print_exc()
