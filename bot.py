import os
import time
import json
import logging
from collections import deque
from pathlib import Path

import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# GOLD-ONLY MICRO SCALPER
EPIC_ALLOWLIST = {"GOLD", "XAUUSD"}
DESIRED_LOTS = 0.10  # user-facing GOLD lot size
SCAN_SECONDS = 1.0  # fixed fast scan
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))
MAX_POSITIONS = 10
ENTRY_COOLDOWN_SECONDS = 10.0
MAX_INITIAL_LOSS_AED = 10.0
PROFIT_TRIGGER_AED = 0.10
PROFIT_FLOOR_AED = 0.10
PRICE_RESOLUTION = "MINUTE"
PRICE_HISTORY = 2

STATE_FILE = Path("bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_micro_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
        })

    def session(self):
        # Capital can temporarily rate-limit session creation (HTTP 429).
        # Back off instead of hammering the login endpoint.
        delay = 5.0
        for attempt in range(6):
            r = self.s.post(
                BASE + "/api/v1/session",
                json={
                    "identifier": IDENTIFIER,
                    "password": PASSWORD,
                    "encryptedPassword": False,
                },
                timeout=20,
            )
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = max(delay, float(retry_after))
                except (TypeError, ValueError):
                    wait = delay
                wait = min(wait, 60.0)
                log.warning("CAPITAL 429 SESSION | retrying in %.1fs | attempt=%d/6", wait, attempt + 1)
                time.sleep(wait)
                delay = min(delay * 2.0, 60.0)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Capital session failed ({r.status_code}): {r.text[:500]}")
            self.s.headers.update({
                "CST": r.headers.get("CST"),
                "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
            })
            return
        raise RuntimeError("Capital session rate-limited after 6 attempts")

    def get(self, path, **params):
        r = self.s.get(BASE + path, params=params, timeout=15)
        if r.status_code == 429:
            raise RuntimeError(f"Capital GET {path} rate-limited (429)")
        if r.status_code >= 400:
            raise RuntimeError(f"Capital GET {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"Capital POST {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def delete(self, path):
        r = self.s.delete(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"Capital DELETE {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def market_details(self, epic):
        return self.get(f"/api/v1/markets/{epic}")

    def prices(self, epic):
        return self.get(
            f"/api/v1/prices/{epic}",
            resolution=PRICE_RESOLUTION,
            max=PRICE_HISTORY,
        )

    def confirm(self, deal_reference):
        return self.get(f"/api/v1/confirms/{deal_reference}")

    def open(self, epic, direction, size):
        if DRY_RUN:
            log.info("DRY RUN | OPEN %s %s %.4f", direction, epic, size)
            return None
        return self.post("/api/v1/positions", {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
        })

    def close(self, deal_id):
        if DRY_RUN:
            log.info("DRY RUN | CLOSE %s", deal_id)
            return None
        return self.delete(f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def quote_mid(q):
    cp = q.get("closePrice", {})
    bid, ask = cp.get("bid"), cp.get("ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def market_mid(m):
    bid, ask = m.get("bid"), m.get("offer")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def latest_price(api, epic):
    raw = api.prices(epic)
    prices = raw.get("prices", [])
    for row in reversed(prices):
        px = quote_mid(row)
        if px is not None:
            return px
    return None


def micro_signal(samples):
    # Price-action only. No EMA/RSI/MACD/ATR.
    if len(samples) < 5:
        return None, {}

    now_px = samples[-1][1]
    moves = {}
    for seconds, key in ((1, "move1"), (3, "move3"), (5, "move5")):
        target = samples[-1][0] - seconds
        prior = min(samples, key=lambda x: abs(x[0] - target))
        moves[key] = now_px - prior[1]

    recent_deltas = [
        abs(samples[i][1] - samples[i - 1][1])
        for i in range(1, len(samples))
    ]
    typical = sum(recent_deltas) / max(len(recent_deltas), 1)
    threshold = max(typical * 0.50, 1e-9)

    if moves["move1"] > threshold and moves["move3"] > threshold and moves["move5"] > threshold:
        return "BUY", moves
    if moves["move1"] < -threshold and moves["move3"] < -threshold and moves["move5"] < -threshold:
        return "SELL", moves
    return None, moves


def gold_market(api):
    for m in api.markets():
        epic = str(m.get("epic", "")).upper()
        status = str(m.get("marketStatus", "")).upper()
        name = str(m.get("instrumentName", m.get("name", ""))).upper()
        if status == "TRADEABLE" and epic in EPIC_ALLOWLIST:
            return m
    return None


def validate_size(api, epic):
    """Convert requested MT-style GOLD lots into Capital API deal size."""
    details = api.market_details(epic)
    rules = details.get("dealingRules", {})
    market = details.get("market", details)

    minimum = float(rules.get("minDealSize", {}).get("value", 0) or 0)
    maximum = float(rules.get("maxDealSize", {}).get("value", 0) or 0)
    increment = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)

    # Capital API /positions uses deal size, not necessarily MT5 lots.
    # GOLD is commonly 100 units per 1.00 lot, so 0.10 lot becomes 10 units.
    lot_size = market.get("lotSize") or details.get("lotSize") or details.get("contractSize") or 100
    lot_size = float(lot_size)
    size = DESIRED_LOTS * lot_size

    if minimum and size < minimum:
        raise RuntimeError(
            f"Requested {DESIRED_LOTS:.2f} GOLD lot = {size:.4f} API size, below broker minimum {minimum}"
        )
    if maximum and size > maximum:
        raise RuntimeError(
            f"Requested {DESIRED_LOTS:.2f} GOLD lot = {size:.4f} API size, above broker maximum {maximum}"
        )
    if increment:
        steps = round(size / increment)
        normalized = steps * increment
        if abs(normalized - size) > 1e-9:
            raise RuntimeError(
                f"Requested {DESIRED_LOTS:.2f} GOLD lot = {size:.4f} API size, incompatible with broker increment {increment}"
            )

    log.info(
        "GOLD SIZE CHECK | lots=%.2f | lot_size=%.4f | api_size=%.4f | min=%.4f | step=%.4f",
        DESIRED_LOTS, lot_size, size, minimum, increment,
    )
    return size

def owned_positions(positions, state):
    out = {}
    live = set()

    for item in positions:
        p = item.get("position", {})
        deal_id = p.get("dealId")
        epic = str(item.get("market", {}).get("epic", "")).upper()
        if deal_id and deal_id in state["owned"] and epic in EPIC_ALLOWLIST:
            out[deal_id] = item
            live.add(deal_id)

    for deal_id in list(state["owned"]):
        if deal_id not in live:
            del state["owned"][deal_id]

    return out


def manage_position(api, item, entry_state, current_px):
    p = item.get("position", {})
    deal_id = p.get("dealId")
    upl = float(p.get("upl", 0) or 0)

    if not deal_id:
        return

    peak = max(float(entry_state.get("peak_upl", 0) or 0), upl)
    entry_state["peak_upl"] = peak

    # Hard per-position loss cap.
    if upl <= -MAX_INITIAL_LOSS_AED:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.warning("MAX LOSS CLOSE | %s | UPL=%.2f", deal_id, upl)
        return

    # Once +0.10 AED has been reached, do not allow the trade to fall back
    # below +0.10 AED. This is the requested small-profit protection.
    if peak >= PROFIT_TRIGGER_AED and upl <= PROFIT_FLOOR_AED:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.info(
            "PROFIT CLOSE | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f",
            deal_id, upl, peak, PROFIT_FLOOR_AED,
        )


def open_position(api, epic, direction, size, state):
    result = api.open(epic, direction, size)
    if DRY_RUN or not result:
        return

    deal_ref = result.get("dealReference")
    if not deal_ref:
        log.warning("OPEN returned no dealReference")
        return

    for _ in range(5):
        try:
            confirmed = api.confirm(deal_ref)
            affected = confirmed.get("affectedDeals", [])
            if affected:
                for deal in affected:
                    deal_id = deal.get("dealId") or deal.get("dealReference")
                    if deal_id:
                        state["owned"][deal_id] = {
                            "epic": epic,
                            "direction": direction,
                            "peak_upl": 0.0,
                            "opened_at": time.time(),
                        }
                        log.info("OWNED POSITION | %s | %s | %s", deal_id, epic, direction)
                return
        except Exception:
            pass
        time.sleep(0.4)


def run():
    api = Capital()
    api.session()

    state = load_state()
    recent = deque(maxlen=12)
    last_entry = 0.0
    cycle_start = time.time()
    last_position_refresh = 0.0
    positions = []

    market = gold_market(api)
    if not market:
        raise RuntimeError("No tradeable GOLD/XAUUSD market found")

    epic = str(market["epic"]).upper()
    validate_size(api, epic)

    log.info(
        "GOLD MICRO SCALPER | DRY_RUN=%s | EPIC=%s | LOTS=%.2f | SCAN=%.1fs | MAX_POS=%d | PROFIT=+%.2f | LOSS=-%.2f",
        DRY_RUN, epic, DESIRED_LOTS, SCAN_SECONDS, MAX_POSITIONS,
        PROFIT_TRIGGER_AED, MAX_INITIAL_LOSS_AED,
    )

    while time.time() - cycle_start < RUN_SECONDS:
        try:
            # Reconnect/session refresh is handled after API failures.
            px = latest_price(api, epic)
            if px is None:
                time.sleep(SCAN_SECONDS)
                continue

            now = time.time()
            recent.append((now, px))

            if now - last_position_refresh >= max(1.0, SCAN_SECONDS):
                positions = api.positions()
                last_position_refresh = now

            owned = owned_positions(positions, state)

            # Manage all bot-owned GOLD positions before looking for new entries.
            for deal_id, item in list(owned.items()):
                manage_position(api, item, state["owned"][deal_id], px)

            # Re-fetch after management so closed positions are no longer counted.
            positions = api.positions()
            owned = owned_positions(positions, state)
            gold_positions = [
                x for x in positions
                if str(x.get("market", {}).get("epic", "")).upper() == epic
            ]

            if signal and len(gold_positions) < MAX_POSITIONS:
                if now - last_entry >= ENTRY_COOLDOWN_SECONDS:
                    size = validate_size(api, epic)
                    open_position(api, epic, signal, size, state)
                    last_entry = now
                    log.info(
                        "ENTRY | %s | %s | size=%.4f | positions=%d/%d | move1=%.5f move3=%.5f move5=%.5f",
                        epic, signal, size, len(gold_positions), MAX_POSITIONS,
                        moves.get("move1", 0), moves.get("move3", 0), moves.get("move5", 0),
                    )
            elif not signal:
                log.info(
                    "SIGNAL_NONE | %s | px=%.5f | positions=%d/%d",
                    epic, px, len(gold_positions), MAX_POSITIONS,
                )

            save_state(state)
            time.sleep(SCAN_SECONDS)

        except Exception as e:
            log.error("CYCLE ERROR | %s", e)
            save_state(state)
            time.sleep(3)
            try:
                api.session()
            except Exception:
                pass

    log.info("RUN COMPLETE | elapsed=%ds", int(time.time() - cycle_start))


if __name__ == "__main__":
    run()
