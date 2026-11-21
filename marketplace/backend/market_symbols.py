"""Whitelist of AL-ZOOL market symbols. Never authorize arbitrary pairs.

XAUUSD broker suffixes remain backward compatible. BTCUSD may have a common
MT5 suffix; BTCUSDT and all other quote currencies are deliberately excluded.
The current terminal/engine connection tracks ONE active symbol at a time.
"""
import re

_BTC = re.compile(r"^BTCUSD(?:[._-][A-Za-z0-9]{1,8}|m)?$", re.IGNORECASE)

def market_family(symbol):
    code = str(symbol or "").strip()
    if not code:
        return None
    if code.upper().startswith("XAUUSD"):
        return "XAUUSD"
    if _BTC.fullmatch(code):
        return "BTCUSD"
    return None

def supported_market_symbol(symbol):
    return market_family(symbol) is not None

def same_market_symbol(left, right):
    return str(left or "").strip().upper() == str(right or "").strip().upper()
