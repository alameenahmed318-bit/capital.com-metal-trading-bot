import json
import math
import os
import time
import traceback

import numpy as np
import pandas as pd

import config
from capital_api import CapitalAPI


# ============================================================
# SAFETY
# ============================================================

# Demo only while testing. Live trading remains disabled.
DEMO_ONLY = True

# Explicitly disabled.
ALLOW_GRID = False
ALLOW_MARTINGALE = False
ALLOW_AVERAGING = False


# ============================================================
# MARKETS
# ============================================================

EPICS = [
    "GOLD",
    "EURUSD",
    "SILVER",
    "OIL_CRUDE",
    "US100",
    "US500",
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


# GOLD
GOLD_RSI_LONG_MIN = 40
GOLD_RSI_LONG_MAX = 70

GOLD_RSI_SHORT_MIN = 30
GOLD_RSI_SHORT_MAX = 60


# Other markets use the same baseline RSI filters initially.
# They are kept as explicit per-market settings so each market can be tuned later.
MARKET_RSI_SETTINGS = {
    "GOLD": (40, 70, 30, 60),
    "EURUSD": (40, 70, 30, 60),
    "SILVER": (40, 70, 30, 60),
    "OIL_CRUDE": (40, 70, 30, 60),
    "US100": (40, 70, 30, 60),
    "US500": (40, 70, 30, 60),
}


# ============================================================
# SL / TP
# ============================================================

SL_ATR_MULT = getattr(config, "SL_ATR_MULT", 1.5)
TP_ATR_MULT = getattr(config, "TP_ATR_MULT", 3.0)


# ============================================================
# TRAILING STOP
# ============================================================

TRAILING_ENABLED = True

# Start trailing after +1R.
TRAILING_START_R = 1.0

# Keep the stop 1R behind current price.
TRAILING_DISTANCE_R = 1.0


# ============================================================
# POSITION SIZE
# ============================================================

# Small Demo sizes.
# Exact monetary risk sizing should only be enabled after
# verifying Capital.com's instrument contract specifications.
MIN_TRADE_SIZE = {
    "GOLD": 0.01,
    "EURUSD": 0.01,
    "SILVER": 1.0,
    "OIL_CRUDE": 0.01,
    "US100": 0.01,
    "US500": 0.01,
}


# ============================================================
# PERSISTENT STATE
# ============================================================

# GitHub Actions runs are separate processes.
# Therefore trailing-stop R values must be persisted.
STATE_FILE = "trades_state.json"


def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "risk_distance": {}
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            return {
                "risk_distance": {}
            }

        if "risk_distance" not in state:
            state["risk_distance"] = {}

        return state

    except Exception as exc:
        log(f"Could not load state file: {exc}")

        return {
            "risk_distance": {}
        }


def save_state(state):
    temp_file = f"{STATE_FILE}.tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            indent=2,
        )

    os.replace(
        temp_file,
        STATE_FILE,
    )


STATE = load_state()


# ============================================================
# LOGGING
# ============================================================

def log(message):
    print(f"[BOT] {message}")


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        number = float(value)

        if not math.isfinite(number):
            return default

        return number

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
    return (
        position.get("epic")
        or position.get("position", {}).get("epic")
    )


def position_deal_id(position):
    return (
        position.get("dealId")
        or position.get("position", {}).get("dealId")
        or position.get("dealReference")
    )


def position_direction(position):
    direction = (
        position.get("direction")
        or position.get("position", {}).get("direction")
    )

    return normalize_direction(direction)


def position_open_level(position):
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


def get_position_size(api, epic, risk_amount_account, risk_distance):
    """
    Size the position so the initial SL risks approximately RISK_PER_TRADE
    of the sizing balance.

    GOLD and EURUSD are USD-quoted on Capital.com while this account is AED.
    The AED/USD peg is used for the sizing conversion. The size is rounded
    down to Capital.com's minimum increment so we never intentionally exceed
    the requested risk. If the broker minimum would exceed the risk budget,
    the trade is skipped.
    """
    risk_amount_account = safe_float(risk_amount_account)
    risk_distance = safe_float(risk_distance)

    if risk_amount_account is None or risk_amount_account <= 0:
        return None

    if risk_distance is None or risk_distance <= 0:
        return None

    market = api.get_market(epic)
    instrument = market.get("instrument", {})
    dealing = market.get("dealingRules", {})

    lot_size = safe_float(instrument.get("lotSize"), 1.0) or 1.0
    min_size = safe_float(
        dealing.get("minDealSize", {}).get("value"),
        MIN_TRADE_SIZE.get(epic, 0.01),
    )
    step = safe_float(
        dealing.get("minSizeIncrement", {}).get("value"),
        min_size,
    )

    if min_size <= 0 or step <= 0 or lot_size <= 0:
        return None

    # These Capital.com markets are USD-quoted here.
    # 1 USD ~= 3.6725 AED.
    account_to_quote = 3.6725
    risk_amount_quote = risk_amount_account / account_to_quote

    raw_size = risk_amount_quote / (risk_distance * lot_size)

    if raw_size < min_size:
        return None

    steps = math.floor((raw_size - min_size) / step + 1e-12)
    size = min_size + max(0, steps) * step

    # Avoid floating-point artifacts.
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    size = round(size, decimals)

    estimated_risk_account = size * risk_distance * lot_size * account_to_quote

    if estimated_risk_account > risk_amount_account * 1.000001:
        size = round(max(0, size - step), decimals)

    if size < min_size:
        return None

    return size


# ============================================================
# CANDLE DATA
# ============================================================

def candles_to_dataframe(raw):

    prices = raw.get("prices", [])

    if not prices:
        return pd.DataFrame()

    rows = []

    for candle in prices:

        open_price = candle.get(
            "openPrice",
            {},
        )

        close_price = candle.get(
            "closePrice",
            {},
        )

        high_price = candle.get(
            "highPrice",
            {},
        )

        low_price = candle.get(
            "lowPrice",
            {},
        )

        def mid(price):

            bid = safe_float(
                price.get("bid")
            )

            ask = safe_float(
                price.get("ask")
            )

            if bid is not None and ask is not None:
                return (bid + ask) / 2

            return (
                bid
                if bid is not None
                else ask
            )

        rows.append(
            {
                "time": candle.get(
                    "snapshotTime"
                ),
                "open": mid(open_price),
                "high": mid(high_price),
                "low": mid(low_price),
                "close": mid(close_price),
            }
        )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
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

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"]
                - previous_close
            ).abs(),
            (
                df["low"]
                - previous_close
            ).abs(),
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
# SIGNAL SETTINGS
# ============================================================

def get_rsi_settings(epic):

    settings = getattr(
        config,
        "MARKET_RSI_SETTINGS",
        {},
    ).get(epic)

    if settings:
        return settings

    return (
        GOLD_RSI_LONG_MIN,
        GOLD_RSI_LONG_MAX,
        GOLD_RSI_SHORT_MIN,
        GOLD_RSI_SHORT_MAX,
    )


# ============================================================
# SIGNAL
# ============================================================

def generate_signal(df, epic, htf_df=None):

    minimum_rows = max(
        EMA_SLOW + 5,
        RSI_PERIOD + 5,
        ATR_PERIOD + 5,
    )

    if len(df) < minimum_rows:
        return None

    if htf_df is None or len(htf_df) < HTF_EMA_SLOW + 5:
        return None
    htf_fast = htf_df["close"].ewm(span=HTF_EMA_FAST, adjust=False).mean().iloc[-2]
    htf_slow = htf_df["close"].ewm(span=HTF_EMA_SLOW, adjust=False).mean().iloc[-2]
    atr_fast = df["atr"].rolling(VOL_REGIME_FAST).mean().iloc[-2]
    atr_slow = df["atr"].rolling(VOL_REGIME_SLOW).mean().iloc[-2]
    if pd.isna(atr_fast) or pd.isna(atr_slow) or atr_slow <= 0 or atr_fast / atr_slow < VOL_REGIME_MIN:
        return None

    # IMPORTANT:
    # Use the last CLOSED candle for the signal.
    #
    # df.iloc[-1] may be the currently forming candle.
    # Therefore crossover is checked using -3 -> -2.
    previous = df.iloc[-3]
    current = df.iloc[-2]

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
        previous["ema_fast"]
        <= previous["ema_slow"]
        and
        current["ema_fast"]
        >
        current["ema_slow"]
    )

    bearish_cross = (
        previous["ema_fast"]
        >= previous["ema_slow"]
        and
        current["ema_fast"]
        <
        current["ema_slow"]
    )

    if bullish_cross and htf_fast > htf_slow:

        if (
            long_min
            <= current["rsi"]
            <= long_max
        ):
            return "BUY"

    if bearish_cross and htf_fast < htf_slow:

        if (
            short_min
            <= current["rsi"]
            <= short_max
        ):
            return "SELL"

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade(df, direction):

    # Use the latest available candle for price/ATR.
    current = df.iloc[-1]

    price = safe_float(
        current["close"]
    )

    atr = safe_float(
        current["atr"]
    )

    if price is None or atr is None:
        return None

    if atr <= 0:
        return None

    sl_distance = (
        atr * SL_ATR_MULT
    )

    tp_distance = (
        atr * TP_ATR_MULT
    )

    if direction == "BUY":

        stop_level = (
            price
            - sl_distance
        )

        profit_level = (
            price
            + tp_distance
        )

    elif direction == "SELL":

        stop_level = (
            price
            + sl_distance
        )

        profit_level = (
            price
            - tp_distance
        )

    else:
        return None

    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": profit_level,
        "risk_distance": sl_distance,
        "atr": atr,
    }


# ============================================================
# POSITIONS
# ============================================================

def get_positions_for_epic(
    positions,
    epic,
):

    return [
        position
        for position in positions
        if position_epic(position) == epic
    ]


def has_position_for_epic(
    positions,
    epic,
):

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

    epic_positions = (
        get_positions_for_epic(
            positions,
            epic,
        )
    )

    if not epic_positions:
        return

    current_price = safe_float(
        current_price
    )

    if current_price is None:
        return

    state_changed = False

    for position in epic_positions:

        deal_id = position_deal_id(
            position
        )

        direction = position_direction(
            position
        )

        entry = position_open_level(
            position
        )

        current_sl = position_stop_level(
            position
        )

        if not deal_id:
            log(
                f"{epic}: missing deal ID."
            )
            continue

        if not direction:
            continue

        if entry is None:
            continue

        deal_key = str(deal_id)

        # ----------------------------------------------------
        # Recover original R from persistent state.
        # ----------------------------------------------------

        stored_risk = safe_float(
            STATE["risk_distance"].get(
                deal_key
            )
        )

        if stored_risk is None:

            if current_sl is None:
                continue

            initial_distance = abs(
                entry - current_sl
            )

            if initial_distance <= 0:
                continue

            STATE[
                "risk_distance"
            ][deal_key] = (
                initial_distance
            )

            stored_risk = initial_distance

            state_changed = True

        r = stored_risk

        if r <= 0:
            continue

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

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
                -
                TRAILING_DISTANCE_R * r
            )

            # Never move SL backwards.
            if current_sl is not None:

                if new_stop <= current_sl:
                    continue

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

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
                +
                TRAILING_DISTANCE_R * r
            )

            # Never move SL backwards.
            if current_sl is not None:

                if new_stop >= current_sl:
                    continue

        else:
            continue

        # ----------------------------------------------------
        # Update Capital.com
        # ----------------------------------------------------

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
                f"{epic}: trailing update failed: "
                f"{exc}"
            )

    if state_changed:
        save_state(STATE)


# ============================================================
# CLEAN OLD STATE
# ============================================================

def cleanup_state(positions):

    active_deals = set()

    for position in positions:

        deal_id = position_deal_id(
            position
        )

        if deal_id:
            active_deals.add(
                str(deal_id)
            )

    stored_deals = list(
        STATE["risk_distance"].keys()
    )

    changed = False

    for deal_id in stored_deals:

        if deal_id not in active_deals:

            del STATE[
                "risk_distance"
            ][deal_id]

            changed = True

    if changed:
        save_state(STATE)


# ============================================================
# PROCESS ONE EPIC
# ============================================================

def process_epic(
    api,
    epic,
    positions,
    balance,
):

    log("")
    log("=" * 60)
    log(
        f"PROCESSING {epic}"
    )
    log("=" * 60)

    try:

        # ----------------------------------------------------
        # Get candles
        # ----------------------------------------------------

        raw = api.get_candles(
            epic=epic,
            resolution=RESOLUTION,
            max_candles=CANDLE_COUNT,
        )

        df = candles_to_dataframe(
            raw
        )

        if df.empty:

            log(
                f"{epic}: no candle data."
            )

            return None

        df = add_indicators(
            df
        )

        if len(df) < 3:

            log(
                f"{epic}: insufficient candles."
            )

            return None

        current_price = safe_float(
            df.iloc[-1]["close"]
        )

        if current_price is None:

            log(
                f"{epic}: invalid current price."
            )

            return None

        # ----------------------------------------------------
        # Trailing stop first
        # ----------------------------------------------------

        manage_trailing_stops(
            api=api,
            positions=positions,
            epic=epic,
            current_price=current_price,
        )

        # ----------------------------------------------------
        # Multiple positions
        #
        # No per-epic position-count limit.
        # A new position is allowed when a NEW EMA crossover
        # signal occurs. This is not Grid/Martingale/Averaging:
        # there is no averaging-in and no repeated entry merely
        # because an existing position is still open.
        # ----------------------------------------------------

        # ----------------------------------------------------
        # Higher-timeframe trend confirmation
        # ----------------------------------------------------
        htf_raw = api.get_candles(epic=epic, resolution=HTF_RESOLUTION, max_candles=HTF_CANDLE_COUNT)
        htf_df = candles_to_dataframe(htf_raw)

        # ----------------------------------------------------
        # Generate signal
        # ----------------------------------------------------
        signal = generate_signal(df, epic, htf_df)

        if signal is None:

            log(
                f"No signal this cycle for {epic}."
            )

            return None

        log(
            f"{epic}: SIGNAL = {signal}"
        )

        # ----------------------------------------------------
        # Calculate SL / TP
        # ----------------------------------------------------

        trade = calculate_trade(
            df,
            signal,
        )

        if trade is None:

            log(
                f"{epic}: trade calculation failed."
            )

            return None

        sizing_balance = min(
            float(balance),
            float(getattr(config, "BALANCE_CAP", balance)),
        )

        risk_percent = float(
            getattr(config, "RISK_PER_TRADE", 0.03)
        )

        risk_amount = sizing_balance * risk_percent

        size = get_position_size(
            api=api,
            epic=epic,
            risk_amount_account=risk_amount,
            risk_distance=trade["risk_distance"],
        )

        if size is None:
            log(
                f"{epic}: minimum trade size would exceed "
                f"the {risk_percent * 100:.2f}% risk budget. "
                "Trade skipped."
            )
            return None

        log(
            f"{epic}: risk budget={risk_amount:.2f} account currency"
        )

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
            f"{epic}: ATR={trade['atr']}"
        )

        log(
            f"{epic}: R distance="
            f"{trade['risk_distance']}"
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
                    "DEMO_ONLY=True but "
                    "IS_DEMO is not enabled."
                )

        # ----------------------------------------------------
        # WORKING ORDER FOR XAUUSD / GOLD
        #
        # Match the manual analysis: BUY only after a confirmed
        # break above the 4,400 trigger. The order is placed as a
        # STOP working order and is not activated unless price
        # reaches the trigger.
        # ----------------------------------------------------
        if epic == "GOLD" and XAU_WORKING_ORDER_ENABLED:
            if signal != "BUY":
                log(f"{epic}: working-order rule requires BUY; no order placed.")
                return None

            trigger = float(XAU_WORKING_TRIGGER)

            if trigger <= trade["entry"]:
                log(
                    f"{epic}: price is already at/above the working trigger "
                    f"{trigger}; no new working order placed."
                )
                return None

            stop_level = trigger - trade["risk_distance"]
            profit_level = trigger + (trade["risk_distance"] * (TP_ATR_MULT / SL_ATR_MULT))

            response = api.place_working_order(
                epic=epic,
                direction="BUY",
                size=size,
                level=trigger,
                stop_level=stop_level,
                profit_level=profit_level,
            )

            log(f"{epic}: WORKING BUY ORDER SENT | trigger={trigger}")
            log(f"{epic}: SL={stop_level} | TP={profit_level}")
            log(f"{epic}: {response}")
            return response

        # ----------------------------------------------------
        # OPEN POSITION FOR OTHER MARKETS
        # ----------------------------------------------------
        response = api.place_order(
            direction=signal,
            size=size,
            stop_level=trade["stop_level"],
            profit_level=trade["profit_level"],
            epic=epic,
        )

        log(f"{epic}: ORDER SENT")
        log(f"{epic}: {response}")
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

    log(
        "Starting trading cycle..."
    )

    if DEMO_ONLY:

        log(
            "DEMO MODE / LIVE TRADING DISABLED"
        )

    # --------------------------------------------------------
    # Safety assertions
    # --------------------------------------------------------

    if ALLOW_GRID:
        raise RuntimeError(
            "Grid trading must remain disabled."
        )

    if ALLOW_MARTINGALE:
        raise RuntimeError(
            "Martingale must remain disabled."
        )

    if ALLOW_AVERAGING:
        raise RuntimeError(
            "Averaging must remain disabled."
        )

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    api = CapitalAPI()

    log(
        "Logging in to Capital.com..."
    )

    api.login()

    balance = api.get_balance()

    log(
        f"Account balance: {balance}"
    )

    # --------------------------------------------------------
    # Get positions ONCE at start.
    # --------------------------------------------------------

    positions = api.get_open_positions()

    log(
        f"Open positions: {len(positions)}"
    )

    # Clean state for positions
    # that no longer exist.
    cleanup_state(
        positions
    )

    # --------------------------------------------------------
    # Process every market independently.
    #
    # All configured markets can trade independently
    # in the same cycle.
    # --------------------------------------------------------

    for epic in EPICS:

        process_epic(
            api=api,
            epic=epic,
            positions=positions,
            balance=balance,
        )

        # Small delay between markets.
        time.sleep(1)

    log("")
    log(
        "Trading cycle completed."
    )


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
