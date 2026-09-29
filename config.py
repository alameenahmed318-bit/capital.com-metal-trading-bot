import os
from dotenv import load_dotenv

load_dotenv()

CAPITAL_API_KEY = os.environ["CAPITAL_API_KEY"]
CAPITAL_EMAIL = os.environ["CAPITAL_EMAIL"]
CAPITAL_PASSWORD = os.environ["CAPITAL_PASSWORD"]
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
_IS_DEMO_RAW = os.environ.get("IS_DEMO", "").strip().lower()
if _IS_DEMO_RAW not in {"true", "false"}:
    raise RuntimeError("IS_DEMO must be explicitly set to 'true' or 'false'. Refusing to start with an ambiguous trading environment.")
IS_DEMO = _IS_DEMO_RAW == "true"

if IS_DEMO:
    CAPITAL_BASE_URL = os.environ["CAPITAL_BASE_DEMO_URL"]
else:
    CAPITAL_BASE_URL = os.environ["CAPITAL_BASE_URL"]


def _float_env(name, default):
    """Parse a float env var, tolerating the empty string."""
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number; using default {default}.")
        return float(default)


STRATEGY = os.environ.get("STRATEGY", "baseline")

# Strict automated-trading universe: US indices and EUR/USD only.
# Other markets are excluded from new automated entries.
DISABLED_FX_EPICS = frozenset({
    "USDCHF", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURCHF", "GBPCHF",
    "AUDCAD", "AUDCHF", "NZDJPY", "CADJPY", "EURUSD_W",
})
FX_EPICS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY"]
METALS_ENERGY_EPICS = ["GOLD", "SILVER", "US100", "US500"]
EPICS = FX_EPICS + METALS_ENERGY_EPICS
FORWARD_TEST_EPICS = EPICS

VOL_REGIME_MIN = _float_env("VOL_REGIME_MIN", 1.05)
VOL_REGIME_FAST = 20
VOL_REGIME_SLOW = 200
HTF_RESOLUTION = "MINUTE_5"
HTF_CANDLE_COUNT = 300
HTF_EMA_FAST = 50
HTF_EMA_SLOW = 200
MAX_PORTFOLIO_RISK = min(_float_env("MAX_PORTFOLIO_RISK", 0.03), 0.03)
MACRO_HOURS_UTC = (12, 13, 14, 15)
VOL_MANAGED_SIZING = os.environ.get("VOL_MANAGED_SIZING", "true").lower() == "true"

RESOLUTION = "MINUTE_15"
CANDLE_COUNT = 300

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

# Per-market RSI filters. All start with the same baseline; the bot keeps
# them separate so they can be tuned independently later.
MARKET_RSI_SETTINGS = {
    "GOLD": (40, 70, 30, 60),
    "EURUSD": (40, 70, 30, 60),
    "SILVER": (40, 70, 30, 60),
    "OIL_CRUDE": (40, 70, 30, 60),
    "US100": (40, 70, 30, 60),
    "US500": (40, 70, 30, 60),
    "EURUSD_W": (40, 70, 30, 60),
    "USDJPY_W": (40, 70, 30, 60),
}

RISK_PER_TRADE = min(_float_env("RISK_PER_TRADE", 0.01), 0.01)
SL_ATR_MULT = 1.8
TP_ATR_MULT = 2.0

DB_PATH = "trades.db"
DAILY_SUMMARY_HOUR_UTC = 21

# Fallbacks only. The bot reads the live min deal size/increment from
# Capital.com's market details before sizing each trade.
INSTRUMENT_PRECISION = {
    "GOLD": 2,
    "EURUSD": 2,
    "SILVER": 1,
    "OIL_CRUDE": 2,
    "US100": 2,
    "US500": 2,
    "EURUSD_W": 2,
    "USDJPY_W": 2,
}

MIN_TRADE_SIZE = {
    "GOLD": 0.01,
    "EURUSD": 0.01,
    "SILVER": 1.0,
    "OIL_CRUDE": 0.01,
    "US100": 0.01,
    "US500": 0.01,
    "EURUSD_W": 0.01,
    "USDJPY_W": 0.01,
}

BALANCE_CAP = _float_env("BALANCE_CAP", 1000)
