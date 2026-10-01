import os
import time
import json
import logging
from pathlib import Path
import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com").rstrip("/")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

SIZE = float(os.getenv("GOLD_TRADE_SIZE", os.getenv("TRADE_SIZE", "0.01")))
DRY_RUN = os.getenv("GOLD_DRY_RUN", "true").lower() == "true"
SCAN_SECONDS = int(os.getenv("GOLD_SCAN_SECONDS", "5"))
RUN_SECONDS = int(os.getenv("GOLD_RUN_SECONDS", "720"))
MAX_POSITIONS = int(os.getenv("GOLD_MAX_POSITIONS", "1"))
MAX_INITIAL_LOSS = float(os.getenv("GOLD_MAX_INITIAL_LOSS_AED", "10"))
PROTECT_TRIGGER = float(os.getenv("GOLD_PROTECT_TRIGGER_AED", "0.25"))
PROFIT_FLOOR = float(os.getenv("GOLD_PROFIT_FLOOR_AED", "0.05"))
STATE_FILE = Path("gold_bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GOLD-NEW] %(levelname)s %(message)s")
log = logging.getLogger("gold_new")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def session(self):
        r = self.s.post(
            BASE + "/api/v1/session",
            json={"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False},
            timeout=20,
        )
        r.raise_for_status()
        self.s.headers.update({
            "CST": r.headers.get("CST"),
            "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
        })

    def request(self, method, path, **kwargs):
        r = self.s.request(method, BASE + path, timeout=20, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"Capital {method} {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def markets(self):
        return self.request("GET", "/api/v1/markets").get("markets", [])

    def prices(self, epic, resolution="MINUTE_5", n=100):
        return self.request("GET", f"/api/v1/prices/{epic}",
                            params={"resolution": resolution, "max": n})

    def positions(self):
        return self.request("GET", "/api/v1/positions").get("positions", [])

    def open(self, epic, direction, size):
        return self.request("POST", "/api/v1/positions", json={
            "epic": epic, "direction": direction, "size": size, "guaranteedStop": False
        })

    def confirm(self, ref):
        return self.request("GET", f"/api/v1/confirms/{ref}")

    def close(self, deal_id):
        return self.request("DELETE", f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def mid(m):
    bid = m.get("bid")
    ask = m.get("offer")
    return (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else None


def price_mid(row):
    q = row.get("closePrice", {})
    bid, ask = q.get("bid"), q.get("ask")
    return (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else None


def history(raw):
    values = [price_mid(x) for x in raw.get("prices", [])]
    return [x for x in values if x is not None]


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def signal(values):
    if len(values) < 20:
        return None
    fast = ema(values, 5)
    slow = ema(values, 13)
    pfast = ema(values[:-1], 5)
    pslow = ema(values[:-1], 13)
    move = values[-1] - values[-4]
    ref = max(max(values[-4:]) - min(values[-4:]), abs(move), 1e-12)

    if fast is None or slow is None or pfast is None or pslow is None:
        return None
    if pfast <= pslow and fast > slow and move > 0:
        return "BUY"
    if pfast >= pslow and fast < slow and move < 0:
        return "SELL"
    if fast > slow and move / ref >= 0.30:
        return "BUY"
    if fast < slow and abs(move) / ref >= 0.30:
        return "SELL"
    return None


def find_gold(markets):
    candidates = []
    for m in markets:
        if str(m.get("marketStatus", "")).upper() != "TRADEABLE":
            continue
        epic = str(m.get("epic", "")).upper()
        name = str(m.get("instrumentName", m.get("name", ""))).upper()
        if epic in {"GOLD", "XAUUSD"} or "GOLD" in name or "XAU/USD" in name or "XAUUSD" in name:
            candidates.append(m)
    if not candidates:
        return None
    candidates.sort(key=lambda x: (
        0 if str(x.get("epic", "")).upper() == "XAUUSD" else 1,
        str(x.get("epic", ""))
    ))
    return candidates[0]


def owned_positions(positions, state):
    out = {}
    live = set()
    for item in positions:
        deal = item.get("position", {}).get("dealId")
        if deal and deal in state["owned"]:
            out[deal] = item
            live.add(deal)
    for deal in list(state["owned"]):
        if deal not in live:
            state["owned"].pop(deal, None)
    return out


def upl(item):
    try:
        return float(item.get("position", {}).get("upl", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def protect(api, owned, state):
    for deal, item in owned.items():
        u = upl(item)
        entry = state["owned"][deal]
        entry["peak_upl"] = max(float(entry.get("peak_upl", 0)), u)
        peak = entry["peak_upl"]

        if u <= -MAX_INITIAL_LOSS:
            if DRY_RUN:
                log.info("DRY RUN | loss limit would close %s | UPL=%.2f", deal, u)
            else:
                api.close(deal)
                log.warning("LOSS LIMIT CLOSE | %s | UPL=%.2f", deal, u)
            continue

        if peak >= PROTECT_TRIGGER and u <= PROFIT_FLOOR:
            if DRY_RUN:
                log.info("DRY RUN | profit floor would close %s | UPL=%.2f peak=%.2f", deal, u, peak)
            else:
                api.close(deal)
                log.info("PROFIT FLOOR CLOSE | %s | UPL=%.2f peak=%.2f", deal, u, peak)


def open_position(api, market, direction, state):
    epic = market["epic"]
    if DRY_RUN:
        log.info("DRY RUN | would OPEN %s %s size=%.4f", direction, epic, SIZE)
        return
    result = api.open(epic, direction, SIZE)
    ref = result.get("dealReference")
    if not ref:
        log.warning("OPEN returned no dealReference: %s", result)
        return
    for _ in range(5):
        try:
            confirmed = api.confirm(ref)
            affected = confirmed.get("affectedDeals", [])
            if affected:
                for d in affected:
                    deal = d.get("dealId")
                    if deal:
                        state["owned"][deal] = {
                            "epic": epic,
                            "direction": direction,
                            "peak_upl": 0.0,
                        }
                        log.info("OWNED NEW GOLD POSITION | %s | %s", deal, epic)
                return
        except Exception:
            time.sleep(0.4)
    log.warning("Could not confirm deal reference %s", ref)


def run():
    api = Capital()
    api.session()
    state = load_state()
    started = time.time()

    log.info("NEW INDEPENDENT GOLD BOT | dry_run=%s | scan=%ss | size=%.4f", DRY_RUN, SCAN_SECONDS, SIZE)

    while time.time() - started < RUN_SECONDS:
        try:
            market = find_gold(api.markets())
            if not market:
                log.warning("GOLD MARKET NOT FOUND / NOT TRADEABLE")
                time.sleep(SCAN_SECONDS)
                continue

            epic = market["epic"]
            raw = api.prices(epic, "MINUTE_5", 100)
            values = history(raw)
            if len(values) > 1:
                values = values[:-1]  # ignore the still-forming candle

            live_positions = api.positions()
            owned = owned_positions(live_positions, state)

            # This bot may ONLY close positions whose dealId it owns.
            protect(api, owned, state)

            # Never touch another bot/manual position. If another position
            # exists on gold, this bot simply waits instead of taking ownership.
            live_gold = [
                p for p in live_positions
                if str(p.get("market", {}).get("epic", "")).upper() == epic.upper()
            ]

            sig = signal(values)
            log.info(
                "%s | signal=%s | owned=%d | all_gold_positions=%d | price=%s",
                epic, sig, len(owned), len(live_gold), market.get("offer")
            )

            if not owned and not live_gold and sig:
                open_position(api, market, sig, state)

            save_state(state)
            time.sleep(SCAN_SECONDS)

        except Exception as exc:
            log.exception("GOLD BOT CYCLE FAILED | %s", exc)
            time.sleep(5)
            try:
                api.session()
            except Exception:
                pass

    save_state(state)
    log.info("NEW GOLD BOT FINISHED")


if __name__ == "__main__":
    run()
