import os
from dotenv import load_dotenv

load_dotenv()

CAPITAL_API_KEY = os.environ["CAPITAL_API_KEY"]
CAPITAL_EMAIL = os.environ["CAPITAL_EMAIL"]
CAPITAL_PASSWORD = os.environ["CAPITAL_PASSWORD"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
IS_DEMO = os.environ["IS_DEMO"].lower() == "true"

CAPITAL_BASE_URL = os.environ["CAPITAL_BASE_DEMO_URL"] if IS_DEMO else os.environ["CAPITAL_BASE_URL"]


def _float_env(name, default):
    """Parse a float env var, tolerating the empty string.

    GitHub Actions substitutes an UNSET repository variable as an empty string
    rather than omitting it, so os.environ.get(name, default) returns "" and
    float("") raises - which would crash every scheduled run the moment one of
    these optional variables was left unset.
    """
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number; using default {default}.")
        return float(default)


# --------------------------------------------------------------------------
# Strategy selection
# --------------------------------------------------------------------------
# STRATEGY selects which decision rule the live bot runs:
#
#   "baseline"   the original EMA-cross + RSI rule, on all three metals.
#   "vol_regime" the same entry rule, but only during US macro hours (12-15 UTC)
#                and only when the volatility regime (ATR20/ATR200) is at least
#                VOL_REGIME_MIN, with volatility-managed position sizing.
#                Backtested (walk-forward, all costs) at PF 1.36-1.42, DSR
#                0.94-0.96 on gold over 4 years - see CLAUDE.md.
#
# This is a switch rather than a rewrite so the previous behaviour is one env
# var away, and switching back is always revert-safe.
STRATEGY = os.environ.get("STRATEGY", "baseline")

FORWARD_TEST_EPICS = ["GOLD"]  # silver backtests negative, copper break-even under vol_regime
VOL_REGIME_MIN = _float_env("VOL_REGIME_MIN", 1.05)
VOL_REGIME_FAST = 20
VOL_REGIME_SLOW = 200
MACRO_HOURS_UTC = (12, 13, 14, 15)  # measured >1.2x mean volatility on all 3 metals
VOL_MANAGED_SIZING = os.environ.get("VOL_MANAGED_SIZING", "false").lower() == "true"

_ALL_EPICS = ["GOLD", "SILVER", "COPPER"]
EPICS = FORWARD_TEST_EPICS

RESOLUTION = "MINUTE_15"
# vol_regime needs ATR(200) plus the ATR period itself, so the candle request must
# comfortably exceed VOL_REGIME_SLOW or vol_regime is always NaN and the bot silently
# never trades. Applies regardless of STRATEGY so switching is a pure env-var flip.
CANDLE_COUNT = 300

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_LONG_MIN = 40
RSI_LONG_MAX = 70
RSI_SHORT_MIN = 30
RSI_SHORT_MAX = 60

RISK_PER_TRADE = _float_env("RISK_PER_TRADE", 0.01)
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0

DB_PATH = "trades.db"

DAILY_SUMMARY_HOUR_UTC = 21

# Per-instrument precision and minimum trade size, read directly from Capital.com's
# GET /api/v1/markets/{epic} dealingRules (minSizeIncrement, minDealSize) on
# 2026-08-19. Do not guess these from another broker's spec - COPPER's minDealSize
# here (10) is 10x what an OANDA-derived guess would have used.
INSTRUMENT_PRECISION = {"GOLD": 2, "SILVER": 1, "COPPER": 0}
MIN_TRADE_SIZE = {"GOLD": 0.01, "SILVER": 1.0, "COPPER": 10.0}

# Caps the balance the risk layer sees, so demo-account sizing (funded far above any
# balance worth sizing against) resembles what a real account would take.
BALANCE_CAP = _float_env("BALANCE_CAP", 1000)
